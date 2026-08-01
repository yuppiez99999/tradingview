# -*- coding: utf-8 -*-
"""DeepSeek Provider — V3 (deepseek-chat) + R1 (deepseek-reasoner).

从原 `utils/alpha/llm_router.py:LLMRouter._call_deepseek/_call_deepseek_reasoner` 拆出 (B3.4.3)。

两个模型:
    - deepseek-chat:      主 LLM, OpenAI 兼容端点 /v1/chat/completions
    - deepseek-reasoner:  云端推理模型, 支持 reasoning_content (思考过程)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional

from utils.alpha.llm.base import _safe_urlopen
from utils.alpha.llm.openai_compat import openai_compatible_chat

logger = logging.getLogger("llm_router")


def call_deepseek(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    provider_cfg: Dict[str, Any],
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> Optional[str]:
    """DeepSeek V3 (deepseek-chat) — OpenAI 兼容接口, 主 LLM.

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        timeout: 超时秒数
        provider_cfg: provider 配置字典 (含 base_url_env/api_key_env/model_env 等)
        max_retries: 最大重试次数
        retry_delay: 重试间隔秒数

    Returns:
        AI 回复文本, 无 API Key 或调用失败返回 None
    """
    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "DEEPSEEK_BASE_URL"),
        provider_cfg.get("base_url_default", "https://api.deepseek.com"),
    )
    api_key = os.environ.get(provider_cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
    if not api_key:
        return None

    model = os.environ.get(
        provider_cfg.get("model_env", "DEEPSEEK_MODEL"),
        provider_cfg.get("model_default", "deepseek-chat"),
    )

    # DeepSeek OpenAI 兼容端点: /v1/chat/completions
    return openai_compatible_chat(
        base_url=base_url.rstrip("/") + "/v1",
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


def call_deepseek_reasoner(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    provider_cfg: Dict[str, Any],
    timeout_seconds: int = 120,
) -> Optional[str]:
    """DeepSeek R1 (deepseek-reasoner) 云端推理模型, 用于复杂决策.

    支持 reasoning_content 字段 (思考过程).

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        provider_cfg: provider 配置字典
        timeout_seconds: 推理模型超时秒数 (默认 120, R1 推理较慢)

    Returns:
        AI 回复文本 (含推理过程), 失败返回 None
    """
    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "DEEPSEEK_BASE_URL"),
        provider_cfg.get("base_url_default", "https://api.deepseek.com"),
    )
    api_key = os.environ.get(provider_cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
    if not api_key:
        return None

    model = os.environ.get(
        provider_cfg.get("reasoner_model_env", "DEEPSEEK_REASONER_MODEL"),
        provider_cfg.get("reasoner_model_default", "deepseek-reasoner"),
    )

    try:
        url = base_url.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with _safe_urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        message = body.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if not content:
            content = message.get("reasoning_content")
        if isinstance(content, str) and content:
            reasoning = message.get("reasoning_content", "")
            if reasoning and len(reasoning) > 50 and reasoning != content:
                return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
            return content.strip()
        return None
    except Exception as e:  # P2 模块 fail-safe, 待后续精确化
        logger.warning("DeepSeek reasoner 调用失败: %s", e)
        return None


__all__ = ["call_deepseek", "call_deepseek_reasoner"]
