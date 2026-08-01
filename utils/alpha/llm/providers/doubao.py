"""豆包 Speed Provider (火山引擎 Ark, OpenAI 兼容接口).

从原 `utils/alpha/llm_router.py:LLMRouter._call_doubao` 拆出 (B3.4.3)。

模型选择优先级:
    1. DOUBAO_ENDPOINT_ID (Ark 推理端点 ID, 优先)
    2. DOUBAO_SPEED_MODEL / DOUBAO_MODEL (模型名称回退)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.alpha.llm.openai_compat import openai_compatible_chat

logger = logging.getLogger("llm_router")


def call_doubao(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    provider_cfg: dict[str, Any],
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> str | None:
    """豆包 Speed (火山引擎 Ark) — OpenAI 兼容接口.

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        timeout: 超时秒数
        provider_cfg: provider 配置字典
        max_retries: 最大重试次数
        retry_delay: 重试间隔秒数

    Returns:
        AI 回复文本, 无 API Key 或调用失败返回 None
    """
    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "DOUBAO_SPEED_BASE_URL"),
        provider_cfg.get("base_url_default", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    api_key = os.environ.get(provider_cfg.get("api_key_env", "VOLCENGINE_API_KEY"), "")
    if not api_key:
        return None

    # 优先 endpoint_id, 否则用 model
    endpoint_env = provider_cfg.get("endpoint_id_env", "DOUBAO_ENDPOINT_ID")
    model = os.environ.get(endpoint_env, "") or os.environ.get(
        provider_cfg.get("model_env", "DOUBAO_SPEED_MODEL"),
        provider_cfg.get("model_default", "doubao-1-5-pro-32k-250115"),
    )

    return openai_compatible_chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


__all__ = ["call_doubao"]
