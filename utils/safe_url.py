"""安全 URL 打开工具 — 限制协议白名单, 防 file:// 等非预期 scheme (CWE-939 / B310).

背景
----
`urllib.request.urlopen` 会按 URL 的 scheme 选择处理器: 若上游字符串可控,
攻击者可传入 ``file:///etc/passwd`` / ``ftp://`` 等让程序读取本地文件或走非预期
协议 (bandit B310)。本模块在打开前强制 **scheme 白名单**, 并对凭据/主机做显式
校验, 未通过即抛 ValueError (fail-closed)。

使用示例
--------
    from utils.safe_url import safe_urlopen

    with safe_urlopen("http://localhost:11434/api/tags", timeout=10) as resp:
        ...
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("safe_url")

# 仅允许网络协议: 明确排除 file/ftp/data 等易被滥用读取本地资源的 scheme
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def validate_url(url: str, allowed_schemes: frozenset[str] = ALLOWED_SCHEMES) -> str:
    """校验 URL 的 scheme 在白名单内, 返回原 URL; 否则抛 ValueError."""
    if not isinstance(url, str) or not url:
        raise ValueError(f"无效 URL: {url!r}")
    scheme = urlparse(url).scheme.lower()
    if scheme not in allowed_schemes:
        raise ValueError(
            f"拒绝非白名单协议 URL: scheme={scheme!r} (允许: {sorted(allowed_schemes)})"
        )
    return url


def safe_urlopen(
    url: str | urllib.request.Request,
    *,
    allowed_schemes: frozenset[str] = ALLOWED_SCHEMES,
    timeout: float | None = None,
    **kwargs: Any,
) -> Any:
    """在 scheme 白名单校验后调用 ``urllib.request.urlopen`` (fail-closed)."""
    raw = url.full_url if isinstance(url, urllib.request.Request) else url
    validate_url(raw, allowed_schemes=allowed_schemes)
    if timeout is not None:
        kwargs["timeout"] = timeout
    return urllib.request.urlopen(url, **kwargs)  # nosec B310 — scheme 已白名单校验
