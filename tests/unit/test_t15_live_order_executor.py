"""T15 单元测试 — LiveOrderExecutor 实盘下单编排器."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from utils.risk.intraday_circuit_breaker import IntradayCircuitBreaker
from utils.risk.kill_switch_manager import KillSwitchManager
from utils.risk.live_order_executor import (
    LiveExecutionResult,
    LiveOrderExecutor,
)
from utils.risk.position_limit_enforcer import PositionLimitEnforcer
from utils.risk.pretrade_guard import PreTradeGuard
from utils.risk.risk_audit_logger import RiskAuditLogger

# ============================================================
# 测试夹具
# ============================================================

@dataclass
class MockSlice:
    slice_idx: int = 0
    target_shares: int = 100
    limit_price: float = 10.0


@dataclass
class MockPlan:
    plan_id: str = "test_plan"
    symbol: str = "sh600000"
    side: str = "buy"
    slices: list[MockSlice] = field(default_factory=lambda: [MockSlice()])


class MockBroker:
    """模拟 broker, 可配置返回状态."""

    def __init__(self, is_live: bool = True, fill_qty: int = 100, avg_price: float = 10.0):
        self.is_live = is_live
        self.name = "mock_broker"
        self._fill_qty = fill_qty
        self._avg_price = avg_price
        self._next_id = 0
        self.place_order_called = 0
        self._last_qty = 0

    def place_order(self, symbol: str, side: str, qty: int, price: float, order_type: str = "limit") -> str:
        self.place_order_called += 1
        self._next_id += 1
        self._last_qty = qty
        return f"broker_order_{self._next_id}"

    def cancel_order(self, broker_order_id: str) -> bool:
        return True

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        # 返回最近一次下单的请求数量作为成交数量
        return {"state": "FILLED", "filled_qty": self._last_qty, "avg_price": self._avg_price}


class FailingBroker(MockBroker):
    """下单抛异常的 broker."""

    def place_order(self, symbol: str, side: str, qty: int, price: float, order_type: str = "limit") -> str:
        raise RuntimeError("broker connection refused")


def _make_executor(
    broker: Any | None = None,
    guard: PreTradeGuard | None = None,
    enforcer: PositionLimitEnforcer | None = None,
    cb: IntradayCircuitBreaker | None = None,
    ksm: KillSwitchManager | None = None,
    audit: RiskAuditLogger | None = None,
    fills_store: Any = None,
) -> LiveOrderExecutor:
    import tempfile
    from pathlib import Path
    if audit is None:
        audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
    return LiveOrderExecutor(
        broker=broker or MockBroker(),
        pretrade_guard=guard or PreTradeGuard(),
        position_enforcer=enforcer or PositionLimitEnforcer(),
        circuit_breaker=cb or IntradayCircuitBreaker(),
        kill_switch=ksm or KillSwitchManager(),
        audit_logger=audit,
        fills_store=fills_store,
    )


# ============================================================
# 测试类
# ============================================================

class TestExecutorConstruction:
    def test_none_broker_raises(self):
        import tempfile
        from pathlib import Path
        audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
        with pytest.raises(ValueError, match="broker 不能为 None"):
            LiveOrderExecutor(
                broker=None,
                pretrade_guard=PreTradeGuard(),
                position_enforcer=PositionLimitEnforcer(),
                circuit_breaker=IntradayCircuitBreaker(),
                kill_switch=KillSwitchManager(),
                audit_logger=audit,
            )

    def test_default_construction(self):
        ex = _make_executor()
        assert ex.broker is not None
        assert ex.guard is not None


class TestNormalExecution:
    def test_single_slice_success(self):
        broker = MockBroker(fill_qty=100, avg_price=10.0)
        fills_mock = MagicMock()
        ex = _make_executor(broker=broker, fills_store=fills_mock)
        plan = MockPlan(slices=[MockSlice(slice_idx=0, target_shares=100, limit_price=10.0)])

        result = ex.execute_plan(plan)

        assert result.total_slices == 1
        assert result.submitted_count == 1
        assert result.filled_count == 1
        assert result.rejected_count == 0
        assert result.total_filled_shares == 100
        assert result.fully_filled
        assert broker.place_order_called == 1
        fills_mock.record_fill.assert_called_once()

    def test_multi_slice_success(self):
        broker = MockBroker(fill_qty=100)
        ex = _make_executor(broker=broker)
        plan = MockPlan(slices=[
            MockSlice(0, 100, 10.0),
            MockSlice(1, 200, 11.0),
            MockSlice(2, 100, 10.5),
        ])

        result = ex.execute_plan(plan)

        assert result.total_slices == 3
        assert result.submitted_count == 3
        assert result.filled_count == 3
        assert result.total_filled_shares == 400

    def test_zero_shares_rejected(self):
        ex = _make_executor()
        plan = MockPlan(slices=[MockSlice(0, 0, 10.0)])

        result = ex.execute_plan(plan)

        assert result.rejected_count == 1
        assert result.submitted_count == 0
        assert result.all_rejected


class TestT09GuardRejection:
    def test_lot_size_violation_rejected(self):
        """150 股 (非 100 整数倍) 应被 T09 拦截."""
        broker = MockBroker()
        ex = _make_executor(broker=broker)
        plan = MockPlan(slices=[MockSlice(0, 150, 10.0)])

        result = ex.execute_plan(plan)

        assert result.rejected_count == 1
        assert result.submitted_count == 0
        assert "T09" in result.slice_results[0].rejection_reason
        assert broker.place_order_called == 0  # broker 未被调用

    def test_st_buy_rejected(self):
        """ST 买入应被 T09 拦截."""
        broker = MockBroker()
        ex = _make_executor(broker=broker)
        plan = MockPlan(
            symbol="sh600001",
            side="buy",
            slices=[MockSlice(0, 100, 5.0)],
        )

        # 修改 guard 请求以包含 ST 名称
        original_check = ex.guard.check

        def st_check(req):
            req.symbol_name = "ST 某某"
            return original_check(req)

        ex.guard.check = st_check
        result = ex.execute_plan(plan)

        assert result.rejected_count == 1


class TestT12KillSwitchRejection:
    def test_kill_switch_blocks_buy(self):
        """T12 L1 级别应拦截买入."""
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)  # L1 CAUTION

        broker = MockBroker()
        ex = _make_executor(broker=broker, ksm=ksm)
        plan = MockPlan(side="buy", slices=[MockSlice(0, 100, 10.0)])

        result = ex.execute_plan(plan)

        assert result.rejected_count == 1
        assert "T12" in result.slice_results[0].rejection_reason

    def test_kill_switch_allows_sell(self):
        """T12 L1 允许卖出 (减仓)."""
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)  # L1

        broker = MockBroker()
        ex = _make_executor(broker=broker, ksm=ksm)
        plan = MockPlan(side="sell", slices=[MockSlice(0, 100, 10.0)])

        result = ex.execute_plan(plan)

        assert result.submitted_count == 1
        assert result.rejected_count == 0


class TestT11CircuitBreakerRejection:
    def test_cb_open_blocks_all(self):
        """T11 熔断器 OPEN 时拦截所有订单."""
        cb = IntradayCircuitBreaker()
        cb.trip("test")

        broker = MockBroker()
        ex = _make_executor(broker=broker, cb=cb)
        plan = MockPlan(slices=[MockSlice(0, 100, 10.0)])

        result = ex.execute_plan(plan)

        assert result.rejected_count == 1
        assert "T11" in result.slice_results[0].rejection_reason


class TestBrokerError:
    def test_broker_exception_handled(self):
        """broker.place_order 异常应被捕获, slice 标记 rejected."""
        broker = FailingBroker()
        ex = _make_executor(broker=broker)
        plan = MockPlan(slices=[MockSlice(0, 100, 10.0)])

        result = ex.execute_plan(plan)

        assert result.rejected_count == 1
        assert "broker.place_order 异常" in result.slice_results[0].rejection_reason


class TestFillsStoreIntegration:
    def test_fill_recorded_to_store(self):
        fills_mock = MagicMock()
        broker = MockBroker(fill_qty=100, avg_price=10.5)
        ex = _make_executor(broker=broker, fills_store=fills_mock)
        plan = MockPlan(slices=[MockSlice(0, 100, 10.0)])

        ex.execute_plan(plan)

        fills_mock.record_fill.assert_called_once()
        call_kwargs = fills_mock.record_fill.call_args
        assert call_kwargs.kwargs["symbol"] == "sh600000"
        assert call_kwargs.kwargs["filled_qty"] == 100.0
        assert call_kwargs.kwargs["avg_price"] == 10.5
        assert call_kwargs.kwargs["is_live"] is True

    def test_no_fills_store_no_error(self):
        """fills_store=None 时不报错."""
        broker = MockBroker()
        ex = _make_executor(broker=broker, fills_store=None)
        plan = MockPlan(slices=[MockSlice(0, 100, 10.0)])

        result = ex.execute_plan(plan)
        assert result.filled_count == 1


class TestLiveExecutionResult:
    def test_all_rejected_property(self):
        result = LiveExecutionResult(plan_id="test", total_slices=2, rejected_count=2)
        assert result.all_rejected

    def test_all_rejected_false_when_some_pass(self):
        result = LiveExecutionResult(plan_id="test", total_slices=2, rejected_count=1)
        assert not result.all_rejected

    def test_fully_filled_property(self):
        result = LiveExecutionResult(
            plan_id="test", total_slices=2,
            submitted_count=2, filled_count=2,
        )
        assert result.fully_filled
