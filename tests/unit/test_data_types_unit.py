"""test_data_types_unit.py — 数据通用类型与转换工具单元测试

覆盖要点:
    - safe_float (None/空/NaN/bool/字符串/数值)
    - safe_int
    - normalize_stock_code (各种格式)
    - get_market_tag (cn/hk/us/jp/kr/tw/unknown)
    - get_currency_tag
    - QuoteResult (is_ok/price_safe)
    - SourceHealth (to_dict)
"""

from __future__ import annotations

import pytest

from utils.data_types import (
    QuoteResult,
    SourceHealth,
    get_currency_tag,
    get_market_tag,
    normalize_stock_code,
    safe_float,
    safe_int,
)

# ============================================================
# safe_float
# ============================================================


class TestSafeFloat:
    @pytest.mark.unit
    def test_normal_float(self):
        assert safe_float(3.14) == 3.14

    @pytest.mark.unit
    def test_int(self):
        assert safe_float(42) == 42.0

    @pytest.mark.unit
    def test_numeric_string(self):
        assert safe_float("3.14") == 3.14

    @pytest.mark.unit
    def test_none_returns_default(self):
        assert safe_float(None, default=0.0) == 0.0

    @pytest.mark.unit
    def test_none_default_none(self):
        assert safe_float(None) is None

    @pytest.mark.unit
    def test_empty_string(self):
        assert safe_float("", default=-1.0) == -1.0

    @pytest.mark.unit
    def test_dash_string(self):
        assert safe_float("-", default=-1.0) == -1.0

    @pytest.mark.unit
    def test_double_dash(self):
        assert safe_float("--", default=-1.0) == -1.0

    @pytest.mark.unit
    def test_null_string(self):
        assert safe_float("null", default=-1.0) == -1.0

    @pytest.mark.unit
    def test_nan_string(self):
        assert safe_float("nan", default=-1.0) == -1.0

    @pytest.mark.unit
    def test_nan_value(self):
        assert safe_float(float("nan"), default=-1.0) == -1.0

    @pytest.mark.unit
    def test_bool_returns_default(self):
        """bool 是 int 子类, 但应返回 default"""
        assert safe_float(True, default=-1.0) == -1.0
        assert safe_float(False, default=-1.0) == -1.0

    @pytest.mark.unit
    def test_invalid_string(self):
        assert safe_float("abc", default=-1.0) == -1.0


# ============================================================
# safe_int
# ============================================================


class TestSafeInt:
    @pytest.mark.unit
    def test_normal_int(self):
        assert safe_int(42) == 42

    @pytest.mark.unit
    def test_float_string(self):
        assert safe_int("123.0") == 123

    @pytest.mark.unit
    def test_none_returns_default(self):
        assert safe_int(None, default=-1) == -1

    @pytest.mark.unit
    def test_invalid_returns_default(self):
        assert safe_int("abc", default=-1) == -1


# ============================================================
# normalize_stock_code
# ============================================================


class TestNormalizeStockCode:
    @pytest.mark.unit
    def test_sh_main_board(self):
        assert normalize_stock_code("600000") == "sh600000"

    @pytest.mark.unit
    def test_sz_main_board(self):
        assert normalize_stock_code("000001") == "sz000001"

    @pytest.mark.unit
    def test_gem(self):
        assert normalize_stock_code("300750") == "sz300750"

    @pytest.mark.unit
    def test_etf_510(self):
        assert normalize_stock_code("510300") == "sh510300"

    @pytest.mark.unit
    def test_bse(self):
        assert normalize_stock_code("430047") == "bj430047"

    @pytest.mark.unit
    def test_already_prefixed(self):
        assert normalize_stock_code("sh600000") == "sh600000"
        assert normalize_stock_code("SZ000001") == "SZ000001"

    @pytest.mark.unit
    def test_hk_5digit(self):
        """5位纯数字视为港股, 返回原样"""
        assert normalize_stock_code("00700") == "00700"

    @pytest.mark.unit
    def test_empty(self):
        assert normalize_stock_code("") == ""

    @pytest.mark.unit
    def test_none(self):
        assert normalize_stock_code(None) == ""


# ============================================================
# get_market_tag
# ============================================================


class TestGetMarketTag:
    @pytest.mark.unit
    def test_cn(self):
        assert get_market_tag("sh600000") == "cn"

    @pytest.mark.unit
    def test_hk_prefixed(self):
        assert get_market_tag("hk00700") == "hk"

    @pytest.mark.unit
    def test_hk_5digit(self):
        assert get_market_tag("00700") == "hk"

    @pytest.mark.unit
    def test_us(self):
        assert get_market_tag("usAAPL") == "us"

    @pytest.mark.unit
    def test_empty(self):
        assert get_market_tag("") == "unknown"

    @pytest.mark.unit
    def test_none(self):
        assert get_market_tag(None) == "unknown"


# ============================================================
# get_currency_tag
# ============================================================


class TestGetCurrencyTag:
    @pytest.mark.unit
    def test_cny(self):
        assert get_currency_tag("sh600000") == "CNY"

    @pytest.mark.unit
    def test_hkd(self):
        assert get_currency_tag("hk00700") == "HKD"

    @pytest.mark.unit
    def test_usd(self):
        assert get_currency_tag("usAAPL") == "USD"


# ============================================================
# QuoteResult
# ============================================================


class TestQuoteResult:
    @pytest.mark.unit
    def test_ok(self):
        q = QuoteResult(code="600000", price=10.0)
        assert q.is_ok is True
        assert q.price_safe == 10.0

    @pytest.mark.unit
    def test_error_not_ok(self):
        q = QuoteResult(code="600000", error="timeout")
        assert q.is_ok is False

    @pytest.mark.unit
    def test_none_price_not_ok(self):
        q = QuoteResult(code="600000", price=None)
        assert q.is_ok is False
        assert q.price_safe == 0.0

    @pytest.mark.unit
    def test_default_values(self):
        q = QuoteResult(code="600000")
        assert q.price is None
        assert q.source == ""
        assert q.is_fallback is False
        assert q.error is None


# ============================================================
# SourceHealth
# ============================================================


class TestSourceHealth:
    @pytest.mark.unit
    def test_to_dict(self):
        h = SourceHealth(
            code="600000",
            available=True,
            failure_count=0,
            last_error=None,
            latency_ms=50.0,
            last_checked_at="2026-01-01",
        )
        d = h.to_dict()
        assert d["code"] == "600000"
        assert d["available"] is True
        assert d["failure_count"] == 0
        assert d["latency_ms"] == 50.0

    @pytest.mark.unit
    def test_defaults(self):
        h = SourceHealth(code="600000", available=True, failure_count=0)
        assert h.last_error is None
        assert h.latency_ms is None
        assert h.last_checked_at is None
