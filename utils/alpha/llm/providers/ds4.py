"""ds4 本地 Provider — DwarfStar 推理引擎 (antirez/ds4).

ds4 是 Redis 作者 Salvatore Sanfilippo 推出的自包含原生推理引擎,
首发针对 DeepSeek V4 Flash 优化, 同时支持 GLM 5.2.

核心特性:
    - 自包含、刻意收窄 (非通用 GGUF runner)
    - 内置 HTTP server (OpenAI 兼容端点 /v1/chat/completions)
    - 2-bit 非对称量化 + DSpark 推测解码
    - Metal / CUDA / ROCm 多后端

集成日期: 2026-08-21 (W34, B 轨 ds4 POC)
Feature Flag: GLM5_DS4_ENABLED=1 开启 (默认关闭, shadow 验证后启用)

参考: GitHub本周热榜统计_20260818.md (antirez/ds4, 21.5k stars)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.alpha.llm.openai_compat import openai_compatible_chat

logger = logging.getLogger("llm_router")


def call_ds4(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    provider_cfg: dict[str, Any],
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> str | None:
    """ds4 本地推理引擎 — OpenAI 兼容接口.

    ds4 内置 HTTP server, 提供 OpenAI 兼容的 /v1/chat/completions 端点.
    默认模型 glm-antirez-q4 (GLM 5.2 Q4_K 单文件 GGUF, 见热榜文档).

    Feature Flag:
        GLM5_DS4_ENABLED=1 开启 (默认关闭, shadow 验证后启用)
        关闭时直接返回 None, 不影响 fallback 链

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
        AI 回复文本, 调用失败或 feature flag 关闭返回 None
    """
    if os.environ.get("GLM5_DS4_ENABLED", "0") != "1":
        logger.debug("ds4 provider 未启用 (GLM5_DS4_ENABLED!=1), 跳过")
        return None

    base_url = os.environ.get(
        provider_cfg.get("base_url_env", "DS4_BASE_URL"),
        provider_cfg.get("base_url_default", "http://localhost:8080"),
    )
    api_key = os.environ.get(
        provider_cfg.get("api_key_env", ""),
        provider_cfg.get("api_key_default", "ds4"),
    )
    model = os.environ.get(
        provider_cfg.get("model_env", "DS4_MODEL"),
        provider_cfg.get("model_default", "glm-antirez-q4"),
    )

    logger.info("ds4 调用: model=%s, base_url=%s", model, base_url)
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


__all__ = ["call_ds4"]
