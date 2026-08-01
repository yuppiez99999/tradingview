"""分析历史数据的 regime 分布, 验证 regime-specific 训练可行性

每个 regime 需要至少 100 样本 (5 日前向收益标签 + 60 日 MA + 20 日波动率)
"""
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


def analyze_regime_distribution():
    """分析每个 cutoff 下的 regime 分布"""
    proxy_file = Path("data_cache") / "historical_510300_5y_base.parquet"
    if not proxy_file.exists():
        print(f"数据文件不存在: {proxy_file}")
        return

    df = pd.read_parquet(proxy_file)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    print(f"510300 数据: {len(df)} 行, 范围 {df.index[0]} ~ {df.index[-1]}")

    # 计算 regime
    ma_period = 60
    slope_window = 5
    close = df["close"]
    ma = close.rolling(ma_period).mean()
    ma_slope = ma.diff(slope_window)
    above_ma = close > ma
    ma_rising = ma_slope > 0

    regime = pd.Series("unknown", index=df.index)
    regime[above_ma & ma_rising] = "bull"
    regime[above_ma & (~ma_rising)] = "choppy"
    regime[(~above_ma) & ma_rising] = "rebound"
    regime[(~above_ma) & (~ma_rising)] = "bear"

    # 全局分布
    print("\n=== 全局 regime 分布 ===")
    dist = regime.value_counts()
    for r, n in dist.items():
        pct = n / len(regime) * 100
        print(f"  {r}: {n} ({pct:.1f}%)")

    # 在每个 walk-forward 窗口起点检查 regime 分布
    cutoffs = [
        "2022-04-01", "2023-04-01", "2023-07-03",
        "2024-03-01", "2024-06-03", "2024-10-01",
        "2025-06-01", "2025-12-01",
    ]
    print("\n=== 各 cutoff 时点的 regime 分布 (历史数据截至该日) ===")
    print(f"{'cutoff':<12} {'bull':>6} {'bear':>6} {'choppy':>6} {'rebound':>8} {'total':>6}")
    for cutoff in cutoffs:
        cutoff_ts = pd.Timestamp(cutoff)
        sub = regime[regime.index <= cutoff_ts]
        if len(sub) == 0:
            continue
        # 移除 unknown
        sub = sub[sub != "unknown"]
        bull = (sub == "bull").sum()
        bear = (sub == "bear").sum()
        choppy = (sub == "choppy").sum()
        rebound = (sub == "rebound").sum()
        total = len(sub)
        print(f"{cutoff:<12} {bull:>6} {bear:>6} {choppy:>6} {rebound:>8} {total:>6}")

    # 各 regime 的样本数是否足够训练
    print("\n=== Regime-specific 训练可行性分析 ===")
    min_samples = 100  # 训练最低样本数
    for r in ["bull", "bear", "choppy", "rebound"]:
        n = (regime == r).sum()
        status = "✓ 可训练" if n >= min_samples else f"✗ 样本不足 (< {min_samples})"
        print(f"  {r}: {n} 样本 - {status}")

    # 检查 walk-forward 训练时 (500 日回看), 各 cutoff 的 regime 样本数
    print("\n=== Walk-forward 训练时 (500 日回看) 各 regime 样本数 ===")
    lookback = 500
    print(f"{'cutoff':<12} {'bull':>6} {'bear':>6} {'choppy':>6} {'rebound':>8} {'total':>6}")
    for cutoff in cutoffs:
        cutoff_ts = pd.Timestamp(cutoff)
        sub = regime[regime.index <= cutoff_ts].tail(lookback)
        sub = sub[sub != "unknown"]
        if len(sub) == 0:
            continue
        bull = (sub == "bull").sum()
        bear = (sub == "bear").sum()
        choppy = (sub == "choppy").sum()
        rebound = (sub == "rebound").sum()
        total = len(sub)
        # 标记样本不足
        flag = ""
        for r, n in [("bull", bull), ("bear", bear),
                     ("choppy", choppy), ("rebound", rebound)]:
            if 0 < n < min_samples:
                flag += f" {r}不足"
        print(f"{cutoff:<12} {bull:>6} {bear:>6} {choppy:>6} {rebound:>8} {total:>6}{flag}")


if __name__ == "__main__":
    analyze_regime_distribution()
