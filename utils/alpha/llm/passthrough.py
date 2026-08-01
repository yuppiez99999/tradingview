"""Feature Flag 透传到旧 llm_client (HC-1).

从原 `utils/alpha/llm_router.py:LLMRouter._passthrough_to_legacy` 拆出 (B3.4.3)。

HC-1 硬约束:
    USE_LLM_REPORT_ANALYZER=False 时, 必须透传到旧 llm_client.chat(),
    行为完全等价于未引入 LLMRouter 之前。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Optional, cast

logger = logging.getLogger("llm_router")


def passthrough_to_legacy(
    prompt: str,
    system: str,
    temperature: float | None,
    max_tokens: int | None,
    passthrough_module: str = "llm_client",
    passthrough_function: str = "chat",
) -> str | None:
    """透传到旧 llm_client (HC-1: flag=False 时的默认行为).

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数 (None 时不传)
        max_tokens: 最大 token 数 (None 时不传)
        passthrough_module: 旧模块名 (默认 "llm_client")
        passthrough_function: 旧函数名 (默认 "chat")

    Returns:
        AI 回复文本, 模块/函数不存在时返回 None
    """
    try:
        mod = importlib.import_module(passthrough_module)
        fn = getattr(mod, passthrough_function, None)
        if fn is None:
            logger.error(
                "透传失败: %s.%s 不存在",
                passthrough_module,
                passthrough_function,
            )
            return None
        # 旧 llm_client.chat(prompt, system, temperature, max_tokens)
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return cast(Optional[str], fn(prompt, system, **kwargs))
    except ImportError as e:
        logger.error("透传失败: 无法导入 %s: %s", passthrough_module, e)
        return None
    except Exception as e:  # P2 模块 fail-safe, 待后续精确化
        logger.error("透传调用失败: %s", e)
        return None


def passthrough_deep_to_legacy(
    prompt: str,
    system: str,
    passthrough_module: str = "llm_client",
    passthrough_function: str = "chat",
) -> str | None:
    """透传 chat_deep 到旧 llm_client (优先 chat_deep, 回退到 chat).

    Args:
        prompt: 用户提示词
        system: 系统提示词
        passthrough_module: 旧模块名 (默认 "llm_client")
        passthrough_function: 旧函数名 (默认 "chat")

    Returns:
        AI 回复文本, 失败返回 None
    """
    try:
        mod = importlib.import_module(passthrough_module)
        # 优先 chat_deep
        fn = getattr(mod, "chat_deep", None)
        if fn:
            return cast(Optional[str], fn(prompt, system))
        # 没有 chat_deep, 用 chat
        fn = getattr(mod, passthrough_function)
        return cast(Optional[str], fn(prompt, system))
    except Exception as e:  # P2 模块 fail-safe, 待后续精确化
        logger.error("透传 chat_deep 失败: %s", e)
        return None


__all__ = ["passthrough_to_legacy", "passthrough_deep_to_legacy"]
