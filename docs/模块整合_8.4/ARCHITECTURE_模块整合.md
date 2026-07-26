# ARCHITECTURE — 终极量化交易系统 8.4 模块整合（6A 阶段 2：Architect）

> 架构文档：基于 ALIGNMENT 文档 D1-D7 决策（默认按推荐候选 A 推进），设计三层保护框架与对冲基金五层分层架构。
> 创建日期：2026-07-26
> 任务编号：MIG-8.4-ARCH-001
> 架构基线：Citadel Pod + Two Sigma Research Pipeline + Renaissance Risk-First + Bridgewater Alpha Decay

---

## 0. 架构哲学

> "Alpha decays. Risk persists. Execution costs compound. Infrastructure outages are unforgivable."
> — 对标基金 CTO 级共识

本架构以**风险优先（Risk-First）**为核心原则，所有设计决策必须满足：
1. **可逆性**（Reversibility）：任何整合动作可通过 Feature Flag 一键回滚
2. **可观测性**（Observability）：每个模块的状态、PnL 贡献、风险敞口可独立度量
3. **可隔离性**（Isolation）：单个模块故障不传播到其他层
4. **可归因性**（Attributability）：每一行 PnL 可追溯到 Alpha 来源 / 执行路径 / 风险决策

---

## 1. 三层保护框架（核心设计）

### 1.1 框架总览

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 3: Feature Flag 控制                                          │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ Layer 2: Shadow Account 并行验证                                 │ │
│ │ ┌─────────────────────────────────────────────────────────────┐ │ │
│ │ │ Layer 1: Git 分支隔离                                       │ │ │
│ │ │                                                             │ │ │
│ │ │  main (生产基线 V9)                                         │ │ │
│ │ │   │                                                         │ │ │
│ │ │   ├── integration/phase1-infra         (Phase 1 基础设施)   │ │ │
│ │ │   ├── integration/phase2-llm          (Phase 2 AI 决策)     │ │ │
│ │ │   ├── integration/phase3-exec-risk     (Phase 3 执行风控)   │ │ │
│ │ │   ├── integration/phase4-signal-macro  (Phase 4 信号宏观)   │ │ │
│ │ │   └── integration/phase5-polish        (Phase 5 优化完善)   │ │ │
│ │ │                                                             │ │ │
│ │ │   合并门槛: PR Review + CI 全绿 + DSR 回归通过 + 双签       │ │ │
│ │ └─────────────────────────────────────────────────────────────┘ │ │
│ │                                                                 │ │
│ │ Shadow 准入: 14 天 + DSR>=5 + 年化>=15% + 回撤<=10% + CV<1.0    │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ Flag 矩阵: USE_INTEGRATED_* 默认=False, 双签启用, 1 键回滚          │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Layer 1 — Git 分支隔离

**分支策略**：
- `main` — 生产基线，仅接受通过全部闸门的合并，受保护分支
- `integration/phase{N}-{name}` — 各阶段整合分支，长生命周期（2-12 周）
- `feature/MIG-{N}-{slug}` — 单特性分支，短生命周期（1-3 天），从 `integration/` 拉出

**合并闸门**（必须全部满足）：
1. ✅ PR 至少 1 个 reviewer 批准
2. ✅ CI 全绿：mypy + pylint + pytest unit + pytest integration
3. ✅ V9 基线回归通过：DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0
4. ✅ ConfigManager 漂移检测通过（`get_config_source(name)` 一致性）
5. ✅ 无新增硬编码密钥（CI 用 `git secrets --scan` 验证）
6. ✅ 双签：阶段负责人 + 风控负责人

**冲突解决原则**：
- 任何对 `utils/config_manager.py` 的修改必须立即 rebase 到所有活跃 integration 分支
- 任何对 V9 训练/推理路径的修改一律拒绝（除非有书面豁免）
- `quantitative_system.py` 拆分期间，原文件保持只读，拆分版本放在 `quantitative_system_v2.py`

### 1.3 Layer 2 — Shadow Account 并行验证

**Shadow 准入分闸**（按 ALIGNMENT D3 推荐候选 C，分层准入）：

| 层级 | Shadow 准入 | 替代验证 |
|------|-------------|----------|
| L1 基础设施 | ❌ 豁免 | 单测 80% + e2e 100% + 灰度发布 24h |
| L2 数据层 | ❌ 豁免 | 数据完整性测试 + P0-P6 降级链 chaos test |
| L3 Alpha 层 | ✅ 必须 | 14 天 + DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0 |
| L4 执行层 | ✅ 必须 | 14 天 + 滑点 bps <= 基线 + 成交率 >= 95% |
| L5 风控层 | ✅ 必须 | 14 天 + 误杀率 < 5% + 漏杀率 = 0% |
| L6 调度层 | ❌ 豁免 | e2e 100% + 7 天 dry-run 对比 |

**Shadow 配置**（基于 project_memory 已确认）：
- `risk_managed=True` 默认开启
- 单因子配置：`Config_E` (target_vol=0.08)
- 因子组合配置：`Config_E_plus1` (target_vol=0.07, dd_threshold=0.018, dd_factor=0.18)
- 单日回撤 >3% 或 3 日累计回撤 >5% fail-fast 终止
- Stage 1 须满 14 天，当前仅运行 1 天（截至 2026-07-26）

**Shadow 数据流**：
```
生产信号 ──┬──> 生产执行（不受影响）
           │
           └──> Shadow 账户（独立记账，独立风控，独立归因）
                  │
                  ├── Daily DSR 计算
                  ├── Daily 归因报告
                  └── 异常告警 → 风控负责人
```

### 1.4 Layer 3 — Feature Flag 控制

**Flag 注册表**（`v8.3_institutional/config/feature_flags.yaml`，纳入 ConfigManager 4 级优先级解析）：

```yaml
# Feature Flag 注册表 — 所有整合模块默认关闭，双签启用
# 规则：flag 默认值必须 = 当前生产行为（不改变现状）

flags:
  # L1 基础设施层
  USE_INTEGRATED_BOOTSTRAP:
    default: false
    description: "使用新 bootstrap 模块替代分散的初始化逻辑"
    requires: [L1单测80%, e2e100%]
    rollback_seconds: 5  # 热加载回滚
  
  USE_INTEGRATED_CORE_REGISTRY:
    default: false
    description: "使用新策略注册表替代 quantitative_system.py 内嵌注册"
    requires: [L1单测80%]
    rollback_seconds: 5
  
  USE_INTEGRATED_DATA_LAYER:
    default: false
    description: "使用新 data_layer 模块统一数据降级链"
    requires: [L2数据完整性测试, P0-P6 chaos test]
    rollback_seconds: 10
  
  # L3 Alpha 层
  USE_LLM_REPORT_ANALYZER:
    default: false
    description: "使用多模型 LLM 路由器替代单一 llm_client"
    requires: [Shadow 14天]
    rollback_seconds: 5
    fallback: "llm_client.py 原路径"
  
  USE_DECISION_THEORIES_FUSION:
    default: false
    description: "启用四大决策理论融合（价值/动量/质量/情绪）"
    requires: [Shadow 14天, DSR>=5]
    rollback_seconds: 5
  
  # L4 执行层
  USE_AUTOMATED_EXECUTION_ROUTER:
    default: false
    description: "使用新订单路由器替代现有 execution_router"
    requires: [Shadow 14天, 滑点<=基线]
    rollback_seconds: 0  # 立即回滚（执行层不能延迟）
  
  # L5 风控层
  USE_RISK_BUS_EVENT_DRIVEN:
    default: false
    description: "启用事件驱动风控总线"
    requires: [Shadow 14天, 误杀率<5%]
    rollback_seconds: 0
    critical_path: true  # 关键路径，变更需 CTO 级审批
  
  # L6 调度层
  USE_DAILY_WORKFLOW_V2:
    default: false
    description: "使用新三阶段工作流（盘前/盘中/盘后）"
    requires: [e2e100%, 7天dry-run]
    rollback_seconds: 30
```

**Flag 操作规则**：
- 启用：双签（变更发起人 + 风控负责人）+ 24h 观察
- 禁用：单签即可（任何风控人员可一键关闭）
- 监控：每个 flag 启用后产生 `reports/flag_audit/{flag_name}.jsonl` 审计日志
- 默认值铁律：`default` 必须等于当前生产行为，禁止"默认启用新模块"

---

## 2. 五层模块分层架构

### 2.1 目标分层（ALIGNMENT D1 推荐候选 A）

```
utils/
├── infra/                          # L1 基础设施
│   ├── __init__.py                 #   re-export 到 utils 顶层（向后兼容）
│   ├── bootstrap.py                #   新建：全局单例/异常处理/启动顺序
│   ├── core.py                     #   新建：策略注册/性能追踪/成本计算
│   ├── config_manager.py           #   迁移自 utils/config_manager.py
│   ├── logger.py                   #   迁移自 utils/logger.py
│   ├── trading_env.py              #   迁移自 utils/trading_env.py
│   └── feature_flags.py            #   新建：Flag 注册表 + 热加载
│
├── data/                           # L2 数据层
│   ├── __init__.py
│   ├── data_layer.py               #   新建：统一数据降级链 P0-P6
│   ├── data_provider.py            #   迁移自 utils/data_provider.py
│   ├── data_gate.py                #   迁移自 utils/data_gate.py
│   ├── data_quality_monitor.py     #   迁移自 utils/data_quality_monitor.py
│   ├── akshare_source.py           #   迁移自 utils/akshare_data_source.py
│   ├── ifind_client.py             #   迁移自 utils/ifind_client.py
│   ├── tdx_source.py               #   迁移自 utils/tdx_data_source.py
│   └── external_data_source.py     #   迁移自 utils/external_data_source.py
│
├── alpha/                          # L3 Alpha 层（研究域）
│   ├── __init__.py
│   ├── factor_library.py           #   迁移自 utils/alpha_factor_library.py
│   ├── factor_evaluator.py         #   迁移自 utils/alpha_evaluator.py
│   ├── gtja191_factors.py          #   迁移自 utils/gtja191_factors.py
│   ├── signal_fusion.py            #   迁移自 utils/signal_fusion.py
│   ├── multi_factor_signal.py      #   新建：多因子融合 + 权重优化
│   ├── fast_backtest.py            #   新建：ML 回测验证引擎（基于 research/fast_backtest_aggregator.py）
│   ├── signal_monitor.py           #   迁移自 utils/lgb_signal_monitor.py（与根目录 signal_monitor.py 去重）
│   ├── llm_router.py               #   新建：多模型 LLM 路由器（基于 15_每日工作流/llm_client.py 扩展）
│   ├── decision_theories.py        #   迁移自 v8.3_institutional/src/factors/decision_theories.py
│   ├── ml_enhanced_selector.py     #   新建：真实 Transformer（替换 Mock）
│   ├── finance_agents/             #   迁移自 utils/finance_agents/
│   └── qlib_bridge.py              #   迁移自 utils/qlib_data_bridge.py
│
├── execution/                      # L4 执行层
│   ├── __init__.py
│   ├── execution_router.py         #   迁移自 utils/execution_router.py
│   ├── smart_order_router.py       #   迁移自 utils/smart_order_router.py
│   ├── execution_algo.py           #   迁移自 utils/execution_algo_engine.py（与 execution_algorithm_engine.py 去重）
│   ├── hedge_engine.py             #   迁移自 utils/hedge_execution_engine.py
│   ├── qmt_broker.py               #   迁移自 utils/qmt_broker.py
│   ├── tca_engine.py               #   迁移自 utils/tca_engine.py（接入闭环）
│   ├── cost_model.py               #   迁移自 utils/cost_model.py
│   ├── market_impact.py            #   迁移自 utils/market_impact_model.py
│   ├── automated_execution.py      #   迁移自根目录 automated_execution_system.py
│   ├── daily_hedge.py              #   迁移自根目录 daily_build_and_hedge.py
│   └── rebalance.py                #   迁移自根目录 rebalance_execution_orders.py
│
├── risk/                           # L5 风控层（中央风控总线）
│   ├── __init__.py
│   ├── risk_bus.py                 #   新建：事件驱动风控总线（核心）
│   ├── risk_event.py               #   新建：风控事件类型定义
│   ├── kill_switch.py              #   迁移自 utils/kill_switch.py（同步路径，不走总线）
│   ├── circuit_breaker.py          #   迁移自 utils/market_circuit_breaker.py
│   ├── risk_guard.py               #   迁移自 utils/risk_guard_integrator.py
│   ├── risk_attribution.py         #   迁移自 utils/risk_attribution.py
│   ├── barra_decomposer.py         #   迁移自 utils/barra_risk_decomposer.py
│   ├── risk_budget.py              #   迁移自 utils/risk_budget_engine.py（含 dynamic_risk_budget）
│   ├── var_monitor.py              #   迁移自 utils/var_monitor.py
│   ├── overnight_gap.py            #   迁移自 utils/overnight_gap_monitor.py
│   ├── stop_loss.py                #   迁移自 utils/stop_loss.py（与根目录 stop_loss_monitor.py 去重）
│   ├── comprehensive_risk.py       #   新建：五维风险评估（基于 risk_attribution 扩展）
│   ├── liquidity_risk.py           #   新建：流动性风险与滑点（基于 market_impact_model 扩展）
│   └── stress_test.py              #   迁移自 utils/stress_test_runner.py
│
├── scheduling/                     # L6 调度层
│   ├── __init__.py
│   ├── daily_runner.py             #   新建：自动化每日工作流（基于 run_daily_eod.py + run_daily_morning.py 整合）
│   ├── daily_workflow.py           #   新建：三阶段工作流（盘前/盘中/盘后）
│   ├── trade_calendar.py           #   迁移自 utils/trade_calendar.py
│   └── task_scheduler.py           #   新建：Windows 任务计划程序抽象
│
├── attribution/                    # L7 归因层（新增，对冲基金必备）
│   ├── __init__.py
│   ├── pnl_attribution.py          #   迁移自 utils/pnl_attribution_engine.py
│   ├── brinson_attribution.py      #   新建：Brinson 归因
│   ├── factor_attribution.py       #   新建：因子归因
│   └── daily_panel.py              #   新建：日级归因面板（三合一）
│
└── _legacy/                        # 兼容层（re-export）
    └── __init__.py                 #   保留旧路径导出，例如 from utils import kill_switch 仍可用
```

### 2.2 Re-export 兼容策略（保护 V9 基线）

每个迁移模块在新位置保留实现，在 `utils/` 顶层通过 `__init__.py` re-export：

```python
# utils/__init__.py（部分示例）
# === 兼容层 re-export — 严禁删除，否则破坏 V9 基线 ===
from utils.infra.config_manager import ConfigManager  # noqa: F401
from utils.infra.logger import get_logger              # noqa: F401
from utils.risk.kill_switch import KillSwitch          # noqa: F401
from utils.alpha.factor_library import AlphaFactorLibrary  # noqa: F401
# ... 完整 re-export 列表见 TASK_模块整合.md T1.4
```

**Re-export 验证脚本**（`scripts/_verify_reexport_compat.py`）：
- 加载所有 V9 训练/推理脚本依赖的 `from utils import X` 语句
- 对每个 X 验证新位置 re-export 是否可用
- 失败立即阻断 PR 合并

---

## 3. 中央风控总线设计（ALIGNMENT D2 推荐候选 A）

### 3.1 总线架构

```
┌──────────────────────────────────────────────────────────────────┐
│                   Risk Event Bus（事件驱动）                       │
│                   基于 asyncio.Queue + pub/sub                    │
└──────┬───────────┬───────────┬───────────┬───────────┬───────────┘
       │           │           │           │           │
       ▼           ▼           ▼           ▼           ▼
   ┌───────┐ ┌───────────┐ ┌───────┐ ┌─────────┐ ┌─────────┐
   │ Kill  │ │ Circuit   │ │ Risk  │ │   VaR   │ │Overnight│
   │Switch │ │ Breaker   │ │Guard  │ │ Monitor │ │  Gap    │
   │(同步) │ │(异步订阅) │ │(聚合) │ │(异步)   │ │(异步)   │
   └───┬───┘ └─────┬─────┘ └───┬───┘ └────┬────┘ └────┬────┘
       │           │           │           │           │
       └───────────┴───────┬───┴───────────┴───────────┘
                           ▼
                   ┌───────────────┐
                   │ RiskDecision  │
                   │   Aggregator  │
                   │ (最终否决权)   │
                   └───────┬───────┘
                           │
                           ▼
                   ┌───────────────┐
                   │  Order Gate   │
                   │(放行/否决/缩仓)│
                   └───────────────┘
```

### 3.2 事件类型

```python
# utils/risk/risk_event.py
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

class RiskEventType(Enum):
    PRE_TRADE = "pre_trade"              # 订单提交前
    POST_TRADE = "post_trade"            # 订单成交后
    INTRADAY_POSITION = "intraday_pos"   # 盘中持仓更新
    MARKET_REGIME = "market_regime"      # 市场状态切换
    DRAWDOWN_BREACH = "drawdown"         # 回撤突破
    OVERNIGHT_GAP = "overnight_gap"      # 隔夜跳空
    EOD_SETTLE = "eod_settle"            # EOD 结算

@dataclass
class RiskEvent:
    event_type: RiskEventType
    timestamp: datetime
    payload: Dict[str, Any]
    source: str                          # 来源模块
    severity: str = "info"               # info/warning/critical
    correlation_id: Optional[str] = None # 跨模块追踪
```

### 3.3 关键路径同步保护

**Kill Switch 不走总线**（HC-2 + R2 缓解）：
- Kill Switch 保留同步直调路径，延迟 <1ms
- 总线故障时 Kill Switch 仍可独立触发
- 总线仅订阅 Kill Switch 事件做日志归档，不参与决策

**风控决策优先级**：
1. **绝对否决**（任何模块可独立否决）：Kill Switch / Circuit Breaker
2. **聚合否决**（多数表决）：RiskGuard / VaR / Overnight Gap
3. **告警不否决**：Risk Attribution / Barra Decomposer

---

## 4. TCA 闭环设计（G5 验收标准）

### 4.1 当前痛点

`tca_engine.py` 已存在但未形成闭环：
- ❌ 执行前：订单路由未调用 TCA 预估
- ❌ 执行后：未将实际成交与预估对比，未反馈到模型
- ❌ 归因：PnL 归因未拆分"Alpha PnL"与"执行 PnL"

### 4.2 闭环设计

```
信号生成 ──> 订单路由 ──┬──> TCA 预估（pre-trade cost）
                        │         │
                        │         ▼
                        │    预估成本 > 阈值? ──是──> 否决订单
                        │         │ 否
                        │         ▼
                        └──> 实际执行
                                  │
                                  ▼
                            TCA 实际（post-trade cost）
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │  PnL 归因拆分          │
                       │  ├─ Alpha PnL         │
                       │  ├─ Execution PnL     │
                       │  │   ├─ Spread Cost   │
                       │  │   ├─ Market Impact │
                       │  │   └─ Timing Cost   │
                       │  └─ Risk PnL          │
                       └──────────────────────┘
                                  │
                                  ▼
                       反馈到 tca_engine 模型校准
```

### 4.3 接入点

- **执行前**：`utils/execution/execution_router.py` 在 `route_order()` 中调用 `tca_engine.estimate()`
- **执行后**：`utils/execution/hedge_engine.py` 在 `on_fill()` 回调中调用 `tca_engine.record()`
- **归因**：`utils/attribution/pnl_attribution.py` 拆分 Alpha/Execution/Risk PnL
- **反馈**：`tca_engine.calibrate()` 每日 EOD 触发，基于当日实际 vs 预估偏差校准

---

## 5. 性能归因面板设计（G6 验收标准）

### 5.1 三合一面板

```python
# utils/attribution/daily_panel.py
class DailyAttributionPanel:
    """日级归因面板 — 三合一（Brinson + Barra + Factor）"""
    
    def generate(self, date: datetime) -> dict:
        return {
            "date": date,
            "brinson": self._brinson_attribution(date),    # 配置/选股/交互效应
            "barra": self._barra_attribution(date),        # 风格因子归因
            "factor": self._factor_attribution(date),      # Alpha 因子贡献
            "summary": {
                "total_pnl": ...,
                "alpha_pnl": ...,       # 来自因子
                "execution_pnl": ...,   # 来自 TCA
                "risk_pnl": ...,        # 来自风险敞口
                "residual": ...,        # 不可解释部分
            }
        }
```

### 5.2 输出格式

每日 `reports/attribution/{date}.json` + `reports/attribution/{date}.md`：
- JSON：机器可读，供 Streamlit 面板消费
- Markdown：人工可读，含表格与简短分析

---

## 6. 集成模式（Integration Patterns）

### 6.1 模式目录

| 模式 | 适用场景 | 实现示例 |
|------|----------|----------|
| **Adapter** | 外部模块接入现有接口 | VibeTradingFactorAdapter（已验证） |
| **Facade** | 多模块统一入口 | RiskBus / DailyAttributionPanel |
| **Strategy** | 多算法可切换 | LLM Router / Broker Adapter |
| **Observer** | 事件订阅 | Risk Event Bus |
| **Re-export** | 向后兼容 | utils/\_\_init\_\_.py |
| **Feature Flag** | 可逆变更 | feature_flags.yaml |

### 6.2 模块去重决策树

```
新模块 vs 已有模块
   │
   ├── 同职能 + 同实现质量 ──> 保留已有，新模块归档到 research/legacy/
   │
   ├── 同职能 + 新模块更优 ──> Adapter 包装新模块，Shadow 验证 14 天后切换
   │
   ├── 同职能 + 各有优劣   ──> Strategy 模式，按 regime/flag 切换
   │
   └── 新模块填补空白       ──> 直接整合，走 Shadow 验证
```

---

## 7. 整合路线图（5 Phase）

### 7.1 Phase 1 — 基础设施层（2 周）

**目标**：建立 L1 基础设施分层 + Feature Flag 框架 + 分支策略

**关键交付物**：
- `utils/infra/` 目录与 re-export
- `utils/infra/bootstrap.py`（新建）
- `utils/infra/core.py`（新建，策略注册表）
- `utils/infra/feature_flags.py`（新建）
- `v8.3_institutional/config/feature_flags.yaml`
- `integration/phase1-infra` 分支
- V9 基线回归脚本

**验收**：G2 + G3 + G8

### 7.2 Phase 2 — AI 决策层（3 周）

**目标**：LLM 路由器 + 决策理论融合

**关键交付物**：
- `utils/alpha/llm_router.py`（基于 llm_client.py 扩展，豆包/GLM-5/SiliconFlow/Ollama fallback）
- `utils/alpha/decision_theories.py`（从 v8.3_institutional 迁移）
- `utils/alpha/multi_factor_signal.py`（新建）
- Shadow 准入 14 天启动

**验收**：G2（Shadow 准入规范）+ L3 单测 60%

### 7.3 Phase 3 — 执行与风控层（4 周）

**目标**：风控总线原型 + TCA 闭环

**关键交付物**：
- `utils/risk/risk_bus.py` + `risk_event.py`（事件驱动总线）
- 5 个风控模块适配器
- TCA 闭环接入（执行前预估 + 执行后归因）
- `utils/execution/` 分层 + 根目录模块迁移

**验收**：G4 + G5 + L4/L5 单测 70%

### 7.4 Phase 4 — 信号与宏观层（6 周）

**目标**：多因子融合 + 回测验证 + 宏观分析

**关键交付物**：
- `utils/alpha/fast_backtest.py`（基于 research/fast_backtest_aggregator.py）
- `utils/alpha/ml_enhanced_selector.py`（真实 Transformer 替换 Mock）
- 宏观指标与行业轮动模块整合

**验收**：L3 单测 60% + Shadow 14 天 DSR>=5

### 7.5 Phase 5 — 优化与完善（12 周+）

**目标**：性能归因面板 + MLops + UI

**关键交付物**：
- `utils/attribution/daily_panel.py`（Brinson + Barra + Factor 三合一）
- MLops 流水线（模型训练/部署/监控）
- Streamlit UI 14 页面完善
- 实盘券商直连补充

**验收**：G6 + G7 + UI 验收

---

## 8. 与现有系统的契约（Contract）

### 8.1 不可破坏的契约

以下接口签名不可修改（V9 基线依赖）：

```python
# ConfigManager（HC-5）
class ConfigManager:
    def get_config(name: str) -> dict: ...
    def get_portfolio_config() -> dict: ...
    def get_settings_config() -> dict: ...
    def get_kill_switch_config() -> dict: ...
    def get_execution_config() -> dict: ...
    def get_backtest_config() -> dict: ...
    def get_risk_budget_config() -> dict: ...
    def list_available() -> list[str]: ...
    def get_config_source(name: str) -> str: ...

# KillSwitch（HC-2）
class KillSwitch:
    def check_kill_switch(...) -> bool: ...  # 同步路径，<1ms

# PipelineOrchestrator（HC-7）
class PipelineOrchestrator:
    def run(...) -> PipelineResult: ...  # 含 ic_weighted_combinations
    @property
    def ic_weighted_enabled: bool: ...   # 默认 True

# ShadowAccount（HC-3 + HC-4）
class ShadowAccount:
    def __init__(..., risk_managed: bool = True, ...): ...
    # Stage 1 不可推进 Stage 2，直到 14 天满足
```

### 8.2 新增契约

```python
# RiskBus（新）
class RiskBus:
    async def publish(event: RiskEvent) -> None: ...
    async def subscribe(event_type: RiskEventType, handler: Callable) -> None: ...
    def decide(order: Order) -> RiskDecision: ...  # 同步聚合决策

# FeatureFlags（新）
class FeatureFlags:
    def is_enabled(name: str) -> bool: ...
    def enable(name: str, signer: str, co_signer: str) -> None: ...
    def disable(name: str, signer: str) -> None: ...
    def audit_trail(name: str) -> list[dict]: ...

# TCAEngine（扩展）
class TCAEngine:
    def estimate(order: Order) -> CostEstimate: ...   # 新增：执行前
    def record(fill: Fill) -> None: ...                # 新增：执行后
    def calibrate(date: datetime) -> None: ...         # 新增：EOD 校准
```

---

## 9. 性能与延迟预算

| 路径 | 预算 | 当前 | 整合后目标 |
|------|------|------|------------|
| Kill Switch 触发 | <1ms | <1ms | <1ms（同步保留） |
| 风控总线决策 | <10ms | N/A | <10ms（异步聚合） |
| TCA 预估 | <50ms | N/A | <50ms（缓存模型） |
| LLM 路由调用 | <5s | <30s | <5s（fallback + 超时） |
| V9 信号生成 | <60s | <60s | <60s（不变） |
| 日级归因面板 | <30s | N/A | <30s |

---

## 10. 决策记录（ADR — Architecture Decision Records）

### ADR-001：选择事件驱动风控总线而非同步调用链

- **背景**：风控模块当前分散，需总线化
- **决策**：事件驱动 pub/sub（ALIGNMENT D2 候选 A）
- **理由**：Two Sigma / DE Shaw 实践验证，故障隔离好，可横向扩展
- **权衡**：异步引入复杂度，Kill Switch 必须保留同步路径
- **状态**：Accepted

### ADR-002：选择 utils/ 子目录分层而非新建 src/

- **背景**：模块物理布局（ALIGNMENT D1）
- **决策**：候选 A — utils/ 内建子目录 + re-export
- **理由**：保护 V9 基线，re-export 让旧导入路径仍可用；新建 src/ 会强制全项目迁移
- **权衡**：utils/ 顶层会有一些 re-export 噪声
- **状态**：Accepted

### ADR-003：Feature Flag 默认值铁律 — 必须等于当前生产行为

- **背景**：Flag 错误可能关闭生产模块
- **决策**：所有新 flag 默认 False（即不改变现状）
- **理由**：R5 风险缓解，对冲基金风控要求"默认安全"
- **权衡**：启用新模块需要双签，进度稍慢
- **状态**：Accepted

### ADR-004：Shadow 准入分层而非一刀切

- **背景**：基础设施类模块 Shadow 验证无意义
- **决策**：候选 C — 按风险等级分层（L1/L2/L6 豁免，L3/L4/L5 必须）
- **理由**：Renaissance 基础设施变更走 chaos engineering，不走 Shadow PnL
- **权衡**：L1/L2 依赖单测+e2e 充分性
- **状态**：Accepted

---

## 11. 下一步

进入阶段 3 Atomize，将本架构拆解为原子化任务，每个任务明确：
- 输入（依赖模块/数据/前置任务）
- 输出（具体交付物）
- 验收标准（可量化）
- 责任层（L1-L7）
- 优先级（P0/P1/P2）
- 预计所在 Phase（1-5）
