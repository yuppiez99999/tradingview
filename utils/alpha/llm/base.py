"""LLM 路由器基础设施 — 异常 / CallRecord / _safe_urlopen / 项目根定位.

从原 `utils/alpha/llm_router.py` 拆出 (B3.4.3)。

设计原则:
    - 无业务逻辑, 仅类型定义与工具函数
    - 不依赖 LLMRouter, 可独立 import
    - `_safe_urlopen` 拒绝非 http/https 协议 (B310 安全规则)
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ============================================================
# 项目根定位 (比硬编码 parent.parent.parent 更健壮)
# ============================================================


def _find_project_root() -> Path:
    """向上查找项目根目录 (通过已知 marker 文件/目录识别)."""
    project_markers = [
        "config",
        "utils",
        "v8.3_institutional",
        "research",
        "tests",
        "requirements.txt",
        "ruff.toml",
        "pytest.ini",
    ]
    current = Path(__file__).resolve().parent
    # llm/base.py → llm/ → alpha/ → utils/ → 项目根 (向上 4 层)
    for _ in range(10):
        if any((current / m).exists() for m in project_markers):
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent.parent


# 项目根目录
_PROJECT_ROOT = _find_project_root()

# 审计日志目录
_AUDIT_LOG_DIR = _PROJECT_ROOT / "reports" / "llm_router"


# ============================================================
# 安全 urlopen (B310)
# ============================================================


def _safe_urlopen(req, timeout=None):
    """安全封装 urllib.request.urlopen — 拒绝非 http/https 协议 (B310)."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"拒绝非 HTTP 协议的 URL: {url[:100]}")
    if timeout is not None:
        return urllib.request.urlopen(req, timeout=timeout)  # nosec B310
    return urllib.request.urlopen(req)  # nosec B310  URL已校验为http/https


# ============================================================
# 异常定义
# ============================================================


class LLMRouterError(Exception):
    """LLMRouter 基础异常."""


class AllProvidersFailedError(LLMRouterError):
    """所有 provider 均失败."""

    def __init__(
        self,
        message: str,
        tried_providers: list,
        last_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.tried_providers = tried_providers
        self.last_error = last_error


class ProviderNotConfiguredError(LLMRouterError):
    """Provider 未配置 (缺少 API Key)."""


# ============================================================
# 类型定义
# ============================================================

# Provider 调用函数签名: (prompt, system, temperature, max_tokens, timeout) -> Optional[str]
ProviderFn = Callable[..., str | None]


# ============================================================
# CallRecord (审计日志用)
# ============================================================


class CallRecord:
    """单次调用记录 (审计日志用)."""

    def __init__(
        self,
        timestamp: str,
        prompt: str,
        system: str,
        provider: str,
        success: bool,
        latency_ms: float,
        error_type: str = "",
        error_message: str = "",
        response_preview: str = "",
    ) -> None:
        self.timestamp = timestamp
        self.prompt = prompt
        self.system = system
        self.provider = provider
        self.success = success
        self.latency_ms = latency_ms
        self.error_type = error_type
        self.error_message = error_message
        self.response_preview = response_preview

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (JSONL 序列化)."""
        return {
            "timestamp": self.timestamp,
            "prompt_preview": self.prompt[:200],
            "system_preview": self.system[:100],
            "provider": self.provider,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "error_type": self.error_type,
            "error_message": self.error_message[:500],
            "response_preview": self.response_preview[:200],
        }


__all__ = [
    "LLMRouterError",
    "AllProvidersFailedError",
    "ProviderNotConfiguredError",
    "ProviderFn",
    "CallRecord",
    "_safe_urlopen",
    "_find_project_root",
    "_PROJECT_ROOT",
    "_AUDIT_LOG_DIR",
]
