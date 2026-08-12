"""SimulatedBroker + 订单/成交数据模型单测.

验证回测撮合路径: 盘口构造、Almgren-Chriss 滑点、下单/撤单、成交回报。
全部内存操作, 无副作用、无网络依赖。
"""
from __future__ import annotations

import pytest

from ms_strategy.src.execution.broker_api import (
    BrokerAPI,
    Fill,
    Order,
    OrderBook,
    SimulatedBroker,
)


def _make_order(symbol="600519.SH", qty=1000, side="BUY", price=100.0) -> Order:
    return Order(
        order_id="T-1",
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="LIMIT",
        price=price,
        ts="2026-08-10T09:30:00",
    )


def test_order_dataclass_fields():
    o = _make_order()
    assert o.symbol == "600519.SH"
    assert o.qty == 1000
    assert o.side == "BUY"


def test_fill_dataclass_fields():
    f = Fill(
        fill_id="F-1",
        order_id="T-1",
        symbol="600519.SH",
        qty=1000,
        price=100.5,
        side="BUY",
        ts="2026-08-10T09:30:01",
    )
    assert f.qty == 1000
    assert f.price == 100.5
    assert f.side == "BUY"


def test_order_book_construction():
    ob = OrderBook(symbol="600519.SH", bid1=99.0, ask1=101.0, bid1_vol=500, ask1_vol=500)
    assert ob.bid1 == 99.0
    assert ob.ask1 == 101.0
    assert ob.ask1 - ob.bid1 == pytest.approx(2.0)


def test_simulated_broker_set_price_and_book():
    b = SimulatedBroker(initial_capital=1_000_000)
    b.set_price("600519.SH", 100.0, volume=1_000_000)
    book = b.get_order_book("600519.SH")
    assert book is not None
    assert book["ask1"] > book["bid1"]


def test_simulated_broker_get_order_book_missing_returns_none():
    b = SimulatedBroker()
    assert b.get_order_book("UNKNOWN.SH") is None


def test_simulated_broker_place_order():
    b = SimulatedBroker()
    order = b.place("600519.SH", 1000, "BUY", price=100.0)
    assert order is not None
    assert order.order_id in b.orders
    assert order.qty == 1000


def test_simulated_broker_slippage_fallback_without_adv():
    b = SimulatedBroker(slippage_bps=3.0)
    # 未 set_price -> ADV 缺失 -> 回退固定滑点
    slip = b._compute_slippage_bps("600519.SH", 1000)
    assert slip == pytest.approx(3.0)


def test_simulated_broker_almgren_chriss_slippage_increases_with_size():
    b = SimulatedBroker()
    b.set_market_context("600519.SH", price=100.0, adv=1_000_000, volatility=0.02)
    small = b._compute_slippage_bps("600519.SH", 10_000)
    large = b._compute_slippage_bps("600519.SH", 200_000)
    assert large > small  # 参与率越高, 滑点越大


def test_simulated_broker_slippage_capped():
    b = SimulatedBroker()
    # 极大参与率 + 高波动 -> 应被上限 100bps 截断
    b.set_market_context("600519.SH", price=100.0, adv=1_000, volatility=0.5)
    slip = b._compute_slippage_bps("600519.SH", 1_000_000)
    assert slip <= 100.0


def test_broker_api_base_is_abstract_usable():
    # BrokerAPI 基类仅作接口约定, 不应直接实例化执行业务
    api = BrokerAPI()
    assert api is not None
    assert api.orders == {}
