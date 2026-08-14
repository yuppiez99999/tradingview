"""G7 覆盖率冲刺 — utils/graph_data_source.py 单元测试.

目标: 覆盖率从 12.83% → 60%+

测试范围:
    1. 模块常量与纯函数: _safe_float / _market_of / _ensure_utf8_stream
    2. GraphDataSource.__init__
    3. _cached: TTL 命中/未命中/None 不缓存
    4. _get: 成功/ConnectionError 重建 Session/ValueError 重试/空响应重试/全部失败
    5. get_stock_boards: _classify 全分支/diff 解析/空 name 跳过
    6. get_industry_relationship: 行业/概念/地域解析/无行业返回 None
    7. get_concept_blocks: None/空/正常/含空段
    8. get_themes: errocode/空 data/正常/默认日期
    9. fetch_board_stocks: 文件缓存命中/未命中+请求成功/失败/回写
    10. _load_board_cache: 文件不存在/过期/正常/JSON 异常/非 list
    11. _save_board_cache: 新建/读取已有/已有异常
    12. fetch_main_business: 文件缓存命中/请求成功解析/None/其他过滤/排序截断
    13. _load_json_cache_value: 不存在/过期/正常/异常
    14. build_concept_edges: 无概念/共享/不共享/min_share/strength 上限
    15. build_industry_edges: 同行业/不同行业
    16. build_thematic_edges: 空/单股/多股/dedup
    17. build_main_business_edges: 无/共享/min_shared/dedup/strength
    18. build_graph_edges: 组合/include 开关
    19. get_graph_data_source: 单例创建/复用

约束:
    - 不发起任何真实网络请求
    - 用 unittest.mock.patch / MagicMock 隔离外部依赖
    - 测试文件可独立运行:
      python -m pytest tests/unit/test_g7_graph_data_source_boost.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

# ============================================================
# PROJECT_ROOT sys.path 注入 (使测试文件可独立运行)
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import graph_data_source as gds_module  # noqa: E402
from utils.graph_data_source import (  # noqa: E402
    _EM_HEADERS,
    _EM_INTERVAL,
    _EM_RETRIES,
    _NO_PROXY_DOMAINS,
    _BOARD_CACHE_FILE,
    _THS_HEADERS,
    _UA,
    GraphDataSource,
    _ensure_utf8_stream,
    _market_of,
    _safe_float,
    get_graph_data_source,
)


# ============================================================
# 公共 fixtures
# ============================================================


@pytest.fixture
def fresh_ds():
    """每个测试一个全新的 GraphDataSource (cache_ttl=0 → 始终 miss, 避免缓存干扰)."""
    return GraphDataSource(cache_ttl=0)


@pytest.fixture
def no_sleep():
    """patch time.sleep 避免重试导致的测试延迟."""
    with patch("utils.graph_data_source.time.sleep") as m:
        yield m


# ============================================================
# 1. 模块常量
# ============================================================


class TestModuleConstants:
    def test_no_proxy_domains(self):
        assert "eastmoney.com" in _NO_PROXY_DOMAINS
        assert "10jqka.com.cn" in _NO_PROXY_DOMAINS
        assert "baidu.com" in _NO_PROXY_DOMAINS

    def test_ua_is_string(self):
        assert isinstance(_UA, str)
        assert "Mozilla" in _UA

    def test_em_headers_structure(self):
        assert _EM_HEADERS["User-Agent"] == _UA
        assert "Referer" in _EM_HEADERS
        assert "Accept" in _EM_HEADERS

    def test_ths_headers_structure(self):
        assert _THS_HEADERS["User-Agent"] == _UA

    def test_em_interval_positive(self):
        assert _EM_INTERVAL > 0

    def test_em_retries_positive(self):
        assert _EM_RETRIES >= 1

    def test_board_cache_file_is_string(self):
        assert isinstance(_BOARD_CACHE_FILE, str)
        assert len(_BOARD_CACHE_FILE) > 0


# ============================================================
# 2. 纯函数: _safe_float
# ============================================================


class TestSafeFloat:
    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_float(self):
        assert _safe_float(3.14) == 3.14

    def test_string_number(self):
        assert _safe_float("2.5") == 2.5

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_empty_string(self):
        assert _safe_float("") == 0.0

    def test_nan_returns_default(self):
        assert _safe_float(float("nan")) == 0.0

    def test_nan_with_custom_default(self):
        assert _safe_float(float("nan"), default=-1.0) == -1.0

    def test_invalid_string(self):
        assert _safe_float("abc") == 0.0

    def test_list_raises_typeerror(self):
        assert _safe_float([1, 2]) == 0.0

    def test_zero_string(self):
        assert _safe_float("0") == 0.0

    def test_falsy_int(self):
        assert _safe_float(0) == 0.0


# ============================================================
# 3. 纯函数: _market_of
# ============================================================


class TestMarketOf:
    def test_sh_6_prefix(self):
        assert _market_of("600276") == "1"

    def test_sh_5_prefix(self):
        assert _market_of("510300") == "1"

    def test_sh_9_prefix(self):
        assert _market_of("900001") == "1"

    def test_sz_0_prefix(self):
        assert _market_of("000001") == "0"

    def test_sz_3_prefix(self):
        assert _market_of("300308") == "0"

    def test_bj_4_prefix(self):
        assert _market_of("430001") == "0"

    def test_bj_8_prefix(self):
        assert _market_of("830001") == "0"

    def test_with_whitespace(self):
        assert _market_of("  600276  ") == "1"

    def test_non_numeric(self):
        assert _market_of("TEST001") == "0"


# ============================================================
# 4. _ensure_utf8_stream (幂等包装)
# ============================================================


class TestEnsureUtf8Stream:
    def test_idempotent_no_raise(self):
        """多次调用不报错 (stdout 在 pytest 中通常已 UTF-8)."""
        _ensure_utf8_stream()
        _ensure_utf8_stream()

    def test_wraps_non_utf8_stream(self):
        """非 UTF-8 流被包装为 UTF-8 TextIOWrapper."""
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        try:
            fake_stream = MagicMock()
            fake_stream.encoding = "ascii"
            fake_stream.buffer = io.BytesIO(b"")
            sys.stdout = fake_stream
            sys.stderr = MagicMock()
            sys.stderr.encoding = "utf-8"

            _ensure_utf8_stream()

            assert isinstance(sys.stdout, io.TextIOWrapper)
            assert sys.stdout.encoding.lower() == "utf-8"
        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr

    def test_skip_stream_without_buffer(self):
        """无 buffer 属性的流被跳过."""
        orig_stdout = sys.stdout
        try:
            fake_stream = MagicMock()
            fake_stream.encoding = "ascii"
            fake_stream.buffer = None
            sys.stdout = fake_stream

            _ensure_utf8_stream()

            # stdout 未被替换 (无 buffer → continue)
            assert sys.stdout is fake_stream
        finally:
            sys.stdout = orig_stdout

    def test_none_stream_skipped(self):
        """sys.stdout 为 None 时跳过不报错."""
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            _ensure_utf8_stream()
        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr


# ============================================================
# 5. GraphDataSource.__init__
# ============================================================


class TestGraphDataSourceInit:
    def test_default_ttl(self):
        ds = GraphDataSource()
        assert ds.cache_ttl == 3600

    def test_custom_ttl(self):
        ds = GraphDataSource(cache_ttl=600)
        assert ds.cache_ttl == 600

    def test_cache_empty(self):
        ds = GraphDataSource()
        assert ds._cache == {}

    def test_source_health_structure(self):
        ds = GraphDataSource()
        assert "eastmoney_push2" in ds.source_health
        assert "ths_hot_reason" in ds.source_health
        assert ds.source_health["eastmoney_push2"]["ok"] is False
        assert ds.source_health["eastmoney_push2"]["last_error"] is None
        assert ds.source_health["eastmoney_push2"]["last_success"] is None

    def test_session_has_em_headers(self):
        ds = GraphDataSource()
        assert ds._session.headers["User-Agent"] == _UA


# ============================================================
# 6. _cached (TTL 缓存)
# ============================================================


class TestCached:
    def test_miss_then_hit(self):
        ds = GraphDataSource(cache_ttl=3600)
        calls = []

        def fetcher():
            calls.append(1)
            return "value"

        assert ds._cached("key", fetcher) == "value"
        assert ds._cached("key", fetcher) == "value"
        assert len(calls) == 1  # 第二次命中缓存

    def test_none_not_cached(self):
        ds = GraphDataSource(cache_ttl=3600)
        calls = []

        def fetcher():
            calls.append(1)
            return None

        assert ds._cached("none_key", fetcher) is None
        assert ds._cached("none_key", fetcher) is None
        assert len(calls) == 2  # None 不缓存, 每次都调用

    def test_expired_re_fetches(self):
        ds = GraphDataSource(cache_ttl=0)  # TTL=0 → 始终过期
        calls = []

        def fetcher():
            calls.append(1)
            return "value"

        ds._cached("key", fetcher)
        ds._cached("key", fetcher)
        assert len(calls) == 2

    def test_fetcher_returns_empty_list(self):
        """空 list 是 falsy 但不是 None → 应缓存."""
        ds = GraphDataSource(cache_ttl=3600)
        calls = []

        def fetcher():
            calls.append(1)
            return []

        assert ds._cached("empty_key", fetcher) == []
        assert ds._cached("empty_key", fetcher) == []
        assert len(calls) == 1  # [] not None → cached


# ============================================================
# 7. _get (带重试的 HTTP 请求)
# ============================================================


class TestGet:
    def test_success(self, fresh_ds, no_sleep):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"data": "ok"}
        fresh_ds._session = MagicMock()
        fresh_ds._session.get.return_value = mock_resp

        result = fresh_ds._get("http://test", {"p": 1})

        assert result == {"data": "ok"}
        assert fresh_ds.source_health["eastmoney_push2"]["ok"] is True
        assert fresh_ds.source_health["eastmoney_push2"]["last_error"] is None
        assert fresh_ds.source_health["eastmoney_push2"]["last_success"] is not None

    def test_success_with_custom_headers_and_source(self, fresh_ds, no_sleep):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"data": "ok"}
        fresh_ds._session = MagicMock()
        fresh_ds._session.get.return_value = mock_resp

        custom_headers = {"X-Custom": "1"}
        result = fresh_ds._get("http://test", {}, headers=custom_headers,
                               source="ths_hot_reason", timeout=5)

        assert result == {"data": "ok"}
        assert fresh_ds.source_health["ths_hot_reason"]["ok"] is True
        # 验证 headers 传递
        _, kwargs = fresh_ds._session.get.call_args
        assert kwargs["headers"] == custom_headers
        assert kwargs["timeout"] == 5

    def test_empty_response_retry_then_success(self, fresh_ds, no_sleep):
        mock_empty = MagicMock()
        mock_empty.raise_for_status.return_value = None
        mock_empty.json.return_value = {}  # 空响应 → 重试

        mock_ok = MagicMock()
        mock_ok.raise_for_status.return_value = None
        mock_ok.json.return_value = {"data": "ok"}

        fresh_ds._session = MagicMock()
        fresh_ds._session.get.side_effect = [mock_empty, mock_ok]

        result = fresh_ds._get("http://test", {})

        assert result == {"data": "ok"}
        assert fresh_ds.source_health["eastmoney_push2"]["ok"] is True

    def test_value_error_all_fail(self, fresh_ds, no_sleep):
        fresh_ds._session = MagicMock()
        fresh_ds._session.get.side_effect = ValueError("parse error")

        result = fresh_ds._get("http://test", {})

        assert result is None
        assert fresh_ds.source_health["eastmoney_push2"]["ok"] is False
        assert "parse error" in fresh_ds.source_health["eastmoney_push2"]["last_error"]

    def test_http_error_all_fail(self, fresh_ds, no_sleep):
        """raise_for_status 抛 HTTPError (OSError 子类) → 重试."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        fresh_ds._session = MagicMock()
        fresh_ds._session.get.return_value = mock_resp

        result = fresh_ds._get("http://test", {})

        assert result is None
        assert fresh_ds.source_health["eastmoney_push2"]["ok"] is False

    def test_connection_error_all_fail(self, no_sleep):
        """ConnectionError → 重建 Session, 全部失败 → None."""
        ds = GraphDataSource()
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("reset")
        ds._session = mock_session

        with patch("utils.graph_data_source.requests.Session", return_value=mock_session):
            result = ds._get("http://test", {})

        assert result is None
        assert ds.source_health["eastmoney_push2"]["ok"] is False
        assert "reset" in ds.source_health["eastmoney_push2"]["last_error"]

    def test_connection_error_then_success(self, no_sleep):
        """ConnectionError 后重建 Session, 下次成功."""
        ds = GraphDataSource()

        mock_session_1 = MagicMock()
        mock_session_1.get.side_effect = requests.exceptions.ConnectionError("reset")

        mock_session_2 = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"ok": True}
        mock_session_2.get.return_value = mock_resp

        ds._session = mock_session_1

        with patch("utils.graph_data_source.requests.Session", return_value=mock_session_2):
            result = ds._get("http://test", {})

        assert result == {"ok": True}
        assert ds.source_health["eastmoney_push2"]["ok"] is True
        assert ds._session is mock_session_2  # session 已重建

    def test_connection_error_close_exception(self, no_sleep):
        """Session.close() 异常被捕获, 不影响重建."""
        ds = GraphDataSource()
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("reset")
        mock_session.close.side_effect = ValueError("close error")
        ds._session = mock_session

        with patch("utils.graph_data_source.requests.Session", return_value=mock_session):
            result = ds._get("http://test", {})

        assert result is None  # 不因 close 异常崩溃


# ============================================================
# 8. get_stock_boards (含 _classify 全分支)
# ============================================================


class TestGetStockBoards:
    def test_classify_all_categories(self, fresh_ds):
        """一条 diff 覆盖 _classify 的 stock/concept/region/industry/index 五分支."""
        mock_response = {
            "data": {
                "diff": [
                    {"f12": "600276", "f14": "恒瑞医药"},        # 6位数字 → stock
                    {"f12": "BK1036", "f14": "半导体概念"},       # 含"概念" → concept
                    {"f12": "BK1037", "f14": "江苏板块"},         # 含"板块" → region
                    {"f12": "BK1038", "f14": "半导体"},           # BK开头 → industry
                    {"f12": "XX999", "f14": "上证指数"},          # 兜底 → index
                ]
            }
        }
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")

        assert len(boards) == 5
        cat_map = {b["name"]: b["category"] for b in boards}
        assert cat_map["恒瑞医药"] == "stock"
        assert cat_map["半导体概念"] == "concept"
        assert cat_map["江苏板块"] == "region"
        assert cat_map["半导体"] == "industry"
        assert cat_map["上证指数"] == "index"

    def test_empty_name_skipped(self, fresh_ds):
        mock_response = {
            "data": {"diff": [
                {"f12": "BK001", "f14": ""},           # 空名 → skip
                {"f12": "BK002", "f14": "None"},       # "None" → skip
                {"f12": "BK003", "f14": "有效板块"},    # 保留
            ]}
        }
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")
        assert len(boards) == 1
        assert boards[0]["name"] == "有效板块"

    def test_get_returns_none(self, fresh_ds):
        with patch.object(fresh_ds, "_get", return_value=None):
            boards = fresh_ds.get_stock_boards("688041")
        assert boards == []

    def test_empty_diff(self, fresh_ds):
        mock_response = {"data": {"diff": []}}
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")
        assert boards == []

    def test_missing_data_key(self, fresh_ds):
        """data 为 None → {} → diff 为 [] → []."""
        mock_response = {"data": None}
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")
        assert boards == []

    def test_missing_diff_key(self, fresh_ds):
        """data 存在但无 diff → []."""
        mock_response = {"data": {"other": "val"}}
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")
        assert boards == []

    def test_items_missing_fields(self, fresh_ds):
        """item 缺 f12/f14 → 空字符串, name 空 → skip."""
        mock_response = {"data": {"diff": [
            {"f12": "BK001"},  # 无 f14 → name="" → skip
            {"f14": "有名字"},  # 无 f12 → code=""
            {"f13": 1},        # 都缺 → skip (name="")
        ]}}
        with patch.object(fresh_ds, "_get", return_value=mock_response):
            boards = fresh_ds.get_stock_boards("688041")
        assert len(boards) == 1
        assert boards[0]["name"] == "有名字"
        assert boards[0]["code"] == ""

    def test_market_prefix_in_params(self, fresh_ds):
        """验证 secid 中市场号拼接."""
        mock_response = {"data": {"diff": []}}
        with patch.object(fresh_ds, "_get", return_value=mock_response) as mock_get:
            fresh_ds.get_stock_boards("688041")
        _, kwargs = mock_get.call_args
        # 原始调用参数: (url, params)
        args = mock_get.call_args[0]
        assert args[1]["secid"] == "1.688041"  # 6开头 → 沪市 1

    def test_market_prefix_sz(self, fresh_ds):
        mock_response = {"data": {"diff": []}}
        with patch.object(fresh_ds, "_get", return_value=mock_response) as mock_get:
            fresh_ds.get_stock_boards("300308")
        args = mock_get.call_args[0]
        assert args[1]["secid"] == "0.300308"  # 3开头 → 深市 0


# ============================================================
# 9. get_industry_relationship
# ============================================================


class TestGetIndustryRelationship:
    def test_no_boards_returns_none(self, fresh_ds):
        with patch.object(fresh_ds, "get_stock_boards", return_value=[]):
            result = fresh_ds.get_industry_relationship("688041")
        assert result is None

    def test_with_industry_concept_region(self, fresh_ds):
        boards = [
            {"code": "BK1038", "name": "半导体", "category": "industry"},
            {"code": "BK1036", "name": "芯片概念", "category": "concept"},
            {"code": "BK1037", "name": "上海板块", "category": "region"},
            {"code": "BK1039", "name": "另一概念", "category": "concept"},
        ]
        with patch.object(fresh_ds, "get_stock_boards", return_value=boards):
            result = fresh_ds.get_industry_relationship("688041")

        assert result is not None
        assert result["code"] == "688041"
        assert result["industry"] == "半导体"
        assert result["industry_code"] == "BK1038"
        assert result["region"] == "上海板块"
        assert "芯片概念" in result["concepts"]
        assert "另一概念" in result["concepts"]

    def test_no_industry_returns_none(self, fresh_ds):
        """有概念/地域但无行业 → None."""
        boards = [
            {"code": "BK1036", "name": "芯片概念", "category": "concept"},
            {"code": "BK1037", "name": "上海板块", "category": "region"},
        ]
        with patch.object(fresh_ds, "get_stock_boards", return_value=boards):
            result = fresh_ds.get_industry_relationship("688041")
        assert result is None

    def test_first_industry_wins(self, fresh_ds):
        """多个行业板块取第一个 (not industry 为 False 时)."""
        boards = [
            {"code": "BK001", "name": "行业A", "category": "industry"},
            {"code": "BK002", "name": "行业B", "category": "industry"},
        ]
        with patch.object(fresh_ds, "get_stock_boards", return_value=boards):
            result = fresh_ds.get_industry_relationship("688041")
        assert result["industry"] == "行业A"
        assert result["industry_code"] == "BK001"

    def test_name_field_empty(self, fresh_ds):
        boards = [{"code": "BK001", "name": "半导体", "category": "industry"}]
        with patch.object(fresh_ds, "get_stock_boards", return_value=boards):
            result = fresh_ds.get_industry_relationship("688041")
        assert result["name"] == ""  # slist/get 不返回个股名称


# ============================================================
# 10. get_concept_blocks
# ============================================================


class TestGetConceptBlocks:
    def test_no_info_returns_empty(self, fresh_ds):
        with patch.object(fresh_ds, "get_industry_relationship", return_value=None):
            result = fresh_ds.get_concept_blocks("688041")
        assert result == []

    def test_empty_concepts_returns_empty(self, fresh_ds):
        info = {"concepts": ""}
        with patch.object(fresh_ds, "get_industry_relationship", return_value=info):
            result = fresh_ds.get_concept_blocks("688041")
        assert result == []

    def test_none_concepts_returns_empty(self, fresh_ds):
        info = {"concepts": None}
        with patch.object(fresh_ds, "get_industry_relationship", return_value=info):
            result = fresh_ds.get_concept_blocks("688041")
        assert result == []

    def test_normal_concepts(self, fresh_ds):
        info = {"concepts": "芯片,半导体,新能源"}
        with patch.object(fresh_ds, "get_industry_relationship", return_value=info):
            result = fresh_ds.get_concept_blocks("688041")
        assert result == ["芯片", "半导体", "新能源"]

    def test_concepts_with_empty_segments(self, fresh_ds):
        info = {"concepts": "芯片,, ,半导体,"}
        with patch.object(fresh_ds, "get_industry_relationship", return_value=info):
            result = fresh_ds.get_concept_blocks("688041")
        assert result == ["芯片", "半导体"]


# ============================================================
# 11. get_themes
# ============================================================


class TestGetThemes:
    def test_default_date(self, fresh_ds):
        """date=None → 使用今天日期构造 URL."""
        mock_resp = {"errocode": 0, "data": [{"code": "001", "reason": "芯片"}]}
        with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
            result = fresh_ds.get_themes()

        today = time.strftime("%Y-%m-%d")
        url_arg = mock_get.call_args[0][0]
        assert today in url_arg
        assert result == [{"code": "001", "reason": "芯片"}]

    def test_custom_date(self, fresh_ds):
        mock_resp = {"errocode": 0, "data": [{"code": "001"}]}
        with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
            result = fresh_ds.get_themes("2026-01-15")

        url_arg = mock_get.call_args[0][0]
        assert "2026-01-15" in url_arg
        assert result == [{"code": "001"}]

    def test_error_code_returns_empty(self, fresh_ds, caplog):
        mock_resp = {"errocode": 1, "errormsg": "系统错误"}
        with patch.object(fresh_ds, "_get", return_value=mock_resp):
            with caplog.at_level("WARNING"):
                result = fresh_ds.get_themes("2026-01-15")
        assert result == []
        assert any("同花顺热点错误" in r.message for r in caplog.records)

    def test_missing_data_key(self, fresh_ds):
        mock_resp = {"errocode": 0}
        with patch.object(fresh_ds, "_get", return_value=mock_resp):
            result = fresh_ds.get_themes("2026-01-15")
        assert result == []

    def test_get_returns_none(self, fresh_ds):
        with patch.object(fresh_ds, "_get", return_value=None):
            result = fresh_ds.get_themes("2026-01-15")
        assert result == []

    def test_ths_headers_passed(self, fresh_ds):
        mock_resp = {"errocode": 0, "data": []}
        with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
            fresh_ds.get_themes("2026-01-15")
        _, kwargs = mock_get.call_args
        assert kwargs["headers"] == _THS_HEADERS
        assert kwargs["source"] == "ths_hot_reason"


# ============================================================
# 12. fetch_board_stocks
# ============================================================


class TestFetchBoardStocks:
    def test_file_cache_hit(self, fresh_ds):
        cached = [{"code": "001", "name": "股票A", "industry": "半导体"}]
        with patch.object(fresh_ds, "_load_board_cache", return_value=cached):
            with patch.object(fresh_ds, "_get") as mock_get:
                result = fresh_ds.fetch_board_stocks("BK1036")

        assert result == cached
        mock_get.assert_not_called()

    def test_file_cache_miss_then_fetch(self, fresh_ds):
        mock_resp = {
            "data": {"diff": [
                {"f12": "001", "f14": "股票A", "f100": "半导体"},
                {"f12": "002", "f14": "股票B", "f100": "半导体"},
            ]}
        }
        with patch.object(fresh_ds, "_load_board_cache", return_value=[]):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache") as mock_save:
                    result = fresh_ds.fetch_board_stocks("BK1036")

        assert len(result) == 2
        assert result[0] == {"code": "001", "name": "股票A", "industry": "半导体"}
        mock_save.assert_called_once()

    def test_fetch_failure_returns_empty(self, fresh_ds):
        with patch.object(fresh_ds, "_load_board_cache", return_value=[]):
            with patch.object(fresh_ds, "_get", return_value=None):
                with patch.object(fresh_ds, "_save_board_cache") as mock_save:
                    result = fresh_ds.fetch_board_stocks("BK1036")

        assert result == []
        mock_save.assert_not_called()

    def test_empty_code_skipped(self, fresh_ds):
        """diff 中 f12 为空 → 跳过."""
        mock_resp = {
            "data": {"diff": [
                {"f12": "", "f14": "无代码", "f100": ""},
                {"f12": "001", "f14": "有代码", "f100": "半导体"},
            ]}
        }
        with patch.object(fresh_ds, "_load_board_cache", return_value=[]):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                result = fresh_ds.fetch_board_stocks("BK1036")

        assert len(result) == 1
        assert result[0]["code"] == "001"

    def test_empty_diff(self, fresh_ds):
        mock_resp = {"data": {"diff": []}}
        with patch.object(fresh_ds, "_load_board_cache", return_value=[]):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache") as mock_save:
                    result = fresh_ds.fetch_board_stocks("BK1036")

        assert result == []
        mock_save.assert_not_called()  # rows 为空不回写

    def test_limit_in_params(self, fresh_ds):
        mock_resp = {"data": {"diff": []}}
        with patch.object(fresh_ds, "_load_board_cache", return_value=[]):
            with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
                fresh_ds.fetch_board_stocks("BK1036", limit=200)

        args = mock_get.call_args[0]
        assert args[1]["pz"] == "200"
        assert args[1]["fs"] == "b:BK1036"


# ============================================================
# 13. _load_board_cache
# ============================================================


class TestLoadBoardCache:
    def test_file_not_exists(self, tmp_path):
        cache_file = str(tmp_path / "no_such_file.json")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", cache_file):
            ds = GraphDataSource()
            assert ds._load_board_cache("key", 86400) == []

    def test_valid_cache(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps({"key": [{"code": "001", "name": "test"}]}),
            encoding="utf-8",
        )
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_board_cache("key", 86400)
        assert result == [{"code": "001", "name": "test"}]

    def test_expired_cache(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"key": []}), encoding="utf-8")
        old_time = time.time() - 100000
        os.utime(cache_file, (old_time, old_time))
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_board_cache("key", 86400)
        assert result == []

    def test_malformed_json(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not json {{{", encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_board_cache("key", 86400)
        assert result == []

    def test_non_list_value(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"key": "not_a_list"}), encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_board_cache("key", 86400)
        assert result == []

    def test_key_not_in_cache(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"other_key": []}), encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_board_cache("key", 86400)
        assert result == []


# ============================================================
# 14. _save_board_cache
# ============================================================


class TestSaveBoardCache:
    def test_new_file(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            ds._save_board_cache("key", [{"code": "001"}])

        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data == {"key": [{"code": "001"}]}

    def test_appends_to_existing(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"existing": [{"code": "000"}]}), encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            ds._save_board_cache("new_key", [{"code": "001"}])

        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "existing" in data
        assert "new_key" in data
        assert data["new_key"] == [{"code": "001"}]

    def test_malformed_existing_overwritten(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not json {{{", encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            ds._save_board_cache("key", [{"code": "001"}])

        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data == {"key": [{"code": "001"}]}

    def test_creates_parent_dir(self, tmp_path):
        cache_file = tmp_path / "subdir" / "cache.json"
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            ds._save_board_cache("key", [{"code": "001"}])

        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["key"] == [{"code": "001"}]


# ============================================================
# 15. fetch_main_business
# ============================================================


class TestFetchMainBusiness:
    def test_file_cache_hit(self, fresh_ds):
        cached = {"code": "688041", "products": ["cached"], "industries": [], "review": ""}
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=cached):
            with patch.object(fresh_ds, "_get") as mock_get:
                result = fresh_ds.fetch_main_business("688041")

        assert result is cached
        mock_get.assert_not_called()

    def test_fetch_success_full_parse(self, fresh_ds):
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.5},
                {"MAINOP_TYPE": "2", "ITEM_NAME": "半导体", "MBI_RATIO": 0.3},
                {"MAINOP_TYPE": "1", "ITEM_NAME": "其他业务", "MBI_RATIO": 0.1},  # 过滤
            ],
            "jyps": [{"BUSINESS_REVIEW": "经营良好"}],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache") as mock_save:
                    result = fresh_ds.fetch_main_business("688041")

        assert result is not None
        assert result["code"] == "688041"
        assert result["products"] == ["芯片"]
        assert result["industries"] == ["半导体"]
        assert result["review"] == "经营良好"
        mock_save.assert_called_once()

    def test_fetch_none_returns_none(self, fresh_ds):
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=None):
                with patch.object(fresh_ds, "_save_board_cache") as mock_save:
                    result = fresh_ds.fetch_main_business("688041")

        assert result is None
        mock_save.assert_not_called()

    def test_ratio_sorting(self, fresh_ds):
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": "A", "MBI_RATIO": 0.1},
                {"MAINOP_TYPE": "1", "ITEM_NAME": "B", "MBI_RATIO": 0.5},
                {"MAINOP_TYPE": "1", "ITEM_NAME": "C", "MBI_RATIO": 0.3},
            ],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache"):
                    result = fresh_ds.fetch_main_business("688041")

        assert result["products"] == ["B", "C", "A"]  # 按占比降序

    def test_max_six_products(self, fresh_ds):
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": f"P{i}", "MBI_RATIO": float(i)}
                for i in range(10)
            ],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache"):
                    result = fresh_ds.fetch_main_business("688041")

        assert len(result["products"]) == 6

    def test_no_products_no_industries_returns_none(self, fresh_ds):
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": "其他", "MBI_RATIO": 0.5},  # 过滤
            ],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache"):
                    result = fresh_ds.fetch_main_business("688041")

        assert result is None

    def test_review_from_multiple_jyps(self, fresh_ds):
        """jyps 列表中取第一个非空 review."""
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.5},
            ],
            "jyps": [
                {"BUSINESS_REVIEW": ""},        # 空 → 跳过
                {"BUSINESS_REVIEW": "实际评述"},  # 取这个
                {"BUSINESS_REVIEW": "被忽略"},
            ],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache"):
                    result = fresh_ds.fetch_main_business("688041")

        assert result["review"] == "实际评述"

    def test_duplicate_item_max_ratio(self, fresh_ds):
        """同名 ITEM_NAME 多期出现 → 取最大 ratio."""
        mock_resp = {
            "zygcfx": [
                {"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.3},
                {"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.6},
                {"MAINOP_TYPE": "1", "ITEM_NAME": "封装", "MBI_RATIO": 0.2},
            ],
        }
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp):
                with patch.object(fresh_ds, "_save_board_cache"):
                    result = fresh_ds.fetch_main_business("688041")

        # 芯片 ratio=0.6 > 封装 ratio=0.2
        assert result["products"] == ["芯片", "封装"]

    def test_url_contains_f10_code(self, fresh_ds):
        mock_resp = {"zygcfx": [{"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.5}]}
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
                fresh_ds.fetch_main_business("688041")

        url_arg = mock_get.call_args[0][0]
        assert "SH688041" in url_arg  # 6开头 → SH

    def test_url_contains_sz_f10_code(self, fresh_ds):
        mock_resp = {"zygcfx": [{"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.5}]}
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
                fresh_ds.fetch_main_business("300308")

        url_arg = mock_get.call_args[0][0]
        assert "SZ300308" in url_arg  # 3开头 → SZ

    def test_timeout_is_15(self, fresh_ds):
        mock_resp = {"zygcfx": [{"MAINOP_TYPE": "1", "ITEM_NAME": "芯片", "MBI_RATIO": 0.5}]}
        with patch.object(fresh_ds, "_load_json_cache_value", return_value=None):
            with patch.object(fresh_ds, "_get", return_value=mock_resp) as mock_get:
                fresh_ds.fetch_main_business("688041")

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 15


# ============================================================
# 16. _load_json_cache_value
# ============================================================


class TestLoadJsonCacheValue:
    def test_file_not_exists(self, tmp_path):
        cache_file = str(tmp_path / "no_such_file.json")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", cache_file):
            ds = GraphDataSource()
            assert ds._load_json_cache_value("key", 86400) is None

    def test_valid_value(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cached = {"code": "001", "products": ["x"]}
        cache_file.write_text(json.dumps({"key": cached}), encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            result = ds._load_json_cache_value("key", 86400)
        assert result == cached

    def test_expired(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"key": {"a": 1}}), encoding="utf-8")
        old_time = time.time() - 100000
        os.utime(cache_file, (old_time, old_time))
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            assert ds._load_json_cache_value("key", 86400) is None

    def test_malformed_json(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not json {{{", encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            assert ds._load_json_cache_value("key", 86400) is None

    def test_key_not_in_cache(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"other": {"a": 1}}), encoding="utf-8")
        with patch.object(gds_module, "_BOARD_CACHE_FILE", str(cache_file)):
            ds = GraphDataSource()
            assert ds._load_json_cache_value("key", 86400) is None


# ============================================================
# 17. build_concept_edges
# ============================================================


class TestBuildConceptEdges:
    def test_no_concepts_no_edges(self, fresh_ds):
        with patch.object(fresh_ds, "get_concept_blocks", return_value=[]):
            edges = fresh_ds.build_concept_edges(["001", "002"])
        assert edges == []

    def test_shared_concepts_creates_edge(self, fresh_ds):
        def mock_concepts(code):
            return {"001": ["芯片", "半导体"], "002": ["芯片", "新能源"]}.get(code, [])

        with patch.object(fresh_ds, "get_concept_blocks", side_effect=mock_concepts):
            edges = fresh_ds.build_concept_edges(["001", "002"])

        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "001"
        assert e["target"] == "002"
        assert e["relation_type"] == "PARTNER"
        assert "芯片" in e["source_info"]
        # shared=1, strength = min(1.0, 1/5) = 0.2
        assert e["strength"] == 0.2

    def test_no_shared_no_edge(self, fresh_ds):
        def mock_concepts(code):
            return {"001": ["芯片"], "002": ["新能源"]}.get(code, [])

        with patch.object(fresh_ds, "get_concept_blocks", side_effect=mock_concepts):
            edges = fresh_ds.build_concept_edges(["001", "002"])
        assert edges == []

    def test_min_concept_share(self, fresh_ds):
        def mock_concepts(code):
            return {"001": ["芯片", "半导体"], "002": ["芯片", "新能源"]}.get(code, [])

        with patch.object(fresh_ds, "get_concept_blocks", side_effect=mock_concepts):
            # shared=1 < 2 → no edge
            edges = fresh_ds.build_concept_edges(["001", "002"], min_concept_share=2)
        assert edges == []

    def test_strength_capped_at_1(self, fresh_ds):
        """6个共享概念 → 6/5=1.2 → min(1.0, 1.2) = 1.0."""
        tags = [f"tag{i}" for i in range(6)]
        with patch.object(fresh_ds, "get_concept_blocks", return_value=tags):
            edges = fresh_ds.build_concept_edges(["001", "002"])
        assert len(edges) == 1
        assert edges[0]["strength"] == 1.0

    def test_three_symbols_pairs(self, fresh_ds):
        def mock_concepts(code):
            return {"001": ["A", "B"], "002": ["A"], "003": ["B"]}.get(code, [])

        with patch.object(fresh_ds, "get_concept_blocks", side_effect=mock_concepts):
            edges = fresh_ds.build_concept_edges(["001", "002", "003"])

        # 001-002 share A, 001-003 share B, 002-003 share nothing
        assert len(edges) == 2
        pairs = {(e["source"], e["target"]) for e in edges}
        assert ("001", "002") in pairs
        assert ("001", "003") in pairs


# ============================================================
# 18. build_industry_edges
# ============================================================


class TestBuildIndustryEdges:
    def test_same_industry_creates_edge(self, fresh_ds):
        def mock_industry(code):
            return {"001": {"industry": "半导体"}, "002": {"industry": "半导体"}}.get(code)

        with patch.object(fresh_ds, "get_industry_relationship", side_effect=mock_industry):
            edges = fresh_ds.build_industry_edges(["001", "002"])

        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "001"
        assert e["target"] == "002"
        assert e["relation_type"] == "COMPETITOR"
        assert e["strength"] == 0.7
        assert "半导体" in e["source_info"]

    def test_different_industry_no_edge(self, fresh_ds):
        def mock_industry(code):
            return {"001": {"industry": "半导体"}, "002": {"industry": "新能源"}}.get(code)

        with patch.object(fresh_ds, "get_industry_relationship", side_effect=mock_industry):
            edges = fresh_ds.build_industry_edges(["001", "002"])
        assert edges == []

    def test_no_industry_no_edge(self, fresh_ds):
        with patch.object(fresh_ds, "get_industry_relationship", return_value=None):
            edges = fresh_ds.build_industry_edges(["001", "002"])
        assert edges == []

    def test_empty_industry_no_edge(self, fresh_ds):
        with patch.object(fresh_ds, "get_industry_relationship",
                          return_value={"industry": ""}):
            edges = fresh_ds.build_industry_edges(["001", "002"])
        assert edges == []


# ============================================================
# 19. build_thematic_edges
# ============================================================


class TestBuildThematicEdges:
    def test_empty_themes(self, fresh_ds):
        with patch.object(fresh_ds, "get_themes", return_value=[]):
            edges = fresh_ds.build_thematic_edges()
        assert edges == []

    def test_single_stock_per_theme_no_edge(self, fresh_ds):
        themes = [{"code": "001", "reason": "芯片"}]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        assert edges == []

    def test_multiple_stocks_same_theme(self, fresh_ds):
        themes = [
            {"code": "001", "reason": "芯片"},
            {"code": "002", "reason": "芯片"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()

        assert len(edges) == 1
        e = edges[0]
        assert e["relation_type"] == "PARTNER"
        assert e["strength"] == 0.5
        assert "芯片" in e["source_info"]

    def test_dedup_pairs_across_themes(self, fresh_ds):
        """同一对股票在多个题材中出现 → 去重."""
        themes = [
            {"code": "001", "reason": "芯片"},
            {"code": "002", "reason": "芯片"},
            {"code": "001", "reason": "半导体"},
            {"code": "002", "reason": "半导体"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        # ("001","002") 只出现一次
        assert len(edges) == 1

    def test_reason_with_spaces(self, fresh_ds):
        """reason 含空格 → replace 为 + 再 split → 两个题材, 同一对去重后 1 条边."""
        themes = [
            {"code": "001", "reason": "芯片 半导体"},
            {"code": "002", "reason": "芯片 半导体"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        # "芯片 半导体" → "芯片+半导体" → 两个题材, 同一对 (001,002) 去重 → 1 条边
        assert len(edges) == 1

    def test_empty_reason_skipped(self, fresh_ds):
        themes = [
            {"code": "001", "reason": ""},
            {"code": "002", "reason": "芯片"},
            {"code": "003", "reason": "芯片"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        assert len(edges) == 1  # 002-003, 001 被跳过

    def test_empty_code_skipped(self, fresh_ds):
        themes = [
            {"code": "", "reason": "芯片"},
            {"code": "002", "reason": "芯片"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        assert edges == []  # 001 无 code, 只剩 002 一只 → 无边

    def test_three_stocks_one_theme(self, fresh_ds):
        """3只股票同一题材 → 3条边 (C(3,2)=3)."""
        themes = [
            {"code": "001", "reason": "芯片"},
            {"code": "002", "reason": "芯片"},
            {"code": "003", "reason": "芯片"},
        ]
        with patch.object(fresh_ds, "get_themes", return_value=themes):
            edges = fresh_ds.build_thematic_edges()
        assert len(edges) == 3


# ============================================================
# 20. build_main_business_edges
# ============================================================


class TestBuildMainBusinessEdges:
    def test_no_tags_no_edges(self, fresh_ds):
        with patch.object(fresh_ds, "fetch_main_business", return_value=None):
            edges = fresh_ds.build_main_business_edges(["001", "002"])
        assert edges == []

    def test_shared_tags_creates_edge(self, fresh_ds):
        def mock_mb(code):
            return {
                "001": {"products": ["芯片"], "industries": ["半导体"]},
                "002": {"products": ["芯片"], "industries": ["新能源"]},
            }.get(code)

        with patch.object(fresh_ds, "fetch_main_business", side_effect=mock_mb):
            edges = fresh_ds.build_main_business_edges(["001", "002"])

        assert len(edges) == 1
        e = edges[0]
        assert e["relation_type"] == "PARTNER"
        # shared=1 (芯片), strength = min(1.0, 0.4+0.15*1) = 0.55
        assert e["strength"] == 0.55
        assert "芯片" in e["source_info"]

    def test_min_shared_filter(self, fresh_ds):
        def mock_mb(code):
            return {
                "001": {"products": ["芯片"], "industries": []},
                "002": {"products": ["芯片"], "industries": []},
            }.get(code)

        with patch.object(fresh_ds, "fetch_main_business", side_effect=mock_mb):
            # shared=1 < 2 → no edge
            edges = fresh_ds.build_main_business_edges(["001", "002"], min_shared=2)
        assert edges == []

    def test_dedup_pairs(self, fresh_ds):
        """同一对只生成一条边."""
        def mock_mb(code):
            return {
                "001": {"products": ["A", "B", "C"], "industries": []},
                "002": {"products": ["A", "B", "C"], "industries": []},
            }.get(code)

        with patch.object(fresh_ds, "fetch_main_business", side_effect=mock_mb):
            edges = fresh_ds.build_main_business_edges(["001", "002"])
        assert len(edges) == 1  # 去重
        # shared=3, strength = min(1.0, 0.4+0.15*3) = 0.85
        assert edges[0]["strength"] == 0.85

    def test_strength_capped_at_1(self, fresh_ds):
        tags = [f"P{i}" for i in range(10)]  # 10 shared
        def mock_mb(code):
            return {"products": tags, "industries": []}

        with patch.object(fresh_ds, "fetch_main_business", side_effect=mock_mb):
            edges = fresh_ds.build_main_business_edges(["001", "002"])
        assert len(edges) == 1
        # 0.4 + 0.15*10 = 1.9 → min(1.0, 1.9) = 1.0
        assert edges[0]["strength"] == 1.0

    def test_empty_products_and_industries_skipped(self, fresh_ds):
        """mb 存在但 products/industries 为空 → tags 为空 → 跳过."""
        with patch.object(fresh_ds, "fetch_main_business",
                          return_value={"products": [], "industries": []}):
            edges = fresh_ds.build_main_business_edges(["001", "002"])
        assert edges == []

    def test_source_info_truncates_to_three(self, fresh_ds):
        """source_info 中 shared 只取前3个."""
        tags = [f"P{i}" for i in range(5)]
        def mock_mb(code):
            return {"products": tags, "industries": []}

        with patch.object(fresh_ds, "fetch_main_business", side_effect=mock_mb):
            edges = fresh_ds.build_main_business_edges(["001", "002"])
        # source_info: "main_business:P0,P1,P2"
        assert edges[0]["source_info"].count(",") == 2  # 3个tag, 2个逗号


# ============================================================
# 21. build_graph_edges
# ============================================================


class TestBuildGraphEdges:
    def test_all_included(self, fresh_ds):
        industry_edges = [{"source": "001", "target": "002", "relation_type": "COMPETITOR",
                            "strength": 0.7, "source_info": "industry:A"}]
        concept_edges = [{"source": "001", "target": "002", "relation_type": "PARTNER",
                          "strength": 0.2, "source_info": "concept:X"}]
        mb_edges = [{"source": "001", "target": "002", "relation_type": "PARTNER",
                     "strength": 0.55, "source_info": "main_business:Y"}]
        theme_edges = [{"source": "001", "target": "003", "relation_type": "PARTNER",
                        "strength": 0.5, "source_info": "theme:Z"}]

        with patch.object(fresh_ds, "build_industry_edges", return_value=industry_edges):
            with patch.object(fresh_ds, "build_concept_edges", return_value=concept_edges):
                with patch.object(fresh_ds, "build_main_business_edges", return_value=mb_edges):
                    with patch.object(fresh_ds, "build_thematic_edges", return_value=theme_edges):
                        edges = fresh_ds.build_graph_edges(["001", "002"])

        assert len(edges) == 4  # 全部合并

    def test_no_themes(self, fresh_ds):
        with patch.object(fresh_ds, "build_industry_edges", return_value=[]):
            with patch.object(fresh_ds, "build_concept_edges", return_value=[]):
                with patch.object(fresh_ds, "build_main_business_edges", return_value=[]):
                    with patch.object(fresh_ds, "build_thematic_edges") as mock_themes:
                        edges = fresh_ds.build_graph_edges(["001"], include_themes=False)

        assert edges == []
        mock_themes.assert_not_called()

    def test_no_main_business(self, fresh_ds):
        with patch.object(fresh_ds, "build_industry_edges", return_value=[]):
            with patch.object(fresh_ds, "build_concept_edges", return_value=[]):
                with patch.object(fresh_ds, "build_main_business_edges") as mock_mb:
                    with patch.object(fresh_ds, "build_thematic_edges", return_value=[]):
                        edges = fresh_ds.build_graph_edges(["001"], include_main_business=False)

        assert edges == []
        mock_mb.assert_not_called()

    def test_date_passed_to_thematic(self, fresh_ds):
        with patch.object(fresh_ds, "build_industry_edges", return_value=[]):
            with patch.object(fresh_ds, "build_concept_edges", return_value=[]):
                with patch.object(fresh_ds, "build_main_business_edges", return_value=[]):
                    with patch.object(fresh_ds, "build_thematic_edges", return_value=[]) as mock_t:
                        fresh_ds.build_graph_edges(["001"], date="2026-01-15")

        # date 作为位置参数传递给 build_thematic_edges
        args, _ = mock_t.call_args
        assert args == ("2026-01-15",)


# ============================================================
# 22. get_graph_data_source (单例)
# ============================================================


class TestGetGraphDataSource:
    def test_creates_singleton(self):
        original = gds_module._graph_data_source
        try:
            gds_module._graph_data_source = None
            ds = get_graph_data_source()
            assert ds is gds_module._graph_data_source
            assert isinstance(ds, GraphDataSource)
        finally:
            gds_module._graph_data_source = original

    def test_returns_existing(self):
        original = gds_module._graph_data_source
        try:
            mock_ds = MagicMock(name="existing_singleton")
            gds_module._graph_data_source = mock_ds
            ds = get_graph_data_source()
            assert ds is mock_ds
        finally:
            gds_module._graph_data_source = original
