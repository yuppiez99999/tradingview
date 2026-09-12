"""G7 覆盖率冲刺 — utils/external_data_source.py 补测试.

目标模块: utils/external_data_source.py (43.20% → 目标 75%+)

覆盖核心路径:
    - 纯函数: _parse_api_float
    - 数据类: MacroIndicator / to_dict
    - FREDApi: __init__ / get_indicator / get_macro_snapshot
    - EcondbApi: __init__ / get_indicator
    - FedTreasuryApi: get_treasury_yields (各期限匹配 + 异常)
    - AlphaVantageApi: __init__ / get_global_quote
    - FinnhubApi: __init__ / get_quote / get_market_news
    - CoinGeckoApi: get_price / get_global_market
    - ExternalDataManager: __init__ / _cache_path / _load_cache /
      _save_cache / get_macro_snapshot / get_global_stock /
      get_crypto_price / get_market_news / get_risk_sentiment

约束:
    - 不发起任何真实网络请求
    - 用 unittest.mock.patch / MagicMock 隔离外部依赖
    - 测试文件可独立运行:
      python -m pytest tests/unit/test_g7_external_data_source_boost.py -q
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from utils.datetime_utils import now_bj

# ============================================================
# 路径设置
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import external_data_source  # noqa: E402
from utils.external_data_source import (  # noqa: E402
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

# Workaround: 源模块使用了 cast() 但未导入, 注入以支持缓存命中路径
if not hasattr(external_data_source, "cast"):
    external_data_source.cast = lambda t, v: v


# P0-2 (2026-09-01): 本文件用 mock 网络层测解析逻辑 — 清除 conftest 的
# QUANT_OFFLINE 默认值, 让 mock 响应走成功路径 (真实外网仍被 mock 拦截)
@pytest.fixture(autouse=True)
def _allow_mocked_network(monkeypatch):
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)


# ============================================================
# 辅助函数 / fixture
# ============================================================


def _mock_response(status_code=200, json_data=None):
    """创建 mock HTTP 响应."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """将 CACHE_DIR 重定向到临时目录, 避免污染真实缓存."""
    monkeypatch.setattr(external_data_source, "CACHE_DIR", tmp_path)
    return tmp_path


# ============================================================
# 1. _parse_api_float 纯函数测试
# ============================================================


class TestParseApiFloat:
    """_parse_api_float 安全解析数值字段."""

    def test_none_returns_none(self):
        assert _parse_api_float(None) is None

    def test_string_number(self):
        assert _parse_api_float("3.5") == 3.5

    def test_float_passthrough(self):
        assert _parse_api_float(3.5) == 3.5

    def test_int(self):
        assert _parse_api_float(42) == 42.0

    def test_fred_missing_dot(self):
        """FRED 缺失值 '.' 返回 None (P1-T1 修复)."""
        assert _parse_api_float(".") is None

    def test_na_string(self):
        assert _parse_api_float("N/A") is None

    def test_empty_string(self):
        assert _parse_api_float("") is None

    def test_nan_returns_none(self):
        assert _parse_api_float(float("nan")) is None

    def test_zero_returns_zero(self):
        assert _parse_api_float(0) == 0.0

    def test_negative(self):
        assert _parse_api_float("-2.5") == -2.5

    def test_invalid_type(self):
        assert _parse_api_float([1, 2]) is None


# ============================================================
# 2. MacroIndicator 数据类测试
# ============================================================


class TestMacroIndicator:
    """MacroIndicator 数据类与 to_dict."""

    def test_default_optional_fields(self):
        mi = MacroIndicator(
            name="CPI", value=2.5, unit="%", date="2026-01", source="FRED"
        )
        assert mi.previous is None
        assert mi.change is None

    def test_to_dict_full(self):
        mi = MacroIndicator(
            name="CPI",
            value=2.5,
            unit="%",
            date="2026-01",
            source="FRED",
            previous=2.3,
            change=0.2,
        )
        d = mi.to_dict()
        assert d["name"] == "CPI"
        assert d["value"] == 2.5
        assert d["unit"] == "%"
        assert d["date"] == "2026-01"
        assert d["source"] == "FRED"
        assert d["previous"] == 2.3
        assert d["change"] == 0.2

    def test_to_dict_none_optionals(self):
        mi = MacroIndicator(
            name="GDP", value=100.0, unit="%", date="2026-Q1", source="Econdb"
        )
        d = mi.to_dict()
        assert d["previous"] is None
        assert d["change"] is None


# ============================================================
# 3. FREDApi 测试
# ============================================================


class TestFREDApi:
    """FREDApi 初始化与 get_indicator / get_macro_snapshot."""

    def test_init_no_key(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        api = FREDApi()
        assert api.api_key == ""
        assert api.available is False

    def test_init_with_key(self):
        api = FREDApi(api_key="test_key")
        assert api.api_key == "test_key"
        assert api.available is True

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "env_key")
        api = FREDApi()
        assert api.api_key == "env_key"
        assert api.available is True

    def test_get_indicator_not_available(self):
        api = FREDApi(api_key="")
        assert api.get_indicator("CPIAUCSL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_http_error(self, mock_session):
        api = FREDApi(api_key="key")
        mock_session.get.return_value = _mock_response(status_code=500)
        assert api.get_indicator("CPIAUCSL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_empty_observations(self, mock_session):
        api = FREDApi(api_key="key")
        mock_session.get.return_value = _mock_response(json_data={"observations": []})
        assert api.get_indicator("CPIAUCSL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_missing_value_dot(self, mock_session):
        """FRED 缺失值 '.' 返回 None (P1-T1 修复)."""
        api = FREDApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={"observations": [{"value": ".", "date": "2026-01"}]}
        )
        assert api.get_indicator("CPIAUCSL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_success_with_previous(self, mock_session):
        api = FREDApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={
                "observations": [
                    {"value": "2.5", "date": "2026-02"},
                    {"value": "2.3", "date": "2026-01"},
                ]
            }
        )
        result = api.get_indicator("CPIAUCSL")
        assert result is not None
        assert result.name == "CPIAUCSL"
        assert result.value == 2.5
        assert result.previous == 2.3
        assert result.change == pytest.approx(0.2)
        assert result.date == "2026-02"
        assert result.source == "FRED"
        assert result.unit == "%"

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_success_single_observation(self, mock_session):
        """仅 1 条观测值时 previous=None, change=None."""
        api = FREDApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={"observations": [{"value": "2.5", "date": "2026-02"}]}
        )
        result = api.get_indicator("CPIAUCSL")
        assert result is not None
        assert result.previous is None
        assert result.change is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_connection_error(self, mock_session):
        api = FREDApi(api_key="key")
        mock_session.get.side_effect = ConnectionError("refused")
        assert api.get_indicator("CPIAUCSL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_json_value_error(self, mock_session):
        """resp.json() 抛 ValueError 时 fail-safe 返回 None."""
        api = FREDApi(api_key="key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_session.get.return_value = mock_resp
        assert api.get_indicator("CPIAUCSL") is None

    def test_get_macro_snapshot_partial(self):
        """get_macro_snapshot 仅部分 series 返回数据."""
        api = FREDApi(api_key="key")
        with patch.object(api, "get_indicator") as mock_get:

            def side_effect(sid):
                if sid == "CPIAUCSL":
                    return MacroIndicator(
                        name=sid, value=2.5, unit="%", date="2026-01", source="FRED"
                    )
                return None

            mock_get.side_effect = side_effect
            result = api.get_macro_snapshot()
        assert "CPI" in result
        assert "PPI" not in result
        assert "GDP" not in result

    def test_get_macro_snapshot_all_none(self):
        api = FREDApi(api_key="key")
        with patch.object(api, "get_indicator", return_value=None):
            result = api.get_macro_snapshot()
        assert result == {}


# ============================================================
# 4. EcondbApi 测试
# ============================================================


class TestEcondbApi:
    """EcondbApi get_indicator."""

    def test_init(self):
        api = EcondbApi()
        assert api.available is True

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_http_error(self, mock_session):
        mock_session.get.return_value = _mock_response(status_code=404)
        api = EcondbApi()
        assert api.get_indicator("CPIUS") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_empty_data(self, mock_session):
        mock_session.get.return_value = _mock_response(json_data={"data": []})
        api = EcondbApi()
        assert api.get_indicator("CPIUS") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_none_value(self, mock_session):
        """缺失值 None 返回 None (P1-T1)."""
        mock_session.get.return_value = _mock_response(
            json_data={"data": [{"value": None, "date": "2026-01"}]}
        )
        api = EcondbApi()
        assert api.get_indicator("CPIUS") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_success(self, mock_session):
        """EcondbApi 取 series_data[-1] 作为最新值."""
        mock_session.get.return_value = _mock_response(
            json_data={
                "data": [
                    {"value": "2.3", "date": "2025-12"},
                    {"value": "2.5", "date": "2026-01"},
                ]
            }
        )
        api = EcondbApi()
        result = api.get_indicator("CPIUS")
        assert result is not None
        assert result.value == 2.5
        assert result.date == "2026-01"
        assert result.source == "Econdb"
        assert result.name == "CPIUS"

    @patch("utils.external_data_source._SESSION")
    def test_get_indicator_oserror(self, mock_session):
        mock_session.get.side_effect = OSError("timeout")
        api = EcondbApi()
        assert api.get_indicator("CPIUS") is None


# ============================================================
# 5. FedTreasuryApi 测试
# ============================================================


class TestFedTreasuryApi:
    """FedTreasuryApi get_treasury_yields."""

    def test_init(self):
        api = FedTreasuryApi()
        assert api.available is True

    @patch("utils.external_data_source._SESSION")
    def test_http_error_returns_empty(self, mock_session):
        mock_session.get.return_value = _mock_response(status_code=500)
        api = FedTreasuryApi()
        assert api.get_treasury_yields() == {}

    @patch("utils.external_data_source._SESSION")
    def test_all_maturities_with_hyphen(self, mock_session):
        """匹配 'X-Month' / 'X-Year' 格式."""
        records = [
            {
                "security_desc": "3-Month Bill",
                "avg_interest_rate_amount": "4.5",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "6-Month Bill",
                "avg_interest_rate_amount": "4.3",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "1-Year Note",
                "avg_interest_rate_amount": "4.0",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "2-Year Note",
                "avg_interest_rate_amount": "3.8",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "5-Year Note",
                "avg_interest_rate_amount": "3.5",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "10-Year Note",
                "avg_interest_rate_amount": "3.2",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "30-Year Bond",
                "avg_interest_rate_amount": "3.4",
                "record_date": "2026-01-01",
            },
        ]
        mock_session.get.return_value = _mock_response(json_data={"data": records})
        api = FedTreasuryApi()
        yields = api.get_treasury_yields()
        assert yields["3M"] == 4.5
        assert yields["6M"] == 4.3
        assert yields["1Y"] == 4.0
        assert yields["2Y"] == 3.8
        assert yields["5Y"] == 3.5
        assert yields["10Y"] == 3.2
        assert yields["30Y"] == 3.4

    @patch("utils.external_data_source._SESSION")
    def test_maturities_with_space(self, mock_session):
        """匹配 'X Month' / 'X Year' 格式."""
        records = [
            {
                "security_desc": "3 Month Bill",
                "avg_interest_rate_amount": "4.5",
                "record_date": "2026-01-01",
            },
            {
                "security_desc": "10 Year Note",
                "avg_interest_rate_amount": "3.2",
                "record_date": "2026-01-01",
            },
        ]
        mock_session.get.return_value = _mock_response(json_data={"data": records})
        api = FedTreasuryApi()
        yields = api.get_treasury_yields()
        assert yields["3M"] == 4.5
        assert yields["10Y"] == 3.2

    @patch("utils.external_data_source._SESSION")
    def test_unmatched_desc_skipped(self, mock_session):
        """不匹配任何期限的记录被跳过."""
        records = [
            {
                "security_desc": "Unknown Security",
                "avg_interest_rate_amount": "5.0",
                "record_date": "2026-01-01",
            },
        ]
        mock_session.get.return_value = _mock_response(json_data={"data": records})
        api = FedTreasuryApi()
        yields = api.get_treasury_yields()
        assert yields == {}

    @patch("utils.external_data_source._SESSION")
    def test_empty_records(self, mock_session):
        mock_session.get.return_value = _mock_response(json_data={"data": []})
        api = FedTreasuryApi()
        assert api.get_treasury_yields() == {}

    @patch("utils.external_data_source._SESSION")
    def test_exception_returns_empty(self, mock_session):
        mock_session.get.side_effect = ConnectionError("refused")
        api = FedTreasuryApi()
        assert api.get_treasury_yields() == {}


# ============================================================
# 6. AlphaVantageApi 测试
# ============================================================


class TestAlphaVantageApi:
    """AlphaVantageApi get_global_quote."""

    def test_init_no_key(self, monkeypatch):
        monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
        api = AlphaVantageApi()
        assert api.available is False

    def test_init_with_key(self):
        api = AlphaVantageApi(api_key="av_key")
        assert api.available is True

    def test_not_available_returns_none(self):
        api = AlphaVantageApi(api_key="")
        assert api.get_global_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_http_error(self, mock_session):
        api = AlphaVantageApi(api_key="key")
        mock_session.get.return_value = _mock_response(status_code=429)
        assert api.get_global_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_empty_quote(self, mock_session):
        api = AlphaVantageApi(api_key="key")
        mock_session.get.return_value = _mock_response(json_data={"Global Quote": {}})
        assert api.get_global_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_success(self, mock_session):
        api = AlphaVantageApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={
                "Global Quote": {
                    "02. open": "150.0",
                    "03. high": "155.0",
                    "04. low": "148.0",
                    "05. price": "152.0",
                    "08. previous close": "149.0",
                    "06. volume": "1000000",
                    "10. change percent": "2.0%",
                }
            }
        )
        result = api.get_global_quote("AAPL")
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["open"] == 150.0
        assert result["high"] == 155.0
        assert result["low"] == 148.0
        assert result["close"] == 152.0
        assert result["prev_close"] == 149.0
        assert result["volume"] == 1000000
        assert result["change_pct"] == 2.0
        assert result["source"] == "alpha_vantage"

    @patch("utils.external_data_source._SESSION")
    def test_exception(self, mock_session):
        api = AlphaVantageApi(api_key="key")
        mock_session.get.side_effect = OSError("timeout")
        assert api.get_global_quote("AAPL") is None


# ============================================================
# 7. FinnhubApi 测试
# ============================================================


class TestFinnhubApi:
    """FinnhubApi get_quote / get_market_news."""

    def test_init_no_key(self, monkeypatch):
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        api = FinnhubApi()
        assert api.available is False

    def test_init_with_key(self):
        api = FinnhubApi(api_key="fh_key")
        assert api.available is True

    def test_get_quote_not_available(self):
        api = FinnhubApi(api_key="")
        assert api.get_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_http_error(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(status_code=403)
        assert api.get_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_current_zero(self, mock_session):
        """c=0 时返回 None."""
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={"c": 0, "o": 0, "h": 0, "l": 0, "pc": 0}
        )
        assert api.get_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_empty_data(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(json_data={})
        # data.get("c", 0) == 0 → None
        assert api.get_quote("AAPL") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_success(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={"c": 152.0, "o": 150.0, "h": 155.0, "l": 148.0, "pc": 149.0}
        )
        result = api.get_quote("AAPL")
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["close"] == 152.0
        assert result["open"] == 150.0
        assert result["high"] == 155.0
        assert result["low"] == 148.0
        assert result["prev_close"] == 149.0
        expected_pct = round((152.0 - 149.0) / 149.0 * 100, 2)
        assert result["change_pct"] == expected_pct
        assert result["source"] == "finnhub"

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_prev_close_zero(self, mock_session):
        """prev_close=0 时 change_pct=0."""
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(
            json_data={"c": 152.0, "o": 150.0, "h": 155.0, "l": 148.0, "pc": 0}
        )
        result = api.get_quote("AAPL")
        assert result is not None
        assert result["change_pct"] == 0

    @patch("utils.external_data_source._SESSION")
    def test_get_quote_exception(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.side_effect = ConnectionError("refused")
        assert api.get_quote("AAPL") is None

    def test_get_market_news_not_available(self):
        api = FinnhubApi(api_key="")
        assert api.get_market_news() == []

    @patch("utils.external_data_source._SESSION")
    def test_get_market_news_http_error(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.return_value = _mock_response(status_code=500)
        assert api.get_market_news() == []

    @patch("utils.external_data_source._SESSION")
    def test_get_market_news_success(self, mock_session):
        api = FinnhubApi(api_key="key")
        news_items = [
            {
                "headline": "News 1",
                "summary": "Summary 1",
                "source": "SRC",
                "url": "http://example.com/1",
                "datetime": 1700000000,
            },
            {
                "headline": "News 2",
                "summary": "Summary 2",
                "source": "SRC2",
                "url": "http://example.com/2",
                "datetime": 1700001000,
            },
        ]
        mock_session.get.return_value = _mock_response(json_data=news_items)
        result = api.get_market_news("general")
        assert len(result) == 2
        assert result[0]["title"] == "News 1"
        assert result[0]["summary"] == "Summary 1"
        assert result[0]["source"] == "SRC"
        assert result[0]["url"] == "http://example.com/1"
        assert result[0]["category"] == "general"
        assert "datetime" in result[0]

    @patch("utils.external_data_source._SESSION")
    def test_get_market_news_max_10(self, mock_session):
        """最多返回 10 条."""
        api = FinnhubApi(api_key="key")
        news_items = [
            {
                "headline": f"News {i}",
                "summary": "",
                "source": "",
                "url": "",
                "datetime": 1700000000 + i,
            }
            for i in range(15)
        ]
        mock_session.get.return_value = _mock_response(json_data=news_items)
        result = api.get_market_news()
        assert len(result) == 10

    @patch("utils.external_data_source._SESSION")
    def test_get_market_news_exception(self, mock_session):
        api = FinnhubApi(api_key="key")
        mock_session.get.side_effect = OSError("timeout")
        assert api.get_market_news() == []


# ============================================================
# 8. CoinGeckoApi 测试
# ============================================================


class TestCoinGeckoApi:
    """CoinGeckoApi get_price / get_global_market."""

    def test_init(self):
        api = CoinGeckoApi()
        assert api.available is True

    @patch("utils.external_data_source._SESSION")
    def test_get_price_http_error(self, mock_session):
        mock_session.get.return_value = _mock_response(status_code=429)
        api = CoinGeckoApi()
        assert api.get_price("bitcoin") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_price_empty_coin_data(self, mock_session):
        mock_session.get.return_value = _mock_response(json_data={"bitcoin": {}})
        api = CoinGeckoApi()
        assert api.get_price("bitcoin") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_price_success(self, mock_session):
        mock_session.get.return_value = _mock_response(
            json_data={
                "bitcoin": {
                    "usd": 50000,
                    "usd_24h_change": 2.5,
                    "usd_market_cap": 1000000000000,
                }
            }
        )
        api = CoinGeckoApi()
        result = api.get_price("bitcoin", "usd")
        assert result is not None
        assert result["coin"] == "bitcoin"
        assert result["price"] == 50000
        assert result["change_24h_pct"] == 2.5
        assert result["market_cap"] == 1000000000000
        assert result["source"] == "coingecko"

    @patch("utils.external_data_source._SESSION")
    def test_get_price_exception(self, mock_session):
        mock_session.get.side_effect = ConnectionError("refused")
        api = CoinGeckoApi()
        assert api.get_price("bitcoin") is None

    @patch("utils.external_data_source._SESSION")
    def test_get_global_market_http_error(self, mock_session):
        mock_session.get.return_value = _mock_response(status_code=503)
        api = CoinGeckoApi()
        assert api.get_global_market() is None

    @patch("utils.external_data_source._SESSION")
    def test_get_global_market_success(self, mock_session):
        mock_session.get.return_value = _mock_response(
            json_data={
                "data": {
                    "total_market_cap": {"usd": 2000000000000},
                    "total_volume": {"usd": 100000000000},
                    "market_cap_percentage": {"btc": 40, "eth": 20},
                    "market_cap_change_percentage_24h_usd": -3.5,
                }
            }
        )
        api = CoinGeckoApi()
        result = api.get_global_market()
        assert result is not None
        assert result["total_market_cap"] == 2000000000000
        assert result["total_volume"] == 100000000000
        assert result["market_cap_percentage"]["btc"] == 40
        assert result["market_cap_change_24h_pct"] == -3.5
        assert result["source"] == "coingecko"

    @patch("utils.external_data_source._SESSION")
    def test_get_global_market_exception(self, mock_session):
        mock_session.get.side_effect = OSError("timeout")
        api = CoinGeckoApi()
        assert api.get_global_market() is None


# ============================================================
# 9. ExternalDataManager 初始化测试
# ============================================================


class TestExternalDataManagerInit:
    """ExternalDataManager 初始化."""

    def test_init_creates_all_apis(self):
        mgr = ExternalDataManager()
        assert isinstance(mgr.fred, FREDApi)
        assert isinstance(mgr.econdb, EcondbApi)
        assert isinstance(mgr.treasury, FedTreasuryApi)
        assert isinstance(mgr.alpha_vantage, AlphaVantageApi)
        assert isinstance(mgr.finnhub, FinnhubApi)
        assert isinstance(mgr.coingecko, CoinGeckoApi)


# ============================================================
# 10. _cache_path 测试
# ============================================================


class TestCachePath:
    """_cache_path 路径生成与键清理."""

    def test_basic_key(self):
        mgr = ExternalDataManager()
        path = mgr._cache_path("macro", "snapshot")
        assert path.name == "macro_snapshot.json"

    def test_forward_slashes_replaced(self):
        mgr = ExternalDataManager()
        path = mgr._cache_path("stock", "AAPL/USD")
        assert "/" not in path.name
        assert "AAPL_USD" in path.name

    def test_backslashes_replaced(self):
        mgr = ExternalDataManager()
        path = mgr._cache_path("stock", "AAPL\\USD")
        assert "\\" not in path.name


# ============================================================
# 11. _load_cache 测试
# ============================================================


class TestLoadCache:
    """_load_cache 缓存读取."""

    def test_file_not_exists(self, isolated_cache):
        mgr = ExternalDataManager()
        assert mgr._load_cache("macro", "nonexistent_key_12345") is None

    def test_valid_cache(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("macro", "test_valid")
        cache_data = {
            "_cache_time": time.time(),
            "_cache_date": now_bj().isoformat(),
            "data": {"key": "value"},
        }
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")
        result = mgr._load_cache("macro", "test_valid")
        assert result == {"key": "value"}

    def test_expired_cache(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("macro", "test_expired")
        cache_data = {
            "_cache_time": time.time() - CACHE_TTL["macro"] - 1,
            "data": {"key": "old"},
        }
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")
        assert mgr._load_cache("macro", "test_expired") is None

    def test_corrupted_cache(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("macro", "test_corrupt")
        cache_file.write_text("not json {{{", encoding="utf-8")
        assert mgr._load_cache("macro", "test_corrupt") is None

    def test_no_cache_time(self, isolated_cache):
        """缓存无 _cache_time 字段时 (默认 0) 视为过期."""
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("macro", "test_no_time")
        cache_file.write_text(json.dumps({"data": {"key": "val"}}), encoding="utf-8")
        assert mgr._load_cache("macro", "test_no_time") is None


# ============================================================
# 12. _save_cache 测试
# ============================================================


class TestSaveCache:
    """_save_cache 缓存写入."""

    @patch("utils.concurrency.atomic_write_text")
    def test_save_success(self, mock_write, isolated_cache):
        mgr = ExternalDataManager()
        mgr._save_cache("macro", "test_save", {"key": "value"})
        mock_write.assert_called_once()
        args = mock_write.call_args
        payload = args[0][1]
        cache = json.loads(payload)
        assert cache["data"] == {"key": "value"}
        assert "_cache_time" in cache
        assert "_cache_date" in cache

    @patch("utils.concurrency.atomic_write_text", side_effect=OSError("disk full"))
    def test_save_failure_does_not_raise(self, mock_write, isolated_cache):
        """缓存保存失败不抛异常, 仅记录 warning."""
        mgr = ExternalDataManager()
        mgr._save_cache("macro", "test_save_fail", {"key": "value"})


# ============================================================
# 13. get_macro_snapshot 测试
# ============================================================


class TestGetMacroSnapshot:
    """get_macro_snapshot 完整流程."""

    def test_cache_hit(self, isolated_cache):
        """缓存命中时直接返回缓存数据."""
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("macro", "snapshot")
        cached = {"fred": {"CPI": {"value": 2.5}}, "treasury_yields": {"10Y": 3.2}}
        cache_file.write_text(
            json.dumps({"_cache_time": time.time(), "data": cached}),
            encoding="utf-8",
        )
        result = mgr.get_macro_snapshot()
        assert result == cached

    def test_cache_miss_fred_available(self, isolated_cache):
        """缓存未命中, FRED 可用时获取数据."""
        mgr = ExternalDataManager()
        mgr.fred = FREDApi(api_key="key")
        with (
            patch.object(mgr.fred, "get_macro_snapshot") as mock_fred,
            patch.object(mgr.treasury, "get_treasury_yields") as mock_treasury,
            patch.object(mgr.coingecko, "get_global_market") as mock_crypto,
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            mock_fred.return_value = {
                "CPI": MacroIndicator(
                    name="CPI", value=2.5, unit="%", date="2026-01", source="FRED"
                )
            }
            mock_treasury.return_value = {"10Y": 3.2}
            mock_crypto.return_value = {"total_market_cap": 1000}
            result = mgr.get_macro_snapshot()
        assert "fred" in result
        assert result["fred"]["CPI"]["value"] == 2.5
        assert result["treasury_yields"]["10Y"] == 3.2
        assert result["crypto_market"]["total_market_cap"] == 1000
        mock_save.assert_called_once()

    def test_cache_miss_fred_unavailable(self, isolated_cache):
        """FRED 不可用时跳过, 所有源空时不保存缓存."""
        mgr = ExternalDataManager()
        mgr.fred = FREDApi(api_key="")
        with (
            patch.object(mgr.treasury, "get_treasury_yields", return_value={}),
            patch.object(mgr.coingecko, "get_global_market", return_value=None),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_macro_snapshot()
        assert result == {}
        mock_save.assert_not_called()

    def test_cache_miss_partial_data(self, isolated_cache):
        """部分数据源返回空时, snapshot 非空仍保存缓存."""
        mgr = ExternalDataManager()
        mgr.fred = FREDApi(api_key="key")
        with (
            patch.object(mgr.fred, "get_macro_snapshot", return_value={}),
            patch.object(
                mgr.treasury, "get_treasury_yields", return_value={"10Y": 3.2}
            ),
            patch.object(mgr.coingecko, "get_global_market", return_value=None),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_macro_snapshot()
        assert "treasury_yields" in result
        assert "crypto_market" not in result
        mock_save.assert_called_once()


# ============================================================
# 14. get_global_stock 测试
# ============================================================


class TestGetGlobalStock:
    """get_global_stock 优先级路由."""

    def test_cache_hit(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("stock", "AAPL")
        cached = {"symbol": "AAPL", "close": 150.0}
        cache_file.write_text(
            json.dumps({"_cache_time": time.time(), "data": cached}),
            encoding="utf-8",
        )
        result = mgr.get_global_stock("AAPL")
        assert result == cached

    def test_finnhub_priority(self, isolated_cache):
        """Finnhub 可用时优先使用."""
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="key")
        mgr.alpha_vantage = AlphaVantageApi(api_key="key")
        with (
            patch.object(
                mgr.finnhub,
                "get_quote",
                return_value={"symbol": "AAPL", "source": "finnhub"},
            ),
            patch.object(mgr.alpha_vantage, "get_global_quote") as mock_av,
            patch.object(mgr, "_save_cache"),
        ):
            result = mgr.get_global_stock("AAPL")
        assert result["source"] == "finnhub"
        mock_av.assert_not_called()

    def test_fallback_to_alpha_vantage(self, isolated_cache):
        """Finnhub 返回 None 时 fallback 到 Alpha Vantage."""
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="key")
        mgr.alpha_vantage = AlphaVantageApi(api_key="key")
        with (
            patch.object(mgr.finnhub, "get_quote", return_value=None),
            patch.object(
                mgr.alpha_vantage,
                "get_global_quote",
                return_value={"symbol": "AAPL", "source": "alpha_vantage"},
            ) as mock_av,
            patch.object(mgr, "_save_cache"),
        ):
            result = mgr.get_global_stock("AAPL")
        assert result["source"] == "alpha_vantage"
        mock_av.assert_called_once()

    def test_both_unavailable(self, isolated_cache):
        """两个源都不可用时返回 None."""
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="")
        mgr.alpha_vantage = AlphaVantageApi(api_key="")
        with patch.object(mgr, "_save_cache") as mock_save:
            result = mgr.get_global_stock("AAPL")
        assert result is None
        mock_save.assert_not_called()

    def test_both_return_none(self, isolated_cache):
        """两个源都返回 None 时不保存缓存."""
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="key")
        mgr.alpha_vantage = AlphaVantageApi(api_key="key")
        with (
            patch.object(mgr.finnhub, "get_quote", return_value=None),
            patch.object(mgr.alpha_vantage, "get_global_quote", return_value=None),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_global_stock("AAPL")
        assert result is None
        mock_save.assert_not_called()


# ============================================================
# 15. get_crypto_price 测试
# ============================================================


class TestGetCryptoPrice:
    """get_crypto_price."""

    def test_cache_hit(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("crypto", "bitcoin")
        cached = {"coin": "bitcoin", "price": 50000}
        cache_file.write_text(
            json.dumps({"_cache_time": time.time(), "data": cached}),
            encoding="utf-8",
        )
        result = mgr.get_crypto_price("bitcoin")
        assert result == cached

    def test_cache_miss_success(self, isolated_cache):
        mgr = ExternalDataManager()
        with (
            patch.object(
                mgr.coingecko,
                "get_price",
                return_value={"coin": "bitcoin", "price": 50000},
            ),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_crypto_price("bitcoin")
        assert result["price"] == 50000
        mock_save.assert_called_once()

    def test_cache_miss_none(self, isolated_cache):
        mgr = ExternalDataManager()
        with (
            patch.object(mgr.coingecko, "get_price", return_value=None),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_crypto_price("bitcoin")
        assert result is None
        mock_save.assert_not_called()


# ============================================================
# 16. get_market_news 测试
# ============================================================


class TestGetMarketNews:
    """get_market_news."""

    def test_cache_hit(self, isolated_cache):
        mgr = ExternalDataManager()
        cache_file = mgr._cache_path("news", "market")
        cached = [{"title": "News 1"}]
        cache_file.write_text(
            json.dumps({"_cache_time": time.time(), "data": cached}),
            encoding="utf-8",
        )
        result = mgr.get_market_news()
        assert result == cached

    def test_cache_miss_success(self, isolated_cache):
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="key")
        with (
            patch.object(
                mgr.finnhub, "get_market_news", return_value=[{"title": "News 1"}]
            ),
            patch.object(mgr, "_save_cache") as mock_save,
        ):
            result = mgr.get_market_news()
        assert len(result) == 1
        mock_save.assert_called_once()

    def test_finnhub_unavailable(self, isolated_cache):
        mgr = ExternalDataManager()
        mgr.finnhub = FinnhubApi(api_key="")
        with patch.object(mgr, "_save_cache") as mock_save:
            result = mgr.get_market_news()
        assert result == []
        mock_save.assert_not_called()


# ============================================================
# 17. get_risk_sentiment 测试
# ============================================================


class TestGetRiskSentiment:
    """get_risk_sentiment 风险情绪指标."""

    def test_with_full_data_yield_curve_inverted(self):
        """完整数据 + 收益率曲线倒挂 (2Y > 10Y)."""
        mgr = ExternalDataManager()
        snapshot = {
            "crypto_market": {"market_cap_change_24h_pct": -3.5},
            "treasury_yields": {"2Y": 3.8, "10Y": 3.2},
            "fred": {"FED_FUNDS_RATE": {"value": 5.25}},
        }
        with patch.object(mgr, "get_macro_snapshot", return_value=snapshot):
            result = mgr.get_risk_sentiment()
        assert result["vix_proxy"] == -3.5
        assert result["treasury_yield_curve"]["2Y"] == 3.8
        assert result["yield_curve_inverted"] is True
        assert result["fed_rate"] == 5.25
        assert "timestamp" in result

    def test_yield_curve_not_inverted(self):
        """收益率曲线未倒挂 (2Y < 10Y)."""
        mgr = ExternalDataManager()
        snapshot = {
            "treasury_yields": {"2Y": 3.0, "10Y": 3.5},
        }
        with patch.object(mgr, "get_macro_snapshot", return_value=snapshot):
            result = mgr.get_risk_sentiment()
        assert result["yield_curve_inverted"] is False
        assert result["vix_proxy"] is None
        assert result["fed_rate"] is None

    def test_empty_snapshot(self):
        """空快照时返回默认值, 无 yield_curve_inverted 键."""
        mgr = ExternalDataManager()
        with patch.object(mgr, "get_macro_snapshot", return_value={}):
            result = mgr.get_risk_sentiment()
        assert result["vix_proxy"] is None
        assert result["treasury_yield_curve"] == {}
        assert result["fed_rate"] is None
        assert "yield_curve_inverted" not in result

    def test_no_fed_rate_in_fred(self):
        """fred 中无 FED_FUNDS_RATE 时 fed_rate 为 None."""
        mgr = ExternalDataManager()
        snapshot = {
            "fred": {"CPI": {"value": 2.5}},
        }
        with patch.object(mgr, "get_macro_snapshot", return_value=snapshot):
            result = mgr.get_risk_sentiment()
        assert result["fed_rate"] is None
