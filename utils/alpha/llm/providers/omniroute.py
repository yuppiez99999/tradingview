"""OmniRoute 网关 Provider — P0 最高优先级 (290+ provider, 500+ 模型).

从原 `utils/alpha/llm_router.py:LLMRouter._call_omniroute` 拆出 (B3.4.3)。

OmniRoute 内部已处理:
    - Feature Flag (USE_OMNIROUTE) 检查
    - 熔断器 (连续失败冷却)
    - Combo 自动路由 (配额耗尽/故障时无缝切换)
    - Token 压缩 (RTK + Caveman 双层, 15-95%)
"""

from __future__ import annotations

import logging

logger = logging.getLogger("llm_router")


def call_omniroute(
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str | None:
    """OmniRoute 网关调用 (P0 最高优先级).

    Args:
        prompt: 用户提示词
        system: 系统提示词
        temperature: 温度参数
        max_tokens: 最大 token 数
        timeout: 超时秒数 (OmniRoute 内部已含熔断, 此处仅作语义占位)

    Returns:
        AI 回复文本, 失败返回 None (降级到下一个 provider)
    """
    try:
        from utils.alpha.omni_route_client import OmniRouteClient

        client = OmniRouteClient.get_instance()
        return client.chat(prompt, system, temperature, max_tokens)
    except Exception as e:  # P2 模块 fail-safe
        logger.warning("OmniRoute provider 调用失败 (降级): %s", e)
        return None


__all__ = ["call_omniroute"]
