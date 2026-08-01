# -*- coding: utf-8 -*-
"""诊断 600276 LightGBM access violation 崩溃原因

检查项:
  1. 600276 历史数据是否有 NaN/inf/极端值
  2. 特征工程是否产生无效值
  3. 数据长度是否足够 (walk-forward 需要足够样本)
  4. 与其他标的对比, 找出异常差异
"""
import pandas as pd
import numpy as np
from pathlib import Path

# 加载 600276 数据
f = Path("data_cache") / "historical_600276_5y_base.parquet"
if not f.exists():
    print(f"ERROR: {f} 不存在")
    exit(1)

df = pd.read_parquet(f)
print("=== 600276 基础数据 ===")
print(f"形状: {df.shape}")
print(f"日期范围: {df.index[0]} ~ {df.index[-1]}")
print(f"列: {list(df.columns)}")
print("\n前5行:")
print(df.head())
print("\n后5行:")
print(df.tail())

# 检查 NaN/inf
print("\n=== 数据质量检查 ===")
for col in df.columns:
    nan_count = df[col].isna().sum()
    inf_count = np.isinf(df[col].astype(float)).sum() if df[col].dtype in [np.float64, np.float32, int] else 0
    if nan_count > 0 or inf_count > 0:
        print(f"  {col}: NaN={nan_count}, inf={inf_count}")

# 检查极端值
print("\n=== 统计摘要 ===")
print(df.describe())

# 检查 close 列的异常值
if "close" in df.columns:
    close = df["close"]
    print("\n=== close 列异常检查 ===")
    print(f"  min={close.min()}, max={close.max()}")
    print(f"  零值数量: {(close == 0).sum()}")
    print(f"  负值数量: {(close < 0).sum()}")
    # 检查收益率
    rets = close.pct_change()
    print(f"  日收益率: min={rets.min():.4f}, max={rets.max():.4f}")
    print(f"  极端收益率(>50%): {(rets.abs() > 0.5).sum()}")
    print(f"  极端收益率(>100%): {(rets.abs() > 1.0).sum()}")

# 对比其他标的
print("\n=== 与其他标的对比 ===")
symbols = ["588000", "688041", "002371", "300308", "600089", "688017", "000333"]
for sym in symbols:
    sf = Path("data_cache") / f"historical_{sym}_5y_base.parquet"
    if sf.exists():
        sdf = pd.read_parquet(sf)
        s_close = sdf["close"] if "close" in sdf.columns else None
        if s_close is not None:
            s_rets = s_close.pct_change()
            print(f"  {sym}: shape={sdf.shape}, close_range=[{s_close.min():.2f}, {s_close.max():.2f}], "
                  f"ret_range=[{s_rets.min():.4f}, {s_rets.max():.4f}]")

# 检查 600276 在 walk-forward 训练窗口的数据量
print("\n=== Walk-forward 训练窗口数据量检查 ===")
# walk-forward 通常用 3 年训练 + 1 月测试
# 检查 2024-10-01 (崩溃月) 时 600276 的可用数据
cutoff = pd.Timestamp("2024-10-01")
if hasattr(df.index, "tz") and df.index.tz is not None:
    df.index = df.index.tz_localize(None)
df_before = df[df.index <= cutoff]
print(f"  2024-10-01 前数据量: {len(df_before)} 行")
if len(df_before) < 250:
    print("  ⚠️ 数据量不足 250 行 (约1年), 可能导致 LGB 训练不稳定")

# 检查特征工程可能的问题
print("\n=== 特征工程预检 ===")
try:
    import sys
    sys.path.insert(0, ".")
    from lgb_enhanced_trainer import add_technical_features, add_mean_reversion_features

    # 尝试对 600276 做特征工程
    feat_df = df.copy()
    if "close" in feat_df.columns and "volume" in feat_df.columns:
        feat_df = add_technical_features(feat_df)
        feat_df = add_mean_reversion_features(feat_df)

        # 检查特征中的 NaN/inf
        nan_cols = feat_df.isna().sum()
        inf_cols = feat_df.select_dtypes(include=[np.number]).apply(lambda x: np.isinf(x).sum())

        problem_cols = []
        for col in feat_df.columns:
            n_nan = nan_cols.get(col, 0)
            n_inf = inf_cols.get(col, 0) if col in inf_cols.index else 0
            if n_nan > len(feat_df) * 0.5 or n_inf > 0:
                problem_cols.append((col, n_nan, n_inf))

        if problem_cols:
            print("  ⚠️ 问题特征列 (NaN>50% 或含inf):")
            for col, n_nan, n_inf in problem_cols:
                print(f"    {col}: NaN={n_nan}/{len(feat_df)}, inf={n_inf}")
        else:
            print(f"  特征工程正常, 共 {len(feat_df.columns)} 列")

        # 检查特征值范围
        numeric_cols = feat_df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            col_data = feat_df[col].dropna()
            if len(col_data) > 0:
                col_min = col_data.min()
                col_max = col_data.max()
                if abs(col_min) > 1e10 or abs(col_max) > 1e10:
                    print(f"  ⚠️ 极端值: {col} range=[{col_min:.2e}, {col_max:.2e}]")
except Exception as e:
    print(f"  特征工程检查失败: {e}")
    import traceback
    traceback.print_exc()
