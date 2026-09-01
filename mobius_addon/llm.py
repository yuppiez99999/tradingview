"""Mobius 外挂 LLM 模块（自包含、零侵入、fail-open）。

设计目标：
- 给外挂一个**真正可用**的 LLM 入口：除复用主系统 GLM-5 外，新增
  直接 OpenAI 兼容端点（由环境变量配置），在本机 GLM-5 不可用（如 llm_client
  缺失导致透传失败）时仍能工作。
- 提供统一的 `chat()` 与 `extract_json()`，集中 JSON 修复逻辑，消除
  extractor/report_extractor 中重复的 parse_json 代码。
- 所有方法 fail-open：任何异常被捕获并记录，返回 None 或默认，绝不抛出到调用方。

后端优先级（运行时探测，无缓存副作用）：
1. 直接 OpenAI 兼容端点（MOBIUS_LLM_API_KEY + MOBIUS_LLM_BASE_URL 已配置时）
2. 主系统 GLM-5 客户端（_common.glm5_quick_chat，只读 import，失败自动跳过）
3. 全部不可用 → 返回 None，调用方降级为规则模板
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

import _common as _c

_LOG = None


def _log() -> logging.Logger:
    global _LOG
    if _LOG is None:
        _LOG = _c.get_logger()
    return _LOG


def backend_status() -> str:
    """返回当前可用后端描述（用于 CLI 诊断），不触发网络。"""
    if os.environ.get("MOBIUS_LLM_API_KEY") and os.environ.get("MOBIUS_LLM_BASE_URL"):
        return "direct:" + os.environ.get("MOBIUS_LLM_MODEL", "default")
    try:
        root = _c.get_sys_root()
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.glm5_client import quick_chat  # noqa: WPS433  # type: ignore

        _ = quick_chat
        return "glm5"
    except Exception:  # noqa: BLE001
        return "none"


def _direct_chat(
    prompt: str,
    system: str | None,
    temperature: float,
    timeout: int,
    max_retries: int,
) -> str:
    """调用直接配置的 OpenAI 兼容 /chat/completions 端点。"""
    import requests  # 延迟导入：未配置端点时不依赖 requests

    base = os.environ["MOBIUS_LLM_BASE_URL"].rstrip("/")
    key = os.environ["MOBIUS_LLM_API_KEY"]
    model = os.environ.get("MOBIUS_LLM_MODEL", "gpt-4o-mini")
    url = base + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages, "temperature": temperature}
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _log().warning("直接 LLM 端点第 %d 次失败: %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(1.0)
    raise last_err or RuntimeError("direct chat failed")


def chat(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.2,
    timeout: int = 30,
    max_retries: int = 2,
) -> str | None:
    """统一 LLM 入口，返回文本或 None（fail-open）。"""
    # 1) 直接 OpenAI 兼容端点（优先，最可控）
    if os.environ.get("MOBIUS_LLM_API_KEY") and os.environ.get("MOBIUS_LLM_BASE_URL"):
        try:
            return _direct_chat(prompt, system, temperature, timeout, max_retries)
        except Exception as exc:  # noqa: BLE001
            _log().warning("直接 LLM 端点不可用, 尝试 GLM-5: %s", exc)
    # 2) 主系统 GLM-5（fail-open，内部已捕获异常）
    resp = _c.glm5_quick_chat(prompt, default="")
    return resp or None


def _repair_json(text: str) -> dict[str, Any] | None:
    """尽力从 LLM 文本中解析 JSON：去代码围栏 + 截取 + 去尾随逗号。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = s[start : end + 1]
    for attempt in (candidate, re.sub(r",(\s*[}\]])", r"\1", candidate)):
        try:
            return json.loads(attempt)
        except Exception:  # noqa: BLE001
            continue
    return None


def extract_json(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any] | None:
    """调用 LLM 并解析返回的 JSON；解析失败返回 None（调用方降级规则模板）。"""
    text = chat(prompt, system=system, temperature=temperature)
    if not text:
        return None
    return _repair_json(text)
