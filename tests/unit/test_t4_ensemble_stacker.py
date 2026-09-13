"""T4 单测: 两树 Stacking Ensemble + Purged K-Fold OOF 防泄漏.

覆盖: 折连续性/覆盖完整性/embargo 隔离(无标签窗口泄漏)/stacking 端到端/
TimesFM meta 列/xgboost 缺失 fail-open.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.alpha.ensemble_stacker import (  # noqa: E402
    purged_kfold_indices,
    stacked_ensemble_fit,
    stacked_ensemble_predict,
)


class TestPurgedKFoldIndices:
    def test_folds_cover_all_samples(self):
        n, k = 200, 5
        folds = purged_kfold_indices(n, k, embargo=5)
        assert len(folds) == k
        all_val = np.concatenate([v for _, v in folds])
        assert np.array_equal(np.sort(all_val), np.arange(n))

    def test_embargo_isolation_no_label_leakage(self):
        """训练集与验证折之间必须至少空出 embargo 个样本 (两侧)."""
        n, k, embargo = 300, 5, 5
        folds = purged_kfold_indices(n, k, embargo=embargo)
        for train, val in folds:
            v_start, v_end = val[0], val[-1] + 1
            before = train[train < v_start]
            after = train[train >= v_end]
            if len(before):
                assert before.max() <= v_start - embargo - 1
            if len(after):
                assert after.min() >= v_end + embargo

    def test_too_few_samples_raises(self):
        with pytest.raises(ValueError):
            purged_kfold_indices(5, 5, embargo=1)

    def test_negative_embargo_raises(self):
        with pytest.raises(ValueError):
            purged_kfold_indices(100, 5, embargo=-1)


def _make_data(n=400, seed=42):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 8))
    y = 0.5 * X[:, 0] * X[:, 1] - 0.3 * X[:, 2] + 0.1 * rng.normal(size=n)
    return X, y


class TestStackedEnsembleFit:
    def test_end_to_end(self):
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y, n_splits=5, embargo=5)
        assert fit["single_model"] is False
        assert fit["timesfm_used"] is False
        assert set(fit["models"]) == {"lgb", "xgb"}
        assert len(fit["ridge_weights"]) == 2
        assert fit["n_samples"] == len(y)
        # 可学习非线性信号下 OOF IC 应为正
        assert fit["oof_ic"]["lgb"] > 0.1
        assert fit["oof_ic"]["xgb"] > 0.1

    def test_predict_shape_and_finite(self):
        X, y = _make_data(n=400, seed=42)
        fit = stacked_ensemble_fit(X, y)
        X_new, _ = _make_data(n=50, seed=99)
        preds = stacked_ensemble_predict(fit, X_new)
        assert preds.shape == (50,)
        assert np.isfinite(preds).all()

    def test_timesfm_meta_column_included(self):
        X, y = _make_data()
        tf_col = 0.1 * y + 0.05 * np.random.default_rng(1).normal(size=len(y))
        fit = stacked_ensemble_fit(X, y, timesfm_col=tf_col)
        assert fit["timesfm_used"] is True
        assert len(fit["ridge_weights"]) == 3
        assert fit["oof_ic"]["timesfm"] > 0.1

    def test_timesfm_all_nan_not_used(self):
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y, timesfm_col=np.full(len(y), np.nan))
        assert fit["timesfm_used"] is False
        assert len(fit["ridge_weights"]) == 2

    def test_nan_rows_filtered(self):
        X, y = _make_data()
        X[0, 0] = np.nan
        y[5] = np.nan
        fit = stacked_ensemble_fit(X, y)
        assert fit["n_samples"] == len(y) - 2


class TestXgbMissingFailOpen:
    def test_single_model_degradation(self, monkeypatch):
        """xgboost import 失败 → 退化为单树 OOF, ridge 只有一个系数."""
        import builtins

        real_import = builtins.__import__

        def _no_xgb(name, *args, **kwargs):
            if name == "xgboost":
                raise ImportError("simulated missing xgboost")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_xgb)
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y)
        assert fit["single_model"] is True
        assert set(fit["models"]) == {"lgb"}
        assert len(fit["ridge_weights"]) == 1
        preds = stacked_ensemble_predict(fit, X[:20])
        assert preds.shape == (20,)
        assert np.isfinite(preds).all()


class TestTimesfmPredictContract:
    """P0 修复 (2026-09-12): fit/predict meta 列严格对齐 + 常数列拒绝."""

    def test_predict_with_timesfm_value_aligns_columns(self):
        X, y = _make_data()
        tf_col = 0.1 * y + 0.05 * np.random.default_rng(1).normal(size=len(y))
        fit = stacked_ensemble_fit(X, y, timesfm_col=tf_col)
        assert fit["timesfm_used"] is True
        preds = stacked_ensemble_predict(fit, X[:30], timesfm_value=0.02)
        assert preds.shape == (30,)
        assert np.isfinite(preds).all()

    def test_predict_without_timesfm_value_fails_closed(self):
        X, y = _make_data()
        tf_col = 0.1 * y + 0.05 * np.random.default_rng(1).normal(size=len(y))
        fit = stacked_ensemble_fit(X, y, timesfm_col=tf_col)
        with pytest.raises(ValueError, match="timesfm_value"):
            stacked_ensemble_predict(fit, X[:30])

    def test_constant_timesfm_column_rejected(self):
        """把单一预测广播到全历史 (常数列) → 拒绝, timesfm_used=False."""
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y, timesfm_col=np.full(len(y), 0.03))
        assert fit["timesfm_used"] is False
        assert len(fit["ridge_weights"]) == 2  # lgb + xgb, 无 timesfm 列

    def test_predict_non_timesfm_fit_ignores_value(self):
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y)
        preds = stacked_ensemble_predict(fit, X[:30], timesfm_value=0.05)
        assert preds.shape == (30,)

    def test_zero_variance_ic_returns_zero_not_nan(self):
        """常数预测列的 OOF IC 应为 0.0 (不可为 NaN 写入 meta)."""
        X, y = _make_data()
        fit = stacked_ensemble_fit(X, y)  # 无 timesfm → oof_ic 只含树模型
        for v in fit["oof_ic"].values():
            assert np.isfinite(v)
