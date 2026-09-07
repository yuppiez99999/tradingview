"""LLM 多模型路由器 — 终极量化交易系统 8.4 (T2.1).

⚠️ B3.4 重构 (2026-07-30): 本文件已拆分为 `utils/alpha/llm/` 子包,
    实际实现迁移至:
        - utils/alpha/llm/base.py             异常 / CallRecord / _safe_urlopen
        - utils/alpha/llm/openai_compat.py    OpenAI 兼容通用调用
        - utils/alpha/llm/audit.py            审计日志
        - utils/alpha/llm/passthrough.py      Feature Flag 透传
        - utils/alpha/llm/router.py           LLMRouter 主类 (薄外壳)
        - utils/alpha/llm/providers/*.py      6 个 provider 实现

本文件保留为向后兼容入口 — 所有现有 import 路径不变:

    from utils.alpha.llm_router import LLMRouter, chat, chat_deep
    from utils.alpha.llm_router import (
        LLMRouterError, AllProvidersFailedError, ProviderNotConfiguredError,
        CallRecord, ProviderFn,
    )
    from utils.alpha.llm_router import test_connection, list_providers, reload

设计原则 (不变):
    1. 5 个 Provider fallback 链 (DeepSeek 优先): deepseek → glm → siliconflow → ds4 → ollama
    2. 5 秒超时 (云 API) + 静默降级 (HC-2 主路径不阻塞)
    3. 审计日志: 每次调用记录到 reports/llm_router/calls_{date}.jsonl
    4. Feature Flag 透传: USE_LLM_REPORT_ANALYZER=False 时透传到旧 llm_client (HC-1)
    5. ConfigManager 4 级优先级解析 (HC-5)

硬约束 (不变):
    - HC-1: flag=False 时必须透传到旧路径, 行为完全等价
    - HC-2: 主路径调用延迟 <5s (云 API 超时)
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

# 从新子包 re-export 所有公共符号 (保持向后兼容)
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
