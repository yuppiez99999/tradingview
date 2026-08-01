# -*- coding: utf-8 -*-
"""Ollama 本地 Provider (OpenAI 兼容端点 + 原生 /api/chat).

从原 `utils/alpha/llm_router.py:LLMRouter._call_ollama/_call_ollama_deep` 拆出 (B3.4.3)。

两个端点:
    - /v1/chat/completions  OpenAI 兼容 (call_ollama)
    - /api/chat             原生端点, 支持 reasoning_content (call_ollama_deep)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional, cast

from utils.alpha.llm.base import _safe_urlopen
from utils.alpha.llm.openai_compat import openai_compatible_chat

logger = logging.getLogger("llm_router")


def call_ollama(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    provider_cfg: Dict[str, Any],
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> Optional[str]:
    """Ollama 本地 — OpenAI 兼容接口 (/v1/chat/completions).

    Ollama 不需要 api_key, 但 OpenAI 兼容接口需要一个占位值。

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
        AI 回复文本, 调用失败返回 None
    """
    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "OLLAMA_BASE_URL"),
        provider_cfg.get("base_url_default", "http://localhost:11434"),
    )
    # Ollama 不需要 api_key, 但 OpenAI 兼容接口需要一个占位
    api_key = os.environ.get(
        provider_cfg.get("api_key_env", ""),
        provider_cfg.get("api_key_default", "ollama"),
    )

    model = os.environ.get(
        provider_cfg.get("model_env", "OLLAMA_MODEL"),
        provider_cfg.get("model_default", "qwen2.5:7b"),
    )

    # Ollama 的 OpenAI 兼容端点: /v1/chat/completions
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


def call_ollama_deep(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    provider_cfg: Dict[str, Any],
    ollama_timeout: int = 30,
) -> Optional[str]:
    """Ollama 深度推理模型 (deepseek-r1:14b) — 原生 /api/chat 端点.

    使用原生端点以支持 reasoning_content 字段 (思考过程).

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        provider_cfg: provider 配置字典
        ollama_timeout: 超时秒数 (默认 30, 本地模型)

    Returns:
        AI 回复文本 (含思考过程), 失败返回 None
    """
    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "OLLAMA_BASE_URL"),
        provider_cfg.get("base_url_default", "http://localhost:11434"),
    )
    # 兼容原代码: 读取 api_key_default 但不使用 (避免 linter 警告, 保留行为)
    provider_cfg.get("api_key_default", "ollama")
    model = os.environ.get(
        provider_cfg.get("deep_model_env", "OLLAMA_DEEP_MODEL"),
        provider_cfg.get("deep_model_default", "deepseek-r1:14b"),
    )

    # 使用原生 /api/chat 端点 (支持 reasoning_content)
    try:
        url = base_url.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json"}
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
        ).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with _safe_urlopen(req, timeout=ollama_timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        message = body.get("message", {})
        content = message.get("content", "")
        reasoning = message.get("reasoning_content", "")
        if content:
            if reasoning and len(reasoning) > 50:
                return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
            return cast(str, content.strip())
        return None
    except Exception as e:  # P2 模块 fail-safe, 待后续精确化
        logger.warning("Ollama deep 调用失败: %s", e)
        return None


__all__ = ["call_ollama", "call_ollama_deep"]
