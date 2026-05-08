"""Crusoe Cloud — public pricing page scrape.

The pricing page is a Webflow grid where each card has the structure:
  <h4>NVIDIA <gpu_name></h4>
  <div class="pricing-tag">{memory}GB</div>
  <div class="pricing-tag">{form_factor}</div>
  <p>$X.YZ/GPU-hr</p>          ← on-demand
  <p>$X.YZ/GPU-hr</p>          ← reserved (we ignore; out of scope)

We emit only the first ($/hr) per card as on-demand.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timezone

PROVIDER = "crusoe"
SOURCE_URL = "https://crusoe.ai/cloud/pricing/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

ITEM_RE = re.compile(
    r'pricing-item-heading">(NVIDIA|AMD)\s+([A-Z0-9]+)</h4>'
    r'.*?pricing_tags-wr">(.*?)</a>'
    r'.*?\$([0-9]+\.[0-9]+)/GPU-hr',
    re.S,
)
TAG_RE = re.compile(r'pricing-tag[^>]*>([^<]+)<')

LABEL_TO_MODEL: dict[tuple[str, str, frozenset], str] = {
    # (vendor, gpu_name, frozenset(tags)) → normalized model
}


def _normalize(vendor: str, gpu: str, tags: list[str]) -> str | None:
    t = {x.strip().upper() for x in tags}
    if vendor == "NVIDIA":
        if gpu == "H200":
            return "H200"
        if gpu == "H100":
            if "SXM" in t:
                return "H100_SXM"
            if "PCIE" in t or "PCIe" in tags:
                return "H100_PCIE"
            return "H100_SXM"  # default if unspecified
        if gpu == "B200":
            return "B200"
        if gpu == "GB200":
            return "GB200"
        if gpu == "A100":
            mem = "80GB" if "80GB" in t else ("40GB" if "40GB" in t else "")
            ff = "SXM" if "SXM" in t else ("PCIE" if "PCIE" in t or "PCIe" in tags else "")
            if mem and ff:
                return f"A100_{ff}_{mem}"
            return None
        if gpu == "L40S":
            return "L40S"
        if gpu == "L40":
            return "L40"
    if vendor == "AMD":
        if gpu == "MI300X":
            return "MI300X"
        if gpu == "MI325X":
            return "MI325X"
        if gpu == "MI355X":
            return "MI355X"
    return None


def fetch() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [crusoe] fetch failed: {e}")
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for m in ITEM_RE.finditer(html):
        vendor, gpu, tags_html, price = m.group(1), m.group(2), m.group(3), float(m.group(4))
        tags = TAG_RE.findall(tags_html)
        model = _normalize(vendor, gpu, tags)
        if not model:
            continue
        if model in seen:
            continue
        seen.add(model)
        rows.append(
            {
                "date": today,
                "provider": PROVIDER,
                "gpu_model": model,
                "gpu_count": 1,
                "region": "us",
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
    print(f"crusoe: {len(rows)} rows")
    for r in sorted(rows, key=lambda x: x["gpu_model"]):
        print(f"  {r['gpu_model']:18s}  ${r['price_median_usd']:.3f}/hr")
