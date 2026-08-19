"""
AI 工具包 — 供 ai_coordinator / AutoResearch Skill 程序化调用的 AI 辅助工具

可用工具:
- code_graph_rag: 代码库知识图谱 RAG (基于 .code-review-graph/graph.db)
"""
from __future__ import annotations

try:
    from .code_graph_rag import CodeGraphRAG
except ImportError:
    pass

__all__ = ["CodeGraphRAG"]
