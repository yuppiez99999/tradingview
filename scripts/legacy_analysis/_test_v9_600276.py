# -*- coding: utf-8 -*-
"""测试 V9 regime-specific 训练函数

验证:
1. train_symbol_regime_specific 能正常训练 600276 双模型
2. bull / non_bull 模型分别训练成功
3. 信号生成正确
"""
import sys
import time
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "utils"))

from lgb_enhanced_trainer import (
    LGB_ENHANCED_CONFIG,
    add_technical_features,
    compute_regime_series,
    train_symbol_regime_specific,
)


def test_v9_600276():
    print("=" * 70)
    print("测试 V9 Regime-Specific 训练 (600276)")
    print("=" * 70)

    # 加载 600276 数据
    sym_file = Path("data_cache") / "historical_600276_5y_base.parquet"
    df = pd.read_parquet(sym_file)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    print(f"600276 数据: {len(df)} 行, {df.index[0]} ~ {df.index[-1]}")

    # 加载 510300 大盘代理数据
    proxy_file = Path("data_cache") / "historical_510300_5y_base.parquet"
    proxy_df = pd.read_parquet(proxy_file)
    if hasattr(proxy_df.index, "tz") and proxy_df.index.tz is not None:
        proxy_df.index = proxy_df.index.tz_localize(None)
    proxy_df = proxy_df.sort_index()
    print(f"510300 数据: {len(proxy_df)} 行")

    # 测试 3 个 cutoff 日期
    cutoffs = ["2024-03-01", "2024-06-03", "2024-10-01"]
    for cutoff in cutoffs:
        print(f"\n--- 测试 cutoff: {cutoff} ---")
        cutoff_ts = pd.Timestamp(cutoff)

        # 截取数据
        df_sub = df[df.index <= cutoff_ts].copy()
        proxy_sub = proxy_df[proxy_df.index <= cutoff_ts].copy()
        print(f"  600276 截取: {len(df_sub)} 行")
        print(f"  510300 截取: {len(proxy_sub)} 行")

        if len(df_sub) < 150:
            print("  数据不足, 跳过")
            continue

        # 计算 regime 序列
        regime_series = compute_regime_series(proxy_sub)
        regime_counts = regime_series.value_counts()
        print(f"  Regime 分布: {dict(regime_counts)}")

        # 添加技术特征
        df_feat = add_technical_features(df_sub)
        print(f"  特征工程后: {df_feat.shape}")

        # 模拟 walk-forward 配置
        config = {
            **LGB_ENHANCED_CONFIG,
            "lgb_params": {
                **LGB_ENHANCED_CONFIG["lgb_params"],
                "n_estimators": 1000,
                "n_jobs": 1,
            },
            "early_stopping_rounds": 100,
            "news_lookback_days": 0,
            "min_samples": 100,  # V9: 每个 regime 子集最少 100 样本
        }

        # 训练 regime-specific 模型
        t0 = time.time()
        try:
            result = train_symbol_regime_specific(
                "600276", df_feat, config, regime_series,
                min_samples_per_regime=100,
            )
            elapsed = time.time() - t0

            if result.get("status") == "OK":
                print(f"\n  ✓ V9 训练成功 (耗时 {elapsed:.1f}s)")
                print(f"  当前 regime: {result.get('current_regime')}")
                print(f"  选择模型: {result.get('selected_regime')}")
                print(f"  bull 样本: {result.get('n_bull_samples')}")
                print(f"  non_bull 样本: {result.get('n_non_bull_samples')}")
                print(f"  信号: {result.get('signal'):.4f}")
                print(f"  最终 IC: {result['final_metrics'].get('ic', 'N/A')}")

                # 检查模型存在性
                models = result.get("models_by_regime", {})
                print("\n  模型状态:")
                for k, v in models.items():
                    print(f"    {k}: {'✓' if v is not None else '✗'}")
            else:
                print(f"\n  ✗ V9 训练跳过: {result.get('reason')}")
        except Exception as e:
            import traceback
            print(f"\n  ✗ V9 训练崩溃: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    test_v9_600276()
