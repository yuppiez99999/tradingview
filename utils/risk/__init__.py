"""L5 风控层 — 中央风控总线 + 多模块适配器.

模块整合 8.4 — ARCHITECTURE §2.1 / §3
当前阶段：T3.3 已完成, 准备进入 T3.4 (TCA 预估)
已完成：
  - T3.1: risk_event.py (7 种事件类型 + RiskEvent/RiskDecision dataclass)
          risk_bus.py (pub/sub 总线 + sync_decide 决策聚合 + 审计日志)
          57 单测 PASS, 覆盖率 85.67%
  - T3.2: kill_switch_adapter.py (KillSwitch 适配器, HC-2 同步路径保护)
          22 单测 PASS, 覆盖率 90.35%
  - T3.3: risk_module_adapters.py (4 个风控模块适配器集合)
          39 单测 PASS, 覆盖率 91.62%
后续任务：
  - T3.4: TCA 接入执行前预估 (订单路由侧)
  - T3.5: TCA 接入执行后归因 (PnL 拆分)
  - T3.6: 日级归因面板
  - 后续迁移: risk_attribution / barra_decomposer / risk_budget / stress_test

硬约束（HC-2）：KillSwitch 必须保留同步直调路径，延迟 <1ms，不走总线
"""

from __future__ import annotations

# Re-export API (按需显式导入, 避免循环依赖)
# T3.1:
#   from utils.risk.risk_event import (
#       RiskEventType, RiskSeverity, RiskAction,
#       RiskEvent, RiskDecision,
#       make_margin_breach_event, make_drawdown_breach_event, make_kill_switch_triggered_event,
#   )
#   from utils.risk.risk_bus import (
#       RiskBus, RiskDecisionAggregator, SubscriptionError,
#       get_bus, publish, subscribe, sync_decide,
#   )
# T3.2:
#   from utils.risk.kill_switch_adapter import KillSwitchAdapter, adapt_kill_switch
# T3.3:
#   from utils.risk.risk_module_adapters import (
#       RiskModuleAdapter, CircuitBreakerAdapter, VaRMonitorAdapter,
#       OvernightGapAdapter, RiskGuardAdapter, RiskModuleRegistry,
#   )
