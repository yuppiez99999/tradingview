"""T16 单元测试 — OrderLifecycleTracker 订单生命周期跟踪器."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from utils.risk.order_lifecycle_tracker import (
    OrderLifecycleTracker,
    OrderState,
    map_broker_state,
)
from utils.risk.risk_audit_logger import RiskAuditLogger

# ============================================================
# 测试夹具
# ============================================================

class MockBroker:
    """模拟 broker, 可配置 get_order_status 返回值."""

    def __init__(self, states: list[dict[str, Any]] | None = None):
        self._states = states or []
        self._call_idx = 0
        self.cancel_calls: list[str] = []

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        if self._call_idx < len(self._states):
            s = self._states[self._call_idx]
            self._call_idx += 1
            return s
        return {"state": "FILLED", "filled_qty": 100, "avg_price": 10.0}

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_calls.append(broker_order_id)
        return True


class FailingCancelBroker(MockBroker):
    """撤单失败的 broker."""

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_calls.append(broker_order_id)
        return False


def _make_tracker(broker: MockBroker | None = None, timeout_sec: int = 30, orphan_sec: int = 10) -> OrderLifecycleTracker:
    import tempfile
    from pathlib import Path
    audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
    return OrderLifecycleTracker(
        broker=broker or MockBroker(),
        audit_logger=audit,
        timeout_sec=timeout_sec,
        orphan_sec=orphan_sec,
    )


# ============================================================
# map_broker_state 测试
# ============================================================

class TestMapBrokerState:
    def test_qmt_numeric_codes(self):
        assert map_broker_state("48") == OrderState.PENDING
        assert map_broker_state("49") == OrderState.SUBMITTED
        assert map_broker_state("50") == OrderState.PARTIAL_FILL
        assert map_broker_state("53") == OrderState.FILLED
        assert map_broker_state("54") == OrderState.CANCELLED
        assert map_broker_state("56") == OrderState.REJECTED

    def test_string_states(self):
        assert map_broker_state("REPORTED") == OrderState.SUBMITTED
        assert map_broker_state("PART_TRADED") == OrderState.PARTIAL_FILL
        assert map_broker_state("ALL_TRADED") == OrderState.FILLED
        assert map_broker_state("CANCELLED") == OrderState.CANCELLED
        assert map_broker_state("REJECTED") == OrderState.REJECTED

    def test_case_insensitive(self):
        assert map_broker_state("filled") == OrderState.FILLED
        assert map_broker_state("Cancelled") == OrderState.CANCELLED

    def test_unknown_returns_error(self):
        assert map_broker_state("UNKNOWN_STATE") == OrderState.ERROR
        assert map_broker_state("") == OrderState.ERROR


# ============================================================
# OrderState 属性测试
# ============================================================

class TestOrderState:
    def test_is_terminal(self):
        assert OrderState.FILLED.is_terminal
        assert OrderState.CANCELLED.is_terminal
        assert OrderState.REJECTED.is_terminal
        assert OrderState.ERROR.is_terminal
        assert OrderState.ORPHANED.is_terminal
        assert not OrderState.PENDING.is_terminal
        assert not OrderState.SUBMITTED.is_terminal
        assert not OrderState.PARTIAL_FILL.is_terminal

    def test_is_active(self):
        assert OrderState.SUBMITTED.is_active
        assert OrderState.PARTIAL_FILL.is_active
        assert not OrderState.FILLED.is_active
        assert not OrderState.CANCELLED.is_active


# ============================================================
# 注册与查询
# ============================================================

class TestRegister:
    def test_register_returns_tracked_order(self):
        tracker = _make_tracker()
        tracked = tracker.register("o1", "b1", "sh600000", "buy", 100)

        assert tracked.order_id == "o1"
        assert tracked.broker_order_id == "b1"
        assert tracked.state == OrderState.SUBMITTED
        assert tracked.planned_qty == 100

    def test_register_empty_id_raises(self):
        tracker = _make_tracker()
        with pytest.raises(ValueError, match="order_id 不能为空"):
            tracker.register("", "b1", "sh", "buy", 100)

    def test_register_zero_qty_raises(self):
        tracker = _make_tracker()
        with pytest.raises(ValueError, match="planned_qty"):
            tracker.register("o1", "b1", "sh", "buy", 0)

    def test_get_state(self):
        tracker = _make_tracker()
        tracker.register("o1", "b1", "sh", "buy", 100)
        assert tracker.get_state("o1") == OrderState.SUBMITTED
        assert tracker.get_state("nonexistent") is None

    def test_get_all_active(self):
        tracker = _make_tracker()
        tracker.register("o1", "b1", "sh", "buy", 100)
        tracker.register("o2", "b2", "sh", "sell", 200)
        active = tracker.get_all_active()
        assert len(active) == 2


# ============================================================
# 轮询与状态转移
# ============================================================

class TestPollOnce:
    def test_submitted_to_filled(self):
        broker = MockBroker(states=[{"state": "FILLED", "filled_qty": 100, "avg_price": 10.0}])
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        changed = tracker.poll_once()

        assert len(changed) == 1
        assert changed[0].state == OrderState.FILLED
        assert changed[0].filled_qty == 100
        assert changed[0].avg_fill_price == 10.0

    def test_submitted_to_partial_fill(self):
        broker = MockBroker(states=[{"state": "PART_TRADED", "filled_qty": 50, "avg_price": 10.0}])
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        changed = tracker.poll_once()

        assert changed[0].state == OrderState.PARTIAL_FILL
        assert changed[0].filled_qty == 50

    def test_same_state_no_change(self):
        broker = MockBroker(states=[{"state": "REPORTED", "filled_qty": 0, "avg_price": 0.0}])
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        changed = tracker.poll_once()

        assert len(changed) == 0  # SUBMITTED → SUBMITTED, 无变化

    def test_rejected_state(self):
        broker = MockBroker(states=[{"state": "REJECTED", "filled_qty": 0, "avg_price": 0.0, "rejection_reason": "限价无效"}])
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        changed = tracker.poll_once()

        assert changed[0].state == OrderState.REJECTED
        assert changed[0].rejection_reason == "限价无效"

    def test_callback_invoked(self):
        broker = MockBroker(states=[{"state": "FILLED", "filled_qty": 100, "avg_price": 10.0}])
        tracker = _make_tracker(broker=broker)
        callback = MagicMock()
        tracker.register("o1", "b1", "sh", "buy", 100, callback=callback)

        tracker.poll_once()

        callback.assert_called_once()
        assert callback.call_args[0][0].state == OrderState.FILLED


class TestInvalidTransition:
    def test_filled_to_submitted_ignored(self):
        """终态后不应再转移."""
        broker = MockBroker(states=[
            {"state": "FILLED", "filled_qty": 100, "avg_price": 10.0},
            {"state": "REPORTED", "filled_qty": 0, "avg_price": 0.0},  # 应被忽略
        ])
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        tracker.poll_once()  # → FILLED
        changed = tracker.poll_once()  # 尝试 FILLED→SUBMITTED, 应无变化

        assert len(changed) == 0


# ============================================================
# 超时与撤单
# ============================================================

class TestTimeoutAndCancel:
    def test_orphaned_on_timeout(self):
        """SUBMITTED 超时 → ORPHANED."""
        broker = MockBroker(states=[{"state": "REPORTED", "filled_qty": 0, "avg_price": 0.0}])
        tracker = _make_tracker(broker=broker, timeout_sec=1, orphan_sec=1)
        tracker.register("o1", "b1", "sh", "buy", 100)

        # 手动调整 deadline 到过去
        with tracker._lock:
            tracker._orders["o1"].timeout_deadline = datetime.now() - timedelta(seconds=1)

        changed = tracker.poll_once()

        assert any(o.state == OrderState.ORPHANED for o in changed)

    def test_cancel_stale_orders(self):
        """超时订单应被撤单."""
        broker = MockBroker()
        tracker = _make_tracker(broker=broker, timeout_sec=1)
        tracker.register("o1", "b1", "sh", "buy", 100)

        with tracker._lock:
            tracker._orders["o1"].timeout_deadline = datetime.now() - timedelta(seconds=1)

        cancelled = tracker.cancel_stale_orders()

        assert "o1" in cancelled
        assert tracker.get_state("o1") == OrderState.CANCELLED

    def test_force_cancel(self):
        broker = MockBroker()
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)

        ok = tracker.force_cancel("o1")

        assert ok
        assert tracker.get_state("o1") == OrderState.CANCELLED

    def test_force_cancel_nonexistent(self):
        tracker = _make_tracker()
        assert tracker.force_cancel("nonexistent") is False

    def test_force_cancel_all(self):
        broker = MockBroker()
        tracker = _make_tracker(broker=broker)
        tracker.register("o1", "b1", "sh", "buy", 100)
        tracker.register("o2", "b2", "sh", "sell", 200)

        cancelled = tracker.force_cancel_all()

        assert len(cancelled) == 2
        assert all(tracker.get_state(oid) == OrderState.CANCELLED for oid in cancelled)

    def test_cancel_failure_marks_error(self):
        broker = FailingCancelBroker()
        tracker = _make_tracker(broker=broker, timeout_sec=1)
        tracker.register("o1", "b1", "sh", "buy", 100)

        with tracker._lock:
            tracker._orders["o1"].timeout_deadline = datetime.now() - timedelta(seconds=1)

        cancelled = tracker.cancel_stale_orders()

        assert len(cancelled) == 0
        assert tracker.get_state("o1") == OrderState.ERROR


# ============================================================
# 快照
# ============================================================

class TestSnapshot:
    def test_snapshot_has_expected_keys(self):
        tracker = _make_tracker()
        tracker.register("o1", "b1", "sh", "buy", 100)
        tracker.register("o2", "b2", "sh", "sell", 200)

        snap = tracker.snapshot()

        assert snap["total_tracked"] == 2
        assert snap["active_count"] == 2
        assert snap["terminal_count"] == 0
        assert "submitted" in snap["by_state"]
        assert len(snap["active_order_ids"]) == 2


# ============================================================
# 配置校验
# ============================================================

class TestConfigValidation:
    def test_invalid_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout_sec"):
            _make_tracker(timeout_sec=0)

    def test_invalid_orphan_raises(self):
        with pytest.raises(ValueError, match="orphan_sec"):
            _make_tracker(orphan_sec=0)
