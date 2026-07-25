# -*- coding: utf-8 -*-
"""重现 600276 LightGBM 训练崩溃

测试步骤:
  1. 单独对 600276 运行 train_symbol_enhanced
  2. 检查是否崩溃
  3. 如果崩溃, 定位具体原因 (数据/特征/标签)
"""
import sys
import logging
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import pandas as pd
import numpy as np
from pathlib import Path

print("=" * 70)
print("600276 LGB 训练崩溃重现测试")
print("=" * 70)

# Step 1: 加载数据
df = pd.read_parquet("data_cache/historical_600276_5y_base.parquet")
if hasattr(df.index, "tz") and df.index.tz is not None:
    df.index = df.index.tz_localize(None)
print(f"原始数据: shape={df.shape}, columns={list(df.columns)}")

# Step 2: 特征工程
try:
    from lgb_enhanced_trainer import (
        add_technical_features,
        add_cross_sectional_features,
        add_industry_relative_strength_features,
        add_capital_flow_features,
        add_cross_market_features,
        add_sentiment_features,
        add_mean_reversion_features,
        add_regime_aware_features,
    )

    print("\nStep 2: 特征工程")
    feat_df = df.copy()

    # 按顺序应用特征工程 (与 institutional_pipeline_runner 一致)
    print("  add_technical_features...")
    feat_df = add_technical_features(feat_df)
    print(f"  after technical: shape={feat_df.shape}, type={type(feat_df).__name__}")

    # 检查 close 列是否还在
    if isinstance(feat_df, pd.DataFrame):
        print(f"  columns: {list(feat_df.columns)[:10]}...")
        if "close" not in feat_df.columns:
            print(f"  ⚠️ close 列丢失! columns={list(feat_df.columns)}")
    else:
        print(f"  ⚠️ 返回类型不是 DataFrame: {type(feat_df)}")

    print("  add_mean_reversion_features...")
    feat_df = add_mean_reversion_features({"600276": feat_df})["600276"]
    print(f"  after mean_reversion: shape={feat_df.shape}")

    print("  add_regime_aware_features...")
    # regime_aware 只接受 ohlcv_dict
    feat_df = add_regime_aware_features({"600276": feat_df})["600276"]
    print(f"  after regime_aware: shape={feat_df.shape}")

    # 检查 NaN/inf
    numeric_cols = feat_df.select_dtypes(include=[np.number]).columns
    nan_count = feat_df[numeric_cols].isna().sum().sum()
    inf_count = np.isinf(feat_df[numeric_cols].astype(float)).sum().sum()
    print(f"  NaN 总数: {nan_count}, inf 总数: {inf_count}")

    if inf_count > 0:
        print("  ⚠️ 存在 inf 值!")
        for col in numeric_cols:
            n_inf = np.isinf(feat_df[col].astype(float)).sum()
            if n_inf > 0:
                print(f"    {col}: {n_inf} inf")

except Exception as e:
    print(f"\n特征工程失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 3: 尝试 LGB 训练
print("\nStep 3: LGB 训练")
try:
    from lgb_enhanced_trainer import train_symbol_enhanced, LGB_ENHANCED_CONFIG

    # 模拟 walk-forward 训练 (截止 2024-10-01)
    cutoff = pd.Timestamp("2024-10-01")
    train_df = feat_df[feat_df.index < cutoff].copy()
    print(f"  训练数据: shape={train_df.shape}")

    # 检查标签 (5日前向收益)
    if "close" in train_df.columns:
        train_df["label"] = train_df["close"].pct_change(5).shift(-5)
        print(f"  标签统计: mean={train_df['label'].mean():.4f}, std={train_df['label'].std():.4f}")
        print(f"  标签 NaN: {train_df['label'].isna().sum()}")

    # 尝试训练
    print("  开始 LGB 训练...")
    result = train_symbol_enhanced(
        symbol="600276",
        df=train_df,
        config=LGB_ENHANCED_CONFIG,
    )
    print(f"  训练成功! signal={result.get('signal', 'N/A')}")

except Exception as e:
    print(f"\nLGB 训练失败: {e}")
    traceback.print_exc()
    print("\n建议修复方案:")
    print("  1. 检查特征工程是否产生 inf/NaN")
    print("  2. 检查标签是否有极端值")
    print("  3. 尝试 skip 600276 或增加数据清洗")
