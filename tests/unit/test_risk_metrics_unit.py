"""risk_metrics 单元测试 — 风险指标计算工具全分支覆盖.

被测模块: utils/risk_metrics.py
覆盖目标: >=95%
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_metrics import (  # noqa: E402
    _align_and_dropna,
    calculate_alpha,
    calculate_beta,
    calculate_calmar_ratio,
    calculate_correlation,
    calculate_es,
    calculate_information_ratio,
    calculate_max_drawdown,
    calculate_performance_metrics,
    calculate_portfolio_weights,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_tracking_error,
    calculate_var,
    calculate_volatility,
    calculate_win_rate,
)

# ============================================================
# calculate_var
# ============================================================

class TestCalculateVar:
    def test_historical_var(self):
        returns = np.array([-0.05, -0.02, 0.01, 0.03, -0.01, 0.02, -0.03, 0.005, -0.015, 0.025])
        var = calculate_var(returns, confidence_level=0.95, method="historical")
        assert var > 0

    def test_parametric_var(self):
        returns = np.random.normal(0.001, 0.02, 1000)
        var = calculate_var(returns, confidence_level=0.95, method="parametric")
        assert var > 0

    def test_monte_carlo_var(self):
        returns = np.random.normal(0.001, 0.02, 1000)
        var = calculate_var(returns, confidence_level=0.95, method="monte_carlo")
        assert var > 0

    def test_empty_returns(self):
        assert calculate_var(np.array([])) == 0.0

    def test_all_nan_returns(self):
        assert calculate_var(np.array([np.nan, np.nan])) == 0.0

    def test_nan_filtered(self):
        returns = np.array([0.01, np.nan, -0.02, np.nan, 0.03])
        var = calculate_var(returns, confidence_level=0.95)
        assert var > 0

    def test_invalid_method(self):
        returns = np.array([0.01, -0.01])
        var = calculate_var(returns, method="invalid")
        assert var == 0.03

    def test_positive_loss(self):
        returns = np.array([-0.05, -0.02, 0.01, 0.03])
        var = calculate_var(returns, confidence_level=0.95)
        assert var >= 0

    def test_99_confidence(self):
        returns = np.random.normal(0, 0.02, 1000)
        var95 = calculate_var(returns, 0.95)
        var99 = calculate_var(returns, 0.99)
        assert var99 >= var95


# ============================================================
# calculate_es
# ============================================================

class TestCalculateEs:
    def test_basic_es(self):
        returns = np.array([-0.05, -0.02, 0.01, 0.03, -0.01, 0.02, -0.03, 0.005, -0.015, 0.025])
        es = calculate_es(returns, confidence_level=0.95)
        assert es > 0

    def test_empty_returns(self):
        assert calculate_es(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_es(np.array([np.nan, np.nan])) == 0.0

    def test_es_gte_var(self):
        returns = np.random.normal(0, 0.02, 1000)
        var = calculate_var(returns, 0.95)
        es = calculate_es(returns, 0.95)
        assert es >= var * 0.99


# ============================================================
# calculate_max_drawdown
# ============================================================

class TestCalculateMaxDrawdown:
    def test_basic_drawdown(self):
        prices = np.array([100, 110, 105, 90, 95, 85])
        dd, start, end = calculate_max_drawdown(prices)
        assert 0 < dd < 1
        assert start < end

    def test_no_drawdown(self):
        prices = np.array([100, 110, 120, 130])
        dd, start, end = calculate_max_drawdown(prices)
        assert dd == 0.0

    def test_empty(self):
        dd, start, end = calculate_max_drawdown(np.array([]))
        assert dd == 0.0 and start == 0 and end == 0

    def test_all_nan(self):
        dd, start, end = calculate_max_drawdown(np.array([np.nan, np.nan]))
        assert dd == 0.0

    def test_nan_filtered(self):
        prices = np.array([100, np.nan, 110, 105, 90])
        dd, _, _ = calculate_max_drawdown(prices)
        assert dd > 0

    def test_single_price(self):
        dd, start, end = calculate_max_drawdown(np.array([100.0]))
        assert dd == 0.0


# ============================================================
# calculate_sharpe_ratio
# ============================================================

class TestCalculateSharpeRatio:
    def test_positive_sharpe(self):
        returns = np.random.normal(0.001, 0.02, 252)
        sharpe = calculate_sharpe_ratio(returns)
        assert isinstance(sharpe, float)

    def test_empty(self):
        assert calculate_sharpe_ratio(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_sharpe_ratio(np.array([np.nan])) == 0.0

    def test_zero_volatility(self):
        returns = np.array([0.001, 0.001, 0.001])
        sharpe = calculate_sharpe_ratio(returns)
        assert sharpe == 0.0

    def test_custom_risk_free(self):
        returns = np.random.normal(0.001, 0.02, 252)
        s1 = calculate_sharpe_ratio(returns, risk_free_rate=0.02)
        s2 = calculate_sharpe_ratio(returns, risk_free_rate=0.05)
        assert s1 != s2


# ============================================================
# calculate_sortino_ratio
# ============================================================

class TestCalculateSortinoRatio:
    def test_basic(self):
        returns = np.random.normal(0.001, 0.02, 252)
        sortino = calculate_sortino_ratio(returns)
        assert isinstance(sortino, float)

    def test_empty(self):
        assert calculate_sortino_ratio(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_sortino_ratio(np.array([np.nan])) == 0.0

    def test_all_positive(self):
        returns = np.array([0.01, 0.02, 0.005])
        sortino = calculate_sortino_ratio(returns)
        assert sortino == 0.0


# ============================================================
# calculate_calmar_ratio
# ============================================================

class TestCalculateCalmarRatio:
    def test_basic(self):
        returns = np.array([0.01, -0.02, 0.005, 0.015, -0.01])
        prices = np.array([100, 101, 99, 99.5, 101, 100])
        calmar = calculate_calmar_ratio(returns, prices)
        assert isinstance(calmar, float)

    def test_empty(self):
        assert calculate_calmar_ratio(np.array([]), np.array([])) == 0.0

    def test_no_drawdown_positive_return(self):
        returns = np.array([0.01, 0.02, 0.01])
        prices = np.array([100, 101, 103, 104])
        calmar = calculate_calmar_ratio(returns, prices)
        assert calmar == float("inf")

    def test_no_drawdown_zero_return(self):
        returns = np.array([0.0, 0.0])
        prices = np.array([100, 100, 100])
        calmar = calculate_calmar_ratio(returns, prices)
        assert calmar == 0.0


# ============================================================
# calculate_volatility
# ============================================================

class TestCalculateVolatility:
    def test_basic(self):
        returns = np.random.normal(0, 0.02, 252)
        vol = calculate_volatility(returns)
        assert vol > 0

    def test_empty(self):
        assert calculate_volatility(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_volatility(np.array([np.nan])) == 0.0

    def test_custom_period(self):
        returns = np.array([0.01, -0.02, 0.005, 0.015])
        v252 = calculate_volatility(returns, period=252)
        v12 = calculate_volatility(returns, period=12)
        assert v252 > v12


# ============================================================
# calculate_beta
# ============================================================

class TestCalculateBeta:
    def test_basic(self):
        returns = np.random.normal(0.001, 0.02, 252)
        market = np.random.normal(0.0005, 0.015, 252)
        beta = calculate_beta(returns, market)
        assert isinstance(beta, float)

    def test_empty(self):
        assert calculate_beta(np.array([]), np.array([])) == 1.0

    def test_different_lengths(self):
        returns = np.array([0.01, 0.02, 0.03, 0.01])
        market = np.array([0.005, 0.015])
        beta = calculate_beta(returns, market)
        assert isinstance(beta, float)

    def test_zero_market_variance(self):
        returns = np.array([0.01, 0.02, 0.03])
        market = np.array([0.01, 0.01, 0.01])
        beta = calculate_beta(returns, market)
        assert beta == 1.0

    def test_nan_filtered(self):
        returns = np.array([0.01, np.nan, 0.02, 0.03])
        market = np.array([0.005, 0.01, np.nan, 0.015])
        beta = calculate_beta(returns, market)
        assert isinstance(beta, float)


# ============================================================
# _align_and_dropna
# ============================================================

class TestAlignAndDropna:
    def test_basic(self):
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([4.0, 5.0, 6.0])
        ra, rb = _align_and_dropna(a, b)
        assert len(ra) == 3 and len(rb) == 3

    def test_different_lengths(self):
        a = np.array([1.0, 2.0, 3.0, 4.0])
        b = np.array([5.0, 6.0])
        ra, rb = _align_and_dropna(a, b)
        assert len(ra) == 2 and len(rb) == 2
        assert list(ra) == [3.0, 4.0]

    def test_nan_removed(self):
        a = np.array([1.0, np.nan, 3.0])
        b = np.array([5.0, 6.0, np.nan])
        ra, rb = _align_and_dropna(a, b)
        assert len(ra) == 1 and len(rb) == 1

    def test_empty(self):
        ra, rb = _align_and_dropna(np.array([]), np.array([]))
        assert len(ra) == 0 and len(rb) == 0


# ============================================================
# calculate_alpha
# ============================================================

class TestCalculateAlpha:
    def test_basic(self):
        returns = np.random.normal(0.001, 0.02, 252)
        market = np.random.normal(0.0005, 0.015, 252)
        alpha = calculate_alpha(returns, market)
        assert isinstance(alpha, float)

    def test_empty(self):
        assert calculate_alpha(np.array([]), np.array([])) == 0.0

    def test_short_arrays(self):
        alpha = calculate_alpha(np.array([0.01]), np.array([0.005]))
        assert alpha == 0.0


# ============================================================
# calculate_correlation
# ============================================================

class TestCalculateCorrelation:
    def test_basic(self):
        matrix = np.random.randn(100, 3)
        corr = calculate_correlation(matrix)
        assert corr.shape == (3, 3)
        assert np.allclose(np.diag(corr), 1.0)

    def test_too_few_rows(self):
        matrix = np.array([[1.0, 2.0]])
        corr = calculate_correlation(matrix)
        assert corr.shape == (2, 2)

    def test_constant_column(self):
        matrix = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])
        corr = calculate_correlation(matrix)
        assert np.allclose(np.diag(corr), 1.0)
        assert not np.any(np.isnan(corr))


# ============================================================
# calculate_tracking_error
# ============================================================

class TestCalculateTrackingError:
    def test_basic(self):
        returns = np.random.normal(0.001, 0.02, 252)
        benchmark = np.random.normal(0.0005, 0.015, 252)
        te = calculate_tracking_error(returns, benchmark)
        assert te > 0

    def test_empty(self):
        assert calculate_tracking_error(np.array([]), np.array([])) == 0.0

    def test_identical(self):
        returns = np.array([0.01, 0.02, 0.015])
        te = calculate_tracking_error(returns, returns)
        assert te == pytest.approx(0.0, abs=1e-10)


# ============================================================
# calculate_information_ratio
# ============================================================

class TestCalculateInformationRatio:
    def test_basic(self):
        returns = np.random.normal(0.001, 0.02, 252)
        benchmark = np.random.normal(0.0005, 0.015, 252)
        ir = calculate_information_ratio(returns, benchmark)
        assert isinstance(ir, float)

    def test_empty(self):
        assert calculate_information_ratio(np.array([]), np.array([])) == 0.0

    def test_zero_tracking_error(self):
        returns = np.array([0.01, 0.02, 0.015])
        ir = calculate_information_ratio(returns, returns)
        assert ir == 0.0


# ============================================================
# calculate_win_rate
# ============================================================

class TestCalculateWinRate:
    def test_basic(self):
        returns = np.array([0.01, -0.02, 0.005, -0.01, 0.03])
        wr = calculate_win_rate(returns)
        assert wr == pytest.approx(0.6)

    def test_empty(self):
        assert calculate_win_rate(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_win_rate(np.array([np.nan, np.nan])) == 0.0

    def test_all_positive(self):
        assert calculate_win_rate(np.array([0.01, 0.02, 0.03])) == 1.0

    def test_all_negative(self):
        assert calculate_win_rate(np.array([-0.01, -0.02])) == 0.0


# ============================================================
# calculate_profit_factor
# ============================================================

class TestCalculateProfitFactor:
    def test_basic(self):
        returns = np.array([0.03, -0.02, 0.01, -0.01, 0.05])
        pf = calculate_profit_factor(returns)
        assert pf > 0

    def test_empty(self):
        assert calculate_profit_factor(np.array([])) == 0.0

    def test_all_nan(self):
        assert calculate_profit_factor(np.array([np.nan])) == 0.0

    def test_all_profit(self):
        returns = np.array([0.01, 0.02, 0.03])
        pf = calculate_profit_factor(returns)
        assert pf == float("inf")

    def test_all_loss(self):
        returns = np.array([-0.01, -0.02, -0.03])
        pf = calculate_profit_factor(returns)
        assert pf == pytest.approx(0.0)


# ============================================================
# calculate_performance_metrics
# ============================================================

class TestCalculatePerformanceMetrics:
    def test_basic_with_all(self):
        returns = np.random.normal(0.001, 0.02, 252)
        prices = 100 * np.exp(np.cumsum(returns))
        benchmark = np.random.normal(0.0005, 0.015, 252)
        metrics = calculate_performance_metrics(returns, prices, benchmark)
        assert "sharpe_ratio" in metrics
        assert "var_95" in metrics
        assert "max_drawdown" in metrics
        assert "beta" in metrics

    def test_without_prices(self):
        returns = np.random.normal(0.001, 0.02, 252)
        metrics = calculate_performance_metrics(returns)
        assert metrics["max_drawdown"] == 0.0
        assert metrics["beta"] == 1.0

    def test_without_benchmark(self):
        returns = np.random.normal(0.001, 0.02, 252)
        prices = 100 * np.exp(np.cumsum(returns))
        metrics = calculate_performance_metrics(returns, prices)
        assert metrics["alpha"] == 0.0
        assert metrics["tracking_error"] == 0.0


# ============================================================
# calculate_portfolio_weights
# ============================================================

class TestCalculatePortfolioWeights:
    def test_basic(self):
        positions = {
            "600519": {"phase1_amount": 100000, "phase2_amount": 50000, "phase3_amount": 0},
            "000001": {"phase1_amount": 200000, "phase2_amount": 0, "phase3_amount": 50000},
        }
        weights = calculate_portfolio_weights(positions)
        assert len(weights) == 2
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_empty(self):
        assert calculate_portfolio_weights({}) == {}

    def test_zero_positions(self):
        positions = {"X": {"phase1_amount": 0, "phase2_amount": 0, "phase3_amount": 0}}
        assert calculate_portfolio_weights(positions) == {}

    def test_negative_filtered(self):
        positions = {
            "X": {"phase1_amount": -100, "phase2_amount": 0, "phase3_amount": 0},
            "Y": {"phase1_amount": 200, "phase2_amount": 0, "phase3_amount": 0},
        }
        weights = calculate_portfolio_weights(positions)
        assert "X" not in weights
        assert "Y" in weights
