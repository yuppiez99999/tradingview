"""ms_strategy.src.execution.smart_order_router 单元测试 — MockBroker + SOR 熔断/暂停/snapshot"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ms_strategy.src.execution.smart_order_router import (
    BrokerAPI,
    MockBroker,
    OrderFill,
    SmartOrderRouter,
)
from utils.datetime_utils import now_utc_naive


class _MockNTP:
    """避免网络依赖的 NTP mock"""

    def __init__(self):
        self.offset_seconds = 0.001

    def server_ts(self) -> datetime:
        return now_utc_naive()

    def local_ts(self) -> datetime:
        return now_utc_naive()

    def get_offset(self) -> float:
        return self.offset_seconds

    def sync(self) -> bool:
        return True

    def snapshot(self) -> dict:
        return {"offset_seconds": self.offset_seconds, "healthy": True}


class _AccountBroker(BrokerAPI):
    """在 MockBroker 基础上补充 get_account_info 供 execute() 资金校验"""

    def __init__(self, available: float = 1e9, slip: float = 0.0):
        self._mock = MockBroker(price_dict={"510300.SH": 4.0})
        self._available = available
        self._slip = slip  # 强制滑点 (用于熔断测试)

    def get_order_book(self, symbol, levels=5):
        return self._mock.get_order_book(symbol, levels)

    def place(self, *a, **k):
        return self._mock.place(*a, **k)

    def wait_fill(self, order_id, timeout=30):
        fill = self._mock.wait_fill(order_id, timeout)
        if fill and self._slip:
            # 强制成交价偏离 (模拟真实滑点 > 熔断阈值)
            fill = dict(fill)
            base = float(fill.get("price", 4.0))
            fill["price"] = base * (1.0 + self._slip)
        return fill

    def cancel(self, *a, **k):
        return self._mock.cancel(*a, **k)

    def get_account_info(self) -> dict:
        return {"available": self._available}


def _router(
    available: float = 1e9, ntp=None, slip: float = 0.0, **kw
) -> SmartOrderRouter:
    return SmartOrderRouter(
        broker=_AccountBroker(available, slip=slip),
        ntp=ntp or _MockNTP(),  # type: ignore[arg-type]
        **kw,
    )


def test_mock_broker_order_book_nonzero():
    """MockBroker.get_order_book 返回五档非空盘口"""
    mb = MockBroker(price_dict={"X": 4.0})
    book = mb.get_order_book("X")
    assert book["bid1"] > 0
    assert book["ask1"] > book["bid1"]
    assert "bid1_vol" in book and "ask1_vol" in book


def test_mock_broker_fill_slippage():
    """MockBroker.wait_fill 的成交价含 ±0.05% 滑点 (BUY 上浮)"""
    mb = MockBroker(price_dict={"X": 4.000})
    oid = mb.place("X", 1000, "BUY", price=4.000)
    fill = mb.wait_fill(oid)
    assert fill is not None
    assert fill["price"] == pytest.approx(4.000 * 1.0005, abs=1e-3)


def test_execute_buy_iceberg_fills():
    """SOR.execute BUY: iceberg 拆单后产生成交回报, 滑点 < 熔断阈值"""
    r = _router()
    fills = r.execute(
        "510300.SH", target_qty=5000, side="BUY", decision_price=4.000, algo="ICEBERG"
    )
    assert len(fills) > 0
    assert all(f.status == "FILLED" for f in fills)
    assert sum(f.fill_qty for f in fills) == 5000


def test_execute_zero_qty_returns_empty():
    """target_qty <= 0 时立即返回空列表"""
    r = _router()
    assert r.execute("510300.SH", target_qty=0, side="BUY", decision_price=4.0) == []


def test_execute_option_sell_margin_block():
    """期权卖方裸卖空保证金不足 → OPTION_BLOCKED 熔断"""
    r = _router(available=1000.0)  # 极低收入
    fills = r.execute(
        "510050C2609M03500.SH", target_qty=10, side="SELL", decision_price=0.05
    )
    assert len(fills) == 1
    assert fills[0].status == "OPTION_BLOCKED"


def test_execute_paused_symbol_skipped():
    """标的处于暂停状态 → 返回 PAUSED 状态 (不撮合)"""
    r = _router()
    # 人为注入暂停 (模拟日内累计滑点超限)
    r.slip_per_symbol["510300.SH"] = 0.5
    r.slip_pause_until["510300.SH"] = now_utc_naive() + timedelta(minutes=30)
    fills = r.execute("510300.SH", target_qty=1000, side="BUY", decision_price=4.0)
    assert len(fills) == 1
    assert fills[0].status == "PAUSED"


def test_execute_slippage_break_circuit():
    """单笔滑点 > 熔断阈值 → SLIPPAGE_BREAK 并停止后续切片"""
    # 强制 1% 滑点 (> 0.1% 熔断阈值)
    r = _router(slip=0.01, slippage_break=0.001)
    fills = r.execute(
        "510300.SH", target_qty=5000, side="BUY", decision_price=4.000, algo="ICEBERG"
    )
    assert any(f.status == "SLIPPAGE_BREAK" for f in fills)


def test_snapshot_structure():
    """snapshot() 返回含 ntp/global_slowdown/paused_symbols/total_fills 的字典"""
    r = _router()
    r.execute("510300.SH", target_qty=1000, side="BUY", decision_price=4.0)
    snap = r.snapshot()
    assert "ntp" in snap
    assert "global_slowdown" in snap
    assert "paused_symbols" in snap
    assert "total_fills" in snap
    assert snap["total_fills"] > 0


def test_order_fill_dataclass():
    """OrderFill 数据类字段完整性"""
    of = OrderFill(
        order_id="O1",
        symbol="X",
        side="BUY",
        fill_price=4.0,
        fill_qty=100,
        decision_price=4.0,
        slippage=0.0,
        server_ts=now_utc_naive(),
        local_ts=now_utc_naive(),
        ntp_offset=0.0,
        status="FILLED",
    )
    assert of.status == "FILLED"
    assert of.fill_qty == 100


def test_broker_api_protocol_compile():
    """BrokerAPI 协议类型可引用 (仅类型标记)"""
    assert BrokerAPI is not None
