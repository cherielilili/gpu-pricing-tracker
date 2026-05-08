"""Nebius — public pricing page scrape.

Pricing table is embedded as a markdown-style 2D array inside the page's
__NEXT_DATA__ JSON. Each row looks like:
  ["NVIDIA HGX B200","20","224","$5.50"]
  ["NVIDIA HGX H200","16","200","$3.50"]
  ["NVIDIA HGX H100","16","200","$2.95"]
  ["NVIDIA L40S with Intel CPU","16-192","96-1152","from $1.82"]

The 4th cell is per-GPU $/hr (on-demand). "Contact us" rows are skipped.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timezone

PROVIDER = "nebius"
SOURCE_URL = "https://nebius.com/prices"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

ROW_RE = re.compile(
    r'\[\\"(NVIDIA[^"\\]+|AMD[^"\\]+)\\",\\"([^"\\]*)\\",\\"([^"\\]*)\\",\\"(?:from\s+)?\$([0-9]+\.[0-9]+)\\"',
    re.I,
)


def _normalize(name: str) -> str | None:
    n = name.upper().strip()
    if "B200" in n:
        return "B200"
    if "H200" in n:
        return "H200"
    if "H100" in n:
        return "H100_SXM"  # Nebius lists HGX H100 (SXM5)
    if "GB200" in n:
        return "GB200"
    if "L40S" in n:
        return "L40S"
    if "A100" in n:
        return "A100_SXM_80GB"
    if "MI300X" in n:
        return "MI300X"
    return None


def fetch() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [nebius] fetch failed: {e}")
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for m in ROW_RE.finditer(html):
        name = m.group(1)
        price = float(m.group(4))
        if price <= 0 or price > 50:
            continue
        model = _normalize(name)
        if not model or model in seen:
            continue
        seen.add(model)
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": model,
                "gpu_count": 1,
                "region": "eu",
                "rental_type": "on_demand",
                "price_min_usd": round(price, 4),
                "price_median_usd": round(price, 4),
                "price_max_usd": round(price, 4),
                "n_offers": 1,
                "source_url": SOURCE_URL,
                "fetched_at": fetched_at,
            }
        )
    return rows


if __name__ == "__main__":
    rows = fetch()
    print(f"nebius: {len(rows)} rows")
    for r in sorted(rows, key=lambda x: x["gpu_model"]):
        print(f"  {r['gpu_model']:18s}  ${r['price_median_usd']:.3f}/hr")
