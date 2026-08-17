# -*- coding: utf-8 -*-
"""ic_hedge_calculator 单元测试 — IC 期货对冲量计算器全覆盖.

被测模块: utils/ic_hedge_calculator.py
覆盖目标: >=95%
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.ic_hedge_calculator import (  # noqa: E402
    DEFAULT_TARGET_BETA,
    IC_BASIS_REDUCE_PCT,
    IC_BASIS_THRESHOLD,
    IC_MARGIN_RATE,
    IC_MAX_CONTRACTS,
    IC_MULTIPLIER,
    ICHedgeCalculator,
    ICHedgeResult,
)


# ============================================================
# calculate — 正常场景
# ============================================================

class TestCalculateNormal:
    def test_basic_hedge(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
        )
        assert r.feasible is True
        assert r.target_contracts >= 1
        assert r.hedge_notional > 0
        assert r.ic_price == 5500.0

    def test_beta_already_at_target(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_000_000,
            portfolio_beta=0.05,
            target_beta=0.05,
            ic_price=5500.0,
        )
        assert r.target_contracts == 0
        assert r.adjusted_contracts == 0
        assert "已达标" in r.reason

    def test_beta_below_target(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_000_000,
            portfolio_beta=0.02,
            target_beta=0.05,
            ic_price=5500.0,
        )
        assert r.target_contracts == 0

    def test_net_beta_reduced(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
        )
        assert r.net_beta < 0.85


# ============================================================
# calculate — 异常参数
# ============================================================

class TestCalculateInvalid:
    def test_zero_market_value(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(0, 0.85, 0.05, 5500.0)
        assert r.feasible is False
        assert "参数错误" in r.reason

    def test_negative_market_value(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(-100, 0.85, 0.05, 5500.0)
        assert r.feasible is False

    def test_zero_ic_price(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_000_000, 0.85, 0.05, 0)
        assert r.feasible is False


# ============================================================
# calculate — 上限控制
# ============================================================

class TestMaxContracts:
    def test_capped_at_max(self):
        calc = ICHedgeCalculator(max_contracts=2)
        r = calc.calculate(
            long_market_value=10_000_000,
            portfolio_beta=1.5,
            target_beta=0.05,
            ic_price=5000.0,
        )
        assert r.target_contracts <= 2

    def test_default_max_3(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=50_000_000,
            portfolio_beta=2.0,
            target_beta=0.05,
            ic_price=5000.0,
        )
        assert r.target_contracts <= IC_MAX_CONTRACTS


# ============================================================
# calculate — 基差调整
# ============================================================

class TestBasisAdjustment:
    def test_high_basis_triggers_warning(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
            basis=0.02,
        )
        assert r.basis_warning is True
        assert r.adjusted_contracts <= r.target_contracts

    def test_low_basis_no_warning(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
            basis=0.005,
        )
        assert r.basis_warning is False

    def test_no_basis_no_warning(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
        )
        assert r.basis_warning is False


# ============================================================
# calculate — 保证金约束
# ============================================================

class TestMarginConstraint:
    def test_margin_usage_ratio(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
            available_margin=500_000,
        )
        assert r.margin_usage_ratio > 0

    def test_insufficient_margin(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(
            long_market_value=1_400_000,
            portfolio_beta=0.85,
            target_beta=0.05,
            ic_price=5500.0,
            available_margin=100,
        )
        assert r.feasible is False
        assert "保证金不足" in r.reason


# ============================================================
# build_hedge_order
# ============================================================

class TestBuildHedgeOrder:
    def test_order_with_contracts(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_400_000, 0.85, 0.05, 5500.0)
        order = calc.build_hedge_order(r, trade_date=date(2026, 8, 17))
        assert order["action"] == "open_short"
        assert order["symbol"] == "IC"
        assert order["exchange"] == "CFFEX"
        assert order["direction"] == "short"
        assert order["contracts"] == r.adjusted_contracts

    def test_skip_order(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_000_000, 0.05, 0.05, 5500.0)
        order = calc.build_hedge_order(r)
        assert order["action"] == "skip"


# ============================================================
# build_rebalance_order
# ============================================================

class TestBuildRebalanceOrder:
    def test_hold(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_400_000, 0.85, 0.05, 5500.0)
        order = calc.build_rebalance_order(r.adjusted_contracts, r)
        assert order["action"] == "hold"

    def test_add_short(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_400_000, 0.85, 0.05, 5500.0)
        order = calc.build_rebalance_order(0, r)
        assert order["action"] == "add_short"
        assert order["delta_contracts"] > 0

    def test_reduce_short(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_400_000, 0.85, 0.05, 5500.0)
        order = calc.build_rebalance_order(r.adjusted_contracts + 2, r)
        assert order["action"] == "reduce_short"


# ============================================================
# summary
# ============================================================

class TestSummary:
    def test_summary_output(self):
        calc = ICHedgeCalculator()
        r = calc.calculate(1_400_000, 0.85, 0.05, 5500.0)
        s = calc.summary(r)
        assert "IC 期货对冲计算结果" in s
        assert "1,400,000" in s