"""trafilatura 正文提取器 (Wave 12-A #4).

功能:
  1. 从 HTML 提取正文 (优于正则方案)
  2. 从 URL 直接提取
  3. 批量提取
  4. 文本清洗
  5. 降级不崩溃 (trafilatura 不可用时回退正则)

依赖: trafilatura (已安装 v2.0)
"""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    import trafilatura

    _TRAFILATURA_AVAILABLE = True
except ImportError:
    trafilatura = None
    _TRAFILATURA_AVAILABLE = False

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class WebContentExtractor:
    """Web 正文提取器 (trafilatura, Wave 12-A #4)."""

    def extract_text(
        self,
        html_content: str,
        include_metadata: bool = False,
    ) -> str | dict[str, Any] | None:
        """从 HTML 提取正文.

        Args:
            html_content: HTML 字符串.
            include_metadata: 是否返回元数据.

        Returns:
            str: 提取的正文; dict: 含元数据; None: 提取失败.
        """
        if not html_content:
            return None

        if _TRAFILATURA_AVAILABLE:
            return self._extract_trafilatura(html_content, include_metadata)
        return self._extract_regex(html_content)

    def extract_from_url(self, url: str, include_metadata: bool = False) -> str | dict[str, Any] | None:
        """从 URL 提取正文.

        Args:
            url: 目标 URL.
            include_metadata: 是否返回元数据.

        Returns:
            str | dict | None: 提取结果.
        """
        if not _TRAFILATURA_AVAILABLE:
            logger.warning("[WebContent] trafilatura 不可用，无法从 URL 提取")
            return None

        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded is None:
                logger.warning("[WebContent] URL 获取失败: %s", url)
                return None
            return self._extract_trafilatura(downloaded, include_metadata)
        except Exception as e:  # noqa: BLE001
            logger.warning("[WebContent] URL 提取失败 %s: %s", url, e)
            return None

    def extract_batch(
        self,
        items: list[str],
        include_metadata: bool = False,
    ) -> list[str | dict[str, Any] | None]:
        """批量提取正文.

        Args:
            items: HTML 字符串列表.
            include_metadata: 是否返回元数据.

        Returns:
            list: 提取结果列表.
        """
        return [self.extract_text(item, include_metadata) for item in items]

    def clean_text(self, text: str | None) -> str:
        """清洗文本 (去 HTML 标签 + 压缩空白).

        Args:
            text: 原始文本.

        Returns:
            str: 清洗后文本.
        """
        if not text:
            return ""

        result = _TAG_RE.sub("", text)
        result = _WHITESPACE_RE.sub(" ", result)
        return result.strip()

    def _extract_trafilatura(self, html: str, include_metadata: bool) -> str | dict[str, Any] | None:
        """trafilatura 后端提取."""
        try:
            if include_metadata:
                extracted = trafilatura.bare_extraction(html)
                if extracted is None:
                    return None
                return {
                    "text": getattr(extracted, "text", "") or "",
                    "title": getattr(extracted, "title", "") or "",
                    "author": getattr(extracted, "author", "") or "",
                    "date": getattr(extracted, "date", "") or "",
                    "url": getattr(extracted, "url", "") or "",
                }
            return trafilatura.extract(html)
        except Exception as e:  # noqa: BLE001
            logger.warning("[WebContent] trafilatura 提取失败: %s", e)
            return self._extract_regex(html)

    @staticmethod
    def _extract_regex(html: str) -> str | None:
        """正则降级后端提取."""
        result = _TAG_RE.sub("", html)
        result = _WHITESPACE_RE.sub(" ", result).strip()
        return result if result else None
