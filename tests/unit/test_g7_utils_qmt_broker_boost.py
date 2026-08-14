"""G7 boost: utils/qmt_broker.py 单元测试.

utils/qmt_broker.py 是 re-export shim, 真正实现在 ms_strategy/src/execution/qmt_broker.py.
本测试覆盖:
  1. shim re-export 行为 (上游 API 可通过 utils.qmt_broker 访问)
  2. 上游 QmtBrokerAPI 核心路径与异常分支 (mock xtquant)
     - connect/disconnect/is_connected
     - place (LIMIT/MARKET/FAK/FOK + BUY/SELL + 未连接 + 异常)
     - cancel (成功/失败/未连接)
     - wait_fill (成交/撤单/超时/未连接)
     - 回调 (_on_xt_callback/_on_order_update/_on_trade/_on_account_update)
     - get_account_info (缓存命中/未命中/异常)
     - get_positions / get_available_funds / check_funds_sufficient
     - get_order_book / get_volume_profile
"""
from __future__ import annotations

import importlib
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.execution.broker_api import Order  # noqa: E402

# ============================================================
# 1. shim re-export
# ============================================================


class TestQmtBrokerShim:
    """utils/qmt_broker.py 是 re-export shim, 验证上游 API 可访问."""

    def test_shim_imports_successfully(self):
        import utils.qmt_broker as shim  # noqa: F401
        assert shim is not None

    def test_shim_marks_itself_as_shim(self):
        import utils.qmt_broker as shim
        assert getattr(shim, "__file_shim__", False) is True

    def test_shim_exposes_upstream_path(self):
        import utils.qmt_broker as shim
        assert hasattr(shim, "__upstream_path__")
        assert "qmt_broker.py" in shim.__upstream_path__

    def test_shim_reexports_qmt_order_status(self):
        import utils.qmt_broker as shim
        assert hasattr(shim, "QMT_ORDER_STATUS")
        assert shim.QMT_ORDER_STATUS[55] == "ALL_TRADED"

    def test_shim_reexports_qmt_broker_api_class(self):
        import utils.qmt_broker as shim
        assert hasattr(shim, "QmtBrokerAPI")

    def test_shim_reexports_xtquant_available_flag(self):
        import utils.qmt_broker as shim
        assert hasattr(shim, "XTQUANT_AVAILABLE")
        # xtquant 未安装时为 False
        assert shim.XTQUANT_AVAILABLE in (True, False)


# ============================================================
# mock xtquant fixture
# ============================================================


@pytest.fixture
def mock_xtquant(monkeypatch):
    """注入 mock xtquant 模块并 reload 上游 qmt_broker, 使 XTQUANT_AVAILABLE=True."""
    xtquant_mod = types.ModuleType("xtquant")
    xtconstant_mod = types.ModuleType("xtquant.xtconstant")
    xtdata_mod = types.ModuleType("xtquant.xtdata")
    xttrader_mod = types.ModuleType("xtquant.xttrader")

    # xtconstant 常量
    xtconstant_mod.MARKET_ORDER = 0
    xtconstant_mod.LIMIT_ORDER = 1
    xtconstant_mod.FAK_ORDER = 2
    xtconstant_mod.FOK_ORDER = 3
    xtconstant_mod.STOCK_BUY = 0
    xtconstant_mod.STOCK_SELL = 1

    # xttrader.XtQuantTrader mock 类
    class MockXtQuantTrader:
        def __init__(self, path="", session_id=0, account_type="STOCK"):
            self.path = path
            self.session_id = session_id
            self.account_type = account_type
            self.registered_cb = None
            self.started = False
            self.connect_result = 0
            self.subscribed = None
            self.stopped = False

        def register_callback(self, cb):
            self.registered_cb = cb

        def start(self):
            self.started = True

        def connect(self):
            return self.connect_result

        def subscribe(self, account_id):
            self.subscribed = account_id

        def stop(self):
            self.stopped = True

        def orderStock(self, **kwargs):  # noqa: N802
            return kwargs.get("stockCode", "X") + "_oid"

        def cancelOrder(self, account_id, order_id):  # noqa: N802
            return 0

        def queryOrder(self, account_id):  # noqa: N802
            return []

        def queryAsset(self, account_id):  # noqa: N802
            return None

        def queryPosition(self, account_id):  # noqa: N802
            return []

    xttrader_mod.XtQuantTrader = MockXtQuantTrader

    monkeypatch.setitem(sys.modules, "xtquant", xtquant_mod)
    monkeypatch.setitem(sys.modules, "xtquant.xtconstant", xtconstant_mod)
    monkeypatch.setitem(sys.modules, "xtquant.xtdata", xtdata_mod)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", xttrader_mod)

    import ms_strategy.src.execution.qmt_broker as qmt_broker_mod
    importlib.reload(qmt_broker_mod)

    yield {
        "xtconstant": xtconstant_mod,
        "xtdata": xtdata_mod,
        "xttrader": xttrader_mod,
        "MockXtQuantTrader": MockXtQuantTrader,
        "module": qmt_broker_mod,
    }

    # 恢复: reload 回去 (xtquant 不可用, 降级为 XTQUANT_AVAILABLE=False)
    try:
        importlib.reload(qmt_broker_mod)
    except Exception:  # noqa: BLE001
        pass


def _make_broker(mock_xtquant, **kwargs):
    """用 mock xtquant 构造已连接的 QmtBrokerAPI."""
    broker_cls = mock_xtquant["module"].QmtBrokerAPI  # noqa: N806
    broker = broker_cls(account_id="acc1", session_id=1, **kwargs)
    return broker


# ============================================================
# 2. QmtBrokerAPI 实例化与连接
# ============================================================


class TestQmtBrokerInit:
    def test_init_with_defaults(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        assert broker.account_id == "acc1"
        assert broker.min_cash_buffer == 5000.0
        assert broker.default_order_type == 1101
        assert broker.quick_trade == 2
        assert broker.is_connected is False

    def test_init_custom_params(self, mock_xtquant):
        broker_cls = mock_xtquant["module"].QmtBrokerAPI  # noqa: N806
        broker = broker_cls(
            account_id="A", session_id=2, account_type="FUTURE",
            path="/tmp", min_cash_buffer=1000.0,
            default_order_type=1102, quick_trade=1,
        )
        assert broker.account_type == "FUTURE"
        assert broker.path == "/tmp"
        assert broker.min_cash_buffer == 1000.0
        assert broker.default_order_type == 1102
        assert broker.quick_trade == 1

    def test_connect_success(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        assert broker.connect() is True
        assert broker.is_connected is True
        assert broker._connection_error == ""

    def test_connect_failure_nonzero_code(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        # 让 connect() 返回非 0
        original_connect = mock_xtquant["MockXtQuantTrader"].connect
        mock_xtquant["MockXtQuantTrader"].connect = lambda self: -1
        try:
            assert broker.connect() is False
            assert broker.is_connected is False
            assert "连接失败" in broker._connection_error
        finally:
            mock_xtquant["MockXtQuantTrader"].connect = original_connect

    def test_connect_exception_returns_false(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        mock_xtquant["MockXtQuantTrader"].register_callback = MagicMock(
            side_effect=RuntimeError("register failed")
        )
        assert broker.connect() is False
        assert broker.is_connected is False
        assert "register failed" in broker._connection_error

    def test_disconnect_clears_caches(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._callbacks["old"] = lambda d: None
        broker._pending_orders["old"] = MagicMock()
        broker._account_cache["x"] = 1
        broker._position_cache["x"] = {}
        assert broker.disconnect() is True
        assert broker.is_connected is False
        assert broker._callbacks == {}
        assert broker._pending_orders == {}
        assert broker._account_cache == {}
        assert broker._position_cache == {}
        assert broker._xt_trader is None

    def test_disconnect_without_trader(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        # 未连接直接 disconnect
        assert broker.disconnect() is True

    def test_disconnect_trader_stop_exception_swallowed(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.stop = MagicMock(side_effect=RuntimeError("stop err"))
        # 不应抛出
        assert broker.disconnect() is True


# ============================================================
# 3. place 下单
# ============================================================


class TestPlace:
    def test_place_limit_buy(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = broker.place("510300.SH", 100, "BUY", order_type="LIMIT", price=4.5)
        assert order is not None
        assert order.symbol == "510300.SH"
        assert order.qty == 100
        assert order.side == "BUY"
        assert order.status == "REPORTED"
        assert order.order_id in broker.orders

    def test_place_market_when_price_zero(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = broker.place("X.SH", 100, "BUY", price=0.0)
        assert order is not None
        assert order.order_type == "LIMIT"  # 默认

    def test_place_market_explicit(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = broker.place("X.SH", 100, "BUY", order_type="MARKET")
        assert order is not None

    def test_place_fak(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = broker.place("X.SH", 100, "BUY", order_type="FAK", price=4.5)
        assert order is not None

    def test_place_fok(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = broker.place("X.SH", 100, "SELL", order_type="FOK", price=4.5)
        assert order is not None
        assert order.side == "SELL"

    def test_place_not_connected_returns_none(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        # 未连接
        assert broker.place("X", 100, "BUY") is None

    def test_place_with_callback_registered(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        cb = MagicMock()
        order = broker.place("X.SH", 100, "BUY", price=4.5, callback=cb)
        assert order is not None
        assert broker._callbacks[order.order_id] is cb

    def test_place_exception_returns_none(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.orderStock = MagicMock(side_effect=RuntimeError("order err"))
        assert broker.place("X", 100, "BUY", price=4.5) is None


# ============================================================
# 4. cancel 撤单
# ============================================================


class TestCancel:
    def test_cancel_success(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        assert broker.cancel(order) is True

    def test_cancel_not_connected(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        assert broker.cancel(order) is False

    def test_cancel_failure_nonzero(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.cancelOrder = MagicMock(return_value=-1)
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        assert broker.cancel(order) is False

    def test_cancel_exception_returns_false(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.cancelOrder = MagicMock(side_effect=OSError("cancel err"))
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        assert broker.cancel(order) is False


# ============================================================
# 5. wait_fill 等待成交
# ============================================================


class TestWaitFill:
    def test_wait_fill_not_connected(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        assert broker.wait_fill(order, timeout=1) is None

    def test_wait_fill_all_traded(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        with patch.object(broker, "_query_order_detail", return_value={
            "status": 55, "traded_volume": 100, "traded_price": 4.52,
        }):
            result = broker.wait_fill(order, timeout=5)
        assert result is not None
        assert result["order_id"] == "1"
        assert result["qty"] == 100
        assert result["price"] == pytest.approx(4.52)

    def test_wait_fill_cancelled_status(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        with patch.object(broker, "_query_order_detail", return_value={
            "status": 57, "traded_volume": 0,
        }):
            result = broker.wait_fill(order, timeout=5)
        assert result is None

    def test_wait_fill_timeout(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = Order(order_id="1", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        with patch.object(broker, "_query_order_detail", return_value=None), \
             patch.object(broker, "cancel", return_value=True):
            result = broker.wait_fill(order, timeout=0.1)
        assert result is None


# ============================================================
# 6. 回调
# ============================================================


class TestCallbacks:
    def test_on_order_update_pending_order(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        order = Order(order_id="9", symbol="X", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        broker._pending_orders["9"] = order
        broker._on_order_update({"order_id": "9", "status": 55})
        assert order.status == "ALL_TRADED"

    def test_on_order_update_unknown_status(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._on_order_update({"order_id": "x", "status": 999})
        # 不应抛出, 无 pending order 匹配

    def test_on_order_update_triggers_callback(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        cb = MagicMock()
        broker._callbacks["5"] = cb
        broker._on_order_update({"order_id": "5", "status": 55})
        cb.assert_called_once()
        # 终态清理回调
        assert "5" not in broker._callbacks

    def test_on_order_update_callback_exception_swallowed(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        cb = MagicMock(side_effect=RuntimeError("cb err"))
        broker._callbacks["5"] = cb
        # 不应抛出
        broker._on_order_update({"order_id": "5", "status": 50})

    def test_on_trade_appends_fill(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._on_trade({
            "trade_id": "t1", "order_id": "o1", "code": "X.SH",
            "volume": 100, "price": 4.5, "direction": "BUY",
        })
        assert len(broker.fills) == 1
        assert broker.fills[0].symbol == "X.SH"
        assert broker.fills[0].qty == 100

    def test_on_account_update_caches(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        data = {"available": 100000.0, "total": 200000.0}
        broker._on_account_update(data)
        assert broker._account_cache == data
        assert broker._last_account_update > 0

    def test_on_xt_callback_routes_order(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        with patch.object(broker, "_on_order_update") as m:
            broker._on_xt_callback({"type": "order", "order_id": "1", "status": 50})
            m.assert_called_once()

    def test_on_xt_callback_routes_trade(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        with patch.object(broker, "_on_trade") as m:
            broker._on_xt_callback({"type": "trade"})
            m.assert_called_once()

    def test_on_xt_callback_routes_account(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        with patch.object(broker, "_on_account_update") as m:
            broker._on_xt_callback({"type": "account"})
            m.assert_called_once()

    def test_on_xt_callback_unknown_type(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        # 不应抛出
        broker._on_xt_callback({"type": "unknown"})

    def test_on_xt_callback_exception_swallowed(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        with patch.object(broker, "_on_order_update", side_effect=RuntimeError("x")):
            # 不应抛出
            broker._on_xt_callback({"type": "order"})


# ============================================================
# 7. 账户/持仓/资金
# ============================================================


class TestAccountAndPositions:
    def test_get_account_info_from_cache(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._account_cache = {"available": 100.0, "total": 200.0}
        broker._last_account_update = time.time()
        result = broker.get_account_info()
        assert result["available"] == 100.0

    def test_get_account_info_query_asset(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        asset = MagicMock()
        asset.available = 50000.0
        asset.total_asset = 100000.0
        asset.frozen = 5000.0
        asset.margin = 0.0
        asset.market_value = 95000.0
        broker._xt_trader.queryAsset = MagicMock(return_value=asset)
        result = broker.get_account_info()
        assert result["available"] == 50000.0
        assert result["total"] == 100000.0

    def test_get_account_info_query_returns_none(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.queryAsset = MagicMock(return_value=None)
        result = broker.get_account_info()
        assert result == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}

    def test_get_account_info_not_connected(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        result = broker.get_account_info()
        assert result == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}

    def test_get_account_info_exception(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.queryAsset = MagicMock(side_effect=OSError("query err"))
        result = broker.get_account_info()
        assert result == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}

    def test_get_positions(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        p1 = MagicMock(stock_code="510300.SH", volume=1000)
        p2 = MagicMock(stock_code="510050.SH", volume=500)
        p3 = MagicMock(stock_code="", volume=0)  # 过滤
        broker._xt_trader.queryPosition = MagicMock(return_value=[p1, p2, p3])
        result = broker.get_positions()
        assert result == {"510300.SH": 1000, "510050.SH": 500}

    def test_get_positions_not_connected(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        assert broker.get_positions() == {}

    def test_get_positions_exception(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.queryPosition = MagicMock(side_effect=RuntimeError("err"))
        assert broker.get_positions() == {}

    def test_get_available_funds(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._account_cache = {"available": 100000.0}
        broker._last_account_update = time.time()
        # 100000 - 5000 buffer = 95000
        assert broker.get_available_funds() == pytest.approx(95000.0)

    def test_get_available_funds_floor_zero(self, mock_xtquant):
        broker = _make_broker(mock_xtquant, min_cash_buffer=200000.0)
        broker._account_cache = {"available": 100.0}
        broker._last_account_update = time.time()
        assert broker.get_available_funds() == 0.0

    def test_check_funds_sufficient(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._account_cache = {"available": 100000.0}
        broker._last_account_update = time.time()
        ok, reason = broker.check_funds_sufficient(50000.0)
        assert ok is True
        assert reason == ""

    def test_check_funds_insufficient(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker._account_cache = {"available": 10000.0}
        broker._last_account_update = time.time()
        ok, reason = broker.check_funds_sufficient(100000.0)
        assert ok is False
        assert "资金不足" in reason


# ============================================================
# 8. 盘口与成交量 profile
# ============================================================


class TestOrderBookAndVolume:
    def test_get_order_book(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        tick_data = {"X.SH": {
            "bidPrice": [4.50, 4.49], "bidVol": [100, 200],
            "askPrice": [4.51, 4.52], "askVol": [150, 250],
            "volume": 10000,
        }}
        mock_xtquant["xtdata"].get_full_tick = MagicMock(return_value=tick_data)
        result = broker.get_order_book("X.SH")
        assert result is not None
        assert result["symbol"] == "X.SH"
        assert result["bid1"] == pytest.approx(4.50)
        assert result["ask1"] == pytest.approx(4.51)
        assert result["total_volume"] == 10000

    def test_get_order_book_symbol_not_in_tick(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        mock_xtquant["xtdata"].get_full_tick = MagicMock(return_value={})
        assert broker.get_order_book("X.SH") is None

    def test_get_order_book_exception(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        mock_xtquant["xtdata"].get_full_tick = MagicMock(side_effect=OSError("err"))
        assert broker.get_order_book("X.SH") is None

    def test_get_volume_profile(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        import numpy as np
        vol = np.array([100, 200, 300])
        mock_xtquant["xtdata"].get_market_data = MagicMock(return_value={
            "volume": {"X.SH": vol},
        })
        result = broker.get_volume_profile("X.SH", window_minutes=3)
        assert result is not None
        assert len(result) == 3
        assert sum(result) == pytest.approx(1.0)

    def test_get_volume_profile_total_zero(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        import numpy as np
        vol = np.array([0, 0, 0])
        mock_xtquant["xtdata"].get_market_data = MagicMock(return_value={
            "volume": {"X.SH": vol},
        })
        result = broker.get_volume_profile("X.SH", window_minutes=3)
        assert result is not None
        assert len(result) == 3
        assert all(v == pytest.approx(1.0 / 3) for v in result)

    def test_get_volume_profile_symbol_missing(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        mock_xtquant["xtdata"].get_market_data = MagicMock(return_value={
            "volume": {},
        })
        assert broker.get_volume_profile("X.SH") is None

    def test_get_volume_profile_exception(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        mock_xtquant["xtdata"].get_market_data = MagicMock(side_effect=RuntimeError("err"))
        assert broker.get_volume_profile("X.SH") is None


# ============================================================
# 9. _query_order_detail / _build_fill_dict
# ============================================================


class TestQueryOrderDetail:
    def test_query_order_detail_found(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        o = MagicMock()
        o.order_id = "42"
        o.order_status = 55
        o.traded_volume = 100
        o.traded_price = 4.5
        o.stock_code = "X.SH"
        broker._xt_trader.queryOrder = MagicMock(return_value=[o])
        result = broker._query_order_detail("42")
        assert result is not None
        assert result["status"] == 55
        assert result["traded_volume"] == 100

    def test_query_order_detail_not_found(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.queryOrder = MagicMock(return_value=[])
        assert broker._query_order_detail("missing") is None

    def test_query_order_detail_exception(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        broker.connect()
        broker._xt_trader.queryOrder = MagicMock(side_effect=OSError("err"))
        assert broker._query_order_detail("x") is None

    def test_build_fill_dict(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        order = Order(order_id="1", symbol="X.SH", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        result = broker._build_fill_dict(order, {"traded_price": 4.52, "traded_volume": 100})
        assert result["order_id"] == "1"
        assert result["fill_id"] == "QMT-1"
        assert result["price"] == pytest.approx(4.52)
        assert result["qty"] == 100

    def test_build_fill_dict_fallback_to_order_values(self, mock_xtquant):
        broker = _make_broker(mock_xtquant)
        order = Order(order_id="1", symbol="X.SH", qty=100, side="BUY",
                      order_type="LIMIT", price=4.5)
        result = broker._build_fill_dict(order, {})
        assert result["price"] == pytest.approx(4.5)
        assert result["qty"] == 100


# ============================================================
# 10. shim 边缘分支覆盖 (import 失败 / path insert)
# ============================================================


class TestShimEdgeCases:
    """覆盖 utils/qmt_broker.py shim 的 except ImportError 与 path insert 分支."""

    def test_shim_import_error_raises(self, monkeypatch):
        """覆盖 shim except ImportError 分支 (行 36-42)."""
        import importlib

        import utils.qmt_broker as shim
        # 让上游 import 失败: 将上游模块设为 None 触发 ImportError
        monkeypatch.setitem(sys.modules, "ms_strategy.src.execution.qmt_broker", None)
        with pytest.raises(ImportError):
            importlib.reload(shim)

    def test_shim_path_insert_branch(self, monkeypatch):
        """覆盖 shim sys.path.insert 分支 (行 27)."""
        import importlib

        import utils.qmt_broker as shim
        ms_dir = str(PROJECT_ROOT / "ms_strategy")
        # 临时移除所有 ms_strategy 路径实例
        removed = []
        while ms_dir in sys.path:
            sys.path.remove(ms_dir)
            removed.append(ms_dir)
        try:
            importlib.reload(shim)
        finally:
            if ms_dir not in sys.path:
                sys.path.insert(0, ms_dir)
            # reload 恢复正常状态
            importlib.reload(shim)
