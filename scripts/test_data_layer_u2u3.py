#!/usr/bin/env python3
"""W6.6.3 数据层增强验证 · 涨停池/跌停池 + 停牌公告 + 复权因子交叉校验

验证内容:
1. LimitPoolProvider: 单例 + 缓存 + Fail-Open 降级
2. 涨停池/跌停池数据结构: LimitPoolData 完整性
3. 日期归一化: 支持 YYYYMMDD / YYYY-MM-DD / datetime
4. 批量预热: get_pools_batch
5. build_backtest_data_from_ohlcv 集成 limit_pool_provider 参数
6. AKShareDataSource.get_suspend_list 接口存在性 + Fail-Open
7. 交叉校验: cross_validate_with_calc 计算与涨停池对比
8. 确定性: 同输入同输出

注意: akshare 实际 API 调用在无网络/非交易时段可能返回空, 验证聚焦接口完整性和降级安全性.
"""

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.limit_pool_provider import (
    LimitPoolProvider,
    LimitPoolData,
    get_limit_pool_provider,
    get_limit_up_pool,
    get_limit_down_pool,
)
from utils.price_limit_calculator import (
    build_backtest_data_from_ohlcv,
    calc_limit_prices,
    normalize_code,
)
from utils.akshare_data_source import AKShareDataSource


# ============================================================
# 合成数据
# ============================================================

def make_synthetic_price_data(n_days: int = 10, n_stocks: int = 5) -> dict[str, pd.DataFrame]:
    """构造 n 只股票 × n_days 天 OHLCV DataFrame"""
    rng = np.random.default_rng(20260812)
    price_data = {}
    for i in range(n_stocks):
        code = f"{600000 + i:06d}"
        start = 10.0 + 2.0 * i
        closes = (start * np.cumprod(1 + rng.normal(0, 0.02, size=n_days))).tolist()
        df = pd.DataFrame({
            "open": [c * 0.99 for c in closes],
            "high": [c * 1.02 for c in closes],
            "low": [c * 0.98 for c in closes],
            "close": closes,
            "volume": (1e6 * rng.lognormal(0, 0.4, size=n_days)).tolist(),
        }, index=pd.date_range("2026-08-01", periods=n_days, freq="B"))
        price_data[code] = df
    return price_data


# ============================================================
# 测试
# ============================================================

def test_limit_pool_provider_singleton():
    """测试 1: LimitPoolProvider 单例 + 缓存"""
    print("\n[测试 1] LimitPoolProvider 单例 + 缓存")
    p1 = LimitPoolProvider()
    p2 = LimitPoolProvider()
    assert p1 is p2, "LimitPoolProvider 应为单例"

    # 缓存初始为空
    info = p1.get_cache_info()
    assert info["cache_size"] == 0
    assert info["ttl_intraday"] == 300
    assert info["ttl_post_market"] == 86400
    print(f"  单例 ✓, 缓存: {info}")

    # 清除缓存
    p1.clear_cache()
    assert p1.get_cache_info()["cache_size"] == 0
    print("  clear_cache ✓")


def test_limit_pool_data_structure():
    """测试 2: LimitPoolData 数据结构"""
    print("\n[测试 2] LimitPoolData 数据结构")
    data = LimitPoolData(
        date="20260812",
        limit_up_codes={"600000", "000001"},
        limit_down_codes={"300750"},
        broken_codes={"600519"},
    )
    assert data.date == "20260812"
    assert data.n_limit_up == 2
    assert data.n_limit_down == 1
    assert data.n_broken == 1
    assert data.is_limit_up("600000")
    assert not data.is_limit_up("999999")
    assert data.is_limit_down("300750")
    assert data.is_broken("600519")
    print(f"  {data}")

    # 空池 (Fail-Open 降级输出)
    empty = LimitPoolData(date="20260812")
    assert empty.n_limit_up == 0
    assert empty.n_limit_down == 0
    assert not empty.is_limit_up("600000")
    print(f"  空池: {empty}")


def test_date_normalization():
    """测试 3: 日期归一化"""
    print("\n[测试 3] 日期归一化")
    provider = LimitPoolProvider()

    # YYYYMMDD
    assert provider._normalize_date("20260812") == "20260812"
    # YYYY-MM-DD
    assert provider._normalize_date("2026-08-12") == "20260812"
    # datetime 对象
    dt = datetime(2026, 8, 12)
    assert provider._normalize_date(dt) == "20260812"
    print("  YYYYMMDD / YYYY-MM-DD / datetime 全部归一化 ✓")

    # TTL 选择
    today = datetime.now().strftime("%Y%m%d")
    ttl_today = provider._get_ttl(today)
    # 今天可能盘中或盘后, 都应 > 0
    assert ttl_today > 0
    ttl_hist = provider._get_ttl("20260101")
    assert ttl_hist == 86400  # 历史数据 TTL=24h
    print(f"  TTL: today={ttl_today}s, history={ttl_hist}s ✓")


def test_fail_open_degradation():
    """测试 4: Fail-Open 降级 (akshare 不可用时返回空池)"""
    print("\n[测试 4] Fail-Open 降级")
    provider = LimitPoolProvider()
    # _get_ak_module 可能返回 None (akshare 未安装或不可用)
    # get_pool 应不抛异常, 返回空 LimitPoolData
    pool = provider.get_pool("20260812")
    assert isinstance(pool, LimitPoolData)
    assert pool.date == "20260812"
    # 数据不可用时池为空 (不抛异常)
    print(f"  get_pool 返回: {pool} (Fail-Open ✓)")

    # 便捷函数
    zt = get_limit_up_pool("20260812")
    dt = get_limit_down_pool("20260812")
    assert isinstance(zt, set)
    assert isinstance(dt, set)
    print(f"  get_limit_up_pool={len(zt)}, get_limit_down_pool={len(dt)} ✓")


def test_batch_preheat():
    """测试 5: 批量预热"""
    print("\n[测试 5] 批量预热")
    provider = LimitPoolProvider()
    provider.clear_cache()

    dates = ["20260810", "20260811", "20260812"]
    pools = provider.get_pools_batch(dates)

    assert len(pools) == 3
    for d in dates:
        assert d in pools
        assert isinstance(pools[d], LimitPoolData)
    print(f"  批量获取 {len(dates)} 天: {list(pools.keys())} ✓")


def test_build_backtest_integration():
    """测试 6: build_backtest_data_from_ohlcv 集成 limit_pool_provider"""
    print("\n[测试 6] build_backtest_data_from_ohlcv 集成")
    price_data = make_synthetic_price_data(n_days=10, n_stocks=5)

    # 不提供 limit_pool_provider (向后兼容)
    data_without_pool = build_backtest_data_from_ohlcv(price_data)
    assert len(data_without_pool) > 0
    assert "limit_up_prices" in data_without_pool[0]
    assert "limit_up_pool" not in data_without_pool[0]
    print(f"  无 provider: {len(data_without_pool)} 天, 含 limit_up_prices, 无 limit_up_pool ✓")

    # 提供 limit_pool_provider (注入涨停池字段)
    provider = LimitPoolProvider()
    data_with_pool = build_backtest_data_from_ohlcv(
        price_data, limit_pool_provider=provider
    )
    assert len(data_with_pool) > 0
    # 即使 akshare 不可用, 也应注入 limit_up_pool 字段 (空集合)
    assert "limit_up_pool" in data_with_pool[0]
    assert "limit_down_pool" in data_with_pool[0]
    assert "broken_pool" in data_with_pool[0]
    # limit_up_pool 应为 set 类型
    assert isinstance(data_with_pool[0]["limit_up_pool"], set)
    print(f"  有 provider: {len(data_with_pool)} 天, 含 limit_up_pool/limit_down_pool/broken_pool ✓")


def test_akshare_suspend_list():
    """测试 7: AKShareDataSource.get_suspend_list 接口"""
    print("\n[测试 7] AKShareDataSource.get_suspend_list")
    # 不实际调用 akshare (可能无网络), 仅验证接口存在性和 Fail-Open
    ds = AKShareDataSource()
    # get_suspend_list 应存在
    assert hasattr(ds, "get_suspend_list")
    # akshare 不可用时返回空 dict (不抛异常)
    # 注意: 如果 akshare 已安装, 这里可能实际调用 API
    # 我们只验证返回类型
    try:
        result = ds.get_suspend_list()
        assert isinstance(result, dict)
        print(f"  get_suspend_list() → dict, {len(result)} 只停牌 ✓")
    except Exception as e:
        # Fail-Open: 任何异常都不应抛出
        print(f"  ⚠ 意外异常 (应 Fail-Open): {e}")
        raise


def test_cross_validate():
    """测试 8: 交叉校验接口"""
    print("\n[测试 8] 交叉校验 cross_validate_with_calc")
    provider = LimitPoolProvider()

    # 模拟 prev_closes
    prev_closes = {
        "600000": 10.00,
        "000001": 15.00,
        "300750": 200.00,
        "688981": 50.00,
    }
    # 调用交叉校验 (涨停池不可用时, pool_up 全 False, calc_up 仍正常计算)
    result = provider.cross_validate_with_calc(
        date="20260812",
        prev_closes=prev_closes,
        st_codes=set(),
    )
    assert len(result) == 4
    for code, vals in result.items():
        assert "calc_limit_up" in vals
        assert "pool_limit_up" in vals
        assert "match" in vals
    # calc_limit_up: prev_close > 0 → lu > 0 → True
    assert all(v["calc_limit_up"] for v in result.values())
    # pool_limit_up: 涨停池不可用 → 全 False
    assert not any(v["pool_limit_up"] for v in result.values())
    print(f"  交叉校验 {len(result)} 标的: calc 全 True, pool 全 False (akshare 不可用) ✓")


def test_determinism():
    """测试 9: 确定性"""
    print("\n[测试 9] 确定性")
    p1 = get_limit_pool_provider()
    p1.clear_cache()
    r1 = p1.get_pool("20260812")
    r2 = p1.get_pool("20260812")  # 第二次应命中缓存
    assert r1.date == r2.date
    assert r1.limit_up_codes == r2.limit_up_codes
    assert r1.limit_down_codes == r2.limit_down_codes
    print("  同日期两次获取一致 (缓存命中) ✓")


def test_existing_u2_u3_still_pass():
    """测试 10: U2/U3 已有功能不回归"""
    print("\n[测试 10] U2/U3 已有功能不回归")
    # U2: calc_limit_prices 仍正常
    lu, ld = calc_limit_prices(10.00, "600000", is_st=False)
    assert abs(lu - 11.00) < 0.01, f"主板涨停价应=11.00, 实际 {lu}"
    assert abs(ld - 9.00) < 0.01, f"主板跌停价应=9.00, 实际 {ld}"
    print(f"  U2 calc_limit_prices: lu={lu}, ld={ld} ✓")

    # 创业板 ±20%
    lu2, ld2 = calc_limit_prices(10.00, "300750", is_st=False)
    assert abs(lu2 - 12.00) < 0.01, f"创业板涨停价应=12.00, 实际 {lu2}"
    print(f"  U2 创业板: lu={lu2} ✓")

    # ST ±5%
    lu3, ld3 = calc_limit_prices(10.00, "600000", is_st=True)
    assert abs(lu3 - 10.50) < 0.01, f"ST涨停价应=10.50, 实际 {lu3}"
    print(f"  U2 ST: lu={lu3} ✓")


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    print("=" * 72)
    print("W6.6.3 数据层增强验证 · 涨停池/跌停池 + 停牌公告 + U2/U3 回归")
    print("=" * 72)

    test_limit_pool_provider_singleton()
    test_limit_pool_data_structure()
    test_date_normalization()
    test_fail_open_degradation()
    test_batch_preheat()
    test_build_backtest_integration()
    test_akshare_suspend_list()
    test_cross_validate()
    test_determinism()
    test_existing_u2_u3_still_pass()

    print("\n" + "=" * 72)
    print("W6.6.3 数据层增强验证 · 全部断言通过 ✓")
    print("=" * 72)
    print("  LimitPoolProvider: 单例 + 缓存 + Fail-Open 降级 ✓")
    print("  LimitPoolData: 涨停/跌停/炸板数据结构 ✓")
    print("  日期归一化: YYYYMMDD/YYYY-MM-DD/datetime ✓")
    print("  批量预热: get_pools_batch ✓")
    print("  build_backtest_data_from_ohlcv: limit_pool_provider 集成 ✓")
    print("  AKShareDataSource.get_suspend_list: 接口 + Fail-Open ✓")
    print("  交叉校验: cross_validate_with_calc ✓")
    print("  确定性: 缓存命中 ✓")
    print("  U2/U3 回归: calc_limit_prices 不变 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
