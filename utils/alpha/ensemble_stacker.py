"""
两树 Stacking Ensemble (LightGBM + XGBoost → Ridge 二层)
=========================================================

T4 (2026-09-07): 六项升级之三 — 两树 ensemble + TimesFM stacking 二层.

设计要点:
    1. Purged K-Fold OOF: 时间序列按序切 K 折, 训练折与验证折之间留
       embargo 间隙 (默认 5 = 标签前瞻窗口), 根除 "训练集末尾样本与验证集
       开头样本标签窗口重叠" 导致的 OOF 泄漏 (回测铁律: Purged K-Fold);
    2. L1 = LightGBM + XGBoost (两树, 异构分裂准则互补);
    3. L2 = Ridge (线性元学习器, 凸且稳定, 不易过拟合 OOF);
    4. TimesFM meta 特征: 调用方可将 TimesFM 预测作为额外列传入
       (timesfm_col), 库内不加载 TimesFM 模型 (解耦, 重依赖由调用方管理);
    5. fail-open: xgboost 缺失时退化为单树 OOF, meta 标注 single_model;
    6. 最终模型: OOF 确定二层权重后, L1 在全量数据重训 (标准 stacking).

用法:
    from utils.alpha.ensemble_stacker import stacked_ensemble_fit, stacked_ensemble_predict

    fit = stacked_ensemble_fit(X, y, n_splits=5, embargo=5)
    preds = stacked_ensemble_predict(fit, X_latest)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

# ruff: noqa: N803, N806 — ML 惯例: 特征矩阵命名 X / 标签 y (大写为业界标准)

logger = logging.getLogger(__name__)

DEFAULT_N_SPLITS = 5
DEFAULT_EMBARGO = 5  # 与 5 日前瞻标签窗口对齐


def purged_kfold_indices(
    n_samples: int, n_splits: int = DEFAULT_N_SPLITS, embargo: int = DEFAULT_EMBARGO
) -> list[tuple[np.ndarray, np.ndarray]]:
    """时间序 Purged K-Fold 切分.

    验证折为连续块 (保持时序), 训练集剔除与验证折相邻的 embargo 个样本
    (两侧), 防止标签窗口跨折泄漏.

    Returns:
        [(train_idx, val_idx), ...] 长度 n_splits, val 按时间顺序.
    """
    if n_samples < n_splits * 2:
        raise ValueError(f"n_samples={n_samples} 不足以切 {n_splits} 折")
    if embargo < 0:
        raise ValueError(f"embargo 必须 >= 0, 实际 {embargo}")

    fold_sizes = np.full(n_splits, n_samples // n_splits, dtype=int)
    fold_sizes[: n_samples % n_splits] += 1
    indices = []
    start = 0
    for size in fold_sizes:
        val_start, val_end = start, start + size
        # 训练集 = 验证折前段 (截去 embargo) + 验证折后段 (截去 embargo)
        train_before_end = max(val_start - embargo, 0)
        train_after_start = min(val_end + embargo, n_samples)
        train_idx = np.concatenate([np.arange(0, train_before_end), np.arange(train_after_start, n_samples)])
        val_idx = np.arange(val_start, val_end)
        indices.append((train_idx, val_idx))
        start = val_end
    return indices


def _oof_predict(model_factory, X, y, indices) -> tuple[np.ndarray, list[Any]]:
    """逐折训练并在验证折上产生 OOF 预测, 返回 (oof, fold_models)."""
    oof = np.full(len(y), np.nan)
    fold_models = []
    for train_idx, val_idx in indices:
        model = model_factory()
        model.fit(X[train_idx], y[train_idx])
        oof[val_idx] = model.predict(X[val_idx])
        fold_models.append(model)
    return oof, fold_models


def stacked_ensemble_fit(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = DEFAULT_N_SPLITS,
    embargo: int = DEFAULT_EMBARGO,
    timesfm_col: np.ndarray | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """两树 stacking 拟合.

    Args:
        X: 特征矩阵 (T × F).
        y: 前瞻收益标签 (T,). 调用方保证 y = 未来 N 日收益, embargo >= N.
        timesfm_col: TimesFM 预测列 (T,), 可选 — 作为 L2 额外特征.
        seed: LGB/XGB 随机种子 (可复现).

    Returns:
        fit dict: {models: {lgb, xgb?}, ridge, oof_ic: {...}, ridge_weights,
        n_samples, n_splits, embargo, single_model: bool, timesfm_used: bool}
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X, y = X[mask], y[mask]
    n = len(y)
    indices = purged_kfold_indices(n, n_splits=n_splits, embargo=embargo)

    import lightgbm as lgb

    try:
        import xgboost as xgb

        has_xgb = True
    except ImportError:
        has_xgb = False
        logger.warning("[EnsembleStacker] xgboost 不可用, 退化为单树 OOF")

    def _lgb_factory():
        return lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=30,
            random_state=seed,
            verbose=-1,
            n_jobs=1,
        )

    oof_lgb, _ = _oof_predict(_lgb_factory, X, y, indices)

    oof_xgb = None
    if has_xgb:

        def _xgb_factory():
            return xgb.XGBRegressor(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=seed,
                n_jobs=1,
                verbosity=0,
            )

        oof_xgb, _ = _oof_predict(_xgb_factory, X, y, indices)

    # === L2: Ridge 元学习器 ===
    from sklearn.linear_model import Ridge

    meta_cols = [oof_lgb]
    if oof_xgb is not None:
        meta_cols.append(oof_xgb)
    if timesfm_col is not None:
        tf = np.asarray(timesfm_col, dtype=float)[mask]
        if len(tf) == n and np.isfinite(tf).any():
            meta_cols.append(np.nan_to_num(tf, nan=0.0))

    meta_X = np.column_stack(meta_cols)
    ridge = Ridge(alpha=1.0).fit(meta_X, y)
    ridge_weights = ridge.coef_.tolist()

    # OOF IC (RankIC 的简化代理: Pearson, 逐折合并)
    def _ic(pred: np.ndarray) -> float:
        ok = np.isfinite(pred) & np.isfinite(y)
        if ok.sum() < 10:
            return 0.0
        return float(np.corrcoef(pred[ok], y[ok])[0, 1])

    oof_ic = {"lgb": _ic(oof_lgb)}
    if oof_xgb is not None:
        oof_ic["xgb"] = _ic(oof_xgb)
    if timesfm_col is not None and len(meta_cols) > (2 if oof_xgb is not None else 1):
        oof_ic["timesfm"] = _ic(meta_cols[-1])

    # === 全量重训 L1 (OOF 已确定二层权重) ===
    final_lgb = _lgb_factory().fit(X, y)
    final_models: dict[str, Any] = {"lgb": final_lgb}
    if has_xgb:
        final_models["xgb"] = _xgb_factory().fit(X, y)

    fit_result = {
        "models": final_models,
        "ridge": ridge,
        "ridge_weights": ridge_weights,
        "oof_ic": oof_ic,
        "n_samples": n,
        "n_splits": n_splits,
        "embargo": embargo,
        "single_model": not has_xgb,
        "timesfm_used": timesfm_col is not None and len(meta_cols) > (2 if oof_xgb is not None else 1),
        "feature_mask": mask,
        "n_features": X.shape[1],
    }
    logger.info(
        "[EnsembleStacker] 拟合完成 | n=%d f=%d xgb=%s timesfm=%s ridge_w=%s oof_ic=%s",
        n,
        X.shape[1],
        has_xgb,
        fit_result["timesfm_used"],
        [round(w, 4) for w in ridge_weights],
        {k: round(v, 4) for k, v in oof_ic.items()},
    )
    return fit_result


def stacked_ensemble_predict(fit: dict[str, Any], X: np.ndarray) -> np.ndarray:
    """用拟合结果预测. 单模型时退化为该模型直接输出."""
    X = np.asarray(X, dtype=float)
    models = fit["models"]
    preds = [np.asarray(models["lgb"].predict(X), dtype=float)]
    if "xgb" in models:
        preds.append(np.asarray(models["xgb"].predict(X), dtype=float))
    meta_X = np.column_stack(preds)
    return np.asarray(fit["ridge"].predict(meta_X), dtype=float)
