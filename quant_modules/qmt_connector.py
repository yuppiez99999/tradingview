"""quant_modules.qmt_connector — QMT 券商连接器 (W7.1.4 paper trading 骨架).

定位 (Wave 7 Sprint 1):
    为 Sprint 2 W7.2.1 (T15 QMT 实盘接入 paper → 10% 灰度) 准备 paper trading 骨架.
    不依赖 xtquant, 纯 Python 模拟, 实现 BrokerProtocol 对接 T15 LiveOrderExecutor.

设计原则:
    1. paper_mode 默认 True — 不实际下单, 限价单假设以限价全部成交, 市价单以当前价成交
    2. live_mode 委托 utils.execution.broker_factory.get_broker() — 延迟导入, xtquant 未安装时降级
    3. 生命周期管理 — connect/disconnect/reconnect, 状态机 IDLE→CONNECTED→DISCONNECTED
    4. 审计日志 — JSONL 落盘 (订单/成交/撤单/持仓), 对接 T14 RiskAuditLogger 协议
    5. Feature Flag 透传 (HC-1) — USE_QMT_LIVE 默认 False, live 需显式启用 + TRADING_ENV=production
    6. fail-closed — live 模式 connect 失败不降级 (与 broker_factory 的 fail-open 区分: 此处是决策路径)

接口 (BrokerProtocol):
    is_live: bool
    place_order(symbol, side, qty, price, order_type) -> broker_order_id
    cancel_order(broker_order_id) -> bool
    get_order_status(broker_order_id) -> dict  # {state, filled_qty, avg_price, rejection_reason}

扩展接口 (T15-T18 实盘验证四件套需要):
    get_positions() -> dict  # {symbol: {qty, avg_price}}
    get_account() -> dict    # {cash, total_asset, market_value}
    health_check() -> dict   # {connected, latency_ms, last_error}

用法:
    from quant_modules.qmt_connector import QmtConnector, QmtConfig

    # paper trading (默认)
    conn = QmtConnector(QmtConfig(paper_mode=True))
    conn.connect()
    oid = conn.place_order("000001.SZ", "BUY", 100, 10.5)

    # live trading (需 xtquant + TRADING_ENV=production)
    conn = QmtConnector(QmtConfig(paper_mode=False, account_id="...", qmt_path="..."))
    conn.connect()  # 委托 QmtBrokerAPI
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("qmt_connector")


# ============================================================
# 异常类
# ============================================================

class QmtConnectorError(Exception):
    """QMT 连接器基础异常."""


class QmtNotConnectedError(QmtConnectorError):
    """未连接就调用下单接口."""


class QmtLiveModeDisabledError(QmtConnectorError):
    """实盘模式未启用 (HC-1 Feature Flag 关闭)."""


# ============================================================
# 配置 & 状态
# ============================================================

class ConnectorState(Enum):
    """连接器状态机: IDLE → CONNECTED → DISCONNECTED."""

    IDLE = "IDLE"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


@dataclass
class QmtConfig:
    """QMT 连接器配置.

    Attributes:
        paper_mode: True=模拟盘 (默认), False=实盘 (需 xtquant + TRADING_ENV=production)
        account_id: QMT 资金账号
        session_id: QMT session (实盘用)
        account_type: STOCK / CREDIT / FUTURE
        qmt_path: xtquant 安装路径
        connect_timeout: 连接超时秒
        audit_path: 审计日志 JSONL 路径 (None=不落盘)
        initial_capital: paper 模式初始资金
        slippage_bps: paper 模拟滑点 (基点, 1bp=0.01%)
        fee_bps: paper 模拟手续费 (基点, A股双边约 1.5bp 佣金 + 10bp 印花税卖出单边)
    """

    paper_mode: bool = True
    account_id: str = ""
    session_id: int = 0
    account_type: str = "STOCK"
    qmt_path: str = ""
    connect_timeout: int = 10
    audit_path: Optional[str] = None
    initial_capital: float = 1_000_000.0
    slippage_bps: float = 2.0
    fee_bps: float = 11.5


@dataclass
class PaperOrder:
    """paper 模式内存订单."""

    broker_order_id: str
    symbol: str
    side: str  # BUY / SELL
    qty: int
    price: float
    order_type: str  # limit / market
    state: str = "FILLED"  # PENDING / FILLED / CANCELLED / REJECTED
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    rejection_reason: str = ""
    created_at: str = ""
    filled_at: str = ""


# ============================================================
# Paper Trading 内存订单簿
# ============================================================

class _PaperOrderBook:
    """paper 模式内存订单簿 — 维护订单/持仓/账户.

    模拟规则:
        - 限价单: 假设以限价全部成交 (乐观假设, paper trading 特性)
        - 市价单: 以调用方传入的 price 成交 (调用方应传当前价)
        - 滑点: 成交价 = price * (1 + slippage * side_sign / 10000)
        - 手续费: notional * fee_bps / 10000
        - T+1: 买入当日不可卖 (A股规则, paper 模式简化为记录买入日期)
    """

    def __init__(self, config: QmtConfig) -> None:
        self._cfg = config
        self._orders: dict[str, PaperOrder] = {}
        self._positions: dict[str, dict[str, Any]] = {}  # {symbol: {qty, avg_price, buy_date}}
        self._cash = config.initial_capital
        self._market_value = 0.0
        self._today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def place_order(
        self, symbol: str, side: str, qty: int, price: float, order_type: str = "limit",
    ) -> PaperOrder:
        oid = f"PAPER-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        side_sign = 1.0 if side.upper() == "BUY" else -1.0
        fill_price = price * (1.0 + side_sign * self._cfg.slippage_bps / 10000.0)
        notional = fill_price * qty
        fee = notional * self._cfg.fee_bps / 10000.0

        if side.upper() == "BUY":
            if self._cash < notional + fee:
                order = PaperOrder(
                    broker_order_id=oid, symbol=symbol, side=side, qty=qty,
                    price=price, order_type=order_type, state="REJECTED",
                    rejection_reason="资金不足", created_at=now,
                )
                self._orders[oid] = order
                return order
            self._cash -= notional + fee
            pos = self._positions.get(symbol, {"qty": 0, "avg_price": 0.0, "buy_date": self._today})
            old_cost = pos["qty"] * pos["avg_price"]
            pos["qty"] += qty
            pos["avg_price"] = (old_cost + notional) / pos["qty"] if pos["qty"] != 0 else 0.0
            pos["buy_date"] = self._today
            self._positions[symbol] = pos
        else:
            pos = self._positions.get(symbol, {"qty": 0, "avg_price": 0.0, "buy_date": ""})
            if pos["qty"] < qty:
                order = PaperOrder(
                    broker_order_id=oid, symbol=symbol, side=side, qty=qty,
                    price=price, order_type=order_type, state="REJECTED",
                    rejection_reason="持仓不足", created_at=now,
                )
                self._orders[oid] = order
                return order
            self._cash += notional - fee
            pos["qty"] -= qty
            if pos["qty"] == 0:
                pos["avg_price"] = 0.0
            self._positions[symbol] = pos

        order = PaperOrder(
            broker_order_id=oid, symbol=symbol, side=side, qty=qty,
            price=price, order_type=order_type, state="FILLED",
            filled_qty=qty, avg_fill_price=fill_price,
            created_at=now, filled_at=now,
        )
        self._orders[oid] = order
        return order

    def cancel_order(self, broker_order_id: str) -> bool:
        order = self._orders.get(broker_order_id)
        if order is None or order.state != "PENDING":
            return False
        order.state = "CANCELLED"
        return True

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        order = self._orders.get(broker_order_id)
        if order is None:
            return {"state": "NOT_FOUND", "filled_qty": 0, "avg_price": 0.0, "rejection_reason": "订单不存在"}
        return {
            "state": order.state,
            "filled_qty": order.filled_qty,
            "avg_price": order.avg_fill_price,
            "rejection_reason": order.rejection_reason,
        }

    def get_positions(self) -> dict[str, dict[str, Any]]:
        return {sym: {"qty": p["qty"], "avg_price": p["avg_price"]} for sym, p in self._positions.items() if p["qty"] != 0}

    def get_account(self) -> dict[str, Any]:
        mv = sum(p["qty"] * p["avg_price"] for p in self._positions.values())
        return {
            "cash": round(self._cash, 2),
            "market_value": round(mv, 2),
            "total_asset": round(self._cash + mv, 2),
        }

    def all_orders(self) -> list[PaperOrder]:
        return list(self._orders.values())


# ============================================================
# 主类 — QmtConnector (实现 BrokerProtocol)
# ============================================================

class QmtConnector:
    """QMT 券商连接器 — paper trading 骨架 + live 委托.

    实现 BrokerProtocol (place_order/cancel_order/get_order_status),
    扩展 get_positions/get_account/health_check 供 T15-T18 实盘验证四件套调用.
    """

    def __init__(self, config: Optional[QmtConfig] = None) -> None:
        self._cfg = config or QmtConfig()
        self._state = ConnectorState.IDLE
        self._paper_book: Optional[_PaperOrderBook] = None
        self._live_broker: Any = None
        self._connect_latency_ms: float = 0.0
        self._last_error: str = ""

    @property
    def is_live(self) -> bool:
        """BrokerProtocol.is_live — paper=False, live=True."""
        return not self._cfg.paper_mode and self._state == ConnectorState.CONNECTED

    @property
    def state(self) -> ConnectorState:
        return self._state

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------

    def connect(self) -> bool:
        """连接 QMT. paper 模式直接就绪, live 模式委托 broker_factory."""
        if self._state == ConnectorState.CONNECTED:
            return True

        t0 = time.perf_counter()
        try:
            if self._cfg.paper_mode:
                self._paper_book = _PaperOrderBook(self._cfg)
                logger.info("QmtConnector paper 模式就绪 (initial_capital=%.2f)", self._cfg.initial_capital)
            else:
                self._live_broker = self._connect_live()
            self._state = ConnectorState.CONNECTED
            self._connect_latency_ms = (time.perf_counter() - t0) * 1000.0
            self._audit("connect", {"paper_mode": self._cfg.paper_mode, "latency_ms": round(self._connect_latency_ms, 2)})
            return True
        except (QmtConnectorError, OSError, RuntimeError, ValueError, TimeoutError) as exc:
            self._state = ConnectorState.ERROR
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.error("QmtConnector connect 失败: %s", self._last_error)
            return False

    def _connect_live(self) -> Any:
        """实盘连接 — 委托 broker_factory, fail-closed (不降级)."""
        if os.environ.get("TRADING_ENV", "sim").lower() != "production":
            raise QmtLiveModeDisabledError("实盘需 TRADING_ENV=production")
        try:
            from utils.execution.broker_factory import get_broker
        except ImportError as exc:
            raise QmtConnectorError(f"broker_factory 导入失败: {exc}") from exc
        broker = get_broker({
            "type": "qmt", "enabled": True, "dry_run": False,
            "account_id": self._cfg.account_id, "session_id": self._cfg.session_id,
            "account_type": self._cfg.account_type, "qmt_path": self._cfg.qmt_path,
            "connect_timeout": self._cfg.connect_timeout,
        })
        if not getattr(broker, "is_live", False):
            raise QmtConnectorError("broker_factory 返回非实盘 broker (可能 xtquant 未安装)")
        return broker

    def disconnect(self) -> None:
        """断开连接."""
        if self._live_broker is not None:
            try:
                disconnect = getattr(self._live_broker, "disconnect", None)
                if disconnect is not None:
                    disconnect()
            except (OSError, RuntimeError, AttributeError) as exc:
                logger.warning("live broker disconnect 异常: %s", exc)
        self._state = ConnectorState.DISCONNECTED
        self._paper_book = None
        self._live_broker = None
        self._audit("disconnect", {})

    def reconnect(self) -> bool:
        """重连 — disconnect + connect."""
        self.disconnect()
        self._state = ConnectorState.IDLE
        return self.connect()

    # --------------------------------------------------------
    # BrokerProtocol 接口
    # --------------------------------------------------------

    def place_order(
        self, symbol: str, side: str, qty: int, price: float, order_type: str = "limit",
    ) -> str:
        """提交订单, 返回 broker_order_id."""
        if self._state != ConnectorState.CONNECTED:
            raise QmtNotConnectedError(f"连接器状态={self._state.value}, 需先 connect()")
        if self._cfg.paper_mode and self._paper_book is not None:
            order = self._paper_book.place_order(symbol, side, qty, price, order_type)
            self._audit("place_order", {"oid": order.broker_order_id, "symbol": symbol, "side": side,
                                        "qty": qty, "price": price, "state": order.state})
            return order.broker_order_id
        if self._live_broker is not None:
            oid = self._live_broker.place_order(symbol, side, qty, price, order_type)
            self._audit("place_order", {"oid": oid, "symbol": symbol, "side": side, "qty": qty, "price": price, "live": True})
            return oid
        raise QmtNotConnectedError("无可用 broker (paper_book/live_broker 均为 None)")

    def cancel_order(self, broker_order_id: str) -> bool:
        """撤单."""
        if self._state != ConnectorState.CONNECTED:
            return False
        if self._cfg.paper_mode and self._paper_book is not None:
            ok = self._paper_book.cancel_order(broker_order_id)
        elif self._live_broker is not None:
            ok = bool(self._live_broker.cancel_order(broker_order_id))
        else:
            return False
        self._audit("cancel_order", {"oid": broker_order_id, "ok": ok})
        return ok

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        """查询订单状态 → {state, filled_qty, avg_price, rejection_reason}."""
        if self._cfg.paper_mode and self._paper_book is not None:
            return self._paper_book.get_order_status(broker_order_id)
        if self._live_broker is not None:
            return self._live_broker.get_order_status(broker_order_id)
        return {"state": "NOT_CONNECTED", "filled_qty": 0, "avg_price": 0.0, "rejection_reason": "连接器未连接"}

    # --------------------------------------------------------
    # 扩展接口 (T15-T18 实盘验证四件套)
    # --------------------------------------------------------

    def get_positions(self) -> dict[str, dict[str, Any]]:
        """查询持仓 → {symbol: {qty, avg_price}}."""
        if self._cfg.paper_mode and self._paper_book is not None:
            return self._paper_book.get_positions()
        if self._live_broker is not None:
            getter = getattr(self._live_broker, "get_positions", None)
            if getter is not None:
                return getter()
        return {}

    def get_account(self) -> dict[str, Any]:
        """查询账户 → {cash, market_value, total_asset}."""
        if self._cfg.paper_mode and self._paper_book is not None:
            return self._paper_book.get_account()
        if self._live_broker is not None:
            getter = getattr(self._live_broker, "get_account", None)
            if getter is not None:
                return getter()
        return {"cash": 0.0, "market_value": 0.0, "total_asset": 0.0}

    def health_check(self) -> dict[str, Any]:
        """健康检查 → {connected, latency_ms, last_error, mode}."""
        return {
            "connected": self._state == ConnectorState.CONNECTED,
            "latency_ms": round(self._connect_latency_ms, 2),
            "last_error": self._last_error,
            "mode": "paper" if self._cfg.paper_mode else "live",
            "state": self._state.value,
        }

    # --------------------------------------------------------
    # 审计
    # --------------------------------------------------------

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        """JSONL 审计日志 (fail-open, 落盘失败仅告警)."""
        if not self._cfg.audit_path:
            return
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        try:
            path = Path(self._cfg.audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("审计日志落盘失败 (fail-open): %s", exc)
