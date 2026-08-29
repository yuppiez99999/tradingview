"""分数阶差分单元测试.

被测模块: utils/fractional_differencing.py
文献: #65 Comparative Financial Data Differentiation (2025.05)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.fractional_differencing import (  # noqa: E402
    DifferencingResult,
    FractionalDifferencing,
    FractionalDifferencingBacktest,
)

# ============================================================
# 权重计算测试
# ============================================================


class TestComputeWeights:
    def test_d_zero(self):
        """d=0: w=[1, 0, 0, ...] (无差分)."""
        fd = FractionalDifferencing()
        weights = fd.compute_weights(0.0)
        assert weights[0] == 1.0
        assert all(abs(w) < 1e-5 for w in weights[1:])

    def test_d_one(self):
        """d=1: w=[1, -1, 0, 0, ...] (一阶差分)."""
        fd = FractionalDifferencing()
        weights = fd.compute_weights(1.0)
        assert weights[0] == pytest.approx(1.0)
        assert weights[1] == pytest.approx(-1.0)
        assert all(abs(w) < 1e-5 for w in weights[2:])

    def test_d_half(self):
        """d=0.5: 权重递减振荡."""
        fd = FractionalDifferencing()
        weights = fd.compute_weights(0.5)
        assert weights[0] == 1.0
        assert weights[1] < 0  # w_1 = -0.5
        assert len(weights) > 2

    def test_weights_decay(self):
        """权重绝对值递减趋于 0."""
        fd = FractionalDifferencing()
        weights = fd.compute_weights(0.4)
        abs_weights = [abs(w) for w in weights[1:]]
        # 总体递减趋势
        assert abs_weights[-1] < abs_weights[0]

    def test_threshold_stops(self):
        """权重在阈值处停止."""
        fd = FractionalDifferencing(weight_threshold=1e-3)
        weights = fd.compute_weights(0.5)
        assert abs(weights[-1]) < 1e-3


# ============================================================
# 差分测试
# ============================================================


class TestDifferencing:
    def test_basic_differencing(self):
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=0.4)
        assert len(result.differenced) > 0
        assert result.d == 0.4

    def test_d_zero_no_change(self):
        """d=0: 差分后 = 原始序列."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=0.0)
        # d=0 时, 差分 = 原始 (去除前缀 NaN)
        assert np.allclose(
            result.differenced, series[result.n_weights - 1 :], atol=1e-6
        )

    def test_d_one_first_difference(self):
        """d=1: 差分 = 一阶差分."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=1.0)
        expected = np.diff(series)
        assert np.allclose(result.differenced, expected, atol=1e-6)

    def test_memory_retained(self):
        """记忆保持 = 1 - d."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=0.4)
        assert result.memory_retained == pytest.approx(0.6)

    def test_d_zero_full_memory(self):
        """d=0: 完全记忆."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=0.0)
        assert result.memory_retained == pytest.approx(1.0)

    def test_d_one_no_memory(self):
        """d=1: 无记忆."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=1.0)
        assert result.memory_retained == pytest.approx(0.0)

    def test_result_fields(self):
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(100)
        result = fd.differencing(series, d=0.5)
        assert result.original is not None
        assert result.n_weights > 0
        assert len(result.weights) == result.n_weights


# ============================================================
# 最优 d 搜索测试
# ============================================================


class TestFindOptimalD:
    def test_returns_d_and_result(self):
        fd = FractionalDifferencing()
        series = np.cumsum(np.random.default_rng(42).standard_normal(200))
        d, result = fd.find_optimal_d(series)
        assert 0 <= d <= 1
        assert isinstance(result, DifferencingResult)

    def test_stationary_series_low_d(self):
        """平稳序列: 最优 d 接近 0."""
        fd = FractionalDifferencing()
        series = np.random.default_rng(42).standard_normal(200)  # 白噪声
        d, _ = fd.find_optimal_d(series)
        assert d <= 0.5  # 平稳序列不需要大 d

    def test_non_stationary_series_higher_d(self):
        """非平稳序列 (随机游走): 需要更大 d."""
        fd = FractionalDifferencing()
        series = np.cumsum(np.random.default_rng(42).standard_normal(200))  # 随机游走
        d, _ = fd.find_optimal_d(series)
        assert d > 0  # 需要差分


# ============================================================
# 对比测试
# ============================================================


class TestCompareWithLogReturns:
    def test_comparison_report(self):
        fd = FractionalDifferencing()
        prices = 100 * np.exp(
            np.cumsum(np.random.default_rng(42).normal(0.0002, 0.01, 200))
        )
        report = fd.compare_with_log_returns(prices, d=0.4)
        assert report["d"] == 0.4
        assert report["fd_memory_retained"] == pytest.approx(0.6)
        assert report["lr_memory_retained"] == 0.0
        assert report["memory_advantage"] == pytest.approx(0.6)

    def test_memory_advantage_positive(self):
        """分数阶差分记忆优势 > 0."""
        fd = FractionalDifferencing()
        prices = 100 * np.exp(
            np.cumsum(np.random.default_rng(42).normal(0.0002, 0.01, 200))
        )
        report = fd.compare_with_log_returns(prices, d=0.4)
        assert report["memory_advantage"] > 0


# ============================================================
# 回测验证测试
# ============================================================


class TestFractionalDifferencingBacktest:
    def test_generate_synthetic_index(self):
        bt = FractionalDifferencingBacktest()
        prices = bt.generate_synthetic_index(n=100)
        assert len(prices) == 100
        assert all(prices > 0)  # 价格为正

    def test_backtest_single(self):
        bt = FractionalDifferencingBacktest()
        prices = bt.generate_synthetic_index(n=300)
        result = bt.backtest_single("TEST", prices)
        assert result.index_name == "TEST"
        assert 0 <= result.optimal_d <= 1

    def test_backtest_multi(self):
        bt = FractionalDifferencingBacktest()
        indices = {
            "A": bt.generate_synthetic_index(seed=1),
            "B": bt.generate_synthetic_index(seed=2),
        }
        result = bt.backtest_multi(indices)
        assert len(result.results) == 2
        assert isinstance(result.all_stationary, bool)

    def test_four_indices_backtest(self):
        """验收: 4 指数回测验证."""
        bt = FractionalDifferencingBacktest()
        indices = {
            "沪深300": bt.generate_synthetic_index(seed=1, n=300),
            "中证500": bt.generate_synthetic_index(seed=2, n=300),
            "创业板": bt.generate_synthetic_index(seed=3, n=300),
            "上证50": bt.generate_synthetic_index(seed=4, n=300),
        }
        result = bt.backtest_multi(indices)
        assert len(result.results) == 4
        assert result.avg_memory_retained > 0


# ============================================================
# 验收标准测试
# ============================================================


class TestAcceptanceCriteria:
    """LIT-5.2 验收: 记忆保持 + 预测精度提升 + 4 指数回测."""

    def test_memory_preservation(self):
        """验收: 分数阶差分保持记忆 (vs 对数收益无记忆)."""
        fd = FractionalDifferencing()
        prices = 100 * np.exp(
            np.cumsum(np.random.default_rng(42).normal(0.0002, 0.01, 200))
        )
        report = fd.compare_with_log_returns(prices, d=0.4)
        assert report["fd_memory_retained"] > 0  # 分数阶有记忆
        assert report["lr_memory_retained"] == 0  # 对数收益无记忆

    def test_stationarity_with_memory(self):
        """验收: 平稳 + 记忆保持."""
        fd = FractionalDifferencing()
        series = np.cumsum(np.random.default_rng(42).standard_normal(200))
        d, result = fd.find_optimal_d(series)
        assert result.is_stationary  # 平稳
        assert result.memory_retained > 0  # 有记忆

    def test_four_index_validation(self):
        """验收: 4 指数回测全部平稳."""
        bt = FractionalDifferencingBacktest()
        indices = {
            "沪深300": bt.generate_synthetic_index(seed=1, n=300),
            "中证500": bt.generate_synthetic_index(seed=2, n=300),
            "创业板": bt.generate_synthetic_index(seed=3, n=300),
            "上证50": bt.generate_synthetic_index(seed=4, n=300),
        }
        result = bt.backtest_multi(indices)
        assert len(result.results) == 4
        assert result.avg_memory_retained > 0
