"""
全量因子库集成测试
测试 Vibe-Trading 462 个因子 + GTJA191 189 个因子 + Alpha101 + QLib158
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from utils.gtja191_factors import GTJA191Factors
from utils.vibe_trading_adapter import get_vibe_adapter


def generate_test_data(n_days: int = 300) -> pd.DataFrame:
    """生成模拟行情数据"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")

    # 模拟真实行情：带漂移的几何布朗运动
    close0 = 100.0
    returns = np.random.randn(n_days) * 0.02 + 0.0005
    close = close0 * np.exp(np.cumsum(returns))

    high = close * (1 + np.abs(np.random.randn(n_days)) * 0.01)
    low = close * (1 - np.abs(np.random.randn(n_days)) * 0.01)
    open_price = close * (1 + np.random.randn(n_days) * 0.005)

    volume = np.abs(np.random.randn(n_days)) * 1e7 + 1e6
    amount = close * volume * 0.001  # 千元

    df = pd.DataFrame(
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
        },
        index=dates,
    )
    return df


def test_vibe_adapter():
    """测试 Vibe-Trading 适配器"""
    print("=" * 70)
    print("测试 1: Vibe-Trading 因子适配器")
    print("=" * 70)

    adapter = get_vibe_adapter()
    health = adapter.health
    print(f"注册表状态: 已加载 {health['loaded']} 个, 失败 {health['failed']} 个")
    if health["errors"]:
        for e in health["errors"][:5]:
            print(f"  错误: {e['alpha_id']} - {e['reason']}")

    # 各 zoo 统计
    for zoo in ["gtja191", "alpha101", "qlib158", "academic", "fundamental"]:
        ids = adapter.list_factors(zoo=zoo)
        print(f"  {zoo:15s}: {len(ids):4d} 个因子")

    return adapter


def test_gtja191_full(df: pd.DataFrame):
    """测试完整 GTJA191"""
    print("\n" + "=" * 70)
    print("测试 2: 国泰君安 GTJA191 完整因子库")
    print("=" * 70)

    calc = GTJA191Factors(lookback=20)
    print(f"GTJA191 因子总数: {calc.count}")

    # 按主题分类
    themes = ["momentum", "reversal", "volume", "volatility", "liquidity"]
    print("\n按主题分类:")
    for theme in themes:
        ids = calc.list_by_theme(theme)
        print(f"  {theme:15s}: {len(ids):3d} 个")

    # 测试几个经典因子
    print("\n经典因子测试:")
    test_factors = [
        ("gtja191_001", "量价秩相关"),
        ("gtja191_005", "量价时序秩相关最大"),
        ("gtja191_028", "KDJ类趋势"),
        ("gtja191_040", "涨跌成交量比"),
        ("gtja191_072", "量价变动相关"),
        ("gtja191_144", "下跌日量价效率"),
        ("gtja191_158", "长期趋势位置"),
        ("gtja191_189", "条件成交量衰减"),
    ]
    for fid, desc in test_factors:
        series = calc.compute_series(df, fid)
        if series is not None:
            last_val = float(series.iloc[-1]) if pd.notna(series.iloc[-1]) else 0.0
            print(f"  ✅ {fid:15s} [{desc:20s}]: {last_val:.6f}")
        else:
            print(f"  ❌ {fid:15s} [{desc:20s}]: 计算失败")

    # 全量计算计时
    print(f"\n全量计算 {calc.count} 个因子...")
    t0 = time.time()
    all_values = calc.compute(df)
    t1 = time.time()

    valid_count = sum(
        1
        for v in all_values.values()
        if v is not None and not (isinstance(v, float) and np.isnan(v))
    )
    print(f"  成功: {valid_count}/{len(all_values)} 个")
    print(f"  耗时: {t1 - t0:.2f} 秒")
    print(f"  平均: {(t1 - t0) / len(all_values) * 1000:.2f} 毫秒/因子")

    return all_values


def test_all_zoos(df: pd.DataFrame, adapter):
    """测试所有 zoo 的因子"""
    print("\n" + "=" * 70)
    print("测试 3: 全部因子库 (GTJA191 + Alpha101 + QLib158 + Academic)")
    print("=" * 70)

    zoos = ["gtja191", "alpha101", "qlib158", "academic"]
    total_ok = 0
    total_fail = 0
    total_time = 0.0

    for zoo in zoos:
        ids = adapter.list_factors(zoo=zoo)
        print(f"\n{zoo} ({len(ids)} 个因子)...")
        t0 = time.time()
        result = adapter.compute_single_stock(df, zoo=zoo)
        t1 = time.time()
        elapsed = t1 - t0
        total_time += elapsed

        n_ok = len(result.values)
        n_fail = len(ids) - n_ok
        total_ok += n_ok
        total_fail += n_fail

        print(
            f"  成功: {n_ok}/{len(ids)} | 耗时: {elapsed:.2f}s | 平均: {elapsed/len(ids)*1000:.1f}ms/个"
        )

    print(f"\n{'=' * 70}")
    print(f"汇总: 成功 {total_ok} 个, 失败 {total_fail} 个, 总耗时 {total_time:.2f} 秒")
    print(f"系统因子总数: {total_ok + total_fail} 个 (来自 Vibe-Trading)")


def test_formula_queries():
    """测试公式查询"""
    print("\n" + "=" * 70)
    print("测试 4: 因子公式与元信息查询")
    print("=" * 70)

    calc = GTJA191Factors()
    samples = ["gtja191_001", "gtja191_040", "gtja191_144"]

    for fid in samples:
        info = calc.get_info(fid)
        formula = calc.get_formula(fid)
        print(f"\n{fid}:")
        print(f"  主题: {info.get('themes', [])}")
        print(f"  所需数据: {info.get('columns_required', [])}")
        print(f"  预热K线: {info.get('min_warmup_bars', 0)} 根")
        print(
            f"  公式: {formula[:80]}..." if len(formula) > 80 else f"  公式: {formula}"
        )


if __name__ == "__main__":
    df = generate_test_data(300)
    print(f"测试数据: {len(df)} 个交易日, {df.columns.tolist()}")
    print(f"价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")

    adapter = test_vibe_adapter()
    test_gtja191_full(df)
    test_all_zoos(df, adapter)
    test_formula_queries()

    print("\n" + "=" * 70)
    print("✅ 全部测试完成")
    print("=" * 70)
