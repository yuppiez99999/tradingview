#!/usr/bin/env python
"""
test_g7_risk_budget_engine_boost.py — 风险预算引擎覆盖率补强测试

覆盖 P0 risk 链路: utils/risk_budget_engine.py
"""

from __future__ import annotations

import pandas as pd

from utils.risk_budget_engine import (
    RiskBudgetEngine,
    RiskCheckResult,
)


class TestRiskCheckResult:
    def test_default_construction(self) -> None:
        result = RiskCheckResult()
        assert result.allowed is True
        assert result.portfolio_var_95 == 0.0
        assert result.portfolio_var_99 == 0.0
        assert result.single_var == {}
        assert result.budget_usage == 0.0
        assert result.violations == []
        assert result.meta == {}

    def test_construction_with_values(self) -> None:
        result = RiskCheckResult(
            allowed=False,
            portfolio_var_95=0.05,
            portfolio_var_99=0.08,
            single_var={"600000": 0.02},
            budget_usage=0.6,
            violations=["超限"],
            meta={"k": "v"},
        )
        assert result.allowed is False
        assert result.portfolio_var_95 == 0.05
        assert result.portfolio_var_99 == 0.08
        assert result.single_var == {"600000": 0.02}
        assert result.budget_usage == 0.6
        assert result.violations == ["超限"]
        assert result.meta == {"k": "v"}


class TestRiskBudgetEngine:
    def test_construction(self) -> None:
        engine = RiskBudgetEngine()
        assert isinstance(engine, RiskBudgetEngine)

    def test_check_pre_trade_empty_portfolio(self) -> None:
        engine = RiskBudgetEngine()
        result = engine.check_pre_trade(target_portfolio={})
        assert isinstance(result, RiskCheckResult)
        assert result.allowed is False
        assert any("空" in v for v in result.violations)

    def test_check_pre_trade_valid_portfolio(self) -> None:
        engine = RiskBudgetEngine()
        prices = pd.Series([10.0, 10.5, 11.0, 10.8, 10.2])
        result = engine.check_pre_trade(
            target_portfolio={"600000": 0.5, "600001": 0.5},
            current_positions={},
            price_data={"600000": prices, "600001": prices},
        )
        assert isinstance(result, RiskCheckResult)
        assert result.portfolio_var_95 >= 0.0
        assert result.portfolio_var_99 >= 0.0
        assert 0.0 <= result.budget_usage <= 1.0 or result.budget_usage >= 0.0

    def test_check_pre_trade_concentrated_portfolio(self) -> None:
        engine = RiskBudgetEngine()
        prices = pd.Series([10.0, 10.5, 11.0, 10.8, 10.2])
        result = engine.check_pre_trade(
            target_portfolio={"600000": 1.0},
            current_positions={},
            price_data={"600000": prices},
        )
        assert isinstance(result, RiskCheckResult)

    def test_check_pre_trade_with_current_positions(self) -> None:
        engine = RiskBudgetEngine()
        prices = pd.Series([10.0, 10.5, 11.0, 10.8, 10.2])
        result = engine.check_pre_trade(
            target_portfolio={"600000": 0.3, "600001": 0.7},
            current_positions={
                "600000": {"shares": 100, "cost_price": 10.0},
                "600001": {"shares": 200, "cost_price": 10.5},
            },
            price_data={"600000": prices, "600001": prices},
        )
        assert isinstance(result, RiskCheckResult)
        assert result.budget_usage >= 0.0
