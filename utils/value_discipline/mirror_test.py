"""镜子测试 — 论点能否在 N 句话内说清且不丢核心

源自 ai-berkshire "5句话说不完整 = 不买" 纪律。
LLM 失败时降级为通过 (不阻塞主流程)。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def mirror_test(
    thesis: str,
    max_sentences: int = 5,
    llm_caller: Any | None = None,
    fallback: bool = True,
) -> bool:
    """判断 thesis 能否压缩到 max_sentences 句且保留核心论点。

    Args:
        thesis: 投资论点文本 (通常 signal.reason)
        max_sentences: 最大句数
        llm_caller: callable(prompt) -> str, 复用主系统 LLM
        fallback: LLM 不可用时是否默认通过

    Returns:
        True = 可压缩 (论点清晰), False = 不可压缩 (action 应降级 HOLD)
    """
    if not thesis or len(thesis.strip()) < 10:
        return True

    if llm_caller is None:
        return fallback

    prompt = (
        f"以下投资论点能否在{max_sentences}句话内说清且不丢核心论点?\n"
        f"论点: {thesis[:500]}\n"
        f'仅输出 JSON: {{"compressible": true或false}}'
    )
    try:
        raw = llm_caller(prompt)
        text = (
            raw.strip() if isinstance(raw, str) else getattr(raw, "content", "").strip()
        )
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return fallback
        obj = json.loads(text[start : end + 1])
        return bool(obj.get("compressible", fallback))
    except Exception as exc:
        logger.warning("镜子测试 LLM 失败, 降级 fallback=%s: %s", fallback, exc)
        return fallback
