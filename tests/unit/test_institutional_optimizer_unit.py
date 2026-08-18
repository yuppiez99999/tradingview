# -*- coding: utf-8 -*-
"""institutional_optimizer 单元测试 — 机构级组合优化器"""
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from utils.institutional_optimizer import (
    PortfolioDecision,
    InstitutionalPortfolioOptimizer,
)


class TestPortfolioDecision:
    def test_defaults(self):
        d = PortfolioDecision()
        assert d.target_weights == {}
        assert d.expected_return == 0.0
        assert d.expected_risk == 0.0
        assert d.estimated_cost == 0.0
        assert d.trades == []
        assert d.meta == {}

    def test_to_dict(self):
        d = PortfolioDecision(
            target_weights={"A": 0.5},
            expected_return=0.123456789,
            expected_risk=0.05,
            estimated_cost=0.001,
            trades=[{"symbol": "A"}],
            meta={"turnover": 0.1},
        )
        result = d.to_dict()
        assert result["target_weights"] == {"A": 0.5}
        assert result["expected_return"] == round(0.123456789, 6)
        assert result["trades"] == [{"symbol": "A"}]
        assert result["meta"] == {"turnover": 0.1}


class TestOptimizerInit:
    def test_defaults(self):
        opt = InstitutionalPortfolioOptimizer()
        assert opt.total_capital == 3_000_000.0
        assert opt.max_weight == 0.15
        assert opt.max_sector_concentration == 0.30
        assert opt.max_turnover == 0.20
        assert opt.risk_aversion == 1.0
        assert opt.min_position_weight == 0.01

    def test_custom(self):
        opt = InstitutionalPortfolioOptimizer(
            total_capital=1_000_000, max_weight=0.25,
            max_sector_concentration=0.40, max_turnover=0.30,
            risk_aversion=2.0, min_position_weight=0.02,
        )
        assert opt.total_capital == 1_000_000.0
        assert opt.max_weight == 0.25
        assert opt.risk_aversion == 2.0


class TestOptimize:
    def test_no_symbols(self):
        opt = InstitutionalPortfolioOptimizer()
        decision = opt.optimize()
        assert decision.meta.get("reason") == "no_symbols"

    def test_with_returns_only(self):
        opt = InstitutionalPortfolioOptimizer()
        decision = opt.optimize(
            expected_returns={"A": 0.10, "B": 0.05, "C": 0.08},
        )
        assert len(decision.target_weights) == 3
        assert "A" in decision.target_weights

    def test_with_positions(self):
        opt = InstitutionalPortfolioOptimizer()
        decision = opt.optimize(
            expected_returns={"A": 0.10, "B": 0.05},
            current_positions={"A": {"shares": 100, "cost_price": 50}, "B": {"shares": 200, "cost_price": 30}},
        )
        assert len(decision.target_weights) == 2

    def test_with_covariance(self):
        opt = InstitutionalPortfolioOptimizer()
        cov = pd.DataFrame(
            {"A": [0.04, 0.01], "B": [0.01, 0.09]},
            index=["A", "B"],
        )
        decision = opt.optimize(
            expected_returns={"A": 0.10, "B": 0.05},
            covariance_matrix=cov,
        )
        assert len(decision.target_weights) == 2

    def test_decision_has_trades(self):
        opt = InstitutionalPortfolioOptimizer()
        decision = opt.optimize(
            expected_returns={"A": 0.10, "B": 0.05},
            current_positions={"A": {"shares": 100, "cost_price": 50}, "B": {"shares": 200, "cost_price": 30}},
        )
        assert isinstance(decision.trades, list)

    def test_decision_to_dict(self):
        opt = InstitutionalPortfolioOptimizer()
        decision = opt.optimize(expected_returns={"A": 0.10})
        d = decision.to_dict()
        assert "target_weights" in d
        assert "expected_return" in d


class TestBuildCovarianceMatrix:
    def test_from_dataframe(self):
        opt = InstitutionalPortfolioOptimizer()
        cov = pd.DataFrame(
            {"A": [0.04, 0.01], "B": [0.01, 0.09]},
            index=["A", "B"],
        )
        result = opt._build_covariance_matrix(cov, ["A", "B"], 2)
        assert result.shape == (2, 2)
        assert result[0, 0] == pytest.approx(0.04)

    def test_default(self):
        opt = InstitutionalPortfolioOptimizer()
        result = opt._build_covariance_matrix(None, ["A", "B"], 2)
        assert result.shape == (2, 2)

    def test_mismatched_columns(self):
        opt = InstitutionalPortfolioOptimizer()
        cov = pd.DataFrame(
            {"X": [0.04, 0.01], "Y": [0.01, 0.09]},
            index=["X", "Y"],
        )
        result = opt._build_covariance_matrix(cov, ["A", "B"], 2)
        assert result.shape == (2, 2)


class TestCurrentWeights:
    def test_empty(self):
        opt = InstitutionalPortfolioOptimizer()
        result = opt._current_weights(["A", "B"], {})
        assert np.all(result == 0.0)

    def test_with_positions(self):
        opt = InstitutionalPortfolioOptimizer()
        positions = {"A": {"shares": 100, "cost_price": 50}, "B": {"shares": 200, "cost_price": 25}}
        result = opt._current_weights(["A", "B"], positions)
        total = result.sum()
        assert total == pytest.approx(1.0)
        assert result[0] == pytest.approx(5000 / 10000)


class TestRiskParityWithSignal:
    def test_empty(self):
        opt = InstitutionalPortfolioOptimizer()
        result = opt._risk_parity_with_signal(np.array([]), np.array([]).reshape(0, 0))
        assert result.size == 0

    def test_equal_vol(self):
        opt = InstitutionalPortfolioOptimizer()
        cov = np.eye(3) * 0.04
        mu = np.array([0.1, 0.1, 0.1])
        result = opt._risk_parity_with_signal(mu, cov)
        assert result.sum() == pytest.approx(1.0, abs=1e-6)

    def test_negative_mu(self):
        opt = InstitutionalPortfolioOptimizer()
        cov = np.eye(2) * 0.04
        mu = np.array([-0.1, -0.1])
        result = opt._risk_parity_with_signal(mu, cov)
        assert result.sum() == pytest.approx(1.0, abs=1e-6)


class TestApplyConstraints:
    def test_empty(self):
        opt = InstitutionalPortfolioOptimizer()
        result = opt._apply_constraints(np.array([]), [], {})
        assert result.size == 0

    def test_max_weight(self):
        opt = InstitutionalPortfolioOptimizer(max_weight=0.15)
        weights = np.array([0.5, 0.5])
        result = opt._apply_constraints(weights, ["A", "B"], {})
        assert all(w <= 0.15 + 1e-10 for w in result)

    def test_negative_clipped(self):
        opt = InstitutionalPortfolioOptimizer()
        weights = np.array([-0.1, 0.5])
        result = opt._apply_constraints(weights, ["A", "B"], {})
        assert all(w >= 0.0 for w in result)

    def test_sector_concentration(self):
        opt = InstitutionalPortfolioOptimizer(max_sector_concentration=0.30)
        weights = np.array([0.4, 0.4, 0.2])
        sector_map = {"A": "tech", "B": "tech", "C": "bank"}
        result = opt._apply_constraints(weights, ["A", "B", "C"], sector_map)
        assert all(w >= 0.0 for w in result)


class TestPypfoptAvailable:
    def test_returns_false(self):
        opt = InstitutionalPortfolioOptimizer()
        assert opt._pypfopt_available() is False