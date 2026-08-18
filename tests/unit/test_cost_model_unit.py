"""test_cost_model_unit.py — 统一成本模型单元测试

覆盖要点:
    - CostAssumption 默认值
    - 各 annual property (commission/stamp/impact/total)
    - breakdown() 各组成部分
    - net_return() 净收益
    - DEFAULT_COST_MODEL / get_cost_model()
"""
from __future__ import annotations

import pytest

from utils.cost_model import DEFAULT_COST_MODEL, CostAssumption, get_cost_model


# ============================================================
# CostAssumption 默认值
# ============================================================


class TestDefaults:
    @pytest.mark.unit
    def test_defaults(self):
        c = CostAssumption()
        assert c.commission_bps == 2.0
        assert c.stamp_duty_bps == 5.0
        assert c.impact_per_side_bps == 5.0
        assert c.option_overlay_bps == 30.0
        assert c.futures_basis_bps == 15.0
        assert c.rebalance_per_year == 12
        assert c.sell_ratio == 0.5


# ============================================================
# annual property
# ============================================================


class TestAnnualProperties:
    @pytest.mark.unit
    def test_commission_annual(self):
        c = CostAssumption()
        # 2 bps * 2 (双边) * 12 (月度) = 48 bps
        assert c.commission_annual_bps == 48.0

    @pytest.mark.unit
    def test_stamp_annual(self):
        c = CostAssumption()
        # 5 bps * 12 * 0.5 = 30 bps
        assert c.stamp_annual_bps == 30.0

    @pytest.mark.unit
    def test_impact_annual(self):
        c = CostAssumption()
        # 5 bps * 2 * 12 = 120 bps
        assert c.impact_annual_bps == 120.0

    @pytest.mark.unit
    def test_annual_total_bps(self):
        c = CostAssumption()
        # 48 + 30 + 120 + 30 + 15 = 243 bps
        assert c.annual_total_bps == 243.0

    @pytest.mark.unit
    def test_annual_total_cost(self):
        c = CostAssumption()
        # 243 bps / 10000 = 0.0243
        assert c.annual_total_cost == pytest.approx(0.0243)

    @pytest.mark.unit
    def test_custom_rebalance(self):
        c = CostAssumption(rebalance_per_year=4)
        # commission: 2*2*4=16, stamp: 5*4*0.5=10, impact: 5*2*4=40
        # total: 16+10+40+30+15 = 111
        assert c.annual_total_bps == 111.0


# ============================================================
# breakdown
# ============================================================


class TestBreakdown:
    @pytest.mark.unit
    def test_breakdown_keys(self):
        c = CostAssumption()
        bd = c.breakdown()
        assert "commission" in bd
        assert "stamp_duty" in bd
        assert "market_impact" in bd
        assert "option_overlay" in bd
        assert "futures_basis" in bd
        assert "total" in bd

    @pytest.mark.unit
    def test_breakdown_values(self):
        c = CostAssumption()
        bd = c.breakdown()
        assert bd["commission"] == pytest.approx(0.0048)  # 48/10000
        assert bd["stamp_duty"] == pytest.approx(0.003)   # 30/10000
        assert bd["market_impact"] == pytest.approx(0.012) # 120/10000
        assert bd["option_overlay"] == pytest.approx(0.003) # 30/10000
        assert bd["futures_basis"] == pytest.approx(0.0015) # 15/10000
        assert bd["total"] == pytest.approx(0.0243)

    @pytest.mark.unit
    def test_breakdown_sums_to_total(self):
        c = CostAssumption()
        bd = c.breakdown()
        parts = bd["commission"] + bd["stamp_duty"] + bd["market_impact"] + bd["option_overlay"] + bd["futures_basis"]
        assert parts == pytest.approx(bd["total"])


# ============================================================
# net_return
# ============================================================


class TestNetReturn:
    @pytest.mark.unit
    def test_net_return(self):
        c = CostAssumption()
        # gross 0.10 - cost 0.0243 = 0.0757
        assert c.net_return(0.10) == pytest.approx(0.0757)

    @pytest.mark.unit
    def test_net_return_negative(self):
        c = CostAssumption()
        # gross -0.05 - cost 0.0243 = -0.0743
        assert c.net_return(-0.05) == pytest.approx(-0.0743)

    @pytest.mark.unit
    def test_net_return_zero(self):
        c = CostAssumption()
        assert c.net_return(0.0) == pytest.approx(-0.0243)


# ============================================================
# DEFAULT_COST_MODEL / get_cost_model
# ============================================================


class TestDefaultModel:
    @pytest.mark.unit
    def test_default_is_cost_assumption(self):
        assert isinstance(DEFAULT_COST_MODEL, CostAssumption)

    @pytest.mark.unit
    def test_get_cost_model_returns_default(self):
        assert get_cost_model() is DEFAULT_COST_MODEL

    @pytest.mark.unit
    def test_default_annual_cost(self):
        assert DEFAULT_COST_MODEL.annual_total_cost == pytest.approx(0.0243)