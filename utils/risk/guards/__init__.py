"""``utils.risk.guards`` — 风控守卫 mixin 域包 (审计 item 11)。

拆自 ``utils/risk_guard_integrator.py`` (2026-09-10), 口径为**零行为变更 + 全量改引用**:
编排骨架仍留在 ``utils/risk_guard_integrator.py``, 各 Guard 按域拆为本包下的 mixin。

**为何本 __init__ 不 import 子模块**:
    子模块之间是"兄弟互相引用"(如 ``kill_switch_level`` → ``plan_context``)。
    若在这里先行 import 子模块, 会在包初始化未完成时触发兄弟模块的
    ``from utils.risk.guards import plan_context``, 造成部分初始化 (partial init) 错误。
    因此本包要求调用方**显式导入具体子模块**::

        from utils.risk.guards import plan_context
        from utils.risk.guards.kill_switch_level import KillSwitchLevel, parse_kill_switch_level

**重量级依赖约束**: 本包内禁止在模块级导入 DrawdownController /
HedgeExecutionEngine / ProtectivePutEngine / MarketCircuitBreaker /
OvernightGapMonitor / IFinDNewsAnalyzer 等引擎 —— 必须保持原样在方法体内惰性
import, 以免把启动成本与可选依赖失败面前置到 import 期。
"""

from __future__ import annotations

__all__: list[str] = []
