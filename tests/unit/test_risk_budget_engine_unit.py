"""risk_budget_engine 单元测试 — 事前风险预算引擎全分支覆盖"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.risk_budget_engine import RiskBudgetEngine, RiskCheckResult  # noqa: E402


@pytest.fixture
def engine():
    return RiskBudgetEngine()


@pytest.fixture
def price_series_volatile():
    np.random.seed(42)
    returns = np.random.randn(60) * 0.03
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.Series(prices)


@pytest.fixture
def price_series_stable():
    np.random.seed(7)
    returns = np.random.randn(60) * 0.005
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.Series(prices)


def _make_price_data(symbols, series_factory):
    return {s: series_factory() for s in symbols}


class TestRiskCheckResultToDict:
    def test_default_to_dict(self):
        r = RiskCheckResult()
        d = r.to_dict()
        assert d["allowed"] is True
        assert d["portfolio_var_95"] == 0.0
        assert d["portfolio_var_99"] == 0.0
        assert d["single_var"] == {}
        assert d["budget_usage"] == 0.0
        assert d["violations"] == []
        assert d["meta"] == {}

    def test_rounding(self):
        r = RiskCheckResult(
            allowed=False,
            portfolio_var_95=0.123456789,
            portfolio_var_99=0.987654321,
            single_var={"A": 0.111111, "B": 0.222222},
            budget_usage=0.555555,
            violations=["v1"],
            meta={"k": "v"},
        )
        d = r.to_dict()
        assert d["portfolio_var_95"] == round(0.123456789, 6)
        assert d["portfolio_var_99"] == round(0.987654321, 6)
        assert d["single_var"]["A"] == round(0.111111, 6)
        assert d["budget_usage"] == round(0.555555, 4)
        assert d["allowed"] is False
        assert d["violations"] == ["v1"]
        assert d["meta"] == {"k": "v"}


class TestCheckPreTradeEmpty:
    def test_empty_portfolio_violation(self, engine):
        result = engine.check_pre_trade({})
        assert result.allowed is False
        assert "目标组合为空" in result.violations
        assert len(result.violations) == 1

    def test_empty_portfolio_no_price_data(self, engine):
        result = engine.check_pre_trade({}, None, None)
        assert result.allowed is False
        assert result.portfolio_var_95 == 0.0
        assert result.portfolio_var_99 == 0.0


class TestCheckPreTradeAllPass:
    def test_all_checks_pass_no_price_data(self, engine):
        portfolio = {"A": 0.05, "B": 0.05, "C": 0.05}
        result = engine.check_pre_trade(portfolio)
        assert result.allowed is True
        assert result.violations == []
        assert result.portfolio_var_95 > 0
        assert result.portfolio_var_99 > 0
        assert set(result.single_var.keys()) == {"A", "B", "C"}
        assert result.budget_usage > 0

    def test_all_checks_pass_with_price_data(self, engine, price_series_stable):
        portfolio = {"A": 0.05, "B": 0.05}
        price_data = {"A": price_series_stable, "B": price_series_stable}
        result = engine.check_pre_trade(portfolio, None, price_data)
        assert result.allowed is True
        assert result.violations == []


class TestCheckPreTradeVarViolation:
    def test_portfolio_var_violation(self, price_series_volatile):
        engine = RiskBudgetEngine(max_daily_var_95=0.0001)
        portfolio = {"A": 0.10, "B": 0.10}
        price_data = {"A": price_series_volatile, "B": price_series_volatile}
        result = engine.check_pre_trade(portfolio, None, price_data)
        assert result.allowed is False
        assert any("组合VaR" in v for v in result.violations)

    def test_portfolio_var_pass_threshold(self, price_series_stable):
        engine = RiskBudgetEngine(max_daily_var_95=0.5)
        portfolio = {"A": 0.05, "B": 0.05}
        price_data = {"A": price_series_stable, "B": price_series_stable}
        result = engine.check_pre_trade(portfolio, None, price_data)
        assert result.portfolio_var_95 <= 0.5


class TestCheckPreTradeSingleVarViolation:
    def test_single_var_violation(self, price_series_volatile):
        engine = RiskBudgetEngine(max_single_var_95=0.0001, max_weight=0.5)
        portfolio = {"A": 0.10}
        price_data = {"A": price_series_volatile}
        result = engine.check_pre_trade(portfolio, None, price_data)
        assert result.allowed is False
        assert any("单标的 A" in v and "VaR" in v for v in result.violations)

    def test_single_var_no_price_data_violation(self):
        engine = RiskBudgetEngine(max_single_var_95=0.0001, max_weight=0.5)
        portfolio = {"A": 0.10}
        result = engine.check_pre_trade(portfolio)
        assert result.allowed is False
        assert any("单标的 A" in v for v in result.violations)


class TestCheckPreTradeConcentration:
    def test_concentration_violation(self, engine):
        portfolio = {"A": 0.20, "B": 0.05}
        result = engine.check_pre_trade(portfolio)
        assert result.allowed is False
        assert any("权重" in v and "A" in v for v in result.violations)

    def test_concentration_boundary_equal_weight_passes(self):
        engine = RiskBudgetEngine(max_weight=0.15)
        portfolio = {"A": 0.15, "B": 0.05}
        result = engine.check_pre_trade(portfolio)
        concentration_violations = [v for v in result.violations if "权重" in v]
        assert concentration_violations == []


class TestCheckPreTradeDrawdown:
    def test_drawdown_violation(self, engine):
        portfolio = {"A": 0.05}
        current_positions = {
            "A": {"shares": 1000, "cost_price": 100.0},
        }
        price_data = {"A": pd.Series([50.0, 50.0, 50.0, 50.0, 50.0, 50.0])}
        result = engine.check_pre_trade(portfolio, current_positions, price_data)
        assert any("回撤" in v for v in result.violations)

    def test_drawdown_no_violation(self, engine):
        portfolio = {"A": 0.05}
        current_positions = {
            "A": {"shares": 1000, "cost_price": 100.0},
        }
        price_data = {"A": pd.Series([110.0, 110.0, 110.0, 110.0, 110.0, 110.0])}
        result = engine.check_pre_trade(portfolio, current_positions, price_data)
        assert not any("回撤" in v for v in result.violations)

    def test_drawdown_no_price_uses_cost(self, engine):
        portfolio = {"A": 0.05}
        current_positions = {
            "A": {"shares": 1000, "cost_price": 100.0},
        }
        result = engine.check_pre_trade(portfolio, current_positions, None)
        assert not any("回撤" in v for v in result.violations)


class TestPortfolioVar:
    def test_no_price_data_fallback(self, engine):
        portfolio = {"A": 0.10, "B": 0.10}
        v95, v99 = engine._portfolio_var(portfolio, {})
        assert v95 > 0
        assert v99 > 0
        assert v99 > v95

    def test_empty_portfolio_returns_zero(self, engine):
        v95, v99 = engine._portfolio_var({}, {})
        assert v95 == 0.0
        assert v99 == 0.0

    def test_with_price_data(self, engine, price_series_stable):
        portfolio = {"A": 0.10, "B": 0.10}
        price_data = {"A": price_series_stable, "B": price_series_stable}
        v95, v99 = engine._portfolio_var(portfolio, price_data)
        assert v95 >= 0
        assert v99 >= 0

    def test_short_series_skipped(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": pd.Series([100.0, 101.0, 102.0])}
        v95, v99 = engine._portfolio_var(portfolio, price_data)
        assert v95 > 0
        assert v99 > 0

    def test_non_series_skipped(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]}
        v95, v99 = engine._portfolio_var(portfolio, price_data)
        assert v95 > 0

    def test_min_len_zero_returns_zero(self, engine):
        portfolio = {"A": 0.10}
        series = pd.Series([100.0])
        price_data = {"A": series}
        v95, v99 = engine._portfolio_var(portfolio, price_data)
        assert v95 > 0

    def test_all_nan_series_skipped_to_fallback(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": pd.Series([np.nan] * 6)}
        v95, v99 = engine._portfolio_var(portfolio, price_data)
        assert v95 > 0
        assert v99 > 0


class TestSingleVar:
    def test_no_price_data_fallback(self, engine):
        portfolio = {"A": 0.10, "B": 0.20}
        result = engine._single_var(portfolio, {})
        assert set(result.keys()) == {"A", "B"}
        assert result["A"] > 0
        assert result["B"] > result["A"]

    def test_with_price_data(self, engine, price_series_stable):
        portfolio = {"A": 0.10}
        price_data = {"A": price_series_stable}
        result = engine._single_var(portfolio, price_data)
        assert "A" in result
        assert result["A"] >= 0

    def test_short_series_fallback(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": pd.Series([100.0, 101.0])}
        result = engine._single_var(portfolio, price_data)
        assert result["A"] > 0

    def test_zero_returns_after_pct_change(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])}
        result = engine._single_var(portfolio, price_data)
        assert result["A"] == 0.0

    def test_non_series_fallback(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": [1, 2, 3, 4, 5, 6]}
        result = engine._single_var(portfolio, price_data)
        assert result["A"] > 0

    def test_all_nan_series_returns_zero(self, engine):
        portfolio = {"A": 0.10}
        price_data = {"A": pd.Series([np.nan] * 6)}
        result = engine._single_var(portfolio, price_data)
        assert result["A"] == 0.0


class TestCheckConcentration:
    def test_no_violation(self, engine):
        portfolio = {"A": 0.05, "B": 0.10}
        assert engine._check_concentration(portfolio) == []

    def test_violation(self, engine):
        portfolio = {"A": 0.20}
        violations = engine._check_concentration(portfolio)
        assert len(violations) == 1
        assert "A" in violations[0]

    def test_multiple_violations(self, engine):
        portfolio = {"A": 0.20, "B": 0.25}
        violations = engine._check_concentration(portfolio)
        assert len(violations) == 2


class TestCheckDrawdownBudget:
    def test_empty_positions(self, engine):
        assert engine._check_drawdown_budget({}, {}) == []

    def test_skip_zero_shares(self, engine):
        positions = {"A": {"shares": 0, "cost_price": 100.0}}
        assert engine._check_drawdown_budget(positions, {}) == []

    def test_skip_zero_cost(self, engine):
        positions = {"A": {"shares": 100, "cost_price": 0}}
        assert engine._check_drawdown_budget(positions, {}) == []

    def test_skip_negative_shares(self, engine):
        positions = {"A": {"shares": -100, "cost_price": 100.0}}
        assert engine._check_drawdown_budget(positions, {}) == []

    def test_no_price_uses_cost_no_violation(self, engine):
        positions = {"A": {"shares": 100, "cost_price": 100.0}}
        assert engine._check_drawdown_budget(positions, {}) == []

    def test_violation_with_price_drop(self, engine):
        positions = {"A": {"shares": 1000, "cost_price": 100.0}}
        price_data = {"A": pd.Series([40.0] * 6)}
        violations = engine._check_drawdown_budget(positions, price_data)
        assert len(violations) == 1
        assert "回撤" in violations[0]

    def test_no_violation_with_price_gain(self, engine):
        positions = {"A": {"shares": 1000, "cost_price": 100.0}}
        price_data = {"A": pd.Series([150.0] * 6)}
        assert engine._check_drawdown_budget(positions, price_data) == []

    def test_total_value_zero_returns_empty(self, engine):
        positions = {"A": {"shares": 100, "cost_price": 100.0}}
        price_data = {"A": pd.Series([0.0] * 6)}
        assert engine._check_drawdown_budget(positions, price_data) == []

    def test_current_value_non_positive_skips_drawdown(self, engine):
        positions = {
            "A": {"shares": 100, "cost_price": 100.0},
            "B": {"shares": -200, "cost_price": 100.0},
        }
        price_data = {"A": pd.Series([100.0] * 6)}
        assert engine._check_drawdown_budget(positions, price_data) == []


class TestComputeBudgetUsage:
    def test_normal_usage(self, engine):
        portfolio = {"A": 0.10, "B": 0.10}
        usage = engine._compute_budget_usage(portfolio, {})
        assert usage > 0
        assert usage < 1.0

    def test_zero_capital_returns_zero(self):
        engine = RiskBudgetEngine(total_capital=0)
        portfolio = {"A": 0.10}
        assert engine._compute_budget_usage(portfolio, {}) == 0.0

    def test_negative_capital_returns_zero(self):
        engine = RiskBudgetEngine(total_capital=-100)
        portfolio = {"A": 0.10}
        assert engine._compute_budget_usage(portfolio, {}) == 0.0


class TestEngineInit:
    def test_default_values(self):
        e = RiskBudgetEngine()
        assert e.total_capital == 3_000_000
        assert e.max_daily_var_95 == 0.035
        assert e.max_single_var_95 == 0.012
        assert e.max_drawdown == 0.15
        assert e.confidence_level == 0.95
        assert e.default_volatility == 0.25
        assert e.max_weight == 0.15

    def test_custom_values(self):
        e = RiskBudgetEngine(
            total_capital=1_000_000,
            max_daily_var_95=0.02,
            max_single_var_95=0.01,
            max_drawdown=0.10,
            confidence_level=0.99,
            default_volatility=0.20,
            max_weight=0.10,
        )
        assert e.total_capital == 1_000_000
        assert e.max_daily_var_95 == 0.02
        assert e.max_single_var_95 == 0.01
        assert e.max_drawdown == 0.10
        assert e.confidence_level == 0.99
        assert e.default_volatility == 0.20
        assert e.max_weight == 0.10

    def test_float_coercion(self):
        e = RiskBudgetEngine(total_capital=100)
        assert isinstance(e.total_capital, float)
        assert e.total_capital == 100.0


class TestCheckPreTradeCombined:
    def test_multiple_violations(self, price_series_volatile):
        engine = RiskBudgetEngine(
            max_daily_var_95=0.0001,
            max_single_var_95=0.0001,
            max_weight=0.05,
        )
        portfolio = {"A": 0.20, "B": 0.20}
        price_data = {"A": price_series_volatile, "B": price_series_volatile}
        result = engine.check_pre_trade(portfolio, None, price_data)
        assert result.allowed is False
        assert len(result.violations) >= 2

    def test_to_dict_output(self, engine):
        portfolio = {"A": 0.05, "B": 0.05}
        result = engine.check_pre_trade(portfolio)
        d = result.to_dict()
        assert "allowed" in d
        assert "portfolio_var_95" in d
        assert "single_var" in d
        assert "budget_usage" in d
        assert "violations" in d
