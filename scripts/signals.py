"""Investment signal layer — derives 5 signals from observations.csv.

Each signal returns a dict with:
  level    — "tightening" / "stable" / "softening" / "n/a"
  headline — short numeric headline ("$2.97/hr median")
  delta    — change vs prior window ("-3.2% / 30D") or None if insufficient data
  detail   — 1-line explanation
  ticker   — list of related tickers

Signals are intentionally conservative on day-1 data: when we don't have
enough history for a delta computation, we report level="stable" with
delta=None rather than fabricate a trend.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "data" / "observations.csv"

# A delta is only meaningful with this many distinct dates of history.
MIN_DAYS_FOR_DELTA = 7


def load() -> list[dict]:
    if not OBS.exists():
        return []
    with OBS.open() as f:
        return list(csv.DictReader(f))


def _filter(rows: list[dict], **kwargs) -> list[dict]:
    out = []
    for r in rows:
        ok = True
        for k, v in kwargs.items():
            if isinstance(v, (set, list, tuple)):
                if r.get(k) not in v:
                    ok = False
                    break
            elif r.get(k) != v:
                ok = False
                break
        if ok:
            out.append(r)
    return out


def _median_price(rows: list[dict]) -> float | None:
    prices = [float(r["price_median_usd"]) for r in rows if r.get("price_median_usd")]
    return round(statistics.median(prices), 4) if prices else None


def _daily_median(rows: list[dict]) -> dict[str, float]:
    """Group by date, return {date: median price}."""
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        try:
            by_date[r["date"]].append(float(r["price_median_usd"]))
        except (KeyError, ValueError):
            continue
    return {d: round(statistics.median(ps), 4) for d, ps in by_date.items() if ps}


def _delta_pct(today_val: float, prior_val: float) -> float:
    if prior_val == 0:
        return 0.0
    return round((today_val - prior_val) / prior_val * 100, 1)


def _classify_change(pct: float | None, *, falling_is: str = "softening") -> str:
    """falling_is='softening' for demand signals (lower price = softer demand);
    falling_is='tightening' for spread/discount signals (lower spread = tighter)."""
    if pct is None:
        return "stable"
    if abs(pct) < 5:
        return "stable"
    if pct < -5:
        return falling_is
    return "softening" if falling_is == "tightening" else "tightening"


def _trend_delta(daily: dict[str, float], days: int) -> float | None:
    """Compute (today − ~N-days-ago) / ~N-days-ago × 100. None if insufficient
    history."""
    if len(daily) < min(days, MIN_DAYS_FOR_DELTA):
        return None
    sorted_dates = sorted(daily.keys())
    today_val = daily[sorted_dates[-1]]
    target = (datetime.fromisoformat(sorted_dates[-1]).date() - timedelta(days=days)).isoformat()
    # find the closest date in history at or before `target`
    candidates = [d for d in sorted_dates if d <= target]
    if not candidates:
        return None
    prior_val = daily[candidates[-1]]
    return _delta_pct(today_val, prior_val)


# ── Signal definitions ──────────────────────────────────────────────────────

def hopper_demand(rows: list[dict]) -> dict:
    """H100 SXM on-demand 1-GPU median across institutional providers
    (excludes Vast.ai marketplace which is too noisy for a demand signal)."""
    institutional = {"lambda", "crusoe", "nebius", "runpod"}
    h100 = _filter(
        rows,
        gpu_model="H100_SXM",
        rental_type="on_demand",
        gpu_count="1",
        provider=institutional,
    )
    daily = _daily_median(h100)
    today = max(daily) if daily else None
    headline = f"${daily[today]:.2f}/hr 中位" if today else "—"

    d30 = _trend_delta(daily, 30)
    d7 = _trend_delta(daily, 7)
    delta_str = None
    pct_for_class = d30 if d30 is not None else d7
    if d30 is not None:
        delta_str = f"{d30:+.1f}% / 30D"
    elif d7 is not None:
        delta_str = f"{d7:+.1f}% / 7D"

    return {
        "name": "Hopper 需求强度",
        "level": _classify_change(pct_for_class, falling_is="softening"),
        "headline": headline,
        "delta": delta_str,
        "detail": "Lambda/Crusoe/Nebius/RunPod H100 SXM on-demand 中位 — 价格回落代表 Hopper 需求软化",
        "tickers": ["NVDA"],
    }


def blackwell_ramp(rows: list[dict]) -> dict:
    """B200 provider count + 相对 H100 溢价。Ramp=越多 provider 上线 + 溢价收窄。"""
    inst = {"lambda", "crusoe", "nebius", "runpod"}
    b200 = _filter(rows, gpu_model="B200", rental_type="on_demand", gpu_count="1", provider=inst)
    h100 = _filter(rows, gpu_model="H100_SXM", rental_type="on_demand", gpu_count="1", provider=inst)

    today = max((r["date"] for r in b200), default=None)
    if not today:
        return {
            "name": "Blackwell ramp", "level": "n/a", "headline": "—",
            "delta": None, "detail": "B200 暂无 institutional on-demand 报价", "tickers": ["NVDA"],
        }

    today_b200 = [r for r in b200 if r["date"] == today]
    today_h100 = [r for r in h100 if r["date"] == today]
    n_providers = len({r["provider"] for r in today_b200})
    b_med = _median_price(today_b200)
    h_med = _median_price(today_h100)
    premium = ((b_med - h_med) / h_med * 100) if (b_med and h_med) else None

    headline = f"{n_providers} provider · " + (f"+{premium:.0f}% vs H100" if premium else "—")

    # delta: shrinking premium = ramp accelerating
    daily_b = _daily_median(b200)
    daily_h = _daily_median(h100)
    common_dates = sorted(set(daily_b) & set(daily_h))
    daily_premium = {d: ((daily_b[d] - daily_h[d]) / daily_h[d] * 100) for d in common_dates}
    pct = _trend_delta(daily_premium, 30) if len(daily_premium) >= MIN_DAYS_FOR_DELTA else None
    delta_str = f"premium {pct:+.1f}% / 30D" if pct is not None else None

    # ramp signal: shrinking premium → tightening (good for NVDA),
    # expanding premium → softening (Blackwell stuck at premium)
    level = "stable"
    if pct is not None:
        if pct < -5:
            level = "tightening"
        elif pct > 5:
            level = "softening"

    return {
        "name": "Blackwell ramp",
        "level": level,
        "headline": headline,
        "delta": delta_str,
        "detail": f"B200 在 {n_providers} 家 institutional 上线 · 相对 H100 溢价收窄即 ramp 加速",
        "tickers": ["NVDA"],
    }


def amd_competition(rows: list[dict]) -> dict:
    """MI300X 折价 vs H100 secure on RunPod (controls for provider). Discount
    收窄 → AMD 抢份额。"""
    mi = _filter(rows, gpu_model="MI300X", rental_type="on_demand", gpu_count="1", provider="runpod")
    h100 = _filter(rows, gpu_model="H100_SXM", rental_type="on_demand", gpu_count="1", provider="runpod")

    today = max((r["date"] for r in mi), default=None)
    if not today:
        return {
            "name": "AMD 竞争",
            "level": "n/a",
            "headline": "—",
            "delta": None,
            "detail": "MI300X 暂无 RunPod 报价",
            "tickers": ["AMD"],
        }
    mi_today = _median_price([r for r in mi if r["date"] == today])
    h_today = _median_price([r for r in h100 if r["date"] == today])
    discount = (1 - mi_today / h_today) * 100 if (mi_today and h_today) else None
    headline = f"MI300X ${mi_today:.2f} · vs H100 折价 {discount:.0f}%" if discount is not None else "—"

    daily_mi = _daily_median(mi)
    daily_h = _daily_median(h100)
    common = sorted(set(daily_mi) & set(daily_h))
    daily_disc = {d: (1 - daily_mi[d] / daily_h[d]) * 100 for d in common}
    pct = _trend_delta(daily_disc, 30) if len(daily_disc) >= MIN_DAYS_FOR_DELTA else None
    delta_str = f"discount {pct:+.1f}pp / 30D" if pct is not None else None

    # discount narrowing (negative delta) → AMD competitive (tightening)
    level = "stable"
    if pct is not None:
        if pct < -3:
            level = "tightening"
        elif pct > 3:
            level = "softening"

    return {
        "name": "AMD 竞争",
        "level": level,
        "headline": headline,
        "delta": delta_str,
        "detail": "MI300X 相对 H100 折价收窄 → AMD 议价能力上升 / 折价扩大 → 仍以便宜替代定位",
        "tickers": ["AMD"],
    }


def neocloud_margin(rows: list[dict]) -> dict:
    """High-tier neocloud premium (Lambda + Crusoe) vs commodity tier (RunPod + Nebius).
    H100 SXM on-demand. Spread 扩大 = 高端 neocloud 议价能力强 (CRWV margin healthy)."""
    high = {"lambda", "crusoe"}
    low = {"runpod", "nebius"}
    h_high = _filter(rows, gpu_model="H100_SXM", rental_type="on_demand", gpu_count="1", provider=high)
    h_low = _filter(rows, gpu_model="H100_SXM", rental_type="on_demand", gpu_count="1", provider=low)

    today = max(({r["date"] for r in h_high} | {r["date"] for r in h_low}), default=None)
    if not today:
        return {
            "name": "Neocloud 利润",
            "level": "n/a",
            "headline": "—",
            "delta": None,
            "detail": "数据不足",
            "tickers": ["CRWV", "NBIS"],
        }
    high_med = _median_price([r for r in h_high if r["date"] == today])
    low_med = _median_price([r for r in h_low if r["date"] == today])
    spread = (high_med - low_med) / low_med * 100 if (high_med and low_med) else None
    headline = (
        f"高端 ${high_med:.2f} · 普通 ${low_med:.2f} · 价差 {spread:+.0f}%"
        if spread is not None else "—"
    )

    daily_high = _daily_median(h_high)
    daily_low = _daily_median(h_low)
    common = sorted(set(daily_high) & set(daily_low))
    daily_spread = {d: (daily_high[d] - daily_low[d]) / daily_low[d] * 100 for d in common}
    pct = _trend_delta(daily_spread, 30) if len(daily_spread) >= MIN_DAYS_FOR_DELTA else None
    delta_str = f"spread {pct:+.1f}pp / 30D" if pct is not None else None

    # spread widening → high-tier neocloud retains margin (tightening = good for CRWV)
    level = "stable"
    if pct is not None:
        if pct > 5:
            level = "tightening"
        elif pct < -5:
            level = "softening"

    return {
        "name": "Neocloud 利润",
        "level": level,
        "headline": headline,
        "delta": delta_str,
        "detail": "Lambda/Crusoe (高端) vs RunPod/Nebius (commodity) H100 价差 — 价差扩大 = 高端议价能力强",
        "tickers": ["CRWV", "NBIS"],
    }


def prosumer_compute(rows: list[dict]) -> dict:
    """RTX 4090/5090 中位价 (Vast 全球碎片市场)。无地理细分时给个全球中位。
    P5 加 region split (US vs CN/JP)。"""
    rtx = _filter(
        rows,
        gpu_model={"RTX_4090", "RTX_5090"},
        rental_type="on_demand",
        provider="vast.ai",
    )
    today = max((r["date"] for r in rtx), default=None)
    if not today:
        return {
            "name": "Prosumer 算力",
            "level": "n/a",
            "headline": "—",
            "delta": None,
            "detail": "Vast.ai 4090/5090 暂无数据",
            "tickers": ["NVDA"],
        }
    today_rows = [r for r in rtx if r["date"] == today]
    by_gpu: dict[str, float | None] = {}
    for g in ("RTX_4090", "RTX_5090"):
        sub = [r for r in today_rows if r["gpu_model"] == g]
        by_gpu[g] = _median_price(sub) if sub else None
    headline_parts = []
    if by_gpu.get("RTX_4090"):
        headline_parts.append(f"4090 ${by_gpu['RTX_4090']:.2f}")
    if by_gpu.get("RTX_5090"):
        headline_parts.append(f"5090 ${by_gpu['RTX_5090']:.2f}")
    headline = " · ".join(headline_parts) if headline_parts else "—"

    # delta on RTX 4090 (longest-tracked)
    daily = _daily_median([r for r in rtx if r["gpu_model"] == "RTX_4090"])
    pct = _trend_delta(daily, 30) if len(daily) >= MIN_DAYS_FOR_DELTA else None
    delta_str = f"4090 {pct:+.1f}% / 30D" if pct is not None else None

    return {
        "name": "Prosumer 算力",
        "level": _classify_change(pct, falling_is="softening"),
        "headline": headline,
        "delta": delta_str,
        "detail": "Vast.ai 全球 4090/5090 中位 — 价格上涨可能反映中国/亚太本地 AI 抢算力",
        "tickers": ["NVDA"],
    }


# ── Investment Notes ─────────────────────────────────────────────────────────

LEVEL_TONE = {
    "tightening": "积极",
    "stable": "稳定",
    "softening": "走软",
    "n/a": "数据不足",
}


def investment_notes(signals: list[dict]) -> list[str]:
    """Generate 2-3 sentences of narrative from signal combinations."""
    by_name = {s["name"]: s for s in signals}
    notes: list[str] = []

    h = by_name.get("Hopper 需求强度", {})
    b = by_name.get("Blackwell ramp", {})
    a = by_name.get("AMD 竞争", {})
    n = by_name.get("Neocloud 利润", {})

    # NVDA composite read
    h_lvl = h.get("level", "n/a")
    b_lvl = b.get("level", "n/a")
    if h_lvl == "tightening" and b_lvl == "tightening":
        notes.append("**NVDA**: H100 价格走强 + B200 溢价收窄，Hopper 需求未消化 + Blackwell ramp 加速，双重利好。")
    elif h_lvl == "softening" and b_lvl != "tightening":
        notes.append("**NVDA**: H100 价格走软，Hopper 周期可能见顶；Blackwell 尚未接力。需关注下季 capex guidance。")
    elif h_lvl == "stable" and b_lvl == "stable":
        notes.append(f"**NVDA**: Hopper 需求 {LEVEL_TONE[h_lvl]} ({h.get('headline', '—')})，Blackwell {LEVEL_TONE[b_lvl]} ({b.get('headline', '—')}) — 等更长时间窗才能定方向。")
    else:
        notes.append(f"**NVDA**: Hopper {LEVEL_TONE[h_lvl]}, Blackwell {LEVEL_TONE[b_lvl]} — 关注 H100 spot 与 B200 溢价两个领先指标。")

    # AMD read
    a_lvl = a.get("level", "n/a")
    if a_lvl == "tightening":
        notes.append("**AMD**: MI300X 折价收窄，议价能力上升 — 财报关注 Datacenter GPU 收入指引。")
    elif a_lvl == "softening":
        notes.append("**AMD**: MI300X 折价扩大，仍以「便宜 H100」定位卖方议价 — 短期不利。")
    elif a.get("headline") and a.get("headline") != "—":
        notes.append(f"**AMD**: {a['headline']} — 折价中位档，无方向性信号。")

    # CRWV/NBIS read
    n_lvl = n.get("level", "n/a")
    if n_lvl == "tightening":
        notes.append("**CRWV / NBIS**: 高端 vs 普通 neocloud 价差扩大，CRWV gross margin 可持续；NBIS 抢市占空间增大。")
    elif n_lvl == "softening":
        notes.append("**CRWV / NBIS**: 高端价差被 commodity tier 蚕食 — neocloud 价格战风险上升。")

    return notes[:3]


# ── Public API ───────────────────────────────────────────────────────────────

def all_signals() -> list[dict]:
    rows = load()
    if not rows:
        return []
    return [
        hopper_demand(rows),
        blackwell_ramp(rows),
        amd_competition(rows),
        neocloud_margin(rows),
        prosumer_compute(rows),
    ]


def signal_count_history() -> tuple[int, str | None]:
    rows = load()
    if not rows:
        return 0, None
    dates = sorted({r["date"] for r in rows})
    return len(dates), dates[-1] if dates else None


if __name__ == "__main__":
    sigs = all_signals()
    if not sigs:
        print("no observations")
        raise SystemExit(1)
    n_days, last_date = signal_count_history()
    print(f"=== Signals as of {last_date} ({n_days} day(s) of history) ===")
    for s in sigs:
        delta = f"  ({s['delta']})" if s.get("delta") else ""
        print(f"\n  [{s['level'].upper():10s}] {s['name']}: {s['headline']}{delta}")
        print(f"    → {s['detail']}")
        print(f"    tickers: {', '.join(s.get('tickers') or [])}")

    print("\n=== Investment Notes ===")
    for note in investment_notes(sigs):
        print(f"  • {note}")
