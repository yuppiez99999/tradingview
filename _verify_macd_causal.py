# -*- coding: utf-8 -*-
"""
验证 MACD 特征是否因果 (无前视偏差)

方法: 扰动测试
  1. 构造一段 OHLCV 数据
  2. 计算原始 MACD 序列
  3. 修改某个未来时刻 t+k 的 close 价格 (大幅扰动)
  4. 重新计算 MACD
  5. 检查 t 时刻之前的 MACD 是否发生变化
     - 若不变 → 因果 (无前视偏差)
     - 若变化 → 含前视偏差

同时验证 ewm(adjust=False) 的递归性质。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional"))

from autolearn_trainer import add_technical_features


def build_synthetic_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """构造合成 OHLCV 数据"""
    rng = np.random.default_rng(seed)
    # 随机游走 close
    rets = rng.normal(0, 0.02, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = (high + low) / 2
    volume = rng.lognormal(15, 0.5, n)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=dates)


def perturb_test():
    """扰动测试: 修改未来数据, 检查过去 MACD 是否变化"""
    df_orig = build_synthetic_ohlcv(n=100, seed=42)
    feat_orig = add_technical_features(df_orig)

    # 扰动点: 在 t=60 处大幅修改 close (影响 t=60 及之后)
    perturb_idx = 60
    df_pert = df_orig.copy()
    # 把 t=60..end 的 close 翻倍 (剧烈扰动)
    df_pert.iloc[perturb_idx:, df_pert.columns.get_loc("close")] *= 2.0
    df_pert.iloc[perturb_idx:, df_pert.columns.get_loc("high")] *= 2.0
    df_pert.iloc[perturb_idx:, df_pert.columns.get_loc("low")] *= 2.0
    df_pert.iloc[perturb_idx:, df_pert.columns.get_loc("open")] *= 2.0
    feat_pert = add_technical_features(df_pert)

    # 检查 perturb_idx 之前的 MACD 是否完全一致
    macd_feats = ["macd", "macd_signal", "macd_hist"]
    print("=" * 70)
    print("扰动测试: 修改 t=60 之后的所有数据, 检查 t<60 的 MACD 是否变化")
    print("=" * 70)

    all_causal = True
    for feat in macd_feats:
        if feat not in feat_orig.columns:
            print(f"  [跳过] {feat} 不在特征列中")
            continue
        before = feat_orig[feat].iloc[:perturb_idx]
        after = feat_pert[feat].iloc[:perturb_idx]
        max_diff = (before - after).abs().max()
        is_causal = max_diff < 1e-12
        status = "✅ 因果 (无前视)" if is_causal else "❌ 含前视偏差"
        if not is_causal:
            all_causal = False
        print(f"  {feat:<15} t<60 最大差异: {max_diff:.2e}  {status}")

    print()
    print("=" * 70)
    print("ewm(adjust=False) 递归性质 + warm-up NaN 修复验证")
    print("=" * 70)
    # 修复后: add_technical_features 给前 35 行填 NaN
    macd_nan_35 = feat_orig["macd"].iloc[:35].isna().sum()
    macd_signal_nan_35 = feat_orig["macd_signal"].iloc[:35].isna().sum()
    macd_hist_nan_35 = feat_orig["macd_hist"].iloc[:35].isna().sum()
    print(f"  feat_orig['macd']        前35行 NaN 数: {macd_nan_35} (修复后应为 35)")
    print(f"  feat_orig['macd_signal'] 前35行 NaN 数: {macd_signal_nan_35} (修复后应为 35)")
    print(f"  feat_orig['macd_hist']   前35行 NaN 数: {macd_hist_nan_35} (修复后应为 35)")
    # 第 36 行应该有值
    print(f"  feat_orig['macd']        第36行值: {feat_orig['macd'].iloc[35]:.6f} (应为有效值, 非 NaN)")

    # 调用 check_lookahead_bias 确认不再误报
    print()
    print("=" * 70)
    print("check_lookahead_bias 检测结果 (修复后)")
    print("=" * 70)
    try:
        sys.path.insert(0, str(BASE_DIR / "v8.3_institutional" / "src"))
        from validation.purged_cv import check_lookahead_bias
        report = check_lookahead_bias(feat_orig)
        macd_leaks = [l for l in report["suspected_leaks"] if "macd" in l.lower()]
        if not macd_leaks:
            print(f"  ✅ macd 类特征未再被误报 (leak_count={report['leak_count']})")
        else:
            print(f"  ❌ 仍被误报: {macd_leaks}")
    except Exception as e:
        print(f"  [跳过检测器验证] {e}")

    print()
    print("=" * 70)
    print(f"结论: {'✅ MACD 因果正确, check_lookahead_bias 为误报' if all_causal else '❌ MACD 含前视偏差, 需修复'}")
    print("=" * 70)
    return all_causal


if __name__ == "__main__":
    ok = perturb_test()
    sys.exit(0 if ok else 1)
