"""训练器核心层 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - train_lgb_with_fallback: LightGBM 训练 (GPU→CPU 自动回退, 全局禁用机制)
  - train_symbol_enhanced: 单标的训练 (真实OHLCV + 情绪因子 + 放宽早停 + 自适应重训)
  - compute_regime_series: 大盘 regime 序列计算 (bull/bear/choppy/rebound)
  - train_symbol_regime_specific: V9 regime-specific 双模型训练 (bull / non-bull)
  - run_enhanced_training: 完整训练流程编排 (5 步: OHLCV → 特征 → 训练 → 持久化 → 信号)

设计原则:
  - 训练函数与持久化解耦: save_model/load_model_meta 在 persistence.py
  - 训练函数与评估解耦: CV/特征选择 在 metrics.py
  - 训练函数与特征工程解耦: add_*_features 在 feature_engineering.py / news_sentiment.py
  - 路径/配置由主模块通过 configure_paths 注入, 避免硬编码

外部依赖:
  - autolearn_trainer.POSITION_SYMBOLS / add_technical_features / add_cross_sectional_features
  - utils.purged_kfold.purged_timeseries_split (经 metrics.py 间接调用)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径/配置 (由主模块注入, 避免硬编码)
# ============================================================
BASE_DIR: Path = Path(__file__).resolve().parent.parent
MODELS_DIR: Path = BASE_DIR / "models" / "lgb_enhanced"


def configure_paths(base_dir: Path, models_dir: Path) -> None:
    """由主模块注入路径。

    Args:
        base_dir: 项目根目录
        models_dir: 模型输出目录
    """
    global BASE_DIR, MODELS_DIR
    BASE_DIR = base_dir
    MODELS_DIR = models_dir


# ============================================================
# v8.7: GPU→CPU 自动回退训练器
# ============================================================
import threading

# 全局标志: 一旦 GPU 训练失败, 后续所有训练直接用 CPU, 避免重复失败
# H4修复: 添加线程锁保护全局可变状态, 防止多线程并发训练时的竞态条件
_GLOBAL_GPU_DISABLED: bool = False
_GLOBAL_GPU_DISABLED_LOCK = threading.Lock()


def train_lgb_with_fallback(
    X_train: np.ndarray,  # noqa: N803
    y_train: np.ndarray,
    X_eval: np.ndarray,  # noqa: N803
    y_eval: np.ndarray,
    config: dict[str, Any],
    log_tag: str = "",
) -> tuple[Any, str]:
    """训练 LightGBM 模型 (GPU 失败自动回退 CPU)

    Args:
        X_train, y_train: 训练集
        X_eval, y_eval: 验证集 (用于早停)
        config: 训练配置 (含 lgb_params)
        log_tag: 日志标签 (如标的代码)

    Returns:
        (model, device_used) 元组, device_used ∈ {"gpu", "cpu"}
    """
    from lightgbm import LGBMRegressor, early_stopping

    global _GLOBAL_GPU_DISABLED
    params = dict(config["lgb_params"])

    # H4修复: 线程安全读取 GPU 禁用标志
    with _GLOBAL_GPU_DISABLED_LOCK:
        gpu_disabled = _GLOBAL_GPU_DISABLED
    if gpu_disabled and params.get("device_type") == "gpu":
        params["device_type"] = "cpu"
        params.pop("gpu_platform_id", None)
        params.pop("gpu_device_id", None)

    callbacks = [early_stopping(stopping_rounds=config["early_stopping_rounds"], verbose=False)]

    # GPU 训练尝试
    if params.get("device_type") == "gpu":
        try:
            model = LGBMRegressor(**params)
            model.fit(X_train, y_train, eval_set=[(X_eval, y_eval)], callbacks=callbacks)
            return model, "gpu"
        except Exception as e:
            err_msg = str(e)[:150]
            logger.warning(f"  [{log_tag}] GPU 训练失败, 回退 CPU: {err_msg}")
            # H4修复: 线程安全设置 GPU 禁用标志
            with _GLOBAL_GPU_DISABLED_LOCK:
                _GLOBAL_GPU_DISABLED = True
            params["device_type"] = "cpu"
            params.pop("gpu_platform_id", None)
            params.pop("gpu_device_id", None)

    # CPU 训练 (回退或默认)
    model = LGBMRegressor(**params)
    model.fit(X_train, y_train, eval_set=[(X_eval, y_eval)], callbacks=callbacks)
    return model, params.get("device_type", "cpu")


# 向后兼容别名 (原 lgb_enhanced_trainer.py 使用带下划线名称)
_train_lgb_with_fallback = train_lgb_with_fallback


def reset_gpu_disabled_flag() -> None:
    """重置全局 GPU 禁用标志 (用于测试或重启 GPU 子系统)。"""
    global _GLOBAL_GPU_DISABLED
    with _GLOBAL_GPU_DISABLED_LOCK:
        _GLOBAL_GPU_DISABLED = False


# ============================================================
# 单标的训练
# ============================================================
def train_symbol_enhanced(
    symbol: str,
    df: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    """训练单标的: 真实OHLCV + 情绪因子 + 放宽早停

    流程:
        1. 全特征 CV 评估
        2. 特征选择
        3. 最终模型训练 (早停 200 轮)
        4. 自适应重训 (best_iter <= 5 时用更小学习率 + 更多估计器)

    Args:
        symbol: 标的代码
        df: 含真实 OHLCV + 技术因子 + 情绪因子的 DataFrame
        config: 训练配置

    Returns:
        训练结果字典 (status="OK" 包含 model/metrics/features, status="SKIP" 包含 reason)
    """
    # 延迟导入 (避免顶层循环依赖)
    from .metrics import (
        ic_score as _ic_score,
    )
    from .metrics import (
        r2_score as _r2_score,
    )
    from .metrics import (
        select_features_by_importance,
        time_series_cv_evaluate,
    )
    from .metrics import (
        signal_sharpe as _signal_sharpe,
    )

    df = df.copy()
    # === V6: 标签从次日收益率改为5日前向收益 ===
    # 动机: Window 1 (2023-07~2024-09) 年化仅2.47%, 根因是次日收益率标签在震荡市
    #       噪声过大, IC极低。5日前向收益与月度调仓周期更匹配, 且在震荡市中
    #       均值回归效应在5日horizon上更显著, 提升信号可预测性。
    # 权衡: 损失最后5天训练样本 (500天数据仅损失1%), 可接受。
    label_horizon = config.get("label_horizon", 5)
    df["target"] = df["close"].pct_change(label_horizon).shift(-label_horizon)
    # P1-1 修复 (2026-09-01): dropna 会丢弃 target=NaN 的最后 label_horizon 行,
    # 之后 iloc[-1:] 取到的是 T-h 行而非最新行 → 当前信号滞后 h 个交易日且
    # 预测的是已实现收益。先保存真实最新行 (整行, selected_features 稍后才确定)。
    latest_raw = df.iloc[[-1]]
    df = df.dropna()

    if len(df) < config["min_samples"]:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": f"样本不足 ({len(df)} < {config['min_samples']})",
        }

    all_feature_cols = [c for c in df.columns if c not in ["open", "high", "low", "close", "volume", "target"]]
    X_all = np.asarray(df[all_feature_cols].values, dtype=np.float64)  # noqa: N806
    y_all = np.asarray(df["target"].values, dtype=np.float64)

    # 替换 inf/nan
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=0.0, neginf=0.0)  # noqa: N806

    # === Step 1: 全特征 CV ===
    cv_result = time_series_cv_evaluate(X_all, y_all, config, n_splits=config["n_splits"], code=symbol)

    # === Step 2: 特征选择 (含情绪因子保护机制) ===
    selected_features = select_features_by_importance(
        all_feature_cols,
        cv_result["feature_importances"],
        threshold=config["feature_selection_threshold"],
        top_n=config["top_n_features"],
    )

    # === Step 3: 用筛选后的特征重新 CV ===
    x_selected = np.asarray(df[selected_features].values, dtype=np.float64)
    x_selected = np.nan_to_num(x_selected, nan=0.0, posinf=0.0, neginf=0.0)
    cv_after_selection = time_series_cv_evaluate(x_selected, y_all, config, n_splits=config["n_splits"], code=symbol)

    # === Step 4: 最终模型 ===
    n_test = max(int(len(df) * config["test_ratio"]), 20)
    n_train = len(df) - n_test
    train_df = df.iloc[:n_train]
    test_df = df.iloc[n_train:]

    x_train = np.asarray(train_df[selected_features].values, dtype=np.float64)
    y_train = np.asarray(train_df["target"].values, dtype=np.float64)
    x_test = np.asarray(test_df[selected_features].values, dtype=np.float64)
    y_test = np.asarray(test_df["target"].values, dtype=np.float64)

    x_train = np.nan_to_num(x_train, nan=0.0, posinf=0.0, neginf=0.0)
    x_test = np.nan_to_num(x_test, nan=0.0, posinf=0.0, neginf=0.0)

    final_model, _ = train_lgb_with_fallback(x_train, y_train, x_test, y_test, config, log_tag=f"{symbol}-final")

    y_pred = final_model.predict(x_test)
    final_r2 = _r2_score(y_test, y_pred)
    final_ic = _ic_score(y_test, y_pred)
    final_sharpe = _signal_sharpe(y_test, y_pred)

    # P1-1 修复: 用 dropna 前保存的真实最新行 (T) 生成当前信号, 而非被截断 df 的 T-h 行
    latest_features = np.asarray(latest_raw[selected_features].values, dtype=np.float64)
    latest_features = np.nan_to_num(latest_features, nan=0.0, posinf=0.0, neginf=0.0)
    latest_pred = float(final_model.predict(latest_features)[0])
    signal = float(np.tanh(latest_pred * 100))

    feat_imp = pd.Series(final_model.feature_importances_, index=selected_features).sort_values(ascending=False)

    best_iter = (
        int(final_model.best_iteration_)
        if hasattr(final_model, "best_iteration_")
        else config["lgb_params"]["n_estimators"]
    )

    # === Step 5: 自适应重训 (欠拟合标的) ===
    # best_iter <= 阈值 说明早停过早触发, 模型未学到足够模式
    # 使用更小学习率 + 更多估计器重训
    adaptive_retrained = False
    adaptive_threshold = config.get("adaptive_retrain_threshold", 5)
    if best_iter <= adaptive_threshold:
        adaptive_lr = config.get("adaptive_retrain_lr", 0.001)
        adaptive_n_est = config.get("adaptive_retrain_n_estimators", 5000)
        logger.info(
            f"  {symbol}: best_iter={best_iter} <= {adaptive_threshold}, "
            f"触发自适应重训 (lr={adaptive_lr}, n_est={adaptive_n_est})"
        )

        adaptive_params = dict(config["lgb_params"])
        adaptive_params["learning_rate"] = adaptive_lr
        adaptive_params["n_estimators"] = adaptive_n_est

        # v8.7: 自适应重训也使用 GPU→CPU 回退机制
        adaptive_config = dict(config)
        adaptive_config["lgb_params"] = adaptive_params
        adaptive_model, _ = train_lgb_with_fallback(
            x_train,
            y_train,
            x_test,
            y_test,
            adaptive_config,
            log_tag=f"{symbol}-adaptive",
        )

        y_pred_adaptive = adaptive_model.predict(x_test)
        adaptive_r2 = _r2_score(y_test, y_pred_adaptive)
        adaptive_ic = _ic_score(y_test, y_pred_adaptive)
        adaptive_sharpe = _signal_sharpe(y_test, y_pred_adaptive)
        adaptive_best_iter = (
            int(adaptive_model.best_iteration_) if hasattr(adaptive_model, "best_iteration_") else adaptive_n_est
        )

        # 仅当自适应版本更优时替换
        if adaptive_r2 > final_r2:
            logger.info(
                f"  {symbol}: 自适应重训改进 R² {final_r2:.4f} → {adaptive_r2:.4f}, "
                f"best_iter {best_iter} → {adaptive_best_iter}"
            )
            final_model = adaptive_model
            y_pred = y_pred_adaptive
            final_r2 = adaptive_r2
            final_ic = adaptive_ic
            final_sharpe = adaptive_sharpe
            best_iter = adaptive_best_iter
            feat_imp = pd.Series(final_model.feature_importances_, index=selected_features).sort_values(ascending=False)
            latest_pred = float(final_model.predict(latest_features)[0])
            signal = float(np.tanh(latest_pred * 100))
            adaptive_retrained = True
        else:
            logger.info(f"  {symbol}: 自适应重训未改进 (R² {adaptive_r2:.4f} <= {final_r2:.4f}), 保留原模型")

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
        "adaptive_retrained": adaptive_retrained,
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
# V9: Regime-Specific 训练 (bull / non-bull 双模型)
# ============================================================
# 动机: V7-Model (regime-aware 特征) + V7.1/V7.2 (权重后处理) 均无法将
#       Window 1 Sharpe CV 降至 <0.5。根因是单一 LGB 模型被 bear 主导
#       (bear 占 47% 样本), 在 bull regime 下信号失效。
# 方案: 每个标的训练两个独立模型:
#   1. bull_model: 仅用 bull regime 样本训练 (close>MA60 且 MA60 上行)
#   2. non_bull_model: 用 bear/choppy/rebound 样本训练
# 预测时按当前 regime 选择对应模型, 解决模型层 regime 适应性问题。


def compute_regime_series(
    proxy_df: pd.DataFrame,
    ma_period: int = 60,
    slope_window: int = 5,
) -> pd.Series:
    """计算大盘 regime 序列 (bull/bear/choppy/rebound)

    Args:
        proxy_df: 大盘代理 (510300) OHLCV 数据
        ma_period: MA 周期
        slope_window: MA 斜率窗口

    Returns:
        pd.Series[index=proxy_df.index, values="bull"/"bear"/"choppy"/"rebound"/"unknown"]
    """
    if proxy_df is None or len(proxy_df) < ma_period + slope_window:
        return pd.Series("unknown", index=proxy_df.index if proxy_df is not None else [])

    df = proxy_df.copy()
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()

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
    return regime


def _prepare_regime_training_data(
    symbol: str,
    df: pd.DataFrame,
    config: dict[str, Any],
    regime_series: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, int, int, list[str]] | None:
    """准备 regime 训练数据: 构建标签 + 对齐 regime + 分割样本。

    Returns:
        (df, regime_aligned, bull_mask, non_bull_mask, n_bull, n_non_bull, all_feature_cols)
        样本不足时返回 None
    """
    df = df.copy()
    label_horizon = config.get("label_horizon", 5)
    df["target"] = df["close"].pct_change(label_horizon).shift(-label_horizon)
    df = df.dropna(subset=["target"])

    if len(df) < config["min_samples"]:
        logger.info(f"[V9-Regime] {symbol}: 样本不足 ({len(df)} < {config['min_samples']})")
        return None

    regime_aligned = regime_series.reindex(df.index).ffill().fillna("unknown")
    df["_regime"] = regime_aligned

    bull_mask = df["_regime"] == "bull"
    non_bull_mask = df["_regime"].isin(["bear", "choppy", "rebound"])
    n_bull = int(bull_mask.sum())
    n_non_bull = int(non_bull_mask.sum())

    all_feature_cols = [
        c for c in df.columns if c not in ["open", "high", "low", "close", "volume", "target", "_regime"]
    ]

    logger.info(f"[V9-Regime] {symbol}: bull={n_bull}, non_bull={n_non_bull}, total={len(df)}")
    return (
        df,
        regime_aligned,
        bull_mask,
        non_bull_mask,
        n_bull,
        n_non_bull,
        all_feature_cols,
    )


def _train_one_regime_model(
    symbol: str,
    df: pd.DataFrame,
    mask: pd.Series,
    n_samples: int,
    all_feature_cols: list[str],
    config: dict[str, Any],
    regime_label: str,
    min_samples_per_regime: int,
) -> dict[str, Any] | None:
    """训练单个 regime 子集模型 (bull 或 non_bull)。

    Returns:
        训练成功的模型 dict, 失败/样本不足返回 None
    """
    if n_samples < min_samples_per_regime:
        logger.info(
            f"[V9-Regime] {symbol} {regime_label} 样本不足 ({n_samples} < {min_samples_per_regime}), 跳过 {regime_label} 模型"  # noqa: E501
        )
        return None

    sub_df = df[mask].copy()
    try:
        result = _train_regime_subset(
            symbol,
            sub_df,
            all_feature_cols,
            config,
            regime_label=regime_label,
            min_samples_per_regime=min_samples_per_regime,
        )
        # V9 修复: 正确处理 SKIP 状态, 避免 KeyError
        if result.get("status") != "OK":
            logger.info(f"[V9-Regime] {symbol} {regime_label} 模型跳过: {result.get('reason', 'unknown')}")
            return None
        logger.info(
            f"[V9-Regime] {symbol} {regime_label} 模型: best_iter={result.get('best_iteration')}, "
            f"IC={result['final_metrics']['ic']:.4f}, signal={result['signal']:.4f}"
        )
        return result
    except Exception as e:
        logger.warning(f"[V9-Regime] {symbol} {regime_label} 模型训练失败: {e}")
        return None


def _train_full_fallback_model(
    symbol: str,
    df: pd.DataFrame,
    config: dict[str, Any],
    need_full: bool,
) -> dict[str, Any] | None:
    """Fallback: 训练全样本模型 (用于 regime 模型均失败时)。

    Returns:
        训练成功的模型 dict, 不需要或失败时返回 None
    """
    if not need_full:
        return None
    try:
        result = train_symbol_enhanced(symbol, df.drop(columns=["_regime"]), config)
        if result.get("status") != "OK":
            return None
        logger.info(
            f"[V9-Regime] {symbol} full 模型 (fallback): "
            f"IC={result['final_metrics']['ic']:.4f}, signal={result['signal']:.4f}"
        )
        return result
    except Exception as e:
        logger.warning(f"[V9-Regime] {symbol} full 模型训练失败: {e}")
        return None


def _select_active_regime_model(
    regime_aligned: pd.Series,
    bull_result: dict[str, Any] | None,
    non_bull_result: dict[str, Any] | None,
    full_result: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """选择当前 regime 对应的模型 (用最新样本的 regime)。"""
    current_regime = regime_aligned.iloc[-1] if len(regime_aligned) > 0 else "unknown"
    if current_regime == "bull" and bull_result is not None:
        return "bull", bull_result
    if current_regime != "bull" and non_bull_result is not None:
        return "non_bull", non_bull_result
    if full_result is not None:
        return "full", full_result
    if bull_result is not None:
        return "bull", bull_result  # 仅有 bull 模型时强制使用
    return "non_bull", non_bull_result  # type: ignore[return-value]


def train_symbol_regime_specific(
    symbol: str,
    df: pd.DataFrame,
    config: dict[str, Any],
    regime_series: pd.Series,
    min_samples_per_regime: int = 100,
) -> dict[str, Any]:
    """V9: 训练 regime-specific 双模型 (bull / non-bull)

    Args:
        symbol: 标的代码
        df: 含特征 + close 的 DataFrame (与 train_symbol_enhanced 相同)
        config: 训练配置
        regime_series: 大盘 regime 序列 (index=date, values="bull"/"bear"/...)
        min_samples_per_regime: 每个模型最少样本数, 不足则降级为全样本模型

    Returns:
        {
            "status": "OK" | "SKIP" | "FALLBACK_FULL",
            "bull_model": {...} | None,      # bull regime 专用模型
            "non_bull_model": {...} | None,  # non-bull regime 专用模型
            "full_model": {...} | None,      # 全样本模型 (fallback)
            "selected_regime": "bull" | "non_bull" | "full",  # 当前 regime 使用的模型
            "signal": float,                 # 当前 regime 模型的信号
            "raw_prediction": float,
            ...
        }
    """
    prepared = _prepare_regime_training_data(symbol, df, config, regime_series)
    if prepared is None:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": f"样本不足 ({len(df)} < {config['min_samples']})",
        }
    (
        df,
        regime_aligned,
        bull_mask,
        non_bull_mask,
        n_bull,
        n_non_bull,
        all_feature_cols,
    ) = prepared

    # 训练 bull / non_bull 模型
    bull_result = _train_one_regime_model(
        symbol,
        df,
        bull_mask,
        n_bull,
        all_feature_cols,
        config,
        "bull",
        min_samples_per_regime,
    )
    non_bull_result = _train_one_regime_model(
        symbol,
        df,
        non_bull_mask,
        n_non_bull,
        all_feature_cols,
        config,
        "non_bull",
        min_samples_per_regime,
    )

    # 任一 regime 模型缺失 → 训练 full fallback
    full_result = _train_full_fallback_model(
        symbol, df, config, need_full=(bull_result is None or non_bull_result is None)
    )

    # 所有模型都失败
    if bull_result is None and non_bull_result is None and full_result is None:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": "所有 regime 模型训练失败",
        }

    selected, active = _select_active_regime_model(regime_aligned, bull_result, non_bull_result, full_result)

    return {
        "status": "OK",
        "symbol": symbol,
        "current_regime": (regime_aligned.iloc[-1] if len(regime_aligned) > 0 else "unknown"),
        "selected_regime": selected,
        "n_bull_samples": n_bull,
        "n_non_bull_samples": n_non_bull,
        "bull_model": bull_result,
        "non_bull_model": non_bull_result,
        "full_model": full_result,
        "selected_features": active.get("selected_features", []),
        "cv_after_selection": active.get("cv_after_selection", {}),
        "final_metrics": active.get("final_metrics", {}),
        "signal": active.get("signal", 0.0),
        "raw_prediction": active.get("raw_prediction", 0.0),
        "n_features_after": active.get("n_features_after", 0),
        # 保留 model 引用供预测使用 (注意: 仅 selected 的 model)
        "model": active.get("model"),
        # 同时保留两个模型的引用, 供 backtest_runner 切换使用
        "models_by_regime": {
            "bull": bull_result.get("model") if bull_result else None,
            "non_bull": non_bull_result.get("model") if non_bull_result else None,
            "full": full_result.get("model") if full_result else None,
        },
        "features_by_regime": {
            "bull": bull_result.get("selected_features") if bull_result else None,
            "non_bull": (non_bull_result.get("selected_features") if non_bull_result else None),
            "full": full_result.get("selected_features") if full_result else None,
        },
    }


def _train_regime_subset(
    symbol: str,
    df_sub: pd.DataFrame,
    all_feature_cols: list[str],
    config: dict[str, Any],
    regime_label: str,
    min_samples_per_regime: int = 100,
) -> dict[str, Any]:
    """训练单个 regime 子集的 LGB 模型 (复用 train_symbol_enhanced 流程)

    Args:
        symbol: 标的代码
        df_sub: 该 regime 的子集 DataFrame (含 _regime 列)
        all_feature_cols: 特征列名
        config: 训练配置
        regime_label: "bull" 或 "non_bull"
        min_samples_per_regime: regime 子集最少样本数 (低于此值返回 SKIP)
            注意: 此值通常 < config["min_samples"], 因为 regime 子集本身是小样本

    Returns:
        与 train_symbol_enhanced 相同格式的结果
    """
    # 移除 _regime 列, 复用 train_symbol_enhanced
    df_clean = df_sub.drop(columns=["_regime"], errors="ignore").copy()

    # V9 修复: 使用 min_samples_per_regime 而非 config["min_samples"] 作为阈值
    # 原因: config["min_samples"]=150 (全样本阈值), 但 bull regime 子集通常只有 100-180 样本
    #       使用 150 会导致 bull 模型在大多数月份失败, 失去 regime-specific 价值
    if len(df_clean) < min_samples_per_regime:
        return {
            "status": "SKIP",
            "reason": f"{regime_label} 样本不足 ({len(df_clean)} < {min_samples_per_regime})",
        }

    # 临时降低 config["min_samples"] 以适配 regime 子集
    # 原因: train_symbol_enhanced 内部也会检查 config["min_samples"], 若不降低会再次拒绝
    config_adapted = dict(config)
    config_adapted["min_samples"] = min(len(df_clean), min_samples_per_regime)

    # 直接复用 train_symbol_enhanced (它内部会做 CV/特征选择/最终训练)
    result = train_symbol_enhanced(symbol, df_clean, config_adapted)
    if result.get("status") != "OK":
        return result

    # 添加 regime 标签到结果
    result["regime_label"] = regime_label
    result["n_regime_samples"] = len(df_clean)
    return result


# ============================================================
# 完整训练流程编排 (run_enhanced_training)
# ============================================================
def _log_training_header(config: dict[str, Any], use_news: bool, symbols: list[tuple]) -> None:
    """输出训练头日志 + GPU 可用性验证。"""
    logger.info("#" * 70)
    logger.info("# LightGBM 增强训练 (真实OHLCV + 情绪因子 + 放宽早停)")
    logger.info(f"# 标的数: {len(symbols)}")
    logger.info(f"# CV 折数: {config['n_splits']}")
    logger.info(f"# 早停轮次: {config['early_stopping_rounds']} (放宽)")
    logger.info(f"# n_estimators: {config['lgb_params']['n_estimators']}")
    logger.info(f"# learning_rate: {config['lgb_params']['learning_rate']}")
    logger.info(f"# 设备类型: {config['lgb_params'].get('device_type', 'cpu')} (v8.7 GPU 加速)")
    logger.info(f"# 新闻情绪因子: {'启用' if use_news else '禁用'}")
    _verify_gpu_availability(config)
    logger.info("#" * 70)


def _verify_gpu_availability(config: dict[str, Any]) -> None:
    """v8.7: GPU 可用性验证 (失败时回退 CPU 并打印警告)。"""
    try:
        import lightgbm as _lgb

        logger.info(f"# LightGBM 版本: {_lgb.__version__}")
        if config["lgb_params"].get("device_type") != "gpu":
            logger.info("# GPU 未启用 (device_type != gpu)")
            return
        import numpy as _np

        _X = _np.array([[1, 2], [3, 4]], dtype=_np.float32)  # noqa: N806
        _y = _np.array([1.0, 2.0], dtype=_np.float32)
        _d = _lgb.Dataset(_X, label=_y)
        _m = _lgb.train(
            {"objective": "regression", "device_type": "gpu", "verbose": -1},
            _d,
            num_boost_round=1,
        )
        logger.info("# GPU 训练验证: 通过 ✓")
    except Exception as _e:
        logger.warning(f"# GPU 验证失败, 回退 CPU: {_e}")


def _build_all_features(
    symbols: list[tuple],
    config: dict[str, Any],
    use_news: bool,
) -> dict[str, pd.DataFrame] | None:
    """执行 Step 1~3: 拉取 OHLCV + 技术因子 + 扩展特征 + 新闻情绪因子。

    Returns:
        特征字典, 无数据时返回 None
    """
    # 延迟导入 (避免顶层循环依赖)
    from autolearn_trainer import add_cross_sectional_features, add_technical_features

    from .data_loader import fetch_all_real_ohlcv
    from .feature_engineering import (
        add_capital_flow_features,
        add_cross_market_features,
        add_industry_relative_strength_features,
    )

    # Step 1: 拉取真实 OHLCV
    logger.info("Step 1: 拉取真实 OHLCV 数据 (Wind MCP > iFinD MCP > 新浪 HTTP)")
    ohlcv_dict = fetch_all_real_ohlcv(symbols, period="2y")
    logger.info(f"  真实 OHLCV: {len(ohlcv_dict)} / {len(symbols)} 标的")
    if not ohlcv_dict:
        logger.error("  无可用真实数据, 训练终止")
        return None

    # Step 2: 技术因子
    logger.info("Step 2: 技术因子工程 (30+ 因子)")
    featured_dict = {symbol: add_technical_features(df) for symbol, df in ohlcv_dict.items()}
    featured_dict = add_cross_sectional_features(featured_dict)
    logger.info(f"  技术因子完成: {len(featured_dict)} 标的")

    # Step 2.5: 扩展特征工程 v2 (针对 best_iter=1 的标的)
    logger.info("Step 2.5: 扩展特征工程 v2 (行业相对强度 + 资金流向 + 跨市场信号)")
    featured_dict = add_industry_relative_strength_features(featured_dict)
    logger.info("  行业相对强度: +5 因子 (industry_return_5/20, relative_strength_5/20, industry_rank_20)")
    featured_dict = add_capital_flow_features(featured_dict)
    logger.info(
        "  资金流向: +5 因子 (capital_flow, cumulative_flow_5, flow_divergence, smart_money_ratio, flow_momentum)"
    )
    featured_dict = add_cross_market_features(featured_dict)
    logger.info("  跨市场信号: +6 因子 (gold/bank/tech/defensive trend, style_rotation, safe_haven_flow)")

    # Step 3: 新闻情绪因子 (Wind MCP 优先, iFinD 回退)
    featured_dict = _build_sentiment_features(featured_dict, symbols, use_news, config)

    # 统计特征数
    for symbol, df in featured_dict.items():
        n_feat = len([c for c in df.columns if c not in ["open", "high", "low", "close", "volume"]])
        logger.info(f"  {symbol}: {n_feat} 个特征 (技术+情绪)")

    return featured_dict


def _build_sentiment_features(
    featured_dict: dict[str, pd.DataFrame],
    symbols: list[tuple],
    use_news: bool,
    config: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    """Step 3: 构建新闻情绪因子 (Wind MCP 优先, iFinD 回退)。"""
    from .news_sentiment import add_sentiment_features, compute_news_sentiment_factors

    if not use_news:
        logger.info("Step 3: 跳过新闻情绪因子")
        return add_sentiment_features(featured_dict, {})

    logger.info("Step 3: 新闻情绪因子 (Wind MCP 优先, iFinD 回退)")
    sentiment_dict = compute_news_sentiment_factors(symbols, lookback_days=config["news_lookback_days"])
    if sentiment_dict:
        featured_dict = add_sentiment_features(featured_dict, sentiment_dict)
        n_sent = sum(1 for df in featured_dict.values() if "sentiment_score" in df.columns)
        logger.info(f"  情绪因子已合并: {n_sent} 标的")
    else:
        logger.warning("  情绪因子拉取失败, 仅使用技术因子")
        # 仍然加入空的情绪列以保持一致
        featured_dict = add_sentiment_features(featured_dict, {})
    return featured_dict


def _resolve_symbol_config(code: str, config: dict[str, Any]) -> dict[str, Any]:
    """N4: 自适应超参数优化 (基于漂移信号)。

    在训练前根据模型漂移状态动态调整 LGB 配置。
    """
    symbol_config = config
    try:
        try:
            from scripts.adaptive_optimize import adaptive_optimize
        except ImportError:
            from adaptive_optimize import adaptive_optimize  # type: ignore[no-redef]

        opt_result = adaptive_optimize(code, config)
        # 用自适应结果覆盖相关字段 (深拷贝避免污染原 config)
        symbol_config = dict(config)
        symbol_config["lgb_params"] = dict(opt_result.get("lgb_params", config["lgb_params"]))
        if "early_stopping_rounds" in opt_result:
            symbol_config["early_stopping_rounds"] = opt_result["early_stopping_rounds"]
        if "top_n_features" in opt_result:
            symbol_config["top_n_features"] = opt_result["top_n_features"]

        # 日志: 仅在有实际调整时输出
        meta = opt_result.get("_adaptive_meta", {})
        if meta.get("level") and meta["level"] != "none":
            adj = meta.get("adjustments", {})
            adj_str = ", ".join(f"{k}: {v['from']}→{v['to']}" for k, v in adj.items()) if adj else "无参数变更"
            logger.info(
                f"  [ADAPTIVE] {code}: 漂移级别={meta['level']}, "
                f"强度={meta.get('severity', 0):.3f}, 调整: {adj_str}"
            )
    except ImportError:
        logger.debug(f"  [ADAPTIVE] {code}: adaptive_optimize 不可用, 使用默认配置")
    except Exception as e:
        logger.debug(f"  [ADAPTIVE] {code}: 自适应优化失败, 使用默认配置: {e}")
    return symbol_config


def _train_single_symbol(
    code: str,
    name: str,
    style: str,
    featured_dict: dict[str, pd.DataFrame],
    config: dict[str, Any],
    force_retrain: bool,
) -> tuple[str, dict[str, Any]]:
    """训练单个标的。

    Returns:
        (status, result) 元组, status ∈ {"OK","CACHED","SKIP","FAIL"}
    """
    # 延迟导入持久化函数
    from .persistence import load_model_meta, save_model, should_retrain

    if code not in featured_dict:
        logger.warning(f"  [SKIP] {code} ({name}): 无数据")
        return "SKIP", {"status": "SKIP", "symbol": code, "reason": "no_data"}

    if not force_retrain and not should_retrain(code, config):
        meta = load_model_meta(code)
        logger.info(f"  [CACHED] {code} ({name}): 模型未过期, 跳过")
        return "CACHED", {
            "status": "CACHED",
            "symbol": code,
            "name": name,
            "style": style,
            "meta": meta,
        }

    logger.info(f"  [TRAIN] {code} ({name})...")
    try:
        symbol_config = _resolve_symbol_config(code, config)
        result = train_symbol_enhanced(code, featured_dict[code], symbol_config)
        if result["status"] != "OK":
            logger.warning(f"  [SKIP] {code}: {result.get('reason')}")
            return "SKIP", result

        _mark_quality_flag(code, result, config)
        paths = save_model(code, result, config)
        _log_saved_symbol(code, result)
        return "OK", {**result, "paths": paths, "name": name, "style": style}
    except Exception as e:
        logger.error(f"  [FAIL] {code}: {e}", exc_info=True)
        return "FAIL", {"status": "FAIL", "symbol": code, "error": str(e)}


def _mark_quality_flag(code: str, result: dict[str, Any], config: dict[str, Any]) -> None:
    """根据 CV 指标 + 过拟合信号设置 quality_flag。

    P0/P1 修复 (2026-08-02):
    - best_iter <= 1 且 final_ic < 0 → NOISE (纯噪声, 信号强制置零)
    - CV IC 与 Final IC 偏差 > 0.3 → 降级 LOW_QUALITY (疑似过拟合)
    """
    cv_metrics = result["cv_after_selection"]
    final_metrics = result.get("final_metrics", {})
    best_iter = result.get("best_iteration", 999)
    final_ic = final_metrics.get("ic", 0)

    # P0-1: 检测纯噪声模型 (best_iter=1 且预测方向错误)
    if best_iter <= 1 and final_ic < 0:
        logger.warning(
            f"  [NOISE] {code}: best_iter={best_iter}, final_ic={final_ic:.4f}, " f"模型为纯噪声, 信号强制置零"
        )
        result["quality_flag"] = "NOISE"
        return

    quality_ok = (
        cv_metrics["mean_r2"] >= config["model_quality_threshold"]["min_cv_r2"]
        and cv_metrics["mean_ic"] >= config["model_quality_threshold"]["min_cv_ic"]
        and cv_metrics["mean_sharpe"] >= config["model_quality_threshold"]["min_cv_sharpe"]
    )

    # P1-2: CV IC 与 Final IC 一致性检查 (过拟合检测)
    cv_ic = cv_metrics.get("mean_ic", 0)
    ic_divergence = abs(cv_ic - final_ic)
    if quality_ok and ic_divergence > 0.3:
        logger.warning(
            f"  [IC_DIVERGENCE] {code}: CV_IC={cv_ic:.4f}, Final_IC={final_ic:.4f}, "
            f"偏差={ic_divergence:.2f} > 0.3, 疑似过拟合, 降级为 LOW_QUALITY"
        )
        quality_ok = False

    if not quality_ok:
        logger.warning(
            f"  [LOW_QUALITY] {code}: "
            f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
            f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}"
        )
        result["quality_flag"] = "LOW_QUALITY"
    else:
        result["quality_flag"] = "OK"


def _log_saved_symbol(code: str, result: dict[str, Any]) -> None:
    """输出训练成功日志。"""
    cv_metrics = result["cv_after_selection"]
    logger.info(
        f"  [SAVED] {code}: "
        f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
        f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}, "
        f"Sharpe={cv_metrics['mean_sharpe']}±{cv_metrics['std_sharpe']}, "
        f"final_r2={result['final_metrics']['r2']}, "
        f"final_ic={result['final_metrics']['ic']}, "
        f"signal={result['signal']}, "
        f"best_iter={result['best_iteration']}, "
        f"features={result['n_features_before']}→{result['n_features_after']}"
    )


def _generate_integrated_signals(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Step 5: 生成集成信号文件。

    P0 修复 (2026-08-02): LOW_QUALITY / NOISE 信号自动置零, 防止垃圾信号污染交易决策。
    raw_signal 字段保留原始信号供调试审查。
    """
    logger.info("Step 5: 生成集成信号")
    signals: dict[str, Any] = {}
    suppressed: list[str] = []
    for code, res in results.items():
        if res.get("status") in ("OK", "CACHED"):
            quality = res.get("quality_flag", "CACHED")
            raw_signal = res.get("signal", res.get("meta", {}).get("signal", 0))
            # P0 修复: LOW_QUALITY / NOISE 信号强制置零
            if quality in ("LOW_QUALITY", "NOISE"):
                signal = 0.0
                suppressed.append(f"{code}({quality})")
            else:
                signal = raw_signal
            signals[code] = {
                "signal": signal,
                "raw_signal": raw_signal,
                "name": res.get("name", ""),
                "style": res.get("style", ""),
                "quality_flag": quality,
                "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
            }
    if suppressed:
        logger.warning(f"  [SIGNAL_SUPPRESS] {len(suppressed)} 个低质量信号已置零: {', '.join(suppressed)}")

    signals_path = MODELS_DIR / "lgb_enhanced_signals.json"
    signals_data: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
        "data_source": "real_ohlcv",
        "features": "technical_37 + extended_16 + sentiment_6",
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
    return signals_data


def run_enhanced_training(
    symbols: list[tuple] | None = None,
    force_retrain: bool = False,
    config: dict[str, Any] | None = None,
    use_news: bool = True,
    post_train_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """执行增强训练

    Args:
        symbols: 标的清单 [(code, suffix, server_type, name, style), ...]
        force_retrain: 强制重训
        config: 训练配置 (None 时使用 LGB_ENHANCED_CONFIG)
        use_news: 是否启用新闻情绪因子
        post_train_callback: 训练后回调钩子 (ER-1.1)，接收训练结果字典；
            None 时训练行为不变（向后兼容）；回调异常 fail-safe 降级不阻断训练

    Returns:
        训练结果汇总 {"status", "total", "trained", "skipped", "failed", "results", "signals"}
    """
    from autolearn_trainer import POSITION_SYMBOLS, invoke_post_train_callback

    if config is None:
        # 延迟导入配置 (避免主模块未注入时使用空默认值)
        try:
            from lgb_enhanced_trainer import LGB_ENHANCED_CONFIG  # type: ignore[import]
        except ImportError:
            # 兜底默认配置 (仅在作为独立包使用时)
            config = {
                "n_splits": 5,
                "test_ratio": 0.2,
                "min_samples": 150,
                "label_horizon": 5,
                "top_n_features": 30,
                "feature_selection_threshold": 1,
                "early_stopping_rounds": 200,
                "retrain_interval_days": 7,
                "news_lookback_days": 250,  # v4.1: 30→250天
                "adaptive_retrain_threshold": 5,
                "adaptive_retrain_lr": 0.001,
                "adaptive_retrain_n_estimators": 5000,
                "lgb_params": {
                    "n_estimators": 2000,
                    "learning_rate": 0.005,
                    "device_type": "cpu",
                    "verbose": -1,
                    "n_jobs": -1,
                    "random_state": 42,
                },
                "model_quality_threshold": {
                    "min_cv_r2": -0.3,
                    "min_cv_ic": 0.0,
                    "min_cv_sharpe": 0.0,
                },
            }
        else:
            config = LGB_ENHANCED_CONFIG

    if symbols is None:
        symbols = POSITION_SYMBOLS

    _log_training_header(config, use_news, symbols)

    # Step 1~3: 特征工程
    featured_dict = _build_all_features(symbols, config, use_news)
    if featured_dict is None:
        return {"status": "FAIL", "error": "no_real_ohlcv"}

    # Step 4: 训练
    logger.info("Step 4: 训练 LightGBM + TSCV (放宽早停)")
    results: dict[str, dict[str, Any]] = {}
    saved = 0
    skipped = 0
    failed = 0

    for code, _suffix, _, name, style in symbols:
        status, result = _train_single_symbol(code, name, style, featured_dict, config, force_retrain)
        results[code] = result
        if status == "OK":
            saved += 1
        elif status == "FAIL":
            failed += 1
        elif status == "SKIP":
            skipped += 1
        # CACHED 不计入 saved/skipped/failed

    # Step 5: 集成信号
    signals_data = _generate_integrated_signals(results)

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
    invoke_post_train_callback(post_train_callback, result, logger_name="lgb_enhanced")

    return result
