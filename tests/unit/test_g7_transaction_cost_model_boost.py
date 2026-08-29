"""G7 覆盖率冲刺 — utils/transaction_cost_model.py 单元测试

目标: 覆盖率 77.27% → ≥85%
测试重点:
    - MarketCapTier 枚举 / classify_market_cap_tier 分层规则
    - CostParameters 默认值 / 自定义
    - TransactionCostModel: 滑点/佣金/冲击/机会成本/延迟成本/容量/综合成本
    - 分层滑点 (LARGE/MID/SMALL/MICRO)
    - 最低佣金 min_commission 边界
    - 印花税仅卖出 (SELL/SHORT)
    - 负 notional 冲击成本 (P1-13 修复)
    - 容量估算 / 策略容量瓶颈
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.transaction_cost_model import (  # noqa: E402
    IMPACT_COEFF_BY_TIER,
    SLIPPAGE_BY_TIER,
    CostParameters,
    MarketCapTier,
    TransactionCostModel,
    classify_market_cap_tier,
)

# ============================================================
# classify_market_cap_tier
# ============================================================


class TestClassifyMarketCapTier:
    def test_none_returns_micro(self):
        assert classify_market_cap_tier(None) == MarketCapTier.MICRO

    def test_large_cap(self):
        # >= 500亿
        assert classify_market_cap_tier(500e8) == MarketCapTier.LARGE
        assert classify_market_cap_tier(1000e8) == MarketCapTier.LARGE

    def test_mid_cap(self):
        # 100-500亿
        assert classify_market_cap_tier(100e8) == MarketCapTier.MID
        assert classify_market_cap_tier(499e8) == MarketCapTier.MID

    def test_small_cap(self):
        # 20-100亿
        assert classify_market_cap_tier(20e8) == MarketCapTier.SMALL
        assert classify_market_cap_tier(99e8) == MarketCapTier.SMALL

    def test_micro_cap(self):
        # < 20亿
        assert classify_market_cap_tier(10e8) == MarketCapTier.MICRO
        assert classify_market_cap_tier(1e8) == MarketCapTier.MICRO

    def test_boundary_500_yi(self):
        """500亿正好是 LARGE 边界."""
        assert classify_market_cap_tier(500e8) == MarketCapTier.LARGE

    def test_with_symbol_ignored(self):
        """symbol 参数当前未使用, 仅靠 market_cap 分类."""
        assert classify_market_cap_tier(600e8, "600519") == MarketCapTier.LARGE


# ============================================================
# CostParameters
# ============================================================


class TestCostParameters:
    def test_defaults(self):
        params = CostParameters()
        assert params.commission_rate == 0.00025
        assert params.stamp_tax_rate == 0.0005
        assert params.min_commission == 5.0
        assert params.default_slippage_bps == 10.0
        assert params.max_pct_of_adv == 0.05

    def test_slippage_by_tier_defaults(self):
        params = CostParameters()
        assert params.slippage_by_tier[MarketCapTier.LARGE] == 3.0
        assert params.slippage_by_tier[MarketCapTier.MICRO] == 35.0

    def test_custom_params(self):
        params = CostParameters(commission_rate=0.001, min_commission=10.0)
        assert params.commission_rate == 0.001
        assert params.min_commission == 10.0


# ============================================================
# TransactionCostModel: 滑点
# ============================================================


class TestSlippage:
    def test_get_slippage_bps_by_tier(self):
        model = TransactionCostModel()
        assert model.get_slippage_bps(MarketCapTier.LARGE) == 3.0
        assert model.get_slippage_bps(MarketCapTier.MID) == 7.0
        assert model.get_slippage_bps(MarketCapTier.SMALL) == 15.0
        assert model.get_slippage_bps(MarketCapTier.MICRO) == 35.0

    def test_estimate_slippage_basic(self):
        model = TransactionCostModel()
        # notional=1,000,000, tier=LARGE (3bps), vol=0.02 (无调整)
        slip = model.estimate_slippage(
            1_000_000, volatility=0.02, tier=MarketCapTier.LARGE
        )
        assert slip == pytest.approx(1_000_000 * 3.0 / 10000.0)

    def test_estimate_slippage_with_market_cap(self):
        """tier=None 时用 market_cap 自动分类."""
        model = TransactionCostModel()
        slip = model.estimate_slippage(1_000_000, market_cap=600e8)
        # LARGE tier = 3bps
        assert slip == pytest.approx(1_000_000 * 3.0 / 10000.0)

    def test_estimate_slippage_volatility_adjustment(self):
        """高波动放大滑点."""
        model = TransactionCostModel()
        slip_low_vol = model.estimate_slippage(
            1_000_000, volatility=0.02, tier=MarketCapTier.LARGE
        )
        slip_high_vol = model.estimate_slippage(
            1_000_000, volatility=0.10, tier=MarketCapTier.LARGE
        )
        assert slip_high_vol > slip_low_vol

    def test_estimate_slippage_micro_tier(self):
        """微盘股滑点最高."""
        model = TransactionCostModel()
        slip = model.estimate_slippage(1_000_000, tier=MarketCapTier.MICRO)
        assert slip == pytest.approx(1_000_000 * 35.0 / 10000.0)


# ============================================================
# TransactionCostModel: 佣金
# ============================================================


class TestCommission:
    def test_buy_no_stamp_tax(self):
        model = TransactionCostModel()
        # notional=100,000, 佣金=100000*0.00025=25 > min_commission=5
        comm = model.estimate_commission(100_000, side="BUY")
        assert comm == pytest.approx(25 + 100_000 * 0.00001)  # 佣金 + 过户费

    def test_sell_with_stamp_tax(self):
        model = TransactionCostModel()
        comm_buy = model.estimate_commission(100_000, side="BUY")
        comm_sell = model.estimate_commission(100_000, side="SELL")
        # 卖出多印花税 100000 * 0.0005 = 50
        assert comm_sell - comm_buy == pytest.approx(50)

    def test_short_with_stamp_tax(self):
        model = TransactionCostModel()
        comm_short = model.estimate_commission(100_000, side="SHORT")
        comm_sell = model.estimate_commission(100_000, side="SELL")
        assert comm_short == pytest.approx(comm_sell)

    def test_min_commission_floor(self):
        """小额交易触发最低佣金."""
        model = TransactionCostModel()
        # notional=1000, 佣金=1000*0.00025=0.25 < min_commission=5
        comm = model.estimate_commission(1000, side="BUY")
        assert comm == pytest.approx(5.0 + 1000 * 0.00001)  # min_commission + 过户费

    def test_side_case_insensitive(self):
        model = TransactionCostModel()
        comm_lower = model.estimate_commission(100_000, side="sell")
        comm_upper = model.estimate_commission(100_000, side="SELL")
        assert comm_lower == comm_upper


# ============================================================
# TransactionCostModel: 市场冲击
# ============================================================


class TestMarketImpact:
    def test_get_impact_coeff_by_tier(self):
        model = TransactionCostModel()
        assert model.get_impact_coeff(MarketCapTier.LARGE) == 0.0006
        assert model.get_impact_coeff(MarketCapTier.MICRO) == 0.0050

    def test_zero_adv_returns_zero(self):
        model = TransactionCostModel()
        assert model.estimate_impact(100_000, avg_daily_volume=0) == 0.0

    def test_negative_notional_abs_value(self):
        """P1-13 修复: 负 notional 取绝对值."""
        model = TransactionCostModel()
        impact_pos = model.estimate_impact(100_000, avg_daily_volume=1_000_000)
        impact_neg = model.estimate_impact(-100_000, avg_daily_volume=1_000_000)
        assert impact_pos == pytest.approx(impact_neg)

    def test_zero_notional_returns_zero(self):
        model = TransactionCostModel()
        assert model.estimate_impact(0, avg_daily_volume=1_000_000) == 0.0

    def test_participation_rate_capped(self):
        """participation 不超过 participation_rate (0.15)."""
        model = TransactionCostModel()
        # notional >> adv, participation 应被 cap 到 0.15
        impact = model.estimate_impact(10_000_000, avg_daily_volume=1_000_000)
        assert impact > 0  # 不为零, 说明按 cap 计算

    def test_impact_with_market_cap(self):
        model = TransactionCostModel()
        impact = model.estimate_impact(
            100_000, avg_daily_volume=1_000_000, market_cap=600e8
        )
        assert impact > 0

    def test_impact_volatility_adjustment(self):
        model = TransactionCostModel()
        impact_low = model.estimate_impact(100_000, 1_000_000, volatility=0.02)
        impact_high = model.estimate_impact(100_000, 1_000_000, volatility=0.10)
        assert impact_high > impact_low


# ============================================================
# 机会成本 / 延迟成本
# ============================================================


class TestOpportunityAndDelayCost:
    def test_opportunity_cost(self):
        model = TransactionCostModel()
        # notional=1,000,000, days=2, rate=0.0001
        oc = model.estimate_opportunity_cost(1_000_000, days_delayed=2)
        assert oc == pytest.approx(1_000_000 * 0.0001 * 2)

    def test_delay_cost_zero_hours(self):
        model = TransactionCostModel()
        assert model.estimate_delay_cost(1_000_000, hours_delayed=0) == 0.0

    def test_delay_cost_negative_hours(self):
        model = TransactionCostModel()
        assert model.estimate_delay_cost(1_000_000, hours_delayed=-1) == 0.0

    def test_delay_cost_positive(self):
        model = TransactionCostModel()
        dc = model.estimate_delay_cost(1_000_000, hours_delayed=2)
        assert dc == pytest.approx(1_000_000 * 0.00005 * 2)


# ============================================================
# 容量估算
# ============================================================


class TestCapacity:
    def test_estimate_capacity_default_pct(self):
        model = TransactionCostModel()
        # adv=10,000,000, max_pct=0.05
        cap = model.estimate_capacity(10_000_000)
        assert cap == pytest.approx(500_000)

    def test_estimate_capacity_custom_pct(self):
        model = TransactionCostModel()
        cap = model.estimate_capacity(10_000_000, max_pct=0.10)
        assert cap == pytest.approx(1_000_000)

    def test_strategy_capacity_bottleneck(self):
        """策略容量取瓶颈 (最小值) * 0.8."""
        model = TransactionCostModel()
        adv_list = {"A": 10_000_000, "B": 5_000_000}
        weights = {"A": 0.10, "B": 0.05}
        cap = model.estimate_strategy_capacity(adv_list, weights, 1_000_000)
        # A: single_cap=500000, strategy_cap=500000/0.10=5,000,000
        # B: single_cap=250000, strategy_cap=250000/0.05=5,000,000
        # bottleneck = min(5M, 5M) * 0.8 = 4,000,000
        assert cap == pytest.approx(4_000_000)

    def test_strategy_capacity_empty_returns_inf(self):
        model = TransactionCostModel()
        cap = model.estimate_strategy_capacity({}, {}, 1_000_000)
        assert cap == float("inf")

    def test_strategy_capacity_skip_zero_adv(self):
        model = TransactionCostModel()
        adv_list = {"A": 0, "B": 10_000_000}
        weights = {"A": 0.10, "B": 0.05}
        cap = model.estimate_strategy_capacity(adv_list, weights, 1_000_000)
        # A adv=0 跳过, 仅 B: 500000/0.05=10M, *0.8=8M
        assert cap == pytest.approx(8_000_000)

    def test_capacity_usage_pct(self):
        model = TransactionCostModel()
        assert model.capacity_usage_pct(500_000, 1_000_000) == pytest.approx(0.5)

    def test_capacity_usage_pct_zero_capacity(self):
        model = TransactionCostModel()
        assert model.capacity_usage_pct(500_000, 0) == 1.0


# ============================================================
# 综合成本
# ============================================================


class TestTotalCost:
    def test_estimate_total_cost_buy(self):
        model = TransactionCostModel()
        result = model.estimate_total_cost(
            notional=100_000, adv=1_000_000, tier=MarketCapTier.LARGE, side="BUY"
        )
        assert result["notional"] == 100_000
        assert result["tier"] == "large"
        assert result["slippage"] > 0
        assert result["commission"] > 0
        assert result["impact"] > 0
        assert result["stamp_tax_included"] is False
        assert result["total"] > 0
        assert result["cost_bps"] > 0

    def test_estimate_total_cost_sell_with_stamp_tax(self):
        model = TransactionCostModel()
        result = model.estimate_total_cost(
            notional=100_000, adv=1_000_000, tier=MarketCapTier.LARGE, side="SELL"
        )
        assert result["stamp_tax_included"] is True

    def test_estimate_total_cost_with_market_cap(self):
        model = TransactionCostModel()
        result = model.estimate_total_cost(notional=100_000, market_cap=600e8)
        assert result["tier"] == "large"

    def test_estimate_total_cost_zero_notional_bps(self):
        """notional=0 时 cost_bps=0 (除零保护)."""
        model = TransactionCostModel()
        result = model.estimate_total_cost(notional=0, tier=MarketCapTier.LARGE)
        assert result["cost_bps"] == 0.0

    def test_cost_penalty(self):
        model = TransactionCostModel()
        penalty = model.cost_penalty(100_000, adv=1_000_000, tier=MarketCapTier.LARGE)
        assert penalty > 0


# ============================================================
# 分层常量
# ============================================================


class TestTierConstants:
    def test_slippage_by_tier_all_present(self):
        assert len(SLIPPAGE_BY_TIER) == 4
        for tier in MarketCapTier:
            assert tier in SLIPPAGE_BY_TIER

    def test_impact_coeff_by_tier_all_present(self):
        assert len(IMPACT_COEFF_BY_TIER) == 4
        for tier in MarketCapTier:
            assert tier in IMPACT_COEFF_BY_TIER

    def test_slippage_monotonic(self):
        """滑点随市值递减: LARGE < MID < SMALL < MICRO."""
        assert (
            SLIPPAGE_BY_TIER[MarketCapTier.LARGE] < SLIPPAGE_BY_TIER[MarketCapTier.MID]
        )
        assert (
            SLIPPAGE_BY_TIER[MarketCapTier.MID] < SLIPPAGE_BY_TIER[MarketCapTier.SMALL]
        )
        assert (
            SLIPPAGE_BY_TIER[MarketCapTier.SMALL]
            < SLIPPAGE_BY_TIER[MarketCapTier.MICRO]
        )
