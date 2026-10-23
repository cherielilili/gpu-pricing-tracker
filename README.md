# GPU Pricing Tracker

每日追踪 GPU 云租金（spot + on-demand），生成投资信号面板。

## 数据源

| Provider | 接口 | 覆盖 |
|---|---|---|
| Vast.ai | 公开 API | H100/H200/H200 NVL/B200/GB200/A100/L40S/L40 + RTX 6000 全系 + 4090/5090 + MI300X/MI325X |
| RunPod | 公开 GraphQL | H100/H200/H200 NVL/B200/B300/GB200/A100/L40S/L40 + RTX 6000 全系 + 4090/5090 + MI300X/MI325X |
| Lambda Labs | HTML | H100/H200/B200/GB200/GH200/A100 |
| Together AI | HTML | H100/H200/B200 (P2，未接入) |
| Crusoe | HTML | H100/H200/A100/L40S/B300/GB200/B200/MI300X/MI355X（仅公开报价） |
| Nebius | HTML | H100/H200/B200/L40S/A100/MI300X |
| Hyperstack | HTML | H100/H200/MI300X (P2，未接入) |
| SF Compute | 公开 spot 市场 | ❌ 站点改版后无公开价格，fetcher 保留但恒返回空（数据止于 2026-08-26） |

Vast 只采 `verified` 主机（质量优先于覆盖）— 未认证机型的报价（如部分 RTX 6000 Ada）不会出现。

## RTX 6000 系列

同一 provider 内各版本价差可达 5 倍（RunPod: MaxQ $0.50 … WK $2.59），因此每个版本独立建模，绝不合并成一个中位数：

| canonical ID | 卡 | RunPod 名称 | Vast 名称 |
|---|---|---|---|
| `RTX_PRO_6000` | PRO 6000 Blackwell Server 96GB | RTX PRO 6000 | RTX PRO 6000 S |
| `RTX_PRO_6000_MAXQ` | PRO 6000 Blackwell Max-Q 96GB | RTX PRO 6000 MaxQ | RTX PRO 6000 Max-Q |
| `RTX_PRO_6000_WK` | PRO 6000 Blackwell Workstation 96GB | RTX PRO 6000 WK | RTX PRO 6000 WS |
| `RTX_6000_ADA` | RTX 6000 Ada 48GB | RTX 6000 Ada | RTX 6000Ada |
| `RTX_A6000` | RTX A6000 (Ampere) 48GB | RTX A6000 | RTX A6000 |

不追踪：MIG 切片（`PRO 6000 MIG 24/48GB`，非整卡）与 Turing 时代的 `Q RTX 6000`（遗留噪音）。`B300` 由 RunPod 与 Crusoe 追踪；`MI355X` 由 Crusoe 页面覆盖（暂无公开报价）；`MI325X` 为 GPU_ORDER 预留位（尚无已接入 provider 上线）。

Crusoe 解析器于 2026-10 重写：旧版跨卡片串价（历史 GB200 行实为 H200 价格、MI355X 行实为 MI300X 价格），且混入了 Self-Serve Deployments（推理端点）价格。现仅解析「GPU instances pricing」表（GPU model | On-demand | Current spot）。历史行不回改 — append-only。

## Schema

`data/observations.csv`:
```
date, provider, gpu_model, gpu_count, region, rental_type,
price_min_usd, price_median_usd, price_max_usd, n_offers,
source_url, fetched_at
```

每日 append；历史日期永不改写，同日重跑会按 dedup key `(date, provider, gpu_model, rental_type, region, gpu_count)` 替换当日行（幂等）。

## 投资信号（P4）

| 信号 | 关联 |
|---|---|
| Hopper 需求强度 (H100 30D 中位数变化) | NVDA 短期 |
| Blackwell ramp (B200/GB200 上线 provider 数 + 价差) | NVDA 长期 |
| AMD 竞争 (MI300X 折价 vs H100) | AMD |
| Neocloud 利润 (CRWV/Nebius/Lambda 价差) | CRWV / NBIS |
| Prosumer 算力 (4090/5090 中国 vs 美国) | 中国本地 AI |

## 运行

```bash
./update.sh                    # 一键手动刷新: fetch + report + 打开 dashboard
python3 scripts/fetch.py        # 拉取 + append
python3 scripts/report.py       # 生成 HTML
python3 -m unittest discover -s tests -v   # 模型目录一致性测试
bash runner.sh                  # 定时任务用: fetch + report + commit + push
```

## 公开链接

GitHub Pages: https://cherielilili.github.io/gpu-pricing-tracker/ (P3 上线)
