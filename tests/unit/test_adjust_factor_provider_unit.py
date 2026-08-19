"""
单元测试: utils/adjust_factor_provider.py
覆盖纯函数 + AdjustFactorProvider 静态方法 + 缓存/单例逻辑
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.adjust_factor_provider import (
    AdjustFactorProvider,
    align_prev_close_to_today,
    align_realtime_to_hfq,
    compute_adjusted_return,
    compute_aligned_return,
    get_adjust_factor_provider,
    hfq_to_unadjusted,
    unadjusted_to_hfq,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    AdjustFactorProvider._instance = None
    yield
    AdjustFactorProvider._instance = None


class TestUnadjustedToHfq:
    def test_basic_conversion(self):
        assert unadjusted_to_hfq(10.0, 2.0) == 20.0

    def test_factor_one(self):
        assert unadjusted_to_hfq(10.0, 1.0) == 10.0

    def test_zero_factor_returns_original(self):
        assert unadjusted_to_hfq(10.0, 0.0) == 10.0

    def test_negative_factor_returns_original(self):
        assert unadjusted_to_hfq(10.0, -1.0) == 10.0

    def test_none_price_returns_original(self):
        assert unadjusted_to_hfq(None, 2.0) is None

    def test_zero_price_returns_original(self):
        assert unadjusted_to_hfq(0.0, 2.0) == 0.0

    def test_negative_price_returns_original(self):
        assert unadjusted_to_hfq(-5.0, 2.0) == -5.0


class TestHfqToUnadjusted:
    def test_basic_conversion(self):
        assert hfq_to_unadjusted(20.0, 2.0) == 10.0

    def test_factor_one(self):
        assert hfq_to_unadjusted(10.0, 1.0) == 10.0

    def test_zero_factor_returns_original(self):
        assert hfq_to_unadjusted(10.0, 0.0) == 10.0

    def test_negative_factor_returns_original(self):
        assert hfq_to_unadjusted(10.0, -1.0) == 10.0

    def test_none_price_returns_original(self):
        assert hfq_to_unadjusted(None, 2.0) is None


class TestComputeAdjustedReturn:
    def test_positive_return(self):
        ret = compute_adjusted_return(10.0, 11.0, 1.0)
        assert ret == pytest.approx(0.1)

    def test_negative_return(self):
        ret = compute_adjusted_return(10.0, 9.0, 1.0)
        assert ret == pytest.approx(-0.1)

    def test_with_factor(self):
        ret = compute_adjusted_return(20.0, 11.0, 2.0)
        assert ret == pytest.approx(0.1)

    def test_invalid_prev_close(self):
        assert compute_adjusted_return(0.0, 10.0, 1.0) == 0.0
        assert compute_adjusted_return(-1.0, 10.0, 1.0) == 0.0
        assert compute_adjusted_return(None, 10.0, 1.0) == 0.0

    def test_invalid_realtime(self):
        assert compute_adjusted_return(10.0, 0.0, 1.0) == 0.0
        assert compute_adjusted_return(10.0, -1.0, 1.0) == 0.0
        assert compute_adjusted_return(10.0, None, 1.0) == 0.0


class TestToDailySymbol:
    def test_sh_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("600519.SH") == "sh600519"

    def test_sz_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("000001.SZ") == "sz000001"

    def test_bj_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("830879.BJ") == "bj830879"

    def test_already_prefixed(self):
        assert AdjustFactorProvider._to_daily_symbol("sh600519") == "sh600519"
        assert AdjustFactorProvider._to_daily_symbol("sz000001") == "sz000001"

    def test_pure_number_sh(self):
        assert AdjustFactorProvider._to_daily_symbol("600519") == "sh600519"
        assert AdjustFactorProvider._to_daily_symbol("900901") == "sh900901"

    def test_pure_number_sz(self):
        assert AdjustFactorProvider._to_daily_symbol("000001") == "sz000001"
        assert AdjustFactorProvider._to_daily_symbol("300001") == "sz300001"

    def test_pure_number_bj(self):
        assert AdjustFactorProvider._to_daily_symbol("430001") == "bj430001"
        assert AdjustFactorProvider._to_daily_symbol("830879") == "bj830879"

    def test_invalid_code(self):
        assert AdjustFactorProvider._to_daily_symbol("abc") is None
        assert AdjustFactorProvider._to_daily_symbol("") is None

    def test_lowercase_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("600519.sh") == "sh600519"


class TestNormalizeFactorDf:
    def test_with_hfq_factor_column(self):
        df = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "hfq_factor": [1.0, 1.05],
        })
        result = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert result is not None
        assert len(result) == 2
        assert "date" in result.columns
        assert "hfq_factor" in result.columns

    def test_with_qfq_factor_column(self):
        df = pd.DataFrame({
            "date": ["2026-01-01"],
            "qfq_factor": [1.0],
        })
        result = AdjustFactorProvider._normalize_factor_df(df, "000001.SZ")
        assert result is not None

    def test_no_date_column(self):
        df = pd.DataFrame({"hfq_factor": [1.0]})
        result = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert result is None

    def test_no_factor_column(self):
        df = pd.DataFrame({"date": ["2026-01-01"]})
        result = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert result is None

    def test_negative_factor_replaced(self):
        df = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "hfq_factor": [1.0, -0.5],
        })
        result = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert result is not None
        assert result["hfq_factor"].iloc[1] == 1.0

    def test_sorted_by_date(self):
        df = pd.DataFrame({
            "date": ["2026-01-03", "2026-01-01", "2026-01-02"],
            "hfq_factor": [1.1, 1.0, 1.05],
        })
        result = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert result is not None
        assert result["date"].iloc[0] < result["date"].iloc[1] < result["date"].iloc[2]


class TestAdjustFactorProviderInit:
    def test_singleton(self):
        p1 = AdjustFactorProvider()
        p2 = AdjustFactorProvider()
        assert p1 is p2

    def test_custom_cache_ttl(self):
        p = AdjustFactorProvider(cache_ttl=3600)
        assert p._cache_ttl == 3600

    def test_default_cache_ttl(self):
        p = AdjustFactorProvider()
        assert p._cache_ttl == 86400


class TestGetHfqFactor:
    def test_returns_default_on_empty_series(self):
        p = AdjustFactorProvider()
        with patch.object(p, "get_hfq_factor_series", return_value=pd.DataFrame()):
            assert p.get_hfq_factor("600519.SH") == 1.0

    def test_returns_latest_factor(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "hfq_factor": [1.0, 1.05],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            assert p.get_hfq_factor("600519.SH") == pytest.approx(1.05)

    def test_historical_date(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.0, 1.05, 1.10],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            factor = p.get_hfq_factor("600519.SH", date="2026-01-02")
            assert factor == pytest.approx(1.05)

    def test_date_before_all_records(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.05, 1.10],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            factor = p.get_hfq_factor("600519.SH", date="2025-12-31")
            assert factor == pytest.approx(1.05)

    def test_invalid_date_returns_default(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01"]),
            "hfq_factor": [1.0],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            factor = p.get_hfq_factor("600519.SH", date="invalid")
            assert factor == 1.0


class TestIsExDividendDate:
    def test_factor_change_detected(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.0, 1.0, 1.10],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            assert p.is_ex_dividend_date("600519.SH", date="2026-01-03") is True

    def test_no_factor_change(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.0, 1.0, 1.0],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            assert p.is_ex_dividend_date("600519.SH", date="2026-01-03") is False

    def test_insufficient_data(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01"]),
            "hfq_factor": [1.0],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            assert p.is_ex_dividend_date("600519.SH") is False

    def test_empty_series(self):
        p = AdjustFactorProvider()
        with patch.object(p, "get_hfq_factor_series", return_value=pd.DataFrame()):
            assert p.is_ex_dividend_date("600519.SH") is False


class TestGetAlignedPrevClose:
    def test_non_ex_dividend_returns_original(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.0, 1.0, 1.0],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            result = p.get_aligned_prev_close("600519.SH", 10.0, date="2026-01-03")
            assert result == pytest.approx(10.0)

    def test_ex_dividend_adjusts(self):
        p = AdjustFactorProvider()
        series = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "hfq_factor": [1.0, 1.0, 1.10],
        })
        with patch.object(p, "get_hfq_factor_series", return_value=series):
            result = p.get_aligned_prev_close("600519.SH", 10.0, date="2026-01-03")
            assert result != pytest.approx(10.0)

    def test_invalid_prev_close(self):
        p = AdjustFactorProvider()
        assert p.get_aligned_prev_close("600519.SH", 0.0) == 0.0
        assert p.get_aligned_prev_close("600519.SH", -1.0) == -1.0


class TestCacheManagement:
    def test_clear_cache(self):
        p = AdjustFactorProvider()
        p._cache["test"] = {"factor": 1.0}
        p.clear_cache()
        assert len(p._cache) == 0

    def test_get_cache_info(self):
        p = AdjustFactorProvider()
        p._cache["600519.SH"] = {"factor": 1.0}
        info = p.get_cache_info()
        assert info["cached_symbols"] == 1
        assert "600519.SH" in info["symbols"]
        assert info["cache_ttl_seconds"] == 86400


class TestGetFactorsBatch:
    def test_batch(self):
        p = AdjustFactorProvider()
        with patch.object(p, "get_hfq_factor", side_effect=[1.0, 1.05, 1.10]):
            result = p.get_factors_batch(["a", "b", "c"])
        assert result == {"a": 1.0, "b": 1.05, "c": 1.10}


class TestModuleFunctions:
    def test_get_adjust_factor_provider_singleton(self):
        p1 = get_adjust_factor_provider()
        p2 = get_adjust_factor_provider()
        assert p1 is p2

    def test_align_realtime_to_hfq(self):
        with patch("utils.adjust_factor_provider.get_adjust_factor_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.get_hfq_factor.return_value = 2.0
            mock_get.return_value = mock_provider
            result = align_realtime_to_hfq(10.0, "600519.SH")
            assert result == 20.0

    def test_compute_aligned_return(self):
        with patch("utils.adjust_factor_provider.get_adjust_factor_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.get_hfq_factor.return_value = 1.0
            mock_get.return_value = mock_provider
            result = compute_aligned_return(10.0, 11.0, "600519.SH")
            assert result == pytest.approx(0.1)

    def test_align_prev_close_to_today(self):
        with patch("utils.adjust_factor_provider.get_adjust_factor_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.get_aligned_prev_close.return_value = 9.5
            mock_get.return_value = mock_provider
            result = align_prev_close_to_today(10.0, "600519.SH")
            assert result == 9.5
