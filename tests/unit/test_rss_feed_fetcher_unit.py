"""rss_feed_fetcher 单元测试 (Wave 12-A #3).

被测模块: utils/rss_feed_fetcher.py
验收门禁: >=5个财经RSS源 / RSS解析可用 / 单测覆盖 / 降级不崩溃
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.rss_feed_fetcher import (  # noqa: E402
    FINANCIAL_RSS_SOURCES,
    RSSFeedFetcher,
)


def _mock_feed_entry(title="测试新闻", link="http://example.com/1", summary="摘要"):
    entry = MagicMock()
    entry.get = lambda key, default="": {
        "title": title,
        "link": link,
        "summary": summary,
        "published": "2026-08-30",
        "published_parsed": None,
        "source": {},
    }.get(key, default)
    return entry


def _mock_feed(entries=None, bozo=False):
    feed = MagicMock()
    feed.entries = entries or []
    feed.bozo = bozo
    feed.bozo_exception = "test error" if bozo else None
    return feed


class TestFinancialRssSources:
    """预定义 RSS 源测试."""

    def test_at_least_5_sources(self):
        assert len(FINANCIAL_RSS_SOURCES) >= 5

    def test_source_names_not_empty(self):
        for name in FINANCIAL_RSS_SOURCES:
            assert len(name) > 0

    def test_source_urls_valid(self):
        for _name, url in FINANCIAL_RSS_SOURCES.items():
            assert url.startswith("http://") or url.startswith("https://")


class TestFetchFeed:
    """fetch_feed 方法测试."""

    @patch("utils.rss_feed_fetcher._FEEDPARSER_AVAILABLE", True)
    @patch("utils.rss_feed_fetcher.feedparser")
    def test_success(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("新闻1"), _mock_feed_entry("新闻2")])
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_feed("http://example.com/rss")
        assert len(result) == 2
        assert result[0]["title"] == "新闻1"

    @patch("utils.rss_feed_fetcher._FEEDPARSER_AVAILABLE", True)
    @patch("utils.rss_feed_fetcher.feedparser")
    def test_max_items_limit(self, mock_fp):
        entries = [_mock_feed_entry(f"新闻{i}") for i in range(50)]
        mock_fp.parse.return_value = _mock_feed(entries)
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_feed("http://example.com/rss", max_items=5)
        assert len(result) == 5

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_empty_feed(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([])
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_feed("http://example.com/rss")
        assert result == []

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_bozo_feed_no_entries(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([], bozo=True)
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_feed("http://example.com/rss")
        assert result == []

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_parse_exception_no_crash(self, mock_fp):
        mock_fp.parse.side_effect = Exception("network error")
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_feed("http://example.com/rss")
        assert result == []

    def test_feedparser_unavailable(self):
        with patch("utils.rss_feed_fetcher._FEEDPARSER_AVAILABLE", False):
            fetcher = RSSFeedFetcher()
            result = fetcher.fetch_feed("http://example.com/rss")
            assert result == []


class TestFetchMultipleFeeds:
    """fetch_multiple_feeds 方法测试."""

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_all_sources(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("新闻")])
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_multiple_feeds()
        assert len(result) == len(FINANCIAL_RSS_SOURCES)
        for name in FINANCIAL_RSS_SOURCES:
            assert name in result

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_selected_sources(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("新闻")])
        fetcher = RSSFeedFetcher()
        names = ["Reuters_Finance", "CNBC_China"]
        result = fetcher.fetch_multiple_feeds(names)
        assert set(result.keys()) == set(names)

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_unknown_source(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([])
        fetcher = RSSFeedFetcher()
        result = fetcher.fetch_multiple_feeds(["unknown_source"])
        assert result["unknown_source"] == []


class TestGetFinancialNews:
    """get_financial_news 方法测试."""

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_returns_dict(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("新闻")])
        fetcher = RSSFeedFetcher()
        result = fetcher.get_financial_news()
        assert isinstance(result, dict)
        assert len(result) >= 5


class TestSearchNewsByKeyword:
    """search_news_by_keyword 方法测试."""

    @patch("utils.rss_feed_fetcher._FEEDPARSER_AVAILABLE", True)
    @patch("utils.rss_feed_fetcher.feedparser")
    def test_match_found(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("中国股市大涨", summary="今日行情")])
        fetcher = RSSFeedFetcher()
        result = fetcher.search_news_by_keyword("中国")
        assert len(result) >= 1
        assert "中国" in result[0]["title"]

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_no_match(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("体育新闻", summary="足球比赛")])
        fetcher = RSSFeedFetcher()
        result = fetcher.search_news_by_keyword("股市")
        assert result == []

    @patch("utils.rss_feed_fetcher._FEEDPARSER_AVAILABLE", True)
    @patch("utils.rss_feed_fetcher.feedparser")
    def test_match_in_summary(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("今日新闻", summary="中国股市收盘")])
        fetcher = RSSFeedFetcher()
        result = fetcher.search_news_by_keyword("中国")
        assert len(result) >= 1

    @patch("utils.rss_feed_fetcher.feedparser")
    def test_matched_source_field(self, mock_fp):
        mock_fp.parse.return_value = _mock_feed([_mock_feed_entry("中国新闻", summary="")])
        fetcher = RSSFeedFetcher()
        result = fetcher.search_news_by_keyword("中国")
        for item in result:
            assert "matched_source" in item


class TestParseEntry:
    """_parse_entry 静态方法测试."""

    def test_basic_parse(self):
        entry = _mock_feed_entry("标题", "http://link", "摘要")
        result = RSSFeedFetcher._parse_entry(entry)
        assert result["title"] == "标题"
        assert result["link"] == "http://link"
        assert result["summary"] == "摘要"

    def test_empty_entry(self):
        entry = MagicMock()
        entry.get = lambda key, default="": default
        result = RSSFeedFetcher._parse_entry(entry)
        assert result["title"] == ""
        assert result["link"] == ""
        assert result["summary"] == ""


class TestGetSourceNames:
    """get_source_names 方法测试."""

    def test_returns_list(self):
        fetcher = RSSFeedFetcher()
        names = fetcher.get_source_names()
        assert isinstance(names, list)
        assert len(names) >= 5

    def test_custom_sources(self):
        custom = {"源A": "http://a", "源B": "http://b"}
        fetcher = RSSFeedFetcher(sources=custom)
        names = fetcher.get_source_names()
        assert set(names) == {"源A", "源B"}
