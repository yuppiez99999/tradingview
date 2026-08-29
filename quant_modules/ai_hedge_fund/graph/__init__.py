"""LangGraph 状态管理 + 编排能力 (v8.6+ 融合 TradingAgents-src)

导出:
    AgentState — 工作流状态
    Reflector — 决策反思 (事后调用, 需 LLM + 收益数据)
    get_checkpointer / has_checkpoint / clear_checkpoint — SQLite checkpoint (崩溃恢复)
"""

from quant_modules.ai_hedge_fund.graph.state import AgentState, show_agent_reasoning

__all__ = [
    "AgentState",
    "show_agent_reasoning",
    "Reflector",
    "get_checkpointer",
    "has_checkpoint",
    "checkpoint_step",
    "clear_checkpoint",
    "clear_all_checkpoints",
    "thread_id",
]


def __getattr__(name: str):
    """懒加载 checkpointer / reflection (依赖 langgraph-checkpoint-sqlite)."""
    if name in (
        "get_checkpointer",
        "has_checkpoint",
        "checkpoint_step",
        "clear_checkpoint",
        "clear_all_checkpoints",
        "thread_id",
    ):
        from quant_modules.ai_hedge_fund.graph import checkpointer as _cp

        return getattr(_cp, name)
    if name == "Reflector":
        from quant_modules.ai_hedge_fund.graph.reflection import Reflector as _R

        return _R
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
