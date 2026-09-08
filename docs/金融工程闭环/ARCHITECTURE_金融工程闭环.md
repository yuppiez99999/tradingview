# ARCHITECTURE 金融工程闭环流水线

> **文档类型**：架构设计方案 — 数据清洗 → Alpha信号生成 → 回测验证 → 自动执行 → 风控监控 闭环
> **创建日期**：2026-08-02
> **版本**：v1.0
> **关联架构**：`docs/自我进化框架/ARCHITECTURE_自我进化框架.md` v2.0
> **关联模块**：`utils/pipeline/`、`qlib/`、`utils/risk_guard_integrator.py`、`utils/kill_switch.py`
> **状态注记（2026-09-08）**：qlib_lgb_v2 shadow（W7.2.9）已于 09-07 按 ROADMAP R-6 停跑归档（双设计缺陷口径：接线错配 + 信号静态）。本文 §2.3 AlphaPipeline 为独立组件——`alpha.enabled` 默认 False 且带 `_QLIB_AVAILABLE` 优雅降级（回退本地因子），代码不受影响；qlib 腿重开前提 = 先修接线错配（2027 Q1 前默认不排期）。当前权威状态见 `cairn/ROADMAP.md`。
> **实现偏差注记（2026-09-08）**：① `configs/pipeline_config.yaml` 从未创建（`utils/pipeline/config.py` 的 DEFAULT_CONFIG_PATH 指向该路径，实际靠默认值运行）；② `scripts/run_pipeline_daemon.py` 未实现（现有 `run_pipeline.py` / `run_pipeline_factor_offline.py`）；③ 流水线测试实际位于 `tests/unit/test_pipeline.py` 等，非本文 §6 的 `tests/pipeline/` 目录结构。

---

## 0. 设计哲学

### 0.1 核心原则

1. **不重复造轮子** — 系统已有 80% 基础设施（DataQualityMonitor / SignalFusionEngine / ExecutionAlgoEngine / KillSwitch等），本架构仅做编排集成
2. **Qlib 作为 Alpha 引擎** — 利用 qlib 完备的因子计算/模型训练/回测框架，接入系统信号融合
3. **Fail-closed 降级** — 每一阶段失败时回退到上一安全状态，不阻塞交易
4. **Feature Flag 灰度** — 全部流水线默认 `False`，双签启用，影子账户先行

### 0.2 与自我进化框架的关系

```
自我进化框架 (L1/L2/L3)         金融工程闭环流水线
├── DriftMonitor (漂移检测)      ← 流水线触发者
├── EvolutionMemory (审计日志)   ← 流水线记录者
├── FeedbackLoop (权重调整)      ← 流水线输出消费者
└── EvolutionGuard (安全护栏)    ← 流水线检查者
```

金融工程闭环是自我进化框架在"数据→信号→执行"链路的具体实现。

---

## 1. 系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PipelineOrchestrator (核心编排器)                      │
│                                                                         │
│  1. DataCleaningPipeline       2. AlphaPipeline       3. BacktestGate   │
│  ┌─────────────────────┐    ┌──────────────────┐    ┌──────────────┐    │
│  │ MultiSourceValidator │    │ QlibDataLoader   │    │ WalkForward   │    │
│  │ OutlierFilter        │ →  │ Alpha158/360因子 │ →  │ DSR校验       │ →  │
│  │ GapFiller            │    │ LGBM/Transformer  │    │ StressTest   │    │
│  │ DataGate             │    │ SignalFusion注入  │    │ CRO Gate     │    │
│  └─────────────────────┘    └──────────────────┘    └──────────────┘    │
│                                                                         │
│        ↓ 信号通过                ↓ 信号通过              ↓ 信号通过      │
│                                                                         │
│  4. ExecutionPipeline          5. RiskMonitor (独立线程)                │
│  ┌─────────────────────┐    ┌──────────────────────────┐               │
│  │ OrderGenerator      │    │ KillSwitch (L1/L2/L3)    │               │
│  │ ExecutionRouter     │    │ DrawdownBreak            │               │
│  │ BrokerAdapter       │    │ RiskGuardIntegrator      │               │
│  │ TCA归因             │    │ OvernightGapGuard        │               │
│  └─────────────────────┘    └──────────────────────────┘               │
│                                                                         │
│  ← ← ← ← ← ← ← ← ← ← ← ← ← 闭环反馈 ← ← ← ← ← ← ← ← ← ← ← ← ← ← ←  │
│  (DriftMonitor 检测到信号衰减 → 自动触发重训 → 新信号上线)              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.1 数据流

```
Wind/iFinD/TDX/AKShare (多源原始数据)
    ↓
DataCleaningPipeline (交叉验证 + 去噪 + 补缺)
    ↓
    ├── 实时行情 → DataGate (质量门控) → ExecutionPipeline
    │
    └── 历史行情 → QlibDataLoader (日频数据) → AlphaPipeline
                        ↓
                  Qlib 因子计算 (Alpha158/Alpha360)
                        ↓
                  Qlib 模型训练 (LGBM/Transformer/LSTM)
                        ↓
                  信号标准化 → 注入 SignalFusionEngine
                        ↓
                  BacktestGate (Walk-Forward + DSR + StressTest)
                        ↓  (通过)
                  ExecutionPipeline (订单生成 + 路由 + 执行)
                        ↓
                  TCA 归因 → DriftMonitor 监控信号衰减
                        ↓  (衰减)
                  ← 自动重训触发 → 回到 Qlib 模型训练
```

---

## 2. 模块详细设计

### 2.1 PipelineOrchestrator (`utils/pipeline/orchestrator.py`)

核心编排器，管理流水线的生命周期和状态机。

```
状态机: IDLE → DATA_READY → ALPHA_READY → BACKTEST_PASSED → EXECUTING → MONITORING
                    ↑            ↑              ↑              ↑            ↑
                 失败回退      失败回退        失败回退       失败暂停      衰减触发
```

**核心 API**:
```python
class PipelineOrchestrator:
    def run_full_cycle(self, mode: str = "auto") -> PipelineResult
    def run_data_cleaning_only(self) -> DataResult
    def run_alpha_only(self) -> AlphaResult
    def run_execution_only(self) -> ExecutionResult
    def get_status(self) -> PipelineStatus
    def get_latest_report(self) -> dict
```

### 2.2 DataCleaningPipeline (`utils/pipeline/data_cleaning.py`)

复用现有 `DataQualityMonitor` + `DataGate`，新增多源交叉验证。

**核心组件**:
1. **MultiSourceValidator** — Wind vs iFinD vs AKShare 三源价格交叉验证，偏离 > 1% 告警
2. **OutlierFilter** — Z-score (>3σ) + IQR + MAD 三重异常值检测
3. **GapFiller** — 线性插值 + 前向填充 + 非交易日标记
4. **DataGateWrapper** — 复用 `DataGate.check_and_gate()`，新增批量检查

**输出**: `DataQualityReport` — 标的质量评分 0-100，异常标记，缺失统计

### 2.3 AlphaPipeline (`utils/pipeline/alpha_pipeline.py`)

基于 qlib 的 Alpha 信号生成流水线，复用现有 `qlib_data_bridge`。

**核心组件**:
1. **QlibDataLoader** — 将系统历史数据转为 qlib 可消费格式 (Alpha158/Alpha360)
2. **QlibModelTrainer** — 支持 LGBM / Transformer / LSTM 三种模型，自动选择最优
3. **SignalGenerator** — 模型预测 → 标准化 [-1, 1] → 注入 `SignalFusionEngine`

**训练流程**:
```
数据准备 (2020-01-01 ~ 2024-06-30) → 训练 (2020-2023) → 验证 (2024H1) → 测试 (2024H2-至今)
                                                                              ↓
                                                                        信号标准化
                                                                              ↓
                                                                        SignalFusionEngine
```

### 2.4 BacktestGate (`utils/pipeline/backtest_gate.py`)

复用系统现有 Walk-Forward / DSR / StressTest，新增过拟合检测。

**核心组件**:
1. **WalkForwardValidator** — 复用 `utils/alpha/purged_kfold.py`
2. **DSREvaluator** — 复用 Deflated Sharpe Ratio 计算
3. **StressTestChecker** — 复用 `utils/stress_test_runner.py`
4. **CROGate** — 首席风控官检查点（人工审批闸门，L2 级自动通过，L3 级需人工）

**验收标准**:
- 样本外 IC > 0.03
- DSR > 1.0 (90% 置信)
- 四大压力场景回撤 < 15%
- 无 Look-ahead bias

### 2.5 ExecutionPipeline (`utils/pipeline/execution_pipeline.py`)

将信号转化为订单并执行，复用现有 ExecutionAlgoEngine / ExecutionRouter。

**核心组件**:
1. **OrderGenerator** — 信号强度 → 买卖方向 + 仓位比例 → 订单
2. **ExecutionRouter** — 复用 `utils/execution_router.py`
3. **BrokerAdapter** — 券商 API 适配层（QMT 桥接）
4. **TCAEngine** — 交易成本归因，复用 `utils/tca_engine.py`

### 2.6 RiskMonitor (`utils/pipeline/risk_monitor.py`)

独立于交易逻辑运行的风控守护线程，复用现有 KillSwitch / RiskGuardIntegrator。

**核心组件**:
1. **KillSwitchMonitor** — 复用 `utils/kill_switch.py`，L1 停开仓，L2 强平，L3 变现
2. **DrawdownBreak** — 硬性回撤上限 + 逐级降仓
3. **OvernightGapGuard** — 隔夜跳空保护
4. **RiskGuardIntegrator** — 日终风控守卫

---

## 3. 配置 (`configs/pipeline_config.yaml`)

```yaml
pipeline:
  # 模式
  mode: auto                          # auto / manual / dry_run
  interval_minutes: 15                # 流水线执行间隔

  # 数据清洗
  data_cleaning:
    enabled: true
    min_quality_score: 80.0           # 数据质量门限
    multi_source_check: true          # 多源交叉验证
    outlier_z_threshold: 3.0          # Z-score 异常阈值
    gap_fill_max_days: 3              # 最大补缺天数

  # Alpha 信号
  alpha:
    enabled: false                    # Feature Flag: 默认 False
    model: auto                       # auto / lightgbm / transformer / lstm
    train_interval_days: 20           # 重训间隔
    retrain_on_drift: true            # DriftMonitor 触发重训
    horizon: 5                        # 预测周期 T+5

  # 回测验证
  backtest_gate:
    enabled: false
    min_ic: 0.03
    min_dsr: 1.0
    max_drawdown: 0.15
    walk_forward_windows: 6

  # 执行
  execution:
    enabled: false
    algo: auto                        # auto / twap / vwap / is / pov
    max_slippage_bps: 10.0
    slice_minutes: 5

  # 风控
  risk_monitor:
    enabled: true                     # 默认开启（防御层）
    check_interval_seconds: 30
    kill_switch_l1_margin: 0.50
    kill_switch_l2_margin: 0.65
    kill_switch_l3_margin: 0.75
    max_daily_drawdown: 0.05          # 单日最大回撤
    max_total_drawdown: 0.15          # 累计最大回撤

  # 日志
  logging:
    level: INFO
    report_dir: reports/pipeline/
    memory_write: true                # 写入 EvolutionMemory
```

---

## 4. 与现有系统的集成点

| 新模块 | 消费方 | 集成方式 |
|--------|--------|----------|
| `PipelineOrchestrator` | `15_每日工作流/run_daily_morning.py` | 盘前自动触发 `run_full_cycle()` |
| `PipelineOrchestrator` | `15_每日工作流/run_daily_eod_workflow.py` | 盘后触发 `run_alpha_only()` 重训 |
| `DataCleaningPipeline` | `utils/data_gate.py` | 复用 DataGate 质量门控逻辑 |
| `AlphaPipeline` | `utils/signal_fusion.py` | 注入 `PostMixLayer` 第 10 层 |
| `BacktestGate` | `utils/alpha/purged_kfold.py` | 复用 Walk-Forward |
| `ExecutionPipeline` | `utils/execution_router.py` | 复用订单路由 |
| `RiskMonitor` | `utils/kill_switch.py` + `utils/risk_guard_integrator.py` | 复用熔断 + 风控守卫 |
| 全部 | `utils/evolution/memory.py` | 写入 EvolutionMemory 审计 |
| 全部 | `utils/system_check.py` | 纳入 P0 自检 C7 |

---

## 5. 安全性设计

### 5.1 Fail-closed 降级链

```
DataCleaningPipeline 失败 → 使用原始数据（风险接受）
AlphaPipeline 失败 → 沿用上次成功信号
BacktestGate 拒绝 → 阻断信号上线，告警
ExecutionPipeline 失败 → 订单不发送，告警
RiskMonitor 触发 L2/L3 → KillSwitch 强制平仓
```

### 5.2 影子账户灰度

```
前 5 日: 仅监控模式 (dry_run=true)
5-20 日: 10% 资金灰度 (AlphaPipeline dry_run+ExecutionPipeline 10%)
20+ 日: 依据 BacktestGate 结果决定是否全量上线
```

### 5.3 审计追踪

所有流水线动作写入 `reports/pipeline/` + EvolutionMemory：
- 每次流水线执行 `pipeline_run_{timestamp}.json`
- 每次信号更新 `signal_update_{date}.json`
- 每次风控触发 `kill_switch_{timestamp}.json`

---

## 6. 目录结构

```
utils/pipeline/
├── __init__.py              # 包入口，re-export
├── orchestrator.py          # 核心编排器
├── data_cleaning.py         # 数据清洗流水线
├── alpha_pipeline.py        # Qlib Alpha 信号流水线
├── backtest_gate.py         # 回测验证网关
├── execution_pipeline.py    # 执行流水线
├── risk_monitor.py          # 独立风控监控
├── config.py                # 配置加载
└── types.py                 # 共享数据结构

scripts/
├── run_pipeline.py          # CLI 入口（单次）
└── run_pipeline_daemon.py   # 守护进程（持续运行）

configs/
└── pipeline_config.yaml     # 流水线配置

tests/
└── pipeline/
    ├── test_orchestrator.py
    ├── test_data_cleaning.py
    ├── test_alpha_pipeline.py
    ├── test_backtest_gate.py
    ├── test_execution_pipeline.py
    └── test_risk_monitor.py
```

---

## 7. 工作量估算

| 模块 | 文件 | 工作量 | 风险 |
|------|------|--------|------|
| 共享类型 + 配置 | `types.py` + `config.py` | S (0.5d) | 低 |
| 数据清洗流水线 | `data_cleaning.py` | M (1d) | 低（复用为主） |
| Alpha 信号流水线 | `alpha_pipeline.py` | L (2d) | 中（Qlib 集成） |
| 回测验证网关 | `backtest_gate.py` | M (1d) | 低（复用为主） |
| 执行流水线 | `execution_pipeline.py` | M (1d) | 低（复用为主） |
| 风控监控 | `risk_monitor.py` | M (1d) | 低（复用为主） |
| 编排器 | `orchestrator.py` | L (2d) | 中（状态机） |
| CLI 入口 | `run_pipeline.py` | S (0.5d) | 低 |
| **合计** | **8 文件** | **~9 人日** | |
