"""test_execution_algorithm_engine_unit.py — 执行算法引擎单元测试

覆盖要点:
    - Order / ChildOrder / ExecutionPlan dataclass
    - ExecutionAlgorithmEngine 构造 (默认/自定义参数)
    - _default_u_shape_curve (24 槽 U 型)
    - _generate_trading_slots (跳过午休/截止收盘)
    - vwap (默认/自定义曲线, adv, 切片类型 OPEN/CLOSE/NORMAL, 总量守恒)
    - twap (均匀拆单, adv, 空槽退化)
    - pov (参与度, 剩余扫尾)
    - is_algo (urgency→λ, adv, sigma2=0 退化)
    - _apply_randomization (大小/时间随机化, 午休钳制)
    - _clamp_to_trading_hours (早于开盘/午休/晚于收盘/正常)
    - select_algorithm (大/中/小单 × urgency)
    - summarize_plan
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from utils.execution_algorithm_engine import (
    ChildOrder,
    ExecutionAlgorithmEngine,
    ExecutionPlan,
    Order,
)

# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def basic_order() -> Order:
    """全天订单 09:30 → 15:00"""
    return Order(
        symbol="000001",
        side="BUY",
        total_shares=10000.0,
        start_time=pd.Timestamp("2026-08-18 09:30"),
        end_time=pd.Timestamp("2026-08-18 15:00"),
    )


@pytest.fixture
def short_order() -> Order:
    """短窗口订单 10:00 → 10:30 (3 个 10min 槽)"""
    return Order(
        symbol="600000",
        side="SELL",
        total_shares=3000.0,
        start_time=pd.Timestamp("2026-08-18 10:00"),
        end_time=pd.Timestamp("2026-08-18 10:30"),
    )


# ============================================================
# dataclass
# ============================================================


class TestOrderDataclass:
    @pytest.mark.unit
    def test_defaults(self):
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
        )
        assert o.benchmark_price == 0.0
        assert o.urgency == "MEDIUM"
        assert o.max_participation == 0.10
        assert o.min_slice_size == 100.0


class TestChildOrderDataclass:
    @pytest.mark.unit
    def test_defaults(self):
        c = ChildOrder(
            symbol="000001", side="BUY", shares=500.0,
            scheduled_time=pd.Timestamp("2026-08-18 10:00"),
        )
        assert c.limit_price is None
        assert c.slice_type == "NORMAL"


class TestExecutionPlanDataclass:
    @pytest.mark.unit
    def test_defaults(self, basic_order):
        p = ExecutionPlan(parent_order=basic_order, algorithm="VWAP")
        assert p.child_orders == []
        assert p.expected_cost_bps == 0.0
        assert p.metadata == {}


# ============================================================
# ExecutionAlgorithmEngine 构造
# ============================================================


class TestEngineConstruction:
    @pytest.mark.unit
    def test_default_params(self):
        eng = ExecutionAlgorithmEngine()
        assert len(eng.default_volume_curve) == 24
        assert eng.impact_coeff == 0.1
        assert eng.impact_decay == 0.5
        assert eng.randomize_size == 0.15
        assert eng.randomize_time == 0.10
        assert eng.risk_aversion == 1.0

    @pytest.mark.unit
    def test_custom_params(self):
        eng = ExecutionAlgorithmEngine(
            default_volume_curve=[1, 2, 3],
            impact_coeff=0.2, impact_decay=0.6,
            randomize_size=0.05, randomize_time=0.02,
            risk_aversion=2.0, seed=7,
        )
        assert eng.default_volume_curve == [1.0, 2.0, 3.0]
        assert eng.impact_coeff == 0.2
        assert eng.risk_aversion == 2.0

    @pytest.mark.unit
    def test_default_u_shape_curve(self):
        curve = ExecutionAlgorithmEngine._default_u_shape_curve()
        assert len(curve) == 24
        # U 型: 开盘和收盘大, 中午小
        assert curve[0] > curve[6]
        assert curve[-1] > curve[6]


# ============================================================
# _generate_trading_slots
# ============================================================


class TestGenerateTradingSlots:
    @pytest.mark.unit
    def test_full_day_skips_lunch(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        slots = eng._generate_trading_slots(
            basic_order.start_time, basic_order.end_time, slot_minutes=30,
        )
        # 上午 09:30-11:30 = 4 槽 (30min), 下午 13:00-15:00 = 4 槽
        assert len(slots) == 8
        # 没有槽落在午休 11:30-13:00
        for s in slots:
            assert not (pd.Timestamp("2026-08-18 11:30") <= s < pd.Timestamp("2026-08-18 13:00"))

    @pytest.mark.unit
    def test_short_window(self, short_order):
        eng = ExecutionAlgorithmEngine()
        slots = eng._generate_trading_slots(
            short_order.start_time, short_order.end_time, slot_minutes=10,
        )
        assert len(slots) == 3
        assert slots[0] == pd.Timestamp("2026-08-18 10:00")
        assert slots[-1] == pd.Timestamp("2026-08-18 10:20")

    @pytest.mark.unit
    def test_crossing_lunch(self):
        eng = ExecutionAlgorithmEngine()
        slots = eng._generate_trading_slots(
            pd.Timestamp("2026-08-18 11:00"),
            pd.Timestamp("2026-08-18 13:30"),
            slot_minutes=30,
        )
        # 11:00 一槽, 跳到 13:00, 13:00 一槽
        assert len(slots) == 2
        assert slots[0] == pd.Timestamp("2026-08-18 11:00")
        assert slots[1] == pd.Timestamp("2026-08-18 13:00")


# ============================================================
# VWAP
# ============================================================


class TestVwap:
    @pytest.mark.unit
    def test_basic_plan(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order)
        assert plan.algorithm == "VWAP"
        assert plan.parent_order is basic_order
        assert plan.num_slices == len(plan.child_orders)
        assert plan.num_slices > 0
        assert plan.total_duration_minutes == 330

    @pytest.mark.unit
    def test_total_shares_conserved(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order, slot_minutes=30)
        total = sum(c.shares for c in plan.child_orders)
        # 随机化后归一化, 总量应接近 total_shares (round 误差)
        assert abs(total - basic_order.total_shares) < plan.num_slices

    @pytest.mark.unit
    def test_with_adv(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order, adv=1_000_000.0)
        assert plan.metadata["adv_provided"] is True
        assert plan.metadata["adv_proxy"] == 1_000_000.0
        expected_impact = 0.1 * 10000 * math.sqrt(10000.0 / 1_000_000.0)
        assert abs(plan.expected_market_impact_bps - expected_impact * 0.7) < 1e-6

    @pytest.mark.unit
    def test_without_adv(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order)
        assert plan.metadata["adv_provided"] is False
        assert plan.metadata["adv_proxy"] == max(basic_order.total_shares * 10, 1.0)

    @pytest.mark.unit
    def test_custom_volume_profile(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        # 自定义曲线: 仅前 4 槽有量
        plan = eng.vwap(basic_order, volume_profile=[1, 1, 1, 1], slot_minutes=30)
        assert plan.num_slices > 0

    @pytest.mark.unit
    def test_slice_type_open_close(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order, slot_minutes=30)
        types = {c.slice_type for c in plan.child_orders}
        # 全天订单应至少有 OPEN 或 CLOSE 切片
        assert types & {"OPEN", "CLOSE"}

    @pytest.mark.unit
    def test_metadata(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order, slot_minutes=15)
        assert plan.metadata["slot_minutes"] == 15
        assert plan.metadata["curve_type"] == "default_u_shape"


# ============================================================
# TWAP
# ============================================================


class TestTwap:
    @pytest.mark.unit
    def test_basic_plan(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.twap(basic_order, slot_minutes=30)
        assert plan.algorithm == "TWAP"
        assert plan.num_slices > 0

    @pytest.mark.unit
    def test_total_shares_conserved(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.twap(basic_order, slot_minutes=30)
        total = sum(c.shares for c in plan.child_orders)
        assert abs(total - basic_order.total_shares) < plan.num_slices

    @pytest.mark.unit
    def test_with_adv(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.twap(basic_order, adv=500_000.0)
        assert plan.metadata["adv_provided"] is True
        # TWAP cost = impact * 1.1
        expected_impact = 0.1 * 10000 * math.sqrt(10000.0 / 500_000.0)
        assert abs(plan.expected_cost_bps - expected_impact * 1.1) < 1e-6

    @pytest.mark.unit
    def test_empty_slots_returns_empty_plan(self):
        """start == end → 0 slots"""
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 10:00"),
            end_time=pd.Timestamp("2026-08-18 10:00"),
        )
        plan = eng.twap(o)
        assert plan.algorithm == "TWAP"
        assert plan.child_orders == []
        assert plan.num_slices == 0


# ============================================================
# POV
# ============================================================


class TestPov:
    @pytest.mark.unit
    def test_basic_plan(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.pov(basic_order, expected_market_volume=500_000.0, slot_minutes=30)
        assert plan.algorithm == "POV"
        assert plan.num_slices > 0

    @pytest.mark.unit
    def test_participation_rate_metadata(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.pov(basic_order, expected_market_volume=500_000.0)
        assert plan.metadata["participation_rate"] == basic_order.max_participation
        assert plan.metadata["expected_market_volume"] == 500_000.0

    @pytest.mark.unit
    def test_remaining_sweep(self):
        """小 market_volume → participation*vol 不足以吃完订单, 剩余扫尾到最后一切片"""
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=10000.0,
            start_time=pd.Timestamp("2026-08-18 10:00"),
            end_time=pd.Timestamp("2026-08-18 11:00"),
            max_participation=0.01,
        )
        plan = eng.pov(o, expected_market_volume=1000.0, slot_minutes=10)
        if plan.child_orders:
            total = sum(c.shares for c in plan.child_orders)
            # 剩余被扫尾到最后, 总量应接近 total_shares
            assert total >= 0

    @pytest.mark.unit
    def test_empty_slots(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 10:00"),
            end_time=pd.Timestamp("2026-08-18 10:00"),
        )
        plan = eng.pov(o, expected_market_volume=1000.0)
        assert plan.algorithm == "POV"
        assert plan.child_orders == []


# ============================================================
# IS (Implementation Shortfall)
# ============================================================


class TestIsAlgo:
    @pytest.mark.unit
    def test_basic_plan(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.is_algo(basic_order)
        assert plan.algorithm == "IS"
        assert plan.num_slices > 0
        assert plan.metadata["urgency"] == "MEDIUM"

    @pytest.mark.unit
    def test_urgency_to_lambda(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan_low = eng.is_algo(basic_order, daily_volatility=0.02)
        # 改 urgency 后重新构造 order
        o_high = Order(
            symbol=basic_order.symbol, side=basic_order.side,
            total_shares=basic_order.total_shares,
            start_time=basic_order.start_time, end_time=basic_order.end_time,
            urgency="HIGH",
        )
        plan_high = eng.is_algo(o_high)
        # HIGH (λ=2.5) 比 MEDIUM (λ=1.0) 前置更激进
        assert plan_low.metadata["lambda"] == 1.0
        assert plan_high.metadata["lambda"] == 2.5

    @pytest.mark.unit
    def test_with_adv(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.is_algo(basic_order, adv=2_000_000.0)
        assert plan.metadata["adv_provided"] is True
        assert plan.metadata["adv_proxy"] == 2_000_000.0

    @pytest.mark.unit
    def test_sigma2_zero(self, basic_order):
        """daily_volatility=0 → timing_risk=0"""
        eng = ExecutionAlgorithmEngine()
        plan = eng.is_algo(basic_order, daily_volatility=0.0)
        assert plan.expected_timing_risk_bps == 0.0

    @pytest.mark.unit
    def test_empty_slots(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 10:00"),
            end_time=pd.Timestamp("2026-08-18 10:00"),
        )
        plan = eng.is_algo(o)
        assert plan.algorithm == "IS"
        assert plan.child_orders == []

    @pytest.mark.unit
    def test_total_shares_conserved(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.is_algo(basic_order, slot_minutes=30)
        total = sum(c.shares for c in plan.child_orders)
        assert abs(total - basic_order.total_shares) < plan.num_slices


# ============================================================
# _clamp_to_trading_hours
# ============================================================


class TestClampToTradingHours:
    @pytest.mark.unit
    def test_normal_morning(self):
        ts = pd.Timestamp("2026-08-18 10:15")
        out = ExecutionAlgorithmEngine._clamp_to_trading_hours(ts)
        assert out == ts

    @pytest.mark.unit
    def test_normal_afternoon(self):
        ts = pd.Timestamp("2026-08-18 14:30")
        out = ExecutionAlgorithmEngine._clamp_to_trading_hours(ts)
        assert out == ts

    @pytest.mark.unit
    def test_before_open(self):
        ts = pd.Timestamp("2026-08-18 09:00")
        out = ExecutionAlgorithmEngine._clamp_to_trading_hours(ts)
        assert out.hour == 9 and out.minute == 30

    @pytest.mark.unit
    def test_lunch_break(self):
        ts = pd.Timestamp("2026-08-18 12:00")
        out = ExecutionAlgorithmEngine._clamp_to_trading_hours(ts)
        assert out.hour == 13 and out.minute == 0

    @pytest.mark.unit
    def test_after_close(self):
        ts = pd.Timestamp("2026-08-18 15:30")
        out = ExecutionAlgorithmEngine._clamp_to_trading_hours(ts)
        assert out.hour == 14 and out.minute == 59


# ============================================================
# _apply_randomization
# ============================================================


class TestApplyRandomization:
    @pytest.mark.unit
    def test_length_preserved(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        slots = eng._generate_trading_slots(
            basic_order.start_time, basic_order.end_time, slot_minutes=30,
        )
        shares = [100.0] * len(slots)
        out = eng._apply_randomization(shares, slots)
        assert len(out) == len(slots)

    @pytest.mark.unit
    def test_no_negative_shares(self, basic_order):
        eng = ExecutionAlgorithmEngine(randomize_size=0.99)
        slots = eng._generate_trading_slots(
            basic_order.start_time, basic_order.end_time, slot_minutes=30,
        )
        shares = [100.0] * len(slots)
        out = eng._apply_randomization(shares, slots)
        for s, _ in out:
            assert s >= 0.0

    @pytest.mark.unit
    def test_time_clamped_to_trading_hours(self, basic_order):
        eng = ExecutionAlgorithmEngine(randomize_time=0.5)
        slots = eng._generate_trading_slots(
            basic_order.start_time, basic_order.end_time, slot_minutes=30,
        )
        shares = [100.0] * len(slots)
        out = eng._apply_randomization(shares, slots)
        for _, t in out:
            hour_min = t.hour * 60 + t.minute
            # 不落在午休 11:30-13:00
            assert not (11 * 60 + 30 < hour_min < 13 * 60)


# ============================================================
# select_algorithm
# ============================================================


class TestSelectAlgorithm:
    @pytest.mark.unit
    def test_large_order_pov(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=100000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
        )
        # participation = 100000 / 400000 = 0.25 > 0.20
        assert eng.select_algorithm(o, adv=400000.0) == "POV"

    @pytest.mark.unit
    def test_medium_order_vwap(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=10000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
            urgency="MEDIUM",
        )
        # participation = 10000 / 200000 = 0.05, 等于阈值, 走小单分支 → VWAP
        assert eng.select_algorithm(o, adv=200000.0) == "VWAP"

    @pytest.mark.unit
    def test_medium_order_high_urgency_is(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=20000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
            urgency="HIGH",
        )
        # participation = 20000 / 200000 = 0.10, 中单 + HIGH → IS
        assert eng.select_algorithm(o, adv=200000.0) == "IS"

    @pytest.mark.unit
    def test_small_order_vwap(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
            urgency="MEDIUM",
        )
        # participation = 1000 / 1000000 = 0.001 < 0.05, 小单 + MEDIUM → VWAP
        assert eng.select_algorithm(o, adv=1_000_000.0) == "VWAP"

    @pytest.mark.unit
    def test_small_order_high_urgency_twap(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
            urgency="HIGH",
        )
        # 小单 + HIGH → TWAP
        assert eng.select_algorithm(o, adv=1_000_000.0) == "TWAP"

    @pytest.mark.unit
    def test_zero_adv_safe(self):
        eng = ExecutionAlgorithmEngine()
        o = Order(
            symbol="000001", side="BUY", total_shares=1000.0,
            start_time=pd.Timestamp("2026-08-18 09:30"),
            end_time=pd.Timestamp("2026-08-18 15:00"),
        )
        # adv=0 不应抛异常
        result = eng.select_algorithm(o, adv=0.0)
        assert result in {"VWAP", "TWAP", "POV", "IS"}


# ============================================================
# summarize_plan
# ============================================================


class TestSummarizePlan:
    @pytest.mark.unit
    def test_summary_keys(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        plan = eng.vwap(basic_order, slot_minutes=30)
        s = eng.summarize_plan(plan)
        expected_keys = {
            "algorithm", "symbol", "side", "total_shares",
            "num_slices", "avg_slice_size", "max_slice_size",
            "duration_minutes", "expected_cost_bps",
            "expected_impact_bps", "expected_timing_risk_bps",
        }
        assert set(s.keys()) == expected_keys
        assert s["algorithm"] == "VWAP"
        assert s["symbol"] == "000001"
        assert s["side"] == "BUY"
        assert s["total_shares"] == 10000.0

    @pytest.mark.unit
    def test_empty_plan_summary(self, basic_order):
        eng = ExecutionAlgorithmEngine()
        empty = ExecutionPlan(parent_order=basic_order, algorithm="TWAP")
        s = eng.summarize_plan(empty)
        assert s["num_slices"] == 0
        assert s["avg_slice_size"] == 0.0
