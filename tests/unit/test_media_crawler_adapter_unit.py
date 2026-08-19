"""media_crawler_adapter 单元测试 — MediaCrawler 自媒体舆情适配器

覆盖:
- MediaCrawlerNewsItem / MediaCrawlerResult dataclass
- normalize_platform / get_platform_display
- _TTLCache: get/set/clear/info
- MediaCrawlerAdapter: 初始化/健康检查/搜索/多平台/解析/缓存
- fetch_social_news
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from utils.media_crawler_adapter import (
    API_PLATFORM_VALUES,
    PLATFORM_MAP,
    MediaCrawlerAdapter,
    MediaCrawlerNewsItem,
    MediaCrawlerResult,
    _TTLCache,
    get_platform_display,
    normalize_platform,
)

# ============================================================
# MediaCrawlerNewsItem
# ============================================================


class TestMediaCrawlerNewsItem:
    """MediaCrawlerNewsItem dataclass 测试"""

    def test_init_defaults(self):
        item = MediaCrawlerNewsItem(title="test")
        assert item.title == "test"
        assert item.content == ""
        assert item.source == ""
        assert item.category == "social"
        assert item.sentiment_score == 0.0
        assert item.keywords == []
        assert item.comments == []
        assert item.raw == {}

    def test_init_full(self):
        item = MediaCrawlerNewsItem(
            title="标题",
            content="内容",
            url="http://x",
            source="xhs",
            sentiment_score=0.5,
            like_count=100,
            keywords=["AI"],
        )
        assert item.title == "标题"
        assert item.sentiment_score == 0.5
        assert item.like_count == 100

    def test_to_dict(self):
        item = MediaCrawlerNewsItem(title="test", like_count=10)
        d = item.to_dict()
        assert d["title"] == "test"
        assert d["like_count"] == 10
        assert "comments" in d


# ============================================================
# MediaCrawlerResult
# ============================================================


class TestMediaCrawlerResult:
    """MediaCrawlerResult dataclass 测试"""

    def test_init_defaults(self):
        r = MediaCrawlerResult(success=True)
        assert r.success is True
        assert r.items == []
        assert r.elapsed_ms == 0.0

    def test_to_dict(self):
        item = MediaCrawlerNewsItem(title="x")
        r = MediaCrawlerResult(success=True, items=[item], source="MediaCrawler", total_count=1)
        d = r.to_dict()
        assert d["success"] is True
        assert d["items_count"] == 1
        assert d["total_count"] == 1
        assert len(d["items"]) == 1


# ============================================================
# normalize_platform / get_platform_display
# ============================================================


class TestPlatformHelpers:
    """平台映射函数测试"""

    def test_normalize_platform_direct(self):
        assert normalize_platform("xhs") == "xhs"
        assert normalize_platform("dy") == "dy"

    def test_normalize_platform_alias(self):
        assert normalize_platform("xiaohongshu") == "xhs"
        assert normalize_platform("douyin") == "dy"
        assert normalize_platform("kuaishou") == "ks"
        assert normalize_platform("bilibili") == "bili"
        assert normalize_platform("weibo") == "wb"

    def test_normalize_platform_unknown(self):
        assert normalize_platform("unknown") == "xhs"  # 默认

    def test_normalize_platform_case(self):
        assert normalize_platform("XHS") == "xhs"
        assert normalize_platform("  Dy  ") == "dy"

    def test_get_platform_display(self):
        assert get_platform_display("xhs") == "小红书"
        assert get_platform_display("dy") == "抖音"
        assert get_platform_display("bili") == "B站"

    def test_get_platform_display_unknown(self):
        assert get_platform_display("unknown") == "unknown"

    def test_platform_map_keys(self):
        for key in API_PLATFORM_VALUES:
            assert key in PLATFORM_MAP


# ============================================================
# _TTLCache
# ============================================================


class TestTTLCache:
    """_TTLCache TTL 缓存测试"""

    def test_init(self):
        c = _TTLCache(ttl_seconds=60)
        assert c.ttl == 60

    def test_set_get(self):
        c = _TTLCache(ttl_seconds=60)
        c.set("key", "value")
        assert c.get("key") == "value"

    def test_get_missing(self):
        c = _TTLCache(ttl_seconds=60)
        assert c.get("missing") is None

    def test_get_expired(self):
        c = _TTLCache(ttl_seconds=1)
        c.set("key", "value", ttl=0)
        time.sleep(0.01)
        assert c.get("key") is None

    def test_clear(self):
        c = _TTLCache(ttl_seconds=60)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.get("a") is None
        assert c.get("b") is None

    def test_info(self):
        c = _TTLCache(ttl_seconds=60)
        c.set("a", 1)
        c.set("b", 2)
        info = c.info()
        assert info["total_entries"] == 2
        assert info["valid_entries"] == 2

    def test_info_with_expired(self):
        c = _TTLCache(ttl_seconds=60)
        c.set("a", 1, ttl=0)
        c.set("b", 2)
        time.sleep(0.01)
        info = c.info()
        assert info["total_entries"] == 2
        assert info["valid_entries"] == 1


# ============================================================
# MediaCrawlerAdapter
# ============================================================


class TestMediaCrawlerAdapter:
    """MediaCrawlerAdapter 适配器测试"""

    def test_init_defaults(self):
        a = MediaCrawlerAdapter()
        assert a.base_url == "http://localhost:8080"
        assert a.timeout == 30
        assert a.enabled is True

    def test_init_custom(self):
        a = MediaCrawlerAdapter(base_url="http://x:8081/", timeout=60, enabled=False)
        assert a.base_url == "http://x:8081"  # rstrip /
        assert a.timeout == 60
        assert a.enabled is False

    def test_check_health_disabled(self):
        a = MediaCrawlerAdapter(enabled=False)
        result = a.check_health()
        assert result["available"] is False
        assert result["reason"] == "feature_flag_disabled"

    def test_check_health_success(self):
        a = MediaCrawlerAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        with patch.object(a._session, "get", return_value=mock_resp):
            result = a.check_health()
        assert result["available"] is True

    def test_check_health_http_error(self):
        a = MediaCrawlerAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch.object(a._session, "get", return_value=mock_resp):
            result = a.check_health()
        assert result["available"] is False

    def test_check_health_exception(self):
        import requests
        a = MediaCrawlerAdapter()
        with patch.object(a._session, "get", side_effect=requests.RequestException("fail")):
            result = a.check_health()
        assert result["available"] is False

    def test_search_disabled(self):
        a = MediaCrawlerAdapter(enabled=False)
        result = a.search("test")
        assert result.success is False
        assert result.error == "feature_flag_disabled"

    def test_search_empty_keyword(self):
        a = MediaCrawlerAdapter()
        result = a.search("")
        assert result.success is False
        assert result.error == "empty_keyword"

    def test_search_whitespace_keyword(self):
        a = MediaCrawlerAdapter()
        result = a.search("   ")
        assert result.success is False
        assert result.error == "empty_keyword"

    def test_search_cache_hit(self):
        """缓存命中 → 直接返回"""
        a = MediaCrawlerAdapter()
        cached_result = MediaCrawlerResult(success=True, items=[MediaCrawlerNewsItem(title="cached")])
        with patch.object(a._cache, "get", return_value=cached_result):
            result = a.search("test", use_cache=True)
        assert result.success is True
        assert result.items[0].title == "cached"

    def test_search_request_exception(self):
        import requests
        a = MediaCrawlerAdapter()
        with patch.object(a._cache, "get", return_value=None):
            with patch.object(a._session, "post", side_effect=requests.RequestException("fail")):
                result = a.search("test")
        assert result.success is False

    def test_search_multi_platform_default(self):
        a = MediaCrawlerAdapter(enabled=False)
        results = a.search_multi_platform("test")
        assert len(results) == 4  # xhs, bili, wb, zhihu
        for r in results.values():
            assert r.success is False

    def test_search_multi_platform_custom(self):
        a = MediaCrawlerAdapter(enabled=False)
        results = a.search_multi_platform("test", platforms=["xhs", "dy"])
        assert len(results) == 2

    def test_clear_cache(self):
        a = MediaCrawlerAdapter()
        a.clear_cache()  # 不报错即可

    def test_cache_info(self):
        a = MediaCrawlerAdapter()
        info = a.cache_info()
        assert "total_entries" in info

    def test_fetch_social_news_disabled(self):
        a = MediaCrawlerAdapter(enabled=False)
        items = a.fetch_social_news("test")
        assert items == []

    def test_parse_raw_item_xhs(self):
        a = MediaCrawlerAdapter()
        raw = {
            "title": "小红书笔记",
            "desc": "内容描述",
            "note_id": "abc123",
            "liked_count": 100,
            "nickname": "user1",
        }
        item = a._parse_raw_item(raw, "xhs")
        assert item is not None
        assert item.title == "小红书笔记"
        assert item.like_count == 100
        assert item.platform == "xhs"

    def test_parse_raw_item_douyin(self):
        a = MediaCrawlerAdapter()
        raw = {
            "aweme_title": "抖音视频",
            "video_desc": "描述",
            "aweme_id": "dy123",
            "create_time": 1700000000,
        }
        item = a._parse_raw_item(raw, "dy")
        assert item is not None
        assert item.title == "抖音视频"
        assert item.platform == "dy"

    def test_parse_raw_item_empty(self):
        a = MediaCrawlerAdapter()
        item = a._parse_raw_item({}, "xhs")
        assert item is not None
        assert item.title == ""

    def test_parse_raw_item_millisecond_ts(self):
        """毫秒级时间戳"""
        a = MediaCrawlerAdapter()
        raw = {"title": "x", "create_time": 1700000000000}  # 毫秒
        item = a._parse_raw_item(raw, "xhs")
        assert item is not None
        assert item.published_at != ""

    def test_fetch_latest_data_empty(self):
        a = MediaCrawlerAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(a._session, "get", return_value=mock_resp):
            items = a._fetch_latest_data("xhs", 10)
        assert items == []

    def test_fetch_latest_data_with_files(self):
        a = MediaCrawlerAdapter()
        files_resp = MagicMock()
        files_resp.status_code = 200
        files_resp.json.return_value = {"files": [{"path": "data.json"}]}

        content_resp = MagicMock()
        content_resp.status_code = 200
        content_resp.json.return_value = {"content": [{"title": "item1"}, {"title": "item2"}]}

        with patch.object(a._session, "get", side_effect=[files_resp, content_resp]):
            items = a._fetch_latest_data("xhs", 10)
        assert len(items) == 2
