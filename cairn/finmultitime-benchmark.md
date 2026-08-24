# FinMultiTime 多模态基准数据 — LIT-5.3

> 文献: #63 FinMultiTime (2025.06)
> 代码: `tests/eval/finmultitime_benchmark.py` | 测试: `tests/unit/test_finmultitime_benchmark_unit.py`
> LOG 指针: 2026-08-24 · LIT-5.3

## 核心设计

FinMultiTime 是多市场、多时间分辨率的金融时间序列基准:

- **市场**: S&P500 (美股, 390分钟/天) + HS300 (沪深300, 240分钟/天)
- **分辨率**: 分钟 (1min) / 日 (1day) / 季度 (1quarter)
- **模态**: 价格 (OHLCV) + 新闻情感 + 财报指标

## 数据结构

```
MarketData
├── minute: pd.DataFrame (OHLCV, 分钟级)
├── daily: pd.DataFrame (OHLCV, 日级)
├── quarterly: pd.DataFrame (OHLCV 聚合, 季度级)
├── news_sentiment: pd.DataFrame (sentiment/news_count/relevance)
└── fundamentals: pd.DataFrame (revenue/earnings/pe_ratio/debt_ratio)

AlignedDataset
├── markets: dict[str, MarketData] (SP500 + HS300)
├── aligned_dates: pd.DatetimeIndex (对齐交易日)
└── alignment_stats: dict (对齐统计)
```

## 生成方法

### 1. OHLCV 生成 (几何布朗运动)
- 日数据: GBM(drift, vol) — SP500 drift=0.0003, vol=0.012; HS300 drift=0.0002, vol=0.015
- 分钟数据: 从日参数缩放 (drift/min_per_day, vol/sqrt(min_per_day))
- 季度数据: 日数据 resample("QE") 聚合

### 2. 新闻情感
- sentiment: N(0, 1) — 情感分数
- news_count: 均匀分布 [0, 50]
- relevance: 均匀分布 [0, 1]

### 3. 财报指标
- revenue: LogNormal(20, 0.5) — 营收
- earnings: N(0, 1e8) — 盈利
- pe_ratio: Uniform(5, 50) — 市盈率
- debt_ratio: Uniform(0, 1) — 负债率

## 验证指标

| 指标 | 方法 | 结果 |
|------|------|------|
| 时间戳对齐 | aligned_dates ⊂ 每个市场日数据索引 | ✅ |
| 分辨率一致性 | 分钟收盘 ≈ 日收盘 (抽查5天) | ✅ |
| 跨市场覆盖率 | aligned_total / total_dates | 100% |
| 多模态完整性 | 非空模态数 / 总模态数 | 100% |

## CLI 演示结果

```
SP500: 分钟98280行, 日252行, 季5行, 新闻252行, 财报5行
HS300: 分钟60480行, 日252行, 季5行, 新闻252行, 财报5行
时间戳对齐: ✅, 分辨率一致性: ✅, 跨市场覆盖率: 100.0%, 多模态完整性: 100.0%
```

## 后续依赖

- LIT-5.4 RAG+RL 情感分析 (无依赖, 可并行)
- LIT-5.5 排序损失评估 (依赖 LIT-5.1)
- LIT-5.6 全量集成验收 (依赖全部)