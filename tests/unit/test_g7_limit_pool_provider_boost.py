"""
G7 Coverage Boost: utils/limit_pool_provider.py (384 lines, 0% -> target ~80%)
"""
from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.limit_pool_provider import LimitPoolData, LimitPoolProvider, get_limit_down_pool, get_limit_pool_provider, get_limit_up_pool


class TestLimitPoolData:
    def test_default_values(self):
        data = LimitPoolData(date="20260812")
        assert data.limit_up_codes == set()
        assert data.limit_down_codes == set()
        assert data.broken_codes == set()
        assert data.limit_up_detail == []
        assert data.limit_down_detail == []

    def test_custom_values(self):
        data = LimitPoolData(
            date="20260812",
            limit_up_codes={"000001.SZ"},
            limit_down_codes={"000002.SZ"},
            broken_codes={"000003.SZ"},
            limit_up_detail=[{"代码": "000001.SZ"}],
            limit_down_detail=[{"代码": "000002.SZ"}],
        )
        assert data.limit_up_codes == {"000001.SZ"}
        assert data.n_limit_up == 1
        assert data.n_limit_down == 1
        assert data.n_broken == 1

    def test_is_limit_up(self):
        data = LimitPoolData(date="20260812", limit_up_codes={"000001.SZ", "000002.SZ"})
        assert data.is_limit_up("000001.SZ") is True
        assert data.is_limit_up("000003.SZ") is False

    def test_is_limit_down(self):
        data = LimitPoolData(date="20260812", limit_down_codes={"000001.SZ"})
        assert data.is_limit_down("000001.SZ") is True
        assert data.is_limit_down("000002.SZ") is False

    def test_is_broken(self):
        data = LimitPoolData(date="20260812", broken_codes={"000001.SZ"})
        assert data.is_broken("000001.SZ") is True
        assert data.is_broken("000002.SZ") is False

    def test_repr(self):
        data = LimitPoolData(date="20260812", limit_up_codes={"000001.SZ"}, limit_down_codes={"000002.SZ"}, broken_codes={"000003.SZ"})
        repr_str = repr(data)
        assert "LimitPoolData" in repr_str
        assert "20260812" in repr_str


class TestLimitPoolProviderInit:
    def test_singleton(self):
        p1 = LimitPoolProvider()
        p2 = LimitPoolProvider()
        assert p1 is p2

    def test_initialized_only_once(self):
        p1 = LimitPoolProvider()
        p1._cache_ttl = 123
        p2 = LimitPoolProvider()
        assert p2._cache_ttl == 123

    def test_custom_cache_ttl(self):
        LimitPoolProvider._instance = None
        p = LimitPoolProvider(cache_ttl=600)
        assert p._cache_ttl == 600
        LimitPoolProvider._instance = None


class TestNormalizeDate:
    def test_datetime_input(self):
        dt = datetime(2026, 8, 12)
        assert LimitPoolProvider._normalize_date(dt) == "20260812"

    def test_yyyymmdd_string(self):
        assert LimitPoolProvider._normalize_date("20260812") == "20260812"

    def test_yyyymmdd_with_dash(self):
        assert LimitPoolProvider._normalize_date("2026-08-12") == "20260812"

    def test_yyyymmdd_with_slash(self):
        assert LimitPoolProvider._normalize_date("2026/08/12") == "20260812"

    def test_whitespace_trimmed(self):
        assert LimitPoolProvider._normalize_date(" 20260812 ") == "20260812"


class TestIsToday:
    def test_today(self):
        today = datetime.now().strftime("%Y%m%d")
        assert LimitPoolProvider._is_today(today) is True

    def test_not_today(self):
        assert LimitPoolProvider._is_today("20200101") is False


class TestGetTtl:
    def test_today_intraday(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        date_str = datetime.now().strftime("%Y%m%d")
        with patch.object(provider, "_is_today", return_value=True):
            with patch("utils.limit_pool_provider.datetime") as mock_dt:
                mock_now = datetime(2026, 8, 12, 10, 0, 0)
                mock_dt.now.return_value = mock_now
                mock_dt.weekday = datetime.weekday
                ttl = provider._get_ttl(date_str)
        assert ttl == LimitPoolProvider.INTRADAY_TTL

    def test_today_after_hours(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        date_str = datetime.now().strftime("%Y%m%d")
        with patch.object(provider, "_is_today", return_value=True):
            with patch("utils.limit_pool_provider.datetime") as mock_dt:
                mock_now = datetime(2026, 8, 12, 16, 0, 0)
                mock_dt.now.return_value = mock_now
                mock_dt.weekday = datetime.weekday
                ttl = provider._get_ttl(date_str)
        assert ttl == LimitPoolProvider.POST_MARKET_TTL

    def test_weekend(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        date_str = datetime.now().strftime("%Y%m%d")
        with patch.object(provider, "_is_today", return_value=True):
            with patch("utils.limit_pool_provider.datetime") as mock_dt:
                mock_now = datetime(2026, 8, 15, 10, 0, 0)
                mock_dt.now.return_value = mock_now
                mock_dt.weekday = datetime.weekday
                ttl = provider._get_ttl(date_str)
        assert ttl == LimitPoolProvider.POST_MARKET_TTL

    def test_historical_date(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        with patch.object(provider, "_is_today", return_value=False):
            ttl = provider._get_ttl("20260801")
        assert ttl == LimitPoolProvider.POST_MARKET_TTL


class TestGetPool:
    def test_akshare_unavailable_returns_empty(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {}
        provider._cache_lock = MagicMock()
        with patch.object(provider, "_get_ak_module", return_value=None):
            data = provider.get_pool("20260812")
        assert isinstance(data, LimitPoolData)
        assert data.limit_up_codes == set()
        assert data.limit_down_codes == set()

    def test_success_with_limit_up(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {}
        provider._cache_lock = MagicMock()
        df_zt = pd.DataFrame({"代码": ["000001", "000002"], "名称": ["A", "B"]})
        df_dt = pd.DataFrame({"代码": [], "名称": []})
        df_zbgc = pd.DataFrame({"代码": [], "名称": []})
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = df_zt
        mock_ak.stock_zt_pool_dt_em.return_value = df_dt
        mock_ak.stock_zt_pool_zbgc_em.return_value = df_zbgc
        with patch.object(provider, "_get_ak_module", return_value=mock_ak):
            data = provider.get_pool("20260812")
        assert data.limit_up_codes == {"000001", "000002"}
        assert data.limit_down_codes == set()
        assert data.broken_codes == set()

    def test_success_with_limit_down(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {}
        provider._cache_lock = MagicMock()
        df_zt = pd.DataFrame({"代码": [], "名称": []})
        df_dt = pd.DataFrame({"代码": ["000003"], "名称": ["C"]})
        df_zbgc = pd.DataFrame({"代码": [], "名称": []})
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = df_zt
        mock_ak.stock_zt_pool_dt_em.return_value = df_dt
        mock_ak.stock_zt_pool_zbgc_em.return_value = df_zbgc
        with patch.object(provider, "_get_ak_module", return_value=mock_ak):
            data = provider.get_pool("20260812")
        assert data.limit_down_codes == {"000003"}

    def test_api_failure_returns_empty_for_that_pool(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {}
        provider._cache_lock = MagicMock()
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.side_effect = RuntimeError("network error")
        mock_ak.stock_zt_pool_dt_em.return_value = pd.DataFrame({"代码": [], "名称": []})
        mock_ak.stock_zt_pool_zbgc_em.return_value = pd.DataFrame({"代码": [], "名称": []})
        with patch.object(provider, "_get_ak_module", return_value=mock_ak):
            data = provider.get_pool("20260812")
        assert data.limit_up_codes == set()
        assert data.limit_down_codes == set()

    def test_cache_hit(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        cached_data = LimitPoolData(date="20260812", limit_up_codes={"000001.SZ"})
        provider._cache = {"20260812": {"data": cached_data, "fetched_at": datetime.now()}}
        provider._cache_lock = MagicMock()
        with patch.object(provider, "_get_ak_module", return_value=MagicMock()):
            data = provider.get_pool("20260812")
        assert data is cached_data
        assert data.limit_up_codes == {"000001.SZ"}


class TestGetPoolsBatch:
    def test_batch_success(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {}
        provider._cache_lock = MagicMock()
        df_zt = pd.DataFrame({"代码": ["000001"], "名称": ["A"]})
        df_dt = pd.DataFrame({"代码": [], "名称": []})
        df_zbgc = pd.DataFrame({"代码": [], "名称": []})
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = df_zt
        mock_ak.stock_zt_pool_dt_em.return_value = df_dt
        mock_ak.stock_zt_pool_zbgc_em.return_value = df_zbgc
        with patch.object(provider, "_get_ak_module", return_value=mock_ak):
            result = provider.get_pools_batch(["20260810", "20260811", "20260812"])
        assert len(result) == 3
        assert "20260810" in result
        assert "20260811" in result
        assert "20260812" in result


class TestCrossValidate:
    def test_cross_validate(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        pool_data = LimitPoolData(date="20260812", limit_up_codes={"000001.SZ"})
        with patch.object(provider, "get_pool", return_value=pool_data):
            with patch("utils.price_limit_calculator.calc_limit_prices", return_value=(10.0, 9.0)):
                with patch("utils.price_limit_calculator.is_at_limit_up", return_value=True):
                    with patch("utils.price_limit_calculator.normalize_code", side_effect=lambda x: x):
                        result = provider.cross_validate_with_calc(
                            "20260812",
                            {"000001.SZ": 10.0},
                            st_codes=set(),
                        )
        assert result["000001.SZ"]["calc_limit_up"] is True
        assert result["000001.SZ"]["pool_limit_up"] is True
        assert result["000001.SZ"]["match"] is True


class TestCacheManagement:
    def test_clear_cache(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {"20260812": MagicMock()}
        provider._cache_lock = MagicMock()
        provider.clear_cache()
        assert provider._cache == {}

    def test_get_cache_info(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._cache = {"20260812": MagicMock(), "20260811": MagicMock()}
        provider._cache_lock = MagicMock()
        info = provider.get_cache_info()
        assert info["cache_size"] == 2
        assert "cached_dates" in info


class TestModuleFunctions:
    def test_get_limit_pool_provider(self):
        LimitPoolProvider._instance = None
        p = get_limit_pool_provider()
        assert isinstance(p, LimitPoolProvider)

    def test_get_limit_up_pool(self):
        LimitPoolProvider._instance = None
        with patch.object(LimitPoolProvider, "get_limit_up_pool", return_value={"000001.SZ"}) as mock_method:
            result = get_limit_up_pool("20260812")
        assert result == {"000001.SZ"}
        LimitPoolProvider._instance = None

    def test_get_limit_down_pool(self):
        LimitPoolProvider._instance = None
        with patch.object(LimitPoolProvider, "get_limit_down_pool", return_value={"000002.SZ"}) as mock_method:
            result = get_limit_down_pool("20260812")
        assert result == {"000002.SZ"}
        LimitPoolProvider._instance = None


class TestGetAkshare:
    def test_akshare_available(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._akshare_source = None
        mock_source = MagicMock()
        with patch("utils.akshare_data_source.get_akshare_source", return_value=mock_source):
            source = provider._get_akshare()
        assert source is mock_source
        assert provider._akshare_source is mock_source

    def test_akshare_unavailable(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        provider._akshare_source = None
        with patch("utils.akshare_data_source.get_akshare_source", side_effect=ImportError("not found")):
            with patch("utils.limit_pool_provider.logger") as mock_logger:
                source = provider._get_akshare()
        assert source is None
        assert provider._akshare_source is None
        mock_logger.warning.assert_called()


class TestGetAkModule:
    def test_akshare_imported(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        mock_ak = MagicMock()
        with patch.dict(sys.modules, {"akshare": MagicMock(return_value=mock_ak)}):
            with patch("builtins.__import__", return_value=mock_ak):
                module = provider._get_ak_module()
        assert module is mock_ak

    def test_akshare_not_installed(self):
        provider = LimitPoolProvider.__new__(LimitPoolProvider)
        provider._initialized = True
        with patch("utils.limit_pool_provider.logger") as mock_logger:
            module = provider._get_ak_module()
        assert module is None
        mock_logger.warning.assert_called()
