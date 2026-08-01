# -*- coding: utf-8 -*-
"""SiliconFlow Provider (OpenAI 兼容接口).

从原 `utils/alpha/llm_router.py:LLMRouter._call_siliconflow` 拆出 (B3.4.3)。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from utils.alpha.llm.openai_compat import openai_compatible_chat

logger = logging.getLogger("llm_router")


def call_siliconflow(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    provider_cfg: Dict[str, Any],
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> Optional[str]:
    """SiliconFlow — OpenAI 兼容接口.

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
        provider_cfg.get("base_url_env", "SILICONFLOW_BASE_URL"),
        provider_cfg.get("base_url_default", "https://api.siliconflow.cn/v1"),
    )
    api_key = os.environ.get(provider_cfg.get("api_key_env", "SILICONFLOW_API_KEY"), "")
    if not api_key:
        return None

    model = os.environ.get(
        provider_cfg.get("model_env", "SILICONFLOW_MODEL"),
        provider_cfg.get("model_default", "Qwen/Qwen2.5-7B-Instruct"),
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


__all__ = ["call_siliconflow"]
