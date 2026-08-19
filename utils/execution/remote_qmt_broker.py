"""
remote_qmt_broker — 云端侧 QMT 远程客户端 (BrokerAPI 实现)

架构:
    云端 RemoteQmtBroker  ──HTTPS(httpx)──▶  Win 实盘机 qmt_rpc_server  ──▶  QMT

设计:
    - 实现 BrokerAPI 同接口, 对上层透明 (broker_factory 注入)
    - fail-open: 网络异常不抛, 返回安全空值 + 告警, 不阻断策略
    - 连接语义: connect() = GET /health (探活 Win 网关 + QMT)
    - place/cancel/wait_fill/get_positions/get_account_info 全走 HTTP
    - 短超时 (默认 10s), wait_fill 超时由 Win 侧处理

环境变量:
    QMT_RPC_URL     — Win 网关地址 (如 http://10.0.0.2:8765 或 https://qmt-gw.tail-xxx)
    QMT_RPC_TOKEN   — 鉴权 token (与 Win 侧 QMT_RPC_TOKEN 一致)
    QMT_RPC_TIMEOUT — HTTP 超时秒 (默认 10)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

from ms_strategy.src.execution.broker_api import BrokerAPI, Fill, Order

logger = logging.getLogger(__name__)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx 未安装, RemoteQmtBroker 将不可用 (pip install httpx)")


class RemoteQmtBroker(BrokerAPI):
    """QMT 远程客户端: 通过 HTTP 调 Win 实盘机网关下单.

    对上层完全透明, 用法同 QmtBrokerAPI:
        broker = RemoteQmtBroker(rpc_url="http://win:8765", token="xxx")
        broker.connect()
        order = broker.place("510300.SH", 1000, "BUY", price=4.5)
        fill = broker.wait_fill(order, timeout=30)
    """

    def __init__(
        self,
        rpc_url: str = "",
        token: str = "",  # nosec B107 # RPC token 默认空值表示未设置, 非硬编码密码
        timeout: float = 10.0,
    ) -> None:
        super().__init__()
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx 未安装, 无法使用 RemoteQmtBroker")

        self.rpc_url = rpc_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._connected = False
        self._last_health: float = 0.0
        self._health_interval: float = 30.0

    # ------------------------------------------------------------
    # 内部 HTTP
    # ------------------------------------------------------------

    def _headers(self) -> dict:
        return {"X-Token": self.token, "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        try:
            resp = self._client.post(path, json=payload, headers=self._headers(), timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.error("RPC POST %s 失败: %d %s", path, resp.status_code, resp.text)
            return None
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            logger.error("RPC POST %s 异常: %s", path, exc)
            return None

    def _get(self, path: str) -> Optional[dict]:
        try:
            resp = self._client.get(path, headers=self._headers(), timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.error("RPC GET %s 失败: %d %s", path, resp.status_code, resp.text)
            return None
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            logger.error("RPC GET %s 异常: %s", path, exc)
            return None

    # ------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------

    def connect(self) -> bool:
        if not self.rpc_url or not self.token:
            logger.error("QMT_RPC_URL / QMT_RPC_TOKEN 未配置")
            return False
        try:
            self._client = httpx.Client(base_url=self.rpc_url)
            result = self._get("/health")
            if result and result.get("connected"):
                self._connected = True
                self._last_health = time.time()
                logger.info("RemoteQmtBroker 已连接: %s (account=%s)",
                            self.rpc_url, result.get("account", ""))
                return True
            logger.error("Win 网关健康检查失败: %s", result)
            self._connected = False
            return False
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            logger.error("RemoteQmtBroker 连接异常: %s", exc)
            self._connected = False
            return False

    def disconnect(self) -> bool:
        self._connected = False
        if self._client:
            try:
                self._client.close()
            except (ValueError, TypeError, OSError):
                pass
        self._client = None
        return True

    @property
    def is_connected(self) -> bool:
        if not self._connected:
            return False
        # 周期性探活 (30s 一次, 避免每次调用都打 health)
        if time.time() - self._last_health > self._health_interval:
            result = self._get("/health")
            if result and result.get("connected"):
                self._last_health = time.time()
                return True
            self._connected = False
            return False
        return True

    # ------------------------------------------------------------
    # 下单
    # ------------------------------------------------------------

    def place(self, symbol: str, qty: int, side: str,
              order_type: str = "LIMIT", price: float = 0.0,
              ts: str = "", callback: Optional[Callable] = None) -> Optional[Order]:
        if not self.is_connected:
            logger.error("RemoteQmtBroker 未连接, 无法下单")
            return None
        payload = {"symbol": symbol, "qty": qty, "side": side,
                   "order_type": order_type, "price": price, "ts": ts}
        result = self._post("/order", payload)
        if not result:
            return None
        try:
            order = Order(
                order_id=str(result["order_id"]),
                symbol=result.get("symbol", symbol),
                qty=int(result.get("qty", qty)),
                side=result.get("side", side),
                order_type=result.get("order_type", order_type),
                price=float(result.get("price", price)),
                status=result.get("status", "REPORTED"),
                ts=result.get("ts", ts or datetime.now().isoformat()),
            )
            self.orders[order.order_id] = order
            logger.info("RPC 下单: %s %s %s qty=%d order_id=%s",
                        symbol, side, order_type, qty, order.order_id)
            return order
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.error("RPC 下单响应解析失败: %s", exc)
            return None

    # ------------------------------------------------------------
    # 撤单
    # ------------------------------------------------------------

    def cancel(self, order: Order) -> bool:
        if not self.is_connected:
            return False
        result = self._post("/cancel", {"order_id": order.order_id})
        return bool(result and result.get("cancelled"))

    # ------------------------------------------------------------
    # 等待成交
    # ------------------------------------------------------------

    def wait_fill(self, order: Order, timeout: int = 30) -> Optional[dict]:
        if not self.is_connected:
            return None
        # 把轮询交给 Win 侧 (它离 QMT 最近, 延迟最低)
        result = self._post("/wait_fill", {"order_id": order.order_id, "timeout": timeout})
        if not result:
            return None
        if not result.get("filled"):
            return None
        try:
            fill = Fill(
                fill_id=str(result.get("fill_id", "")),
                order_id=str(result.get("order_id", order.order_id)),
                symbol=str(result.get("symbol", order.symbol)),
                qty=int(result.get("qty", 0)),
                price=float(result.get("price", 0.0)),
                side=str(result.get("side", order.side)),
                ts=str(result.get("ts", datetime.now().isoformat())),
            )
            self.fills.append(fill)
            order.status = "FILLED"
            order.filled_qty = fill.qty
            order.avg_price = fill.price
            return {
                "order_id": fill.order_id, "fill_id": fill.fill_id,
                "symbol": fill.symbol, "qty": fill.qty,
                "price": fill.price, "side": fill.side, "ts": fill.ts,
            }
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.error("RPC wait_fill 响应解析失败: %s", exc)
            return None

    # ------------------------------------------------------------
    # 持仓 / 账户
    # ------------------------------------------------------------

    def get_positions(self) -> dict:
        if not self.is_connected:
            return {}
        result = self._get("/positions")
        if not result:
            return {}
        positions = result.get("positions", {})
        self.positions.update(positions)
        return dict(positions)

    def get_account_info(self) -> dict:
        if not self.is_connected:
            return {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}
        result = self._get("/account")
        if not result:
            return {"available": 0.0, "total": 0.0, "frozen": 0.0, "margin": 0.0}
        return result

    def get_available_funds(self) -> float:
        account = self.get_account_info()
        return float(account.get("available", 0.0))

    # ------------------------------------------------------------
    # 盘口 (可选, 走 Win 侧 xtdata)
    # ------------------------------------------------------------

    def get_order_book(self, symbol: str, levels: int = 5) -> Optional[dict]:
        if not self.is_connected:
            return None
        result = self._get(f"/order_book?symbol={symbol}&levels={levels}")
        return result


__all__ = ["RemoteQmtBroker", "HTTPX_AVAILABLE"]
