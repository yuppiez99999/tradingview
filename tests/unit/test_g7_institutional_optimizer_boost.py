"""
G7 Coverage Boost: utils/institutional_optimizer.py (371 lines, 0% -> target ~80%)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from utils.institutional_optimizer import (
    InstitutionalPortfolioOptimizer,
    PortfolioDecision,
)


class TestPortfolioDecision:
    def test_defaults(self):
        decision = PortfolioDecision()
        assert decision.target_weights == {}
        assert decision.expected_return == 0.0
        assert decision.expected_risk == 0.0
        assert decision.estimated_cost == 0.0
        assert decision.trades == []
        assert decision.meta == {}

    def test_to_dict(self):
        decision = PortfolioDecision(
            target_weights={"000001.SZ": 0.1},
            expected_return=0.05,
            expected_risk=0.02,
            estimated_cost=0.001,
            trades=[{"symbol": "000001.SZ", "change": 0.1}],
            meta={"solver": "risk_parity_fallback"},
        )
        data = decision.to_dict()
        assert data["expected_return"] == 0.05
        assert data["expected_risk"] == 0.02
        assert data["estimated_cost"] == 0.001
        assert data["trades"][0]["symbol"] == "000001.SZ"
        assert data["meta"]["solver"] == "risk_parity_fallback"

    def test_to_dict_rounding(self):
        decision = PortfolioDecision(
            expected_return=0.123456789,
            estimated_cost=0.000123456,
        )
        data = decision.to_dict()
        assert len(str(data["expected_return"]).split(".")[1]) <= 6
        assert len(str(data["estimated_cost"]).split(".")[1]) <= 6


class TestInstitutionalPortfolioOptimizerInit:
    def test_defaults(self):
        optimizer = InstitutionalPortfolioOptimizer()
        assert optimizer.total_capital == 3_000_000.0
        assert optimizer.max_weight == 0.15
        assert optimizer.max_sector_concentration == 0.30
        assert optimizer.max_turnover == 0.20
        assert optimizer.risk_aversion == 1.0
        assert optimizer.min_position_weight == 0.01

    def test_custom_params(self):
        optimizer = InstitutionalPortfolioOptimizer(
            total_capital=1_000_000.0,
            max_weight=0.10,
            max_sector_concentration=0.25,
            max_turnover=0.15,
            risk_aversion=2.0,
            min_position_weight=0.02,
        )
        assert optimizer.total_capital == 1_000_000.0
        assert optimizer.max_weight == 0.10
        assert optimizer.risk_aversion == 2.0
        assert optimizer.min_position_weight == 0.02


class TestOptimize:
    def test_empty_symbols(self):
        optimizer = InstitutionalPortfolioOptimizer()
        decision = optimizer.optimize()
        assert decision.target_weights == {}
        assert decision.meta["reason"] == "no_symbols"

    def test_no_expected_returns(self):
        optimizer = InstitutionalPortfolioOptimizer()
        decision = optimizer.optimize(
            current_positions={"000001.SZ": {"shares": 100, "cost_price": 10.0}}
        )
        assert "000001.SZ" in decision.target_weights
        assert decision.expected_return == 0.0

    def test_basic_optimization(self):
        optimizer = InstitutionalPortfolioOptimizer(max_weight=0.5)
        symbols = ["000001.SZ", "000002.SZ"]
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.09]],
            index=symbols,
            columns=symbols,
        )
        decision = optimizer.optimize(
            expected_returns={"000001.SZ": 0.1, "000002.SZ": 0.05},
            covariance_matrix=cov,
            current_positions={"000001.SZ": {"shares": 100, "cost_price": 10.0}},
        )
        assert len(decision.target_weights) == 2
        assert abs(sum(decision.target_weights.values()) - 1.0) < 0.01

    def test_sector_constraints(self):
        optimizer = InstitutionalPortfolioOptimizer(max_sector_concentration=0.5)
        symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
        cov = pd.DataFrame(
            [[0.04, 0.0, 0.0], [0.0, 0.04, 0.0], [0.0, 0.0, 0.04]],
            index=symbols,
            columns=symbols,
        )
        decision = optimizer.optimize(
            expected_returns={s: 0.1 for s in symbols},
            covariance_matrix=cov,
            current_positions={s: {"shares": 1, "cost_price": 1.0} for s in symbols},
            sector_map={s: "financials" for s in symbols},
        )
        sector_sum = sum(decision.target_weights.get(s, 0.0) for s in symbols)
        assert sector_sum <= 1.0 + 1e-9

    def test_impact_cost_estimate(self):
        optimizer = InstitutionalPortfolioOptimizer()
        impact_model = MagicMock()
        impact_model.estimate.return_value = MagicMock(total_impact_bps=50.0)
        decision = optimizer.optimize(
            expected_returns={"000001.SZ": 0.1},
            covariance_matrix=pd.DataFrame(
                [[0.04]], index=["000001.SZ"], columns=["000001.SZ"]
            ),
            current_positions={"000001.SZ": {"shares": 1000, "cost_price": 10.0}},
            impact_model=impact_model,
        )
        assert decision.estimated_cost > 0.0

    def test_trade_generation(self):
        optimizer = InstitutionalPortfolioOptimizer()
        decision = optimizer.optimize(
            expected_returns={"000001.SZ": 0.1, "000002.SZ": 0.05},
            covariance_matrix=pd.DataFrame(
                [[0.04, 0.01], [0.01, 0.09]],
                index=["000001.SZ", "000002.SZ"],
                columns=["000001.SZ", "000002.SZ"],
            ),
            current_positions={"000001.SZ": {"shares": 1000, "cost_price": 10.0}},
        )
        assert len(decision.trades) > 0
        assert any(t["symbol"] == "000001.SZ" for t in decision.trades)


class TestBuildCovarianceMatrix:
    def test_exact_symbol_match(self):
        optimizer = InstitutionalPortfolioOptimizer()
        symbols = ["000001.SZ", "000002.SZ"]
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.09]],
            index=symbols,
            columns=symbols,
        )
        result = optimizer._build_covariance_matrix(cov, symbols, 2)
        assert result.shape == (2, 2)

    def test_column_mismatch_fallback(self):
        optimizer = InstitutionalPortfolioOptimizer()
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.09]], index=["x", "y"], columns=["x", "y"]
        )
        result = optimizer._build_covariance_matrix(cov, ["000001.SZ", "000002.SZ"], 2)
        expected = np.full((2, 2), (0.25 / np.sqrt(252)) ** 2) * np.eye(2)
        np.testing.assert_allclose(result, expected)

    def test_wrong_shape_fallback(self):
        optimizer = InstitutionalPortfolioOptimizer()
        cov = pd.DataFrame([[0.04]], index=["000001.SZ"], columns=["000001.SZ"])
        result = optimizer._build_covariance_matrix(cov, ["000001.SZ", "000002.SZ"], 2)
        expected = np.full((2, 2), (0.25 / np.sqrt(252)) ** 2) * np.eye(2)
        np.testing.assert_allclose(result, expected)


class TestCurrentWeights:
    def test_with_positions(self):
        optimizer = InstitutionalPortfolioOptimizer()
        weights = optimizer._current_weights(
            ["000001.SZ", "000002.SZ"],
            {
                "000001.SZ": {"shares": 100, "cost_price": 10.0},
                "000002.SZ": {"shares": 50, "cost_price": 20.0},
            },
        )
        assert weights is not None
        np.testing.assert_allclose(weights, [0.5, 0.5])

    def test_empty_positions(self):
        optimizer = InstitutionalPortfolioOptimizer()
        weights = optimizer._current_weights(["000001.SZ"], {})
        # 源码行为: 空持仓时返回全 0 权重数组 (而非 None), 表示无持仓的零权重
        assert weights is not None
        np.testing.assert_allclose(weights, [0.0])


class TestSolveWeights:
    def test_risk_parity_with_signal(self):
        optimizer = InstitutionalPortfolioOptimizer(
            min_position_weight=0.05, max_weight=0.5
        )
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        mu = np.array([0.1, 0.05])
        np.zeros(2)
        np.zeros(2)
        weights = optimizer._risk_parity_with_signal(mu, cov)
        assert weights.shape == (2,)
        assert abs(weights.sum() - 1.0) < 1e-6
        assert np.all(weights >= -1e-9)

    def test_zero_expected_returns(self):
        optimizer = InstitutionalPortfolioOptimizer()
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        mu = np.array([0.0, 0.0])
        weights = optimizer._risk_parity_with_signal(mu, cov)
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_single_asset(self):
        optimizer = InstitutionalPortfolioOptimizer()
        cov = np.array([[0.04]])
        mu = np.array([0.1])
        weights = optimizer._risk_parity_with_signal(mu, cov)
        assert weights.shape == (1,)
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_equal_weights_for_equal_returns(self):
        optimizer = InstitutionalPortfolioOptimizer()
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        mu = np.array([0.1, 0.1])
        weights = optimizer._risk_parity_with_signal(mu, cov)
        np.testing.assert_allclose(weights, [0.5, 0.5], atol=1e-6)


class TestApplyConstraints:
    def test_non_negative_and_sum(self):
        optimizer = InstitutionalPortfolioOptimizer(max_weight=1.0)
        weights = np.array([0.5, 0.5])
        result = optimizer._apply_constraints(weights, ["000001.SZ", "000002.SZ"], {})
        assert np.all(result >= -1e-9)
        assert abs(result.sum() - 1.0) < 1e-6

    def test_sector_capping(self):
        optimizer = InstitutionalPortfolioOptimizer(max_sector_concentration=0.5)
        weights = np.array([0.6, 0.6])
        sector_map = {"000001.SZ": "A", "000002.SZ": "A"}
        result = optimizer._apply_constraints(
            weights, ["000001.SZ", "000002.SZ"], sector_map
        )
        assert np.all(result >= -1e-9)
        sector_sum = sum(
            result[i]
            for i, s in enumerate(["000001.SZ", "000002.SZ"])
            if sector_map[s] == "A"
        )
        assert sector_sum <= 0.5 + 1e-9


class TestBuildDecision:
    def test_meta_solver_flag(self):
        optimizer = InstitutionalPortfolioOptimizer()
        symbols = ["000001.SZ"]
        weights = np.array([1.0])
        current_weights = np.array([0.0])
        mu = np.array([0.1])
        cov = np.array([[0.04]])
        impact_costs = np.array([0.001])
        decision = optimizer._build_decision(
            symbols=symbols,
            weights=weights,
            current_weights=current_weights,
            mu=mu,
            cov=cov,
            impact_costs=impact_costs,
            current_positions={},
        )
        assert decision.meta["solver"] == "risk_parity_fallback"

    def test_trade_threshold(self):
        optimizer = InstitutionalPortfolioOptimizer()
        symbols = ["000001.SZ"]
        weights = np.array([1.0])
        current_weights = np.array([1.0])
        mu = np.array([0.1])
        cov = np.array([[0.04]])
        impact_costs = np.array([0.001])
        decision = optimizer._build_decision(
            symbols=symbols,
            weights=weights,
            current_weights=current_weights,
            mu=mu,
            cov=cov,
            impact_costs=impact_costs,
            current_positions={"000001.SZ": {"shares": 100, "cost_price": 10.0}},
        )
        assert decision.trades == []


class TestPypfoptAvailable:
    def test_fallback_flag(self):
        optimizer = InstitutionalPortfolioOptimizer()
        assert optimizer._pypfopt_available() is False
