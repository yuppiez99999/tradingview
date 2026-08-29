"""G7 覆盖率冲刺 — utils/etf_flow_monitor.py 补测试.

目标模块: utils/etf_flow_monitor.py (21.07% → 目标 60%+)

覆盖核心路径:
    - 模块常量: SIGNAL_THRESHOLDS / NATIONAL_TEAM_ETFS / ETF_TO_STOCKS
    - ETFRealTimeTracker:
        * _init_data_sources (异常路径)
        * _to_wind_code (委托)
        * _fetch_wind_fund_flow (None/正常/异常)
        * _fetch_ifind_fund_flow (None/缺失/正常/异常)
        * _fetch_eastmoney_fund_flow (正常/空klines/异常)
        * _fetch_price_based_flow (正常/空/异常)
        * _fetch_sina_fund_flow (前缀/状态码/解析/正常/异常)
        * get_etf_fund_flow (多源回退/_eastmoney_blocked 跳过)
        * get_all_etf_fund_flows (默认填充)
        * detect_signals (高/中/低 + 流入/流出 + 排序)
        * get_signal_summary (净流入/净流出/平衡)
        * update_positions_json (加载失败/成功/保存失败/关联ETF)
    - refresh_etf_flow_signals (默认路径/自定义/失败/成功)
    - get_etf_flow_summary

约束:
    - 不发起任何真实网络请求
    - 用 unittest.mock.patch / MagicMock 隔离外部依赖
    - 测试文件可独立运行:
      python -m pytest tests/unit/test_g7_etf_flow_monitor_boost.py -q

运行:
    python -m pytest tests/unit/test_g7_etf_flow_monitor_boost.py -v
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 路径设置 (必须在导入被测模块前完成)
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 屏蔽 IFIND_TOKEN, 保证 _init_data_sources 的 ifind 分支可预测跳过
os.environ.pop("IFIND_TOKEN", None)

from utils import etf_flow_monitor  # noqa: E402
from utils.etf_flow_monitor import (  # noqa: E402
    ETF_TO_STOCKS,
    NATIONAL_TEAM_ETFS,
    SIGNAL_THRESHOLDS,
    ETFRealTimeTracker,
    get_etf_flow_summary,
    refresh_etf_flow_signals,
)

# ============================================================
# 公共 fixture: 重置全局封禁状态, 避免测试间污染
# ============================================================


@pytest.fixture(autouse=True)
def _reset_eastmoney_blocked():
    """每个测试前后重置 _eastmoney_blocked 全局标志."""
    etf_flow_monitor._eastmoney_blocked = False
    yield
    etf_flow_monitor._eastmoney_blocked = False


@pytest.fixture
def tracker_no_sources():
    """构造一个所有数据源都被禁用的 ETFRealTimeTracker.

    通过 patch _init_data_sources 为 no-op, 避免 __init__ 触发真实文件/导入.
    """
    with patch.object(ETFRealTimeTracker, "_init_data_sources", lambda self: None):
        t = ETFRealTimeTracker()
    # 显式置空, 防止外部环境变量意外命中
    t.wind_mcp_available = False
    t.ifind_mcp_available = False
    t._wind_mcp_client = None
    t._ifind_client = None
    return t


# ============================================================
# 1. 模块常量
# ============================================================


class TestModuleConstants:
    """模块级常量结构验证."""

    def test_signal_thresholds_keys(self):
        assert set(SIGNAL_THRESHOLDS.keys()) == {"high", "medium", "low"}
        assert SIGNAL_THRESHOLDS["high"] == 50
        assert SIGNAL_THRESHOLDS["medium"] == 10
        assert SIGNAL_THRESHOLDS["low"] == 2

    def test_national_team_etfs_structure(self):
        assert len(NATIONAL_TEAM_ETFS) >= 10
        for etf in NATIONAL_TEAM_ETFS:
            assert "code" in etf
            assert "name" in etf
            assert "category" in etf
            assert isinstance(etf["code"], str)
            assert len(etf["code"]) == 6

    def test_national_team_etfs_contains_510300(self):
        codes = [e["code"] for e in NATIONAL_TEAM_ETFS]
        assert "510300" in codes
        assert "510050" in codes
        assert "159915" in codes  # 深市 15 开头

    def test_etf_to_stocks_structure(self):
        assert "510300" in ETF_TO_STOCKS
        info = ETF_TO_STOCKS["510300"]
        assert "板块" in info
        assert "个股票池" in info
        assert isinstance(info["个股票池"], list)
        assert len(info["个股票池"]) > 0


# ============================================================
# 2. _to_wind_code 委托测试
# ============================================================


class TestToWindCode:
    """_to_wind_code 委托给 contracts.symbols.to_wind_code."""

    def test_sh_etf(self, tracker_no_sources):
        # 510300 -> 510300.SH
        assert tracker_no_sources._to_wind_code("510300") == "510300.SH"

    def test_sz_etf(self, tracker_no_sources):
        # 159915 -> 159915.SZ
        assert tracker_no_sources._to_wind_code("159915") == "159915.SZ"

    def test_kechuang_etf(self, tracker_no_sources):
        # 588000 -> 588000.SH (5 开头)
        assert tracker_no_sources._to_wind_code("588000") == "588000.SH"


# ============================================================
# 3. _fetch_wind_fund_flow
# ============================================================


class TestFetchWindFundFlow:
    """Wind MCP 资金流获取."""

    def test_no_client_returns_none(self, tracker_no_sources):
        assert tracker_no_sources._fetch_wind_fund_flow("510300") is None

    def test_client_not_available_returns_none(self, tracker_no_sources):
        # _wind_mcp_client 存在但 wind_mcp_available=False
        tracker_no_sources._wind_mcp_client = {"quote": MagicMock()}
        assert tracker_no_sources._fetch_wind_fund_flow("510300") is None

    def test_quote_returns_none(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(return_value=None),
        }
        assert tracker_no_sources._fetch_wind_fund_flow("510300") is None

    def test_quote_price_le_zero_returns_none(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(return_value={"price": 0, "amount": 100}),
        }
        assert tracker_no_sources._fetch_wind_fund_flow("510300") is None

    def test_valid_quote_inflow(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(
                return_value={
                    "price": 4.5,
                    "amount": 1_000_000_000,  # 10 亿 (1e9 * 1e-8 = 10)
                    "volume": 200_000_000,
                    "change": 1.23,
                }
            ),
        }
        result = tracker_no_sources._fetch_wind_fund_flow("510300")
        assert result is not None
        assert result["code"] == "510300"
        assert result["name"] == "沪深300ETF华泰柏瑞"
        assert result["category"] == "宽基"
        assert result["source"] == "wind_mcp"
        assert result["net_flow_yi"] == 10.0
        assert result["amount_yi"] == 10.0
        assert result["trend"] == "流入"
        assert result["change_pct"] == 1.23
        assert result["volume"] == 200_000_000

    def test_valid_quote_outflow(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(
                return_value={
                    "price": 4.5,
                    "amount": -500_000_000,  # -5 亿
                    "volume": 100,
                    "change": -0.5,
                }
            ),
        }
        result = tracker_no_sources._fetch_wind_fund_flow("510050")
        assert result is not None
        assert result["name"] == "上证50ETF华夏"
        assert result["trend"] == "流出"
        assert result["net_flow_yi"] == -5.0

    def test_unknown_etf_code_no_name(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(
                return_value={
                    "price": 1.0,
                    "amount": 1_000_000_000,
                    "volume": 0,
                    "change": 0,
                }
            ),
        }
        # 不在 NATIONAL_TEAM_ETFS 列表中的代码
        result = tracker_no_sources._fetch_wind_fund_flow("510999")
        assert result is not None
        assert result["name"] == ""
        assert "category" not in result

    def test_amount_none_trend_neutral(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(
                return_value={
                    "price": 1.0,
                    "amount": None,
                    "volume": None,
                    "change": 0,
                }
            ),
        }
        result = tracker_no_sources._fetch_wind_fund_flow("510300")
        assert result is not None
        assert result["trend"] == "中性"
        assert result["net_flow_yi"] == 0.0
        assert result["volume"] == 0

    def test_quote_raises_returns_none(self, tracker_no_sources):
        tracker_no_sources.wind_mcp_available = True
        tracker_no_sources._wind_mcp_client = {
            "quote": MagicMock(side_effect=RuntimeError("wind down")),
        }
        assert tracker_no_sources._fetch_wind_fund_flow("510300") is None


# ============================================================
# 4. _fetch_ifind_fund_flow
# ============================================================


@pytest.mark.skip(
    reason="API 重构: utils/etf_flow_monitor.py 已移除 iFinD 数据源, "
    "改为 Wind MCP / 东财 / 新浪 / 价格动量回退链, 该方法不再存在"
)
class TestFetchIFindFundFlow:
    """iFinD MCP 资金流获取 (已废弃: 源码重构移除 iFinD 数据源)."""

    def test_no_client_returns_none(self, tracker_no_sources):
        assert tracker_no_sources._fetch_ifind_fund_flow("510300") is None

    def test_client_not_available_returns_none(self, tracker_no_sources):
        tracker_no_sources._ifind_client = MagicMock()
        assert tracker_no_sources._fetch_ifind_fund_flow("510300") is None

    def test_code_not_in_quotes_returns_none(self, tracker_no_sources):
        tracker_no_sources.ifind_mcp_available = True
        tracker_no_sources._ifind_client = MagicMock()
        tracker_no_sources._ifind_client.get_etf_quotes = MagicMock(return_value={})
        assert tracker_no_sources._fetch_ifind_fund_flow("510300") is None

    def test_valid_quote(self, tracker_no_sources):
        tracker_no_sources.ifind_mcp_available = True
        tracker_no_sources._ifind_client = MagicMock()
        tracker_no_sources._ifind_client.get_etf_quotes = MagicMock(
            return_value={
                "510300": {
                    "price": 4.5,
                    "change_pct": 0.0123,  # 1.23%
                    "volume": 1000000,
                }
            }
        )
        result = tracker_no_sources._fetch_ifind_fund_flow("510300")
        assert result is not None
        assert result["code"] == "510300"
        assert result["name"] == "沪深300ETF华泰柏瑞"
        assert result["category"] == "宽基"
        assert result["source"] == "ifind_mcp"
        assert result["change_pct"] == pytest.approx(1.23)
        assert result["volume"] == 1000000
        assert result["net_flow_yi"] == 0.0
        assert result["trend"] == "中性"

    def test_change_pct_none(self, tracker_no_sources):
        tracker_no_sources.ifind_mcp_available = True
        tracker_no_sources._ifind_client = MagicMock()
        tracker_no_sources._ifind_client.get_etf_quotes = MagicMock(
            return_value={"510300": {"price": 4.5, "volume": 100}}
        )
        result = tracker_no_sources._fetch_ifind_fund_flow("510300")
        assert result is not None
        assert result["change_pct"] == 0

    def test_exception_returns_none(self, tracker_no_sources):
        tracker_no_sources.ifind_mcp_available = True
        tracker_no_sources._ifind_client = MagicMock()
        tracker_no_sources._ifind_client.get_etf_quotes = MagicMock(
            side_effect=KeyError("boom")
        )
        assert tracker_no_sources._fetch_ifind_fund_flow("510300") is None


# ============================================================
# 5. _fetch_eastmoney_fund_flow
# ============================================================


class TestFetchEastmoneyFundFlow:
    """东财 push2 资金流获取."""

    def test_valid_klines_inflow(self, tracker_no_sources):
        # 构造 push2 响应: klines 最后一个元素以主力净流入(元)结尾
        # 例如 "...,500000000" 表示 5 亿
        resp = {
            "data": {
                "klines": [
                    "2026-08-10,4.50,4.60,100,200,300,400,500000000",
                ]
            }
        }
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            result = tracker_no_sources._fetch_eastmoney_fund_flow("510300")

        assert result is not None
        assert result["code"] == "510300"
        assert result["name"] == "沪深300ETF华泰柏瑞"
        assert result["category"] == "宽基"
        assert result["source"] == "eastmoney_push2"
        assert result["net_flow_yi"] == 5.0
        assert result["trend"] == "流入"
        assert result["change_pct"] == 0.0
        assert result["volume"] == 0

    def test_outflow(self, tracker_no_sources):
        resp = {"data": {"klines": ["x,-1.0,-2.0,0,0,0,0,-300000000"]}}
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            result = tracker_no_sources._fetch_eastmoney_fund_flow("510050")

        assert result is not None
        assert result["net_flow_yi"] == -3.0
        assert result["trend"] == "流出"

    def test_zero_flow_neutral(self, tracker_no_sources):
        resp = {"data": {"klines": ["x,0,0,0,0,0,0,0"]}}
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            result = tracker_no_sources._fetch_eastmoney_fund_flow("510050")

        assert result is not None
        assert result["trend"] == "中性"
        assert result["net_flow_yi"] == 0.0

    def test_empty_klines_returns_none(self, tracker_no_sources):
        resp = {"data": {"klines": []}}
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            assert tracker_no_sources._fetch_eastmoney_fund_flow("510300") is None

    def test_no_data_key_returns_none(self, tracker_no_sources):
        resp = {}
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            assert tracker_no_sources._fetch_eastmoney_fund_flow("510300") is None

    def test_invalid_json_returns_none(self, tracker_no_sources):
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"not json"
            mock_opener.open.return_value = mock_resp

            assert tracker_no_sources._fetch_eastmoney_fund_flow("510300") is None

    def test_open_raises_returns_none(self, tracker_no_sources):
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_opener.open.side_effect = OSError("timeout")
            assert tracker_no_sources._fetch_eastmoney_fund_flow("510300") is None

    def test_unknown_etf_code(self, tracker_no_sources):
        resp = {"data": {"klines": ["x,0,0,0,0,0,0,100000000"]}}
        with patch.object(etf_flow_monitor, "_em_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(resp).encode("utf-8")
            mock_opener.open.return_value = mock_resp

            result = tracker_no_sources._fetch_eastmoney_fund_flow("510999")
        assert result is not None
        assert result["name"] == ""
        assert result["category"] == ""


# ============================================================
# 6. _fetch_price_based_flow
# ============================================================


class TestFetchPriceBasedFlow:
    """价格动量代理资金流."""

    def test_valid_inflow(self, tracker_no_sources):
        mock_quotes = {
            "510300": {"change_pct": 2.5},
        }
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value=mock_quotes,
        ):
            result = tracker_no_sources._fetch_price_based_flow("510300")
        assert result is not None
        assert result["code"] == "510300"
        assert result["name"] == "沪深300ETF华泰柏瑞"
        assert result["source"] == "price_momentum"
        assert result["change_pct"] == 2.5
        assert result["net_flow_yi"] == 25.0  # 2.5 * 10
        assert result["trend"] == "流入"

    def test_outflow(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value={"510050": {"change_pct": -1.5}},
        ):
            result = tracker_no_sources._fetch_price_based_flow("510050")
        assert result is not None
        assert result["net_flow_yi"] == -15.0
        assert result["trend"] == "流出"

    def test_zero_change_neutral(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value={"510300": {"change_pct": 0}},
        ):
            result = tracker_no_sources._fetch_price_based_flow("510300")
        assert result is not None
        assert result["trend"] == "中性"
        assert result["net_flow_yi"] == 0.0

    def test_no_quote_returns_none(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value={},
        ):
            assert tracker_no_sources._fetch_price_based_flow("510300") is None

    def test_quote_none_returns_none(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value={"510300": None},
        ):
            assert tracker_no_sources._fetch_price_based_flow("510300") is None

    def test_change_pct_string(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            return_value={"510300": {"change_pct": "3.2"}},
        ):
            result = tracker_no_sources._fetch_price_based_flow("510300")
        assert result is not None
        assert result["change_pct"] == 3.2

    def test_exception_returns_none(self, tracker_no_sources):
        with patch(
            "utils.astock_realtime.get_realtime_quotes",
            side_effect=RuntimeError("net down"),
        ):
            assert tracker_no_sources._fetch_price_based_flow("510300") is None


# ============================================================
# 7. _fetch_sina_fund_flow
# ============================================================


class TestFetchSinaFundFlow:
    """新浪财经资金流获取."""

    def _make_sina_response(self, sina_code, fields):
        text = f'hq_str_{sina_code}="{",".join(fields)}";'
        return text

    def test_valid_sh_etf_inflow(self, tracker_no_sources):
        # fields: name, open, prev_close, current, high, low, ?, ?, volume, amount, ...
        fields = [
            "沪深300ETF",
            "4.50",
            "4.40",
            "4.60",
            "4.65",
            "4.35",
            "4.50",
            "4.55",
            "1000000",
            "500000000",
        ]
        text = self._make_sina_response("sh510300", fields)
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = text
            session.get.return_value = resp

            result = tracker_no_sources._fetch_sina_fund_flow("510300")

        assert result is not None
        assert result["code"] == "510300"
        assert result["name"] == "沪深300ETF"
        assert result["source"] == "sina_http"
        assert result["net_flow_yi"] == 5.0  # 5e8 * 1e-8
        assert result["amount_yi"] == 5.0
        assert result["volume"] == 1000000
        assert result["trend"] == "流入"
        # change_pct = (4.60 - 4.40) / 4.40 * 100 ≈ 4.5454...
        assert result["change_pct"] == pytest.approx(4.55, abs=0.05)

    def test_valid_sz_etf_outflow(self, tracker_no_sources):
        fields = [
            "创业板ETF",
            "3.00",
            "3.10",
            "2.95",
            "3.05",
            "2.90",
            "3.00",
            "3.00",
            "500000",
            "-200000000",
        ]
        text = self._make_sina_response("sz159915", fields)
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = text
            session.get.return_value = resp

            result = tracker_no_sources._fetch_sina_fund_flow("159915")

        assert result is not None
        assert result["net_flow_yi"] == -2.0
        assert result["trend"] == "流出"
        assert result["change_pct"] < 0
        assert result["category"] == "成长科技"  # NATIONAL_TEAM_ETFS 命中

    def test_invalid_prefix_returns_none(self, tracker_no_sources):
        # 不在 51/58/15/16 前缀中, 直接返回 None, 不发请求
        with patch("requests.Session") as MockSession:
            result = tracker_no_sources._fetch_sina_fund_flow("600519")
            assert result is None
            MockSession.return_value.get.assert_not_called()

    def test_status_not_200_returns_none(self, tracker_no_sources):
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 403
            resp.text = ""
            session.get.return_value = resp

            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None

    def test_no_prefix_in_text_returns_none(self, tracker_no_sources):
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "完全无关的响应内容"
            session.get.return_value = resp

            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None

    def test_too_few_fields_returns_none(self, tracker_no_sources):
        # 只有 5 个字段, 不够 10 个
        text = 'hq_str_sh510300="ETF,4.5,4.4,4.6,4.65";'
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = text
            session.get.return_value = resp

            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None

    def test_invalid_float_returns_none(self, tracker_no_sources):
        fields = [
            "ETF",
            "bad",
            "bad",
            "bad",
            "bad",
            "bad",
            "x",
            "y",
            "100",
            "200",
        ]
        text = self._make_sina_response("sh510300", fields)
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = text
            session.get.return_value = resp

            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None

    def test_current_le_zero_returns_none(self, tracker_no_sources):
        fields = [
            "ETF",
            "0",
            "4.4",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
        ]
        text = self._make_sina_response("sh510300", fields)
        with patch("requests.Session") as MockSession:
            session = MockSession.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.text = text
            session.get.return_value = resp

            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None

    def test_exception_returns_none(self, tracker_no_sources):
        with patch("requests.Session") as MockSession:
            MockSession.return_value.get.side_effect = OSError("network")
            assert tracker_no_sources._fetch_sina_fund_flow("510300") is None


# ============================================================
# 8. get_etf_fund_flow (多源回退)
# ============================================================


class TestGetEtfFundFlow:
    """get_etf_fund_flow 数据源回退链."""

    def test_wind_first(self, tracker_no_sources):
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "wind_mcp", "net_flow_yi": 1.0}
        )
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_price_based_flow = MagicMock(return_value=None)

        result = tracker_no_sources.get_etf_fund_flow("510300")
        assert result["source"] == "wind_mcp"
        tracker_no_sources._fetch_ifind_fund_flow.assert_not_called()

    def test_wind_none_ifind_used(self, tracker_no_sources):
        # API 重构: iFinD 已移除, wind 失败后回退到东财 push2
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "eastmoney_push2"}
        )
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(return_value=None)

        result = tracker_no_sources.get_etf_fund_flow("510300")
        assert result["source"] == "eastmoney_push2"
        tracker_no_sources._fetch_sina_fund_flow.assert_not_called()

    def test_eastmoney_used_when_first_two_none(self, tracker_no_sources):
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "eastmoney_push2"}
        )
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(return_value=None)

        result = tracker_no_sources.get_etf_fund_flow("510300")
        assert result["source"] == "eastmoney_push2"
        tracker_no_sources._fetch_sina_fund_flow.assert_not_called()

    def test_eastmoney_blocked_skips_eastmoney(self, tracker_no_sources):
        etf_flow_monitor._eastmoney_blocked = True
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "eastmoney_push2"}
        )
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "sina_http"}
        )

        result = tracker_no_sources.get_etf_fund_flow("510300")
        # eastmoney 应该被跳过, sina 命中
        assert result["source"] == "sina_http"
        tracker_no_sources._fetch_eastmoney_fund_flow.assert_not_called()

    def test_eastmoney_failure_sets_blocked_flag(self, tracker_no_sources):
        # 首次 eastmoney 失败, 标志位应被置为 True
        assert etf_flow_monitor._eastmoney_blocked is False
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(
            return_value={"code": "510300", "source": "sina_http"}
        )

        tracker_no_sources.get_etf_fund_flow("510300")
        assert etf_flow_monitor._eastmoney_blocked is True

    def test_all_sources_none_returns_none(self, tracker_no_sources):
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_price_based_flow = MagicMock(return_value=None)

        assert tracker_no_sources.get_etf_fund_flow("510300") is None

    def test_price_based_last_resort(self, tracker_no_sources):
        tracker_no_sources._fetch_wind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_ifind_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_eastmoney_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_sina_fund_flow = MagicMock(return_value=None)
        tracker_no_sources._fetch_price_based_flow = MagicMock(
            return_value={"code": "510300", "source": "price_momentum"}
        )

        result = tracker_no_sources.get_etf_fund_flow("510300")
        assert result["source"] == "price_momentum"


# ============================================================
# 9. get_all_etf_fund_flows
# ============================================================


class TestGetAllEtfFundFlows:
    """批量获取所有 ETF 资金流."""

    def test_all_success(self, tracker_no_sources):
        tracker_no_sources.get_etf_fund_flow = MagicMock(
            return_value={"code": "x", "net_flow_yi": 5.0, "name": "X", "category": "C"}
        )
        result = tracker_no_sources.get_all_etf_fund_flows()
        assert len(result) == len(NATIONAL_TEAM_ETFS)
        for etf in NATIONAL_TEAM_ETFS:
            assert etf["code"] in result
            assert result[etf["code"]]["net_flow_yi"] == 5.0

    def test_some_none_fills_defaults(self, tracker_no_sources):
        # 第一个返回 None, 第二个返回正常数据
        def fake_fetch(code):
            if code == NATIONAL_TEAM_ETFS[0]["code"]:
                return None
            return {"code": code, "net_flow_yi": 3.0}

        tracker_no_sources.get_etf_fund_flow = MagicMock(side_effect=fake_fetch)
        result = tracker_no_sources.get_all_etf_fund_flows()

        first = result[NATIONAL_TEAM_ETFS[0]["code"]]
        assert first["net_flow_yi"] == 0.0
        assert first["trend"] == "中性"
        assert first["source"] == "none"
        assert first["name"] == NATIONAL_TEAM_ETFS[0]["name"]
        assert first["category"] == NATIONAL_TEAM_ETFS[0]["category"]

        second_code = NATIONAL_TEAM_ETFS[1]["code"]
        assert result[second_code]["net_flow_yi"] == 3.0


# ============================================================
# 10. detect_signals
# ============================================================


class TestDetectSignals:
    """信号检测: 高/中/低 + 流入/流出 + 排序."""

    def test_high_inflow_signal(self, tracker_no_sources):
        flow_data = {
            "510300": {
                "name": "沪深300ETF华泰柏瑞",
                "category": "宽基",
                "net_flow_yi": 60.0,
                "change_pct": 1.0,
                "trend": "流入",
                "source": "wind_mcp",
            }
        }
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        s = signals[0]
        assert s["confidence"] == "高"
        assert s["signal_type"] == "国家队强加仓信号"
        assert s["code"] == "510300"

    def test_medium_inflow_signal(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": 15.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "中"
        assert signals[0]["signal_type"] == "国家队加仓信号"

    def test_low_inflow_signal(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": 3.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "低"
        assert signals[0]["signal_type"] == "国家队关注信号"

    def test_high_outflow_signal(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": -60.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "高"
        assert signals[0]["signal_type"] == "国家队强减仓信号"

    def test_medium_outflow_signal(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": -15.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "中"
        assert signals[0]["signal_type"] == "国家队减仓信号"

    def test_low_outflow_signal(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": -3.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "低"
        assert signals[0]["signal_type"] == "国家队减持关注"

    def test_below_threshold_skipped(self, tracker_no_sources):
        # net_flow_yi = 1, 低于 low 阈值 2, 不产生信号
        flow_data = {"510300": {"name": "X", "net_flow_yi": 1.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert signals == []

    def test_negative_below_threshold_skipped(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": -1.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert signals == []

    def test_zero_flow_skipped(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X", "net_flow_yi": 0.0, "category": "C"}}
        signals = tracker_no_sources.detect_signals(flow_data)
        assert signals == []

    def test_empty_flow_data(self, tracker_no_sources):
        signals = tracker_no_sources.detect_signals({})
        assert signals == []

    def test_missing_net_flow_field(self, tracker_no_sources):
        flow_data = {"510300": {"name": "X"}}  # 没有 net_flow_yi
        signals = tracker_no_sources.detect_signals(flow_data)
        assert signals == []

    def test_sorting_by_confidence_then_abs_flow(self, tracker_no_sources):
        flow_data = {
            "510300": {"name": "A", "net_flow_yi": 3.0, "category": "C"},  # 低
            "510050": {"name": "B", "net_flow_yi": 60.0, "category": "C"},  # 高
            "510500": {"name": "C", "net_flow_yi": 15.0, "category": "C"},  # 中
            "588000": {
                "name": "D",
                "net_flow_yi": 80.0,
                "category": "C",
            },  # 高, abs 更大
        }
        signals = tracker_no_sources.detect_signals(flow_data)
        # 高优先: 80 在 60 前
        assert signals[0]["net_flow_yi"] == 80.0
        assert signals[0]["confidence"] == "高"
        assert signals[1]["net_flow_yi"] == 60.0
        assert signals[1]["confidence"] == "高"
        # 中
        assert signals[2]["confidence"] == "中"
        # 低
        assert signals[3]["confidence"] == "低"

    def test_signal_fields_complete(self, tracker_no_sources):
        flow_data = {
            "510300": {
                "name": "沪深300ETF华泰柏瑞",
                "category": "宽基",
                "net_flow_yi": 60.0,
                "change_pct": 1.5,
                "trend": "流入",
                "source": "wind_mcp",
            }
        }
        signals = tracker_no_sources.detect_signals(flow_data)
        s = signals[0]
        for field in (
            "code",
            "name",
            "category",
            "net_flow_yi",
            "change_pct",
            "trend",
            "signal_type",
            "confidence",
            "source",
        ):
            assert field in s
        assert s["name"] == "沪深300ETF华泰柏瑞"
        assert s["source"] == "wind_mcp"

    def test_default_values_when_fields_missing(self, tracker_no_sources):
        flow_data = {
            "999999": {"net_flow_yi": 60.0}
        }  # 缺 name/category/change_pct/trend/source
        signals = tracker_no_sources.detect_signals(flow_data)
        s = signals[0]
        assert s["name"] == "999999"  # 默认回退到 code
        assert s["category"] == "未知"
        assert s["trend"] == "中性"
        assert s["source"] == "未知"
        assert s["change_pct"] == 0


# ============================================================
# 11. get_signal_summary
# ============================================================


class TestGetSignalSummary:
    """信号汇总."""

    def test_net_inflow(self, tracker_no_sources):
        flow_data = {
            "510300": {"net_flow_yi": 60.0, "name": "A", "category": "C"},
            "510050": {"net_flow_yi": -30.0, "name": "B", "category": "C"},
        }
        summary = tracker_no_sources.get_signal_summary(flow_data)
        assert summary["total_flow_yi"] == 30.0
        assert summary["overall_trend"] == "净流入"
        assert summary["signal_count"] == 2
        assert summary["strong_signals"] == 1  # 60.0 高
        assert summary["medium_signals"] == 1  # -30.0 中
        assert summary["low_signals"] == 0
        assert "signals" in summary
        assert "flow_data" in summary

    def test_net_outflow(self, tracker_no_sources):
        flow_data = {
            "510300": {"net_flow_yi": -60.0, "name": "A", "category": "C"},
            "510050": {"net_flow_yi": 10.0, "name": "B", "category": "C"},
        }
        summary = tracker_no_sources.get_signal_summary(flow_data)
        assert summary["total_flow_yi"] == -50.0
        assert summary["overall_trend"] == "净流出"

    def test_balanced(self, tracker_no_sources):
        flow_data = {
            "510300": {"net_flow_yi": 60.0, "name": "A", "category": "C"},
            "510050": {"net_flow_yi": -60.0, "name": "B", "category": "C"},
        }
        summary = tracker_no_sources.get_signal_summary(flow_data)
        assert summary["total_flow_yi"] == 0.0
        assert summary["overall_trend"] == "平衡"

    def test_empty_flow_data(self, tracker_no_sources):
        summary = tracker_no_sources.get_signal_summary({})
        assert summary["total_flow_yi"] == 0.0
        assert summary["overall_trend"] == "平衡"
        assert summary["signal_count"] == 0
        assert summary["signals"] == []

    def test_signal_counts(self, tracker_no_sources):
        flow_data = {
            "A": {"net_flow_yi": 100.0, "name": "A", "category": "C"},  # 高
            "B": {"net_flow_yi": 50.0, "name": "B", "category": "C"},  # 高 (>=50)
            "C": {"net_flow_yi": 20.0, "name": "C", "category": "C"},  # 中
            "D": {"net_flow_yi": 5.0, "name": "D", "category": "C"},  # 低
            "E": {"net_flow_yi": -100.0, "name": "E", "category": "C"},  # 高
            "F": {"net_flow_yi": -20.0, "name": "F", "category": "C"},  # 中
            "G": {"net_flow_yi": -5.0, "name": "G", "category": "C"},  # 低
        }
        summary = tracker_no_sources.get_signal_summary(flow_data)
        assert summary["signal_count"] == 7
        assert summary["strong_signals"] == 3
        assert summary["medium_signals"] == 2
        assert summary["low_signals"] == 2


# ============================================================
# 12. update_positions_json
# ============================================================


class TestUpdatePositionsJson:
    """positions.json 更新."""

    def _make_positions(self, code_num=None, with_signal=False):
        """构造测试用 positions 数据."""
        pos = {"code": code_num} if code_num else {}
        if with_signal:
            pos["etf_flow_signal"] = "旧值"
            pos["etf_inflow"] = 1.0
        return {
            "meta": {},
            "positions": {
                "p1": pos,
            },
        }

    def test_load_failure_returns_error(self, tracker_no_sources, tmp_path):
        # 文件不存在
        missing = tmp_path / "no_such.json"
        result = tracker_no_sources.update_positions_json(str(missing))
        assert result["status"] == "error"
        assert "message" in result

    def test_load_invalid_json_returns_error(self, tracker_no_sources, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        result = tracker_no_sources.update_positions_json(str(bad))
        assert result["status"] == "error"

    def test_successful_update_with_etf_match(self, tracker_no_sources, tmp_path):
        positions = self._make_positions(code_num="510300")
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        # mock get_all_etf_fund_flows + detect_signals
        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={
                "510300": {
                    "code": "510300",
                    "name": "沪深300ETF",
                    "category": "宽基",
                    "net_flow_yi": 60.0,
                    "change_pct": 1.0,
                    "trend": "流入",
                    "source": "wind_mcp",
                }
            }
        )
        tracker_no_sources.detect_signals = MagicMock(
            return_value=[
                {
                    "code": "510300",
                    "name": "沪深300ETF",
                    "category": "宽基",
                    "net_flow_yi": 60.0,
                    "change_pct": 1.0,
                    "trend": "流入",
                    "signal_type": "国家队强加仓信号",
                    "confidence": "高",
                    "source": "wind_mcp",
                }
            ]
        )

        result = tracker_no_sources.update_positions_json(str(pf))
        assert result["status"] == "success"
        assert result["updated_count"] == 1
        assert result["signal_count"] == 1
        assert result["total_flow_yi"] == 60.0

        # 验证写入的内容
        saved = json.loads(pf.read_text(encoding="utf-8"))
        pos = saved["positions"]["p1"]
        assert pos["etf_inflow"] == 60.0
        assert pos["etf_flow_signal"] == "强加仓"
        assert "last_etf_update" in saved["meta"]

    def test_medium_confidence_signal_label(self, tracker_no_sources, tmp_path):
        positions = self._make_positions(code_num="510300")
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={
                "510300": {"code": "510300", "name": "X", "net_flow_yi": 20.0}
            }
        )
        tracker_no_sources.detect_signals = MagicMock(
            return_value=[
                {
                    "code": "510300",
                    "name": "X",
                    "net_flow_yi": 20.0,
                    "signal_type": "国家队加仓信号",
                    "confidence": "中",
                }
            ]
        )

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        assert saved["positions"]["p1"]["etf_flow_signal"] == "加仓"

    def test_low_confidence_signal_label(self, tracker_no_sources, tmp_path):
        positions = self._make_positions(code_num="510300")
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={"510300": {"code": "510300", "name": "X", "net_flow_yi": 5.0}}
        )
        tracker_no_sources.detect_signals = MagicMock(
            return_value=[
                {
                    "code": "510300",
                    "name": "X",
                    "net_flow_yi": 5.0,
                    "signal_type": "国家队关注信号",
                    "confidence": "低",
                }
            ]
        )

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        assert saved["positions"]["p1"]["etf_flow_signal"] == "关注"

    def test_no_signal_match_neutral(self, tracker_no_sources, tmp_path):
        # ETF 在 flow_data 但不在 signal_map → "中性"
        positions = self._make_positions(code_num="510300")
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={"510300": {"code": "510300", "name": "X", "net_flow_yi": 1.0}}
        )
        tracker_no_sources.detect_signals = MagicMock(return_value=[])

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        assert saved["positions"]["p1"]["etf_flow_signal"] == "中性"
        assert saved["positions"]["p1"]["etf_inflow"] == 1.0

    def test_related_etf_linkage(self, tracker_no_sources, tmp_path):
        # 持仓股票 600036 在 510300 的个股票池, 但 600036 不在 flow_data
        positions = {
            "meta": {},
            "positions": {
                "p1": {
                    "code": "600036",
                    "etf_flow_signal": "旧值",
                    "etf_inflow": 0,
                }
            },
        }
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={
                "510300": {
                    "code": "510300",
                    "name": "沪深300ETF华泰柏瑞",
                    "net_flow_yi": 60.0,
                }
            }
        )
        tracker_no_sources.detect_signals = MagicMock(
            return_value=[
                {
                    "code": "510300",
                    "name": "沪深300ETF华泰柏瑞",
                    "net_flow_yi": 60.0,
                    "signal_type": "国家队强加仓信号",
                    "confidence": "高",
                }
            ]
        )

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        pos = saved["positions"]["p1"]
        assert pos["etf_inflow"] == 60.0
        assert pos["etf_flow_signal"] == "关联沪深300ETF华泰柏瑞"

    def test_related_etf_no_signal_match(self, tracker_no_sources, tmp_path):
        # 关联 ETF 在 flow_data 但不在 signal_map → 关联+etf_code
        positions = {
            "meta": {},
            "positions": {
                "p1": {
                    "code": "600036",
                    "etf_flow_signal": "旧值",
                    "etf_inflow": 0,
                }
            },
        }
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={"510300": {"code": "510300", "name": "X", "net_flow_yi": 1.0}}
        )
        tracker_no_sources.detect_signals = MagicMock(return_value=[])

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        assert saved["positions"]["p1"]["etf_flow_signal"] == "关联510300"

    def test_related_etf_not_found_clears_fields(self, tracker_no_sources, tmp_path):
        # 股票代码不在任何 ETF 池中 → 清空
        positions = {
            "meta": {},
            "positions": {
                "p1": {
                    "code": "999999",
                    "etf_flow_signal": "旧值",
                    "etf_inflow": 100,
                }
            },
        }
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(
            return_value={
                "510300": {"code": "510300", "name": "X", "net_flow_yi": 60.0}
            }
        )
        tracker_no_sources.detect_signals = MagicMock(return_value=[])

        tracker_no_sources.update_positions_json(str(pf))
        saved = json.loads(pf.read_text(encoding="utf-8"))
        assert saved["positions"]["p1"]["etf_flow_signal"] == ""
        assert saved["positions"]["p1"]["etf_inflow"] == 0

    def test_empty_code_skipped(self, tracker_no_sources, tmp_path):
        # code 为空字符串 → 跳过
        positions = {
            "meta": {},
            "positions": {"p1": {"code": ""}},
        }
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(return_value={})
        tracker_no_sources.detect_signals = MagicMock(return_value=[])

        result = tracker_no_sources.update_positions_json(str(pf))
        assert result["status"] == "success"
        assert result["updated_count"] == 0

    def test_save_failure_returns_error(self, tracker_no_sources, tmp_path):
        positions = {"meta": {}, "positions": {}}
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(positions), encoding="utf-8")

        tracker_no_sources.get_all_etf_fund_flows = MagicMock(return_value={})
        tracker_no_sources.detect_signals = MagicMock(return_value=[])

        # patch open 在写入时抛错
        original_open = open

        def fake_open(file, mode="r", *args, **kwargs):
            if "w" in mode:
                raise OSError("disk full")
            return original_open(file, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=fake_open):
            result = tracker_no_sources.update_positions_json(str(pf))

        assert result["status"] == "error"
        assert "disk full" in result["message"]


# ============================================================
# 13. refresh_etf_flow_signals
# ============================================================


class TestRefreshEtfFlowSignals:
    """refresh_etf_flow_signals 顶层入口."""

    def test_default_positions_file_path(self):
        # 验证默认路径拼接正确 (不实际打开文件, 只 mock tracker)
        with (
            patch.object(ETFRealTimeTracker, "_init_data_sources", lambda self: None),
            patch.object(ETFRealTimeTracker, "update_positions_json") as mock_update,
            patch.object(ETFRealTimeTracker, "get_all_etf_fund_flows") as mock_all,
            patch.object(ETFRealTimeTracker, "get_signal_summary") as mock_summary,
        ):

            mock_update.return_value = {"status": "success", "updated_count": 0}
            mock_all.return_value = {}
            mock_summary.return_value = {"total_flow_yi": 0.0, "overall_trend": "平衡"}

            refresh_etf_flow_signals()

            called_path = mock_update.call_args[0][0]
            assert called_path.endswith("positions.json")
            assert "config" in called_path.replace("\\", "/").replace("/", "/")

    def test_custom_positions_file(self, tmp_path):
        pf = tmp_path / "custom.json"
        pf.write_text(json.dumps({"meta": {}, "positions": {}}), encoding="utf-8")

        with (
            patch.object(ETFRealTimeTracker, "_init_data_sources", lambda self: None),
            patch.object(ETFRealTimeTracker, "update_positions_json") as mock_update,
            patch.object(ETFRealTimeTracker, "get_all_etf_fund_flows") as mock_all,
            patch.object(ETFRealTimeTracker, "get_signal_summary") as mock_summary,
        ):

            mock_update.return_value = {"status": "success", "updated_count": 0}
            mock_all.return_value = {}
            mock_summary.return_value = {"overall_trend": "平衡"}

            result = refresh_etf_flow_signals(str(pf))

            mock_update.assert_called_once_with(str(pf))
            assert result["status"] == "success"
            assert "summary" in result

    def test_failure_no_summary(self, tmp_path):
        # update_positions_json 失败 → 不调用 get_signal_summary, 不附加 summary
        with (
            patch.object(ETFRealTimeTracker, "_init_data_sources", lambda self: None),
            patch.object(ETFRealTimeTracker, "update_positions_json") as mock_update,
            patch.object(ETFRealTimeTracker, "get_signal_summary") as mock_summary,
        ):

            mock_update.return_value = {"status": "error", "message": "boom"}

            result = refresh_etf_flow_signals("/nonexistent/path.json")

            assert result["status"] == "error"
            assert "summary" not in result
            mock_summary.assert_not_called()


# ============================================================
# 14. get_etf_flow_summary
# ============================================================


class TestGetEtfFlowSummary:
    """get_etf_flow_summary 顶层入口."""

    def test_returns_summary(self):
        with (
            patch.object(ETFRealTimeTracker, "_init_data_sources", lambda self: None),
            patch.object(ETFRealTimeTracker, "get_all_etf_fund_flows") as mock_all,
            patch.object(ETFRealTimeTracker, "get_signal_summary") as mock_summary,
        ):

            mock_all.return_value = {
                "510300": {
                    "code": "510300",
                    "name": "X",
                    "net_flow_yi": 60.0,
                    "category": "C",
                }
            }
            mock_summary.return_value = {
                "total_flow_yi": 60.0,
                "overall_trend": "净流入",
                "signal_count": 1,
                "strong_signals": 1,
                "medium_signals": 0,
                "low_signals": 0,
                "signals": [],
                "flow_data": {},
            }

            result = get_etf_flow_summary()
            assert result["total_flow_yi"] == 60.0
            assert result["overall_trend"] == "净流入"
            mock_all.assert_called_once()
            mock_summary.assert_called_once()


# ============================================================
# 15. _init_data_sources 异常路径
# ============================================================


class TestInitDataSources:
    """_init_data_sources 异常路径 (真实加载流程)."""

    def test_wind_mcp_file_missing_silent(self):
        # wind_mcp_fetcher.py 不存在时, 不抛异常, wind_mcp_available 保持 False
        with patch.object(
            ETFRealTimeTracker,
            "_init_data_sources",
            ETFRealTimeTracker._init_data_sources,
        ):
            t = ETFRealTimeTracker()
        assert t.wind_mcp_available is False
        assert t._wind_mcp_client is None

    def test_ifind_token_missing_silent(self):
        # API 重构: iFinD 数据源已移除, 本测试跳过
        pytest.skip(
            "utils/etf_flow_monitor.py 已移除 iFinD 数据源, ifind_mcp_available 属性不再存在"
        )

    def test_ifind_token_present_loads_client(self):
        # API 重构: iFinD 数据源已移除, 本测试跳过
        pytest.skip(
            "utils/etf_flow_monitor.py 已移除 iFinD 数据源, ifind_mcp_available 属性不再存在"
        )

    def test_ifind_token_present_but_constructor_raises(self):
        # API 重构: iFinD 数据源已移除, 本测试跳过
        pytest.skip(
            "utils/etf_flow_monitor.py 已移除 iFinD 数据源, ifind_mcp_available 属性不再存在"
        )

    def test_wind_mcp_file_present_but_spec_none_raises(self, tmp_path):
        # 风险路径: wind_mcp_fetcher.py 存在但 spec_from_file_location 返回 None
        # 实际行为: 代码主动 raise ImportError, 而 except 元组不包含 ImportError,
        # 异常会向上传播 (这是已知的设计盲点, 测试记录此行为)
        with patch("os.path.isfile", return_value=True):
            with patch("importlib.util.spec_from_file_location", return_value=None):
                with pytest.raises(ImportError, match="无法加载 wind_mcp_fetcher"):
                    ETFRealTimeTracker()

    def test_wind_mcp_file_present_but_exec_raises_silent(self, tmp_path):
        # 风险路径: wind_mcp_fetcher.py 存在且 spec 正常, 但 exec_module 抛出被捕获的异常
        # 例如 AttributeError → 应该被静默捕获, wind_mcp_available 保持 False
        with patch("os.path.isfile", return_value=True):
            fake_spec = MagicMock()
            fake_spec.loader.exec_module.side_effect = AttributeError("missing attr")
            with patch(
                "importlib.util.spec_from_file_location", return_value=fake_spec
            ):
                t = ETFRealTimeTracker()
                assert t.wind_mcp_available is False
                assert t._wind_mcp_client is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
