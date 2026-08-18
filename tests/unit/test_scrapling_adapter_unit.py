"""scrapling_adapter 单元测试 — Scrapling 高性能爬虫适配器

覆盖:
- _check_scrapling: 可用性检测 (缓存)
- ScraplingAdapter: 初始化/可用性/fetcher/fallback/抓取接口/降级
- get_adapter / fetch_news / fetch_url / is_available 便捷函数
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils import scrapling_adapter


# ============================================================
# _check_scrapling
# ============================================================


class TestCheckScrapling:
    """_check_scrapling 可用性检测测试"""

    def setup_method(self):
        """每个测试前重置缓存"""
        scrapling_adapter._scrapling_available = None
        scrapling_adapter._StealthyFetcher = None
        scrapling_adapter._Fetcher = None
        scrapling_adapter._PlayWrightFetcher = None

    def test_returns_bool(self):
        with patch("builtins.__import__", side_effect=ImportError):
            result = scrapling_adapter._check_scrapling()
        assert isinstance(result, bool)

    def test_caches_result(self):
        """第二次调用应返回缓存值"""
        with patch("builtins.__import__", side_effect=ImportError):
            r1 = scrapling_adapter._check_scrapling()
            r2 = scrapling_adapter._check_scrapling()
        assert r1 == r2


# ============================================================
# ScraplingAdapter
# ============================================================


class TestScraplingAdapter:
    """ScraplingAdapter 适配器测试"""

    def setup_method(self):
        scrapling_adapter._scrapling_available = None
        scrapling_adapter._default_adapter = None

    def test_init_defaults(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.use_stealth is True
        assert a.timeout == 30
        assert a._fetcher is None
        assert a._fallback is None
        assert a._available is None

    def test_init_custom(self):
        a = scrapling_adapter.ScraplingAdapter(use_stealth=False, timeout=60)
        assert a.use_stealth is False
        assert a.timeout == 60

    def test_is_available_returns_bool(self):
        a = scrapling_adapter.ScraplingAdapter()
        with patch.object(scrapling_adapter, "_check_scrapling", return_value=False):
            assert isinstance(a.is_available, bool)

    def test_is_available_caches(self):
        a = scrapling_adapter.ScraplingAdapter()
        with patch.object(scrapling_adapter, "_check_scrapling", return_value=False) as m:
            _ = a.is_available
            _ = a.is_available
        assert m.call_count == 1

    def test_get_fetcher_unavailable(self):
        """Scrapling 不可用 → 返回 None"""
        a = scrapling_adapter.ScraplingAdapter()
        a._available = False
        assert a._get_fetcher() is None

    def test_get_fetcher_caches(self):
        """fetcher 懒加载缓存"""
        a = scrapling_adapter.ScraplingAdapter()
        a._fetcher = "fake"
        assert a._get_fetcher() == "fake"

    def test_get_fallback_caches(self):
        """fallback 懒加载缓存"""
        a = scrapling_adapter.ScraplingAdapter()
        a._fallback = "fake"
        assert a._get_fallback() == "fake"

    def test_fetch_url_empty_url(self):
        """空 URL → 空结果"""
        a = scrapling_adapter.ScraplingAdapter()
        result = a.fetch_url("")
        assert result["success"] is False
        assert "error" in result
        assert result["url"] == ""

    def test_fetch_url_none_url(self):
        a = scrapling_adapter.ScraplingAdapter()
        result = a.fetch_url(None)
        assert result["success"] is False

    def test_fetch_news_empty_keyword(self):
        """空关键词 → 空列表"""
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_news("") == []

    def test_fetch_news_none_keyword(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_news(None) == []

    def test_fetch_announcements_empty(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_announcements("") == []

    def test_fetch_announcements_none(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_announcements(None) == []

    def test_fetch_research_reports_empty(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_research_reports("") == []

    def test_fetch_research_reports_none(self):
        a = scrapling_adapter.ScraplingAdapter()
        assert a.fetch_research_reports(None) == []

    def test_empty_result(self):
        a = scrapling_adapter.ScraplingAdapter()
        r = a._empty_result("http://x", "test error")
        assert r["success"] is False
        assert r["html"] == ""
        assert r["text"] == ""
        assert r["url"] == "http://x"
        assert r["source"] == "none"
        assert r["error"] == "test error"

    def test_extract_text_no_selector(self):
        """无选择器 → 返回页面文本"""
        a = scrapling_adapter.ScraplingAdapter()
        page = MagicMock()
        page.get_all_text.return_value = "hello world"
        text = a._extract_text(page, None)
        assert text == "hello world"

    def test_extract_text_with_selector(self):
        """有选择器 → 返回匹配元素文本"""
        a = scrapling_adapter.ScraplingAdapter()
        page = MagicMock()
        el = MagicMock()
        el.text = "matched"
        page.css.return_value = [el]
        text = a._extract_text(page, ".title")
        assert "matched" in text

    def test_extract_text_fallback_to_str(self):
        """无 text 属性 → str(page)"""
        a = scrapling_adapter.ScraplingAdapter()
        page = "raw string"
        text = a._extract_text(page, None)
        assert text == "raw string"

    def test_extract_text_exception_returns_empty(self):
        """异常 → 空字符串"""
        a = scrapling_adapter.ScraplingAdapter()
        page = MagicMock()
        page.get_all_text.side_effect = RuntimeError("boom")
        text = a._extract_text(page, None)
        assert text == ""

    def test_fetch_url_scrapling_unavailable_fallback(self):
        """Scrapling 不可用 → 降级 requests"""
        a = scrapling_adapter.ScraplingAdapter()
        a._available = False
        with patch.object(a, "_fallback_fetch_url", return_value={"success": True, "text": "ok"}):
            result = a.fetch_url("http://example.com")
        assert result["success"] is True

    def test_fetch_news_with_fallback_success(self):
        """fetch_news 优先用 WebScraper"""
        a = scrapling_adapter.ScraplingAdapter()
        a._available = False
        mock_scraper = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_item = MagicMock()
        mock_item.to_dict.return_value = {"title": "news1"}
        mock_result.items = [mock_item]
        mock_scraper.fetch_news.return_value = mock_result
        with patch.object(a, "_get_fallback", return_value=mock_scraper):
            items = a.fetch_news("半导体")
        assert len(items) == 1
        assert items[0]["title"] == "news1"

    def test_fetch_news_fallback_returns_empty(self):
        """WebScraper 返回空 → 空列表"""
        a = scrapling_adapter.ScraplingAdapter()
        a._available = False
        mock_scraper = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.items = []
        mock_scraper.fetch_news.return_value = mock_result
        with patch.object(a, "_get_fallback", return_value=mock_scraper):
            items = a.fetch_news("半导体")
        assert items == []

    def test_fetch_news_fallback_exception(self):
        """WebScraper 异常 → 空列表"""
        a = scrapling_adapter.ScraplingAdapter()
        a._available = False
        mock_scraper = MagicMock()
        mock_scraper.fetch_news.side_effect = RuntimeError("boom")
        with patch.object(a, "_get_fallback", return_value=mock_scraper):
            items = a.fetch_news("半导体")
        assert items == []


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """get_adapter / fetch_news / fetch_url / is_available 测试"""

    def setup_method(self):
        scrapling_adapter._default_adapter = None
        scrapling_adapter._scrapling_available = None

    def test_get_adapter_singleton(self):
        a1 = scrapling_adapter.get_adapter()
        a2 = scrapling_adapter.get_adapter()
        assert a1 is a2

    def test_get_adapter_creates_instance(self):
        a = scrapling_adapter.get_adapter()
        assert isinstance(a, scrapling_adapter.ScraplingAdapter)

    def test_is_available_returns_bool(self):
        with patch.object(scrapling_adapter, "_check_scrapling", return_value=False):
            assert isinstance(scrapling_adapter.is_available(), bool)

    def test_fetch_url_convenience(self):
        with patch.object(scrapling_adapter, "_check_scrapling", return_value=False):
            result = scrapling_adapter.fetch_url("")
        assert result["success"] is False

    def test_fetch_news_convenience_empty(self):
        with patch.object(scrapling_adapter, "_check_scrapling", return_value=False):
            result = scrapling_adapter.fetch_news("")
        assert result == []