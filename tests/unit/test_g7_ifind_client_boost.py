"""G7 覆盖率冲刺 — utils/ifind_client.py 补测试.

目标模块: utils/ifind_client.py (11.53% → 目标 60%+)

覆盖核心路径:
    - 纯函数: _parse_markdown_table / _col / _parse_ifind_response /
      _normalize_row / _extract_indicators_from_row
    - IFindClient: __init__ / _next_id / _headers / _rate_limit /
      call (含异常路径与配额超限)
    - 业务方法 (mock self.call): get_historical_klines / get_etf_quotes /
      get_etf_historical / get_index_historical / get_index_latest /
      get_edb_value / search_edb / get_bond_market / get_futures_realtime /
      search_news / get_fundamentals_batch / _parse_financials_content

约束:
    - 不发起任何真实网络请求
    - 用 unittest.mock.patch / MagicMock 隔离外部依赖
    - 测试文件可独立运行:
      python -m pytest tests/unit/test_g7_ifind_client_boost.py -q

运行:
    python -m pytest tests/unit/test_g7_ifind_client_boost.py -v
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 路径设置 + 环境变量 (必须在导入 ifind_client 前完成)
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ifind_client 模块加载时要求 IFIND_TOKEN 环境变量存在
os.environ.setdefault("IFIND_TOKEN", "test_token_for_unit_test_only")

from utils import ifind_client  # noqa: E402
from utils.ifind_client import (  # noqa: E402
    IFindClient,
    _col,
    _extract_indicators_from_row,
    _normalize_row,
    _parse_ifind_response,
    _parse_markdown_table,
)

# ============================================================
# 1. _parse_markdown_table 纯函数测试
# ============================================================


class TestParseMarkdownTable:
    """_parse_markdown_table 解析 markdown 表格为 dict 列表."""

    def test_empty_string_returns_empty_list(self):
        """空字符串返回空列表."""
        assert _parse_markdown_table("") == []

    def test_none_returns_empty_list(self):
        """None 返回空列表 (falsy)."""
        assert _parse_markdown_table(None) == []  # type: ignore[arg-type]

    def test_single_line_returns_empty_list(self):
        """单行文本 (无表头+数据) 返回空列表."""
        assert _parse_markdown_table("only one line") == []

    def test_standard_table_with_separator(self):
        """标准 markdown 表格 (带 |---| 分隔符)."""
        text = "| 日期 | 开盘价 | 收盘价 |\n|---|---|---|\n| 20260101 | 10.0 | 10.5 |\n| 20260102 | 10.5 | 10.3 |"
        rows = _parse_markdown_table(text)
        assert len(rows) == 2
        assert rows[0]["日期"] == "20260101"
        assert rows[0]["开盘价"] == "10.0"
        assert rows[1]["收盘价"] == "10.3"

    def test_table_without_separator(self):
        """无分隔符的表格 (header 后直接跟数据)."""
        text = "| 日期 | 收盘价 |\n| 20260101 | 10.5 |"
        rows = _parse_markdown_table(text)
        assert len(rows) == 1
        assert rows[0]["日期"] == "20260101"
        assert rows[0]["收盘价"] == "10.5"

    def test_table_with_leading_blank_lines(self):
        """前导空行不影响表头识别."""
        text = "\n\n| 日期 | 收盘价 |\n|---|---|\n| 20260101 | 10.5 |"
        rows = _parse_markdown_table(text)
        assert len(rows) == 1
        assert rows[0]["日期"] == "20260101"

    def test_hash_line_breaks_data_parsing(self):
        """数据区域遇到 # 开头行则停止解析."""
        text = "| 日期 | 收盘价 |\n|---|---|\n| 20260101 | 10.5 |\n# 注释\n| 20260102 | 10.3 |"
        rows = _parse_markdown_table(text)
        assert len(rows) == 1  # # 后的数据行不被解析
        assert rows[0]["日期"] == "20260101"

    def test_strips_parentheses_from_headers(self):
        """表头中的括号内容被去除 (如 "成交额（万元）" → "成交额")."""
        text = "| 成交额（万元） | 收盘价 |\n|---|---|\n| 100 | 10.5 |"
        rows = _parse_markdown_table(text)
        assert len(rows) == 1
        assert "成交额" in rows[0]
        assert "成交额（万元）" not in rows[0]
        assert rows[0]["成交额"] == "100"

    def test_empty_data_lines_skipped(self):
        """空行在数据区域被跳过."""
        text = "| 日期 | 收盘价 |\n|---|---|\n\n| 20260101 | 10.5 |\n"
        rows = _parse_markdown_table(text)
        assert len(rows) == 1
        assert rows[0]["日期"] == "20260101"

    def test_no_pipe_in_lines_returns_empty(self):
        """无 | 的文本返回空列表."""
        text = "这是普通文本\n第二行"
        rows = _parse_markdown_table(text)
        assert rows == []


# ============================================================
# 2. _col 纯函数测试
# ============================================================


class TestCol:
    """_col 从行 dict 中按关键词提取列值."""

    def test_exact_match(self):
        """精确匹配 key."""
        row = {"日期": "20260101", "收盘价": "10.5"}
        assert _col(row, "日期") == "20260101"
        assert _col(row, "收盘价") == "10.5"

    def test_paren_prefix_match(self):
        """key 以 'kw（' 开头时匹配 (如 '成交额（万元）')."""
        row = {"成交额（万元）": "100"}
        assert _col(row, "成交额") == "100"

    def test_prefix_match_when_exact_not_present(self):
        """精确匹配失败时使用前缀匹配."""
        row = {"收盘价最新": "10.5"}
        assert _col(row, "收盘价") == "10.5"

    def test_no_match_returns_none(self):
        """无匹配时返回 None."""
        row = {"日期": "20260101"}
        assert _col(row, "开盘价") is None

    def test_multiple_keywords_first_wins(self):
        """多个关键词时,第一个匹配的优先."""
        row = {"开盘价": "10.0", "open": "11.0"}
        # 第一轮精确匹配: "开盘价" 先匹配
        assert _col(row, "开盘价", "open") == "10.0"

    def test_multiple_keywords_fallback(self):
        """多关键词 fallback 到第二个."""
        row = {"open": "11.0"}
        assert _col(row, "开盘价", "open") == "11.0"

    def test_empty_row_returns_none(self):
        """空 dict 返回 None."""
        assert _col({}, "anything") is None


# ============================================================
# 3. _parse_ifind_response 纯函数测试
# ============================================================


class TestParseIfindResponse:
    """_parse_ifind_response 解析 iFind API 响应为统一结构."""

    def test_empty_content_returns_default(self):
        """content 为空时返回默认结构."""
        result = _parse_ifind_response({"data": {"result": {"content": []}}})
        assert result == {"text": "", "tables": [], "datas": []}

    def test_no_data_key_returns_default(self):
        """无 data 键时返回默认结构 (异常被捕获)."""
        result = _parse_ifind_response({})
        assert result == {"text": "", "tables": [], "datas": []}

    def test_none_result_returns_default(self):
        """result 为 None 时返回默认结构 (AttributeError 被捕获)."""
        result = _parse_ifind_response(None)  # type: ignore[arg-type]
        assert result == {"text": "", "tables": [], "datas": []}

    def test_data_is_none_returns_default(self):
        """data 为 None 时返回默认结构."""
        result = _parse_ifind_response({"data": None})
        assert result == {"text": "", "tables": [], "datas": []}

    def test_non_text_type_skipped(self):
        """非 text type 的 content 项被跳过."""
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "image", "text": "x"}]}}}
        )
        assert result["text"] == ""
        assert result["tables"] == []

    def test_json_decode_fail_skipped(self):
        """text 非 JSON 时跳过 (但不崩溃)."""
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": "not json"}]}}}
        )
        assert "not json" in result["text"]
        assert result["tables"] == []

    def test_code_not_one_skipped(self):
        """inner code != 1 时跳过."""
        inner = json.dumps({"code": 0, "data": {"answer": "| x |\n|---|\n| 1 |"}})
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert result["tables"] == []

    def test_datas_success_extracted(self):
        """datas 中 success=True 的 data 被提取."""
        inner = json.dumps(
            {
                "code": 1,
                "data": {"datas": [{"success": True, "data": {"key": "val"}}]},
            }
        )
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert len(result["datas"]) == 1
        assert result["datas"][0] == {"key": "val"}

    def test_datas_success_false_skipped(self):
        """datas 中 success=False 的项被跳过."""
        inner = json.dumps(
            {
                "code": 1,
                "data": {"datas": [{"success": False, "data": {"key": "val"}}]},
            }
        )
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert len(result["datas"]) == 0

    def test_answer_table_string_parsed(self):
        """answer1 含 markdown 表格字符串时被解析为 tables."""
        table_str = "| 日期 | 收盘价 |\n|---|---|\n| 20260101 | 10.5 |"
        inner = json.dumps({"code": 1, "data": {"answer1": table_str}})
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert len(result["tables"]) == 1
        assert result["tables"][0]["日期"] == "20260101"

    def test_answer_json_array_string_parsed(self):
        """answer 为 JSON 数组字符串时被解析为 tables."""
        arr_str = json.dumps([{"日期": "20260101", "收盘价": "10.5"}])
        inner = json.dumps({"code": 1, "data": {"answer": arr_str}})
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert len(result["tables"]) == 1
        assert result["tables"][0]["日期"] == "20260101"

    def test_text_list_value_parsed(self):
        """text 字段为 list 时被解析为 tables."""
        inner = json.dumps({"code": 1, "data": {"text": [{"a": "1"}, {"b": "2"}]}})
        result = _parse_ifind_response(
            {"data": {"result": {"content": [{"type": "text", "text": inner}]}}}
        )
        assert len(result["tables"]) == 2

    def test_text_accumulated(self):
        """多个 text 项的文本被累积到 text 字段."""
        result = _parse_ifind_response(
            {
                "data": {
                    "result": {
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "text", "text": "world"},
                        ]
                    }
                }
            }
        )
        assert "hello" in result["text"]
        assert "world" in result["text"]


# ============================================================
# 4. _normalize_row 纯函数测试
# ============================================================


class TestNormalizeRow:
    """_normalize_row 将行数据标准化为 {str: str}."""

    def test_basic_conversion(self):
        """键值转为字符串."""
        row = {"a": 1, "b": 2.5}
        out = _normalize_row(row)
        assert out == {"a": "1", "b": "2.5"}

    def test_none_value_to_empty_string(self):
        """None 值转为空字符串."""
        row = {"a": None, "b": "x"}
        out = _normalize_row(row)
        assert out["a"] == ""
        assert out["b"] == "x"

    def test_keys_converted_to_string(self):
        """非字符串键被转为字符串."""
        row = {1: "a", 2: "b"}
        out = _normalize_row(row)
        assert out == {"1": "a", "2": "b"}

    def test_empty_row(self):
        """空 dict 返回空 dict."""
        assert _normalize_row({}) == {}


# ============================================================
# 5. _extract_indicators_from_row 纯函数测试
# ============================================================


class TestExtractIndicatorsFromRow:
    """_extract_indicators_from_row 从行提取数值型指标."""

    def test_none_value_skipped(self):
        """None 值被跳过."""
        out = _extract_indicators_from_row({"a": None, "b": 1.0})
        assert "a" not in out
        assert out["b"] == 1.0

    def test_int_value(self):
        """int 类型被提取."""
        out = _extract_indicators_from_row({"x": 42})
        assert out["x"] == 42.0
        assert isinstance(out["x"], float)

    def test_float_value(self):
        """float 类型被提取."""
        out = _extract_indicators_from_row({"x": 3.14})
        assert out["x"] == 3.14

    def test_string_numeric(self):
        """字符串数值被提取."""
        out = _extract_indicators_from_row({"x": "12.34"})
        assert out["x"] == 12.34

    def test_special_values_skipped(self):
        """特殊值 (--, N/A, NA, null, None, -) 被跳过."""
        for val in ("--", "-", "N/A", "NA", "null", "None", ""):
            out = _extract_indicators_from_row({"x": val})
            assert "x" not in out, f"应跳过 {val!r}"

    def test_yi_unit_conversion(self):
        """'亿' 单位转换为乘以 1e8."""
        out = _extract_indicators_from_row({"x": "1.5亿"})
        assert out["x"] == 1.5e8

    def test_wan_unit_conversion(self):
        """'万' 单位转换为乘以 1e4."""
        out = _extract_indicators_from_row({"x": "100万"})
        assert out["x"] == 100e4

    def test_thousand_separator_removed(self):
        """千分位逗号被去除."""
        out = _extract_indicators_from_row({"x": "1,234.56"})
        assert out["x"] == 1234.56

    def test_percent_sign_removed(self):
        """百分号被去除."""
        out = _extract_indicators_from_row({"x": "12.5%"})
        assert out["x"] == 12.5

    def test_unparseable_string_skipped(self):
        """不可解析的字符串被跳过."""
        out = _extract_indicators_from_row({"x": "abc", "y": "10.0"})
        assert "x" not in out
        assert out["y"] == 10.0

    def test_nan_and_inf_skipped(self):
        """NaN 和 Inf 被跳过."""
        out = _extract_indicators_from_row(
            {
                "nan_val": float("nan"),
                "inf_val": float("inf"),
                "ok": 1.0,
            }
        )
        assert "nan_val" not in out
        assert "inf_val" not in out
        assert out["ok"] == 1.0

    def test_empty_row(self):
        """空 dict 返回空 dict."""
        assert _extract_indicators_from_row({}) == {}


# ============================================================
# 6. IFindClient 初始化与基础方法测试
# ============================================================


class TestIFindClientInit:
    """IFindClient 初始化与基础方法."""

    def test_init_default(self):
        """默认初始化: max_concurrency=2, 计数器归零."""
        client = IFindClient()
        assert client.max_concurrency == 2
        assert client.call_count == 0
        assert client.error_count == 0
        assert client.last_success == 0
        assert client._sessions == {}
        assert client._req_ids == {}
        assert client._quota_exceeded == {}
        assert client._quota_retry_delay == 3600

    def test_init_custom_concurrency(self):
        """自定义 max_concurrency."""
        client = IFindClient(max_concurrency=5)
        assert client.max_concurrency == 5

    def test_next_id_increments(self):
        """_next_id 递增."""
        client = IFindClient()
        assert client._next_id("stock") == 1
        assert client._next_id("stock") == 2
        assert client._next_id("stock") == 3

    def test_next_id_different_types_independent(self):
        """不同 server_type 的 ID 计数独立."""
        client = IFindClient()
        assert client._next_id("stock") == 1
        assert client._next_id("fund") == 1
        assert client._next_id("stock") == 2
        assert client._next_id("fund") == 2

    def test_headers_no_session(self):
        """无 session 时不含 Mcp-Session-Id."""
        client = IFindClient()
        h = client._headers()
        assert h["Content-Type"] == "application/json"
        assert "Authorization" in h
        assert "Mcp-Session-Id" not in h

    def test_headers_no_session_with_t(self):
        """有 t 但未建立 session 时不含 Mcp-Session-Id."""
        client = IFindClient()
        h = client._headers("stock")
        assert "Mcp-Session-Id" not in h

    def test_headers_with_session(self):
        """有 t 且已建立 session 时含 Mcp-Session-Id."""
        client = IFindClient()
        client._sessions["stock"] = "sess-123"
        h = client._headers("stock")
        assert h["Mcp-Session-Id"] == "sess-123"

    def test_rate_limit_first_call_no_sleep(self):
        """首次调用不 sleep (last=0, gap 远大于 0.5)."""
        client = IFindClient()
        with patch("utils.ifind_client.time.sleep") as mock_sleep:
            client._rate_limit("stock")
        mock_sleep.assert_not_called()

    def test_rate_limit_sleeps_when_gap_small(self):
        """间隔 < 0.5s 时 sleep (0.5 - gap)."""
        client = IFindClient()
        client._last_request_time["stock"] = 100.0
        with (
            patch("utils.ifind_client.time.time", return_value=100.2),
            patch("utils.ifind_client.time.sleep") as mock_sleep,
        ):
            client._rate_limit("stock")
        mock_sleep.assert_called_once_with(pytest.approx(0.3, abs=0.01))

    def test_rate_limit_no_sleep_when_gap_large(self):
        """间隔 >= 0.5s 时不 sleep."""
        client = IFindClient()
        client._last_request_time["stock"] = 100.0
        with (
            patch("utils.ifind_client.time.time", return_value=100.6),
            patch("utils.ifind_client.time.sleep") as mock_sleep,
        ):
            client._rate_limit("stock")
        mock_sleep.assert_not_called()


# ============================================================
# 7. IFindClient.call 方法测试 (含异常路径)
# ============================================================


class TestIFindClientCall:
    """IFindClient.call 调用流程与异常处理."""

    def test_call_unknown_server_type(self):
        """未知 server_type 返回 error, 不发起网络请求."""
        client = IFindClient()
        result = client.call("unknown", "tool", {})
        assert result["ok"] is False
        assert "unknown server_type" in result["error"]

    def test_call_quota_exceeded_not_expired(self):
        """配额超限且未过期时返回 quota_exceeded."""
        client = IFindClient()
        client._sessions["stock"] = "sess"  # 跳过 _init
        client._quota_exceeded["stock"] = 1000.0
        with patch("utils.ifind_client.time.time", return_value=1500.0):
            result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert result.get("quota_exceeded") is True

    def test_call_quota_expired_clears_and_continues(self):
        """配额超限已过期时清除标记并继续调用."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        client._quota_exceeded["stock"] = 1000.0
        # now - 1000 > 3600 → 过期
        mock_resp = MagicMock()
        mock_resp.text = '{"result": {"content": []}}'
        mock_resp.json.return_value = {"result": {"content": []}}
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        with (
            patch("utils.ifind_client.time.time", return_value=5000.0),
            patch.object(ifind_client, "_IFIND_SESSION") as mock_session,
        ):
            mock_session.post.return_value = mock_resp
            result = client.call("stock", "tool", {})
        assert "stock" not in client._quota_exceeded
        assert result["ok"] is True

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_network_error(self, mock_session):
        """网络异常 (RequestException) 时返回 error, error_count++."""
        import requests

        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_session.post.side_effect = requests.RequestException("conn refused")
        result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert "conn refused" in result["error"]
        assert client.error_count == 1

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_response_with_error_field(self, mock_session):
        """响应 data 含 error 字段时返回 error."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = '{"error": "some error"}'
        mock_resp.json.return_value = {"error": "some error"}
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert result["error"] == "some error"
        assert client.error_count == 1

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_http_error(self, mock_session):
        """HTTP 错误状态码时返回 error + status_code."""
        import requests

        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = '{"result": {}}'
        mock_resp.json.return_value = {"result": {}}
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert result["status_code"] == 500
        assert client.error_count == 1

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_quota_in_response_text(self, mock_session):
        """响应文本含 '超限' 时设置配额超限标记."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        json.dumps({"code": 1, "data": {"answer": "用户使用工具已超限"}})
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {"result": {"content": [{"type": "text", "text": "超限提示"}]}}
        )
        mock_resp.json.return_value = {
            "result": {"content": [{"type": "text", "text": "超限提示"}]}
        }
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert result.get("quota_exceeded") is True
        assert "stock" in client._quota_exceeded

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_quota_in_response_english(self, mock_session):
        """响应文本含 'quota' (英文) 时设置配额超限."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = '{"result": {"content": [{"text": "quota exceeded"}]}}'
        mock_resp.json.return_value = {
            "result": {"content": [{"text": "quota exceeded"}]}
        }
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is False
        assert result.get("quota_exceeded") is True

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_success(self, mock_session):
        """成功调用: 返回 ok=True, call_count++."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = '{"result": {"content": []}}'
        mock_resp.json.return_value = {"result": {"content": []}}
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "get_stock_performance", {"query": "test"})
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert client.call_count == 1
        assert client.error_count == 0
        assert client.last_success > 0

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_empty_response_text(self, mock_session):
        """空响应文本时 data 为 None, 仍返回 ok."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is True

    @patch.object(ifind_client, "_IFIND_SESSION")
    def test_call_json_decode_fail_falls_back_to_text(self, mock_session):
        """JSON 解析失败时 data 回退为原始文本."""
        client = IFindClient()
        client._sessions["stock"] = "sess"
        mock_resp = MagicMock()
        mock_resp.text = "not json at all"
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_session.post.return_value = mock_resp
        result = client.call("stock", "tool", {})
        assert result["ok"] is True
        assert result["data"] == "not json at all"


# ============================================================
# 8. get_historical_klines 路由测试
# ============================================================


class TestGetHistoricalKlines:
    """get_historical_klines 路由逻辑测试."""

    def test_routes_to_fund_when_code_starts_with_5(self):
        """5 开头代码路由到 _get_fund_historical."""
        client = IFindClient()
        with (
            patch.object(client, "_get_fund_historical", return_value=[]) as mock_fund,
            patch.object(
                client, "_get_stock_historical", return_value=[]
            ) as mock_stock,
        ):
            client.get_historical_klines("510300", days=60)
        mock_fund.assert_called_once_with("510300", 60)
        mock_stock.assert_not_called()

    def test_routes_to_stock_when_code_not_starts_with_5(self):
        """非 5 开头代码路由到 _get_stock_historical."""
        client = IFindClient()
        with (
            patch.object(client, "_get_fund_historical", return_value=[]) as mock_fund,
            patch.object(
                client, "_get_stock_historical", return_value=[]
            ) as mock_stock,
        ):
            client.get_historical_klines("000001", days=60)
        mock_stock.assert_called_once_with("000001", 60)
        mock_fund.assert_not_called()


# ============================================================
# 9. _get_stock_historical 测试
# ============================================================


class TestGetStockHistorical:
    """_get_stock_historical 股票历史 K 线拉取."""

    @patch("utils.ifind_client.time.sleep")
    def test_success_parses_klines(self, mock_sleep):
        """成功解析 K 线数据 (含日期去重与排序)."""
        client = IFindClient()
        table_str = "| 日期 | 开盘价 | 收盘价 | 最高价 | 最低价 | 成交量 |\n|---|---|---|---|---|---|\n| 20260102 | 10.5 | 10.3 | 10.6 | 10.1 | 80000 |\n| 20260101 | 10.0 | 10.5 | 10.8 | 9.8 | 100000 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client._get_stock_historical("000001", days=60)
        assert rows is not None
        assert len(rows) == 2
        # 验证按日期升序排序
        assert rows[0]["日期"] == "20260101"
        assert rows[1]["日期"] == "20260102"
        assert rows[0]["开盘价"] == 10.0
        assert rows[1]["收盘价"] == 10.3

    @patch("utils.ifind_client.time.sleep")
    def test_empty_response_returns_none(self, mock_sleep):
        """空响应返回 None."""
        client = IFindClient()
        call_result = {"ok": True, "data": {"result": {"content": []}}}
        with patch.object(client, "call", return_value=call_result):
            rows = client._get_stock_historical("000001", days=60)
        assert rows is None

    @patch("utils.ifind_client.time.sleep")
    def test_invalid_numeric_values_skipped(self, mock_sleep):
        """无效数值行被跳过 (ValueError 被捕获)."""
        client = IFindClient()
        table_str = (
            "| 日期 | 开盘价 | 收盘价 |\n|---|---|---|\n| 20260101 | abc | 10.5 |"
        )
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client._get_stock_historical("000001", days=60)
        # "abc" 无法转 float, 整行被跳过
        assert rows is None


# ============================================================
# 10. _get_fund_historical 测试
# ============================================================


class TestGetFundHistorical:
    """_get_fund_historical 基金历史数据转换."""

    def test_success_converts_etf_to_klines(self):
        """成功将 ETF 数据转换为 K 线格式 (nav 作为 OHLC)."""
        client = IFindClient()
        etf_data = [
            {"date": "20260101", "nav": 1.5, "change_pct": 0.5, "cumulative_nav": 1.6},
            {
                "date": "20260102",
                "nav": 1.52,
                "change_pct": 1.33,
                "cumulative_nav": 1.62,
            },
        ]
        with patch.object(client, "get_etf_historical", return_value=etf_data):
            rows = client._get_fund_historical("510300", days=60)
        assert rows is not None
        assert len(rows) == 2
        assert rows[0]["日期"] == "20260101"
        assert rows[0]["开盘价"] == 1.5
        assert rows[0]["收盘价"] == 1.5
        assert rows[0]["最高价"] == 1.5
        assert rows[0]["最低价"] == 1.5
        assert rows[0]["成交量"] == 0

    def test_empty_etf_returns_none(self):
        """ETF 数据为空时返回 None."""
        client = IFindClient()
        with patch.object(client, "get_etf_historical", return_value=None):
            rows = client._get_fund_historical("510300", days=60)
        assert rows is None


# ============================================================
# 11. get_etf_quotes 测试
# ============================================================


class TestGetEtfQuotes:
    """get_etf_quotes ETF 实时净值查询."""

    def test_success_parses_quotes(self):
        """成功解析 ETF 净值与涨跌幅."""
        client = IFindClient()
        table_str = "| 证券代码 | 单位净值 | 涨跌幅 | 日期 |\n|---|---|---|---|\n| 510300.SH | 1.5 | 0.5 | 20260101 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            quotes = client.get_etf_quotes(["510300"])
        assert "510300" in quotes
        assert quotes["510300"]["price"] == 1.5
        assert quotes["510300"]["change_pct"] == 0.5
        assert quotes["510300"]["date"] == "20260101"

    def test_empty_response_returns_empty_dict(self):
        """空响应返回空 dict."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            quotes = client.get_etf_quotes(["510300"])
        assert quotes == {}

    def test_invalid_numeric_skipped(self):
        """数值解析失败的行被跳过."""
        client = IFindClient()
        table_str = (
            "| 证券代码 | 单位净值 | 涨跌幅 |\n|---|---|---|\n| 510300.SH | abc | 0.5 |"
        )
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            quotes = client.get_etf_quotes(["510300"])
        assert "510300" not in quotes


# ============================================================
# 12. get_etf_historical 测试
# ============================================================


class TestGetEtfHistorical:
    """get_etf_historical ETF 历史净值查询."""

    def test_success_parses_historical(self):
        """成功解析历史净值数据."""
        client = IFindClient()
        table_str = "| 日期 | 单位净值 | 涨跌幅 | 累计单位净值 |\n|---|---|---|---|\n| 20260101 | 1.5 | 0.5 | 1.6 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client.get_etf_historical("510300", days=60)
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["date"] == "20260101"
        assert rows[0]["nav"] == 1.5
        assert rows[0]["change_pct"] == 0.5
        assert rows[0]["cumulative_nav"] == 1.6

    def test_empty_response_returns_none(self):
        """空响应返回 None."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            rows = client.get_etf_historical("510300", days=60)
        assert rows is None

    def test_invalid_date_skipped(self):
        """日期格式不合法 (不以数字开头) 的行被跳过."""
        client = IFindClient()
        table_str = "| 日期 | 单位净值 |\n|---|---|\n| 日期 | 1.5 |\n| 20260101 | 1.5 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client.get_etf_historical("510300", days=60)
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["date"] == "20260101"


# ============================================================
# 13. get_index_historical 测试
# ============================================================


class TestGetIndexHistorical:
    """get_index_historical 指数历史数据查询."""

    @patch("utils.ifind_client.time.sleep")
    def test_success_parses_index_data(self, mock_sleep):
        """成功解析指数收盘价与成交额."""
        client = IFindClient()
        table_str = "| 日期 | 收盘价 | 成交额 |\n|---|---|---|\n| 20260102 | 3000 | 5亿 |\n| 20260101 | 2990 | 4.5亿 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client.get_index_historical("沪深300", days=60)
        assert rows is not None
        assert len(rows) == 2
        # 按日期升序排序
        assert rows[0]["date"] == "20260101"
        assert rows[0]["close"] == 2990.0
        # 成交额 "4.5亿" → 4.5e8
        assert rows[0]["amount"] == 4.5e8

    @patch("utils.ifind_client.time.sleep")
    def test_empty_response_returns_none(self, mock_sleep):
        """空响应返回 None."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            rows = client.get_index_historical("沪深300", days=60)
        assert rows is None


# ============================================================
# 14. get_index_latest 测试
# ============================================================


class TestGetIndexLatest:
    """get_index_latest 指数最新行情."""

    def test_success_returns_latest(self):
        """成功返回最新收盘价与涨跌幅."""
        client = IFindClient()
        table_str = "| 收盘价 | 涨跌幅 |\n|---|---|\n| 3000 | 0.5 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            result = client.get_index_latest("沪深300")
        assert result is not None
        assert result["close"] == 3000.0
        assert result["change_pct"] == 0.5
        assert "date" in result

    def test_empty_response_returns_none(self):
        """空响应返回 None."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            result = client.get_index_latest("沪深300")
        assert result is None

    def test_invalid_numeric_returns_none(self):
        """数值解析失败时返回 None."""
        client = IFindClient()
        table_str = "| 收盘价 |\n|---|---|\n| abc |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            result = client.get_index_latest("沪深300")
        assert result is None


# ============================================================
# 15. get_edb_value / search_edb / get_bond_market 测试
# ============================================================


class TestEdbAndBondMethods:
    """get_edb_value / search_edb / get_bond_market 测试."""

    def test_get_edb_value_success(self):
        """成功返回 float 值."""
        client = IFindClient()
        table_str = "| GDP |\n|---|\n| 100.5 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            val = client.get_edb_value("GDP")
        assert val == 100.5

    def test_get_edb_value_empty_returns_none(self):
        """空响应返回 None."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            val = client.get_edb_value("GDP")
        assert val is None

    def test_get_edb_value_unparseable_returns_none(self):
        """不可解析的值返回 None."""
        client = IFindClient()
        table_str = "| GDP |\n|---|\n| abc |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            val = client.get_edb_value("GDP")
        assert val is None

    def test_search_edb_passthrough(self):
        """search_edb 透传 call."""
        client = IFindClient()
        expected = {"ok": True, "data": "x"}
        with patch.object(client, "call", return_value=expected) as mock_call:
            result = client.search_edb("GDP")
        assert result == expected
        mock_call.assert_called_once_with("edb", "search_edb", {"query": "GDP"})

    def test_get_bond_market_passthrough(self):
        """get_bond_market 透传 call."""
        client = IFindClient()
        expected = {"ok": True, "data": "x"}
        with patch.object(client, "call", return_value=expected) as mock_call:
            result = client.get_bond_market("国债收益率")
        assert result == expected
        mock_call.assert_called_once_with(
            "bond", "bond_market_data", {"query": "国债收益率"}
        )


# ============================================================
# 16. get_futures_realtime 测试
# ============================================================


class TestGetFuturesRealtime:
    """get_futures_realtime 期货实时行情查询."""

    def test_empty_codes_returns_none(self):
        """空 codes 列表返回 None."""
        client = IFindClient()
        assert client.get_futures_realtime([]) is None

    def test_success_parses_futures(self):
        """成功解析期货行情数据."""
        client = IFindClient()
        table_str = "| tradeDate | open | high | low | latest | volume |\n|---|---|---|---|---|---|\n| 20260101 | 3500 | 3550 | 3480 | 3520 | 100000 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client.get_futures_realtime(["IF2401"])
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["tradeDate"] == "20260101"
        assert rows[0]["open"] == 3500.0
        assert rows[0]["high"] == 3550.0
        assert rows[0]["latest"] == 3520.0
        assert rows[0]["volume"] == 100000.0

    def test_empty_response_returns_none(self):
        """空响应返回 None."""
        client = IFindClient()
        with patch.object(
            client,
            "call",
            return_value={"ok": True, "data": {"result": {"content": []}}},
        ):
            rows = client.get_futures_realtime(["IF2401"])
        assert rows is None

    def test_datas_fallback_when_no_tables(self):
        """tables 为空但 datas 有内容时使用 datas 兜底."""
        client = IFindClient()
        inner = json.dumps(
            {
                "code": 1,
                "data": {
                    "datas": [
                        {
                            "success": True,
                            "data": {"tradeDate": "20260101", "open": "3500"},
                        }
                    ]
                },
            }
        )
        call_result = {
            "ok": True,
            "data": {"result": {"content": [{"type": "text", "text": inner}]}},
        }
        with patch.object(client, "call", return_value=call_result):
            rows = client.get_futures_realtime(["IF2401"])
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["tradeDate"] == "20260101"
        assert rows[0]["open"] == 3500.0


# ============================================================
# 17. search_news 测试
# ============================================================


class TestSearchNews:
    """search_news 新闻搜索参数构造."""

    def test_with_time_range(self):
        """带时间范围的查询."""
        client = IFindClient()
        expected = {"ok": True}
        with patch.object(client, "call", return_value=expected) as mock_call:
            result = client.search_news(
                "科技", time_start="20260101", time_end="20260131", size=10
            )
        assert result == expected
        mock_call.assert_called_once_with(
            "news",
            "search_news",
            {
                "query": "科技",
                "size": 10,
                "time_start": "20260101",
                "time_end": "20260131",
            },
        )

    def test_without_time_range(self):
        """不带时间范围的查询."""
        client = IFindClient()
        expected = {"ok": True}
        with patch.object(client, "call", return_value=expected) as mock_call:
            result = client.search_news("科技")
        assert result == expected
        mock_call.assert_called_once_with(
            "news",
            "search_news",
            {
                "query": "科技",
                "size": 5,
            },
        )


# ============================================================
# 18. get_fundamentals_batch 测试
# ============================================================


class TestGetFundamentalsBatch:
    """get_fundamentals_batch 批量财务指标拉取."""

    def test_empty_symbols_returns_empty(self):
        """空 symbols 列表返回空 dict."""
        client = IFindClient()
        assert client.get_fundamentals_batch([]) == {}

    def test_success_parses_financials(self):
        """成功解析 PE/PB 指标 (每列为一个指标名)."""
        client = IFindClient()
        table_str = "| 市盈率PE | 市净率PB |\n|---|---|\n| 15.5 | 2.3 |"
        call_result = {
            "ok": True,
            "data": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"code": 1, "data": {"answer": table_str}}
                            ),
                        }
                    ]
                }
            },
        }
        with patch.object(client, "call", return_value=call_result):
            result = client.get_fundamentals_batch(["600519.SH"], max_workers=1)
        assert "600519.SH" in result
        assert result["600519.SH"]["pe"] == 15.5
        assert result["600519.SH"]["pb"] == 2.3

    def test_call_failure_skipped(self):
        """call 返回失败时该 symbol 被跳过."""
        client = IFindClient()
        call_result = {"ok": False, "error": "some error"}
        with patch.object(client, "call", return_value=call_result):
            result = client.get_fundamentals_batch(["600519.SH"], max_workers=1)
        assert "600519.SH" not in result

    def test_quota_exceeded_terminates_batch(self):
        """配额超限时终止批量拉取, 返回空 dict."""
        client = IFindClient()
        call_result = {"ok": False, "error": "quota", "quota_exceeded": True}
        with patch.object(client, "call", return_value=call_result):
            result = client.get_fundamentals_batch(
                ["600519.SH", "000333.SZ"], max_workers=1
            )
        assert result == {}


# ============================================================
# 19. _parse_financials_content 测试
# ============================================================


class TestParseFinancialsContent:
    """_parse_financials_content 解析财务指标内容."""

    def test_empty_content_returns_empty(self):
        """空 content 返回空 dict."""
        client = IFindClient()
        assert client._parse_financials_content([], "600519.SH") == {}

    def test_json_with_markdown_table(self):
        """JSON (code=1) 含 answer markdown 表格时被解析 (每列为一个指标)."""
        client = IFindClient()
        table_str = "| 市盈率PE | 市净率PB |\n|---|---|\n| 15.5 | 2.3 |"
        content = [
            {
                "type": "text",
                "text": json.dumps({"code": 1, "data": {"answer": table_str}}),
            }
        ]
        result = client._parse_financials_content(content, "600519.SH")
        assert result["pe"] == 15.5
        assert result["pb"] == 2.3

    def test_plain_markdown_text(self):
        """非 JSON 文本含 markdown 表格时被解析 (每列为一个指标)."""
        client = IFindClient()
        text = "| 市盈率PE |\n|---|\n| 20.0 |"
        content = [{"type": "text", "text": text}]
        result = client._parse_financials_content(content, "600519.SH")
        assert result["pe"] == 20.0

    def test_key_normalization_all_indicators(self):
        """键名归一化: PE/PB/ROE/市值/流通市值/营收/净利润."""
        client = IFindClient()
        table_str = (
            "| 市盈率PE | 市净率PB | 净资产收益率ROE | 总市值 | 流通市值 | 营收 | 净利润 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 15.5 | 2.3 | 18.0 | 2000亿 | 1500亿 | 500亿 | 100亿 |"
        )
        content = [
            {
                "type": "text",
                "text": json.dumps({"code": 1, "data": {"answer": table_str}}),
            }
        ]
        result = client._parse_financials_content(content, "600519.SH")
        assert result["pe"] == 15.5
        assert result["pb"] == 2.3
        assert result["roe"] == 18.0
        assert result["market_cap"] == 2000e8
        assert result["float_market_cap"] == 1500e8
        assert result["revenue"] == 500e8
        assert result["net_profit"] == 100e8

    def test_data_payload_extracted(self):
        """JSON (code=1) 的 data 字段本身也提取指标."""
        client = IFindClient()
        content = [
            {
                "type": "text",
                "text": json.dumps(
                    {"code": 1, "data": {"市盈率PE": 12.0, "answer": ""}}
                ),
            }
        ]
        result = client._parse_financials_content(content, "600519.SH")
        assert result["pe"] == 12.0

    def test_empty_text_skipped(self):
        """空 text 项被跳过."""
        client = IFindClient()
        content = [{"type": "text", "text": ""}]
        result = client._parse_financials_content(content, "600519.SH")
        assert result == {}

    def test_special_values_skipped(self):
        """特殊值 (--, N/A) 被跳过."""
        client = IFindClient()
        table_str = "| 市盈率PE | 市净率PB |\n|---|---|\n| -- | 2.3 |"
        content = [
            {
                "type": "text",
                "text": json.dumps({"code": 1, "data": {"answer": table_str}}),
            }
        ]
        result = client._parse_financials_content(content, "600519.SH")
        assert "pe" not in result
        assert result["pb"] == 2.3

    def test_json_code_not_one_falls_back_to_markdown(self):
        """JSON code != 1 时回退到直接解析 text 为 markdown."""
        client = IFindClient()
        text = "| 市盈率PE |\n|---|\n| 25.0 |"
        content = [{"type": "text", "text": text}]
        result = client._parse_financials_content(content, "600519.SH")
        assert result["pe"] == 25.0
