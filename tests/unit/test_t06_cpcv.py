"""T06: Combinatorial Purged CV 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.combinatorial_purged_cv import (
    CombinatorialPurgedCV,
    CPCVConfig,
    CPCVResult,
)


class TestConfigValidation:
    def test_invalid_n_groups_raises(self):
        with pytest.raises(ValueError, match="n_groups 必须 >= 2"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=1))

    def test_invalid_n_test_groups_zero_raises(self):
        with pytest.raises(ValueError, match="n_test_groups 必须在"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=0))

    def test_invalid_n_test_groups_too_large_raises(self):
        with pytest.raises(ValueError, match="n_test_groups 必须在"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=6))

    def test_invalid_purge_pct_raises(self):
        with pytest.raises(ValueError, match="purge_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(purge_pct=-0.1))

    def test_invalid_embargo_pct_raises(self):
        with pytest.raises(ValueError, match="embargo_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(embargo_pct=0.6))

    def test_default_config_valid(self):
        cv = CombinatorialPurgedCV()
        assert cv.config.n_groups == 6
        assert cv.config.n_test_groups == 2


class TestSplitGeneration:
    def test_split_count_matches_combination(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=600)
        assert len(splits) == 15  # C(6,2) = 15

    def test_split_count_n6_k1(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=1, min_train_samples=10))
        splits = cv.split(n_samples=600)
        assert len(splits) == 6

    def test_split_count_n5_k2(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=5, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=500)
        assert len(splits) == 10

    def test_each_split_has_non_empty_test(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=600)
        for split in splits:
            assert len(split.test_idx) > 0
            assert len(split.train_idx) > 0


class TestPurgeAndEmbargo:
    def test_train_test_no_overlap(self):
        cv = CombinatorialPurgedCV(CPCVConfig(
            n_groups=6, n_test_groups=2, embargo_pct=0.0, min_train_samples=10
        ))
        splits = cv.split(n_samples=600)
        for split in splits:
            train_set = set(split.train_idx.tolist())
            test_set = set(split.test_idx.tolist())
            assert len(train_set & test_set) == 0

    def test_embargo_removes_post_test_samples(self):
        cv = CombinatorialPurgedCV(CPCVConfig(
            n_groups=6, n_test_groups=2, embargo_pct=0.02, min_train_samples=10
        ))
        n_samples = 600
        n_embargo = max(1, int(n_samples * 0.02))
        splits = cv.split(n_samples=n_samples)
        for split in splits:
            test_max = int(split.test_idx[-1])
            embargo_range = set(range(test_max + 1, min(test_max + n_embargo + 1, n_samples)))
            train_set = set(split.train_idx.tolist())
            assert len(embargo_range & train_set) == 0

    def test_purge_removes_around_test_samples(self):
        cv = CombinatorialPurgedCV(CPCVConfig(
            n_groups=6, n_test_groups=2, purge_pct=0.1, embargo_pct=0.0, min_train_samples=10
        ))
        splits = cv.split(n_samples=600)
        for split in splits:
            test_min = int(split.test_idx[0])
            purge_n = max(1, int(len(split.test_idx) * 0.1))
            purge_lo_range = set(range(max(0, test_min - purge_n), test_min))
            train_set = set(split.train_idx.tolist())
            assert len(purge_lo_range & train_set) == 0


class TestRun:
    def test_run_returns_results(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series(train_df["ret"].mean(), index=test_df.index)

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) > 0
        assert all(isinstance(r, CPCVResult) for r in results)

    def test_run_handles_strategy_exception(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        call_count = [0]
        def strategy_fn(train_df, test_df):
            call_count[0] += 1
            if call_count[0] == 3:
                raise ValueError("模拟策略失败")
            return pd.Series([0.001] * len(test_df))

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) > 0
        assert len(results) < 15


class TestAggregate:
    def test_aggregate_returns_distribution_stats(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series(train_df["ret"].mean(), index=test_df.index)

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        agg = cv.aggregate(results)

        assert agg["n_paths"] == len(results)
        for key in ["sharpe_mean", "sharpe_std", "sharpe_median",
                    "sharpe_p5", "sharpe_p95", "sharpe_min", "sharpe_max",
                    "sharpe_distribution"]:
            assert key in agg
        assert len(agg["sharpe_distribution"]) == len(results)

    def test_aggregate_empty_results(self):
        cv = CombinatorialPurgedCV()
        agg = cv.aggregate([])
        assert agg["n_paths"] == 0

    def test_aggregate_quantile_ordering(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        agg = cv.aggregate(results)

        assert agg["sharpe_p5"] <= agg["sharpe_median"] <= agg["sharpe_p95"]
        assert agg["sharpe_min"] <= agg["sharpe_p5"]
        assert agg["sharpe_p95"] <= agg["sharpe_max"]


class TestEdgeCases:
    def test_insufficient_samples_reduces_groups(self):
        cv = CombinatorialPurgedCV(CPCVConfig(
            n_groups=10, n_test_groups=2, min_train_samples=5
        ))
        splits = cv.split(n_samples=20)
        assert isinstance(splits, list)

    def test_min_train_samples_filter(self):
        cv = CombinatorialPurgedCV(CPCVConfig(
            n_groups=6, n_test_groups=2, min_train_samples=1000
        ))
        splits = cv.split(n_samples=600)
        assert len(splits) == 0

    def test_summary_handles_empty(self):
        cv = CombinatorialPurgedCV()
        s = cv.summary([])
        assert "无有效路径" in s


class TestMetricsComputation:
    def test_compute_metrics_positive_sharpe(self):
        returns = pd.Series([0.001] * 100)
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sharpe > 0
        assert sortino > 0
        assert max_dd == 0.0

    def test_compute_metrics_negative_sharpe(self):
        returns = pd.Series([-0.001] * 100)
        sharpe, _sortino, max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sharpe < 0
        assert max_dd > 0

    def test_compute_metrics_empty_series(self):
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(pd.Series([]))
        assert sharpe == 0.0
        assert sortino == 0.0
        assert max_dd == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
