"""SF Compute — public homepage scrape.

SF Compute is a spot-market-style cluster reseller. The full price API
requires auth, and the public homepage headline this parser targeted
("Buy H100s from $X.YZ/hr") has been removed from their site, so this
fetcher now returns nothing every run. Kept wired in case the headline
returns; their authenticated API would need credentials we don't have.

The chart on the homepage shows historical spot prices but the data is
client-rendered SVG; we only ever captured the headline number.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import UTC, datetime

PROVIDER = "sfcompute"
SOURCE_URL = "https://sfcompute.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

HEADLINE_RE = re.compile(r'Buy\s+(H100|H200|B200)s?\s+from\s+\$([0-9]+\.[0-9]+)/hr', re.IGNORECASE)


def fetch() -> list[dict]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [sfcompute] fetch failed: {e}")
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for m in HEADLINE_RE.finditer(html):
        gpu_label, price_str = m.group(1).upper(), m.group(2)
        model = {"H100": "H100_SXM", "H200": "H200", "B200": "B200"}.get(gpu_label)
        if not model or model in seen:
            continue
        seen.add(model)
        price = float(price_str)
        if price <= 0 or price > 50:
            continue
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": model,
                "gpu_count": 1,
                "region": "us",
                "rental_type": "spot",
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
    print(f"sfcompute: {len(rows)} rows")
    for r in rows:
        print(f"  {r['gpu_model']:18s} {r['rental_type']:6s}  ${r['price_median_usd']:.3f}/hr")
