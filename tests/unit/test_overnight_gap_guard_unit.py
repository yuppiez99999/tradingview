"""overnight_gap_guard 单元测试 — 隔夜跳空 Guard 全覆盖.

被测模块: utils/overnight_gap_guard.py
覆盖目标: >=90%
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.overnight_gap_guard import OvernightGapGuard  # noqa: E402

# ============================================================
# __init__
# ============================================================


class TestInit:
    def test_default(self):
        guard = OvernightGapGuard()
        assert guard.l1_threshold == 0.02
        assert guard.l2_threshold == 0.03
        assert guard.l3_threshold == 0.05

    def test_custom(self):
        guard = OvernightGapGuard(
            l1_threshold=0.015, l2_threshold=0.025, l3_threshold=0.04
        )
        assert guard.l1_threshold == 0.015
        assert guard.l2_threshold == 0.025


# ============================================================
# check_gap
# ============================================================


class TestCheckGap:
    def test_normal(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.10, prev_close=10.00, symbol="A")
        assert status.level == 0
        assert status.gap_direction == "UP"

    def test_l1_alert(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.25, prev_close=10.00, symbol="A")
        assert status.level == 1
        assert status.reduce_pct == 0.0

    def test_l2_reduce(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.35, prev_close=10.00, symbol="A")
        assert status.level == 2
        assert status.reduce_pct == 0.30

    def test_l3_clear(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.60, prev_close=10.00, symbol="A")
        assert status.level == 3
        assert status.reduce_pct == 0.50

    def test_gap_down(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.40, prev_close=10.00, symbol="A")
        assert status.level == 3
        assert status.gap_direction == "DOWN"

    def test_gap_down_l2(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=9.65, prev_close=10.00, symbol="A")
        assert status.level == 2
        assert status.gap_direction == "DOWN"

    def test_no_gap(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.00, prev_close=10.00, symbol="A")
        assert status.level == 0
        assert status.gap_direction == "NONE"

    def test_zero_prev_close(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.00, prev_close=0, symbol="A")
        assert status.level == 0
        assert status.gap_direction == "NONE"

    def test_negative_prev_close(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.00, prev_close=-1, symbol="A")
        assert status.level == 0

    def test_symbol(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.10, prev_close=10.00, symbol="600519")
        assert status.symbol == "600519"

    def test_actions_l3(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.60, prev_close=10.00, symbol="A")
        assert len(status.actions) >= 2

    def test_timestamp(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(opening_price=10.10, prev_close=10.00, symbol="A")
        assert status.timestamp != ""


# ============================================================
# apply_to_plan
# ============================================================


class TestApplyToPlan:
    def test_l0_no_change(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.10, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        result = guard.apply_to_plan(plan, status, {})
        assert "gap_guard_reduce_orders" not in result["execution_plan"]

    def test_l1_alert_only(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.25, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        result = guard.apply_to_plan(plan, status, {})
        assert result["market_state"].get("circuit_level") == "ALERT"

    def test_l2_reduce_long_on_gap_down(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(9.65, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "BUY", "actual_shares": 1000}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 1
        assert orders[0]["direction"] == "SELL"

    def test_l2_reduce_short_on_gap_up(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.35, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "SHORT", "actual_shares": 1000}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 1
        assert orders[0]["direction"] == "BUY_TO_CLOSE"

    def test_l2_no_reduce_favorable_direction(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.35, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "BUY", "actual_shares": 1000}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 0

    def test_l3_halt_add_positions(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.60, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "SHORT", "actual_shares": 1000}}
        result = guard.apply_to_plan(plan, status, positions)
        assert result["market_state"]["halt_add_positions"] is True
        assert result["market_state"]["circuit_level"] == "CRITICAL"

    def test_reduce_qty_rounded_to_lot(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(9.65, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "BUY", "actual_shares": 350}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 1
        assert orders[0]["shares"] % 100 == 0

    def test_reduce_qty_too_small(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(9.65, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"A": {"side": "BUY", "actual_shares": 200}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 0

    def test_different_symbol_not_affected(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(9.65, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        positions = {"B": {"side": "BUY", "actual_shares": 1000}}
        result = guard.apply_to_plan(plan, status, positions)
        orders = result["execution_plan"].get("gap_guard_reduce_orders", [])
        assert len(orders) == 0

    def test_l1_does_not_override_warning(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.25, 10.00, "A")
        plan = {
            "execution_plan": {},
            "market_state": {"circuit_level": "WARNING"},
            "risk_guard": {},
        }
        result = guard.apply_to_plan(plan, status, {})
        assert result["market_state"]["circuit_level"] == "WARNING"

    def test_l2_sets_warning(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(9.65, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        result = guard.apply_to_plan(plan, status, {})
        assert result["market_state"]["circuit_level"] == "WARNING"

    def test_risk_guard_metadata(self):
        guard = OvernightGapGuard()
        status = guard.check_gap(10.35, 10.00, "A")
        plan = {"execution_plan": {}, "market_state": {}, "risk_guard": {}}
        result = guard.apply_to_plan(plan, status, {})
        assert "overnight_gap_guard" in result["risk_guard"]
        assert result["risk_guard"]["overnight_gap_guard"]["level"] == 2
