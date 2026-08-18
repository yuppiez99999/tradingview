"""
单元测试: utils/purged_kfold.py
覆盖 purged_timeseries_split / purged_kfold_generator / validate_embargo / overfitting_diagnosis
"""
from __future__ import annotations

import numpy as np
import pytest

from utils.purged_kfold import (
    overfitting_diagnosis,
    purged_kfold_generator,
    purged_timeseries_split,
    validate_embargo,
)


class TestPurgedTimeseriesSplit:
    def test_basic_split(self):
        folds = list(purged_timeseries_split(100, n_splits=5))
        assert len(folds) > 0
        for train_idx, test_idx in folds:
            assert len(train_idx) > 0
            assert len(test_idx) > 0

    def test_train_before_test(self):
        folds = list(purged_timeseries_split(100, n_splits=5))
        for train_idx, test_idx in folds:
            assert train_idx.max() < test_idx.min()

    def test_no_overlap(self):
        folds = list(purged_timeseries_split(200, n_splits=5))
        for train_idx, test_idx in folds:
            assert len(set(train_idx) & set(test_idx)) == 0

    def test_invalid_n_splits(self):
        with pytest.raises(ValueError):
            list(purged_timeseries_split(100, n_splits=1))

    def test_small_samples_fallback(self):
        folds = list(purged_timeseries_split(20, n_splits=5))
        assert len(folds) == 1
        train_idx, test_idx = folds[0]
        assert len(train_idx) > 0
        assert len(test_idx) > 0

    def test_embargo_creates_gap(self):
        folds = list(purged_timeseries_split(200, n_splits=5, embargo_pct=0.05))
        for train_idx, test_idx in folds:
            gap = test_idx.min() - train_idx.max()
            assert gap > 0

    def test_custom_params(self):
        folds = list(purged_timeseries_split(300, n_splits=3, embargo_pct=0.02, min_train_pct=0.4))
        assert len(folds) > 0

    def test_indices_are_numpy_arrays(self):
        folds = list(purged_timeseries_split(100, n_splits=5))
        for train_idx, test_idx in folds:
            assert isinstance(train_idx, np.ndarray)
            assert isinstance(test_idx, np.ndarray)


class TestPurgedKFoldGenerator:
    def test_returns_list(self):
        folds = purged_kfold_generator(100, n_splits=5)
        assert isinstance(folds, list)

    def test_no_overlap(self):
        folds = purged_kfold_generator(200, n_splits=5)
        for train_idx, test_idx in folds:
            assert len(set(train_idx) & set(test_idx)) == 0

    def test_train_before_test(self):
        folds = purged_kfold_generator(200, n_splits=5)
        for train_idx, test_idx in folds:
            assert train_idx.max() < test_idx.min()

    def test_custom_purge_pct(self):
        folds = purged_kfold_generator(200, n_splits=5, purge_pct=0.02)
        assert len(folds) > 0

    def test_small_test_size_reduces_splits(self):
        folds = purged_kfold_generator(50, n_splits=10)
        assert len(folds) > 0


class TestValidateEmbargo:
    def test_valid_gap(self):
        train = np.arange(0, 50)
        test = np.arange(55, 100)
        ok, msg = validate_embargo(train, test, min_gap=1)
        assert ok is True
        assert "安全" in msg

    def test_insufficient_gap(self):
        train = np.arange(0, 50)
        test = np.arange(50, 100)
        ok, msg = validate_embargo(train, test, min_gap=5)
        assert ok is False
        assert "间隔不足" in msg

    def test_empty_train(self):
        ok, msg = validate_embargo(np.array([]), np.arange(10))
        assert ok is False

    def test_empty_test(self):
        ok, msg = validate_embargo(np.arange(10), np.array([]))
        assert ok is False

    def test_exact_min_gap(self):
        train = np.arange(0, 50)
        test = np.arange(52, 100)
        ok, msg = validate_embargo(train, test, min_gap=2)
        assert ok is True


class TestOverfittingDiagnosis:
    def test_insufficient_data(self):
        result = overfitting_diagnosis([{"r2": 0.5}])
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_stable_metrics_pass(self):
        folds = [{"r2": 0.5, "ic": 0.3, "sharpe": 1.5} for _ in range(5)]
        result = overfitting_diagnosis(folds)
        assert result["overall_pass"] is True

    def test_signal_decay_detected(self):
        folds = [
            {"r2": 0.8},
            {"r2": 0.7},
            {"r2": 0.3},
            {"r2": 0.2},
        ]
        result = overfitting_diagnosis(folds, metric_keys=("r2",))
        assert not result["metrics"]["r2"]["pass"]

    def test_high_cv_detected(self):
        folds = [{"r2": 0.9}, {"r2": 0.1}, {"r2": 0.8}, {"r2": 0.05}]
        result = overfitting_diagnosis(folds, metric_keys=("r2",))
        assert len(result["metrics"]["r2"]["issues"]) > 0

    def test_spread_ratio_detected(self):
        folds = [{"r2": 1.0}, {"r2": 0.01}, {"r2": 0.9}, {"r2": 0.02}]
        result = overfitting_diagnosis(folds, metric_keys=("r2",))
        issues = result["metrics"]["r2"]["issues"]
        assert any("极值偏离" in i for i in issues)

    def test_multiple_metric_keys(self):
        folds = [
            {"r2": 0.5, "ic": 0.3, "sharpe": 1.0},
            {"r2": 0.4, "ic": 0.25, "sharpe": 0.9},
        ]
        result = overfitting_diagnosis(folds, metric_keys=("r2", "ic", "sharpe"))
        assert "r2" in result["metrics"]
        assert "ic" in result["metrics"]
        assert "sharpe" in result["metrics"]

    def test_total_issues_count(self):
        folds = [{"r2": 0.9}, {"r2": 0.1}, {"r2": 0.8}, {"r2": 0.05}]
        result = overfitting_diagnosis(folds, metric_keys=("r2",))
        assert result["total_issues"] == len(result["metrics"]["r2"]["issues"])