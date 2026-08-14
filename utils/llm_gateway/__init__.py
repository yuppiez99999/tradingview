"""LLM Gateway — LiteLLM-style 多模型统一路由层.

在现有 utils/alpha/llm/router.py LLMRouter 之上提供 OpenAI-compatible 统一接口:
    - ChatRequest / ChatResponse 标准化
    - 场景路由 (intraday/rebalance/report/hedge)
    - 成本统计 + Provider 元信息
    - 复用 LLMRouter 5-provider fallback 链 (不重复实现 provider 调用)

子模块:
    - types: 统一类型定义 (ChatRequest/ChatResponse/Usage/ProviderInfo)
    - litellm_router: LiteLLMRouter 主类

用法:
    from utils.llm_gateway import LiteLLMRouter, ChatRequest

    router = LiteLLMRouter.get_instance()
    resp = router.chat(ChatRequest(prompt="你好", scene="intraday"))
    print(resp.content, resp.provider.name)
"""
from __future__ import annotations

from utils.llm_gateway.litellm_router import LiteLLMRouter
from utils.llm_gateway.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderInfo,
    Usage,
)

__all__ = [
    "LiteLLMRouter",
    "ChatRequest",
    "ChatResponse",
    "ChatMessage",
    "ProviderInfo",
    "Usage",
]
