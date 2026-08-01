# -*- coding: utf-8 -*-
"""T12: 全局撤单 Guard 测试 — 一键撤所有.

验证目标:
    1. 撤销所有 PENDING 订单
    2. 跳过 FILLED / CANCELLED / REJECTED 订单
    3. broker 未连接时返回 success=False
    4. 无订单时返回 success=True, cancelled_count=0
    5. 单笔撤单失败时重试
    6. 按 symbols 过滤撤单
    7. 部分失败时 success=False
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.global_cancel_guard import GlobalCancelGuard


# ============================================================
# 辅助: 创建 mock broker
# ============================================================
def make_mock_broker(orders, connected=True):
    """创建 mock broker."""
    broker = MagicMock()
    broker.is_connected = connected
    broker.orders = orders
    broker.cancel.return_value = True
    return broker


def make_order(order_id, status="PENDING", symbol="510300"):
    """创建 mock 订单 (dict 形式)."""
    return {"order_id": order_id, "status": status, "symbol": symbol}


# ============================================================
# 1. T12: 基本撤单功能
# ============================================================
class TestT12BasicCancel:
    """T12: 基本撤单功能."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_cancel_all_pending_orders(self):
        """T12: 撤销所有 PENDING 订单."""
        orders = {
            "o001": make_order("o001", "PENDING"),
            "o002": make_order("o002", "PENDING"),
            "o003": make_order("o003", "PENDING"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker, trigger_reason="manual")

        assert result.success is True
        assert result.total_orders == 3
        assert result.cancelled_count == 3
        assert result.failed_count == 0
        assert result.skipped_count == 0
        assert len(result.cancelled_order_ids) == 3
        assert broker.cancel.call_count == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_cancel_partially_filled_orders(self):
        """T12: 撤销 PARTIALLY_FILLED 订单."""
        orders = {
            "o001": make_order("o001", "PARTIALLY_FILLED"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.cancelled_count == 1
        assert result.skipped_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_skip_filled_orders(self):
        """T12: 跳过 FILLED 订单."""
        orders = {
            "o001": make_order("o001", "FILLED"),
            "o002": make_order("o002", "PENDING"),
            "o003": make_order("o003", "FILLED"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.cancelled_count == 1, "只撤销 PENDING 订单"
        assert result.skipped_count == 2, "FILLED 订单被跳过"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_skip_cancelled_and_rejected_orders(self):
        """T12: 跳过 CANCELLED / REJECTED 订单."""
        orders = {
            "o001": make_order("o001", "CANCELLED"),
            "o002": make_order("o002", "REJECTED"),
            "o003": make_order("o003", "EXPIRED"),
            "o004": make_order("o004", "PENDING"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.cancelled_count == 1
        assert result.skipped_count == 3


# ============================================================
# 2. T12: broker 未连接
# ============================================================
class TestT12BrokerDisconnected:
    """T12: broker 未连接时返回失败."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_broker_not_connected_returns_failure(self):
        """T12: broker 未连接时 success=False."""
        orders = {"o001": make_order("o001", "PENDING")}
        broker = make_mock_broker(orders, connected=False)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.success is False
        assert result.cancelled_count == 0
        assert "broker_not_connected" in result.errors

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_broker_no_is_connected_attr(self):
        """T12: broker 无 is_connected 属性时返回失败."""
        broker = MagicMock()
        del broker.is_connected  # 删除属性
        broker.orders = {}
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.success is False


# ============================================================
# 3. T12: 无订单
# ============================================================
class TestT12NoOrders:
    """T12: 无订单时返回成功."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_no_orders_returns_success(self):
        """T12: 无订单时 success=True, cancelled_count=0."""
        broker = make_mock_broker({})
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.success is True
        assert result.total_orders == 0
        assert result.cancelled_count == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_empty_list_orders(self):
        """T12: orders 为空列表时返回成功."""
        broker = make_mock_broker([])
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)

        assert result.success is True
        assert result.total_orders == 0


# ============================================================
# 4. T12: 撤单失败与重试
# ============================================================
class TestT12CancelFailure:
    """T12: 撤单失败与重试."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_cancel_failure_returns_failure(self):
        """T12: 单笔撤单失败 (重试后仍失败), success=False."""
        orders = {"o001": make_order("o001", "PENDING")}
        broker = make_mock_broker(orders)
        broker.cancel.return_value = False  # 撤单始终失败
        guard = GlobalCancelGuard(max_retries=1, retry_delay_sec=0.01)

        result = guard.cancel_all_orders(broker)

        assert result.success is False
        assert result.cancelled_count == 0
        assert result.failed_count == 1
        assert len(result.failed_order_ids) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_cancel_exception_treated_as_failure(self):
        """T12: broker.cancel 抛异常时视为失败."""
        orders = {"o001": make_order("o001", "PENDING")}
        broker = make_mock_broker(orders)
        broker.cancel.side_effect = ConnectionError("broker timeout")
        guard = GlobalCancelGuard(max_retries=0)

        result = guard.cancel_all_orders(broker)

        assert result.success is False
        assert result.failed_count == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_partial_failure(self):
        """T12: 部分撤单失败, success=False."""
        orders = {
            "o001": make_order("o001", "PENDING"),
            "o002": make_order("o002", "PENDING"),
        }
        broker = make_mock_broker(orders)
        broker.cancel.side_effect = [True, False]  # 第1笔成功, 第2笔失败
        guard = GlobalCancelGuard(max_retries=0)

        result = guard.cancel_all_orders(broker)

        assert result.success is False
        assert result.cancelled_count == 1
        assert result.failed_count == 1


# ============================================================
# 5. T12: 按 symbols 过滤
# ============================================================
class TestT12SymbolFilter:
    """T12: 按 symbols 过滤撤单."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_cancel_only_specified_symbols(self):
        """T12: 只撤销指定标的的订单."""
        orders = {
            "o001": make_order("o001", "PENDING", "510300"),
            "o002": make_order("o002", "PENDING", "588080"),
            "o003": make_order("o003", "PENDING", "510300"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker, symbols=["510300"])

        assert result.cancelled_count == 2, "只撤销 510300 的订单"
        assert result.skipped_count == 1, "588080 被跳过"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_cancel_empty_symbols_list(self):
        """T12: symbols=[] (空列表) 时不匹配任何标的, 全部跳过."""
        orders = {
            "o001": make_order("o001", "PENDING", "510300"),
        }
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        # symbols=[] (空列表, 非 None) - 不匹配任何 symbol
        result = guard.cancel_all_orders(broker, symbols=[])

        # 空列表意味着: 订单的 symbol 不在 [] 中, 全部跳过
        assert result.cancelled_count == 0
        assert result.skipped_count == 1


# ============================================================
# 6. T12: 触发原因记录
# ============================================================
class TestT12TriggerReason:
    """T12: 触发原因记录."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_manual_trigger_reason(self):
        """T12: 人工触发记录 trigger_reason=manual."""
        broker = make_mock_broker({})
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker, trigger_reason="manual")
        assert result.trigger_reason == "manual"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_auto_data_source_down_reason(self):
        """T12: 数据源中断自动触发."""
        broker = make_mock_broker({})
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker, trigger_reason="auto_data_source_down")
        assert result.trigger_reason == "auto_data_source_down"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_auto_market_crash_reason(self):
        """T12: 市场崩盘自动触发."""
        broker = make_mock_broker({})
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker, trigger_reason="auto_market_crash")
        assert result.trigger_reason == "auto_market_crash"


# ============================================================
# 7. T12: 订单对象格式兼容
# ============================================================
class TestT12OrderFormat:
    """T12: 订单对象格式兼容 (dict / object)."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t12_dict_orders(self):
        """T12: dict 格式订单."""
        orders = {"o001": {"order_id": "o001", "status": "PENDING", "symbol": "510300"}}
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)
        assert result.cancelled_count == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t12_list_orders(self):
        """T12: list 格式订单."""
        orders = [
            {"order_id": "o001", "status": "PENDING", "symbol": "510300"},
            {"order_id": "o002", "status": "FILLED", "symbol": "510300"},
        ]
        broker = make_mock_broker(orders)
        guard = GlobalCancelGuard()

        result = guard.cancel_all_orders(broker)
        assert result.total_orders == 2
        assert result.cancelled_count == 1
        assert result.skipped_count == 1
