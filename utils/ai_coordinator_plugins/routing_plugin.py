"""
路由插件 — 把 ai_coordinator.py route() 硬编码 if-else 拆成可插拔插件

原 route() 逻辑 (ai_coordinator.py:187-213):
    1. 预算 >80% 且非 CRITICAL → doubao_speed  (BudgetGuard)
    2. INTRADAY_DECISION       → doubao_speed
    3. DEEP_RESEARCH           → deepseek (if budget<50%) else doubao_speed
    4. MACRO_ANALYSIS          → glm5     (if budget<60%) else doubao_speed
    5. 默认                    → doubao_speed

拆成 5 个插件, 按 priority 降序排列, PluginRegistry 遍历第一个 can_handle=True 的执行.

为避免循环导入, 本模块用字符串值比较 TaskType / Priority (兼容 Enum 和 str).
"""

from __future__ import annotations

from typing import Any

from .base import RoutingContext, RoutingPlugin, RoutingResult


def _enum_value(x: Any) -> str:
    """取 Enum.value 或 str(x), 兼容 Enum 和字符串"""
    v = getattr(x, "value", None)
    if v is not None:
        return str(v)
    return str(x)


# ── 预算守卫插件 (最高优先级, 预算快用完时强制切便宜模型) ──


class BudgetGuardRoutingPlugin(RoutingPlugin):
    """预算守卫 — 预算 >80% 且非 CRITICAL 时强制切 doubao_speed

    对应原 route() 第 197-200 行
    """

    @property
    def name(self) -> str:
        return "budget_guard"

    @property
    def priority(self) -> int:
        return 100

    def can_handle(self, context: RoutingContext) -> bool:
        if context.daily_token_budget <= 0:
            return False
        budget_ratio = context.budget_ratio
        is_critical = (
            _enum_value(context.priority) == "critical"
            or _enum_value(context.priority) == "4"
        )
        return budget_ratio > 0.8 and not is_critical

    def handle(self, context: RoutingContext) -> RoutingResult:
        return RoutingResult(
            model="doubao_speed",
            reason=f"预算守卫: budget_ratio={context.budget_ratio:.0%} > 80%",
        )


# ── 盘中决策插件 ──


class IntradayRoutingPlugin(RoutingPlugin):
    """盘中决策路由 — 对应原 route() 第 203-204 行

    TaskType.INTRADAY_DECISION → doubao_speed (速度快成本低)
    """

    @property
    def name(self) -> str:
        return "intraday"

    @property
    def priority(self) -> int:
        return 90

    def can_handle(self, context: RoutingContext) -> bool:
        return _enum_value(context.task_type) == "intraday_decision"

    def handle(self, context: RoutingContext) -> RoutingResult:
        return RoutingResult(
            model="doubao_speed", reason="盘中决策: 豆包 Speed 速度快成本低"
        )


# ── 深度研究插件 ──


class DeepResearchRoutingPlugin(RoutingPlugin):
    """深度研究路由 — 对应原 route() 第 206-207 行

    TaskType.DEEP_RESEARCH → deepseek (if budget<50%) else doubao_speed
    """

    @property
    def name(self) -> str:
        return "deep_research"

    @property
    def priority(self) -> int:
        return 80

    def can_handle(self, context: RoutingContext) -> bool:
        return _enum_value(context.task_type) == "deep_research"

    def handle(self, context: RoutingContext) -> RoutingResult:
        if context.budget_ratio < 0.5:
            return RoutingResult(
                model="deepseek", reason="深度研究: 预算充足用 deepseek"
            )
        return RoutingResult(
            model="doubao_speed", reason="深度研究: 预算紧张降级 doubao_speed"
        )


# ── 宏观分析插件 ──


class MacroAnalysisRoutingPlugin(RoutingPlugin):
    """宏观分析路由 — 对应原 route() 第 209-210 行

    TaskType.MACRO_ANALYSIS → glm5 (if budget<60%) else doubao_speed
    """

    @property
    def name(self) -> str:
        return "macro_analysis"

    @property
    def priority(self) -> int:
        return 70

    def can_handle(self, context: RoutingContext) -> bool:
        return _enum_value(context.task_type) == "macro_analysis"

    def handle(self, context: RoutingContext) -> RoutingResult:
        if context.budget_ratio < 0.6:
            return RoutingResult(model="glm5", reason="宏观分析: 预算充足用 glm5")
        return RoutingResult(
            model="doubao_speed", reason="宏观分析: 预算紧张降级 doubao_speed"
        )


# ── 默认兜底插件 (最低优先级) ──


class DefaultRoutingPlugin(RoutingPlugin):
    """默认路由 — 对应原 route() 第 212-213 行

    日报/情绪分析等其它任务 → doubao_speed
    """

    @property
    def name(self) -> str:
        return "default"

    @property
    def priority(self) -> int:
        return 10

    def can_handle(self, context: RoutingContext) -> bool:
        return True

    def handle(self, context: RoutingContext) -> RoutingResult:
        return RoutingResult(
            model="doubao_speed", reason="默认: 日报/情绪分析用便宜模型"
        )


# ── 工厂函数: 一次性创建全部默认路由插件 ──


def create_default_routing_plugins() -> list[RoutingPlugin]:
    """创建全部默认路由插件 (按 priority 降序)

    Returns:
        [BudgetGuard, Intraday, DeepResearch, MacroAnalysis, Default]
    """
    return [
        BudgetGuardRoutingPlugin(),
        IntradayRoutingPlugin(),
        DeepResearchRoutingPlugin(),
        MacroAnalysisRoutingPlugin(),
        DefaultRoutingPlugin(),
    ]
