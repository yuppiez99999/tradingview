"""test_transaction_cost_model_unit.py — 交易成本模型单元测试

覆盖要点:
    - MarketCapTier 枚举
    - classify_market_cap_tier (大/中/小/微/None)
    - CostParameters 默认值
    - TransactionCostModel 构造
    - get_slippage_bps / get_impact_coeff
    - estimate_slippage (分层/波动率调整/market_cap 自动分组)
    - estimate_commission (买入/卖出/最低佣金)
    - estimate_impact (正常/零ADV/负notional)
    - estimate_opportunity_cost / estimate_delay_cost
    - estimate_capacity / estimate_strategy_capacity / capacity_usage_pct
    - estimate_total_cost / cost_penalty
"""
from __future__ import annotations

import pytest

from utils.transaction_cost_model import (
    CostParameters,
    MarketCapTier,
    TransactionCostModel,
    classify_market_cap_tier,
)

# ============================================================
# classify_market_cap_tier
# ============================================================


class TestClassifyMarketCapTier:
    @pytest.mark.unit
    def test_none(self):
        assert classify_market_cap_tier(None) == MarketCapTier.MICRO

    @pytest.mark.unit
    def test_large(self):
        assert classify_market_cap_tier(600e8) == MarketCapTier.LARGE  # 600亿

    @pytest.mark.unit
    def test_mid(self):
        assert classify_market_cap_tier(200e8) == MarketCapTier.MID  # 200亿

    @pytest.mark.unit
    def test_small(self):
        assert classify_market_cap_tier(50e8) == MarketCapTier.SMALL  # 50亿

    @pytest.mark.unit
    def test_micro(self):
        assert classify_market_cap_tier(10e8) == MarketCapTier.MICRO  # 10亿

    @pytest.mark.unit
    def test_boundary_500(self):
        assert classify_market_cap_tier(500e8) == MarketCapTier.LARGE

    @pytest.mark.unit
    def test_boundary_100(self):
        assert classify_market_cap_tier(100e8) == MarketCapTier.MID

    @pytest.mark.unit
    def test_boundary_20(self):
        assert classify_market_cap_tier(20e8) == MarketCapTier.SMALL


# ============================================================
# CostParameters
# ============================================================


class TestCostParameters:
    @pytest.mark.unit
    def test_defaults(self):
        p = CostParameters()
        assert p.default_slippage_bps == 10.0
        assert p.commission_rate == 0.00025
        assert p.stamp_tax_rate == 0.0005
        assert p.min_commission == 5.0
        assert p.max_pct_of_adv == 0.05
        assert p.slippage_by_tier[MarketCapTier.LARGE] == 3.0

    @pytest.mark.unit
    def test_tier_dicts_are_independent(self):
        p1 = CostParameters()
        p2 = CostParameters()
        p1.slippage_by_tier[MarketCapTier.LARGE] = 99.0
        assert p2.slippage_by_tier[MarketCapTier.LARGE] == 3.0  # 不受影响


# ============================================================
# TransactionCostModel 构造
# ============================================================


class TestConstruction:
    @pytest.mark.unit
    def test_default(self):
        m = TransactionCostModel()

        assert m.params.commission_rate == 0.00025

    @pytest.mark.unit
    def test_custom_params(self):
        p = CostParameters(commission_rate=0.001)
        m = TransactionCostModel(params=p)
        assert m.params.commission_rate == 0.001


# ============================================================
# 分层查询
# ============================================================


class TestTierLookup:
    @pytest.mark.unit
    def test_slippage_by_tier(self):
        m = TransactionCostModel()
        assert m.get_slippage_bps(MarketCapTier.LARGE) == 3.0
        assert m.get_slippage_bps(MarketCapTier.MID) == 7.0
        assert m.get_slippage_bps(MarketCapTier.SMALL) == 15.0
        assert m.get_slippage_bps(MarketCapTier.MICRO) == 35.0

    @pytest.mark.unit
    def test_impact_coeff_by_tier(self):
        m = TransactionCostModel()
        assert m.get_impact_coeff(MarketCapTier.LARGE) == 0.0006
        assert m.get_impact_coeff(MarketCapTier.MICRO) == 0.0050


# ============================================================
# estimate_slippage
# ============================================================


class TestEstimateSlippage:
    @pytest.mark.unit
    def test_basic(self):
        m = TransactionCostModel()
        # notional=1M, LARGE tier (3bps)
        s = m.estimate_slippage(1_000_000, tier=MarketCapTier.LARGE)
        assert abs(s - 1_000_000 * 3.0 / 10000) < 0.01

    @pytest.mark.unit
    def test_volatility_adjustment(self):
        m = TransactionCostModel()
        base = m.estimate_slippage(1_000_000, volatility=0.02, tier=MarketCapTier.LARGE)
        high_vol = m.estimate_slippage(1_000_000, volatility=0.04, tier=MarketCapTier.LARGE)
        assert high_vol > base

    @pytest.mark.unit
    def test_market_cap_auto_tier(self):
        m = TransactionCostModel()
        s1 = m.estimate_slippage(1_000_000, market_cap=600e8)
        s2 = m.estimate_slippage(1_000_000, tier=MarketCapTier.LARGE)
        assert abs(s1 - s2) < 0.01


# ============================================================
# estimate_commission
# ============================================================


class TestEstimateCommission:
    @pytest.mark.unit
    def test_buy(self):
        m = TransactionCostModel()
        c = m.estimate_commission(100_000, side="BUY")
        # 佣金 = 100000 * 0.00025 = 25, 印花税=0, 过户费=100000*0.00001=1
        assert abs(c - (25 + 1)) < 0.01

    @pytest.mark.unit
    def test_sell(self):
        m = TransactionCostModel()
        c = m.estimate_commission(100_000, side="SELL")
        # 佣金=25, 印花税=100000*0.0005=50, 过户费=1
        assert abs(c - (25 + 50 + 1)) < 0.01

    @pytest.mark.unit
    def test_min_commission(self):
        m = TransactionCostModel()
        c = m.estimate_commission(100, side="BUY")
        # 佣金=100*0.00025=0.025 < min_commission=5
        assert c >= 5.0


# ============================================================
# estimate_impact
# ============================================================


class TestEstimateImpact:
    @pytest.mark.unit
    def test_basic(self):
        m = TransactionCostModel()
        impact = m.estimate_impact(100_000, 1_000_000, tier=MarketCapTier.LARGE)
        assert impact > 0

    @pytest.mark.unit
    def test_zero_adv(self):
        m = TransactionCostModel()
        assert m.estimate_impact(100_000, 0) == 0.0

    @pytest.mark.unit
    def test_negative_notional(self):
        """负 notional 取绝对值"""
        m = TransactionCostModel()
        impact_pos = m.estimate_impact(100_000, 1_000_000, tier=MarketCapTier.LARGE)
        impact_neg = m.estimate_impact(-100_000, 1_000_000, tier=MarketCapTier.LARGE)
        assert abs(impact_pos - impact_neg) < 0.01

    @pytest.mark.unit
    def test_participation_cap(self):
        """notional/ADV 超过 participation_rate 时被截断"""
        m = TransactionCostModel()
        # notional = 100M, ADV = 1M → participation = 100 >> 0.15
        impact = m.estimate_impact(100_000_000, 1_000_000, tier=MarketCapTier.LARGE)
        assert impact > 0  # 不应爆炸


# ============================================================
# 机会成本 / 延迟成本
# ============================================================


class TestOpportunityAndDelayCost:
    @pytest.mark.unit
    def test_opportunity_cost(self):
        m = TransactionCostModel()
        oc = m.estimate_opportunity_cost(1_000_000, days_delayed=5)
        assert abs(oc - 1_000_000 * 0.0001 * 5) < 0.01

    @pytest.mark.unit
    def test_delay_cost_zero(self):
        m = TransactionCostModel()
        assert m.estimate_delay_cost(1_000_000, hours_delayed=0) == 0.0

    @pytest.mark.unit
    def test_delay_cost(self):
        m = TransactionCostModel()
        dc = m.estimate_delay_cost(1_000_000, hours_delayed=2)
        assert dc > 0


# ============================================================
# 容量估算
# ============================================================


class TestCapacity:
    @pytest.mark.unit
    def test_single_capacity(self):
        m = TransactionCostModel()
        cap = m.estimate_capacity(100_000_000)
        assert abs(cap - 100_000_000 * 0.05) < 0.01

    @pytest.mark.unit
    def test_strategy_capacity(self):
        m = TransactionCostModel()
        adv_list = {"600519": 1e9, "000858": 5e8}
        weights = {"600519": 0.6, "000858": 0.4}
        cap = m.estimate_strategy_capacity(adv_list, weights, 5_000_000)
        assert cap > 0
        assert cap < float("inf")

    @pytest.mark.unit
    def test_strategy_capacity_empty(self):
        m = TransactionCostModel()
        cap = m.estimate_strategy_capacity({}, {}, 5_000_000)
        assert cap == float("inf")

    @pytest.mark.unit
    def test_capacity_usage(self):
        m = TransactionCostModel()
        assert m.capacity_usage_pct(500, 1000) == 0.5
        assert m.capacity_usage_pct(500, 0) == 1.0


# ============================================================
# estimate_total_cost / cost_penalty
# ============================================================


class TestTotalCost:
    @pytest.mark.unit
    def test_buy(self):
        m = TransactionCostModel()
        result = m.estimate_total_cost(1_000_000, adv=10_000_000, tier=MarketCapTier.LARGE, side="BUY")
        assert result["notional"] == 1_000_000
        assert result["tier"] == "large"
        assert result["total"] > 0
        assert result["cost_bps"] > 0
        assert result["stamp_tax_included"] is False

    @pytest.mark.unit
    def test_sell(self):
        m = TransactionCostModel()
        result = m.estimate_total_cost(1_000_000, adv=10_000_000, tier=MarketCapTier.LARGE, side="SELL")
        assert result["stamp_tax_included"] is True

    @pytest.mark.unit
    def test_market_cap_auto(self):
        m = TransactionCostModel()
        result = m.estimate_total_cost(1_000_000, market_cap=600e8)
        assert result["tier"] == "large"

    @pytest.mark.unit
    def test_cost_penalty(self):
        m = TransactionCostModel()
        p = m.cost_penalty(1_000_000, adv=10_000_000, tier=MarketCapTier.LARGE)
        assert p > 0
