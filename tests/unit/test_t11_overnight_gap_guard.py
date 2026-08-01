# -*- coding: utf-8 -*-
"""T11: 隔夜跳空 Guard 测试 — 跳空 > 3% 自动降仓.

验证目标:
    1. 跳空 2% 触发 L1 警戒 (只告警, 不降仓)
    2. 跳空 3% 触发 L2 降仓 (降仓 30%, 仅不利方向)
    3. 跳空 5% 触发 L3 清仓 (降仓 50% + 禁止加仓)
    4. 跳空上涨只降空头, 跳空下跌只降多头
    5. 降仓数量向下取整到 100 股
    6. 降仓数量不足 100 股时跳过
    7. 非跳空标的的持仓不受影响
"""
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.overnight_gap_guard import OvernightGapGuard


# ============================================================
# 1. T11: 跳空级别触发
# ============================================================
class TestT11GapLevelTrigger:
    """T11: 跳空级别触发测试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_1pct_no_trigger(self):
        """T11: 跳空 1% 不触发任何级别."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.10, prev_close=10.00, symbol="510300")

        assert status.level == 0
        assert status.level_name == "正常"
        assert status.reduce_pct == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_2pct_triggers_l1(self):
        """T11: 跳空 2% 触发 L1 警戒."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.21, prev_close=10.00, symbol="510300")

        assert status.level == 1
        assert status.level_name == "L1警戒"
        assert status.reduce_pct == 0.0, "L1 不降仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_3pct_triggers_l2(self):
        """T11: 跳空 3% 触发 L2 降仓."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.30, prev_close=10.00, symbol="510300")

        assert status.level == 2
        assert status.level_name == "L2降仓"
        assert status.reduce_pct == 0.30, "L2 降仓 30%"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_5pct_triggers_l3(self):
        """T11: 跳空 5% 触发 L3 清仓."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.50, prev_close=10.00, symbol="510300")

        assert status.level == 3
        assert status.level_name == "L3清仓"
        assert status.reduce_pct == 0.50, "L3 降仓 50%"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_down_3pct_triggers_l2(self):
        """T11: 跳空下跌 3% 触发 L2."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        assert status.level == 2
        assert status.gap_pct == pytest.approx(-0.03, abs=1e-6)
        assert status.gap_direction == "DOWN"


# ============================================================
# 2. T11: 跳空方向判断
# ============================================================
class TestT11GapDirection:
    """T11: 跳空方向判断测试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_up_direction(self):
        """T11: 上涨跳空 direction=UP."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.30, prev_close=10.00)

        assert status.gap_direction == "UP"
        assert status.gap_pct > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_gap_down_direction(self):
        """T11: 下跌跳空 direction=DOWN."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00)

        assert status.gap_direction == "DOWN"
        assert status.gap_pct < 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t11_no_gap_direction_none(self):
        """T11: 无跳空 direction=NONE."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.00, prev_close=10.00)

        assert status.gap_direction == "NONE"
        assert status.level == 0


# ============================================================
# 3. T11: apply_to_plan 降仓订单生成
# ============================================================
class TestT11ApplyToPlan:
    """T11: apply_to_plan 降仓订单生成测试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l2_gap_down_reduces_long_positions(self):
        """T11: L2 跳空下跌, 多头持仓被降仓 30%."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "BUY", "actual_shares": 10000},
        }

        result = guard.apply_to_plan(plan, status, positions)

        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 1, "应生成 1 笔降仓订单"
        assert reduce_orders[0]["direction"] == "SELL", "多头降仓方向=SELL"
        assert reduce_orders[0]["shares"] == 3000, "10000 × 30% = 3000 股"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l2_gap_up_reduces_short_positions(self):
        """T11: L2 跳空上涨, 空头持仓被降仓 30%."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.30, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "SELL", "quantity": 1000},
        }

        result = guard.apply_to_plan(plan, status, positions)

        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 1
        assert reduce_orders[0]["direction"] == "BUY_TO_CLOSE", "空头降仓=BUY_TO_CLOSE"
        assert reduce_orders[0]["shares"] == 300, "1000 × 30% = 300 股"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l2_gap_down_does_not_reduce_short(self):
        """T11: L2 跳空下跌, 空头持仓不被降仓 (有利方向)."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "SELL", "quantity": 100},
        }

        result = guard.apply_to_plan(plan, status, positions)

        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 0, "跳空下跌对空头有利, 不应降仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l2_gap_up_does_not_reduce_long(self):
        """T11: L2 跳空上涨, 多头持仓不被降仓 (有利方向)."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.30, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "BUY", "actual_shares": 10000},
        }

        result = guard.apply_to_plan(plan, status, positions)

        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 0, "跳空上涨对多头有利, 不应降仓"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l3_reduces_50pct(self):
        """T11: L3 降仓 50% (跳空下跌 + 多头 = 不利方向)."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.50, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "BUY", "actual_shares": 10000},
        }

        result = guard.apply_to_plan(plan, status, positions)

        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 1
        assert reduce_orders[0]["shares"] == 5000, "10000 × 50% = 5000 股"
        assert result["market_state"]["halt_add_positions"] is True
        assert result["market_state"]["circuit_level"] == "CRITICAL"


# ============================================================
# 4. T11: 降仓数量取整
# ============================================================
class TestT11QuantityRounding:
    """T11: 降仓数量向下取整到 100 股."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_quantity_rounded_to_100(self):
        """T11: 降仓数量向下取整到 100 股."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        # 850 股 × 30% = 255 股, 取整 = 200 股
        positions = {"510300": {"side": "BUY", "actual_shares": 850}}

        result = guard.apply_to_plan(plan, status, positions)
        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 1
        assert reduce_orders[0]["shares"] == 200, "850 × 30% = 255, 取整 = 200"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_quantity_below_100_skipped(self):
        """T11: 降仓数量不足 100 股时跳过."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        # 300 股 × 30% = 90 股, 不足 100, 跳过
        positions = {"510300": {"side": "BUY", "actual_shares": 300}}

        result = guard.apply_to_plan(plan, status, positions)
        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 0, "300 × 30% = 90 < 100, 应跳过"


# ============================================================
# 5. T11: L1 警戒不降仓
# ============================================================
class TestT11L1NoReduce:
    """T11: L1 警戒只告警, 不降仓."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_l1_no_reduce_orders(self):
        """T11: L1 警戒不生成降仓订单."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.21, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"510300": {"side": "BUY", "actual_shares": 10000}}

        result = guard.apply_to_plan(plan, status, positions)
        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 0, "L1 不应生成降仓订单"
        assert result["market_state"].get("circuit_level") == "ALERT"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t11_l1_does_not_override_higher_level(self):
        """T11: L1 不覆盖更高级别."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.21, prev_close=10.00, symbol="510300")

        plan = {
            "execution_plan": {},
            "market_state": {"circuit_level": "CRITICAL"},
            "risk_guard": {},
        }
        positions = {}

        result = guard.apply_to_plan(plan, status, positions)
        assert result["market_state"]["circuit_level"] == "CRITICAL"


# ============================================================
# 6. T11: 非跳空标的不受影响
# ============================================================
class TestT11NonTargetSymbol:
    """T11: 非跳空标的的持仓不受影响."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_non_target_symbol_not_reduced(self):
        """T11: 跳空 510300, 持仓 588080 不受影响."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.70, prev_close=10.00, symbol="510300")

        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {
            "510300": {"side": "BUY", "actual_shares": 10000},
            "588080": {"side": "BUY", "actual_shares": 5000},  # 非跳空标的
        }

        result = guard.apply_to_plan(plan, status, positions)
        reduce_orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(reduce_orders) == 1, "只应降仓 510300, 不降仓 588080"
        assert reduce_orders[0]["symbol"] == "510300"


# ============================================================
# 7. T11: 边界条件
# ============================================================
class TestT11BoundaryConditions:
    """T11: 边界条件测试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_exactly_3pct_triggers_l2(self):
        """T11: 恰好 3.000% 触发 L2."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.30, prev_close=10.00)
        assert status.level == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t11_2_9pct_triggers_l1_not_l2(self):
        """T11: 2.9% 触发 L1, 不是 L2."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.291, prev_close=10.00)
        assert status.level == 1
        assert status.reduce_pct == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t11_prev_close_zero_returns_normal(self):
        """T11: 前收盘价为 0 时返回正常 (数据异常保护)."""
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.00, prev_close=0.00)
        assert status.level == 0
        assert status.level_name == "数据异常"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t11_custom_thresholds(self):
        """T11: 自定义阈值."""
        guard = OvernightGapGuard(l2_threshold=0.04, l3_threshold=0.06)
        # 3% 在自定义阈值下应触发 L1 (因 L2=4%)
        status = guard.check_gap(opening_price=10.30, prev_close=10.00)
        assert status.level == 1
        # 4% 触发 L2
        status2 = guard.check_gap(opening_price=10.40, prev_close=10.00)
        assert status2.level == 2
