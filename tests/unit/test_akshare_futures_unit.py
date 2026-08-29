"""akshare_futures 单元测试 — 期货数据统一接口"""

from unittest.mock import MagicMock

from utils.akshare_futures import (
    _normalize_ak_daily,
    _normalize_ak_quotes,
    _to_float,
    _to_str,
)


class TestToFloat:
    def test_normal(self):
        assert _to_float(3.14) == 3.14

    def test_int(self):
        assert _to_float(42) == 42.0

    def test_string(self):
        assert _to_float("1.5") == 1.5

    def test_none(self):
        assert _to_float(None) is None

    def test_invalid_string(self):
        assert _to_float("abc") is None

    def test_zero(self):
        assert _to_float(0) == 0.0

    def test_negative(self):
        assert _to_float(-1.5) == -1.5


class TestToStr:
    def test_normal(self):
        assert _to_str("hello") == "hello"

    def test_int(self):
        assert _to_str(42) == "42"

    def test_float(self):
        assert _to_str(3.14) == "3.14"

    def test_none(self):
        assert _to_str(None) is None


class TestNormalizeAkQuotes:
    def test_empty(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = []
        result = _normalize_ak_quotes(mock_df)
        assert result == {}

    def test_single_row(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "IF2509",
                "最新价": 4000.0,
                "开盘价": 3950.0,
                "最高价": 4050.0,
                "最低价": 3900.0,
                "成交量": 100000,
            }
        ]
        result = _normalize_ak_quotes(mock_df)
        assert "IF2509" in result
        assert result["IF2509"]["symbol"] == "IF2509"
        assert result["IF2509"]["source"] == "akshare"
        assert result["IF2509"]["latest"] == 4000.0
        assert result["IF2509"]["open"] == 3950.0

    def test_multiple_rows(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {"symbol": "A", "最新价": 100},
            {"symbol": "B", "最新价": 200},
        ]
        result = _normalize_ak_quotes(mock_df)
        assert len(result) == 2

    def test_no_symbol_skipped(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [{"最新价": 100}]
        result = _normalize_ak_quotes(mock_df)
        assert result == {}

    def test_english_keys(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "A",
                "current_price": 50.0,
                "open": 49.0,
                "high": 51.0,
                "low": 48.0,
                "volume": 1000,
            }
        ]
        result = _normalize_ak_quotes(mock_df)
        assert result["A"]["latest"] == 50.0
        assert result["A"]["open"] == 49.0

    def test_to_dict_exception(self):
        mock_df = MagicMock()
        mock_df.to_dict.side_effect = ValueError("error")
        result = _normalize_ak_quotes(mock_df)
        assert result == {}


class TestNormalizeAkDaily:
    def test_empty(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = []
        result = _normalize_ak_daily(mock_df)
        assert result == {}

    def test_single_row(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "IF2509",
                "日期": "2026-01-01",
                "开盘价": 3950.0,
                "收盘价": 4000.0,
                "最高价": 4050.0,
                "最低价": 3900.0,
                "成交量": 100000,
            }
        ]
        result = _normalize_ak_daily(mock_df)
        assert "IF2509" in result
        assert result["IF2509"]["source"] == "akshare_daily"
        assert result["IF2509"]["date"] == "2026-01-01"
        assert result["IF2509"]["close"] == 4000.0

    def test_no_symbol_skipped(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [{"收盘价": 100}]
        result = _normalize_ak_daily(mock_df)
        assert result == {}

    def test_english_keys(self):
        mock_df = MagicMock()
        mock_df.to_dict.return_value = [
            {
                "symbol": "A",
                "date": "2026-01-01",
                "open": 10,
                "close": 11,
                "high": 12,
                "low": 9,
                "volume": 500,
            }
        ]
        result = _normalize_ak_daily(mock_df)
        assert result["A"]["close"] == 11.0

    def test_to_dict_exception(self):
        mock_df = MagicMock()
        mock_df.to_dict.side_effect = TypeError("error")
        result = _normalize_ak_daily(mock_df)
        assert result == {}
