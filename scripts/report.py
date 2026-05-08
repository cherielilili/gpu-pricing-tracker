#!/usr/bin/env python3
"""GPU Pricing Tracker — daily HTML report.

Reads:
  data/observations.csv

Output:
  reports/<date>_gpu_pricing.html  — self-contained, inline CSS, no external deps
  index.html                       — copy of latest report (for GitHub Pages)

Style: 黑灰蓝白配色, matches llm-pricing-tracker. No charts in P3 — those land
in P4 along with the investment-signal dashboard.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "data" / "observations.csv"
REPORTS_DIR = ROOT / "reports"

# GPU display order — most-watched on top.
GPU_ORDER = [
    "B200", "GB200", "B300",
    "H200", "H100_SXM", "H100_PCIE", "H100_NVL",
    "MI300X", "MI325X", "MI355X",
    "A100_SXM_80GB", "A100_SXM_40GB", "A100_PCIE_80GB", "A100_PCIE_40GB",
    "GH200",
    "L40S", "L40",
    "RTX_PRO_6000",
    "RTX_5090", "RTX_4090",
]
GPU_LABEL = {
    "B200": "B200",
    "GB200": "GB200 NVL72",
    "B300": "B300",
    "H200": "H200",
    "H100_SXM": "H100 SXM",
    "H100_PCIE": "H100 PCIe",
    "H100_NVL": "H100 NVL",
    "MI300X": "MI300X",
    "MI325X": "MI325X",
    "MI355X": "MI355X",
    "A100_SXM_80GB": "A100 SXM 80GB",
    "A100_SXM_40GB": "A100 SXM 40GB",
    "A100_PCIE_80GB": "A100 PCIe 80GB",
    "A100_PCIE_40GB": "A100 PCIe 40GB",
    "GH200": "GH200",
    "L40S": "L40S",
    "L40": "L40",
    "RTX_PRO_6000": "RTX PRO 6000",
    "RTX_5090": "RTX 5090",
    "RTX_4090": "RTX 4090",
}

PROVIDERS = ["lambda", "crusoe", "nebius", "runpod", "vast.ai", "sfcompute"]
PROVIDER_LABEL = {
    "lambda": "Lambda",
    "crusoe": "Crusoe",
    "nebius": "Nebius",
    "runpod": "RunPod",
    "vast.ai": "Vast.ai",
    "sfcompute": "SF Compute",
}

# Investment-signal hint per GPU (concise — full signal layer is P4).
GPU_NOTE = {
    "B200": "Blackwell ramp",
    "GB200": "Blackwell rack-scale",
    "B300": "Blackwell Ultra (early)",
    "H200": "Hopper refresh",
    "H100_SXM": "NVDA core demand signal",
    "H100_PCIE": "Hopper PCIe",
    "MI300X": "AMD vs H100 spread",
    "MI325X": "AMD MI325 ramp",
    "MI355X": "AMD MI355 ramp",
    "RTX_5090": "Prosumer / 中国 AI",
    "RTX_4090": "Prosumer / 长尾",
    "GH200": "ARM × Hopper",
    "L40S": "推理工作负载",
}


def load() -> list[dict]:
    with OBS.open() as f:
        return list(csv.DictReader(f))


def fmt_price(p: float | None) -> str:
    if p is None:
        return '<span class="muted">—</span>'
    return f"${p:.2f}"


def cross_provider_table(rows: list[dict], rental_type: str) -> str:
    """Build a GPU × Provider matrix of median per-GPU $/hr."""
    matrix: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["rental_type"] != rental_type:
            continue
        # Prefer 1-GPU configs to keep cross-provider comparison apples-to-apples;
        # fall back to all sizes if no 1-GPU exists.
        try:
            count = int(r["gpu_count"])
        except ValueError:
            continue
        matrix[(r["gpu_model"], r["provider"], count)].append(float(r["price_median_usd"]))

    # collapse: for each (gpu, provider), take the median across configs but
    # prefer 1-GPU rows when available
    flat: dict[tuple[str, str], float] = {}
    for (gpu, prov, count), prices in matrix.items():
        flat.setdefault((gpu, prov), []).extend(prices if count == 1 else [])
    # if a (gpu,prov) has no 1-GPU rows, fall back to all sizes
    for (gpu, prov, count), prices in matrix.items():
        if (gpu, prov) not in flat or not flat[(gpu, prov)]:
            flat.setdefault((gpu, prov), []).extend(prices)

    cells: dict[tuple[str, str], float] = {}
    for k, prices in flat.items():
        if prices:
            cells[k] = round(statistics.median(prices), 4)

    gpus_present = [g for g in GPU_ORDER if any((g, p) in cells for p in PROVIDERS)]
    if not gpus_present:
        return '<div class="empty">No data for this rental type.</div>'

    head = "".join(f"<th>{PROVIDER_LABEL[p]}</th>" for p in PROVIDERS)
    body_rows = []
    for gpu in gpus_present:
        row_prices = [cells.get((gpu, p)) for p in PROVIDERS]
        valid_prices = [p for p in row_prices if p is not None]
        if not valid_prices:
            continue
        cheap = min(valid_prices)
        pricey = max(valid_prices)
        cells_html = []
        for p in row_prices:
            if p is None:
                cells_html.append('<td class="muted">—</td>')
            elif p == cheap and p != pricey:
                cells_html.append(f'<td class="cheapest">${p:.2f}</td>')
            elif p == pricey and p != cheap:
                cells_html.append(f'<td class="priciest">${p:.2f}</td>')
            else:
                cells_html.append(f'<td>${p:.2f}</td>')
        note = GPU_NOTE.get(gpu, "")
        note_html = f'<sup class="src-tag">{note}</sup>' if note else ""
        body_rows.append(
            f"<tr><th>{GPU_LABEL[gpu]}{note_html}</th>" + "".join(cells_html) + "</tr>"
        )
    return f"""
<table class="data-table">
  <thead><tr><th>GPU</th>{head}</tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>
"""


def spot_vs_ondemand_table(rows: list[dict]) -> str:
    """Per-GPU spot vs on-demand spread — tightening signal."""
    by_gpu: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"spot": [], "on_demand": []})
    for r in rows:
        try:
            cnt = int(r["gpu_count"])
        except ValueError:
            continue
        if cnt != 1:
            continue
        rt = r["rental_type"]
        if rt not in ("spot", "on_demand"):
            continue
        by_gpu[r["gpu_model"]][rt].append(float(r["price_median_usd"]))

    body_rows = []
    for gpu in GPU_ORDER:
        if gpu not in by_gpu:
            continue
        spots = by_gpu[gpu]["spot"]
        ods = by_gpu[gpu]["on_demand"]
        if not (spots and ods):
            continue
        spot_med = statistics.median(spots)
        od_med = statistics.median(ods)
        spread = (od_med - spot_med) / od_med * 100 if od_med else 0
        spread_class = "down" if spread > 30 else ("up" if spread < 10 else "muted")
        body_rows.append(
            f"<tr><th>{GPU_LABEL[gpu]}</th>"
            f"<td>${spot_med:.2f}</td>"
            f"<td>${od_med:.2f}</td>"
            f"<td class='{spread_class}'>{spread:.0f}%</td>"
            f"<td class='muted'>{len(spots)} spot · {len(ods)} on-demand</td></tr>"
        )
    if not body_rows:
        return '<div class="empty">No GPU has both spot and on-demand observations today.</div>'
    return f"""
<table class="data-table">
  <thead><tr><th>GPU</th><th>Spot $/hr</th><th>On-demand $/hr</th><th>Discount</th><th>样本</th></tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>
<p class="hint">Discount = (on-demand − spot) / on-demand. 越大说明 spot 越便宜，市场越宽松；越小说明现货供不应求。</p>
"""


def cards_block(rows: list[dict], date: str) -> str:
    n_obs = len(rows)
    providers = sorted({r["provider"] for r in rows})
    gpu_models = sorted({r["gpu_model"] for r in rows})

    # Headline H100 SXM on-demand (1-GPU) median
    h100_prices = [
        float(r["price_median_usd"]) for r in rows
        if r["gpu_model"] == "H100_SXM" and r["rental_type"] == "on_demand" and r.get("gpu_count") == "1"
    ]
    h100_med = statistics.median(h100_prices) if h100_prices else None
    b200_prices = [
        float(r["price_median_usd"]) for r in rows
        if r["gpu_model"] == "B200" and r["rental_type"] == "on_demand" and r.get("gpu_count") == "1"
    ]
    b200_med = statistics.median(b200_prices) if b200_prices else None

    cards = [
        ("观察点", str(n_obs)),
        ("Provider", str(len(providers))),
        ("GPU 型号", str(len(gpu_models))),
        ("H100 SXM 中位 (on-demand)", f"${h100_med:.2f}/hr" if h100_med else "—"),
        ("B200 中位 (on-demand)", f"${b200_med:.2f}/hr" if b200_med else "—"),
    ]
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="label">{lbl}</div><div class="val">{val}</div></div>'
        for lbl, val in cards
    ) + '</div>'


def main() -> int:
    rows = load()
    if not rows:
        print("no observations — abort")
        return 1
    today_iso = datetime.now().strftime("%Y-%m-%d")
    today_in_data = sorted({r["date"] for r in rows})[-1]
    today_rows = [r for r in rows if r["date"] == today_in_data]

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"{today_in_data}_gpu_pricing.html"

    cross_od = cross_provider_table(today_rows, "on_demand")
    cross_spot = cross_provider_table(today_rows, "spot")
    spread = spot_vs_ondemand_table(today_rows)
    cards = cards_block(today_rows, today_in_data)

    html = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"/>
<title>GPU 云租金追踪 — {today_in_data}</title>
<style>
  :root {{
    --bg:#ffffff; --surface:#fafafa; --border:#e5e7eb;
    --text:#111827; --muted:#6b7280; --accent:#1f3a5f;
    --up:#b91c1c; --down:#0e7490;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 40px; background:var(--bg); color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','SF Pro Text',
                     'Helvetica Neue',system-ui,sans-serif;
         font-size:14px; line-height:1.6; max-width:1200px; margin-left:auto; margin-right:auto; }}
  header {{ border-bottom:1px solid var(--border); padding-bottom:16px; margin-bottom:24px; }}
  h1 {{ margin:0; font-size:22px; font-weight:600; color:var(--text); }}
  .sub {{ color:var(--muted); font-size:13px; margin-top:4px; }}
  .pill {{ display:inline-block; padding:1px 8px; background:#eef2f7; color:var(--accent);
          border-radius:3px; font-size:11px; margin-left:8px; font-weight:500; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
            gap:12px; margin-bottom:24px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:6px;
           padding:12px 16px; }}
  .card .label {{ font-size:11px; color:var(--muted); letter-spacing:0.04em; font-weight:500; }}
  .card .val {{ font-size:20px; font-weight:600; margin-top:4px; color:var(--text); }}
  section {{ margin-bottom:32px; }}
  h2 {{ font-size:15px; font-weight:600; margin:0 0 12px; padding-bottom:6px;
        border-bottom:1px solid var(--border); }}
  table.data-table {{ width:100%; border-collapse:collapse; font-size:13px;
                       font-variant-numeric:tabular-nums; }}
  .data-table th, .data-table td {{ padding:7px 10px; text-align:left;
                                     border-bottom:1px solid var(--border); }}
  .data-table thead th {{ background:var(--surface); font-weight:600; color:var(--muted);
                          font-size:12px; letter-spacing:0.02em; }}
  .data-table tbody tr:hover {{ background:#f9fafb; }}
  .data-table tbody th {{ font-weight:500; color:var(--text); background:transparent; }}
  td.cheapest {{ background:#ecfdf5; color:#047857; font-weight:600; }}
  td.priciest {{ background:#fef2f2; color:#b91c1c; font-weight:600; }}
  td.up {{ color:var(--up); font-weight:600; }}
  td.down {{ color:var(--down); font-weight:600; }}
  td.muted, .muted {{ color:var(--muted); }}
  sup.src-tag {{ font-size:9px; color:var(--muted); margin-left:4px; font-weight:400; letter-spacing:0; }}
  .empty {{ padding:16px; background:var(--surface); border:1px solid var(--border);
            border-radius:6px; color:var(--muted); text-align:center; font-size:13px; }}
  .hint {{ font-size:12px; color:var(--muted); margin-top:8px; }}
  footer {{ margin-top:32px; padding-top:16px; border-top:1px solid var(--border);
            font-size:12px; color:var(--muted); }}
  footer code {{ background:var(--surface); padding:1px 4px; border-radius:3px; font-size:11px; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
</style></head><body>

<header>
  <h1>GPU 云租金追踪 <span class="pill">v0.3 · 每日</span></h1>
  <div class="sub">{today_in_data} 快照 · 6 provider 公开 API/页面 · 投资信号面板将在 v0.4 上线</div>
</header>

{cards}

<section>
  <h2>① 跨 provider 对比 — On-Demand (单卡 $/hr 中位数)</h2>
  {cross_od}
  <p class="hint">绿色 = 该 GPU 当前最便宜 provider · 红色 = 最贵 · "—" = 该 provider 暂未公开此 GPU。</p>
</section>

<section>
  <h2>② 跨 provider 对比 — Spot / 现货</h2>
  {cross_spot}
  <p class="hint">RunPod community + Vast.ai bid 市场 + SF Compute 现货拍卖。</p>
</section>

<section>
  <h2>③ Spot vs On-Demand 折扣 — 紧缺度信号</h2>
  {spread}
</section>

<footer>
  数据来源:
  <a href="https://console.vast.ai/api/v0/bundles/">Vast.ai 公开 API</a> ·
  <a href="https://api.runpod.io/graphql">RunPod GraphQL</a> ·
  <a href="https://lambda.ai/service/gpu-cloud">Lambda Labs</a> ·
  <a href="https://crusoe.ai/cloud/pricing/">Crusoe</a> ·
  <a href="https://nebius.com/prices">Nebius</a> ·
  <a href="https://sfcompute.com/">SF Compute</a> ·
  <code>data/observations.csv</code> 每日累积，
  <a href="https://github.com/cherielilili/gpu-pricing-tracker">repo on GitHub</a>。
</footer>

</body></html>
"""

    out_path.write_text(html)
    # also copy to index.html for GitHub Pages
    (ROOT / "index.html").write_text(html)
    print(f"wrote {out_path}")
    print(f"wrote {ROOT / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
