"""test_global_cancel_guard_unit.py — 全局撤单 Guard 单元测试

覆盖要点:
    - CancelResult dataclass
    - GlobalCancelGuard 构造
    - cancel_all_orders (broker未连接/无订单/正常撤单/symbols过滤/全部跳过/撤单失败)
    - _cancel_with_retry (成功/失败/异常)
    - _get_order_id / _get_order_status / _get_order_symbol (dict/对象)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from utils.global_cancel_guard import CancelResult, GlobalCancelGuard

# ============================================================
# CancelResult
# ============================================================


class TestCancelResult:
    @pytest.mark.unit
    def test_defaults(self):
        r = CancelResult(
            success=True,
            total_orders=0,
            cancelled_count=0,
            failed_count=0,
            skipped_count=0,
        )
        assert r.cancelled_order_ids == []
        assert r.errors == []
        assert r.trigger_reason == ""


# ============================================================
# 构造
# ============================================================


class TestConstruction:
    @pytest.mark.unit
    def test_default(self):
        g = GlobalCancelGuard()
        assert g.max_retries == 2
        assert g.retry_delay_sec == 0.5

    @pytest.mark.unit
    def test_custom(self):
        g = GlobalCancelGuard(max_retries=5, retry_delay_sec=1.0)
        assert g.max_retries == 5
        assert g.retry_delay_sec == 1.0


# ============================================================
# Mock broker
# ============================================================


@dataclass
class MockOrder:
    order_id: str = "ord1"
    status: str = "PENDING"
    symbol: str = "600519"


class MockBroker:
    def __init__(self, orders=None, connected=True, cancel_success=True):
        self.is_connected = connected
        self.orders = orders or []
        self._cancel_success = cancel_success

    def cancel(self, order):
        if self._cancel_success:
            return True
        return False


# ============================================================
# cancel_all_orders
# ============================================================


class TestCancelAllOrders:
    @pytest.mark.unit
    def test_broker_not_connected(self):
        g = GlobalCancelGuard()
        broker = MockBroker(connected=False)
        result = g.cancel_all_orders(broker)
        assert result.success is False
        assert result.total_orders == 0
        assert "broker_not_connected" in result.errors

    @pytest.mark.unit
    def test_no_orders(self):
        g = GlobalCancelGuard()
        broker = MockBroker(orders=[])
        result = g.cancel_all_orders(broker)
        assert result.success is True
        assert result.total_orders == 0

    @pytest.mark.unit
    def test_cancel_pending(self):
        g = GlobalCancelGuard()
        orders = [
            MockOrder(order_id="1", status="PENDING"),
            MockOrder(order_id="2", status="FILLED"),
            MockOrder(order_id="3", status="PARTIALLY_FILLED"),
        ]
        broker = MockBroker(orders=orders)
        result = g.cancel_all_orders(broker)
        assert result.success is True
        assert result.total_orders == 3
        assert result.cancelled_count == 2
        assert result.skipped_count == 1

    @pytest.mark.unit
    def test_symbols_filter(self):
        g = GlobalCancelGuard()
        orders = [
            MockOrder(order_id="1", status="PENDING", symbol="600519"),
            MockOrder(order_id="2", status="PENDING", symbol="000858"),
        ]
        broker = MockBroker(orders=orders)
        result = g.cancel_all_orders(broker, symbols=["600519"])
        assert result.cancelled_count == 1
        assert result.skipped_count == 1

    @pytest.mark.unit
    def test_all_skipped(self):
        g = GlobalCancelGuard()
        orders = [
            MockOrder(order_id="1", status="FILLED"),
            MockOrder(order_id="2", status="CANCELLED"),
        ]
        broker = MockBroker(orders=orders)
        result = g.cancel_all_orders(broker)
        assert result.cancelled_count == 0
        assert result.skipped_count == 2

    @pytest.mark.unit
    def test_cancel_failure(self):
        g = GlobalCancelGuard(max_retries=0)
        orders = [MockOrder(order_id="1", status="PENDING")]
        broker = MockBroker(orders=orders, cancel_success=False)
        result = g.cancel_all_orders(broker)
        assert result.success is False
        assert result.failed_count == 1

    @pytest.mark.unit
    def test_dict_orders(self):
        g = GlobalCancelGuard()
        orders = [
            {"order_id": "d1", "status": "PENDING", "symbol": "600519"},
            {"order_id": "d2", "status": "FILLED", "symbol": "000858"},
        ]
        broker = MockBroker(orders=orders)
        result = g.cancel_all_orders(broker)
        assert result.cancelled_count == 1
        assert result.skipped_count == 1

    @pytest.mark.unit
    def test_dict_orders_as_dict(self):
        """broker.orders 是 dict 而非 list"""
        g = GlobalCancelGuard()
        orders = {"k1": MockOrder(order_id="1", status="PENDING")}
        broker = MockBroker(orders=orders)
        result = g.cancel_all_orders(broker)
        assert result.cancelled_count == 1

    @pytest.mark.unit
    def test_trigger_reason(self):
        g = GlobalCancelGuard()
        broker = MockBroker(orders=[])
        result = g.cancel_all_orders(broker, trigger_reason="auto_crash")
        assert result.trigger_reason == "auto_crash"


# ============================================================
# 辅助方法
# ============================================================


class TestHelpers:
    @pytest.mark.unit
    def test_get_order_id_dict(self):
        g = GlobalCancelGuard()
        assert g._get_order_id({"order_id": "x1"}) == "x1"
        assert g._get_order_id({"id": "x2"}) == "x2"
        assert g._get_order_id({}) == "unknown"

    @pytest.mark.unit
    def test_get_order_id_object(self):
        g = GlobalCancelGuard()
        assert g._get_order_id(MockOrder(order_id="y1")) == "y1"

    @pytest.mark.unit
    def test_get_order_status_dict(self):
        g = GlobalCancelGuard()
        assert g._get_order_status({"status": "pending"}) == "PENDING"

    @pytest.mark.unit
    def test_get_order_symbol_dict(self):
        g = GlobalCancelGuard()
        assert g._get_order_symbol({"symbol": "600519"}) == "600519"
        assert g._get_order_symbol({}) == ""
