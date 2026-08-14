"""G7 boost: ms_strategy/src/backtest/combinatorial_purged_cv.py 单元测试.

覆盖 CPCVConfig / CPCVSplit / CPCVResult / CombinatorialPurgedCV 的全部公开接口,
包括配置校验/分割生成/purge/embargo/run/aggregate/summary 的核心路径与边界分支.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.combinatorial_purged_cv import (  # noqa: E402
    CombinatorialPurgedCV,
    CPCVConfig,
    CPCVResult,
    CPCVSplit,
)

# ============================================================
# 1. 配置校验
# ============================================================


class TestConfigValidation:
    def test_default_config(self):
        cv = CombinatorialPurgedCV()
        assert cv.config.n_groups == 6
        assert cv.config.n_test_groups == 2

    def test_none_config_uses_default(self):
        cv = CombinatorialPurgedCV(None)
        assert cv.config.n_groups == 6

    def test_n_groups_lt_2_raises(self):
        with pytest.raises(ValueError, match="n_groups 必须 >= 2"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=1))

    def test_n_test_groups_zero_raises(self):
        with pytest.raises(ValueError, match="n_test_groups 必须在"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=0))

    def test_n_test_groups_ge_n_groups_raises(self):
        with pytest.raises(ValueError, match="n_test_groups 必须在"):
            CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=6))

    def test_purge_pct_negative_raises(self):
        with pytest.raises(ValueError, match="purge_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(purge_pct=-0.1))

    def test_purge_pct_gt_one_raises(self):
        with pytest.raises(ValueError, match="purge_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(purge_pct=1.5))

    def test_embargo_pct_negative_raises(self):
        with pytest.raises(ValueError, match="embargo_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(embargo_pct=-0.1))

    def test_embargo_pct_gt_half_raises(self):
        with pytest.raises(ValueError, match="embargo_pct 必须在"):
            CombinatorialPurgedCV(CPCVConfig(embargo_pct=0.6))

    def test_boundary_values_valid(self):
        cv = CombinatorialPurgedCV(CPCVConfig(purge_pct=0.0, embargo_pct=0.0))
        assert cv.config.purge_pct == 0.0
        cv2 = CombinatorialPurgedCV(CPCVConfig(purge_pct=1.0, embargo_pct=0.5))
        assert cv2.config.purge_pct == 1.0


# ============================================================
# 2. split 生成
# ============================================================


class TestSplit:
    def test_split_count_n6_k2(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=600)
        assert len(splits) == 15  # C(6,2)=15

    def test_split_count_n6_k1(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=1, min_train_samples=10))
        splits = cv.split(n_samples=600)
        assert len(splits) == 6

    def test_split_count_n5_k2(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=5, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=500)
        assert len(splits) == 10

    def test_train_test_no_overlap(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2,
                                              embargo_pct=0.0, min_train_samples=10))
        splits = cv.split(n_samples=600)
        for split in splits:
            assert len(set(split.train_idx.tolist()) & set(split.test_idx.tolist())) == 0

    def test_test_idx_sorted(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=600)
        for split in splits:
            assert np.all(np.diff(split.test_idx) > 0)

    def test_path_id_sequential(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        splits = cv.split(n_samples=600)
        for i, split in enumerate(splits):
            assert split.path_id == i

    def test_min_train_samples_filter(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=1000))
        splits = cv.split(n_samples=600)
        assert len(splits) == 0

    def test_insufficient_samples_reduces_groups(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=10, n_test_groups=2, min_train_samples=5))
        splits = cv.split(n_samples=20)
        assert isinstance(splits, list)
        # 样本不足时 n_groups 被调整
        assert cv.config.n_groups <= 10

    def test_embargo_removes_post_test(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2,
                                              embargo_pct=0.02, min_train_samples=10))
        n_samples = 600
        n_embargo = max(1, int(n_samples * 0.02))
        splits = cv.split(n_samples=n_samples)
        for split in splits:
            test_max = int(split.test_idx[-1])
            embargo_range = set(range(test_max + 1, min(test_max + n_embargo + 1, n_samples)))
            assert len(embargo_range & set(split.train_idx.tolist())) == 0

    def test_purge_removes_around_test(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2,
                                              purge_pct=0.1, embargo_pct=0.0,
                                              min_train_samples=10))
        splits = cv.split(n_samples=600)
        for split in splits:
            test_min = int(split.test_idx[0])
            purge_n = max(1, int(len(split.test_idx) * 0.1))
            purge_lo_range = set(range(max(0, test_min - purge_n), test_min))
            assert len(purge_lo_range & set(split.train_idx.tolist())) == 0


# ============================================================
# 3. _compute_group_boundaries
# ============================================================


class TestGroupBoundaries:
    def test_even_split(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=4, n_test_groups=1, min_train_samples=1))
        bounds = cv._compute_group_boundaries(400)
        assert len(bounds) == 4
        assert bounds[0] == (0, 100)
        assert bounds[-1] == (300, 400)

    def test_uneven_split(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=3, n_test_groups=1, min_train_samples=1))
        bounds = cv._compute_group_boundaries(100)
        assert len(bounds) == 3
        # 验证不重叠且覆盖
        for i in range(len(bounds) - 1):
            assert bounds[i][1] <= bounds[i + 1][0]

    def test_samples_lt_groups(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=10, n_test_groups=1, min_train_samples=1))
        bounds = cv._compute_group_boundaries(5)
        # 样本不足, 实际组数 < 10
        assert len(bounds) < 10
        assert cv.config.n_groups == len(bounds)


# ============================================================
# 4. split_with_labels
# ============================================================


class TestSplitWithLabels:
    def test_label_col_none(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        data = pd.DataFrame({"ret": np.random.normal(0, 0.01, 600)})
        splits = cv.split_with_labels(data, label_col=None)
        assert len(splits) > 0

    def test_label_col_not_in_columns(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        data = pd.DataFrame({"ret": np.random.normal(0, 0.01, 600)})
        splits = cv.split_with_labels(data, label_col="nonexistent")
        assert len(splits) > 0

    def test_label_col_present(self):
        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=10))
        data = pd.DataFrame({"ret": np.random.normal(0, 0.01, 600),
                             "label": np.random.choice([0, 1], 600)})
        splits = cv.split_with_labels(data, label_col="label")
        assert len(splits) > 0


# ============================================================
# 5. run
# ============================================================


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
        assert all(r.n_test_samples > 0 for r in results)

    def test_run_strategy_exception_skipped(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )
        call_count = [0]

        def strategy_fn(train_df, test_df):
            call_count[0] += 1
            if call_count[0] == 3:
                raise ValueError("模拟失败")
            return pd.Series([0.001] * len(test_df))

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) > 0
        assert len(results) < 15

    def test_run_returns_none_skipped(self):
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return None

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) == 0

    def test_run_returns_empty_skipped(self):
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([], dtype=float)

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) == 0

    def test_run_insufficient_train_skipped(self):
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=5000))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) == 0

    def test_run_key_error_caught(self):
        data = pd.DataFrame(
            {"ret": np.random.normal(0, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            raise KeyError("missing column")

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        assert len(results) == 0


# ============================================================
# 6. _compute_metrics
# ============================================================


class TestComputeMetrics:
    def test_positive_sharpe(self):
        returns = pd.Series([0.001] * 100)
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sharpe > 0
        assert sortino > 0
        assert max_dd == 0.0

    def test_negative_sharpe(self):
        returns = pd.Series([-0.001] * 100)
        sharpe, _sortino, max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sharpe < 0
        assert max_dd > 0

    def test_empty_series(self):
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(pd.Series([]))
        assert sharpe == 0.0
        assert sortino == 0.0
        assert max_dd == 0.0

    def test_single_element(self):
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(pd.Series([0.001]))
        assert sharpe == 0.0

    def test_zero_std(self):
        returns = pd.Series([0.0] * 100)
        sharpe, sortino, max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sharpe == 0.0

    def test_no_downside_positive_mean(self):
        returns = pd.Series([0.001, 0.002, 0.003])
        _sharpe, sortino, _max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sortino == float("inf")

    def test_no_downside_non_positive_mean(self):
        returns = pd.Series([0.0, 0.0, 0.0])
        _sharpe, sortino, _max_dd = CombinatorialPurgedCV._compute_metrics(returns)
        assert sortino == 0.0


# ============================================================
# 7. aggregate
# ============================================================


class TestAggregate:
    def test_empty_results(self):
        cv = CombinatorialPurgedCV()
        agg = cv.aggregate([])
        assert agg["n_paths"] == 0

    def test_normal_aggregate(self):
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
        for key in ("sharpe_mean", "sharpe_std", "sharpe_median",
                    "sharpe_p5", "sharpe_p25", "sharpe_p75", "sharpe_p95",
                    "sharpe_min", "sharpe_max", "sortino_mean", "sortino_median",
                    "max_dd_mean", "max_dd_p95", "overall_sharpe",
                    "sharpe_distribution"):
            assert key in agg
        assert len(agg["sharpe_distribution"]) == len(results)

    def test_quantile_ordering(self):
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

    def test_returns_none_skipped(self):
        # 构造 returns=None 的 CPCVResult
        r = CPCVResult(path_id=0, test_groups=(0, 1), sharpe=1.0, returns=None)
        cv = CombinatorialPurgedCV()
        agg = cv.aggregate([r])
        assert agg["n_paths"] == 1
        assert agg["overall_sharpe"] == 0.0


# ============================================================
# 8. summary
# ============================================================


class TestSummary:
    def test_empty(self):
        cv = CombinatorialPurgedCV()
        s = cv.summary([])
        assert "无有效路径" in s

    def test_normal(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.02, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2, min_train_samples=20))
        results = cv.run(data, strategy_fn, verbose=False)
        s = cv.summary(results)
        assert "CPCV 汇总" in s
        assert "Sharpe Ratio" in s

    def test_none_uses_last_results(self):
        cv = CombinatorialPurgedCV()
        cv._last_results = []
        s = cv.summary(None)
        assert "无有效路径" in s


# ============================================================
# 9. dataclass 构造
# ============================================================


class TestDataclasses:
    def test_cpcv_split(self):
        split = CPCVSplit(
            path_id=0,
            train_idx=np.array([1, 2, 3]),
            test_idx=np.array([0, 4]),
            test_groups=(0, 1),
        )
        assert split.path_id == 0
        assert split.test_groups == (0, 1)

    def test_cpcv_result_defaults(self):
        r = CPCVResult(path_id=0, test_groups=(0, 1))
        assert r.sharpe == 0.0
        assert r.sortino == 0.0
        assert r.max_dd == 0.0
        assert r.returns is None
        assert r.n_test_samples == 0
        assert r.n_train_samples == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
