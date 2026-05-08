"""Orchestrator — runs every fetcher, appends to data/observations.csv.

Dedup key:
  (date, provider, gpu_model, rental_type, region, gpu_count)

Same-day reruns will *replace* a matching row rather than duplicate it.
This makes intra-day reruns idempotent while still letting different
machine sizes (gpu_count) coexist as separate observations.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OBS = DATA / "observations.csv"

FIELDNAMES = [
    "date",
    "provider",
    "gpu_model",
    "gpu_count",
    "region",
    "rental_type",
    "price_min_usd",
    "price_median_usd",
    "price_max_usd",
    "n_offers",
    "source_url",
    "fetched_at",
]


def _key(row: dict) -> tuple:
    return (
        row["date"],
        row["provider"],
        row["gpu_model"],
        row["rental_type"],
        row["region"],
        int(row["gpu_count"]),
    )


def load_existing() -> dict[tuple, dict]:
    if not OBS.exists():
        return {}
    out: dict[tuple, dict] = {}
    with OBS.open() as f:
        for row in csv.DictReader(f):
            try:
                row["gpu_count"] = int(row["gpu_count"])
                out[_key(row)] = row
            except (KeyError, ValueError):
                continue
    return out


def write(rows_by_key: dict[tuple, dict]) -> None:
    DATA.mkdir(exist_ok=True)
    rows = sorted(
        rows_by_key.values(),
        key=lambda r: (r["date"], r["provider"], r["gpu_model"], r["rental_type"]),
    )
    with OBS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    from vast_fetcher import fetch as fetch_vast
    from runpod_fetcher import fetch as fetch_runpod
    from lambda_fetcher import fetch as fetch_lambda
    from crusoe_fetcher import fetch as fetch_crusoe
    from nebius_fetcher import fetch as fetch_nebius
    from sfcompute_fetcher import fetch as fetch_sfcompute

    fetchers = [
        ("vast", fetch_vast),
        ("runpod", fetch_runpod),
        ("lambda", fetch_lambda),
        ("crusoe", fetch_crusoe),
        ("nebius", fetch_nebius),
        ("sfcompute", fetch_sfcompute),
    ]

    existing = load_existing()
    print(f"existing observations: {len(existing)}")

    new_rows: list[dict] = []
    for name, fn in fetchers:
        try:
            got = fn()
        except Exception as e:
            print(f"  [{name}] hard failure: {e}")
            continue
        print(f"  [{name}] {len(got)} rows")
        new_rows.extend(got)

    if not new_rows:
        print("no rows fetched — aborting (existing csv left untouched)")
        return 1

    replaced = 0
    added = 0
    for r in new_rows:
        k = _key(r)
        if k in existing:
            replaced += 1
        else:
            added += 1
        existing[k] = r

    write(existing)
    print(f"wrote {OBS} — total {len(existing)} (added {added}, replaced {replaced})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
