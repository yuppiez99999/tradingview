# -*- coding: utf-8 -*-
"""诊断 600276 LightGBM access violation 崩溃

目标:
1. 在隔离环境下复现 600276 训练崩溃
2. 识别崩溃根本原因 (NaN/Inf/数据类型/内存)
3. 验证修复方案 (数据清洗 + 鲁棒性增强)
"""
import os
import sys
import time
import logging
import traceback
import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# 路径
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "utils"))
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/diagnose_600276.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("diagnose_600276")

# 测试 cutoff 日期 (覆盖 walk-forward 训练各阶段)
TEST_CUTOFFS = [
    "2024-02-01",  # 第一次崩溃附近
    "2024-03-01",
    "2024-06-03",  # 2024-06 崩盘月
    "2024-10-01",  # 最近崩溃
    "2025-06-01",  # 后续月份
]


def load_600276_data() -> pd.DataFrame:
    """加载 600276 真实 OHLCV 数据"""
    base_file = Path("data_cache") / "historical_600276_5y_base.parquet"
    if not base_file.exists():
        logger.error("数据文件不存在: %s", base_file)
        return None
    df = pd.read_parquet(base_file)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    logger.info("600276 数据: %d 行, 范围 %s ~ %s", len(df), df.index[0], df.index[-1])
    return df


def build_features_simple(df: pd.DataFrame) -> pd.DataFrame:
    """使用 lgb_enhanced_trainer 的特征工程"""
    from autolearn_trainer import add_technical_features, add_cross_sectional_features

    df = df.copy()
    # 技术因子
    df = add_technical_features(df)
    # 截面因子 (单标的简化版)
    try:
        df = add_cross_sectional_features(df)
    except Exception as e:
        logger.warning("截面因子失败 (单标的时正常): %s", e)
    return df


def validate_data_quality(df: pd.DataFrame, label: str) -> dict:
    """验证数据质量 (NaN/Inf/极端值)"""
    if df is None or len(df) == 0:
        return {"status": "empty"}

    issues = []
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    n_nan_total = 0
    n_inf_total = 0
    n_extreme_total = 0

    for col in numeric_cols:
        col_data = df[col]
        n_nan = int(col_data.isna().sum())
        n_inf = int(np.isinf(col_data).sum())
        n_extreme = int(((col_data > 1e10) | (col_data < -1e10)).sum())

        n_nan_total += n_nan
        n_inf_total += n_inf
        n_extreme_total += n_extreme

        if n_nan > len(df) * 0.5:
            issues.append(f"{col}: NaN {n_nan}/{len(df)} ({n_nan/len(df)*100:.1f}%)")
        if n_inf > 0:
            issues.append(f"{col}: Inf {n_inf}")
        if n_extreme > 0:
            issues.append(f"{col}: 极端值 {n_extreme}")

    # 检查 close 列
    if "close" in df.columns:
        close = df["close"]
        if (close <= 0).any():
            issues.append(f"close: 包含非正值 min={close.min()}")
        rets = close.pct_change().dropna()
        if len(rets) > 0:
            max_ret = float(rets.max())
            min_ret = float(rets.min())
            if max_ret > 0.5 or min_ret < -0.5:
                issues.append(f"收益率异常: max={max_ret:.4f}, min={min_ret:.4f}")

    logger.info("[%s] 数据质量: %d 行, %d 列, NaN=%d, Inf=%d, 极端=%d, 问题=%d",
                label, len(df), df.shape[1], n_nan_total, n_inf_total,
                n_extreme_total, len(issues))
    for issue in issues[:5]:
        logger.info("  - %s", issue)

    return {
        "status": "ok" if not issues else "issues",
        "n_rows": len(df),
        "n_cols": df.shape[1],
        "n_nan": n_nan_total,
        "n_inf": n_inf_total,
        "n_extreme": n_extreme_total,
        "issues": issues,
    }


def test_train_600276(df: pd.DataFrame, cutoff: str) -> dict:
    """测试训练 600276 单标的"""
    from lgb_enhanced_trainer import train_symbol_enhanced, LGB_ENHANCED_CONFIG

    # 模拟 walk-forward 配置 (与 institutional_pipeline_runner.WALKFORWARD_LGB_CONFIG 一致)
    config = {
        **LGB_ENHANCED_CONFIG,
        "lgb_params": {
            **LGB_ENHANCED_CONFIG["lgb_params"],
            "n_estimators": 1000,        # 2000 → 1000
        },
        "early_stopping_rounds": 100,     # 200 → 100
        "news_lookback_days": 0,          # 跳过新闻
    }

    t0 = time.time()
    try:
        result = train_symbol_enhanced("600276", df, config)
        elapsed = time.time() - t0
        if result.get("status") == "OK":
            logger.info("[%s] ✓ 训练成功: best_iter=%s, IC=%.4f, signal=%.4f (耗时 %.1fs)",
                        cutoff, result.get("best_iteration"),
                        result["final_metrics"]["ic"],
                        result["signal"], elapsed)
            return {"status": "OK", "best_iter": result.get("best_iteration"),
                    "ic": result["final_metrics"]["ic"],
                    "signal": result["signal"], "elapsed": elapsed}
        else:
            logger.warning("[%s] 训练跳过: %s", cutoff, result.get("reason"))
            return {"status": "SKIP", "reason": result.get("reason"), "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - t0
        logger.error("[%s] ✗ 训练崩溃: %s (耗时 %.1fs)", cutoff, e, elapsed)
        logger.error(traceback.format_exc())
        return {"status": "CRASH", "error": str(e), "elapsed": elapsed,
                "traceback": traceback.format_exc()}


def main():
    logger.info("=" * 70)
    logger.info("诊断 600276 LightGBM access violation 崩溃")
    logger.info("=" * 70)

    results = {}

    # 1. 数据质量检查
    logger.info("\n[1/3] 全量数据质量检查")
    df_raw = load_600276_data()
    if df_raw is None:
        logger.error("无法加载 600276 数据，退出")
        return

    quality = validate_data_quality(df_raw, "raw_all")
    results["raw_data_quality"] = quality

    # 2. 在不同 cutoff 日期训练
    logger.info("\n[2/3] 在不同 cutoff 日期测试训练")
    train_results = {}
    for cutoff in TEST_CUTOFFS:
        logger.info("\n--- 测试 cutoff: %s ---", cutoff)
        cutoff_ts = pd.Timestamp(cutoff)
        df = df_raw[df_raw.index <= cutoff_ts].copy()
        if len(df) < 150:
            logger.warning("[%s] 数据不足 %d 行, 跳过", cutoff, len(df))
            train_results[cutoff] = {"status": "no_data", "n_rows": len(df)}
            continue

        # 构建特征
        try:
            df_feat = build_features_simple(df)
        except Exception as e:
            logger.error("[%s] 特征构建失败: %s", cutoff, e)
            logger.error(traceback.format_exc())
            train_results[cutoff] = {"status": "feat_fail", "error": str(e)}
            continue

        # 特征数据质量检查
        feat_quality = validate_data_quality(df_feat, f"feat_{cutoff}")
        train_result = test_train_600276(df_feat, cutoff)
        train_results[cutoff] = {
            "data_quality": feat_quality,
            "train": train_result,
        }

    results["train_by_cutoff"] = train_results

    # 3. 检查是否复现崩溃
    logger.info("\n[3/3] 崩溃诊断汇总")
    crash_count = sum(1 for r in train_results.values()
                      if r.get("train", {}).get("status") == "CRASH")
    ok_count = sum(1 for r in train_results.values()
                   if r.get("train", {}).get("status") == "OK")
    skip_count = sum(1 for r in train_results.values()
                     if r.get("train", {}).get("status") == "SKIP")
    no_data_count = sum(1 for r in train_results.values()
                        if r.get("status") == "no_data")

    logger.info("训练结果: OK=%d, SKIP=%d, CRASH=%d, NO_DATA=%d",
                ok_count, skip_count, crash_count, no_data_count)

    if crash_count == 0:
        logger.info("✓ 未复现崩溃 - 600276 在所有 cutoff 日期训练成功")
        logger.info("  崩溃可能是间歇性的 (内存压力/线程竞争)")
        logger.info("  建议: 添加 try-except 容错 + 数据清洗, 防止未来崩溃")
        results["diagnosis"] = "intermittent_no_repro"
    else:
        logger.warning("✗ 复现崩溃 - 需要修复数据或训练逻辑")
        results["diagnosis"] = "reproduced"

    # 保存诊断结果
    output_path = Path("output/validation_reports")
    output_path.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    diag_file = output_path / f"diagnose_600276_{ts}.json"
    with open(diag_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.info("\n诊断结果已保存: %s", diag_file)

    return results


if __name__ == "__main__":
    main()
