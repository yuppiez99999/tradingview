"""G7 boost: ms_strategy/src/execution/broker_api.py 单元测试.

覆盖 BrokerAPI 抽象基类与 SimulatedBroker 模拟撮合全部公开接口,
包括 Almgren-Chriss 滑点模型、上下限保护、持仓/资金更新等核心路径与边界分支.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ms_strategy.src.execution.broker_api import (  # noqa: E402
    BrokerAPI,
    Fill,
    Order,
    OrderBook,
    SimulatedBroker,
)

# ============================================================
# 1. 数据模型
# ============================================================


class TestDataModels:
    def test_order_book_defaults(self):
        ob = OrderBook(symbol="X")
        assert ob.symbol == "X"
        assert ob.bid_volumes == [0] * 5
        assert ob.ask_volumes == [0] * 5

    def test_order_defaults(self):
        o = Order(
            order_id="1",
            symbol="X",
            qty=100,
            side="BUY",
            order_type="LIMIT",
            price=10.0,
        )
        assert o.status == "PENDING"
        assert o.filled_qty == 0
        assert o.avg_price == 0.0

    def test_fill(self):
        f = Fill(
            fill_id="1",
            order_id="1",
            symbol="X",
            qty=100,
            price=10.0,
            side="BUY",
            ts="2026-01-01",
        )
        assert f.qty == 100


# ============================================================
# 2. BrokerAPI 基类
# ============================================================


class TestBrokerAPIBase:
    def test_connect_disconnect(self):
        b = BrokerAPI()
        assert b.connect() is True
        assert b.is_connected is True
        assert b.disconnect() is True
        assert b.is_connected is False

    def test_get_positions_default(self):
        b = BrokerAPI()
        assert b.get_positions() == {}

    def test_get_volume_profile_default(self):
        b = BrokerAPI()
        assert b.get_volume_profile("X") is None

    def test_get_account_info_default(self):
        b = BrokerAPI()
        assert b.get_account_info() == {"available": 0, "total": 0, "margin": 0}

    def test_not_implemented_methods(self):
        b = BrokerAPI()
        with pytest.raises(NotImplementedError):
            b.get_order_book("X")
        with pytest.raises(NotImplementedError):
            b.place("X", 100, "BUY")
        with pytest.raises(NotImplementedError):
            b.cancel(MagicMock())
        with pytest.raises(NotImplementedError):
            b.wait_fill(MagicMock())


# ============================================================
# 3. SimulatedBroker 滑点模型
# ============================================================


class TestSlippage:
    def test_no_adv_fallback(self):
        b = SimulatedBroker()
        assert b._compute_slippage_bps("X", 100) == 2.0

    def test_zero_participation(self):
        b = SimulatedBroker()
        b.set_price("X", 10.0, volume=100000, volatility=0.02)
        assert b._compute_slippage_bps("X", 0) == 2.0

    def test_almgren_chriss(self):
        b = SimulatedBroker(slippage_coef=0.142, slippage_bps=2.0)
        b.set_market_context("X", 10.0, adv=100000, volatility=0.02)
        # participation=0.01, vol_scaling=1.0
        # slip = 0.142 * 0.02 * 0.1 * 1.0 * 10000 = 2.84
        slip = b._compute_slippage_bps("X", 1000)
        assert slip == pytest.approx(2.84, abs=0.01)

    def test_upper_cap_100(self):
        b = SimulatedBroker(slippage_coef=10.0)
        b.set_market_context("X", 10.0, adv=1000, volatility=0.5)
        slip = b._compute_slippage_bps("X", 1000)
        assert slip == 100.0

    def test_lower_floor(self):
        b = SimulatedBroker(slippage_coef=0.001, slippage_bps=2.0)
        b.set_market_context("X", 10.0, adv=1000000, volatility=0.001)
        slip = b._compute_slippage_bps("X", 100)
        assert slip >= 0.2


# ============================================================
# 4. SimulatedBroker 撮合
# ============================================================


class TestSimulatedBroker:
    def test_init_defaults(self):
        b = SimulatedBroker()
        assert b.capital == 5_000_000
        assert b.available == 5_000_000

    def test_set_price_default_volatility(self):
        b = SimulatedBroker()
        b.set_price("X", 10.0)
        assert b._volatilities["X"] == 0.02

    def test_set_market_context(self):
        b = SimulatedBroker()
        b.set_market_context("X", 10.0, adv=100000, volatility=0.03)
        assert b._prices["X"] == 10.0
        assert b._volumes["X"] == 100000
        assert b._volatilities["X"] == 0.03

    def test_get_order_book_with_price(self):
        b = SimulatedBroker()
        b.set_price("X", 10.0, volume=100000)
        ob = b.get_order_book("X")
        assert ob["symbol"] == "X"
        assert ob["bid1"] == pytest.approx(9.999)
        assert ob["ask1"] == pytest.approx(10.001)
        assert ob["total_volume"] == 100000

    def test_get_order_book_no_price(self):
        b = SimulatedBroker()
        assert b.get_order_book("X") is None

    def test_place(self):
        b = SimulatedBroker()
        order = b.place("X", 100, "BUY", price=10.0)
        assert order.symbol == "X"
        assert order.qty == 100
        assert order.order_id in b.orders

    def test_cancel_exists(self):
        b = SimulatedBroker()
        order = b.place("X", 100, "BUY")
        assert b.cancel(order) is True
        assert order.status == "CANCELLED"

    def test_cancel_not_exists(self):
        b = SimulatedBroker()
        order = Order(
            order_id="X",
            symbol="X",
            qty=100,
            side="BUY",
            order_type="LIMIT",
            price=10.0,
        )
        assert b.cancel(order) is False

    def test_wait_fill_buy(self):
        b = SimulatedBroker()
        b.set_price("X", 10.0, volume=100000)
        order = b.place("X", 100, "BUY", price=10.0)
        result = b.wait_fill(order)
        assert result is not None
        assert result["side"] == "BUY"
        assert order.status == "FILLED"
        assert b.positions["X"] > 0

    def test_wait_fill_sell(self):
        b = SimulatedBroker()
        b.set_price("X", 10.0, volume=100000)
        order = b.place("X", 100, "SELL", price=10.0)
        result = b.wait_fill(order)
        assert result is not None
        assert result["side"] == "SELL"
        assert b.positions["X"] < 0

    def test_wait_fill_no_price(self):
        b = SimulatedBroker()
        order = b.place("X", 100, "BUY", price=10.0)
        assert b.wait_fill(order) is None

    def test_get_volume_profile(self):
        b = SimulatedBroker()
        profile = b.get_volume_profile("X", window_minutes=30)
        assert len(profile) == 30
        assert sum(profile) == pytest.approx(1.0)

    def test_get_account_info(self):
        b = SimulatedBroker(initial_capital=1_000_000)
        info = b.get_account_info()
        assert info["total"] == 1_000_000
        assert info["available"] == 1_000_000
