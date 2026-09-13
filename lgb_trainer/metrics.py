"""评估指标 + 时间序列交叉验证 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - r2_score: R² 决定系数
  - ic_score: 信息系数 (Pearson 相关)
  - signal_sharpe: 信号夏普比率 (sign-based)
  - time_series_cv_evaluate: Purged K-Fold 时间序列交叉验证
  - select_features_by_importance: 基于重要性的特征选择 (含受保护特征机制)

依赖说明:
  time_series_cv_evaluate 需要训练函数 `_train_lgb_with_fallback` 作为参数传入,
  避免与 trainer.py 形成循环导入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 评估指标
# ============================================================
def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R² 决定系数。

    Args:
        y_true: 真实值
        y_pred: 预测值

    Returns:
        R² 分数 (1.0 为完美, 0.0 为均值预测, 负数为劣于均值)
    """
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2) + 1e-9
    return float(1 - ss_res / ss_tot)


def ic_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """信息系数 (Pearson 相关)。

    Args:
        y_true: 真实值
        y_pred: 预测值

    Returns:
        IC 相关系数, 样本不足或方差为 0 时返回 0.0
    """
    if len(y_true) < 5:
        return 0.0
    if np.std(y_pred) < 1e-9:
        return 0.0
    return float(np.corrcoef(y_true, y_pred)[0, 1])


def signal_sharpe(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """信号夏普比率 (基于 sign 的策略夏普)。

    策略: 按预测信号方向持仓, 计算日化夏普 * sqrt(252) 年化。

    Args:
        y_true: 真实收益
        y_pred: 预测信号

    Returns:
        年化夏普比率, 方差为 0 时返回 0.0
    """
    signal = np.sign(y_pred)
    returns = signal * y_true
    if returns.std() < 1e-9:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


# ============================================================
# 时间序列交叉验证
# ============================================================
def time_series_cv_evaluate(
    X: np.ndarray,  # noqa: N803
    y: np.ndarray,
    config: dict,
    n_splits: int = 5,
    code: str = "",
    train_fn: Callable | None = None,
) -> dict[str, Any]:
    """时间序列交叉验证评估 (Purged K-Fold, 防标签泄漏)。

    P3-1 FIX (2026-07-29): 用 Purged K-Fold 替代裸 TimeSeriesSplit
    标签为未来 label_horizon 日收益率, 标准 TSS 在分割点存在标签重叠泄漏
    (训练集尾部样本的标签与测试集头部样本共享未来信息, 导致 CV 指标虚高)。
    purged_timeseries_split 在训练集尾部剔除 embargo 大小的样本, 消除泄漏。

    Args:
        X: 特征矩阵
        y: 标签向量
        config: 训练配置 (含 label_horizon, lgb_params, early_stopping_rounds)
        n_splits: 折数
        code: 标的代码 (日志用)
        train_fn: 训练函数 (X_train, y_train, X_eval, y_eval, config, log_tag)
                   若为 None, 则从 trainer 模块动态导入 (避免循环依赖)

    Returns:
        {
            "fold_metrics": [{fold, train_size, test_size, r2, ic, sharpe, best_iteration}],
            "mean_r2": float, "std_r2": float,
            "mean_ic": float, "std_ic": float,
            "mean_sharpe": float, "std_sharpe": float,
            "feature_importances": np.ndarray,
        }
    """
    # 延迟导入训练函数, 避免 metrics ↔ trainer 循环
    if train_fn is None:
        from .trainer import train_lgb_with_fallback as train_fn

    n_samples = int(X.shape[0])
    label_horizon = int(config.get("label_horizon", 5))
    # embargo 大小 ≈ 标签 horizon (行号即时序顺序), 至少 1 个样本
    # 默认 embargo_pct=0.01 对 ~500 样本仅约 5, 与 horizon 对齐; 显式计算更稳健
    embargo_pct = max(0.005, label_horizon / max(n_samples, 1))
    from utils.purged_kfold import purged_timeseries_split

    folds = list(
        purged_timeseries_split(
            n_samples=n_samples, n_splits=n_splits, embargo_pct=embargo_pct
        )
    )

    fold_metrics: list[dict[str, Any]] = []
    all_importances: list[np.ndarray] = []
    n_features = X.shape[1]
    # P0-M2 (2026-09-13): OOF 策略日收益代理序列 — DSR 准入用。
    # 口径: sign(预测) × 已实现前向收益 / horizon (方向持仓、把 h 日收益摊到
    # 日频, 近似消除前向窗口重叠对 IID 假设的破坏)。空仓 (预测≈0) 记 0 收益。
    dsr_returns: list[float] = []

    for fold_idx, (train_idx, test_idx) in enumerate(folds):
        X_train_fold, X_test_fold = X[train_idx], X[test_idx]  # noqa: N806
        y_train_fold, y_test_fold = y[train_idx], y[test_idx]

        if len(X_train_fold) < 50 or len(X_test_fold) < 10:
            continue

        # P0 修复 (2026-09-13): 早停验证集从折内训练段尾部切出 (带 purge 间隔),
        # 不再复用折外测试集 — 此前每折用 X_test_fold 早停又在同一 X_test_fold
        # 上评估, fold 指标 (mean_ic/sharpe) 系统性乐观, 且经由早停选择污染
        # feature_importances (Step 2 特征选择被间接泄漏)。
        from .trainer import carve_validation_split

        X_tr_sub, y_tr_sub, X_val, y_val = carve_validation_split(  # noqa: N806 — ML 惯例 X/y 大写
            X_train_fold, y_train_fold, label_horizon=label_horizon
        )
        model, _device_used = train_fn(
            X_tr_sub,
            y_tr_sub,
            X_val,
            y_val,
            config,
            log_tag=f"{code}-fold{fold_idx + 1}",
        )

        y_pred = model.predict(X_test_fold)
        r2 = r2_score(y_test_fold, y_pred)
        ic = ic_score(y_test_fold, y_pred)
        sharpe = signal_sharpe(y_test_fold, y_pred)

        # P0-M2: 累积 OOF 策略日收益代理 (方向 × 已实现前向收益 / horizon)
        horizon = max(int(label_horizon), 1)
        dsr_returns.extend(
            (float(np.sign(p)) * float(t)) / horizon
            for p, t in zip(
                np.asarray(y_pred, dtype=float), y_test_fold, strict=False
            )
            if np.isfinite(t)
        )

        fold_metrics.append(
            {
                "fold": fold_idx + 1,
                "train_size": len(train_idx),
                "test_size": len(test_idx),
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
            "dsr_pass": False,
            "dsr": 0.0,
        }

    r2s = [f["r2"] for f in fold_metrics]
    ics = [f["ic"] for f in fold_metrics]
    sharps = [f["sharpe"] for f in fold_metrics]

    # P0-M2: Deflated Sharpe Ratio 准入 — 多重检验修正 (n_trials 用候选特征数
    # 做代理: 每个特征都是一次"尝试"; 数据窥探下高 Sharpe 极易是运气)。
    dsr_pass = False
    dsr_value = 0.0
    dsr_verdict = "not_evaluated"
    try:
        from utils.backtest.deflated_sharpe import deflated_sharpe_ratio

        required = float(
            config.get("model_quality_threshold", {}).get("required_dsr", 0.80)
        )
        n_trials = max(1, int(config.get("model_quality_threshold", {}).get("dsr_n_trials", n_features)))
        dsr_result = deflated_sharpe_ratio(
            np.asarray(dsr_returns, dtype=float), n_trials=n_trials, required_dsr=required
        )
        dsr_pass = bool(dsr_result.is_pass)
        dsr_value = round(float(dsr_result.deflated_sharpe_ratio), 4)
        dsr_verdict = dsr_result.verdict
    except Exception as e:  # noqa: BLE001 — DSR 计算失败不阻断 CV, 准入侧降级为不通过并留痕
        logger.warning("DSR 计算失败 (按不通过处理): %s", e)
        dsr_verdict = f"error: {e}"

    return {
        "fold_metrics": fold_metrics,
        "mean_r2": round(float(np.mean(r2s)), 4),
        "std_r2": round(float(np.std(r2s)), 4),
        "mean_ic": round(float(np.mean(ics)), 4),
        "std_ic": round(float(np.std(ics)), 4),
        "mean_sharpe": round(float(np.mean(sharps)), 4),
        "std_sharpe": round(float(np.std(sharps)), 4),
        "feature_importances": np.mean(all_importances, axis=0),
        "dsr_pass": dsr_pass,
        "dsr": dsr_value,
        "dsr_verdict": dsr_verdict,
    }


# ============================================================
# 特征选择
# ============================================================
def select_features_by_importance(
    feature_cols: list[str],
    importances: np.ndarray,
    threshold: float = 3.0,
    top_n: int = 20,
    protected_patterns: list[str] | None = None,
    min_protected: int = 1,
) -> list[str]:
    """根据 CV 平均特征重要性筛选特征。

    Args:
        feature_cols: 所有特征名
        importances: 特征重要性数组
        threshold: 重要性阈值
        top_n: 保留 Top N
        protected_patterns: 受保护特征前缀列表 (如 ["sent_"], 强制保留)
        min_protected: 每个受保护前缀至少保留的最少特征数

    Returns:
        选中特征列表
    """
    importance_series = pd.Series(importances, index=feature_cols)
    selected = importance_series[importance_series >= threshold]
    if len(selected) == 0:
        selected = importance_series.sort_values(ascending=False).head(top_n)
    else:
        selected = selected.sort_values(ascending=False).head(top_n)
    selected_features = list(selected.index)

    # 受保护特征机制: 确保至少 min_protected 个受保护特征入选
    if protected_patterns:
        for pattern in protected_patterns:
            protected_in = [f for f in selected_features if f.startswith(pattern)]
            if len(protected_in) < min_protected:
                # 从未入选的特征中, 找重要性最高的受保护特征
                protected_all = importance_series[
                    importance_series.index.str.startswith(pattern)
                ].sort_values(ascending=False)
                for feat in protected_all.index:
                    if feat not in selected_features:
                        selected_features.append(feat)
                        if (
                            len([f for f in selected_features if f.startswith(pattern)])
                            >= min_protected
                        ):
                            break
    return selected_features
