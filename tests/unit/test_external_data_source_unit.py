"""external_data_source 单元测试 — 外部金融数据源

覆盖:
- _parse_api_float: 安全数值解析
- MacroIndicator dataclass
- FREDApi / EcondbApi / FedTreasuryApi / AlphaVantageApi / FinnhubApi / CoinGeckoApi
- ExternalDataManager (mock 网络)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.external_data_source import (
    CACHE_TTL,
    AlphaVantageApi,
    CoinGeckoApi,
    EcondbApi,
    ExternalDataManager,
    FedTreasuryApi,
    FinnhubApi,
    FREDApi,
    MacroIndicator,
    _parse_api_float,
)

# ============================================================
# _parse_api_float
# ============================================================


class TestParseApiFloat:
    """_parse_api_float 安全解析测试"""

    def test_normal_float(self):
        assert _parse_api_float(3.14) == 3.14

    def test_int(self):
        assert _parse_api_float(42) == 42.0

    def test_string_number(self):
        assert _parse_api_float("3.14") == 3.14

    def test_none(self):
        assert _parse_api_float(None) is None

    def test_fred_missing_value(self):
        """FRED 缺失值 '.' → None"""
        assert _parse_api_float(".") is None

    def test_n_a_string(self):
        assert _parse_api_float("N/A") is None

    def test_empty_string(self):
        assert _parse_api_float("") is None

    def test_nan(self):
        result = _parse_api_float(float("nan"))
        assert result is None

    def test_invalid_string(self):
        assert _parse_api_float("abc") is None


# ============================================================
# MacroIndicator
# ============================================================


class TestMacroIndicator:
    """MacroIndicator dataclass 测试"""

    def test_init(self):
        m = MacroIndicator(name="CPI", value=3.2, unit="%", date="2026-08", source="FRED")
        assert m.name == "CPI"
        assert m.value == 3.2
        assert m.previous is None
        assert m.change is None

    def test_to_dict(self):
        m = MacroIndicator(name="CPI", value=3.2, unit="%", date="2026-08", source="FRED", previous=3.0, change=0.2)
        d = m.to_dict()
        assert d["name"] == "CPI"
        assert d["value"] == 3.2
        assert d["previous"] == 3.0
        assert d["change"] == 0.2


# ============================================================
# FREDApi
# ============================================================


class TestFREDApi:
    """FREDApi 测试"""

    def test_init_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            api = FREDApi()
            assert api.available is False

    def test_init_with_key(self):
        api = FREDApi(api_key="test_key")
        assert api.available is True

    def test_get_indicator_unavailable(self):
        api = FREDApi(api_key="")
        assert api.get_indicator("CPIAUCSL") is None

    def test_get_indicator_success(self):
        api = FREDApi(api_key="test_key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "observations": [
                {"value": "3.2", "date": "2026-08-01"},
                {"value": "3.0", "date": "2026-07-01"},
            ]
        }
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_indicator("CPIAUCSL")
        assert result is not None
        assert result.value == 3.2
        assert result.previous == 3.0
        assert result.change == pytest.approx(0.2)

    def test_get_indicator_missing_value(self):
        """FRED 缺失值 '.' → None"""
        api = FREDApi(api_key="test_key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"observations": [{"value": ".", "date": "2026-08-01"}]}
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_indicator("CPIAUCSL") is None

    def test_get_indicator_http_error(self):
        api = FREDApi(api_key="test_key")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_indicator("CPIAUCSL") is None

    def test_get_macro_snapshot_unavailable(self):
        api = FREDApi(api_key="")
        assert api.get_macro_snapshot() == {}


# ============================================================
# EcondbApi
# ============================================================


class TestEcondbApi:
    """EcondbApi 测试"""

    def test_init(self):
        api = EcondbApi()
        assert api.available is True

    def test_get_indicator_success(self):
        api = EcondbApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"value": 2.5, "date": "2026-08"}]}
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_indicator("CPIUS")
        assert result is not None
        assert result.value == 2.5

    def test_get_indicator_http_error(self):
        api = EcondbApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_indicator("CPIUS") is None

    def test_get_indicator_empty_data(self):
        api = EcondbApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_indicator("CPIUS") is None


# ============================================================
# FedTreasuryApi
# ============================================================


class TestFedTreasuryApi:
    """FedTreasuryApi 测试"""

    def test_init(self):
        api = FedTreasuryApi()
        assert api.available is True

    def test_get_treasury_yields_success(self):
        api = FedTreasuryApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"security_desc": "10-Year Bond", "avg_interest_rate_amount": "3.2", "record_date": "2026-08-01"},
                {"security_desc": "2-Year Note", "avg_interest_rate_amount": "3.8", "record_date": "2026-08-01"},
            ]
        }
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            yields = api.get_treasury_yields()
        assert "10Y" in yields
        assert yields["10Y"] == 3.2
        assert "2Y" in yields

    def test_get_treasury_yields_http_error(self):
        api = FedTreasuryApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_treasury_yields() == {}


# ============================================================
# AlphaVantageApi
# ============================================================


class TestAlphaVantageApi:
    """AlphaVantageApi 测试"""

    def test_init_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            api = AlphaVantageApi()
            assert api.available is False

    def test_init_with_key(self):
        api = AlphaVantageApi(api_key="test")
        assert api.available is True

    def test_get_global_quote_unavailable(self):
        api = AlphaVantageApi(api_key="")
        assert api.get_global_quote("AAPL") is None

    def test_get_global_quote_success(self):
        api = AlphaVantageApi(api_key="test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "Global Quote": {
                "02. open": "150.0",
                "03. high": "155.0",
                "04. low": "149.0",
                "05. price": "153.0",
                "08. previous close": "151.0",
                "06. volume": "1000000",
                "10. change percent": "1.32%",
            }
        }
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_global_quote("AAPL")
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["close"] == 153.0


# ============================================================
# FinnhubApi
# ============================================================


class TestFinnhubApi:
    """FinnhubApi 测试"""

    def test_init_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            api = FinnhubApi()
            assert api.available is False

    def test_get_quote_unavailable(self):
        api = FinnhubApi(api_key="")
        assert api.get_quote("AAPL") is None

    def test_get_quote_success(self):
        api = FinnhubApi(api_key="test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"c": 150, "o": 148, "h": 152, "l": 147, "pc": 145}
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_quote("AAPL")
        assert result is not None
        assert result["close"] == 150

    def test_get_market_news_unavailable(self):
        api = FinnhubApi(api_key="")
        assert api.get_market_news() == []

    def test_get_market_news_success(self):
        api = FinnhubApi(api_key="test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"headline": "News 1", "summary": "Sum", "source": "src", "url": "http://x", "datetime": 1700000000}
        ]
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            news = api.get_market_news()
        assert len(news) == 1
        assert news[0]["title"] == "News 1"


# ============================================================
# CoinGeckoApi
# ============================================================


class TestCoinGeckoApi:
    """CoinGeckoApi 测试"""

    def test_init(self):
        api = CoinGeckoApi()
        assert api.available is True

    def test_get_price_success(self):
        api = CoinGeckoApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bitcoin": {"usd": 50000, "usd_24h_change": 2.5, "usd_market_cap": 1000000000}
        }
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_price("bitcoin")
        assert result is not None
        assert result["price"] == 50000

    def test_get_price_http_error(self):
        api = CoinGeckoApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            assert api.get_price("bitcoin") is None

    def test_get_global_market_success(self):
        api = CoinGeckoApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "total_market_cap": {"usd": 2_000_000_000_000},
                "total_volume": {"usd": 100_000_000_000},
                "market_cap_percentage": {"btc": 50},
                "market_cap_change_percentage_24h_usd": -2.5,
            }
        }
        with patch("utils.external_data_source._SESSION.get", return_value=mock_resp):
            result = api.get_global_market()
        assert result is not None
        assert result["total_market_cap"] == 2_000_000_000_000


# ============================================================
# ExternalDataManager
# ============================================================


class TestExternalDataManager:
    """ExternalDataManager 管理器测试"""

    def test_init(self):
        m = ExternalDataManager()
        assert m.fred is not None
        assert m.econdb is not None
        assert m.treasury is not None
        assert m.alpha_vantage is not None
        assert m.finnhub is not None
        assert m.coingecko is not None

    def test_cache_path(self):
        m = ExternalDataManager()
        path = m._cache_path("macro", "test/key")
        assert "macro_test_key" in str(path)

    def test_load_cache_missing(self):
        m = ExternalDataManager()
        assert m._load_cache("macro", "nonexistent_key_12345") is None

    def test_get_macro_snapshot_no_api(self):
        """无 API key → 空快照 (但 treasury 和 coingecko 可用)"""
        m = ExternalDataManager()
        with patch.object(m.fred, "available", False):
            with patch.object(m.treasury, "get_treasury_yields", return_value={}):
                with patch.object(m.coingecko, "get_global_market", return_value=None):
                    with patch.object(m, "_load_cache", return_value=None):
                        with patch.object(m, "_save_cache"):
                            result = m.get_macro_snapshot()
        assert isinstance(result, dict)

    def test_get_global_stock_no_api(self):
        m = ExternalDataManager()
        with patch.object(m.finnhub, "available", False), patch.object(m.alpha_vantage, "available", False):
            with patch.object(m, "_load_cache", return_value=None):
                assert m.get_global_stock("AAPL") is None

    def test_get_crypto_price_no_api(self):
        m = ExternalDataManager()
        with patch.object(m.coingecko, "get_price", return_value=None):
            with patch.object(m, "_load_cache", return_value=None):
                assert m.get_crypto_price("bitcoin") is None

    def test_get_market_news_no_api(self):
        m = ExternalDataManager()
        with patch.object(m.finnhub, "available", False), patch.object(m, "_load_cache", return_value=None):
            assert m.get_market_news() == []

    def test_get_risk_sentiment(self):
        m = ExternalDataManager()
        with patch.object(m, "get_macro_snapshot", return_value={}):
            sentiment = m.get_risk_sentiment()
        assert "timestamp" in sentiment
        assert "vix_proxy" in sentiment
        assert "treasury_yield_curve" in sentiment
        assert "fed_rate" in sentiment

    def test_cache_ttl_values(self):
        assert CACHE_TTL["macro"] == 3600
        assert CACHE_TTL["stock"] == 300
        assert CACHE_TTL["crypto"] == 180
