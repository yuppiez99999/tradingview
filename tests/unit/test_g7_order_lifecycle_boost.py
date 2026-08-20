#!/usr/bin/env python
"""
test_g7_order_lifecycle_boost.py — 订单生命周期跟踪器覆盖率补强测试

覆盖 P0 risk 链路: utils/risk/order_lifecycle_tracker.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.risk.order_lifecycle_tracker import (
    OrderLifecycleTracker,
    OrderState,
    TrackedOrder,
    map_broker_state,
)


class TestOrderState:
    def test_values_exist(self) -> None:
        assert OrderState.PENDING == "pending"
        assert OrderState.FILLED == "filled"

    def test_is_terminal(self) -> None:
        assert OrderState.FILLED.is_terminal is True
        assert OrderState.PENDING.is_terminal is False

    def test_is_active(self) -> None:
        assert OrderState.PENDING.is_active is True
        assert OrderState.FILLED.is_active is False


class TestMapBrokerState:
    def test_known_state(self) -> None:
        result = map_broker_state("pending")
        assert isinstance(result, OrderState)

    def test_unknown_state(self) -> None:
        result = map_broker_state("unknown_state_xyz")
        assert isinstance(result, OrderState)

    def test_empty_string(self) -> None:
        result = map_broker_state("")
        assert isinstance(result, OrderState)


class TestTrackedOrder:
    def test_construction(self) -> None:
        order = TrackedOrder(
            order_id="test_001",
            broker_order_id="brk_001",
            symbol="sh600519",
            side="buy",
            planned_qty=100,
        )
        assert order.order_id == "test_001"
        assert order.state == OrderState.PENDING


class TestOrderLifecycleTracker:
    def _make_tracker(self) -> OrderLifecycleTracker:
        broker = MagicMock()
        audit = MagicMock()
        return OrderLifecycleTracker(broker=broker, audit_logger=audit)

    def test_construction(self) -> None:
        tracker = self._make_tracker()
        assert isinstance(tracker, OrderLifecycleTracker)

    def test_invalid_timeout(self) -> None:
        broker = MagicMock()
        audit = MagicMock()
        with pytest.raises(ValueError):
            OrderLifecycleTracker(broker=broker, audit_logger=audit, timeout_sec=0)

    def test_register_order(self) -> None:
        tracker = self._make_tracker()
        order = tracker.register(
            order_id="o1", broker_order_id="b1",
            symbol="sh600519", side="buy", planned_qty=100,
        )
        assert order.order_id == "o1"
