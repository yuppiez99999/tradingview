# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat/completions 通用调用 (含单 provider 内重试).

从原 `utils/alpha/llm_router.py:LLMRouter._openai_compatible_chat` 拆出 (B3.4.3)。

适用于: 豆包 / GLM / SiliconFlow / Ollama / DeepSeek (均提供 OpenAI 兼容端点)

行为:
    - 401/403 (认证失败): 立即返回 None, 不重试
    - 429 (限流): 等待 retry_delay 后重试
    - 5xx (服务器错误): 重试
    - 其他 HTTPError: 返回 None
    - URLError / 其他异常: 重试一次
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from utils.alpha.llm.base import _safe_urlopen

logger = logging.getLogger("llm_router")


def openai_compatible_chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> Optional[str]:
    """OpenAI 兼容 chat/completions 调用.

    Args:
        base_url: API 基础 URL (如 "https://api.deepseek.com/v1")
        api_key: Bearer token
        model: 模型名
        prompt: 用户提示词
        system: 系统提示词 (可选)
        temperature: 温度参数
        max_tokens: 最大 token 数
        timeout: 超时秒数
        max_retries: 最大重试次数 (默认 1)
        retry_delay: 重试间隔秒数 (默认 1.0)

    Returns:
        AI 回复文本, 失败返回 None
    """
    url = base_url.rstrip("/") + "/chat/completions"
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

    last_error: Optional[Exception] = None
    for attempt in range(1 + max_retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with _safe_urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            content = body.get("choices", [{}])[0].get("message", {}).get("content")
            if not content:
                content = body.get("choices", [{}])[0].get("message", {}).get("reasoning_content")
            return content if isinstance(content, str) else None
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (401, 403):
                logger.warning("认证失败 (%d), 不重试: %s", e.code, url)
                return None
            elif e.code == 429:
                logger.warning("限流 (429), %ds 后重试", retry_delay)
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue
            elif e.code >= 500:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue
            else:
                return None
        except urllib.error.URLError as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(retry_delay)
            continue
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            last_error = e
            return None

    logger.warning(
        "OpenAI 兼容调用失败 (重试 %d 次仍失败): %s, last_error=%s",
        max_retries,
        url,
        last_error,
    )
    return None


__all__ = ["openai_compatible_chat"]
