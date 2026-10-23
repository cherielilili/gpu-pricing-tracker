"""Crusoe Cloud — public pricing page scrape.

The page (Webflow) renders the "GPU instances pricing" table
(GPU model | On-demand | Current spot) as two sibling CMS collections:
first the GPU cards (each div carries data-sort-name and links to
/cloud/gpus/<slug>), then a flat block of price cells in the same card
order — the whole On-demand column, then the whole Current-spot column.
A cell holds either "$X.XX / GPU-hr" or "Contact sales".

The cell count must cover the cards (2 cells per card, spot column may
omit trailing cards); anything else means the layout changed again and we
emit NOTHING rather than mis-pair prices across GPUs — the pre-2026-10
parser did exactly that, blending in prices from other sections so its
"GB200" rows were really H200's price.

Other sections on the same page price DIFFERENT products and are never
recorded: "Self-Serve Deployments" (inference endpoints: B200 $9.65,
H200 $6.00, H100 $5.50, MI355X $7.70) and token-priced serverless models.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import UTC, datetime

PROVIDER = "crusoe"
SOURCE_URL = "https://crusoe.ai/cloud/pricing/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# GPU-table cards: every data-sort-name inside the "GPU instances" table
# region (scoped by its table headers, see parse()). Other sections (VM
# types, serverless models, Self-Serve endpoints) carry data-sort-name too
# and even link to /cloud/gpus/, so the region boundary is the discriminator.
CARD_RE = re.compile(r'data-sort-name="((?:NVIDIA|AMD)\s+[^"]+)"')
TAG_RE = re.compile(r'pricing-tag[^>]*>([^<]+)<')
# One price cell: either a $/GPU-hr price or "Contact sales".
PRICE_CELL_RE = re.compile(
    r'<div role="listitem" class="gpu-row[^"]*">\s*<div class="pricing-rich[^"]*">(.*?)</div>',
    re.DOTALL,
)
# "$8.96</p><p>/</p><p>GPU-hr" — tags, HTML entities, thousands separators
# and whitespace may sit between the price and the unit inside a cell.
PRICE_RE = re.compile(
    r'\$([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})'
    r'(?:<[^>]+>|&[a-z]+;|&#\d+;|[\s/])*GPU-hr'
)
# Section anchors include the heading class so stray "GPU model"/"CPU Type"
# text elsewhere on the page cannot re-scope the parse.
SECTION_START_RE = re.compile(r'pricing-heading[^>]*>GPU model<')
SECTION_END_RE = re.compile(r'pricing-heading[^>]*>CPU Type<')


def _normalize(vendor: str, gpu: str, tags: list[str]) -> str | None:
    t = {x.strip().upper() for x in tags}
    if vendor == "NVIDIA":
        if gpu == "H200":
            return "H200"
        if gpu == "H100":
            if "PCIE" in t:
                return "H100_PCIE"
            return "H100_SXM"  # default if unspecified
        if gpu == "B200":
            return "B200"
        if gpu == "B300":
            return "B300"
        if gpu == "GB200":
            return "GB200"
        if gpu == "A100":
            mem = "80GB" if "80GB" in t else ("40GB" if "40GB" in t else "")
            ff = "SXM" if "SXM" in t else ("PCIE" if "PCIE" in t else "")
            if mem and ff:
                return f"A100_{ff}_{mem}"
            return None
        if gpu == "L40S":
            return "L40S"
        if gpu == "L40":
            return "L40"
        # Vera Rubin / GB300: no canonical slot yet; skip until they ship
        # with public pricing.
    if vendor == "AMD":
        if gpu == "MI300X":
            return "MI300X"
        if gpu == "MI325X":
            return "MI325X"
        if gpu == "MI355X":
            return "MI355X"
    return None


def _row(today: str, fetched_at: str, model: str, rental_type: str, price: float) -> dict:
    return {
        "date": today,
        "provider": PROVIDER,
        "gpu_model": model,
        "gpu_count": 1,
        "region": "us",
        "rental_type": rental_type,
        "price_min_usd": round(price, 4),
        "price_median_usd": round(price, 4),
        "price_max_usd": round(price, 4),
        "n_offers": 1,
        "source_url": SOURCE_URL,
        "fetched_at": fetched_at,
    }


def parse(html: str) -> list[tuple[str, float | None, float | None]] | None:
    """Extract (gpu_model, on_demand_price, spot_price) triples from the
    GPU instances table. Prices are None when the cell says Contact sales.
    Returns None on layout drift so the caller can report it — never guess
    a pairing."""
    clean = re.sub(r"<style.*?</style>|<script.*?</script>", "", html, flags=re.DOTALL)

    # Scope to the GPU instances table: from its "GPU model" column header
    # to the next section's "CPU Type" header. Inside this region every
    # data-sort-name is a GPU card and every gpu-row cell belongs to the
    # flat cell block, so positional pairing is safe.
    s0 = SECTION_START_RE.search(clean)
    s1 = SECTION_END_RE.search(clean)
    if not s0 or not s1 or s1.start() <= s0.start():
        return None
    zone = clean[s0.start():s1.start()]

    # Tag blocks sit inside each card's own span, bounded by the next card.
    # Capturing over the whole span (not up to the first </a>) keeps tags
    # that follow a nested anchor, so the SXM/PCIe discriminator survives.
    matches = list(CARD_RE.finditer(zone))
    cards: list[tuple[str, list[str]]] = []
    for j, m in enumerate(matches):
        name = m.group(1)
        vendor, gpu = name.split(None, 1)
        seg_end = matches[j + 1].start() if j + 1 < len(matches) else len(zone)
        tags = TAG_RE.findall(zone[m.end():seg_end])
        cards.append((f"{vendor} {gpu}", tags))
    if not cards:
        return []

    cells: list[float | None] = []
    for cm in PRICE_CELL_RE.finditer(zone):
        pm = PRICE_RE.search(cm.group(1))
        cells.append(float(pm.group(1).replace(",", "")) if pm else None)

    # Exact two cells per card or nothing: with a hole anywhere else in the
    # cell block the columns cannot be located, and guessing would shift
    # prices onto the wrong GPUs.
    n = len(cards)
    if len(cells) != 2 * n:
        return None
    od_cells, spot_cells = cells[:n], cells[n:]

    out: list[tuple[str, float | None, float | None]] = []
    for (name, tags), od, sp in zip(cards, od_cells, spot_cells, strict=True):
        vendor, gpu = name.split(None, 1)
        model = _normalize(vendor, gpu, tags)
        if model:
            out.append((model, od, sp))
    return out


def fetch() -> list[dict]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [crusoe] fetch failed: {e}")
        return []

    rows: list[dict] = []
    triples = parse(html)
    if triples is None:
        print("  [crusoe] pricing table layout changed — emitting nothing")
        return []
    for model, od, sp in triples:
        # $0.00 is a placeholder/sold-out marker, not a market price; other
        # fetchers guard the same way.
        if od is not None and od > 0:
            rows.append(_row(today, fetched_at, model, "on_demand", od))
        if sp is not None and sp > 0:
            rows.append(_row(today, fetched_at, model, "spot", sp))
    return rows


if __name__ == "__main__":
    got = fetch()
    print(f"crusoe: {len(got)} rows")
    for r in sorted(got, key=lambda x: (x["rental_type"], x["gpu_model"])):
        print(f"  {r['gpu_model']:18s} {r['rental_type']:10s}  ${r['price_median_usd']:.2f}/hr")
