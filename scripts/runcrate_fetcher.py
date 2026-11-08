"""Runcrate - public GPU pricing feed.

Runcrate publishes its current GPU catalog as JSON, no auth needed:
  https://www.runcrate.ai/api/gpu-pricing
Each entry carries min/avg/max $/GPU-hour and `available_options` (how many
offers are currently orderable). Types with no available option are skipped.

Only types whose feed name maps to exactly one model in this repo are kept.
Types that do not say SXM vs PCIe (H100, A100) are left out rather than
guessed, so they never pollute the H100_SXM / A100_* series.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

PROVIDER = "runcrate"
SOURCE_URL = "https://www.runcrate.ai/api/gpu-pricing"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Runcrate gpu_type -> normalized model name used in data/observations.csv.
TYPE_TO_MODEL = {
    "B300": "B300",
    "B200": "B200",
    "H200": "H200",
    "GH200": "GH200",
    "L40S": "L40S",
    "L40": "L40",
    "RTX5090": "RTX_5090",
    "RTX4090": "RTX_4090",
    "RTXPro6000": "RTX_PRO_6000",
}


def parse(payload: dict, today: str, fetched_at: str) -> list[dict]:
    rows: list[dict] = []
    for gpu in payload.get("gpus", []):
        model = TYPE_TO_MODEL.get(gpu.get("gpu_type"))
        if not model:
            continue
        n_offers = int(gpu.get("available_options") or 0)
        if n_offers < 1:
            continue
        price_min = gpu.get("min_price_hr")
        if not isinstance(price_min, (int, float)) or price_min <= 0 or price_min > 50:
            continue
        price_avg = gpu.get("avg_price_hr") or price_min
        price_max = gpu.get("max_price_hr") or price_min
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": model,
                "gpu_count": 1,
                "region": "global",
                "rental_type": "on_demand",
                "price_min_usd": round(price_min, 4),
                "price_median_usd": round(price_avg, 4),
                "price_max_usd": round(price_max, 4),
                "n_offers": n_offers,
                "source_url": SOURCE_URL,
                "fetched_at": fetched_at,
            }
        )
    return rows


def fetch() -> list[dict]:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    fetched_at = now.isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        payload = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    except Exception as e:
        print(f"  [runcrate] fetch failed: {e}")
        return []

    return parse(payload, today, fetched_at)


if __name__ == "__main__":
    rows = fetch()
    print(f"runcrate: {len(rows)} rows")
    for r in sorted(rows, key=lambda x: x["gpu_model"]):
        print(
            f"  {r['gpu_model']:14s}  n={r['n_offers']:2d}  "
            f"${r['price_min_usd']:.3f}/{r['price_median_usd']:.3f}/{r['price_max_usd']:.3f}"
        )
