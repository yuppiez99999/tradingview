"""RSS 财经源解析器 (feedparser, Wave 12-A #3).

功能:
  1. 预定义 >=5 个财经 RSS 源
  2. 单源/多源 RSS 解析
  3. 关键词搜索过滤
  4. 降级不崩溃 (网络失败返回空)

依赖: feedparser (已安装 v6.0.11)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

try:
    import feedparser

    _FEEDPARSER_AVAILABLE = True
except ImportError:
    feedparser = None
    _FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)

FINANCIAL_RSS_SOURCES: dict[str, str] = {
    "Reuters_Finance": "https://news.google.com/rss/search?q=finance+china&hl=en",
    "CNBC_China": "https://news.google.com/rss/search?q=china+stock&hl=en",
    "Bloomberg_Asia": "https://news.google.com/rss/search?q=asia+market&hl=en",
    "Yahoo_Finance": "https://news.google.com/rss/search?q=yahoo+finance+china&hl=en",
    "东方财富": "https://news.google.com/rss/search?q=东方财富&hl=zh-CN",
    "新浪财经": "https://news.google.com/rss/search?q=新浪财经&hl=zh-CN",
    "华尔街见闻": "https://news.google.com/rss/search?q=华尔街见闻&hl=zh-CN",
}

_DEFAULT_MAX_ITEMS = 20


class RSSFeedFetcher:
    """RSS 财经源解析器 (feedparser, Wave 12-A #3)."""

    def __init__(self, sources: dict[str, str] | None = None):
        self.sources = sources if sources is not None else FINANCIAL_RSS_SOURCES

    def fetch_feed(self, feed_url: str, max_items: int = _DEFAULT_MAX_ITEMS) -> list[dict]:
        """获取单个 RSS 源.

        Args:
            feed_url: RSS 源 URL.
            max_items: 最大返回条数.

        Returns:
            list[dict]: 新闻条目列表, 每项含 title/link/summary/published/source.
        """
        if not _FEEDPARSER_AVAILABLE:
            logger.warning("[RSS] feedparser 不可用")
            return []

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:  # noqa: BLE001
            logger.warning("[RSS] 解析失败 %s: %s", feed_url, e)
            return []

        if feed.bozo and not feed.entries:
            logger.warning("[RSS] 源异常 %s: %s", feed_url, feed.bozo_exception)
            return []

        items = []
        for entry in feed.entries[:max_items]:
            items.append(self._parse_entry(entry))
        return items

    def fetch_multiple_feeds(
        self,
        source_names: list[str] | None = None,
        max_items_per_source: int = _DEFAULT_MAX_ITEMS,
    ) -> dict[str, list[dict]]:
        """获取多个 RSS 源.

        Args:
            source_names: 源名称列表, None 则获取全部预定义源.
            max_items_per_source: 每个源最大返回条数.

        Returns:
            dict[str, list[dict]]: {源名称: [新闻条目]}.
        """
        names = source_names if source_names is not None else list(self.sources.keys())
        result: dict[str, list[dict]] = {}
        for name in names:
            url = self.sources.get(name)
            if url is None:
                logger.warning("[RSS] 未知源: %s", name)
                result[name] = []
                continue
            result[name] = self.fetch_feed(url, max_items_per_source)
        return result

    def get_financial_news(self, max_items_per_source: int = _DEFAULT_MAX_ITEMS) -> dict[str, list[dict]]:
        """获取全部预定义财经新闻.

        Returns:
            dict[str, list[dict]]: {源名称: [新闻条目]}.
        """
        return self.fetch_multiple_feeds(None, max_items_per_source)

    def search_news_by_keyword(
        self,
        keyword: str,
        source_names: list[str] | None = None,
        max_items_per_source: int = _DEFAULT_MAX_ITEMS,
    ) -> list[dict]:
        """按关键词搜索新闻.

        Args:
            keyword: 搜索关键词.
            source_names: 源名称列表, None 则搜索全部预定义源.
            max_items_per_source: 每个源最大返回条数.

        Returns:
            list[dict]: 匹配的新闻条目列表.
        """
        all_feeds = self.fetch_multiple_feeds(source_names, max_items_per_source)
        matches = []
        for source_name, items in all_feeds.items():
            for item in items:
                title = item.get("title", "")
                summary = item.get("summary", "")
                if keyword in title or keyword in summary:
                    item_copy = {**item, "matched_source": source_name}
                    matches.append(item_copy)
        return matches

    def get_source_names(self) -> list[str]:
        """获取全部预定义源名称."""
        return list(self.sources.keys())

    @staticmethod
    def _parse_entry(entry: Any) -> dict:
        """解析单个 feed 条目为统一格式."""
        published = entry.get("published", "")
        parsed_date = None
        if published:
            try:
                dt = entry.get("published_parsed")
                if dt:
                    parsed_date = datetime(dt.tm_year, dt.tm_mon, dt.tm_mday, dt.tm_hour, dt.tm_min).isoformat()
            except (ValueError, TypeError, AttributeError):
                pass

        return {
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", "").strip(),
            "published": published,
            "published_parsed": parsed_date,
            "source": entry.get("source", {}).get("title", ""),
        }
