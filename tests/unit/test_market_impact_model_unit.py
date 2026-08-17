# -*- coding: utf-8 -*-
"""market_impact_model 单元测试 — Almgren-Chriss + Square-Root 市场冲击模型全分支覆盖.

被测模块: utils/market_impact_model.py
覆盖目标: >=95%
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.market_impact_model import (  # noqa: E402
    ImpactEstimate,
    ImpactParams,
    MarketImpactModel,
    OptimalTrajectory,
    classify_order_urgency,
)


# ============================================================
# 数据结构测试
# ============================================================

class TestImpactParams:
    def test_defaults(self):
        p = ImpactParams()
        assert p.eta == 0.142
        assert p.gamma == 0.314
        assert p.alpha == 0.6
        assert p.sr_coefficient == 0.5
        assert p.volatility_scaling is True
        assert p.daily_volatility == 0.02

    def test_custom(self):
        p = ImpactParams(eta=0.2, gamma=0.5, alpha=1.0, volatility_scaling=False)
        assert p.eta == 0.2
        assert p.volatility_scaling is False


class TestImpactEstimate:
    def test_construction(self):
        est = ImpactEstimate(
            symbol="600519", order_shares=10000, adv=500000,
            participation_rate=0.02, temporary_impact_bps=5.0,
            permanent_impact_bps=3.0, total_impact_bps=8.0,
            price_impact=1.44, decision_price=1800.0,
            expected_exec_price=1801.44, model_used="SQRT+AC",
        )
        assert est.symbol == "600519"
        assert est.model_used == "SQRT+AC"
        assert est.metadata == {}

    def test_with_metadata(self):
        est = ImpactEstimate(
            symbol="000001", order_shares=500, adv=1000000,
            participation_rate=0.0005, temporary_impact_bps=1.0,
            permanent_impact_bps=0.5, total_impact_bps=1.5,
            price_impact=0.015, decision_price=10.0,
            expected_exec_price=10.015, model_used="SQRT+AC",
            metadata={"vol": 0.03},
        )
        assert est.metadata["vol"] == 0.03


class TestOptimalTrajectory:
    def test_construction(self):
        traj = OptimalTrajectory(
            times=[0, 0.5, 1.0], holdings=[100, 50, 0],
            trades=[0, 50, 50], speeds=[0, 100, 100],
            expected_cost=10.0, cost_variance=5.0,
            efficient_frontier_lam=1.0, half_life=0.35,
        )
        assert traj.times == [0, 0.5, 1.0]
        assert traj.holdings[-1] == 0
        assert traj.half_life == 0.35


# ============================================================
# MarketImpactModel.estimate
# ============================================================

class TestEstimate:
    def test_basic_estimate(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="600519", order_shares=10000, adv=500000, decision_price=1800.0)
        assert est.symbol == "600519"
        assert est.order_shares == 10000
        assert est.adv == 500000
        assert est.participation_rate == pytest.approx(0.02)
        assert est.total_impact_bps > 0
        assert est.temporary_impact_bps > 0
        assert est.permanent_impact_bps > 0
        assert est.model_used == "SQRT+AC"

    def test_negative_shares_abs(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="TEST", order_shares=-5000, adv=100000, decision_price=10.0)
        assert est.order_shares == 5000

    def test_adv_none_uses_default(self):
        model = MarketImpactModel(default_adv=200000)
        est = model.estimate(symbol="X", order_shares=1000, adv=None, decision_price=5.0)
        assert est.adv == 200000

    def test_adv_zero_uses_default(self):
        model = MarketImpactModel(default_adv=300000)
        est = model.estimate(symbol="X", order_shares=1000, adv=0, decision_price=5.0)
        assert est.adv == 300000

    def test_adv_negative_uses_default(self):
        model = MarketImpactModel(default_adv=100000)
        est = model.estimate(symbol="X", order_shares=1000, adv=-1, decision_price=5.0)
        assert est.adv == 100000

    def test_volatility_scaling(self):
        model = MarketImpactModel()
        est_low = model.estimate(symbol="X", order_shares=1000, adv=100000, volatility=0.01)
        est_high = model.estimate(symbol="X", order_shares=1000, adv=100000, volatility=0.05)
        assert est_high.total_impact_bps > est_low.total_impact_bps

    def test_volatility_scaling_disabled(self):
        params = ImpactParams(volatility_scaling=False)
        model = MarketImpactModel(params=params)
        est_low = model.estimate(symbol="X", order_shares=1000, adv=100000, volatility=0.01)
        est_high = model.estimate(symbol="X", order_shares=1000, adv=100000, volatility=0.05)
        assert est_low.total_impact_bps == pytest.approx(est_high.total_impact_bps)

    def test_vol_scale_floor(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="X", order_shares=1000, adv=100000, volatility=0.001)
        assert est.metadata["vol_scale"] >= 0.5

    def test_decision_price_impact(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="X", order_shares=1000, adv=100000, decision_price=100.0)
        expected_price = 100.0 * (1 + est.total_impact_bps / 10000.0)
        assert est.expected_exec_price == pytest.approx(expected_price)
        assert est.price_impact == pytest.approx(100.0 * est.total_impact_bps / 10000.0)

    def test_zero_decision_price(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="X", order_shares=1000, adv=100000, decision_price=0.0)
        assert est.price_impact == 0.0
        assert est.expected_exec_price == 0.0

    def test_execution_time_days(self):
        model = MarketImpactModel()
        est = model.estimate(symbol="X", order_shares=10000, adv=100000, execution_time_days=5.0)
        assert est.metadata["execution_time_days"] == 5.0

    def test_custom_params(self):
        params = ImpactParams(eta=0.3, gamma=0.6, alpha=0.8, sr_coefficient=0.8)
        model = MarketImpactModel(params=params)
        est = model.estimate(symbol="X", order_shares=10000, adv=100000, decision_price=50.0)
        assert est.total_impact_bps > 0


# ============================================================
# MarketImpactModel.optimal_trajectory
# ============================================================

class TestOptimalTrajectory:
    def test_basic_trajectory(self):
        model = MarketImpactModel()
        traj = model.optimal_trajectory(total_shares=10000, time_horizon=1.0, n_steps=10)
        assert len(traj.times) == 11
        assert len(traj.holdings) == 11
        assert traj.holdings[0] == pytest.approx(10000, rel=1e-6)
        assert traj.holdings[-1] == pytest.approx(0, abs=1e-6)
        assert traj.expected_cost > 0
        assert traj.cost_variance >= 0

    def test_degenerate_uniform(self):
        params = ImpactParams(eta=0.0)
        model = MarketImpactModel(params=params)
        traj = model.optimal_trajectory(total_shares=1000, n_steps=5)
        for i in range(6):
            expected = 1000 * (1 - i / 5)
            assert traj.holdings[i] == pytest.approx(expected, rel=1e-6)

    def test_zero_risk_aversion(self):
        model = MarketImpactModel()
        traj = model.optimal_trajectory(total_shares=1000, risk_aversion=0.0, n_steps=5)
        for i in range(6):
            expected = 1000 * (1 - i / 5)
            assert traj.holdings[i] == pytest.approx(expected, rel=1e-6)

    def test_trades_sum_to_total(self):
        model = MarketImpactModel()
        traj = model.optimal_trajectory(total_shares=5000, n_steps=10)
        total_traded = sum(traj.trades)
        assert total_traded == pytest.approx(5000, rel=1e-4)

    def test_half_life_in_range(self):
        model = MarketImpactModel()
        traj = model.optimal_trajectory(total_shares=1000, time_horizon=1.0, n_steps=10)
        assert 0 <= traj.half_life <= 1.0

    def test_efficient_frontier_lam(self):
        model = MarketImpactModel()
        traj = model.optimal_trajectory(total_shares=1000, risk_aversion=2.5, n_steps=5)
        assert traj.efficient_frontier_lam == 2.5


# ============================================================
# MarketImpactModel.estimate_basket
# ============================================================

class TestEstimateBasket:
    def test_multiple_orders(self):
        model = MarketImpactModel()
        orders = [
            {"symbol": "600519", "order_shares": 10000, "adv": 500000, "decision_price": 1800},
            {"symbol": "000001", "order_shares": 5000, "adv": 1000000, "decision_price": 15},
        ]
        results = model.estimate_basket(orders)
        assert len(results) == 2
        assert results[0].symbol == "600519"
        assert results[1].symbol == "000001"

    def test_empty_basket(self):
        model = MarketImpactModel()
        results = model.estimate_basket([])
        assert results == []

    def test_missing_fields(self):
        model = MarketImpactModel()
        results = model.estimate_basket([{"symbol": "X"}])
        assert len(results) == 1
        assert results[0].order_shares == 0


# ============================================================
# MarketImpactModel.efficient_frontier
# ============================================================

class TestEfficientFrontier:
    def test_default_lam_range(self):
        model = MarketImpactModel()
        frontier = model.efficient_frontier(total_shares=10000)
        assert len(frontier) == 6
        for lam, cost, std in frontier:
            assert lam > 0
            assert cost >= 0
            assert std >= 0

    def test_custom_lam_range(self):
        model = MarketImpactModel()
        frontier = model.efficient_frontier(total_shares=10000, lam_range=[1.0, 5.0])
        assert len(frontier) == 2
        assert frontier[0][0] == 1.0
        assert frontier[1][0] == 5.0


# ============================================================
# classify_order_urgency
# ============================================================

class TestClassifyOrderUrgency:
    def test_high_urgency_large_signal(self):
        result = classify_order_urgency(order_shares=20000, adv=100000, alpha_signal_strength=0.8)
        assert result == "HIGH"

    def test_high_urgency_volatility(self):
        result = classify_order_urgency(order_shares=20000, adv=100000, market_volatility=0.04)
        assert result == "HIGH"

    def test_low_urgency(self):
        result = classify_order_urgency(order_shares=100, adv=100000)
        assert result == "LOW"

    def test_medium_urgency(self):
        result = classify_order_urgency(order_shares=5000, adv=100000, alpha_signal_strength=0.1)
        assert result == "MEDIUM"

    def test_zero_adv(self):
        result = classify_order_urgency(order_shares=1000, adv=0)
        assert result in ("LOW", "MEDIUM", "HIGH")

    def test_negative_signal(self):
        result = classify_order_urgency(order_shares=20000, adv=100000, alpha_signal_strength=-0.8)
        assert result == "HIGH"