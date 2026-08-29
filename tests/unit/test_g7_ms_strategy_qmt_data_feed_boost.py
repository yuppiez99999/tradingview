"""G7 boost: ms_strategy/src/data/qmt_data_feed.py 单元测试.

覆盖 QmtDataFeed 全部公开接口:
  - 订阅管理 (add/remove_symbols, 重订阅)
  - 连接与重连 (connect/disconnect/check_connection/_try_reconnect)
  - Tick 回调 (_on_tick, on_tick, on_disconnect)
  - 数据查询 (get_price/ohlc/volume/all_prices/tick_history/historical_data)
  - 诊断 (get_status/__repr__)
mock xtquant.xtdata, 覆盖核心路径与异常分支.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

import ms_strategy.src.data.qmt_data_feed as qmt_feed_mod  # noqa: E402
from ms_strategy.src.data.qmt_data_feed import (  # noqa: E402
    HEARTBEAT_TIMEOUT,
    MAX_RECONNECT_ATTEMPTS,
    QmtDataFeed,
)

# ============================================================
# mock xtdata fixture
# ============================================================


@pytest.fixture
def mock_xtdata(monkeypatch):
    """注入 mock xtdata 并设置 XTDATA_AVAILABLE=True."""
    mock = MagicMock(name="xtdata")
    mock.download_history_data = MagicMock()
    mock.subscribe_whole_quote = MagicMock()
    mock.unsubscribe_quote = MagicMock()
    mock.get_market_data = MagicMock()
    monkeypatch.setattr(qmt_feed_mod, "XTDATA_AVAILABLE", True)
    monkeypatch.setattr(qmt_feed_mod, "xtdata", mock, raising=False)
    return mock


@pytest.fixture
def no_sleep(monkeypatch):
    """禁止 time.sleep 避免测试等待."""
    monkeypatch.setattr(qmt_feed_mod.time, "sleep", lambda s: None)


# ============================================================
# 1. 初始化与属性
# ============================================================


class TestInit:
    def test_default_init(self):
        feed = QmtDataFeed()
        assert feed.is_connected is False
        assert feed.symbol_count == 0
        assert feed.total_ticks_received == 0

    def test_init_with_symbols(self):
        feed = QmtDataFeed(symbols=["510300.SH", "IF2507.CFFEX"])
        assert feed.symbol_count == 2

    def test_init_empty_list(self):
        feed = QmtDataFeed(symbols=[])
        assert feed.symbol_count == 0

    def test_repr(self):
        feed = QmtDataFeed(symbols=["X"])
        r = repr(feed)
        assert "QmtDataFeed" in r
        assert "symbols=1" in r


# ============================================================
# 2. 订阅管理
# ============================================================


class TestSubscription:
    def test_add_new_symbols_disconnected(self, mock_xtdata):
        feed = QmtDataFeed()
        n = feed.add_symbols(["A", "B"])
        assert n == 2
        assert feed.symbol_count == 2
        # 未连接不重订阅
        mock_xtdata.subscribe_whole_quote.assert_not_called()

    def test_add_duplicate_symbols(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        n = feed.add_symbols(["A", "B"])
        assert n == 1

    def test_add_symbols_when_connected_triggers_resubscribe(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        mock_xtdata.subscribe_whole_quote.reset_mock()
        feed.add_symbols(["B"])
        mock_xtdata.subscribe_whole_quote.assert_called_once()

    def test_remove_existing_symbols(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A", "B"])
        feed._prices["A"] = 4.5
        feed._tick_cache["A"] = deque(maxlen=1000)
        n = feed.remove_symbols(["A", "X"])
        assert n == 1
        assert feed.symbol_count == 1
        assert "A" not in feed._prices
        assert "A" not in feed._tick_cache

    def test_remove_nonexistent_symbols(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        n = feed.remove_symbols(["X"])
        assert n == 0


# ============================================================
# 3. 连接
# ============================================================


class TestConnect:
    def test_connect_success(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A", "B"])
        assert feed.connect() is True
        assert feed.is_connected is True
        assert feed._reconnect_count == 0
        # 下载历史数据被调用
        assert mock_xtdata.download_history_data.call_count == 2
        mock_xtdata.subscribe_whole_quote.assert_called_once()

    def test_connect_no_symbols(self, mock_xtdata):
        feed = QmtDataFeed()
        assert feed.connect() is True
        # 无标的时不订阅
        mock_xtdata.subscribe_whole_quote.assert_not_called()

    def test_connect_xtquant_unavailable(self, monkeypatch):
        monkeypatch.setattr(qmt_feed_mod, "XTDATA_AVAILABLE", False)
        feed = QmtDataFeed(symbols=["A"])
        assert feed.connect() is False
        assert feed.is_connected is False
        assert "xtquant" in feed._connection_error

    def test_connect_download_exception(self, mock_xtdata):
        mock_xtdata.download_history_data.side_effect = OSError("download err")
        feed = QmtDataFeed(symbols=["A"])
        assert feed.connect() is False
        assert feed.is_connected is False

    def test_connect_subscribe_exception(self, mock_xtdata):
        mock_xtdata.subscribe_whole_quote.side_effect = RuntimeError("sub err")
        feed = QmtDataFeed(symbols=["A"])
        assert feed.connect() is False
        assert feed.is_connected is False


# ============================================================
# 4. 断开
# ============================================================


class TestDisconnect:
    def test_disconnect_with_symbols(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A", "B"])
        feed.connect()
        feed.disconnect()
        assert feed.is_connected is False
        mock_xtdata.unsubscribe_quote.assert_called_once()

    def test_disconnect_no_symbols(self, mock_xtdata):
        feed = QmtDataFeed()
        feed.connect()
        feed.disconnect()
        assert feed.is_connected is False
        mock_xtdata.unsubscribe_quote.assert_not_called()

    def test_disconnect_unsubscribe_exception_swallowed(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        mock_xtdata.unsubscribe_quote.side_effect = RuntimeError("err")
        # 不应抛出
        feed.disconnect()
        assert feed.is_connected is False


# ============================================================
# 5. Tick 回调
# ============================================================


class TestOnTick:
    def test_on_tick_updates_prices(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick(
            [
                {
                    "code": "A",
                    "lastPrice": 4.5,
                    "open": 4.4,
                    "high": 4.6,
                    "low": 4.3,
                    "volume": 1000,
                    "amount": 4500.0,
                    "time": 123,
                }
            ]
        )
        assert feed.get_price("A") == pytest.approx(4.5)
        assert feed.get_volume("A") == 1000
        assert feed.total_ticks_received == 1

    def test_on_tick_ohlc(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick(
            [
                {
                    "code": "A",
                    "lastPrice": 4.5,
                    "open": 4.4,
                    "high": 4.6,
                    "low": 4.3,
                    "volume": 1000,
                }
            ]
        )
        ohlc = feed.get_ohlc("A")
        assert ohlc["open"] == pytest.approx(4.4)
        assert ohlc["high"] == pytest.approx(4.6)
        assert ohlc["low"] == pytest.approx(4.3)
        assert ohlc["close"] == pytest.approx(4.5)

    def test_on_tick_caches_tick_history(self, mock_xtdata):
        feed = QmtDataFeed()
        feed.add_symbols(["A"])
        feed._on_tick([{"code": "A", "lastPrice": 4.5, "volume": 100, "time": 1}])
        feed._on_tick([{"code": "A", "lastPrice": 4.6, "volume": 200, "time": 2}])
        history = feed.get_tick_history("A", n=10)
        assert len(history) == 2
        assert history[0]["price"] == pytest.approx(4.5)
        assert history[1]["price"] == pytest.approx(4.6)

    def test_on_tick_empty_data(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick([])
        assert feed.total_ticks_received == 0

    def test_on_tick_none_data(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick(None)  # type: ignore[arg-type]
        assert feed.total_ticks_received == 0

    def test_on_tick_missing_code_skipped(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick([{"lastPrice": 4.5}])  # 无 code
        assert feed.total_ticks_received == 0

    def test_on_tick_default_values(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed._on_tick([{"code": "A"}])  # 缺所有价格字段
        assert feed.get_price("A") == pytest.approx(0.0)
        assert feed.get_volume("A") == 0

    def test_on_tick_triggers_user_callback(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        cb = MagicMock()
        feed.on_tick(cb)
        feed._on_tick([{"code": "A", "lastPrice": 4.5}])
        cb.assert_called_once()

    def test_on_tick_callback_exception_swallowed(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        cb = MagicMock(side_effect=RuntimeError("cb err"))
        feed.on_tick(cb)
        # 不应抛出
        feed._on_tick([{"code": "A", "lastPrice": 4.5}])

    def test_on_tick_updates_heartbeat(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        old = feed._last_heartbeat
        time.sleep(0.01)
        feed._on_tick([{"code": "A", "lastPrice": 4.5}])
        assert feed._last_heartbeat > old

    def test_on_tick_exception_in_loop(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        # float(None) 会 TypeError, 但被捕获
        feed._on_tick([{"code": "A", "lastPrice": None}])  # type: ignore[dict-item]
        # 不应抛出

    def test_on_tick_decorator_returns_callback(self, mock_xtdata):
        feed = QmtDataFeed()
        cb = lambda d: None  # noqa: E731
        assert feed.on_tick(cb) is cb

    def test_on_disconnect_decorator(self, mock_xtdata):
        feed = QmtDataFeed()
        cb = lambda: None  # noqa: E731
        assert feed.on_disconnect(cb) is cb
        assert cb in feed._on_disconnect_callbacks


# ============================================================
# 6. 断线检测与重连
# ============================================================


class TestCheckConnection:
    def test_not_connected_returns_false(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        assert feed.check_connection() is False

    def test_connected_fresh_heartbeat_returns_true(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        assert feed.check_connection() is True

    def test_heartbeat_timeout_triggers_disconnect_callbacks(
        self, mock_xtdata, no_sleep
    ):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        # 模拟心跳超时
        feed._last_heartbeat = time.time() - (HEARTBEAT_TIMEOUT + 10)
        cb = MagicMock()
        feed.on_disconnect(cb)
        with patch.object(feed, "_try_reconnect", return_value=True):
            result = feed.check_connection()
        assert result is True
        cb.assert_called_once()

    def test_heartbeat_timeout_reconnect(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        feed._last_heartbeat = time.time() - (HEARTBEAT_TIMEOUT + 10)
        with patch.object(feed, "_try_reconnect", return_value=True) as m:
            result = feed.check_connection()
        m.assert_called_once()
        assert result is True

    def test_disconnect_callback_exception_swallowed(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        feed._last_heartbeat = time.time() - (HEARTBEAT_TIMEOUT + 10)
        cb = MagicMock(side_effect=RuntimeError("cb err"))
        feed.on_disconnect(cb)
        with patch.object(feed, "_try_reconnect", return_value=True):
            # 不应抛出
            feed.check_connection()


class TestTryReconnect:
    def test_reconnect_success(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed(symbols=["A"])
        assert feed._try_reconnect() is True
        assert feed.is_connected is True
        assert feed._reconnect_count == 0  # 成功后重置

    def test_reconnect_max_attempts(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed(symbols=["A"])
        feed._reconnect_count = MAX_RECONNECT_ATTEMPTS
        assert feed._try_reconnect() is False

    def test_reconnect_failure_recurses(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed(symbols=["A"])
        # 第一次订阅失败, 第二次成功
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("sub err")

        mock_xtdata.subscribe_whole_quote.side_effect = side_effect
        result = feed._try_reconnect()
        assert result is True
        assert call_count[0] == 2

    def test_reconnect_no_symbols(self, mock_xtdata, no_sleep):
        feed = QmtDataFeed()
        # 无标的, _resubscribe 直接 return, 重连成功
        assert feed._try_reconnect() is True


# ============================================================
# 7. 数据查询
# ============================================================


class TestQueries:
    def test_get_price_missing(self, mock_xtdata):
        feed = QmtDataFeed()
        assert feed.get_price("X") is None

    def test_get_ohlc_missing(self, mock_xtdata):
        feed = QmtDataFeed()
        ohlc = feed.get_ohlc("X")
        assert ohlc == {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0}

    def test_get_volume_missing(self, mock_xtdata):
        feed = QmtDataFeed()
        assert feed.get_volume("X") == 0

    def test_get_all_prices(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A", "B"])
        feed._prices["A"] = 4.5
        feed._prices["B"] = 3.5
        prices = feed.get_all_prices()
        assert prices == {"A": 4.5, "B": 3.5}
        # 返回副本
        prices["A"] = 0
        assert feed.get_price("A") == 4.5

    def test_get_tick_history_missing_symbol(self, mock_xtdata):
        feed = QmtDataFeed()
        assert feed.get_tick_history("X") == []

    def test_get_tick_history_n_limit(self, mock_xtdata):
        feed = QmtDataFeed()
        feed.add_symbols(["A"])
        for i in range(5):
            feed._on_tick([{"code": "A", "lastPrice": float(i), "time": i}])
        history = feed.get_tick_history("A", n=3)
        assert len(history) == 3
        assert history[0]["price"] == pytest.approx(2.0)

    def test_get_historical_data_success(self, mock_xtdata):
        mock_xtdata.get_market_data.return_value = {"close": {"A": [4.5]}}
        feed = QmtDataFeed()
        result = feed.get_historical_data("A")
        assert result == {"close": {"A": [4.5]}}

    def test_get_historical_data_exception(self, mock_xtdata):
        mock_xtdata.get_market_data.side_effect = OSError("err")
        feed = QmtDataFeed()
        assert feed.get_historical_data("A") is None


# ============================================================
# 8. 诊断
# ============================================================


class TestStatus:
    def test_get_status_disconnected(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A", "B"])
        status = feed.get_status()
        assert status["connected"] is False
        assert status["symbols_count"] == 2
        assert status["total_ticks"] == 0
        assert status["reconnect_count"] == 0
        assert status["connection_error"] == ""
        assert "cache_sizes" in status

    def test_get_status_connected(self, mock_xtdata):
        feed = QmtDataFeed(symbols=["A"])
        feed.connect()
        feed._on_tick([{"code": "A", "lastPrice": 4.5}])
        status = feed.get_status()
        assert status["connected"] is True
        assert status["total_ticks"] == 1
        assert status["seconds_since_heartbeat"] >= 0

    def test_get_status_cache_sizes(self, mock_xtdata):
        feed = QmtDataFeed()
        feed.add_symbols(["A"])
        feed._on_tick([{"code": "A", "lastPrice": 4.5}])
        status = feed.get_status()
        assert status["cache_sizes"]["A"] == 1
