"""
ai_decision — 多 AI 模型综合自动交易决策系统
=============================================

在现有量化系统 (llm_client / ModelRouter / AICoordinator / FinanceAgentOrchestrator)
之上新增的解耦决策包, 实现研究报告中设计的"多 AI 辩论 + 共识"架构.

核心特性:
  - 统一 Provider 抽象, 无 API Key 时优雅降级到 MockProvider, 全链路可跑
  - Bull/Bear/Judge 结构化辩论 (条件触发, <=2 轮)
  - 非线性聚合 (Brier 动态权重 + 语义去重 + 多样性奖励), 融合五 Agent 加权投票
  - 硬风控独立运行 + 人工审批升级 + shadow/paper/auto 三态模式开关
  - 默认 Shadow 模式, 不触发真实下单; 经验证后切 auto

典型入口:
  from ai_decision.orchestrator import run_decision
  from ai_decision.cli import main
"""

from __future__ import annotations

__version__ = "1.0.0"

from ai_decision.models import (
    DebateDecision,
    DebateRecord,
    DebateTrigger,
    DecisionContext,
    ModelView,
    TradingDecision,
)
from ai_decision.providers import (
    BaseProvider,
    ClaudeProvider,
    GptProvider,
    LlmClientProvider,
    MockProvider,
    MoonshotProvider,
    get_active_provider,
)

__all__ = [
    "BaseProvider",
    "ClaudeProvider",
    "DebateDecision",
    "DebateRecord",
    "DebateTrigger",
    "DecisionContext",
    "GptProvider",
    "LlmClientProvider",
    "MockProvider",
    "ModelView",
    "MoonshotProvider",
    "TradingDecision",
    "__version__",
    "get_active_provider",
]
