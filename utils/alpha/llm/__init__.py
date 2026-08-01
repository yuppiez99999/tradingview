# -*- coding: utf-8 -*-
"""LLM 路由器子包 — 终极量化交易系统 8.4 (B3.4 拆分).

将原 `utils/alpha/llm_router.py` (1110 行 God Class) 拆分为:

    - base.py             异常 / CallRecord / _safe_urlopen / 项目根定位
    - openai_compat.py    OpenAI 兼容 chat/completions 通用调用 (含重试)
    - audit.py            审计日志 (JSONL 按日切分)
    - passthrough.py      Feature Flag 透传到旧 llm_client
    - router.py           LLMRouter 主类 (薄外壳 + fallback 链 + 审计调度)
    - providers/          6 个 provider 实现 (omniroute/deepseek/doubao/glm/siliconflow/ollama)

向后兼容:
    `from utils.alpha.llm_router import LLMRouter, chat, chat_deep, ...`
    仍可使用 — `llm_router.py` 已改为 re-export 入口。
"""

from utils.alpha.llm.base import (
    AllProvidersFailedError,
    CallRecord,
    LLMRouterError,
    ProviderFn,
    ProviderNotConfiguredError,
)
from utils.alpha.llm.router import (
    LLMRouter,
    chat,
    chat_deep,
    list_providers,
    reload,
    test_connection,
)

__all__ = [
    "LLMRouter",
    "LLMRouterError",
    "AllProvidersFailedError",
    "ProviderNotConfiguredError",
    "CallRecord",
    "ProviderFn",
    "chat",
    "chat_deep",
    "test_connection",
    "list_providers",
    "reload",
]
