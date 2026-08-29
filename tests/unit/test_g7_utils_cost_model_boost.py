"""G7 boost: utils/cost_model.py 单元测试.

覆盖 CostAssumption dataclass 全部属性/方法、get_cost_model 单例,
包括默认值、自定义值、边界 (rebalance=0 / sell_ratio=0)、breakdown 审计、
net_return 扣除路径. 无外部依赖, 纯计算.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.cost_model import (  # noqa: E402
    DEFAULT_COST_MODEL,
    CostAssumption,
    get_cost_model,
)

# ============================================================
# 1. 默认值与属性计算
# ============================================================


class TestCostAssumptionDefaults:
    def test_default_field_values(self):
        c = CostAssumption()
        assert c.commission_bps == 2.0
        assert c.stamp_duty_bps == 5.0
        assert c.impact_per_side_bps == 5.0
        assert c.option_overlay_bps == 30.0
        assert c.futures_basis_bps == 15.0
        assert c.rebalance_per_year == 12
        assert c.sell_ratio == 0.5

    def test_commission_annual_bps(self):
        # 2.0 * 2 * 12 = 48
        assert CostAssumption().commission_annual_bps == pytest.approx(48.0)

    def test_stamp_annual_bps(self):
        # 5.0 * 12 * 0.5 = 30
        assert CostAssumption().stamp_annual_bps == pytest.approx(30.0)

    def test_impact_annual_bps(self):
        # 5.0 * 2 * 12 = 120
        assert CostAssumption().impact_annual_bps == pytest.approx(120.0)

    def test_annual_total_bps(self):
        # 48 + 30 + 120 + 30 + 15 = 243
        assert CostAssumption().annual_total_bps == pytest.approx(243.0)

    def test_annual_total_cost_decimal(self):
        # 243 bps / 10000 = 0.0243
        assert CostAssumption().annual_total_cost == pytest.approx(0.0243)


# ============================================================
# 2. 自定义值
# ============================================================


class TestCostAssumptionCustom:
    def test_custom_rebalance_doubles_commission(self):
        c = CostAssumption(rebalance_per_year=24)
        # 2.0 * 2 * 24 = 96
        assert c.commission_annual_bps == pytest.approx(96.0)

    def test_custom_sell_ratio_zero(self):
        c = CostAssumption(sell_ratio=0.0)
        assert c.stamp_annual_bps == pytest.approx(0.0)

    def test_custom_sell_ratio_one(self):
        c = CostAssumption(sell_ratio=1.0)
        # 5.0 * 12 * 1.0 = 60
        assert c.stamp_annual_bps == pytest.approx(60.0)

    def test_zero_rebalance_eliminates_per_trade_costs(self):
        c = CostAssumption(rebalance_per_year=0)
        assert c.commission_annual_bps == pytest.approx(0.0)
        assert c.stamp_annual_bps == pytest.approx(0.0)
        assert c.impact_annual_bps == pytest.approx(0.0)
        # 仅剩年化固定项
        assert c.annual_total_bps == pytest.approx(45.0)

    def test_custom_all_fields(self):
        c = CostAssumption(
            commission_bps=1.0,
            stamp_duty_bps=3.0,
            impact_per_side_bps=2.0,
            option_overlay_bps=10.0,
            futures_basis_bps=5.0,
            rebalance_per_year=6,
            sell_ratio=0.4,
        )
        assert c.commission_annual_bps == pytest.approx(12.0)  # 1*2*6
        assert c.stamp_annual_bps == pytest.approx(7.2)  # 3*6*0.4
        assert c.impact_annual_bps == pytest.approx(24.0)  # 2*2*6
        assert c.annual_total_bps == pytest.approx(58.2)  # 12+7.2+24+10+5


# ============================================================
# 3. breakdown 审计
# ============================================================


class TestBreakdown:
    def test_breakdown_keys(self):
        bd = CostAssumption().breakdown()
        assert set(bd.keys()) == {
            "commission",
            "stamp_duty",
            "market_impact",
            "option_overlay",
            "futures_basis",
            "total",
        }

    def test_breakdown_values_sum_to_total(self):
        c = CostAssumption()
        bd = c.breakdown()
        parts = bd["commission"] + bd["stamp_duty"] + bd["market_impact"]
        parts += bd["option_overlay"] + bd["futures_basis"]
        assert parts == pytest.approx(bd["total"])

    def test_breakdown_total_matches_annual_total_cost(self):
        c = CostAssumption()
        bd = c.breakdown()
        assert bd["total"] == pytest.approx(c.annual_total_cost)

    def test_breakdown_default_values(self):
        bd = CostAssumption().breakdown()
        assert bd["commission"] == pytest.approx(0.0048)  # 48/10000
        assert bd["stamp_duty"] == pytest.approx(0.0030)  # 30/10000
        assert bd["market_impact"] == pytest.approx(0.0120)  # 120/10000
        assert bd["option_overlay"] == pytest.approx(0.0030)  # 30/10000
        assert bd["futures_basis"] == pytest.approx(0.0015)  # 15/10000


# ============================================================
# 4. net_return
# ============================================================


class TestNetReturn:
    def test_net_return_deducts_cost(self):
        c = CostAssumption()
        assert c.net_return(0.10) == pytest.approx(0.10 - 0.0243)

    def test_net_return_zero_gross(self):
        c = CostAssumption()
        assert c.net_return(0.0) == pytest.approx(-0.0243)

    def test_net_return_negative_gross(self):
        c = CostAssumption()
        assert c.net_return(-0.05) == pytest.approx(-0.05 - 0.0243)

    def test_net_return_custom_model(self):
        c = CostAssumption(rebalance_per_year=0)
        # annual_total_cost = 45/10000 = 0.0045
        assert c.net_return(0.10) == pytest.approx(0.10 - 0.0045)


# ============================================================
# 5. get_cost_model 单例
# ============================================================


class TestGetCostModel:
    def test_returns_cost_assumption_instance(self):
        assert isinstance(get_cost_model(), CostAssumption)

    def test_returns_default_constant(self):
        assert get_cost_model() is DEFAULT_COST_MODEL

    def test_repeated_calls_return_same_object(self):
        assert get_cost_model() is get_cost_model()

    def test_default_cost_model_is_default_assumption(self):
        # DEFAULT_COST_MODEL 应使用默认参数构造
        default = CostAssumption()
        assert DEFAULT_COST_MODEL.commission_bps == default.commission_bps
        assert DEFAULT_COST_MODEL.rebalance_per_year == default.rebalance_per_year
        assert DEFAULT_COST_MODEL.annual_total_cost == pytest.approx(
            default.annual_total_cost
        )
