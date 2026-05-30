#!/usr/bin/env python3
"""Compare last two snapshot dates in observations.csv and push alerts.

Aggregation: (provider, gpu_model, rental_type) → median of per-GPU price
  per-GPU price = price_median_usd / gpu_count (so cluster pricing comparable)

Alert rules:
  - on_demand single SKU: ±10% triggers
  - spot single SKU: NOT alerted (too noisy); only feeds cohort signal
  - new (provider, gpu_model, rental_type) entry
  - same-direction cohort: ≥3 SKUs of same gpu_model moving same way (any rental_type)
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "data" / "observations.csv"

THRESHOLD = 0.10
WEBHOOK_ENV = "DISCORD_WEBHOOK_TRACKING"
PROXIES = {"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"}
REPORT_URL = "https://cherielilili.github.io/gpu-pricing-tracker/"


def load_rows() -> list[dict]:
    with OBS.open() as f:
        return list(csv.DictReader(f))


def latest_two_dates(rows: list[dict]) -> tuple[str, str] | None:
    dates = sorted({r["date"] for r in rows})
    if len(dates) < 2:
        return None
    return dates[-2], dates[-1]


def snapshot(rows: list[dict], date: str) -> dict[tuple, float]:
    grouped: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if r["date"] != date:
            continue
        try:
            price = float(r["price_median_usd"])
            count = int(r["gpu_count"])
        except (TypeError, ValueError):
            continue
        if count <= 0 or price <= 0:
            continue
        per_gpu = price / count
        key = (r["provider"], r["gpu_model"], r["rental_type"])
        grouped[key].append(per_gpu)
    return {k: statistics.median(v) for k, v in grouped.items() if v}


def detect(prev: dict, curr: dict) -> dict:
    changes = []  # all changes (both on_demand and spot)
    added = []
    for key, new_price in curr.items():
        if key not in prev:
            added.append((key, new_price))
            continue
        old_price = prev[key]
        if old_price == 0:
            continue
        pct = (new_price - old_price) / old_price
        if abs(pct) >= THRESHOLD:
            changes.append((key, old_price, new_price, pct))

    # cohort signal — counts both rental_types
    by_model_dir: dict[tuple[str, str], int] = defaultdict(int)
    for (provider, model, rt), _, _, pct in changes:
        direction = "down" if pct < 0 else "up"
        by_model_dir[(model, direction)] += 1
    cohort = [(m, d, n) for (m, d), n in by_model_dir.items() if n >= 3]

    # single-SKU alerts: only on_demand
    on_demand_changes = [c for c in changes if c[0][2] == "on_demand"]
    on_demand_added = [a for a in added if a[0][2] == "on_demand"]

    return {
        "on_demand_changes": on_demand_changes,
        "on_demand_added": on_demand_added,
        "cohort": cohort,
    }


def format_message(prev_date: str, curr_date: str, diff: dict) -> str | None:
    od_changes = diff["on_demand_changes"]
    od_added = diff["on_demand_added"]
    cohort = diff["cohort"]
    if not od_changes and not od_added and not cohort:
        return None

    lines = [f"🎮 **GPU 租金异动** {curr_date} (vs {prev_date})"]

    if cohort:
        lines.append("")
        lines.append("**赛道同向信号** (≥3 SKU 同向)")
        cohort.sort(key=lambda x: x[2], reverse=True)
        for model, direction, n in cohort:
            arrow = "↓" if direction == "down" else "↑"
            lines.append(f"• {model}: {n} SKU 同向 {arrow}")

    if od_changes:
        od_changes.sort(key=lambda x: abs(x[3]), reverse=True)
        lines.append("")
        lines.append("**On-demand 变化 (±10%+)**")
        for (provider, model, rt), old_p, new_p, pct in od_changes[:15]:
            arrow = "↓" if pct < 0 else "↑"
            lines.append(
                f"• {model} [{provider}]: "
                f"${old_p:.2f} → ${new_p:.2f}/h/GPU ({arrow}{abs(pct)*100:.0f}%)"
            )
        if len(od_changes) > 15:
            lines.append(f"… 另有 {len(od_changes)-15} 条 on-demand 变化")

    if od_added:
        lines.append("")
        lines.append(f"**新上线 on-demand SKU ({len(od_added)})**")
        for (provider, model, rt), price in od_added[:10]:
            lines.append(f"• {model} [{provider}] @ ${price:.2f}/h/GPU")

    lines.append("")
    lines.append(f"→ {REPORT_URL}")
    return "\n".join(lines)


def push(text: str) -> bool:
    url = os.environ.get(WEBHOOK_ENV, "")
    if not url:
        print(f"[detect_changes] {WEBHOOK_ENV} not set — skipping push", file=sys.stderr)
        print(text)
        return False
    try:
        r = requests.post(url, json={"content": text}, proxies=PROXIES, timeout=10)
        ok = r.status_code in (200, 204)
        print(f"[detect_changes] discord push status={r.status_code}", file=sys.stderr)
        return ok
    except Exception as e:
        print(f"[detect_changes] push failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    rows = load_rows()
    pair = latest_two_dates(rows)
    if not pair:
        print("[detect_changes] not enough dates", file=sys.stderr)
        return 0
    prev_date, curr_date = pair
    prev = snapshot(rows, prev_date)
    curr = snapshot(rows, curr_date)
    diff = detect(prev, curr)
    msg = format_message(prev_date, curr_date, diff)
    if msg is None:
        print("[detect_changes] no material changes", file=sys.stderr)
        return 0
    if dry_run:
        print(msg)
        return 0
    push(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
