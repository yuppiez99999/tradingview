"""
AI 记忆包 — 团队级共享记忆中枢

把各 AI 分析师的决策教训/反思聚合为跨会话共享记忆,
让分析师们"记得"上次为什么止损、某标的的历史判断准确率等。

可用工具:
- team_memory_hub: 团队级共享记忆中枢 (TencentDB-Agent-Memory 接入)
"""

from __future__ import annotations

try:
    from .team_memory_hub import AgentProfile, Lesson, TeamMemoryHub
except ImportError:
    pass

__all__ = ["TeamMemoryHub", "Lesson", "AgentProfile"]
