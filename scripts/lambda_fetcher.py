"""Lambda Labs — public pricing page scrape.

Pricing table is server-rendered as escaped JSON inside the HTML. Pattern:
  "NVIDIA <gpu_name>" ... "$<price>"
where <price> is the per-GPU on-demand $/hr for that instance config.

We aggregate min/median/max across all instance sizes (1x/2x/4x/8x) for
each GPU model so dashboards have one canonical row per (provider, gpu_model).
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timezone

PROVIDER = "lambda"
SOURCE_URL = "https://lambda.ai/service/gpu-cloud"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Regex anchors to the table's canonical price column:
#   <td data-label="PRICE/GPU/HR*">$X.XX</td>
# In the escaped JSON-in-HTML form, this is:
#   data-label=\"PRICE\u002FGPU\u002FHR*\"\u003E$X.XX
# We pair each price with the GPU label that appears earliest before it
# (within a single table row, so cap the lookback distance).
GPU_LABEL_RE = re.compile(
    r'NVIDIA\s+(B200(?:\s+SXM6?)?|H200(?:\s+SXM)?|H100\s+(?:SXM|PCIe|NVL)|GH200|A100\s+(?:SXM|PCIe)(?:\s+\d+GB)?|GB200(?:\s+NVL\d+)?)'
)
PRICE_CELL_RE = re.compile(
    r'data-label=\\"PRICE\\u002FGPU\\u002FHR\*?\\"\\u003E\$([0-9]+\.[0-9]+)'
)

# Lambda label → normalized model name.
LABEL_TO_MODEL = [
    (re.compile(r"^B200(?:\s+SXM6?)?$"), "B200"),
    (re.compile(r"^GB200"), "GB200"),
    (re.compile(r"^H200"), "H200"),
    (re.compile(r"^H100\s+SXM"), "H100_SXM"),
    (re.compile(r"^H100\s+PCIe"), "H100_PCIE"),
    (re.compile(r"^H100\s+NVL"), "H100_NVL"),
    (re.compile(r"^GH200"), "GH200"),
    (re.compile(r"^A100\s+SXM(?:\s+40GB)?$"), "A100_SXM_40GB"),  # default 40GB if not stated… Lambda labels ambiguous
    (re.compile(r"^A100\s+SXM\s+80GB"), "A100_SXM_80GB"),
    (re.compile(r"^A100\s+PCIe(?:\s+40GB)?$"), "A100_PCIE_40GB"),
    (re.compile(r"^A100\s+PCIe\s+80GB"), "A100_PCIE_80GB"),
]


def _normalize(label: str) -> str | None:
    label = label.strip()
    for rx, model in LABEL_TO_MODEL:
        if rx.match(label):
            return model
    return None


def fetch() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [lambda] fetch failed: {e}")
        return []

    # Walk price cells in order; pair each with the most recent GPU label.
    gpu_positions = [(m.start(), m.group(1)) for m in GPU_LABEL_RE.finditer(html)]
    buckets: dict[str, list[float]] = {}
    for pm in PRICE_CELL_RE.finditer(html):
        try:
            price = float(pm.group(1))
        except ValueError:
            continue
        if price <= 0 or price > 50:
            continue
        # find the GPU label whose position is < pm.start() and closest to it
        nearest_label = None
        for pos, label in gpu_positions:
            if pos < pm.start():
                nearest_label = label
            else:
                break
        if not nearest_label:
            continue
        # Sanity: GPU label must be within 1500 chars of the price cell
        # (prevents pairing across distant table rows).
        last_pos = max((p for p, _ in gpu_positions if p < pm.start()), default=-1)
        if pm.start() - last_pos > 1500:
            continue
        model = _normalize(nearest_label)
        if not model:
            continue
        buckets.setdefault(model, []).append(price)

    rows: list[dict] = []
    for model, prices in buckets.items():
        prices.sort()
        n = len(prices)
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": model,
                "gpu_count": 1,
                "region": "us",
                "rental_type": "on_demand",
                "price_min_usd": round(prices[0], 4),
                "price_median_usd": round(prices[n // 2], 4),
                "price_max_usd": round(prices[-1], 4),
                "n_offers": n,
                "source_url": SOURCE_URL,
                "fetched_at": fetched_at,
            }
        )
    return rows


if __name__ == "__main__":
    rows = fetch()
    print(f"lambda: {len(rows)} rows")
    for r in sorted(rows, key=lambda x: x["gpu_model"]):
        print(
            f"  {r['gpu_model']:18s}  n={r['n_offers']:2d}  "
            f"${r['price_min_usd']:.3f}/{r['price_median_usd']:.3f}/{r['price_max_usd']:.3f}"
        )
