"""
路由插件 — 把 ai_coordinator.py route() 硬编码 if-else 拆成可插拔插件

原 route() 逻辑 (ai_coordinator.py, 2026-09-07 对齐 MLX 本地):
    1. 预算 >80% 且非 CRITICAL → mlx_qwen3_8b  (BudgetGuard)
    2. INTRADAY_DECISION       → mlx_qwen3_8b
    3. DEEP_RESEARCH           → deepseek (if budget<50%) else mlx_qwen3_8b
    4. MACRO_ANALYSIS          → glm5     (if budget<60%) else mlx_qwen3_8b
    5. 默认                    → mlx_qwen3_8b

(doubao 已于 2026-09-07 出局, 降级/兜底模型统一为本地 MLX mlx_qwen3_8b 零成本)

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
    """预算守卫 — 预算 >80% 且非 CRITICAL 时强制切本地 MLX (零 API 成本)

    对应原 route() 预算守卫分支
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
            model="mlx_qwen3_8b",
            reason=f"预算守卫: budget_ratio={context.budget_ratio:.0%} > 80%",
        )


# ── 盘中决策插件 ──


class IntradayRoutingPlugin(RoutingPlugin):
    """盘中决策路由 — 对应原 route() 盘中分支

    TaskType.INTRADAY_DECISION → mlx_qwen3_8b (本地零成本低延迟)
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
            model="mlx_qwen3_8b", reason="盘中决策: MLX 本地零成本低延迟"
        )


# ── 深度研究插件 ──


class DeepResearchRoutingPlugin(RoutingPlugin):
    """深度研究路由 — 对应原 route() 深度研究分支

    TaskType.DEEP_RESEARCH → deepseek (if budget<50%) else mlx_qwen3_8b
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
            model="mlx_qwen3_8b", reason="深度研究: 预算紧张降级本地 mlx_qwen3_8b"
        )


# ── 宏观分析插件 ──


class MacroAnalysisRoutingPlugin(RoutingPlugin):
    """宏观分析路由 — 对应原 route() 宏观分析分支

    TaskType.MACRO_ANALYSIS → glm5 (if budget<60%) else mlx_qwen3_8b
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
            model="mlx_qwen3_8b", reason="宏观分析: 预算紧张降级本地 mlx_qwen3_8b"
        )


# ── 默认兜底插件 (最低优先级) ──


class DefaultRoutingPlugin(RoutingPlugin):
    """默认路由 — 对应原 route() 默认分支

    日报/情绪分析等其它任务 → mlx_qwen3_8b (本地便宜模型)
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
            model="mlx_qwen3_8b", reason="默认: 日报/情绪分析用本地便宜模型"
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
