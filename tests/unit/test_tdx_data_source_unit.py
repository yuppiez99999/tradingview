"""tdx_data_source 单元测试 — 通达信数据源适配器."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.tdx_data_source import TDXDataSource, get_tdx_source, safe_float


class TestSafeFloat:
    def test_normal(self):
        assert safe_float(3.14) == 3.14

    def test_none(self):
        assert safe_float(None) is None
        assert safe_float(None, default=0.0) == 0.0

    def test_string(self):
        assert safe_float("3.14") == 3.14

    def test_invalid(self):
        assert safe_float("abc") is None
        assert safe_float("abc", default=0) == 0


class TestToTdxCode:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_plain(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("600276") == "600276"

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_sh_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("sh600276") == "600276"
        assert ds._to_tdx_code("SH600276") == "600276"

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_sz_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("sz000001") == "000001"

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_bj_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("bj430047") == "430047"

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_dot_suffix(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("600276.SH") == "600276"
        assert ds._to_tdx_code("000001.SZ") == "000001"
        assert ds._to_tdx_code("430047.BJ") == "430047"

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_whitespace(self, _mock):
        ds = TDXDataSource()
        assert ds._to_tdx_code("  600276  ") == "600276"


class TestGetMarket:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_sh_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._get_market("sh600276") == 1
        assert ds._get_market("SH600276") == 1

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_sz_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._get_market("sz000001") == 0
        assert ds._get_market("SZ000001") == 0

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_bj_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._get_market("bj430047") == 2

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_dot_suffix(self, _mock):
        ds = TDXDataSource()
        assert ds._get_market("600276.SH") == 1
        assert ds._get_market("000001.SZ") == 0
        assert ds._get_market("430047.BJ") == 2

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_by_code_prefix(self, _mock):
        ds = TDXDataSource()
        assert ds._get_market("600276") == 1
        assert ds._get_market("000001") == 0
        assert ds._get_market("300274") == 0
        assert ds._get_market("688041") == 1
        assert ds._get_market("510300") == 1
        assert ds._get_market("430047") == 2
        assert ds._get_market("830001") == 2


class TestInitConnection:
    def test_graceful_failure(self):
        # 模拟连接失败, 验证 _init_connection 优雅降级 (环境可能真实连通, 故 mock _connect 抛异常)
        with patch("utils.tdx_data_source.TDXDataSource._connect", side_effect=OSError("connection refused")):
            ds = TDXDataSource()
        assert ds._connected is False
        assert ds.source_health["tdx"]["ok"] is False


class TestEnsureConnected:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_no_api_cls(self, _mock):
        ds = TDXDataSource()
        ds._api_cls = None
        assert ds._ensure_connected() is False


class TestDisconnect:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_basic(self, _mock):
        ds = TDXDataSource()
        ds._connected = True
        ds._api = MagicMock()
        ds.disconnect()
        assert ds._connected is False
        assert ds._api is None

    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_no_api(self, _mock):
        ds = TDXDataSource()
        ds.disconnect()
        assert ds._connected is False


class TestGetRealtimeQuote:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_not_connected(self, _mock):
        ds = TDXDataSource()
        ds._connected = False
        ds._api_cls = None
        assert ds.get_realtime_quote("600276") is None


class TestGetHistoricalKlines:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_not_connected(self, _mock):
        ds = TDXDataSource()
        ds._connected = False
        ds._api_cls = None
        assert ds.get_historical_klines("600276") is None


class TestGetFinancialData:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_not_connected(self, _mock):
        ds = TDXDataSource()
        ds._connected = False
        ds._api_cls = None
        assert ds.get_financial_data("600276") is None


class TestGetSectorStocks:
    @patch("utils.tdx_data_source.TDXDataSource._init_connection")
    def test_not_connected(self, _mock):
        ds = TDXDataSource()
        ds._connected = False
        ds._api_cls = None
        assert ds.get_sector_stocks("银行") == []


class TestGetTdxSource:
    def test_singleton(self):
        s1 = get_tdx_source()
        s2 = get_tdx_source()
        assert s1 is s2
