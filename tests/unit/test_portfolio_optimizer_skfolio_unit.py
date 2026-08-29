"""
skfolio 统一优化后端 — 单元测试
================================

测试覆盖:
- Strategy 枚举
- OptimizationResult 结果
- NumpyOptimizer 5 种策略
- SkfolioConfig 配置
- SkfolioOptimizer 集成接口
- 便捷函数
- 端到端策略对比

文献: #42 skfolio 2025.07
"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.portfolio_optimizer_skfolio import (
    NumpyOptimizer,
    OptimizationResult,
    SkfolioConfig,
    SkfolioOptimizer,
    Strategy,
    compare_all_strategies,
    optimize_portfolio,
)

# ============================================================
# 枚举测试
# ============================================================


class TestStrategy:
    """策略枚举测试。"""

    def test_count(self):
        assert len(Strategy) == 5

    def test_values(self):
        assert Strategy.MEAN_VARIANCE.value == "mean_variance"
        assert Strategy.MAX_SHARPE.value == "max_sharpe"
        assert Strategy.MIN_VARIANCE.value == "min_variance"
        assert Strategy.RISK_PARITY.value == "risk_parity"
        assert Strategy.HRP.value == "hrp"


# ============================================================
# 结果测试
# ============================================================


class TestOptimizationResult:
    """优化结果测试。"""

    def test_get_weights(self):
        r = OptimizationResult(weights=np.array([0.3, 0.7]))
        np.testing.assert_array_equal(r.get_weights(), [0.3, 0.7])

    def test_to_dict(self):
        r = OptimizationResult(weights=np.array([0.5, 0.5]))
        d = r.to_dict()
        assert "weights" in d
        assert "sharpe_ratio" in d


# ============================================================
# Numpy 优化器测试
# ============================================================


class TestNumpyOptimizer:
    """numpy 核心优化器测试。"""

    def setup_method(self):
        self.rng = np.random.default_rng(42)
        self.returns = self.rng.standard_normal((252, 5)) * 0.02 + 0.001

    def test_mean_variance(self):
        weights = NumpyOptimizer.mean_variance(self.returns)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-6)

    def test_max_sharpe(self):
        weights = NumpyOptimizer.max_sharpe(self.returns)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-6)

    def test_min_variance(self):
        weights = NumpyOptimizer.min_variance(self.returns)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-6)

    def test_risk_parity(self):
        weights = NumpyOptimizer.risk_parity(self.returns, n_iters=50)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-6)

    def test_risk_pity_equal_risk(self):
        """风险平价: 各资产风险贡献接近相等。"""
        returns = self.rng.standard_normal((500, 4)) * 0.02 + 0.001
        weights = NumpyOptimizer.risk_parity(returns, n_iters=200)
        cov = np.cov(returns, rowvar=False)
        risk_contrib = weights * (cov @ weights)
        risk_contrib = risk_contrib / risk_contrib.sum()
        assert np.std(risk_contrib) < 0.05

    def test_hrp(self):
        pytest.importorskip("scipy")
        weights = NumpyOptimizer.hrp(self.returns)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-6)

    def test_hrp_single_asset(self):
        """单资产 HRP 返回 [1.0]。"""
        returns = np.array([[0.01], [0.02], [0.03]])
        weights = NumpyOptimizer.hrp(returns)
        np.testing.assert_array_almost_equal(weights, [1.0])

    def test_min_variance_lowest_risk(self):
        """最小方差策略风险最低。"""
        mv = NumpyOptimizer.min_variance(self.returns)
        ms = NumpyOptimizer.max_sharpe(self.returns)
        cov = np.cov(self.returns, rowvar=False)
        risk_mv = np.sqrt(mv @ cov @ mv)
        risk_ms = np.sqrt(ms @ cov @ ms)
        assert risk_mv <= risk_ms + 1e-8


# ============================================================
# 配置测试
# ============================================================


class TestSkfolioConfig:
    """配置测试。"""

    def test_defaults(self):
        config = SkfolioConfig()
        assert config.strategy == Strategy.MIN_VARIANCE
        assert config.risk_aversion == 1.0
        assert config.max_weight == 1.0

    def test_custom(self):
        config = SkfolioConfig(
            strategy=Strategy.MAX_SHARPE,
            risk_aversion=2.0,
            max_weight=0.3,
        )
        assert config.strategy == Strategy.MAX_SHARPE
        assert config.risk_aversion == 2.0
        assert config.max_weight == 0.3


# ============================================================
# 集成优化器测试
# ============================================================


class TestSkfolioOptimizer:
    """集成优化器测试。"""

    def setup_method(self):
        self.rng = np.random.default_rng(42)
        self.returns = self.rng.standard_normal((252, 5)) * 0.02 + 0.001

    def test_fit_min_variance(self):
        opt = SkfolioOptimizer(strategy=Strategy.MIN_VARIANCE)
        opt.fit(self.returns)
        weights = opt.get_weights()
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_fit_max_sharpe(self):
        opt = SkfolioOptimizer(strategy=Strategy.MAX_SHARPE)
        opt.fit(self.returns)
        result = opt.get_result()
        assert result.strategy == Strategy.MAX_SHARPE
        assert result.success

    def test_fit_mean_variance(self):
        opt = SkfolioOptimizer(strategy=Strategy.MEAN_VARIANCE)
        opt.fit(self.returns)
        assert opt.get_result().success

    def test_fit_risk_parity(self):
        opt = SkfolioOptimizer(strategy=Strategy.RISK_PARITY)
        opt.fit(self.returns)
        assert opt.get_result().success

    def test_fit_hrp(self):
        pytest.importorskip("scipy")
        opt = SkfolioOptimizer(strategy=Strategy.HRP)
        opt.fit(self.returns)
        assert opt.get_result().success

    def test_predict(self):
        opt = SkfolioOptimizer(strategy=Strategy.MIN_VARIANCE)
        opt.fit(self.returns)
        pred = opt.predict(self.returns)
        assert isinstance(pred, float)

    def test_not_fitted_error(self):
        opt = SkfolioOptimizer()
        with pytest.raises(RuntimeError):
            opt.get_weights()

    def test_constraints(self):
        """权重约束。"""
        config = SkfolioConfig(max_weight=0.3)
        opt = SkfolioOptimizer(strategy=Strategy.MIN_VARIANCE, config=config)
        opt.fit(self.returns)
        weights = opt.get_weights()
        assert np.all(weights <= 0.3 + 1e-6)

    def test_compare_strategies(self):
        """对比所有策略。"""
        opt = SkfolioOptimizer()
        results = opt.compare_strategies(self.returns)
        assert len(results) == 5
        for _name, result in results.items():
            assert isinstance(result, OptimizationResult)


# ============================================================
# 便捷函数测试
# ============================================================


class TestConvenienceFunctions:
    """便捷函数测试。"""

    def setup_method(self):
        self.rng = np.random.default_rng(42)
        self.returns = self.rng.standard_normal((252, 4)) * 0.02 + 0.001

    def test_optimize_portfolio(self):
        weights = optimize_portfolio(self.returns, strategy=Strategy.MIN_VARIANCE)
        assert len(weights) == 4
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_compare_all_strategies(self):
        comparison = compare_all_strategies(self.returns)
        assert len(comparison) == 5
        for _name, metrics in comparison.items():
            assert "sharpe_ratio" in metrics
            assert "expected_return" in metrics


# ============================================================
# 端到端集成测试
# ============================================================


class TestEndToEnd:
    """端到端集成测试。"""

    def test_all_strategies_produce_valid_weights(self):
        """所有策略产生有效权重。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((252, 6)) * 0.02 + 0.001

        for strategy in Strategy:
            if strategy == Strategy.HRP:
                pytest.importorskip("scipy")
            opt = SkfolioOptimizer(strategy=strategy)
            opt.fit(returns)
            weights = opt.get_weights()
            assert abs(weights.sum() - 1.0) < 1e-6, f"{strategy.value} 权重和不为1"
            assert np.all(weights >= -1e-6), f"{strategy.value} 权重为负"

    def test_sharpe_maximization(self):
        """MaxSharpe 策略夏普最高。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((500, 5)) * 0.02 + np.array(
            [0.001, 0.002, 0.003, 0.0005, 0.004]
        )

        opt_ms = SkfolioOptimizer(strategy=Strategy.MAX_SHARPE)
        opt_mv = SkfolioOptimizer(strategy=Strategy.MIN_VARIANCE)
        opt_ms.fit(returns)
        opt_mv.fit(returns)

        assert (
            opt_ms.get_result().sharpe_ratio >= opt_mv.get_result().sharpe_ratio - 1e-6
        )

    def test_risk_parity_diversified(self):
        """风险平价权重分散。"""
        rng = np.random.default_rng(42)
        returns = rng.standard_normal((500, 4)) * 0.02 + 0.001
        weights = NumpyOptimizer.risk_parity(returns, n_iters=200)
        assert np.min(weights) > 0.05
        assert np.max(weights) < 0.50
