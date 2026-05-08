"""RunPod public GraphQL — gpuTypes query is unauthenticated.

Returns four price points per GPU:
  securePrice          → Secure Cloud on-demand   (rental_type=on_demand, region=secure)
  communitySpotPrice   → Community Cloud spot     (rental_type=spot,      region=community)
  lowestPrice.uninterruptablePrice → cheapest on-demand across both clouds
  lowestPrice.minimumBidPrice      → cheapest spot across both clouds

We emit secure + community as separate rows so they can be compared, and
also emit the "lowest" row as a third synthetic region="any" so dashboards
have a single number per GPU.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

PROVIDER = "runpod"
SOURCE_URL = "https://api.runpod.io/graphql"
USER_AGENT = "gpu-pricing-tracker/0.1 (+https://github.com/cherielilili/gpu-pricing-tracker)"

# RunPod displayName → our normalized model name. Models we don't track are skipped.
GPU_MAP = {
    "H100 SXM": "H100_SXM",
    "H100 PCIe": "H100_PCIE",
    "H100 NVL": "H100_NVL",
    "H200 SXM": "H200",
    "H200": "H200",
    "B200": "B200",
    "B300": "B300",
    "GB200": "GB200",
    "A100 SXM": "A100_SXM_80GB",
    "A100 PCIe": "A100_PCIE_80GB",
    "A100 SXM 40GB": "A100_SXM_40GB",
    "L40S": "L40S",
    "L40": "L40",
    "RTX 4090": "RTX_4090",
    "RTX 5090": "RTX_5090",
    "RTX PRO 6000": "RTX_PRO_6000",
    "MI300X": "MI300X",
    "MI325X": "MI325X",
}

QUERY = """
query GpuTypes {
  gpuTypes {
    displayName
    memoryInGb
    securePrice
    communitySpotPrice
    lowestPrice(input: {gpuCount: 1}) {
      minimumBidPrice
      uninterruptablePrice
    }
  }
}
"""


def _post() -> list[dict]:
    req = urllib.request.Request(
        SOURCE_URL,
        data=json.dumps({"query": QUERY}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    if "errors" in body:
        raise RuntimeError(f"RunPod GraphQL errors: {body['errors'][:1]}")
    return body.get("data", {}).get("gpuTypes", []) or []


def _row(today: str, fetched_at: str, gpu_model: str, region: str,
         rental_type: str, price: float | None) -> dict | None:
    if price is None or price <= 0:
        return None
    p = round(float(price), 4)
    return {
        "date": today,
        "provider": PROVIDER,
        "gpu_model": gpu_model,
        "gpu_count": 1,
        "region": region,
        "rental_type": rental_type,
        "price_min_usd": p,
        "price_median_usd": p,
        "price_max_usd": p,
        "n_offers": 1,
        "source_url": SOURCE_URL,
        "fetched_at": fetched_at,
    }


def fetch() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        gpu_types = _post()
    except Exception as e:
        print(f"  [runpod] fetch failed: {e}")
        return []

    rows: list[dict] = []
    for t in gpu_types:
        name = t.get("displayName")
        gpu_model = GPU_MAP.get(name)
        if not gpu_model:
            continue
        lowest = t.get("lowestPrice") or {}
        candidates = [
            ("secure", "on_demand", t.get("securePrice")),
            ("community", "spot", t.get("communitySpotPrice")),
            ("any", "on_demand", lowest.get("uninterruptablePrice")),
            ("any", "spot", lowest.get("minimumBidPrice")),
        ]
        for region, rental_type, price in candidates:
            row = _row(today, fetched_at, gpu_model, region, rental_type, price)
            if row:
                rows.append(row)
    return rows


if __name__ == "__main__":
    rows = fetch()
    print(f"runpod: {len(rows)} rows")
    for r in sorted(rows, key=lambda x: (x["gpu_model"], x["rental_type"], x["region"])):
        print(
            f"  {r['gpu_model']:18s} {r['rental_type']:10s} {r['region']:10s}  "
            f"${r['price_median_usd']:.3f}/hr"
        )
