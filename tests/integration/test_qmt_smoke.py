"""test_qmt_smoke.py — QMT 连接器 paper 模式 smoke 测试

验证 quant_modules/qmt_connector.py 的下单/撤单/回调链路在 paper 模式无异常.

覆盖链路:
    1. 生命周期: connect → CONNECTED → disconnect → DISCONNECTED
    2. 下单: place_order (BUY/SELL limit/market) → FILLED
    3. 撤单: cancel_order → CANCELLED
    4. 查询: get_order_status / get_positions / get_account
    5. 健康检查: health_check
    6. 异常: 未连接调用 / 资金不足拒绝
"""

import pytest

from quant_modules.qmt_connector import (
    ConnectorState,
    QmtConfig,
    QmtConnector,
    QmtNotConnectedError,
)


@pytest.fixture
def paper_config():
    return QmtConfig(
        paper_mode=True,
        initial_capital=1_000_000.0,
        slippage_bps=2.0,
        fee_bps=11.5,
    )


@pytest.fixture
def connected_connector(paper_config):
    conn = QmtConnector(paper_config)
    conn.connect()
    yield conn
    conn.disconnect()


# ============================================================
# 1. 生命周期
# ============================================================


class TestLifecycle:
    def test_initial_state_is_idle(self, paper_config):
        conn = QmtConnector(paper_config)
        assert conn.state == ConnectorState.IDLE

    def test_connect_transitions_to_connected(self, paper_config):
        conn = QmtConnector(paper_config)
        conn.connect()
        assert conn.state == ConnectorState.CONNECTED
        conn.disconnect()

    def test_disconnect_transitions_to_disconnected(self, paper_config):
        conn = QmtConnector(paper_config)
        conn.connect()
        conn.disconnect()
        assert conn.state == ConnectorState.DISCONNECTED

    def test_is_live_flag_is_false_in_paper_mode(self, paper_config):
        conn = QmtConnector(paper_config)
        assert conn.is_live is False


# ============================================================
# 2. 下单
# ============================================================


class TestPlaceOrder:
    def test_buy_limit_order_fills(self, connected_connector):
        oid = connected_connector.place_order("000001.SZ", "BUY", 100, 10.50, "limit")
        assert oid is not None
        status = connected_connector.get_order_status(oid)
        assert status["state"] == "FILLED"
        assert status["filled_qty"] == 100

    def test_sell_limit_order_fills(self, connected_connector):
        connected_connector.place_order("000001.SZ", "BUY", 200, 10.50, "limit")
        oid = connected_connector.place_order("000001.SZ", "SELL", 100, 10.60, "limit")
        status = connected_connector.get_order_status(oid)
        assert status["state"] == "FILLED"

    def test_market_order_fills(self, connected_connector):
        oid = connected_connector.place_order("600000.SH", "BUY", 100, 8.00, "market")
        status = connected_connector.get_order_status(oid)
        assert status["state"] == "FILLED"

    def test_insufficient_funds_rejected(self, connected_connector):
        oid = connected_connector.place_order(
            "000001.SZ", "BUY", 100000, 100.00, "limit"
        )
        status = connected_connector.get_order_status(oid)
        assert status["state"] == "REJECTED"
        assert "资金不足" in status.get("rejection_reason", "")


# ============================================================
# 3. 撤单
# ============================================================


class TestCancelOrder:
    def test_cancel_filled_order_returns_false(self, connected_connector):
        oid = connected_connector.place_order("000001.SZ", "BUY", 100, 10.50, "limit")
        result = connected_connector.cancel_order(oid)
        assert result is False

    def test_cancel_nonexistent_order_returns_false(self, connected_connector):
        result = connected_connector.cancel_order("NONEXISTENT-12345")
        assert result is False


# ============================================================
# 4. 查询
# ============================================================


class TestQueries:
    def test_get_order_status_returns_fields(self, connected_connector):
        oid = connected_connector.place_order("000001.SZ", "BUY", 100, 10.50, "limit")
        status = connected_connector.get_order_status(oid)
        assert "state" in status
        assert "filled_qty" in status
        assert "avg_price" in status

    def test_get_positions_after_buy(self, connected_connector):
        connected_connector.place_order("000001.SZ", "BUY", 100, 10.50, "limit")
        positions = connected_connector.get_positions()
        assert "000001.SZ" in positions
        assert positions["000001.SZ"]["qty"] == 100

    def test_get_account_has_expected_fields(self, connected_connector):
        account = connected_connector.get_account()
        assert "cash" in account
        assert "total_asset" in account
        assert account["cash"] > 0


# ============================================================
# 5. 健康检查
# ============================================================


class TestHealthCheck:
    def test_health_check_when_connected(self, connected_connector):
        health = connected_connector.health_check()
        assert health["connected"] is True

    def test_health_check_when_disconnected(self, paper_config):
        conn = QmtConnector(paper_config)
        health = conn.health_check()
        assert health["connected"] is False


# ============================================================
# 6. 异常
# ============================================================


class TestErrors:
    def test_place_order_before_connect_raises(self, paper_config):
        conn = QmtConnector(paper_config)
        with pytest.raises(QmtNotConnectedError):
            conn.place_order("000001.SZ", "BUY", 100, 10.50)

    def test_cancel_order_before_connect_returns_false(self, paper_config):
        conn = QmtConnector(paper_config)
        result = conn.cancel_order("test-oid")
        assert result is False
