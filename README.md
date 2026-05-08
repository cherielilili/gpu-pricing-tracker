# GPU Pricing Tracker

每日追踪 GPU 云租金（spot + on-demand），生成投资信号面板。

## 数据源

| Provider | 接口 | 覆盖 |
|---|---|---|
| Vast.ai | 公开 API | NVIDIA 全系 + AMD MI300X + 4090/5090 |
| RunPod | 公开 pricing 页 | H100/H200/A100/MI300X |
| Lambda Labs | HTML | H100/H200/B200 (P2) |
| Together AI | HTML | H100/H200/B200 (P2) |
| SF Compute | 公开 spot 市场 | H100 (P2) |
| Hyperstack | HTML | H100/H200/MI300X (P2) |
| Crusoe / Nebius | HTML | H100/H200/B200 (P2) |

## Schema

`data/observations.csv`:
```
date, provider, gpu_model, gpu_count, region, rental_type,
price_per_gpu_hour_usd, source_url, fetched_at
```

每日 append，永不覆盖，dedup key = `(date, provider, gpu_model, rental_type, region, gpu_count)`。

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
python3 scripts/fetch.py        # 拉取 + append
python3 scripts/report.py       # 生成 HTML
bash runner.sh                  # 一键: fetch + report + commit + push
```

## 公开链接

GitHub Pages: https://cherielilili.github.io/gpu-pricing-tracker/ (P3 上线)
