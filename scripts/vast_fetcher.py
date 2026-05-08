"""Vast.ai public bundles API — pulls on-demand and bid (spot) offers.

No auth required. dph_total is whole-machine $/hr; we divide by num_gpus
to normalize to per-GPU $/hr. min_bid (when type=bid) is the per-machine
spot bid floor — also normalized per-GPU.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterable

PROVIDER = "vast.ai"
SOURCE_URL = "https://console.vast.ai/api/v0/bundles/"
USER_AGENT = "gpu-pricing-tracker/0.1 (+https://github.com/cherielilili/gpu-pricing-tracker)"

# Vast's gpu_name strings (left) → our normalized model name (right).
GPU_MAP = {
    "H100 SXM": "H100_SXM",
    "H100 PCIE": "H100_PCIE",
    "H100 NVL": "H100_NVL",
    "H200": "H200",
    "H200 NVL": "H200",
    "B200": "B200",
    "GB200": "GB200",
    "A100 SXM4": "A100_SXM_80GB",
    "A100 PCIE": "A100_PCIE_80GB",
    "A100X": "A100_80GB",
    "L40S": "L40S",
    "L40": "L40",
    "RTX 4090": "RTX_4090",
    "RTX 5090": "RTX_5090",
    "MI300X": "MI300X",
    "MI325X": "MI325X",
}


def _fetch(query: dict) -> list[dict]:
    url = SOURCE_URL + "?q=" + urllib.parse.quote(json.dumps(query))
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read()).get("offers", [])


def _country(offer: dict) -> str:
    geo = offer.get("geolocation") or ""
    if "," in geo:
        return geo.rsplit(",", 1)[-1].strip()
    return geo or "unknown"


def _aggregate(
    offers: Iterable[dict], rental_type: str, today: str, fetched_at: str
) -> list[dict]:
    """Group offers by (gpu_model, gpu_count, region) and emit one row per
    group with min/median/max price across the marketplace."""
    buckets: dict[tuple, list[float]] = {}
    for o in offers:
        gpu = o.get("gpu_name")
        if gpu not in GPU_MAP:
            continue
        n_gpu = o.get("num_gpus") or 0
        if n_gpu <= 0:
            continue
        if rental_type == "spot":
            machine_price = o.get("min_bid") or o.get("dph_base")
        else:
            machine_price = o.get("dph_total") or o.get("dph_base")
        if not machine_price or machine_price <= 0:
            continue
        per_gpu = float(machine_price) / float(n_gpu)
        key = (GPU_MAP[gpu], int(n_gpu), _country(o))
        buckets.setdefault(key, []).append(per_gpu)

    rows: list[dict] = []
    for (gpu_model, n_gpu, region), prices in buckets.items():
        prices.sort()
        n = len(prices)
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": gpu_model,
                "gpu_count": n_gpu,
                "region": region,
                "rental_type": rental_type,
                "price_min_usd": round(prices[0], 4),
                "price_median_usd": round(prices[n // 2], 4),
                "price_max_usd": round(prices[-1], 4),
                "n_offers": n,
                "source_url": SOURCE_URL,
                "fetched_at": fetched_at,
            }
        )
    return rows


def fetch() -> list[dict]:
    """Return normalized observation rows for both on-demand and spot."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    base_query = {
        "verified": {"eq": True},
        "external": {"eq": False},
        "rentable": {"eq": True},
        "gpu_name": {"in": list(GPU_MAP.keys())},
        "order": [["dph_total", "asc"]],
        "limit": 500,
    }

    rows: list[dict] = []
    for rental_type, vast_type in [("on_demand", "on-demand"), ("spot", "bid")]:
        q = dict(base_query, type=vast_type)
        try:
            offers = _fetch(q)
        except Exception as e:
            print(f"  [vast] {rental_type} fetch failed: {e}")
            continue
        rows.extend(_aggregate(offers, rental_type, today, fetched_at))

    return rows


if __name__ == "__main__":
    rows = fetch()
    print(f"vast.ai: {len(rows)} aggregated rows")
    for r in sorted(rows, key=lambda x: (x["gpu_model"], x["rental_type"], x["region"]))[:25]:
        print(
            f"  {r['gpu_model']:18s} {r['rental_type']:10s} {r['region']:6s} "
            f"x{r['gpu_count']:<2}  n={r['n_offers']:3d}  "
            f"${r['price_min_usd']:.3f}/{r['price_median_usd']:.3f}/{r['price_max_usd']:.3f}"
        )
