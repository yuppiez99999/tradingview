"""
LightGBM + 时间序列交叉验证训练器
=================================

针对当前集成模型表现不佳 (R² 全为负, IC 全为负) 的优化方案:
    1. 换模型: 纯 LightGBM (结构化数据表现更好)
        - 早停 (early_stopping) 防过拟合
        - 更深树 (max_depth=6) + 更多估计器 (1000)
        - 更小学习率 (0.01) 配合更多估计器
        - 特征筛选 (去除低重要性特征)
    2. 时间序列交叉验证 (TimeSeriesSplit):
        - 5折滚动窗口验证
        - 每折记录 R²/IC/Sharpe
        - 平均+标准差作为稳健评估指标
    3. 对比报告: 旧集成 vs 新 LGB+CV

用法:
    python lgb_tscv_trainer.py                    # 训练全部持仓
    python lgb_tscv_trainer.py --symbols 688041   # 训练单个标的
    python lgb_tscv_trainer.py --force-retrain    # 强制重训
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

# ============================================================
# 路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models" / "lgb_tscv"  # 新目录, 不覆盖旧模型
REPORTS_DIR = BASE_DIR / "reports" / "lgb_tscv"
LOG_DIR = BASE_DIR / "logs"

for d in [MODELS_DIR, REPORTS_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 复用旧训练器的标的清单和特征工程
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(BASE_DIR))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils
from autolearn_trainer import (  # noqa: E402
    POSITION_SYMBOLS,
    add_cross_sectional_features,
    add_technical_features,
    load_ohlcv_history,
    load_returns_history,
    synthesize_ohlcv_from_returns,
)

# ============================================================
# 新训练配置 - 纯 LightGBM 优化版
# ============================================================
LGB_TSCV_CONFIG = {
    "lookback_days": 500,
    "min_samples": 150,  # 提高最小样本要求 (CV 需要更多数据)
    "test_ratio": 0.2,  # 最终 holdout 测试集
    "n_splits": 5,  # TimeSeriesSplit 折数
    "top_n_features": 15,  # 保留 Top 15 重要特征 (减少噪音)
    "feature_selection_threshold": 5,  # 重要性 < 5 的特征剔除
    "retrain_interval_days": 7,
    "model_quality_threshold": {
        "min_cv_r2": -0.3,  # CV 平均 R² 下限
        "min_cv_ic": 0.0,  # CV 平均 IC 下限 (必须非负)
        "min_cv_sharpe": 0.0,  # CV 平均 Sharpe 下限
    },
    "lgb_params": {
        "n_estimators": 1000,  # 大幅增加, 配合早停
        "learning_rate": 0.01,  # 更小学习率
        "max_depth": 6,  # 更深树
        "num_leaves": 31,
        "min_child_samples": 30,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "random_state": 42,
        "verbose": -1,
        "n_jobs": -1,
    },
    "early_stopping_rounds": 50,  # 早停轮次
}

logger = logging.getLogger("lgb_tscv")


# ============================================================
# 时间序列交叉验证
# ============================================================
def time_series_cv_evaluate(
    X: np.ndarray,  # noqa: N803
    y: np.ndarray,
    config: dict,
    n_splits: int = 5,
    use_purged: bool = True,
    embargo_pct: float = 0.01,
) -> dict[str, Any]:
    """时间序列交叉验证评估 (v8.3.2: Purged KFold 防泄漏)

    使用 Purged TimeSeriesSplit 进行滚动窗口验证:
    - 标准 TSCV 在 train/test 交接处存在标签重叠泄漏
    - Purged KFold 在分割点前后剔除 embargo 样本, 切断泄漏路径
    - 参考: De Prado (2018) "Advances in Financial ML" Ch.7

    Args:
        X: 特征矩阵
        y: 目标变量 (N 日 forward return)
        config: 训练配置
        n_splits: CV 折数
        use_purged: 是否使用 Purged KFold (默认 True)
        embargo_pct: embargo 比例 (默认1%, 即剔除约1%的边界样本)

    Returns:
        {
            "fold_metrics": [{"r2": ..., "ic": ..., "sharpe": ...}, ...],
            "mean_r2": ..., "std_r2": ...,
            "mean_ic": ..., "std_ic": ...,
            "mean_sharpe": ..., "std_sharpe": ...,
            "feature_importances": np.ndarray,
            "purged_kfold_used": bool,
            "overfitting_diagnosis": Dict,
        }
    """
    from lightgbm import LGBMRegressor

    n_samples = X.shape[0]
    fold_metrics = []
    all_importances = []
    n_features = X.shape[1]

    # --- 分割器选择 ---
    if use_purged and n_samples >= 100:
        from utils.purged_kfold import overfitting_diagnosis, purged_timeseries_split

        folds = list(purged_timeseries_split(n_samples, n_splits=n_splits, embargo_pct=embargo_pct))
        logger.info(
            "使用 Purged KFold (n_splits=%d, embargo_pct=%.2f%%), 共 %d 折",
            n_splits,
            embargo_pct * 100,
            len(folds),
        )
    else:
        from sklearn.model_selection import TimeSeriesSplit

        tscv = TimeSeriesSplit(n_splits=n_splits)
        folds = list(tscv.split(X))
        logger.info("使用标准 TimeSeriesSplit (n_splits=%d), 共 %d 折", n_splits, len(folds))

    # --- 各折独立验证: 检查 train/test 间隔 ---
    from utils.purged_kfold import validate_embargo

    embargo_checks = []
    for fold_idx, (train_idx, test_idx) in enumerate(folds):
        ok, msg = validate_embargo(train_idx, test_idx, min_gap=1)
        embargo_checks.append({"fold": fold_idx + 1, "pass": ok, "message": msg})
        if not ok:
            logger.warning("Fold %d 间隔不足: %s", fold_idx + 1, msg)

    # --- 训练 ---
    for fold_idx, (train_idx, test_idx) in enumerate(folds):
        X_train_fold, X_test_fold = X[train_idx], X[test_idx]  # noqa: N806
        y_train_fold, y_test_fold = y[train_idx], y[test_idx]

        if len(X_train_fold) < 50 or len(X_test_fold) < 10:
            logger.warning(
                "Fold %d 样本量不足 (train=%d, test=%d), 跳过",
                fold_idx + 1,
                len(X_train_fold),
                len(X_test_fold),
            )
            continue

        model = LGBMRegressor(**config["lgb_params"])
        model.fit(
            X_train_fold,
            y_train_fold,
            eval_set=[(X_test_fold, y_test_fold)],
            callbacks=[
                __import__("lightgbm").early_stopping(
                    stopping_rounds=config["early_stopping_rounds"],
                    verbose=False,
                ),
            ],
        )

        y_pred = model.predict(X_test_fold)

        # 指标计算
        r2 = _r2_score(y_test_fold, y_pred)
        ic = _ic_score(y_test_fold, y_pred)
        sharpe = _signal_sharpe(y_test_fold, y_pred)

        fold_metrics.append(
            {
                "fold": fold_idx + 1,
                "train_size": len(train_idx),
                "test_size": len(test_idx),
                "gap_samples": (int(test_idx[0] - train_idx[-1]) if len(train_idx) and len(test_idx) else 0),
                "r2": round(r2, 4),
                "ic": round(ic, 4),
                "sharpe": round(sharpe, 4),
                "best_iteration": (
                    int(model.best_iteration_)
                    if hasattr(model, "best_iteration_")
                    else config["lgb_params"]["n_estimators"]
                ),
            }
        )

        all_importances.append(model.feature_importances_)

    if not fold_metrics:
        return {
            "fold_metrics": [],
            "mean_r2": -999,
            "std_r2": 0,
            "mean_ic": 0,
            "std_ic": 0,
            "mean_sharpe": 0,
            "std_sharpe": 0,
            "feature_importances": np.zeros(n_features),
            "purged_kfold_used": use_purged,
            "embargo_checks": embargo_checks,
            "overfitting_diagnosis": {"overall_pass": None, "summary": "无数据"},
        }

    r2s = [f["r2"] for f in fold_metrics]
    ics = [f["ic"] for f in fold_metrics]
    sharps = [f["sharpe"] for f in fold_metrics]

    # 平均特征重要性
    mean_importances = np.mean(all_importances, axis=0)

    # --- 过拟合诊断 (NEW) ---
    of_diag = {"overall_pass": None, "summary": "无诊断"}
    if use_purged and len(fold_metrics) >= 2:
        from utils.purged_kfold import overfitting_diagnosis

        of_diag = overfitting_diagnosis(fold_metrics)
        if not of_diag.get("overall_pass", True):
            logger.warning(
                "[OverfitDiagnosis] %s — %d 项指标异常",
                of_diag.get("summary", ""),
                of_diag.get("total_issues", 0),
            )
        else:
            logger.info("[OverfitDiagnosis] PASS — 无过拟合迹象")

    return {
        "fold_metrics": fold_metrics,
        "mean_r2": round(float(np.mean(r2s)), 4),
        "std_r2": round(float(np.std(r2s)), 4),
        "mean_ic": round(float(np.mean(ics)), 4),
        "std_ic": round(float(np.std(ics)), 4),
        "mean_sharpe": round(float(np.mean(sharps)), 4),
        "std_sharpe": round(float(np.std(sharps)), 4),
        "feature_importances": mean_importances,
        "purged_kfold_used": use_purged,
        "embargo_checks": embargo_checks,
        "overfitting_diagnosis": of_diag,
    }


# ============================================================
# 指标函数
# ============================================================
def _r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2) + 1e-9
    return 1 - ss_res / ss_tot


def _ic_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 5:
        return 0.0
    if np.std(y_pred) < 1e-9:
        return 0.0
    return float(np.corrcoef(y_true, y_pred)[0, 1])


def _signal_sharpe(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    signal = np.sign(y_pred)
    returns = signal * y_true
    if returns.std() < 1e-9:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


# ============================================================
# 特征选择
# ============================================================
def select_features_by_importance(
    feature_cols: list[str],
    importances: np.ndarray,
    threshold: float = 5.0,
    top_n: int = 15,
) -> list[str]:
    """根据 CV 平均特征重要性筛选特征

    Args:
        feature_cols: 原始特征列
        importances: CV 平均重要性数组
        threshold: 重要性下限 (低于此值剔除)
        top_n: 最多保留 Top N

    Returns:
        筛选后的特征列
    """
    importance_series = pd.Series(importances, index=feature_cols)
    # 筛选
    selected = importance_series[importance_series >= threshold]
    if len(selected) == 0:
        # 全部低于阈值, 取 Top N
        selected = importance_series.sort_values(ascending=False).head(top_n)
    else:
        # 按重要性排序, 取 Top N
        selected = selected.sort_values(ascending=False).head(top_n)
    return list(selected.index)


# ============================================================
# 训练单标的 (CV + 最终模型)
# ============================================================
def train_symbol_with_cv(
    symbol: str,
    df: pd.DataFrame,
    config: dict,
) -> dict[str, Any]:
    """训练单标的: CV 评估 -> 特征选择 -> 最终模型

    流程:
        1. 全特征 CV 评估 (5折 TimeSeriesSplit)
        2. 根据 CV 平均重要性筛选 Top 15 特征
        3. 用筛选后的特征 + 全部训练数据 (含 holdout) 训练最终模型
        4. holdout 测试集给出最终指标

    Args:
        symbol: 标的代码
        df: 含特征 + close 的 DataFrame
        config: 训练配置

    Returns:
        训练结果字典
    """
    import lightgbm as lgb
    from lightgbm import LGBMRegressor

    # 构造目标: 次日收益率
    df = df.copy()
    df["target"] = df["close"].pct_change().shift(-1)
    # P1-1 修复 (2026-09-01): dropna 丢弃 target=NaN 的最后 1 行,
    # 之后 iloc[-1:] 取到 T-1 行 → 当前信号滞后 1 个交易日。
    # 先保存真实最新行 (整行, selected_features 稍后才确定)。
    latest_raw = df.iloc[[-1]]
    df = df.dropna()

    if len(df) < config["min_samples"]:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": f"样本不足 ({len(df)} < {config['min_samples']})",
        }

    # 全部特征列
    all_feature_cols = [c for c in df.columns if c not in ["open", "high", "low", "close", "volume", "target"]]
    X_all = np.asarray(df[all_feature_cols].values, dtype=np.float64)  # noqa: N806
    y_all = np.asarray(df["target"].values, dtype=np.float64)

    # === Step 1: 全特征 CV 评估 ===
    cv_result = time_series_cv_evaluate(X_all, y_all, config, n_splits=config["n_splits"])

    # === Step 2: 特征选择 ===
    selected_features = select_features_by_importance(
        all_feature_cols,
        cv_result["feature_importances"],
        threshold=config["feature_selection_threshold"],
        top_n=config["top_n_features"],
    )

    # === Step 3: 用筛选后的特征重新 CV (对比) ===
    x_selected = np.asarray(df[selected_features].values, dtype=np.float64)
    cv_after_selection = time_series_cv_evaluate(x_selected, y_all, config, n_splits=config["n_splits"])

    # === Step 4: 最终模型 (用筛选特征 + 全部数据) ===
    # holdout: 最后 20% 作为最终测试
    n_test = max(int(len(df) * config["test_ratio"]), 20)
    n_train = len(df) - n_test
    train_df = df.iloc[:n_train]
    test_df = df.iloc[n_train:]

    x_train = np.asarray(train_df[selected_features].values, dtype=np.float64)
    y_train = np.asarray(train_df["target"].values, dtype=np.float64)
    x_test = np.asarray(test_df[selected_features].values, dtype=np.float64)
    y_test = np.asarray(test_df["target"].values, dtype=np.float64)

    final_model = LGBMRegressor(**config["lgb_params"])
    final_model.fit(
        x_train,
        y_train,
        eval_set=[(x_test, y_test)],
        callbacks=[
            lgb.early_stopping(
                stopping_rounds=config["early_stopping_rounds"],
                verbose=False,
            ),
        ],
    )

    y_pred = final_model.predict(x_test)
    final_r2 = _r2_score(y_test, y_pred)
    final_ic = _ic_score(y_test, y_pred)
    final_sharpe = _signal_sharpe(y_test, y_pred)

    # 最新信号
    # P1-1 修复: 用 dropna 前保存的真实最新行 (T), 并做与训练一致的 nan/inf 清洗
    latest_features = np.asarray(latest_raw[selected_features].values, dtype=np.float64)
    latest_features = np.nan_to_num(latest_features, nan=0.0, posinf=0.0, neginf=0.0)
    latest_pred = float(final_model.predict(latest_features)[0])
    signal = float(np.tanh(latest_pred * 100))

    # 特征重要性
    feat_imp = pd.Series(final_model.feature_importances_, index=selected_features).sort_values(ascending=False)

    best_iter = (
        int(final_model.best_iteration_)
        if hasattr(final_model, "best_iteration_")
        else config["lgb_params"]["n_estimators"]
    )

    return {
        "status": "OK",
        "symbol": symbol,
        "n_samples": len(df),
        "n_features_before": len(all_feature_cols),
        "n_features_after": len(selected_features),
        "selected_features": selected_features,
        "n_train": n_train,
        "n_test": n_test,
        "train_period": f"{train_df.index[0].date()} → {train_df.index[-1].date()}",
        "test_period": f"{test_df.index[0].date()} → {test_df.index[-1].date()}",
        "best_iteration": best_iter,
        "cv_before_selection": {
            "mean_r2": cv_result["mean_r2"],
            "std_r2": cv_result["std_r2"],
            "mean_ic": cv_result["mean_ic"],
            "std_ic": cv_result["std_ic"],
            "mean_sharpe": cv_result["mean_sharpe"],
            "std_sharpe": cv_result["std_sharpe"],
            "fold_metrics": cv_result["fold_metrics"],
        },
        "cv_after_selection": {
            "mean_r2": cv_after_selection["mean_r2"],
            "std_r2": cv_after_selection["std_r2"],
            "mean_ic": cv_after_selection["mean_ic"],
            "std_ic": cv_after_selection["std_ic"],
            "mean_sharpe": cv_after_selection["mean_sharpe"],
            "std_sharpe": cv_after_selection["std_sharpe"],
            "fold_metrics": cv_after_selection["fold_metrics"],
        },
        "final_metrics": {
            "r2": round(final_r2, 4),
            "ic": round(final_ic, 4),
            "sharpe": round(final_sharpe, 4),
        },
        "signal": round(signal, 4),
        "raw_prediction": round(latest_pred, 6),
        "top_features": feat_imp.to_dict(),
        "model": final_model,
    }


# ============================================================
# 模型持久化
# ============================================================
def save_model(symbol: str, result: dict, config: dict) -> dict:
    """保存模型与元数据"""
    symbol_dir = MODELS_DIR / symbol
    symbol_dir.mkdir(exist_ok=True)

    model_path = symbol_dir / f"{symbol}_lgb_tscv_model.pkl"
    meta_path = symbol_dir / f"{symbol}_meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(result["model"], f)

    meta = {
        "symbol": symbol,
        "saved_at": now_bj().isoformat(),
        "model_type": "LightGBM_TSCV",
        "n_samples": result["n_samples"],
        "n_features_before": result["n_features_before"],
        "n_features_after": result["n_features_after"],
        "selected_features": result["selected_features"],
        "train_period": result["train_period"],
        "test_period": result["test_period"],
        "best_iteration": result["best_iteration"],
        "cv_before_selection": result["cv_before_selection"],
        "cv_after_selection": result["cv_after_selection"],
        "final_metrics": result["final_metrics"],
        "signal": result["signal"],
        "raw_prediction": result["raw_prediction"],
        "top_features": result["top_features"],
        "config": {
            "lgb_params": config["lgb_params"],
            "early_stopping_rounds": config["early_stopping_rounds"],
            "n_splits": config["n_splits"],
            "test_ratio": config["test_ratio"],
            "top_n_features": config["top_n_features"],
            "feature_selection_threshold": config["feature_selection_threshold"],
        },
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    return {
        "model_path": str(model_path),
        "meta_path": str(meta_path),
    }


def load_model_meta(symbol: str) -> dict | None:
    """加载已有模型的元数据"""
    meta_path = MODELS_DIR / symbol / f"{symbol}_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def should_retrain(symbol: str, config: dict) -> bool:
    meta = load_model_meta(symbol)
    if meta is None:
        return True
    saved_at = datetime.fromisoformat(meta["saved_at"])
    age_days = (now_bj() - saved_at).days
    return age_days >= config["retrain_interval_days"]


# ============================================================
# 主训练流程
# ============================================================
def run_lgb_tscv_training(
    symbols: list[tuple] | None = None,
    force_retrain: bool = False,
    config: dict | None = None,
    predictor: str = "lightgbm",
    post_train_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """执行 LightGBM + TSCV 训练

    Args:
        symbols: 标的清单
        force_retrain: 强制重训
        config: 训练配置
        predictor: 预测器 (lightgbm/timesfm/hybrid, S2 集成)
        post_train_callback: 训练后回调钩子 (ER-1.1)，接收训练结果字典；
            None 时训练行为不变（向后兼容）；回调异常 fail-safe 降级不阻断训练

    Returns:
        训练结果汇总
    """
    from autolearn_trainer import invoke_post_train_callback

    # S2: timesfm 预测器可用性检查 (向后兼容, 默认 lightgbm 不受影响)
    timesfm_predictor = None
    if predictor in ("timesfm", "hybrid"):
        try:
            from utils.timesfm_predictor import TimesFMPredictor

            timesfm_predictor = TimesFMPredictor()
            if not timesfm_predictor.available:
                logger.warning(
                    "predictor=%s 但 TimesFM 不可用 (未安装/预检失败), 降级到 lightgbm",
                    predictor,
                )
                if predictor == "timesfm":
                    predictor = "lightgbm"
            else:
                logger.info("TimesFM 预测器可用, predictor=%s", predictor)
        except (ImportError, AttributeError, ModuleNotFoundError, OSError) as exc:
            logger.warning("TimesFMPredictor 导入失败, 降级到 lightgbm: %s", exc)
            predictor = "lightgbm"
    if config is None:
        config = LGB_TSCV_CONFIG
    if symbols is None:
        symbols = POSITION_SYMBOLS

    logger.info("#" * 70)
    logger.info("# LightGBM + 时间序列交叉验证训练")
    logger.info(f"# 标的数: {len(symbols)}")
    logger.info(f"# CV 折数: {config['n_splits']}")
    logger.info(f"# 早停轮次: {config['early_stopping_rounds']}")
    logger.info("#" * 70)

    # Step 1+2: 加载真实 K 线 (P0-M4, 2026-09-13)
    # 原流程: 真实收盘价 → 收益率 → 随机噪声合成 OHLCV → 训练 (ATR/振幅类因子
    # 建立在伪造数据上)。现改为真实 K 线优先; 合成仅作显式授权的降级路径
    # (QUANT_ALLOW_SYNTHETIC_OHLCV=1, 限冒烟/管线验证, 不用于真实模型)。
    logger.info("Step 1: 加载真实历史 K 线")
    try:
        ohlcv_dict = load_ohlcv_history()
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"  真实 K 线加载失败: {e}")
        ohlcv_dict = {}
    logger.info(f"  真实 OHLCV: {len(ohlcv_dict)} 标的")

    # Step 1b: 幸存者偏差修复 (P0-M1) — 合入退市股真实 OHLCV (永久缓存,
    # 新拉数量受 QUANT_DELISTED_MAX 限, 默认 30); 失败不阻断训练 (降级纯存活池)
    delisted_symbols: list[tuple] = []
    try:
        from utils.universe.survivorship_free_universe import expand_training_universe

        ohlcv_dict, delisted_symbols = expand_training_universe(ohlcv_dict)
        if delisted_symbols:
            symbols = list(symbols) + delisted_symbols
    except Exception as e:  # noqa: BLE001 — 退市样本是增强项, 失败降级为纯存活池
        logger.warning("[P0-M1] 退市样本合入失败, 使用纯存活池: %s", e)

    if len(ohlcv_dict) < 2:
        # 真实数据不足以训练: 仅在显式授权下回退合成 (原实现无条件合成)
        from utils.runtime_mode import env_flag

        if not env_flag("QUANT_ALLOW_SYNTHETIC_OHLCV"):
            logger.error(
                "[P0-M4] 真实 K 线不足且未授权合成 — 拒绝在伪造 OHLCV 上训练。"
                "如确需管线冒烟, 设置 QUANT_ALLOW_SYNTHETIC_OHLCV=1 (产物不得上线)"
            )
            return {
                "status": "FAIL",
                "error": (
                    f"real_ohlcv_insufficient: {len(ohlcv_dict)} 标的可用 "
                    "(合成回退需 QUANT_ALLOW_SYNTHETIC_OHLCV=1)"
                ),
            }
        logger.warning("[P0-M4] 显式授权合成回退 (QUANT_ALLOW_SYNTHETIC_OHLCV=1) — 冒烟用途")
        try:
            returns_df = load_returns_history()
            ohlcv_dict = synthesize_ohlcv_from_returns(returns_df)
            logger.warning(
                f"  合成 OHLCV 回退完成: {len(ohlcv_dict)} 标的 (attrs[synthetic]=True, 不得用于真实模型)"
            )
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"  合成回退失败: {e}")
            return {"status": "FAIL", "error": str(e)}

    # Step 3: 特征工程
    logger.info("Step 3: 特征工程 (37 因子)")
    featured_dict = {}
    for symbol, df in ohlcv_dict.items():
        df_feat = add_technical_features(df)
        featured_dict[symbol] = df_feat
    featured_dict = add_cross_sectional_features(featured_dict)

    # Step 4: 训练
    logger.info("Step 4: 训练 LightGBM + TSCV")
    results = {}
    saved = 0
    skipped = 0
    failed = 0

    for code, _suffix, _, name, style in symbols:
        if code not in featured_dict:
            logger.warning(f"  [SKIP] {code} ({name}): 无历史数据")
            skipped += 1
            continue

        if not force_retrain and not should_retrain(code, config):
            meta = load_model_meta(code)
            logger.info(f"  [CACHED] {code} ({name}): 模型未过期, 跳过")
            results[code] = {
                "status": "CACHED",
                "symbol": code,
                "name": name,
                "style": style,
                "meta": meta,
            }
            continue

        logger.info(f"  [TRAIN] {code} ({name})...")
        try:
            result = train_symbol_with_cv(code, featured_dict[code], config)
            if result["status"] != "OK":
                logger.warning(f"  [SKIP] {code}: {result.get('reason')}")
                skipped += 1
                results[code] = result
                continue

            # 质量评估 (使用 CV 后筛选的指标)
            cv_metrics = result["cv_after_selection"]
            quality_ok = (
                cv_metrics["mean_r2"] >= config["model_quality_threshold"]["min_cv_r2"]
                and cv_metrics["mean_ic"] >= config["model_quality_threshold"]["min_cv_ic"]
                and cv_metrics["mean_sharpe"] >= config["model_quality_threshold"]["min_cv_sharpe"]
            )

            if not quality_ok:
                logger.warning(
                    f"  [LOW_QUALITY] {code}: "
                    f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
                    f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}"
                )
                result["quality_flag"] = "LOW_QUALITY"
            else:
                result["quality_flag"] = "OK"

            paths = save_model(code, result, config)
            saved += 1
            logger.info(
                f"  [SAVED] {code}: "
                f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
                f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}, "
                f"Sharpe={cv_metrics['mean_sharpe']}±{cv_metrics['std_sharpe']}, "
                f"final_r2={result['final_metrics']['r2']}, "
                f"signal={result['signal']}, "
                f"best_iter={result['best_iteration']}, "
                f"features={result['n_features_before']}→{result['n_features_after']}"
            )
            results[code] = {**result, "paths": paths, "name": name, "style": style}

        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"  [FAIL] {code}: {e}", exc_info=True)
            failed += 1
            results[code] = {"status": "FAIL", "symbol": code, "error": str(e)}

    # Step 5: 生成集成信号
    logger.info("Step 5: 生成集成信号")
    signals = {}
    for code, res in results.items():
        if res.get("status") in ("OK", "CACHED"):
            signals[code] = {
                "signal": res.get("signal", res.get("meta", {}).get("signal", 0)),
                "name": res.get("name", ""),
                "style": res.get("style", ""),
                "quality_flag": res.get("quality_flag", "CACHED"),
                "model_type": "LightGBM_TSCV",
            }

    signals_path = MODELS_DIR / "lgb_tscv_signals.json"
    signals_data = {
        "generated_at": now_bj().isoformat(),
        "trade_date": now_bj().strftime("%Y-%m-%d"),
        "model_type": "LightGBM_TSCV",
        "signals": {code: s for code, s in signals.items()},
        "summary": {
            "total": len(signals),
            "bullish": sum(1 for s in signals.values() if s["signal"] > 0.2),
            "bearish": sum(1 for s in signals.values() if s["signal"] < -0.2),
            "neutral": sum(1 for s in signals.values() if abs(s["signal"]) <= 0.2),
        },
    }
    with open(signals_path, "w", encoding="utf-8") as f:
        json.dump(signals_data, f, ensure_ascii=False, indent=2)
    logger.info(f"  信号文件: {signals_path}")

    result = {
        "status": "OK",
        "total": len(symbols),
        "trained": saved,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "signals": signals_data,
    }

    # Step 6: post_train_callback 钩子 (ER-1.1, Wave 7-ERL Sprint 1)
    # 训练完成后调用回调，用于训练→进化→再平衡联动；fail-safe 降级
    invoke_post_train_callback(post_train_callback, result, logger_name="lgb_tscv")

    return result


# ============================================================
# 对比报告
# ============================================================
def _load_old_model_metrics(code: str, old_models_dir: Path) -> tuple[Any, Any]:
    """加载旧模型元数据中的 ensemble_r2 / ensemble_ic"""
    old_meta_path = old_models_dir / code / f"{code}_meta.json"
    old_r2 = "N/A"
    old_ic = "N/A"
    if old_meta_path.exists():
        with open(old_meta_path, encoding="utf-8") as f:
            old_meta = json.load(f)
        old_r2 = old_meta.get("metrics", {}).get("ensemble_r2", "N/A")
        old_ic = old_meta.get("metrics", {}).get("ensemble_ic", "N/A")
    return old_r2, old_ic


def _extract_new_metrics(r: dict) -> tuple[Any, Any, float, float]:
    """从结果中提取新模型指标"""
    if r.get("status") == "OK":
        new_r2 = r["cv_after_selection"]["mean_r2"]
        new_ic = r["cv_after_selection"]["mean_ic"]
        new_r2_std = r["cv_after_selection"]["std_r2"]
        new_ic_std = r["cv_after_selection"]["std_ic"]
    else:
        new_r2 = r.get("meta", {}).get("cv_after_selection", {}).get("mean_r2", "N/A")
        new_ic = r.get("meta", {}).get("cv_after_selection", {}).get("mean_ic", "N/A")
        new_r2_std = r.get("meta", {}).get("cv_after_selection", {}).get("std_r2", 0)
        new_ic_std = r.get("meta", {}).get("cv_after_selection", {}).get("std_ic", 0)
    return new_r2, new_ic, new_r2_std, new_ic_std


def _compute_improvement(old_val: Any, new_val: Any) -> str:
    """计算指标改进值"""
    if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
        diff = round(new_val - old_val, 4)
        return f"{diff:+.4f}"
    return "N/A"


def _build_report_header(result: dict, report_path: Path) -> list[str]:
    """构建报告头部"""
    return [
        f"# LightGBM + TSCV 训练报告 - {now_bj().strftime('%Y-%m-%d')}",
        "",
        f"**生成时间**: {now_bj().isoformat()}",
        "**模型类型**: 纯 LightGBM + TimeSeriesSplit (5折)",
        f"**标的数**: {result['total']}",
        f"**训练成功**: {result['trained']}",
        f"**跳过**: {result['skipped']}",
        f"**失败**: {result['failed']}",
        "",
        "## 优化点",
        "",
        "1. **换模型**: 纯 LightGBM (移除 XGBoost)",
        "   - 早停 (early_stopping_rounds=50) 防过拟合",
        "   - n_estimators 200→1000, learning_rate 0.05→0.01",
        "   - max_depth 4→6",
        "2. **时间序列交叉验证**: TimeSeriesSplit 5折滚动窗口",
        "   - 训练集逐步扩大, 更贴近实盘",
        "   - 输出 mean±std 作为稳健评估",
        "3. **特征选择**: 基于 CV 重要性筛选 Top 15",
        "   - 去除低重要性特征 (阈值 5)",
        "   - 减少噪音, 提升泛化",
        "",
        "## 一、新旧模型对比 (CV R²)",
        "",
        "| 标的 | 名称 | 旧集成 R² | 新 LGB CV R² | 改进 | 旧 IC | 新 CV IC | 改进 |",
        "|------|------|---------|------------|------|-------|---------|------|",
    ]


def _build_comparison_table(result: dict, old_models_dir: Path) -> tuple[list[str], list[float], list[float]]:
    """构建新旧模型对比表格"""
    lines = []
    improvements_r2: list[float] = []
    improvements_ic: list[float] = []

    for code, r in result["results"].items():
        if r.get("status") not in ("OK", "CACHED"):
            continue
        name = r.get("name", "")
        old_r2, old_ic = _load_old_model_metrics(code, old_models_dir)
        new_r2, new_ic, new_r2_std, new_ic_std = _extract_new_metrics(r)

        r2_improve = _compute_improvement(old_r2, new_r2)
        ic_improve = _compute_improvement(old_ic, new_ic)

        if isinstance(old_r2, (int, float)) and isinstance(new_r2, (int, float)):
            improvements_r2.append(round(new_r2 - old_r2, 4))
        if isinstance(old_ic, (int, float)) and isinstance(new_ic, (int, float)):
            improvements_ic.append(round(new_ic - old_ic, 4))

        lines.append(
            f"| {code} | {name} | {old_r2} | {new_r2}±{new_r2_std} | {r2_improve} | "
            f"{old_ic} | {new_ic}±{new_ic_std} | {ic_improve} |"
        )

    return lines, improvements_r2, improvements_ic


def _build_improvement_summary(improvements_r2: list[float], improvements_ic: list[float]) -> list[str]:
    """构建整体改进汇总"""
    if not improvements_r2:
        return []
    return [
        "",
        "## 二、整体改进汇总",
        "",
        f"- R² 平均改进: **{np.mean(improvements_r2):+.4f}**",
        f"- R² 改进标的数: {sum(1 for x in improvements_r2 if x > 0)} / {len(improvements_r2)}",
        f"- IC 平均改进: **{np.mean(improvements_ic):+.4f}**",
        f"- IC 改进标的数: {sum(1 for x in improvements_ic if x > 0)} / {len(improvements_ic)}",
    ]


def _build_cv_details_section(result: dict) -> list[str]:
    """构建 CV 详情表格"""
    lines = [
        "",
        "## 三、CV 详情 (特征选择后)",
        "",
        "| 标的 | 名称 | Fold 数 | CV R² (mean±std) | CV IC (mean±std) | CV Sharpe | 最终 R² | 最终 IC | 信号 | 特征数 |",  # noqa: E501
        "|------|------|---------|------------------|------------------|-----------|---------|--------|------|--------|",  # noqa: E501
    ]
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        cv = r["cv_after_selection"]
        fm = r["final_metrics"]
        n_folds = len(cv["fold_metrics"])
        lines.append(
            f"| {code} | {r.get('name', '')} | {n_folds} | "
            f"{cv['mean_r2']}±{cv['std_r2']} | "
            f"{cv['mean_ic']}±{cv['std_ic']} | "
            f"{cv['mean_sharpe']}±{cv['std_sharpe']} | "
            f"{fm['r2']} | {fm['ic']} | {r['signal']} | "
            f"{r['n_features_before']}→{r['n_features_after']} |"
        )
    return lines


def _build_top_features_section(result: dict) -> list[str]:
    """构建特征重要性章节"""
    lines = [
        "",
        "## 四、特征重要性 (Top 5)",
        "",
    ]
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        top_feat = r.get("top_features", {})
        if not top_feat:
            continue
        lines.append(f"### {code} ({r.get('name', '')})")
        lines.append("")
        for i, (feat, imp) in enumerate(list(top_feat.items())[:5], 1):
            lines.append(f"{i}. **{feat}**: {imp}")
        lines.append("")
    return lines


def _build_cv_folds_section(result: dict) -> list[str]:
    """构建 CV 折详情章节 (第一个 OK 标的)"""
    lines = [
        "",
        "## 五、CV 折详情 (第一个 OK 标的)",
        "",
    ]
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        cv = r["cv_after_selection"]
        lines.append(f"### {code} ({r.get('name', '')}) - 特征选择后")
        lines.append("")
        lines.append("| Fold | 训练样本 | 测试样本 | R² | IC | Sharpe | 最佳迭代 |")
        lines.append("|------|---------|---------|-----|-----|--------|---------|")
        for fold in cv["fold_metrics"]:
            lines.append(
                f"| {fold['fold']} | {fold['train_size']} | {fold['test_size']} | "
                f"{fold['r2']} | {fold['ic']} | {fold['sharpe']} | {fold['best_iteration']} |"
            )
        break
    return lines


def _build_risk_notes(report_path: Path) -> list[str]:
    """构建风险提示"""
    return [
        "",
        "## 六、风险提示",
        "",
        "- 历史数据仅含日收益率, OHLCV 为合成数据, 实际特征质量受限",
        "- TimeSeriesSplit CV 更稳健, 但样本数仍偏少 (~191 日)",
        "- 早停可能让模型欠拟合, 如 R² 仍为负, 建议放宽 early_stopping_rounds",
        "- IC 为负说明预测方向相反, 可考虑反向操作或检查数据/标签",
        "",
        "---",
        f"**报告路径**: `{report_path}`",
        "**信号文件**: `models/lgb_tscv/lgb_tscv_signals.json`",
    ]


def generate_comparison_report(result: dict) -> Path:
    """生成新旧模型对比报告"""
    today = now_bj().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"lgb_tscv_report_{today}.md"

    old_models_dir = BASE_DIR / "models" / "autolearn"

    lines = _build_report_header(result, report_path)
    comparison_lines, improvements_r2, improvements_ic = _build_comparison_table(result, old_models_dir)
    lines.extend(comparison_lines)
    lines.extend(_build_improvement_summary(improvements_r2, improvements_ic))
    lines.extend(_build_cv_details_section(result))
    lines.extend(_build_top_features_section(result))
    lines.extend(_build_cv_folds_section(result))
    lines.extend(_build_risk_notes(report_path))

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"对比报告已生成: {report_path}")
    return report_path


# ============================================================
# CLI
# ============================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="LightGBM + 时间序列交叉验证训练",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--force-retrain", action="store_true", help="强制重训所有标的")
    parser.add_argument("--symbols", nargs="+", default=None, help="指定标的代码 (默认全部持仓)")
    parser.add_argument(
        "--predictor",
        choices=["lightgbm", "timesfm", "hybrid"],
        default="lightgbm",
        help="预测器: lightgbm(默认) / timesfm(零样本) / hybrid(融合, S2 集成)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_DIR / f"lgb_tscv_{now_bj():%Y%m%d}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    symbols = POSITION_SYMBOLS
    if args.symbols:
        symbols = [s for s in POSITION_SYMBOLS if s[0] in args.symbols]

    result = run_lgb_tscv_training(
        symbols=symbols,
        force_retrain=args.force_retrain,
        predictor=args.predictor,
    )

    if result["status"] == "OK":
        report_path = generate_comparison_report(result)
        logger.info(f"\n✓ 训练完成, 对比报告: {report_path}")
        logger.info("✓ 信号文件: models/lgb_tscv/lgb_tscv_signals.json")
        sys.exit(0)
    else:
        logger.info("\n✗ 训练失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
