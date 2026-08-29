---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-02
contains: lgb-training, regime-specific, model-lifecycle, retrain-trigger, mlops, model-registry
related:
  - cairn/alpha-factor-system.md
  - cairn/backtest-standards.md
  - cairn/self-evolution-framework.md
---

# 模型训练与生命周期

> 记录 LightGBM 训练管道、Regime-Specific V9 双模型设计、模型存储与版本管理、退役机制、重训触发、MLOps 管道。对应 `lgb_trainer/`、`utils/alpha/auto_retrain_scheduler.py`、`utils/alpha/model_registry.py`。

## 一、训练管道六阶段

由 `lgb_trainer/trainer.py:run_enhanced_training` 编排：

### 阶段 1：OHLCV 数据加载（`data_loader.py`）

多源优先级：free-stockdb 本地 → Wind MCP → iFinD → 新浪 HTTP。12 小时 parquet 缓存（`cache/ohlcv/`），损坏文件自动清理。`fetch_all_real_ohlcv()` 为所有持仓标的拉取 2 年日线。

### 阶段 2：特征工程（`feature_engineering.py` + `news_sentiment.py`）

四组特征：

- **35+ 技术因子**（`autolearn_trainer.py`）：收益率、均线、波动率、RSI、MACD、布林带、ATR、Williams %R、KDJ、CMO 等
- **扩展特征 v2**（`feature_engineering.py`）：均值回归（9 个）+ Regime-Aware（8 个）+ 行业相对强度（5 个）+ 资金流向（5 个）+ 跨市场信号（6 个）
- **截面因子**（`add_cross_sectional_features`）：行业内排名、市场排名、行业超额收益
- **新闻情绪**（`news_sentiment.py`）：三级加权情感词典，多词短语优先匹配

### 阶段 3：时间序列交叉验证 + 特征选择（`metrics.py`）

Purged K-Fold TSCV（embargo = label_horizon），标签为 5 日前向收益。评估指标 R² + IC(Pearson) + Signal Sharpe。基于 CV 平均重要性的阈值 + Top-N 筛选，支持受保护特征。

### 阶段 4：LightGBM 训练（`trainer.py`）

GPU→CPU 自动回退（全局标志 + 线程锁）。80/20 时序分割，200 轮早停。自适应重训：`best_iteration <= 5` 时用更小 lr(0.001) + 更多 estimators(5000) 重训，仅 R² 改善时替换。质量标记：`quality_flag` = OK / LOW_QUALITY / NOISE。

### 阶段 5：持久化 + 信号生成（`persistence.py`）

模型 pickle 序列化 + 元数据 JSON。集成信号文件 `lgb_enhanced_signals.json`，低质量信号自动置零。

### 阶段 6（外围）：报告生成（`report_generator.py`）

Markdown 三方对比报告（旧集成 / TSCV / 增强模型），8 个章节含对比表、CV 详情、特征重要性 Top10、情绪因子统计等。

## 二、Regime-Specific 双模型（V9）

V9 的核心创新来自对 V7 系列失败的分析：单一 LGB 模型被 bear regime 主导（bear 占 47% 样本），在 bull regime 下信号完全失效。

### 四态 Regime 分类

基于 510300（沪深 300 ETF）作为大盘代理，用 MA60 + 斜率判断：

| Regime | 条件 | 含义 |
|--------|------|------|
| bull | close > MA60 且 MA60 上行 | 牛市——低波动、趋势向上 |
| bear | close < MA60 且 MA60 下行 | 熊市——高波动、系统性下跌 |
| choppy | close > MA60 且 MA60 下行 | 震荡——价格高于均线但均线走弱 |
| rebound | close < MA60 且 MA60 上行 | 反弹——价格低迷但均线开始修复 |

### 双模型结构

```
bull_model ← 仅在 bull regime 样本上训练
non_bull_model ← 在 bear + choppy + rebound 三态样本上训练
full_model ← 全样本 fallback
```

### 推理时切换

当前 regime = bull 且 bull_model 存在 → 用 bull_model；否则 non_bull_model；任一缺失 → 降级为 full_model。输出中 `selected_regime` 字段标注当前活跃模型。

### Regime-Aware 特征（训练时注入）

- 四态独热编码：`market_regime_bull/bear/choppy/rebound`
- 大盘 20 日波动/动量：`market_vol_20`、`market_mom_20`
- 交互项：`vol20_x_bull`、`mom20_x_bull` 等——让模型学到"bull 下高波动→低收益"模式

### 绩效

> 年化 19.62% / 最大回撤 9.95% / Sharpe 1.315（影子账户 10%→50%→100% 灰度发布中）

## 三、模型存储与管理

### 文件系统（`lgb_trainer/persistence.py`）

```
models/lgb_enhanced/
├── {symbol}/
│   ├── {symbol}_lgb_enhanced_model.pkl  — pickle 序列化
│   ├── {symbol}_meta.json              — 完整元数据
│   └── backups/                         — 自动备份
└── lgb_enhanced_signals.json           — 集成信号
```

元数据包含：symbol、saved_at、model_type、n_samples、selected_features、train/test period、best_iteration、adaptive_retrained、CV metrics（特征选择前后）、final_metrics（R²/IC/Sharpe）、signal、top_features、config。

### ModelRegistry（`utils/alpha/model_registry.py`）

生命周期状态机：

```
REGISTERED → STAGING → PRODUCTION → ARCHIVED
     ↑          │           │
     └──────────┘           │
     (重新测试)              └──→ 归档
```

双后端：MLflow Model Registry（优先，`USE_MODEL_REGISTRY` Feature Flag + 本地文件系统兜底。

核心 API：`register_model()`、`transition_stage()`、`promote_model()`、`archive_model()`、`load_model()`、`search_models()`、`export_registry()`。

## 四、退役机制

来自 `factor_kill_switch.py`（CIO 锁定参数）：

| 条件 | 动作 | 状态 |
|------|------|------|
| 连续 5 日 IC < 0.02 | 仓位减半 | degraded |
| 连续 10 日 IC < 0 | 自动禁用 | disabled |
| 连续 20 日 IC < 0 | 强制退役（不可恢复） | retired |
| 单日回撤 > 3% | 仓位减半 (T+0) | emergency_exit |
| 累计回撤 > 8% | 仓位减至 25% | emergency_exit |
| 累计回撤 > 12% | 全部退出 | emergency_exit |

## 五、重训触发机制

三种触发源：

**定时触发**（`persistence.py:should_retrain`）：模型年龄 ≥ 7 天（默认 `retrain_interval_days`）。

**漂移触发**（`drift_monitor.py` → `auto_retrain_scheduler.py`）：DriftMonitor 告警数 ≥ threshold_count 且严重级别 ≥ threshold_severity 时回调触发。包含 IC 衰减（连续 5 日 < 0.02）、ADWIN 概念漂移（delta=0.002）、KS 检验（p < 0.05）、PSI（> 0.25）、OOS Gap（> 5%）五维度。

**手动触发**：`python run_auto_retrain.py [--force] [--symbols]`。增量重训（仅退化模型）或全量重训（`--force`）。

**安全机制**：重训前自动备份旧模型到 `backups/`、备份失败跳过该标的（防止覆盖）、重训后自动验证新旧对比、生成 Markdown 报告归档。

## 六、MLOps 管道

`utils/alpha/mlops_pipeline.py` 编排全生命周期：

```
Train → Register → Drift Monitor → A/B Test → Promote / Rollback
```

AutoRetrainScheduler 自动编排：
1. 监听 DriftMonitor 漂移告警
2. subprocess 调用 V9 训练脚本（30 分钟超时）
3. 训练完成 → 自动注册到 ModelRegistry (STAGING)
4. 自动启动 A/B 测试（20% 流量，Hash Symbol 分流，14 天验证）
5. A/B 通过 → 晋升 PRODUCTION（不通过不自动晋升，HC-4 需人工介入）
6. 旧 PRODUCTION 版本自动归档

LiveScheduler（`live_scheduler.py`）每日收盘后的 N2-N6 策略评估驱动 MLOps 管道的日频扫描。

## 七、后续方向

- V9 双模型全量上线——完成影子账户灰度发布，推进至 Production
- A/B 测试增强——当前 14 天验证期是否可基于统计显著性动态缩短
- 特征工程自动化——AutoFactorFactory（Phase 3）替代手工特征选择
- 模型解释性——引入 SHAP 值分析辅助退役决策的人机协同

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码架构与模块导航](architecture-map.md) (相似度 12%)
- [情绪因子演化：v4.0 → v4.3](sentiment-factor-evolution.md) (相似度 12%)
- [自我进化框架](self-evolution-framework.md) (相似度 11%)
- [对冲方案 v8.7 优化 — RegimeFolio 动态阈值 + 紧急跨级 + IV 感知 + Deep Hedging + 多智能体](hedge-v87-regime-adaptive-20260827.md) (相似度 10%)
- [A股ETF + 期权对冲 + 自我再平衡子模型](etf-option-hedge-model.md) (相似度 10%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
