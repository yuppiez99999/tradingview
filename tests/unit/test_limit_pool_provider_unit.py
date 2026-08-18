"""test_limit_pool_provider_unit.py — 涨停池/跌停池数据提供器单元测试

覆盖要点:
    - LimitPoolData (构造/属性/is_limit_up/down/broken/__repr__)
    - LimitPoolProvider (单例/构造/_normalize_date/_is_today/_get_ttl)
    - get_pool (akshare不可用→空池)
    - get_limit_up_pool / get_limit_down_pool / get_broken_pool
    - get_pools_batch
    - clear_cache / get_cache_info
    - 便捷函数 get_limit_pool_provider / get_limit_up_pool / get_limit_down_pool
"""
from __future__ import annotations

from datetime import datetime

import pytest

from utils.limit_pool_provider import (
    LimitPoolData,
    LimitPoolProvider,
    get_limit_down_pool,
    get_limit_pool_provider,
    get_limit_up_pool,
)


# ============================================================
# LimitPoolData
# ============================================================


class TestLimitPoolData:
    @pytest.mark.unit
    def test_defaults(self):
        d = LimitPoolData(date="20260818")
        assert d.limit_up_codes == set()
        assert d.limit_down_codes == set()
        assert d.broken_codes == set()
        assert d.n_limit_up == 0
        assert d.n_limit_down == 0
        assert d.n_broken == 0

    @pytest.mark.unit
    def test_with_data(self):
        d = LimitPoolData(
            date="20260818",
            limit_up_codes={"600519", "000858"},
            limit_down_codes={"601318"},
            broken_codes={"300308"},
        )
        assert d.n_limit_up == 2
        assert d.n_limit_down == 1
        assert d.n_broken == 1

    @pytest.mark.unit
    def test_is_limit_up(self):
        d = LimitPoolData(date="20260818", limit_up_codes={"600519"})
        assert d.is_limit_up("600519") is True
        assert d.is_limit_up("000858") is False

    @pytest.mark.unit
    def test_is_limit_down(self):
        d = LimitPoolData(date="20260818", limit_down_codes={"601318"})
        assert d.is_limit_down("601318") is True
        assert d.is_limit_down("600519") is False

    @pytest.mark.unit
    def test_is_broken(self):
        d = LimitPoolData(date="20260818", broken_codes={"300308"})
        assert d.is_broken("300308") is True
        assert d.is_broken("600519") is False

    @pytest.mark.unit
    def test_repr(self):
        d = LimitPoolData(date="20260818", limit_up_codes={"A"}, limit_down_codes={"B"})
        r = repr(d)
        assert "20260818" in r
        assert "涨停=1" in r


# ============================================================
# LimitPoolProvider 构造与单例
# ============================================================


class TestConstruction:
    @pytest.mark.unit
    def test_singleton(self):
        p1 = LimitPoolProvider()
        p2 = LimitPoolProvider()
        assert p1 is p2

    @pytest.mark.unit
    def test_default_ttl(self):
        p = LimitPoolProvider()
        assert p._cache_ttl == LimitPoolProvider.INTRADAY_TTL


# ============================================================
# 日期工具
# ============================================================


class TestDateUtils:
    @pytest.mark.unit
    def test_normalize_date_string_compact(self):
        assert LimitPoolProvider._normalize_date("20260818") == "20260818"

    @pytest.mark.unit
    def test_normalize_date_string_dashed(self):
        assert LimitPoolProvider._normalize_date("2026-08-18") == "20260818"

    @pytest.mark.unit
    def test_normalize_date_string_slashed(self):
        assert LimitPoolProvider._normalize_date("2026/08/18") == "20260818"

    @pytest.mark.unit
    def test_normalize_date_datetime(self):
        dt = datetime(2026, 8, 18)
        assert LimitPoolProvider._normalize_date(dt) == "20260818"

    @pytest.mark.unit
    def test_is_today(self):
        today = datetime.now().strftime("%Y%m%d")
        assert LimitPoolProvider._is_today(today) is True
        assert LimitPoolProvider._is_today("20200101") is False

    @pytest.mark.unit
    def test_get_ttl_today(self):
        p = LimitPoolProvider()
        today = datetime.now().strftime("%Y%m%d")
        ttl = p._get_ttl(today)
        # 今天可能是盘中或盘后, 但 TTL 应为正值
        assert ttl > 0

    @pytest.mark.unit
    def test_get_ttl_historical(self):
        p = LimitPoolProvider()
        ttl = p._get_ttl("20200101")
        assert ttl == LimitPoolProvider.POST_MARKET_TTL


# ============================================================
# get_pool (akshare 不可用 → 空池)
# ============================================================


class TestGetPool:
    @pytest.mark.unit
    def test_returns_empty_when_akshare_unavailable(self):
        p = LimitPoolProvider()
        p.clear_cache()
        data = p.get_pool("20200101")
        assert isinstance(data, LimitPoolData)
        # akshare 不可用 → 空池
        assert data.n_limit_up == 0
        assert data.n_limit_down == 0

    @pytest.mark.unit
    def test_get_limit_up_pool(self):
        p = LimitPoolProvider()
        p.clear_cache()
        codes = p.get_limit_up_pool("20200101")
        assert isinstance(codes, set)

    @pytest.mark.unit
    def test_get_limit_down_pool(self):
        p = LimitPoolProvider()
        p.clear_cache()
        codes = p.get_limit_down_pool("20200101")
        assert isinstance(codes, set)

    @pytest.mark.unit
    def test_get_broken_pool(self):
        p = LimitPoolProvider()
        p.clear_cache()
        codes = p.get_broken_pool("20200101")
        assert isinstance(codes, set)


# ============================================================
# 批量
# ============================================================


class TestBatch:
    @pytest.mark.unit
    def test_get_pools_batch(self):
        p = LimitPoolProvider()
        p.clear_cache()
        result = p.get_pools_batch(["20200101", "20200102"])
        assert len(result) == 2
        assert "20200101" in result
        assert "20200102" in result

    @pytest.mark.unit
    def test_get_pools_batch_with_datetime(self):
        p = LimitPoolProvider()
        p.clear_cache()
        result = p.get_pools_batch([datetime(2020, 1, 1)])
        assert "20200101" in result


# ============================================================
# 缓存管理
# ============================================================


class TestCache:
    @pytest.mark.unit
    def test_clear_cache(self):
        p = LimitPoolProvider()
        p.get_pool("20200101")
        p.clear_cache()
        info = p.get_cache_info()
        assert info["cache_size"] == 0

    @pytest.mark.unit
    def test_cache_info(self):
        p = LimitPoolProvider()
        info = p.get_cache_info()
        assert "cached_dates" in info
        assert "cache_size" in info
        assert "ttl_intraday" in info
        assert "ttl_post_market" in info


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    @pytest.mark.unit
    def test_get_limit_pool_provider(self):
        p = get_limit_pool_provider()
        assert isinstance(p, LimitPoolProvider)

    @pytest.mark.unit
    def test_get_limit_up_pool_func(self):
        LimitPoolProvider().clear_cache()
        codes = get_limit_up_pool("20200101")
        assert isinstance(codes, set)

    @pytest.mark.unit
    def test_get_limit_down_pool_func(self):
        LimitPoolProvider().clear_cache()
        codes = get_limit_down_pool("20200101")
        assert isinstance(codes, set)