"""One-time backfill — pull historical gpu_price_snapshots from Supabase
(populated since 2026-02-11 by ~/Projects/Trackers/gpu-tracker on Mini)
and merge into our observations.csv.

The old tracker uses getdeploying.com aggregator which covers ~33 providers,
including AWS/Azure/GCP/CoreWeave/Hyperstack that our direct fetchers don't
have. After backfill, the Signal Dashboard's 30D delta computations will
fire for real (87+ days of history vs 1).

Schema mapping:
  old prices[i].provider     → normalized provider slug
  old prices[i].gpu_model    → parsed for SXM/PCIE/NVL suffix
  old prices[i].billing_type → rental_type (Reserved skipped)
  old prices[i].price_hr     → per-GPU $/hr (already)
  old snapshot_at            → date

Aggregation: group by (date, provider, gpu_model, gpu_count, region, rental_type)
and emit min/median/max same as our live fetchers.
"""
from __future__ import annotations

import csv
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "data" / "observations.csv"

# Mini path; if running on Air the script will SSH-tunnel via Supabase REST so
# we just need the SUPABASE_URL/KEY (loaded from old .env on Mini).
OLD_ENV = Path("/Users/cherie/Projects/Trackers/gpu-tracker/.env")
SOURCE_URL = "https://getdeploying.com"

# ── slug → base GPU family (further parsed from gpu_model field) ────────────
SLUG_BASE = {
    "nvidia-h100": "H100",
    "nvidia-h200": "H200",
    "nvidia-b200": "B200",
    "nvidia-a100": "A100",
    "nvidia-l40s": "L40S",
    "nvidia-gb200": "GB200",
    "amd-mi300x": "MI300X",
    "amd-instinct-mi300x": "MI300X",
}

PROVIDER_NORMALIZE = {
    "vast.ai": "vast.ai",
    "runpod": "runpod",
    "lambda labs": "lambda",
    "crusoe": "crusoe",
    "nebius": "nebius",
    "sf compute": "sfcompute",
    "amazon web services": "aws",
    "microsoft azure": "azure",
    "google cloud": "gcp",
    "oracle cloud": "oracle",
    "coreweave": "coreweave",
    "digitalocean": "digitalocean",
    "hyperstack": "hyperstack",
    "paperspace": "paperspace",
    "fly.io": "flyio",
    "vultr": "vultr",
    "ovhcloud": "ovh",
    "cudo compute": "cudo",
    "thunder compute": "thunder",
    "novita": "novita",
    "together": "together",
    "koyeb": "koyeb",
    "civo": "civo",
    "cirrascale": "cirrascale",
    "sesterce": "sesterce",
    "theta edgecloud": "theta",
    "fluidstack": "fluidstack",
    "lyceum": "lyceum",
    "verda": "verda",
    "beyond.pl": "beyondpl",
    "packet·ai": "packetai",
    "packetai": "packetai",
    "hot aisle": "hotaisle",
    "upcloud": "upcloud",
    "oblivus": "oblivus",
}

BILLING_MAP = {
    "On-Demand": "on_demand",
    "Spot": "spot",
    # "Reserved (Xmo)" → skip (we don't track reserved)
}


def _normalize_gpu(slug: str, gpu_model_field: str) -> str | None:
    """Resolve to our model id (e.g. H100_SXM, A100_SXM_80GB)."""
    base = SLUG_BASE.get(slug)
    if not base:
        return None

    # Cheaper sluggable cases — no SXM/PCIE distinction tracked in our schema
    if base in ("H200", "B200", "GB200", "L40S", "MI300X"):
        return base

    # H100: needs SXM / PCIE / NVL suffix from gpu_model_field
    if base == "H100":
        text = gpu_model_field or ""
        if re.search(r"\bNVL\b", text, re.I):
            return "H100_NVL"
        if re.search(r"\bPCIe\b", text, re.I):
            return "H100_PCIE"
        # default SXM (matches getdeploying's most common config)
        return "H100_SXM"

    # A100: 40GB/80GB × SXM/PCIE
    if base == "A100":
        text = gpu_model_field or ""
        size = "80GB" if "80GB" in text else ("40GB" if "40GB" in text else "80GB")
        ff = "PCIE" if re.search(r"\bPCIe\b", text, re.I) else "SXM"
        return f"A100_{ff}_{size}"

    return None


def _normalize_provider(name: str | None) -> str | None:
    if not name:
        return None
    return PROVIDER_NORMALIZE.get(name.strip().lower(), name.strip().lower())


def fetch_snapshots(limit: int = 5000) -> list[dict]:
    if not OLD_ENV.exists():
        print(f"  [backfill] {OLD_ENV} not found — must run on Mini or copy .env locally")
        return []
    env = {}
    for line in OLD_ENV.read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

    url = env["SUPABASE_URL"]
    key = env["SUPABASE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    all_rows = []
    offset = 0
    page = 1000
    while True:
        r = httpx.get(
            f"{url}/rest/v1/gpu_price_snapshots",
            params={
                "select": "gpu_slug,snapshot_at,prices",
                "order": "snapshot_at.asc",
                "offset": offset,
                "limit": page,
            },
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        all_rows.extend(chunk)
        if len(chunk) < page or len(all_rows) >= limit:
            break
        offset += page
    return all_rows


def expand_to_observations(snapshots: list[dict]) -> list[dict]:
    """Each old snapshot has a prices array — flatten into per-config rows,
    keep latest snapshot of each day per (gpu, provider, gpu_count, rental_type)."""
    # bucket by (date, gpu_model, provider, gpu_count, rental_type) → list of prices
    bucket: dict[tuple, list[float]] = defaultdict(list)
    fetched_at: dict[tuple, str] = {}

    for snap in snapshots:
        slug = snap["gpu_slug"]
        snap_iso = snap["snapshot_at"]
        date_str = snap_iso[:10]
        for entry in snap.get("prices") or []:
            prov = _normalize_provider(entry.get("provider"))
            if not prov:
                continue
            gpu = _normalize_gpu(slug, entry.get("gpu_model") or "")
            if not gpu:
                continue
            rental = BILLING_MAP.get(entry.get("billing_type"))
            if not rental:
                continue  # skip reserved
            try:
                price = float(entry.get("price_hr"))
            except (TypeError, ValueError):
                continue
            if price <= 0 or price > 100:
                continue
            try:
                gpu_count = int(entry.get("gpu_count") or 1)
            except (TypeError, ValueError):
                gpu_count = 1
            key = (date_str, prov, gpu, gpu_count, "global", rental)
            bucket[key].append(price)
            fetched_at[key] = snap_iso

    rows: list[dict] = []
    for (date_str, prov, gpu, gpu_count, region, rental), prices in bucket.items():
        prices.sort()
        n = len(prices)
        rows.append({
            "date": date_str,
            "provider": prov,
            "gpu_model": gpu,
            "gpu_count": gpu_count,
            "region": region,
            "rental_type": rental,
            "price_min_usd": round(prices[0], 4),
            "price_median_usd": round(prices[n // 2], 4),
            "price_max_usd": round(prices[-1], 4),
            "n_offers": n,
            "source_url": SOURCE_URL,
            "fetched_at": fetched_at[(date_str, prov, gpu, gpu_count, region, rental)],
        })
    return rows


# ── merge logic mirrors fetch.py orchestrator ────────────────────────────────
FIELDNAMES = [
    "date", "provider", "gpu_model", "gpu_count", "region", "rental_type",
    "price_min_usd", "price_median_usd", "price_max_usd", "n_offers",
    "source_url", "fetched_at",
]


def _key(row: dict) -> tuple:
    return (
        row["date"], row["provider"], row["gpu_model"],
        row["rental_type"], row["region"], int(row["gpu_count"]),
    )


def main() -> int:
    snaps = fetch_snapshots()
    print(f"fetched {len(snaps)} snapshots from Supabase")
    if not snaps:
        return 1

    backfill_rows = expand_to_observations(snaps)
    print(f"expanded to {len(backfill_rows)} observation rows")

    # merge with existing observations.csv
    existing: dict[tuple, dict] = {}
    if OBS.exists():
        with OBS.open() as f:
            for r in csv.DictReader(f):
                try:
                    r["gpu_count"] = int(r["gpu_count"])
                    existing[_key(r)] = r
                except (KeyError, ValueError):
                    continue
    print(f"existing observations: {len(existing)}")

    added = replaced = 0
    for r in backfill_rows:
        k = _key(r)
        if k in existing:
            # prefer existing live-fetcher data over getdeploying aggregate when same key
            # (getdeploying is min across many providers, lower fidelity per provider)
            # — but for historical dates the live fetcher has nothing, so backfill wins
            if existing[k].get("source_url") == SOURCE_URL:
                replaced += 1
                existing[k] = r
            else:
                # live data exists; keep it
                continue
        else:
            added += 1
            existing[k] = r

    OBS.parent.mkdir(exist_ok=True)
    sorted_rows = sorted(
        existing.values(),
        key=lambda x: (x["date"], x["provider"], x["gpu_model"], x["rental_type"]),
    )
    with OBS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(sorted_rows)

    print(f"wrote {OBS} — total {len(existing)} (added {added}, replaced {replaced})")
    dates = sorted({r["date"] for r in existing.values()})
    print(f"date range: {dates[0]} → {dates[-1]}  ({len(dates)} distinct days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
