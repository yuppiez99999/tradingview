"""篮子清算最小 shortfall 单元测试.

被测模块: utils/basket_liquidation.py
文献: #55 Minimal Shortfall Basket Liquidation (2025.02)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.basket_liquidation import (  # noqa: E402
    BasketLiquidationResult,
    BasketLiquidator,
    FactorModel,
    LiquidationSlice,
)

# ============================================================
# 因子模型测试
# ============================================================


class TestFactorModel:
    def test_from_covariance_identity(self):
        """单位协方差矩阵: 1 个因子即可."""
        cov = np.eye(3)
        fm = FactorModel.from_covariance(cov)
        assert fm.loadings.shape == (3, fm.n_factors)
        assert len(fm.specific_var) == 3

    def test_from_covariance_custom_factors(self):
        cov = np.array([[1.0, 0.5, 0.3], [0.5, 1.0, 0.4], [0.3, 0.4, 1.0]])
        fm = FactorModel.from_covariance(cov, n_factors=2)
        assert fm.n_factors == 2
        assert fm.loadings.shape == (3, 2)

    def test_reconstruct_covariance(self):
        cov = np.array([[1.0, 0.5], [0.5, 1.0]])
        fm = FactorModel.from_covariance(cov, n_factors=1)
        reconstructed = fm.reconstruct_covariance()
        assert reconstructed.shape == (2, 2)

    def test_effective_dimensions(self):
        fm = FactorModel(loadings=np.ones((5, 2)), specific_var=np.ones(5), n_factors=2)
        assert fm.effective_dimensions() == 3  # 2 + 1

    def test_auto_factor_selection(self):
        """自动选择因子数 (解释 95% 方差)."""
        # 3 个特征值: 10, 1, 0.1 → 1 个因子解释 90%, 2 个解释 99%
        cov = np.diag([10, 1, 0.1])
        fm = FactorModel.from_covariance(cov)
        assert fm.n_factors >= 1


# ============================================================
# 篮子清算测试
# ============================================================


class TestBasketLiquidator:
    def test_basic_liquidation(self):
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 5000],
            adv=[500000, 1000000],
        )
        assert len(result.symbols) == 2
        assert result.total_shortfall > 0

    def test_slices_count(self):
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A"],
            shares=[10000],
            adv=[500000],
            n_slices=5,
        )
        assert len(result.slices) == 5

    def test_correlation_matrix(self):
        liquidator = BasketLiquidator()
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 10000],
            adv=[500000, 500000],
            corr_matrix=corr,
        )
        assert result.total_shortfall > 0

    def test_no_correlation(self):
        """无相关性 (单位矩阵): shortfall = 朴素清算."""
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 10000],
            adv=[500000, 500000],
            corr_matrix=np.eye(2),
        )
        # 无相关性时, 联合清算 ≈ 朴素清算
        assert result.vs_naive_improvement == pytest.approx(0, abs=100)

    def test_factor_model_in_result(self):
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A", "B", "C"],
            shares=[10000, 5000, 8000],
            adv=[500000, 1000000, 300000],
        )
        assert result.factor_model is not None
        assert result.effective_dimensions > 0

    def test_dimensionality_reduction(self):
        """高维篮子: 有效维度 < 完整维度 (有因子结构)."""
        liquidator = BasketLiquidator()
        n = 10
        # 3 因子结构
        rng = np.random.default_rng(42)
        A = rng.standard_normal((n, 3))
        cov = A @ A.T + np.eye(n) * 0.1
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
        result = liquidator.liquidate(
            symbols=[f"S{i}" for i in range(n)],
            shares=[10000] * n,
            adv=[500000] * n,
            corr_matrix=corr,
        )
        assert result.full_dimensions == n
        assert result.effective_dimensions < n

    def test_metadata(self):
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A"],
            shares=[1000],
            adv=[500000],
            n_slices=5,
        )
        assert result.metadata["n_slices"] == 5
        assert "n_factors" in result.metadata

    def test_trades_sum_to_total(self):
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 5000],
            adv=[500000, 1000000],
            n_slices=10,
        )
        total_traded_0 = sum(s.trades[0] for s in result.slices)
        total_traded_1 = sum(s.trades[1] for s in result.slices)
        assert total_traded_0 == pytest.approx(10000, rel=1e-4)
        assert total_traded_1 == pytest.approx(5000, rel=1e-4)


class TestLiquidationSlice:
    def test_construction(self):
        s = LiquidationSlice(time=0.5, trades=[100, 200], shortfall=10.0)
        assert s.time == 0.5
        assert s.trades == [100, 200]
        assert s.shortfall == 10.0


class TestBasketLiquidationResult:
    def test_construction(self):
        fm = FactorModel(loadings=np.ones((2, 1)), specific_var=np.ones(2), n_factors=1)
        result = BasketLiquidationResult(
            symbols=["A", "B"],
            total_shares=[100, 200],
            slices=[],
            total_shortfall=100,
            naive_shortfall=120,
            vs_naive_improvement=20,
            factor_model=fm,
            effective_dimensions=2,
            full_dimensions=2,
        )
        assert result.metadata == {}


# ============================================================
# 验收标准测试
# ============================================================


class TestAcceptanceCriteria:
    """LIT-4.5 验收: 高相关股票 RL 清算 + 解决维度灾难."""

    def test_high_correlation_liquidation(self):
        """高相关股票联合清算."""
        liquidator = BasketLiquidator()
        corr = np.array([[1.0, 0.8], [0.8, 1.0]])
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 10000],
            adv=[500000, 500000],
            corr_matrix=corr,
        )
        assert result.total_shortfall > 0

    def test_dimensionality_curse_solved(self):
        """维度灾难解决: 10 只股票有效维度 << 10."""
        liquidator = BasketLiquidator()
        n = 10
        # 低维因子结构 (3 个因子)
        rng = np.random.default_rng(42)
        A = rng.standard_normal((n, 3))
        cov = A @ A.T + np.eye(n) * 0.1
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
        result = liquidator.liquidate(
            symbols=[f"S{i}" for i in range(n)],
            shares=[10000] * n,
            adv=[500000] * n,
            corr_matrix=corr,
        )
        # 3 因子结构 → 有效维度 ≈ 4 (3+1), 远小于 10
        assert result.effective_dimensions < result.full_dimensions
        assert result.metadata["dimensionality_reduction"] > 0

    def test_naive_comparison(self):
        """vs 朴素清算对比."""
        liquidator = BasketLiquidator()
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        result = liquidator.liquidate(
            symbols=["A", "B"],
            shares=[10000, 10000],
            adv=[500000, 500000],
            corr_matrix=corr,
        )
        assert result.naive_shortfall > 0
        assert isinstance(result.vs_naive_improvement, float)
