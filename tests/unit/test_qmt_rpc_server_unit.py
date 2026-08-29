"""execution/qmt_rpc_server.py 单元测试 — QMT RPC 网关.

目标模块: utils/execution/qmt_rpc_server.py (0% → 高覆盖)
覆盖: 鉴权 / IP 白名单 / 账户脱敏 / 启动守卫 / QmtGateway / 各 endpoint / main
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from utils.execution import qmt_rpc_server as srv

# ============================================================
# MaskAccountTest — 账户脱敏
# ============================================================


class MaskAccountTest:

    def test_long_account_masks_head(self):
        assert srv._mask_account("1234567890") == "****7890"

    def test_short_account_full_mask(self):
        assert srv._mask_account("1234") == "****"

    def test_empty_account(self):
        assert srv._mask_account("") == "****"

    def test_none_account(self):
        assert srv._mask_account(None) == "****"

    def test_exact_5_chars(self):
        assert srv._mask_account("12345") == "****2345"


# ============================================================
# AssertSafeBindTest — 启动安全守卫
# ============================================================


class AssertSafeBindTest:

    def test_loopback_allowed(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        # 回环地址应放行 (不抛异常)
        srv._assert_safe_bind("127.0.0.1")
        srv._assert_safe_bind("localhost")
        srv._assert_safe_bind("::1")

    def test_non_loopback_without_whitelist_rejected(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        with pytest.raises(SystemExit):
            srv._assert_safe_bind("0.0.0.0")

    def test_non_loopback_with_whitelist_allowed(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1")
        # 不抛异常
        srv._assert_safe_bind("0.0.0.0")

    def test_non_loopback_with_allow_public(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.setenv("QMT_RPC_ALLOW_PUBLIC", "1")
        srv._assert_safe_bind("192.168.1.1")


# ============================================================
# QmtGatewayTest — 网关连接管理
# ============================================================


class QmtGatewayTest:

    def test_init_defaults(self):
        gw = srv.QmtGateway()
        assert gw.broker is None
        assert gw.last_reconnect == 0.0
        assert gw.reconnect_interval == 30.0

    def test_ensure_connected_no_broker_returns_false(self, monkeypatch):
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        gw = srv.QmtGateway()
        result = gw.ensure_connected()
        assert result is False

    def test_ensure_connected_with_connected_broker(self):
        gw = srv.QmtGateway()
        gw.broker = MagicMock()
        gw.broker.is_connected = True
        assert gw.ensure_connected() is True

    def test_ensure_connected_reconnect_throttle(self):
        """冷却期内不重连."""
        import time

        gw = srv.QmtGateway()
        gw.last_reconnect = time.time()  # 刚连过
        result = gw.ensure_connected()
        assert result is False

    def test_connect_missing_config(self, monkeypatch):
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        gw = srv.QmtGateway()
        assert gw._connect() is False

    def test_connect_import_error_handled(self, monkeypatch):
        monkeypatch.setenv("QMT_ACCOUNT_ID", "acc")
        monkeypatch.setenv("QMT_PATH", "/nonexistent")
        gw = srv.QmtGateway()
        # QmtBrokerAPI 导入会失败或连接失败 → 返回 False
        result = gw._connect()
        assert result is False
        assert gw.broker is None


# ============================================================
# VerifyTokenTest — Token 鉴权
# ============================================================


class VerifyTokenTest:

    def test_missing_env_raises_503(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_TOKEN", raising=False)
        with pytest.raises(Exception) as exc_info:
            srv._verify_token(None)
        assert "503" in str(exc_info.value.status_code)

    def test_invalid_token_raises_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret")
        with pytest.raises(Exception) as exc_info:
            srv._verify_token("wrong")
        assert "401" in str(exc_info.value.status_code)

    def test_none_token_raises_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret")
        with pytest.raises(Exception) as exc_info:
            srv._verify_token(None)
        assert "401" in str(exc_info.value.status_code)

    def test_valid_token_passes(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret")
        # 不抛异常
        srv._verify_token("secret")


# ============================================================
# VerifyIpTest — IP 白名单
# ============================================================


class VerifyIpTest:

    def test_no_whitelist_passes(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        req = MagicMock()
        srv._verify_ip(req)

    def test_allowed_ip_passes(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1,10.0.0.2")
        req = MagicMock()
        req.client.host = "10.0.0.1"
        srv._verify_ip(req)

    def test_disallowed_ip_rejected(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1")
        req = MagicMock()
        req.client.host = "99.99.99.99"
        with pytest.raises(Exception) as exc_info:
            srv._verify_ip(req)
        assert "403" in str(exc_info.value.status_code)


# ============================================================
# ApiEndpointTest — FastAPI 端点集成
# ============================================================


class ApiEndpointTest:
    """用 TestClient 测试各端点 (mock gateway 连接状态)."""

    @pytest.fixture(autouse=True)
    def _setup_token(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "test-token")

    def test_health_not_connected(self, monkeypatch):
        """gateway 未连接时 health 返回 connected=False."""
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        # 重置 gateway
        srv.gateway.broker = None
        srv.gateway.last_reconnect = 0.0
        with TestClient(srv.app) as client:
            resp = client.get("/health", headers={"X-Token": "test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert "account" in data

    def test_health_missing_token(self):
        with TestClient(srv.app) as client:
            resp = client.get("/health")
        assert resp.status_code == 401

    def test_health_wrong_token(self):
        with TestClient(srv.app) as client:
            resp = client.get("/health", headers={"X-Token": "wrong"})
        assert resp.status_code == 401

    def test_order_not_connected(self, monkeypatch):
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        srv.gateway.broker = None
        srv.gateway.last_reconnect = 0.0
        with TestClient(srv.app) as client:
            resp = client.post(
                "/order",
                json={"symbol": "510300", "qty": 100, "side": "buy"},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 503

    def test_order_success(self, monkeypatch):
        """mock gateway 已连接 + broker.place 成功."""
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_order = MagicMock()
        mock_order.order_id = "ORD1"
        mock_order.symbol = "510300"
        mock_order.qty = 100
        mock_order.side = "buy"
        mock_order.order_type = "LIMIT"
        mock_order.price = 10.0
        mock_order.status = "submitted"
        mock_order.ts = "2026-01-01"
        mock_broker.place.return_value = mock_order
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/order",
                json={"symbol": "510300", "qty": 100, "side": "buy", "price": 10.0},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"] == "ORD1"
        assert data["symbol"] == "510300"

    def test_order_place_returns_none(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.place.return_value = None
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/order",
                json={"symbol": "510300", "qty": 100, "side": "buy"},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 500

    def test_cancel_not_connected(self, monkeypatch):
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        srv.gateway.broker = None
        srv.gateway.last_reconnect = 0.0
        with TestClient(srv.app) as client:
            resp = client.post(
                "/cancel",
                json={"order_id": "X1"},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 503

    def test_cancel_success(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.orders = {}
        mock_broker.cancel.return_value = True
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/cancel",
                json={"order_id": "X1"},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

    def test_wait_fill_not_found(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.orders = {}
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/wait_fill",
                json={"order_id": "missing", "timeout": 5},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 404

    def test_wait_fill_not_filled(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_order = MagicMock()
        mock_broker.orders = {"O1": mock_order}
        mock_broker.wait_fill.return_value = None
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/wait_fill",
                json={"order_id": "O1", "timeout": 5},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["filled"] is False

    def test_wait_fill_success(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_order = MagicMock()
        mock_broker.orders = {"O1": mock_order}
        mock_broker.wait_fill.return_value = {
            "order_id": "O1",
            "price": 10.5,
            "qty": 100,
        }
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.post(
                "/wait_fill",
                json={"order_id": "O1", "timeout": 5},
                headers={"X-Token": "test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filled"] is True
        assert data["price"] == 10.5

    def test_positions_success(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.get_positions.return_value = [{"symbol": "510300", "qty": 100}]
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.get("/positions", headers={"X-Token": "test-token"})
        assert resp.status_code == 200
        assert "positions" in resp.json()

    def test_account_success(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.get_account_info.return_value = {"balance": 100000.0}
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.get("/account", headers={"X-Token": "test-token"})
        assert resp.status_code == 200
        assert resp.json()["balance"] == 100000.0

    def test_orders_success(self, monkeypatch):
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_order = MagicMock()
        mock_order.order_id = "O1"
        mock_order.symbol = "510300"
        mock_order.qty = 100
        mock_order.side = "buy"
        mock_order.order_type = "LIMIT"
        mock_order.price = 10.0
        mock_order.status = "filled"
        mock_order.ts = "t1"
        mock_order.filled_qty = 100
        mock_order.avg_price = 10.0
        mock_broker.orders = {"O1": mock_order}
        srv.gateway.broker = mock_broker
        with TestClient(srv.app) as client:
            resp = client.get("/orders", headers={"X-Token": "test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "O1" in data["orders"]

    def test_positions_not_connected(self, monkeypatch):
        monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("QMT_PATH", raising=False)
        srv.gateway.broker = None
        srv.gateway.last_reconnect = 0.0
        with TestClient(srv.app) as client:
            resp = client.get("/positions", headers={"X-Token": "test-token"})
        assert resp.status_code == 503


# ============================================================
# MainTest — 入口函数
# ============================================================


class MainTest:

    def test_main_loopback_invokes_uvicorn(self, monkeypatch):
        """main() 在回环地址应调用 uvicorn.run."""
        monkeypatch.setenv("QMT_RPC_TOKEN", "t")
        monkeypatch.delenv("QMT_RPC_HOST", raising=False)
        monkeypatch.delenv("QMT_RPC_PORT", raising=False)
        with patch("uvicorn.run") as mock_run:
            with patch("sys.argv", ["qmt_rpc_server.py"]):
                srv.main()
        assert mock_run.called

    def test_main_custom_host_port(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "t")
        with patch("uvicorn.run") as mock_run:
            with patch(
                "sys.argv",
                ["qmt_rpc_server.py", "--host", "127.0.0.1", "--port", "9999"],
            ):
                srv.main()
        assert mock_run.called
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["host"] == "127.0.0.1"
        assert call_kwargs.kwargs["port"] == 9999

    def test_main_unsafe_bind_exits(self, monkeypatch):
        """非回环地址且无白名单 → SystemExit."""
        monkeypatch.setenv("QMT_RPC_TOKEN", "t")
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        with pytest.raises(SystemExit):
            with patch("sys.argv", ["qmt_rpc_server.py", "--host", "0.0.0.0"]):
                srv.main()
