---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-02
contains: data-provider, cleaning-pipeline, alpha-pipeline, backtest-gate, pipeline-orchestrator, multi-source-validation, data-quality
related:
  - cairn/alpha-factor-system.md
  - cairn/backtest-standards.md
  - cairn/risk-architecture.md
---

# 数据管道架构

> 记录项目的数据管道六阶段闭环架构：从多源数据采集到清洗验证、Alpha 信号生成、回测网关、执行、风控监控。对应 `utils/pipeline/` + `utils/data_provider.py` + `utils/purged_kfold.py`。

## 一、六阶段闭环流水线

状态机驱动（`utils/pipeline/types.py`）：

```
IDLE → DATA_CLEANING → ALPHA_GENERATION → BACKTEST_GATE
    → EXECUTION → RISK_MONITOR → COMPLETED
```

编排器 `utils/pipeline/orchestrator.py` 按顺序调度，每阶段独立 fail-closed（失败回退到上一安全状态）。

## 二、L0 — 多源数据采集

核心类 `MarketDataProvider`（`utils/data_provider.py`，66.6 KB）。

### 五级降级链

```
Wind MCP (P1) → iFinD MCP (P2) → 通达信/TDX (P3) → AKShare (P4) → 新浪 HTTP (P5)
```

每种源对应实时（`_try_*_realtime`）和历史（`_try_*_historical`）两套接口。全部失败时抛 `RuntimeError`，不再返回硬编码假数据。

### 两级缓存
- 内存缓存：LRU，行情 60s TTL、历史 1d TTL
- 持久化缓存：Parquet 文件，24h TTL，`data_cache/` 目录

### 跨源符号映射

`_to_wind_code()`、`_to_sina_code()` 处理不同数据源间的代码格式转换。Wind 使用 `.SH`/`.SZ` 后缀，新浪使用 `sh`/`sz` 前缀，通达信使用纯数字。

### CrossSourceValidator（`utils/data_provider.py:1418`）

独立多源交叉校验器：
- `validate()`：实时多源价格偏差检测，中位数仲裁，偏差超 2% 标记不一致
- `validate_historical()`：历史 K 线相关系数 + 逐日偏差统计

### 扩展数据集成

- 价格预测：`get_price_prediction()`
- 外部宏观：`get_external_macro()`
- 新闻情感：`get_news_sentiment()`
- AI 日报：`get_ai_daily_report()`
- 气象数据：`WeatherDataAdapter` 4 级降级（apizero.cn → Open-Meteo → 本地缓存 → 兜底）

## 三、L1 — 数据清洗与验证

核心类 `DataCleaningPipeline`（`utils/pipeline/data_cleaning.py`）。

### 四步流水线

1. **多源交叉验证**（`_validate_multi_source`）：Wind vs iFinD vs AKShare 三源价格偏离度
2. **异常值检测**（`_detect_outliers`）：Z-score（3.0）+ IQR（1.5x）+ MAD（3.0）三重检测
3. **缺失值填充**（`_fill_gaps`）：连续 NaN 缺口天数检测 + 前值回填/行业中位数填充
4. **DataGate 质量门控**（`_check_gate`）：复用 `DataGate` 做硬拦截

### 质量评分（0-100）

扣分机制：多源偏离 >1% 扣 min(dev_pct × 5, 30) + 每个异常值扣 10 + 缺失天数扣 min(gap_days × 5, 20) + Gate 拒绝扣 30。

### 下层依赖

- `DataQualityMonitor`（`utils/data_quality_monitor.py`）：6 维质量引擎——完整性、缺失值、异常值、一致性、延迟、新鲜度评分
- `DataGate`（`utils/data_gate.py`）：质量评分 < 80 / 价格偏离 > 1% / 新鲜度超时 → 硬阻断

## 四、L2 — Alpha 信号生成

核心类 `AlphaPipeline`（`utils/pipeline/alpha_pipeline.py`）。

### 三级降级链

```
Qlib LGBM/Transformer → 本地因子库 (AlphaFactorLibrary) → 中性信号 (全 0)
```

信号标准化到 [-1, 1]，注入 `SignalFusionEngine` 作为第 10 层（`pipeline_alpha`），保守权重 10%。

### 过拟合防护
- `utils/purged_kfold.py`：De Prado (2018) Purged K-Fold，purge + embargo 双重时间隔离
- `utils/alpha/data_contract.py`：数据契约——冻结 schema + point-in-time 切片校验，GAP-8 交付物
- `overfitting_diagnosis()`：前半折/后半折衰减检测 + CV 变异系数 + 极值偏离比三重诊断

## 五、L3 — 回测验证网关

核心类 `BacktestGate`（`utils/pipeline/backtest_gate.py`）。

### 四道闸门

1. Walk-Forward 回测——滚动窗口样本外验证
2. Deflated Sharpe Ratio (DSR > 1.0)——多重假设检验校正
3. 四大压力场景——复用 `StressTestRunner`
4. CRO Gate——首席风控官检查

验收标准：IC > 0.03, DSR > 1.0, 最大回撤 < 15%。不过闸门不上线。

## 六、L4+L5 — 执行与风控监控

- **ExecutionPipeline**（`utils/pipeline/execution_pipeline.py`）：信号 → 目标持仓 → 订单 → TWAP/VWAP 路由。`dry_run` 默认开启，实盘需 `confirmation_token`。
- **RiskMonitor**（`utils/pipeline/risk_monitor.py`）：独立线程运行，不阻塞交易。KillSwitch 三级熔断（保证金 50%/65%/75%），回撤保护 + 隔夜跳空保护。

## 七、关键设计决策

**坏数据不交易**：`DataGate` 在数据质量评分 < 80 时硬阻断整条流水线。这是数据管道的第一原则——宁可错过交易机会也不能用脏数据决策。

**Fail-Fast + 优雅降级共存**：数据源层全部失败抛 RuntimeError（不返回假数据），模块层导入用 try/except + `_HAS_*` 标志优雅降级。每阶段 fail-closed 回退到上一安全状态。

**配置驱动**：`configs/pipeline_config.yaml` 控制全部六阶段开关，环境变量覆盖（`PIPELINE_MODE`, `PIPELINE_ALPHA_ENABLED` 等）。

**G1 FIX 多源校验**（2026-08-02）：`MarketDataProvider.run_cross_source_validation()` 中位数仲裁，偏差超 2% 标记不一致。`DataCleaningPipeline._validate_multi_source()` 流水线内集成三源比价。

**FactorDecayMonitor 集成**（v8.5）：滚动窗口 IC 趋势检测，与模型退役标准联动。`utils/alpha/factor_decay_monitor.py`。

**气象数据插件架构**：`WeatherDataAdapter` 4 级降级链独立于主数据源降级链，通过 `utils/weather_data_adapter.py` 统一接口注入。

## 八、相关的外部依赖
- 气象 API：apizero.cn + Open-Meteo（开源免费气象 API）
- Wind MCP / iFinD MCP：金融终端数据接口
- 通达信（TDX）：免费行情客户端数据源
- AKShare：开源 Python 金融数据接口库
- 新浪财经 HTTP：最后的兜底方案

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码架构与模块导航](architecture-map.md) (相似度 11%)
- [代码审查经验沉淀：量化系统 8.4 资金安全与回测可信度](code-review-lessons-v8.4.md) (相似度 8%)
- [回测标准](backtest-standards.md) (相似度 8%)
- [GNN 供应链产业链因子落地设计](gnn-supply-chain-factor.md) (相似度 7%)
- [自我进化框架](self-evolution-framework.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
