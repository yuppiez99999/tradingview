---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-02
contains: module-map, dependency-graph, entry-points, data-flow, code-navigation
related:
  - cairn/alpha-factor-system.md
  - cairn/data-pipeline.md
  - cairn/model-training.md
  - cairn/risk-architecture.md
---

# 代码架构与模块导航

> 项目模块功能边界、依赖关系图、核心入口点与数据流向。用于快速定位代码和理解系统骨架。优先使用 code-review-graph MCP 工具（`cairn/code-review-graph-guide.md`）做具体代码探索。

## 一、一级模块功能边界

```
e:/各种PY程序/28-终极量化交易系统8.4/
│
├── utils/                  ★★★ 核心工具库 (60+ 模块，所有模块的底层依赖)
├── lgb_trainer/            ★  LightGBM 增强训练器子包
├── ai_decision/            ★  多AI辩论共识决策引擎
├── ms_strategy/            ★  机构级策略库 (因子/回测/风控/对冲/WonderTrader)
├── realtime_monitor/       ★  盘中实时行情监控
├── reporting/              ★  盘后报告子模块 (PnL/对冲/次日计划)
├── 15_每日工作流/           ★★ 生产调度中枢 (晨间+EOD 工作流入口)
├── scripts/                运维脚本与辅助入口
├── config/                 运行时配置 (positions.json/因子权重/阈值)
├── data_pipeline/          数据管道骨架 (ingestion/cleaning/features/serving)
├── ai/                     AI 推荐生成器
├── tests/                  测试套件 (unit/integration)
├── models/                 训练好的模型存储
├── data/ + data_cache/     原始数据与缓存
├── docs/                   文档
├── _archive/               归档历史代码
│
└── 核心顶层脚本 (单文件入口):
    ├── daily_trade_executor.py         每日自动交易执行
    ├── build_plan_executor.py          建仓计划执行器
    ├── generate_daily_report.py        收盘盈亏报告 (DeepSeek AI)
    ├── institutional_pipeline_runner.py 机构级量化闭环运行器
    ├── live_scheduler.py               实时监控并发调度器
    ├── alpha_hedge_engine.py           Alpha 对冲引擎
    ├── autolearn_trainer.py            自动学习训练器
    └── lgb_enhanced_trainer.py         LGB 增强训练协调器
```

## 二、模块详细说明

### utils/ — 核心工具库

所有模块的共同依赖。60+ 个 .py 文件和 8 个子包。关键组件：

| 组件 | 文件 | 功能 |
|------|------|------|
| 数据提供 | `data_provider.py` (66KB) | 五级降级多源数据 |
| 数据质量 | `data_gate.py` + `data_quality_monitor.py` | 坏数据不交易 |
| 流水线 | `pipeline/` 子包（8 文件） | 六阶段闭环编排 |
| Alpha | `alpha/` 子包（因子库/合同/注册表/调度/漂移/MLOps） | 信号+模型生命周期 |
| 执行 | `execution/` 子包 | TWAP/VWAP/路由/TCA |
| 风控 | `risk/` 子包 | Vega/KillSwitch/压力测试 |
| 进化 | `evolution/` 子包 | Feature Flag/基础设施 |
| 对冲 | 独立模块 | hedge_execution_engine/protective_put_engine |
| 配置 | `master_config_manager.py` | 单一事实源一致性校验 |
| 金融引擎 | `fineng/` 子模块 | BS 定价/GARCH/Kalman/EVT |
| 气象 | `weather_data_adapter.py` | 4 级降级气象数据 |
| 信号融合 | `signal_fusion.py` | 10 层信号加权融合引擎 |
| 投资组合优化 | `institutional_optimizer.py` | InstitutionalOptimizer |
| 因子管理器 | `factor_manager.py` | 11 大类因子调度 |

### lgb_trainer/ — 模型训练子包

7 个模块 + 1 个 `__init__.py`：

| 文件 | 职责 |
|------|------|
| `trainer.py` | 核心训练器：GPU 回退、单标的训练、Regime V9 双模型、流程编排 |
| `data_loader.py` | 多源 OHLCV 加载 + 12h parquet 缓存 |
| `feature_engineering.py` | 均值回归/Regime-Aware/行业/资金/跨市场特征（~35 个） |
| `news_sentiment.py` | 三级加权情感词典新闻因子 |
| `metrics.py` | Purged K-Fold CV + R²/IC/Sharpe + 特征选择 |
| `persistence.py` | 模型保存/加载/重训判定（7 天过期） |
| `report_generator.py` | 三方对比 Markdown 报告 |

### ai_decision/ — 多AI辩论决策引擎

15 个模块：

| 文件 | 职责 |
|------|------|
| `debate_engine.py` | Bull/Bear/Judge 三方辩论核心 |
| `aggregator.py` | 非线性聚合（Brier 权重 + 语义去重） |
| `hard_risk_gate.py` | 硬风控门——辩论结果不可覆盖 |
| `mode_manager.py` | Shadow/Paper/Auto 三态模式管理 |
| `bridge.py` | 执行桥接 + 灰度发布 |
| `providers/` | 5 模型 Provider 抽象层（含 Mock 降级） |
| `monitor/` | 模型健康监控 |

### ms_strategy/ — 机构级策略库

WonderTrader 集成 + 因子库 + 回测 + 风控 + 对冲 + 执行：

| 子目录 | 职责 |
|--------|------|
| `factors/` | GTJA191 因子（21 因子纯 Python） |
| `backtest/` | Qlib 回测 + LGB 回测运行 |
| `risk/` | 风控守卫 + 止损监控 |
| `hedge/` | 对冲信号生成 |
| `execution/` | 执行中间件 |
| `src/ml/` | 漂移检测器 + 因子监控 |
| `wondertrader/` | WonderTrader 策略实现 |

## 三、依赖图谱

依赖方向严格单向——上层依赖于下层，无循环：

```
级 0: utils/  (无项目内依赖，仅依赖第三方库)
       ↑
级 1: lgb_trainer/  ms_strategy/  ai_decision/  realtime_monitor/
       ↑
级 2: reporting/  (依赖 utils + lgb_trainer 输出)
       ↑
级 3: 15_每日工作流/  (编排上述所有模块)
       ↑
级 4: 顶层入口脚本  (daily_trade_executor, institutional_pipeline_runner, live_scheduler)
```

关键依赖细节：

- `lgb_trainer/` → `utils/`（数据提供、信号融合、因子管理器）
- `ai_decision/` → `utils/`（KillSwitch、执行路由器、因子评估器）
- `reporting/` → `utils/`（价格获取、交易日历、路径配置）
- `15_每日工作流/` → `lgb_trainer/` + `utils/` + `reporting/` + `config/`
- `live_scheduler.py` → `utils/` + `ai_decision/` + `realtime_monitor/` + `reporting/`
- `institutional_pipeline_runner.py` → `utils/pipeline/` 全套

## 四、核心入口点

| 入口 | 命令 | 用途 |
|------|------|------|
| 每日交易 | `python daily_trade_executor.py` | 盘前检查→盘中监控→盘后风控→报告 |
| 机构闭环 | `python institutional_pipeline_runner.py` | 六阶段量化闭环：数据→Alpha→回测→执行→风控 |
| 实时监控 | `python live_scheduler.py --live` | 盘中并发调度：信号刷新+风控+策略评估 |
| 建仓执行 | `python build_plan_executor.py` | 分批建仓计划执行 |
| 模型训练 | `python lgb_enhanced_trainer.py` | 增强模型训练入口 |
| 自动重训 | `python run_auto_retrain.py` | 每日自动重训（定时/漂移触发） |
| 晨间工作流 | `15_每日工作流/run_morning_workflow.bat` | 每日 08:45 开盘前信息采集+计划 |
| EOD 工作流 | `15_每日工作流/run_daily_eod_workflow.py` | 每日 15:30 收盘后五阶段闭环 |
| 对冲更新 | `python daily_hedge_update.py` | 每日对冲保证金调整 |
| P0 自检 | `scripts/run_p0_self_check.ps1` | 系统健康检查 |
| 影子账户 | `scripts/run_shadow_check.py` | 影子账户性能跟踪 |

## 五、数据流向

完整端到端数据流：

```
外部数据源 (Wind/iFinD/TDX/AKShare/新浪)
    │
    ▼ utils/data_provider.py (L0 采集 + 多源校验)
    │
    ▼ utils/pipeline/data_cleaning.py (L1 清洗 + 质量门控)
    │
    ├──→ utils/pipeline/alpha_pipeline.py (L2 Alpha 信号 [-1,1])
    │        │
    │        ▼ lgb_trainer/ (模型训练 + 信号生成)
    │        │
    │        ▼ SignalFusionEngine (10 层加权融合)
    │              │
    ├──────────────┤
    │              ▼
    │    ai_decision/debate_engine.py (多 AI 辩论共识)
    │              │
    │              ▼
    │    daily_trade_executor.py (订单生成)
    │              │
    ├──────────────┤
    │              ▼
    │    utils/pipeline/execution_pipeline.py (L4 TWAP/VWAP 执行)
    │              │
    │              ▼
    │    utils/pipeline/backtest_gate.py (L3 回测验证)
    │              │
    │              ▼
    └──→ utils/pipeline/risk_monitor.py (L5 KillSwitch 风控)
              │
              ▼
         reporting/ (收盘 PnL + 对冲 + 次日计划)
```

## 六、导航提示

- 想理解"数据怎么来的"：先看 `utils/data_provider.py`，再看 `utils/pipeline/data_cleaning.py`
- 想理解"信号怎么生成的"：先看 `lgb_trainer/trainer.py`，再看 `utils/signal_fusion.py`
- 想理解"订单怎么执行的"：先看 `daily_trade_executor.py`，再看 `utils/pipeline/execution_pipeline.py`
- 想理解"怎么风控的"：先看 `cairn/risk-architecture.md`，再看 `utils/pipeline/risk_monitor.py`
- 想理解"模型怎么训练和进化的"：先看 `cairn/model-training.md`，再看 `lgb_trainer/trainer.py`
- 想定位具体函数/类：用 code-review-graph MCP 的 `semantic_search_nodes_tool` 或 `query_graph_tool`

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [模型训练与生命周期](model-training.md) (相似度 12%)
- [数据管道架构](data-pipeline.md) (相似度 11%)
- [qlib 选股模型回测验证与 V9 对比](qlib-backtest-validation.md) (相似度 10%)
- [自我进化迭代再平衡闭环（Evolution-Rebalance Loop）](evolution-rebalance-loop.md) (相似度 9%)
- [自我进化框架](self-evolution-framework.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
