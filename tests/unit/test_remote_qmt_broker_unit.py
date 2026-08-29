"""
test_remote_qmt_broker_unit — RemoteQmtBroker 单元测试 (mock HTTP)

覆盖:
- connect() 成功/失败/未配置
- place() 正常/网络异常/响应解析失败
- cancel() / wait_fill() / get_positions() / get_account_info()
- fail-open: 网络异常返回安全空值, 不抛
- is_connected 周期性探活
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.execution.remote_qmt_broker import RemoteQmtBroker

# ============================================================
# fixture: mock httpx.Client
# ============================================================


@pytest.fixture
def mock_client():
    """mock httpx.Client, 返回 (client, mock_get, mock_post)."""
    with patch("utils.execution.remote_qmt_broker.httpx.Client") as MockClient:
        client = MagicMock()
        MockClient.return_value = client
        mock_get = MagicMock()
        mock_post = MagicMock()
        client.get = mock_get
        client.post = mock_post
        yield client, mock_get, mock_post


def _ok(resp_data):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = resp_data
    return m


def _err(status=500, text="err"):
    m = MagicMock()
    m.status_code = status
    m.text = text
    return m


# ============================================================
# connect
# ============================================================


class TestConnect:
    def test_connect_success(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "800123"})
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t", timeout=5)
        assert b.connect() is True
        assert b.is_connected is True

    def test_connect_fail_gateway_down(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.return_value = _ok({"connected": False})
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        assert b.connect() is False
        assert b.is_connected is False

    def test_connect_fail_no_url(self, mock_client):
        b = RemoteQmtBroker(rpc_url="", token="t")
        assert b.connect() is False

    def test_connect_fail_no_token(self, mock_client):
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="")
        assert b.connect() is False

    def test_connect_http_error(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.side_effect = httpx.ConnectError("network down")
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        assert b.connect() is False


# ============================================================
# place
# ============================================================


class TestPlace:
    def _connected_broker(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "x"})
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b.connect()
        return b

    def test_place_success(self, mock_client):
        b = self._connected_broker(mock_client)
        _, _, mock_post = mock_client
        mock_post.return_value = _ok(
            {
                "order_id": "12345",
                "symbol": "510300.SH",
                "qty": 1000,
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 4.5,
                "status": "REPORTED",
                "ts": "2026-08-17T09:30:00",
            }
        )
        order = b.place("510300.SH", 1000, "BUY", price=4.5)
        assert order is not None
        assert order.order_id == "12345"
        assert order.symbol == "510300.SH"
        assert order.qty == 1000
        assert b.orders["12345"].order_id == "12345"

    def test_place_not_connected(self, mock_client):
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        assert b.place("510300.SH", 1000, "BUY") is None

    def test_place_http_error_fail_open(self, mock_client):
        b = self._connected_broker(mock_client)
        _, _, mock_post = mock_client
        mock_post.side_effect = httpx.ConnectError("timeout")
        # fail-open: 不抛, 返回 None
        assert b.place("510300.SH", 1000, "BUY") is None

    def test_place_bad_response_fail_open(self, mock_client):
        b = self._connected_broker(mock_client)
        _, _, mock_post = mock_client
        mock_post.return_value = _err(500, "internal")
        assert b.place("510300.SH", 1000, "BUY") is None


# ============================================================
# cancel
# ============================================================


class TestCancel:
    def test_cancel_success(self, mock_client):
        _, mock_get, mock_post = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "x"})
        mock_post.return_value = _ok({"cancelled": True, "order_id": "12345"})
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b.connect()
        from ms_strategy.src.execution.broker_api import Order

        order = Order(
            order_id="12345",
            symbol="510300.SH",
            qty=1000,
            side="BUY",
            order_type="LIMIT",
            price=4.5,
        )
        assert b.cancel(order) is True

    def test_cancel_fail_open(self, mock_client):
        _, mock_get, mock_post = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "x"})
        mock_post.side_effect = httpx.ConnectError("net")
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b.connect()
        from ms_strategy.src.execution.broker_api import Order

        order = Order(
            order_id="12345", symbol="", qty=0, side="", order_type="", price=0.0
        )
        assert b.cancel(order) is False


# ============================================================
# wait_fill
# ============================================================


class TestWaitFill:
    def test_wait_fill_filled(self, mock_client):
        _, mock_get, mock_post = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "x"})
        mock_post.return_value = _ok(
            {
                "filled": True,
                "order_id": "12345",
                "fill_id": "F1",
                "symbol": "510300.SH",
                "qty": 1000,
                "price": 4.502,
                "side": "BUY",
                "ts": "2026-08-17T09:30:05",
            }
        )
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b.connect()
        from ms_strategy.src.execution.broker_api import Order

        order = Order(
            order_id="12345",
            symbol="510300.SH",
            qty=1000,
            side="BUY",
            order_type="LIMIT",
            price=4.5,
        )
        fill = b.wait_fill(order, timeout=10)
        assert fill is not None
        assert fill["qty"] == 1000
        assert len(b.fills) == 1

    def test_wait_fill_unfilled(self, mock_client):
        _, mock_get, mock_post = mock_client
        mock_get.return_value = _ok({"connected": True, "account": "x"})
        mock_post.return_value = _ok({"filled": False, "order_id": "12345"})
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b.connect()
        from ms_strategy.src.execution.broker_api import Order

        order = Order(
            order_id="12345", symbol="", qty=0, side="", order_type="", price=0.0
        )
        assert b.wait_fill(order) is None


# ============================================================
# 持仓 / 账户
# ============================================================


class TestQuery:
    def test_get_positions(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.return_value = _ok(
            {"positions": {"510300.SH": 1000, "510500.SH": 500}}
        )
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b._connected = True
        b._client = MagicMock()
        b._client.get = mock_get
        b._last_health = time.time()
        pos = b.get_positions()
        assert pos == {"510300.SH": 1000, "510500.SH": 500}

    def test_get_account_info(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.return_value = _ok(
            {"available": 100000, "total": 500000, "frozen": 0, "margin": 0}
        )
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b._connected = True
        b._client = MagicMock()
        b._client.get = mock_get
        b._last_health = time.time()
        acc = b.get_account_info()
        assert acc["available"] == 100000
        assert acc["total"] == 500000

    def test_get_account_fail_open(self, mock_client):
        _, mock_get, _ = mock_client
        mock_get.side_effect = httpx.ConnectError("net")
        b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
        b._connected = True
        b._client = MagicMock()
        b._client.get = mock_get
        b._last_health = time.time()
        acc = b.get_account_info()
        assert acc == {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}


# ============================================================
# disconnect
# ============================================================


def test_disconnect(mock_client):
    _, mock_get, _ = mock_client
    mock_get.return_value = _ok({"connected": True, "account": "x"})
    b = RemoteQmtBroker(rpc_url="http://win:8765", token="t")
    b.connect()
    assert b.disconnect() is True
    assert b.is_connected is False
