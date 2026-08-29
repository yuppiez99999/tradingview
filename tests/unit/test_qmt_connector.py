"""tests/unit.test_qmt_connector — W7.1.4 QMT 连接器 paper trading 骨架测试.

覆盖:
    1. 配置 & 状态机
    2. 生命周期 (connect/disconnect/reconnect)
    3. BrokerProtocol 接口 (place_order/cancel_order/get_order_status)
    4. paper trading 行为 (买入/卖出/资金不足/持仓不足/滑点/手续费)
    5. 扩展接口 (get_positions/get_account/health_check)
    6. 审计日志 JSONL 落盘
    7. live 模式禁用 + 异常处理
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quant_modules.qmt_connector import (
    ConnectorState,
    QmtConfig,
    QmtConnector,
    QmtNotConnectedError,
)

# ============================================================
# 配置 & 状态机
# ============================================================


class TestQmtConfig:
    def test_defaults(self) -> None:
        cfg = QmtConfig()
        assert cfg.paper_mode is True
        assert cfg.account_type == "STOCK"
        assert cfg.initial_capital == 1_000_000.0
        assert cfg.slippage_bps == 2.0
        assert cfg.fee_bps == 11.5

    def test_custom(self) -> None:
        cfg = QmtConfig(paper_mode=False, account_id="A001", initial_capital=500_000.0)
        assert cfg.paper_mode is False
        assert cfg.account_id == "A001"
        assert cfg.initial_capital == 500_000.0


class TestConnectorState:
    def test_enum_values(self) -> None:
        assert ConnectorState.IDLE.value == "IDLE"
        assert ConnectorState.CONNECTED.value == "CONNECTED"
        assert ConnectorState.DISCONNECTED.value == "DISCONNECTED"
        assert ConnectorState.ERROR.value == "ERROR"


# ============================================================
# 生命周期
# ============================================================


class TestLifecycle:
    def test_connect_paper(self) -> None:
        conn = QmtConnector(QmtConfig(paper_mode=True))
        assert conn.state == ConnectorState.IDLE
        assert conn.connect() is True
        assert conn.state == ConnectorState.CONNECTED
        assert conn.is_live is False

    def test_connect_idempotent(self) -> None:
        conn = QmtConnector()
        assert conn.connect() is True
        assert conn.connect() is True
        assert conn.state == ConnectorState.CONNECTED

    def test_disconnect(self) -> None:
        conn = QmtConnector()
        conn.connect()
        conn.disconnect()
        assert conn.state == ConnectorState.DISCONNECTED

    def test_reconnect(self) -> None:
        conn = QmtConnector()
        conn.connect()
        assert conn.reconnect() is True
        assert conn.state == ConnectorState.CONNECTED

    def test_connect_latency_recorded(self) -> None:
        conn = QmtConnector()
        conn.connect()
        hc = conn.health_check()
        assert hc["latency_ms"] >= 0.0
        assert hc["connected"] is True


# ============================================================
# BrokerProtocol 接口 — paper trading
# ============================================================


class TestPaperTrading:
    def test_place_order_buy_success(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0))
        conn.connect()
        oid = conn.place_order("000001.SZ", "BUY", 100, 10.0)
        assert oid.startswith("PAPER-")
        st = conn.get_order_status(oid)
        assert st["state"] == "FILLED"
        assert st["filled_qty"] == 100
        assert st["avg_price"] > 10.0  # 含滑点

    def test_place_order_sell_success(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0))
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 200, 10.0)
        oid = conn.place_order("000001.SZ", "SELL", 100, 10.5)
        st = conn.get_order_status(oid)
        assert st["state"] == "FILLED"
        assert st["filled_qty"] == 100
        assert st["avg_price"] < 10.5  # 卖出滑点方向

    def test_buy_insufficient_cash_rejected(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=1000.0))
        conn.connect()
        oid = conn.place_order("600519.SH", "BUY", 100, 2000.0)
        st = conn.get_order_status(oid)
        assert st["state"] == "REJECTED"
        assert "资金不足" in st["rejection_reason"]

    def test_sell_insufficient_position_rejected(self) -> None:
        conn = QmtConnector()
        conn.connect()
        oid = conn.place_order("000001.SZ", "SELL", 100, 10.0)
        st = conn.get_order_status(oid)
        assert st["state"] == "REJECTED"
        assert "持仓不足" in st["rejection_reason"]

    def test_cancel_order_paper(self) -> None:
        conn = QmtConnector()
        conn.connect()
        oid = conn.place_order("000001.SZ", "BUY", 100, 10.0)
        assert conn.cancel_order(oid) is False

    def test_get_order_status_not_found(self) -> None:
        conn = QmtConnector()
        conn.connect()
        st = conn.get_order_status("NONEXIST")
        assert st["state"] == "NOT_FOUND"

    def test_not_connected_raises(self) -> None:
        conn = QmtConnector()
        with pytest.raises(QmtNotConnectedError):
            conn.place_order("000001.SZ", "BUY", 100, 10.0)


# ============================================================
# 持仓 & 账户
# ============================================================


class TestPositionsAccount:
    def test_positions_after_buy(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0))
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)
        pos = conn.get_positions()
        assert "000001.SZ" in pos
        assert pos["000001.SZ"]["qty"] == 100

    def test_positions_empty_after_sell_all(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0))
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)
        conn.place_order("000001.SZ", "SELL", 100, 10.0)
        pos = conn.get_positions()
        assert "000001.SZ" not in pos

    def test_account_balance(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0))
        conn.connect()
        acct = conn.get_account()
        assert acct["cash"] == 100_000.0
        assert acct["total_asset"] == 100_000.0
        conn.place_order("000001.SZ", "BUY", 100, 10.0)
        acct2 = conn.get_account()
        assert acct2["cash"] < 100_000.0

    def test_account_not_connected(self) -> None:
        conn = QmtConnector()
        acct = conn.get_account()
        assert acct == {"cash": 0.0, "market_value": 0.0, "total_asset": 0.0}


# ============================================================
# health_check
# ============================================================


class TestHealthCheck:
    def test_idle(self) -> None:
        conn = QmtConnector()
        hc = conn.health_check()
        assert hc["connected"] is False
        assert hc["mode"] == "paper"
        assert hc["state"] == "IDLE"

    def test_connected(self) -> None:
        conn = QmtConnector()
        conn.connect()
        hc = conn.health_check()
        assert hc["connected"] is True
        assert hc["last_error"] == ""

    def test_live_mode_flag(self) -> None:
        conn = QmtConnector(QmtConfig(paper_mode=False))
        hc = conn.health_check()
        assert hc["mode"] == "live"


# ============================================================
# 审计日志
# ============================================================


class TestAuditLog:
    def test_audit_jsonl(self, tmp_path: Path) -> None:
        audit = tmp_path / "qmt_audit.jsonl"
        conn = QmtConnector(QmtConfig(audit_path=str(audit)))
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)
        assert audit.exists()
        lines = audit.read_text(encoding="utf-8").strip().split("\n")
        events = [json.loads(line) for line in lines]
        assert any(e["event"] == "connect" for e in events)
        assert any(e["event"] == "place_order" for e in events)

    def test_audit_disabled(self, tmp_path: Path) -> None:
        conn = QmtConnector(QmtConfig())
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)

    def test_audit_fail_open(self, tmp_path: Path) -> None:
        bad_path = str(tmp_path / "nonexistent_deep" / "x" / "y" / "qmt.jsonl")
        conn = QmtConnector(QmtConfig(audit_path=bad_path))
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)


# ============================================================
# live 模式
# ============================================================


class TestLiveMode:
    def test_live_requires_production_env(self) -> None:
        old = os.environ.get("TRADING_ENV")
        os.environ["TRADING_ENV"] = "sim"
        try:
            conn = QmtConnector(QmtConfig(paper_mode=False))
            assert conn.connect() is False
            assert conn.state == ConnectorState.ERROR
            assert "production" in conn.health_check()["last_error"]
        finally:
            if old is None:
                os.environ.pop("TRADING_ENV", None)
            else:
                os.environ["TRADING_ENV"] = old

    def test_is_live_false_when_paper(self) -> None:
        conn = QmtConnector(QmtConfig(paper_mode=True))
        conn.connect()
        assert conn.is_live is False

    def test_is_live_false_when_not_connected(self) -> None:
        conn = QmtConnector(QmtConfig(paper_mode=False))
        assert conn.is_live is False


# ============================================================
# 滑点 & 手续费
# ============================================================


class TestSlippageFee:
    def test_slippage_applied(self) -> None:
        conn = QmtConnector(QmtConfig(initial_capital=100_000.0, slippage_bps=10.0))
        conn.connect()
        oid = conn.place_order("000001.SZ", "BUY", 100, 10.0)
        st = conn.get_order_status(oid)
        assert st["avg_price"] == pytest.approx(10.0 * 1.001, rel=1e-6)

    def test_fee_deducted(self) -> None:
        conn = QmtConnector(
            QmtConfig(initial_capital=100_000.0, slippage_bps=0.0, fee_bps=10.0)
        )
        conn.connect()
        conn.place_order("000001.SZ", "BUY", 100, 10.0)
        acct = conn.get_account()
        expected_cost = 1000.0 * (1 + 10.0 / 10000.0)
        assert acct["cash"] == pytest.approx(100_000.0 - expected_cost, rel=1e-4)
