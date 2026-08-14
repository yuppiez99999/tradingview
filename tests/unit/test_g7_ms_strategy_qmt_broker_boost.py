"""G7 boost: ms_strategy/src/execution/qmt_broker.py 单元测试.

覆盖 QmtBrokerAPI 连接/下单/撤单/轮询/盘口/账户/持仓全部公开接口,
mock xtquant (xttrader/xtconstant/xtdata), 包括未连接/异常/超时等分支.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import ms_strategy.src.execution.qmt_broker as qmt_mod  # noqa: E402
from ms_strategy.src.execution.qmt_broker import QMT_ORDER_STATUS  # noqa: E402


@pytest.fixture
def qmt_env():
    """patch xtquant 环境, yield (broker, xt_trader, xt_const, xt_data)."""
    with patch.object(qmt_mod, "XTQUANT_AVAILABLE", True), \
         patch.object(qmt_mod, "xttrader", MagicMock(), create=True) as xt_trader, \
         patch.object(qmt_mod, "xtconstant", MagicMock(), create=True) as xt_const, \
         patch.object(qmt_mod, "xtdata", MagicMock(), create=True) as xt_data:
        broker = qmt_mod.QmtBrokerAPI(account_id="800123", session_id=123)
        yield broker, xt_trader, xt_const, xt_data


def _connect(broker, xt_trader):
    """辅助: 连接 broker (mock connect 返回 0)."""
    mock_trader = xt_trader.XtQuantTrader.return_value
    mock_trader.connect.return_value = 0
    return broker.connect()


# ============================================================
# 1. 常量与构造
# ============================================================


class TestConstants:
    def test_qmt_order_status(self):
        assert QMT_ORDER_STATUS[55] == "ALL_TRADED"
        assert QMT_ORDER_STATUS[54] == "CANCELLED"
        assert QMT_ORDER_STATUS[48] == "NOT_REPORTED"
        assert QMT_ORDER_STATUS[53] == "PART_TRADED"

    def test_xtquant_unavailable_raises(self):
        """XTQUANT_AVAILABLE=False → RuntimeError."""
        with patch.object(qmt_mod, "XTQUANT_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="xtquant 未安装"):
                qmt_mod.QmtBrokerAPI()


# ============================================================
# 2. 连接管理
# ============================================================


class TestConnect:
    def test_connect_success(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        assert _connect(broker, xt_trader) is True
        assert broker.is_connected is True
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.register_callback.assert_called_once()
        mock_trader.start.assert_called_once()
        mock_trader.subscribe.assert_called_once_with("800123")

    def test_connect_failure_nonzero_code(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = -1
        assert broker.connect() is False
        assert "连接失败" in broker._connection_error

    def test_connect_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        xt_trader.XtQuantTrader.side_effect = RuntimeError("conn error")
        assert broker.connect() is False
        assert "conn error" in broker._connection_error

    def test_disconnect_clears_state(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        broker._callbacks["x"] = MagicMock()
        broker._pending_orders["x"] = MagicMock()
        assert broker.disconnect() is True
        assert broker._connected is False
        assert len(broker._callbacks) == 0
        assert len(broker._pending_orders) == 0
        assert broker._xt_trader is None

    def test_disconnect_stop_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.stop.side_effect = RuntimeError("stop fail")
        broker.connect()
        assert broker.disconnect() is True

    def test_is_connected_false_before_connect(self, qmt_env):
        broker, _, _, _ = qmt_env
        assert broker.is_connected is False


# ============================================================
# 3. 回调
# ============================================================


class TestCallbacks:
    def test_callback_order(self, qmt_env):
        broker, _, _, _ = qmt_env
        with patch.object(broker, "_on_order_update") as m:
            broker._on_xt_callback({"type": "order", "order_id": "1"})
            m.assert_called_once()

    def test_callback_trade(self, qmt_env):
        broker, _, _, _ = qmt_env
        with patch.object(broker, "_on_trade") as m:
            broker._on_xt_callback({"type": "trade"})
            m.assert_called_once()

    def test_callback_account(self, qmt_env):
        broker, _, _, _ = qmt_env
        with patch.object(broker, "_on_account_update") as m:
            broker._on_xt_callback({"type": "account"})
            m.assert_called_once()

    def test_callback_exception_swallowed(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._on_xt_callback(None)

    def test_on_order_update_pending(self, qmt_env):
        broker, _, _, _ = qmt_env
        order = MagicMock()
        broker._pending_orders["123"] = order
        broker._on_order_update({"order_id": "123", "status": 55})
        assert order.status == "ALL_TRADED"

    def test_on_order_update_triggers_callback(self, qmt_env):
        broker, _, _, _ = qmt_env
        cb = MagicMock()
        broker._callbacks["123"] = cb
        broker._on_order_update({"order_id": "123", "status": 55})
        cb.assert_called_once()

    def test_on_order_update_terminal_clears_callback(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._callbacks["123"] = MagicMock()
        broker._on_order_update({"order_id": "123", "status": 55})
        assert "123" not in broker._callbacks

    def test_on_order_update_unknown_status(self, qmt_env):
        broker, _, _, _ = qmt_env
        order = MagicMock()
        broker._pending_orders["123"] = order
        broker._on_order_update({"order_id": "123", "status": 999})
        assert order.status == "UNKNOWN_999"

    def test_on_trade_creates_fill(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._on_trade({
            "trade_id": "T1", "order_id": "O1", "code": "510300.SH",
            "volume": 100, "price": 4.5, "direction": "BUY",
        })
        assert len(broker.fills) == 1
        assert broker.fills[0].symbol == "510300.SH"
        assert broker.fills[0].qty == 100

    def test_on_account_update(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._on_account_update({"available": 100000})
        assert broker._account_cache == {"available": 100000}
        assert broker._last_account_update > 0


# ============================================================
# 4. 下单
# ============================================================


class TestPlace:
    def test_not_connected(self, qmt_env):
        broker, _, _, _ = qmt_env
        assert broker.place("X", 100, "BUY") is None

    def test_limit_success(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.return_value = "ORD123"
        broker.connect()
        order = broker.place("510300.SH", 100, "BUY", order_type="LIMIT", price=4.5)
        assert order is not None
        assert order.order_id == "ORD123"
        assert "ORD123" in broker.orders

    def test_market(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.return_value = "M1"
        broker.connect()
        order = broker.place("X", 100, "BUY", order_type="MARKET")
        assert order is not None

    def test_fak(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.return_value = "F1"
        broker.connect()
        order = broker.place("X", 100, "BUY", order_type="FAK", price=4.5)
        assert order is not None

    def test_fok(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.return_value = "F2"
        broker.connect()
        order = broker.place("X", 100, "SELL", order_type="FOK", price=4.5)
        assert order is not None

    def test_with_callback(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.return_value = "C1"
        broker.connect()
        cb = MagicMock()
        broker.place("X", 100, "BUY", price=10.0, callback=cb)
        assert "C1" in broker._callbacks

    def test_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.orderStock.side_effect = RuntimeError("order fail")
        broker.connect()
        assert broker.place("X", 100, "BUY", price=10.0) is None


# ============================================================
# 5. 撤单
# ============================================================


class TestCancel:
    def test_not_connected(self, qmt_env):
        broker, _, _, _ = qmt_env
        assert broker.cancel(MagicMock()) is False

    def test_success(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.cancelOrder.return_value = 0
        broker.connect()
        order = MagicMock()
        order.order_id = "O1"
        assert broker.cancel(order) is True

    def test_failure(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.cancelOrder.return_value = -1
        broker.connect()
        order = MagicMock()
        order.order_id = "O1"
        assert broker.cancel(order) is False

    def test_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        mock_trader = xt_trader.XtQuantTrader.return_value
        mock_trader.connect.return_value = 0
        mock_trader.cancelOrder.side_effect = RuntimeError("fail")
        broker.connect()
        order = MagicMock()
        order.order_id = "O1"
        assert broker.cancel(order) is False


# ============================================================
# 6. 等待成交
# ============================================================


class TestWaitFill:
    def test_not_connected(self, qmt_env):
        broker, _, _, _ = qmt_env
        assert broker.wait_fill(MagicMock()) is None

    def test_all_traded(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        detail = {"status": 55, "traded_volume": 100, "traded_price": 4.5}
        with patch.object(broker, "_query_order_detail", return_value=detail):
            order = MagicMock()
            order.order_id = "O1"
            order.symbol = "X"
            order.qty = 100
            order.side = "BUY"
            order.price = 4.5
            result = broker.wait_fill(order, timeout=2)
        assert result is not None
        assert result["qty"] == 100

    def test_cancelled(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        detail = {"status": 57, "traded_volume": 0}
        with patch.object(broker, "_query_order_detail", return_value=detail):
            order = MagicMock()
            order.order_id = "O1"
            result = broker.wait_fill(order, timeout=2)
        assert result is None

    def test_timeout(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        with patch.object(broker, "_query_order_detail", return_value=None), \
             patch.object(broker, "cancel", return_value=True) as mock_cancel:
            order = MagicMock()
            order.order_id = "O1"
            result = broker.wait_fill(order, timeout=1)
        assert result is None
        mock_cancel.assert_called_once()


# ============================================================
# 7. 查询订单详情
# ============================================================


class TestQueryOrderDetail:
    def test_found(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        mock_order = MagicMock()
        mock_order.order_id = "O1"
        mock_order.order_status = 55
        mock_order.traded_volume = 100
        mock_order.traded_price = 4.5
        mock_order.stock_code = "X"
        xt_trader.XtQuantTrader.return_value.queryOrder.return_value = [mock_order]
        detail = broker._query_order_detail("O1")
        assert detail is not None
        assert detail["status"] == 55
        assert detail["traded_volume"] == 100

    def test_not_found(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        xt_trader.XtQuantTrader.return_value.queryOrder.return_value = []
        assert broker._query_order_detail("X") is None

    def test_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        xt_trader.XtQuantTrader.return_value.queryOrder.side_effect = RuntimeError("q fail")
        assert broker._query_order_detail("X") is None

    def test_build_fill_dict(self, qmt_env):
        broker, _, _, _ = qmt_env
        order = MagicMock()
        order.order_id = "O1"
        order.symbol = "X"
        order.qty = 100
        order.side = "BUY"
        order.price = 4.5
        detail = {"traded_price": 4.52, "traded_volume": 100}
        fill = broker._build_fill_dict(order, detail)
        assert fill["order_id"] == "O1"
        assert fill["qty"] == 100
        assert fill["price"] == 4.52
        assert fill["fill_id"] == "QMT-O1"


# ============================================================
# 8. 盘口
# ============================================================


class TestGetOrderBook:
    def test_success(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_full_tick.return_value = {
            "X": {"bidPrice": [4.49], "bidVol": [100],
                  "askPrice": [4.51], "askVol": [200], "volume": 1000},
        }
        ob = broker.get_order_book("X")
        assert ob is not None
        assert ob["symbol"] == "X"
        assert ob["bid1"] == 4.49
        assert ob["ask1"] == 4.51

    def test_symbol_not_in_tick(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_full_tick.return_value = {}
        assert broker.get_order_book("X") is None

    def test_exception(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_full_tick.side_effect = RuntimeError("tick fail")
        assert broker.get_order_book("X") is None


# ============================================================
# 9. 账户/持仓
# ============================================================


class TestAccount:
    def test_cache_hit(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._account_cache = {"available": 100.0}
        broker._last_account_update = time.time()
        info = broker.get_account_info()
        assert info["available"] == 100.0

    def test_query_success(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        mock_asset = MagicMock()
        mock_asset.available = 100000.0
        mock_asset.total_asset = 200000.0
        mock_asset.frozen = 50000.0
        mock_asset.margin = 10000.0
        mock_asset.market_value = 150000.0
        xt_trader.XtQuantTrader.return_value.queryAsset.return_value = mock_asset
        info = broker.get_account_info()
        assert info["available"] == 100000.0
        assert info["total"] == 200000.0

    def test_not_connected_default(self, qmt_env):
        broker, _, _, _ = qmt_env
        info = broker.get_account_info()
        assert info == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}

    def test_exception_default(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        xt_trader.XtQuantTrader.return_value.queryAsset.side_effect = RuntimeError("fail")
        info = broker.get_account_info()
        assert info == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}


class TestPositions:
    def test_success(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        p1 = MagicMock()
        p1.stock_code = "X"
        p1.volume = 100
        p2 = MagicMock()
        p2.stock_code = "Y"
        p2.volume = 200
        xt_trader.XtQuantTrader.return_value.queryPosition.return_value = [p1, p2]
        assert broker.get_positions() == {"X": 100, "Y": 200}

    def test_empty(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        xt_trader.XtQuantTrader.return_value.queryPosition.return_value = []
        assert broker.get_positions() == {}

    def test_exception(self, qmt_env):
        broker, xt_trader, _, _ = qmt_env
        _connect(broker, xt_trader)
        xt_trader.XtQuantTrader.return_value.queryPosition.side_effect = RuntimeError("fail")
        assert broker.get_positions() == {}


class TestFunds:
    def test_available_funds(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._account_cache = {"available": 100000.0}
        broker._last_account_update = time.time()
        assert broker.get_available_funds() == 95000.0

    def test_check_sufficient_ok(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._account_cache = {"available": 100000.0}
        broker._last_account_update = time.time()
        ok, reason = broker.check_funds_sufficient(50000.0)
        assert ok is True
        assert reason == ""

    def test_check_insufficient(self, qmt_env):
        broker, _, _, _ = qmt_env
        broker._account_cache = {"available": 10000.0}
        broker._last_account_update = time.time()
        ok, reason = broker.check_funds_sufficient(50000.0)
        assert ok is False
        assert "资金不足" in reason


# ============================================================
# 10. 成交量 profile
# ============================================================


class TestVolumeProfile:
    def test_success(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        vol = np.array([100.0, 200.0, 300.0, 200.0, 100.0])
        xt_data.get_market_data.return_value = {"volume": {"X": vol}}
        profile = broker.get_volume_profile("X", window_minutes=5)
        assert profile is not None
        assert len(profile) == 5
        assert sum(profile) == pytest.approx(1.0)

    def test_no_data(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_market_data.return_value = None
        assert broker.get_volume_profile("X") is None

    def test_symbol_not_in_bars(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_market_data.return_value = {"volume": {}}
        assert broker.get_volume_profile("X") is None

    def test_zero_total_uniform(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        vol = np.zeros(5)
        xt_data.get_market_data.return_value = {"volume": {"X": vol}}
        profile = broker.get_volume_profile("X", window_minutes=5)
        assert profile is not None
        assert len(profile) == 5
        assert all(p == pytest.approx(1.0 / 5) for p in profile)

    def test_exception(self, qmt_env):
        broker, _, _, xt_data = qmt_env
        xt_data.get_market_data.side_effect = RuntimeError("fail")
        assert broker.get_volume_profile("X") is None
