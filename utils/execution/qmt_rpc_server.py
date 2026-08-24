"""
qmt_rpc_server — Windows 实盘机侧 QMT RPC 网关 (云上桥接的"手脚")

架构:
    云端 Linux (策略/决策)  ──HTTPS──▶  本网关 (Win 实盘机)  ──xtquant──▶  QMT 客户端  ──▶  交易所

职责:
    - 接收云端下单/撤单/查询指令, 转成本地 xtquant 调用
    - 回传持仓/账户/成交给云端
    - 强 token 鉴权 + 仅内网/VPN 暴露
    - QMT 连接断开时自愈重连

部署 (Win 实盘机):
    pip install fastapi uvicorn
    python utils/execution/qmt_rpc_server.py --port 8765
    或用 NSSM 注册为 Windows 服务 (见 scripts/cloud/install_qmt_rpc_service.ps1)

安全:
    - 不暴露公网, 通过 Tailscale/WireGuard/云 VPN 打通内网
    - QMT_RPC_TOKEN 环境变量鉴权 (X-Token header)
    - 仅允许配置的来源 IP (ALLOWED_IPS)
"""
from __future__ import annotations

import argparse
import hashlib  # noqa: F401  (保留以备扩展)
import hmac
import logging
import os
import sys
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================
# 请求/响应模型
# ============================================================

class OrderReq(BaseModel):
    symbol: str
    qty: int
    side: str
    order_type: str = "LIMIT"
    price: float = 0.0
    ts: str = ""


class CancelReq(BaseModel):
    order_id: str


class WaitFillReq(BaseModel):
    order_id: str
    timeout: int = 30


# ============================================================
# 鉴权
# ============================================================

def _verify_token(x_token: Optional[str] = Header(None, alias="X-Token")) -> None:
    expected = os.environ.get("QMT_RPC_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="QMT_RPC_TOKEN 未配置, 网关拒绝服务")
    # 恒定时间比较, 避免时序侧信道泄露 token 长度/内容
    if not x_token or not hmac.compare_digest(x_token, expected):
        raise HTTPException(status_code=401, detail="invalid token")


def _verify_ip(request: Request) -> None:
    allowed = os.environ.get("QMT_RPC_ALLOWED_IPS", "").strip()
    if not allowed:
        return
    client_ip = request.client.host if request.client else ""
    allowed_list = [ip.strip() for ip in allowed.split(",") if ip.strip()]
    if client_ip and client_ip not in allowed_list:
        raise HTTPException(status_code=403, detail=f"IP {client_ip} not allowed")


def _mask_account(account: str) -> str:
    """对外暴露接口只回传账户尾 4 位, 避免账户 ID 泄露 (CWE-200)."""
    account = account or ""
    if len(account) <= 4:
        return "****"
    return "****" + account[-4:]


def _assert_safe_bind(host: str) -> None:
    """fail-closed 启动守卫: 禁止在非回环地址且未配置 IP 白名单时启动。

    防止网关以 0.0.0.0/内网网卡对外暴露却只有 Token 一道防线 (HIGH-1)。
    回到回环地址 (127.0.0.1/localhost/::1) 默认放行; 非回环必须配置
    QMT_RPC_ALLOWED_IPS, 或显式 QMT_RPC_ALLOW_PUBLIC=1 覆盖 (需确认已前置 TLS/隔离)。
    """
    loopback = {"127.0.0.1", "localhost", "::1"}
    if host in loopback:
        return
    allowed = os.environ.get("QMT_RPC_ALLOWED_IPS", "").strip()
    allow_public = os.environ.get("QMT_RPC_ALLOW_PUBLIC", "").strip().lower() in {"1", "true", "yes"}
    if not allowed and not allow_public:
        raise SystemExit(
            "安全策略拒绝启动: 网关绑定到非回环地址 %r 但未配置 QMT_RPC_ALLOWED_IPS。"
            "请设置白名单 (QMT_RPC_ALLOWED_IPS) 或显式 QMT_RPC_ALLOW_PUBLIC=1"
            "并确认已前置 TLS 终止且网络隔离到位。" % host
        )
    logger.warning("网关以非回环地址 %s 启动, 请确认已前置 TLS 终止且网络隔离到位。", host)


# ============================================================
# QMT Broker 单例 + 自愈重连
# ============================================================

class QmtGateway:
    """QMT broker 单例, 带自愈重连."""

    def __init__(self) -> None:
        self.broker: Any = None
        self.last_reconnect: float = 0.0
        self.reconnect_interval: float = 30.0

    def ensure_connected(self) -> bool:
        if self.broker and getattr(self.broker, "is_connected", False):
            return True
        now = time.time()
        if now - self.last_reconnect < self.reconnect_interval:
            return False
        self.last_reconnect = now
        return self._connect()

    def _connect(self) -> bool:
        try:
            from ms_strategy.src.execution.qmt_broker import QmtBrokerAPI
            account_id = os.environ.get("QMT_ACCOUNT_ID", "")
            session_id = int(os.environ.get("QMT_SESSION_ID", "0"))
            account_type = os.environ.get("QMT_ACCOUNT_TYPE", "STOCK")
            qmt_path = os.environ.get("QMT_PATH", "")
            if not account_id or not qmt_path:
                logger.error("QMT_ACCOUNT_ID / QMT_PATH 未配置, 无法连接")
                return False
            self.broker = QmtBrokerAPI(
                account_id=account_id,
                session_id=session_id,
                account_type=account_type,
                path=qmt_path,
            )
            if self.broker.connect():
                logger.info("QMT 网关已连接: account=%s", account_id)
                return True
            logger.error("QMT connect() 失败")
            self.broker = None
            return False
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, ImportError) as exc:
            logger.error("QMT 网关连接异常: %s", exc, exc_info=True)
            self.broker = None
            return False


gateway = QmtGateway()


# ============================================================
# FastAPI app
# ============================================================

app = FastAPI(title="QMT RPC Gateway", version="1.0")


@app.on_event("startup")
async def _startup() -> None:
    gateway.ensure_connected()


@app.get("/health")
async def health(_: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)) -> dict:
    connected = gateway.ensure_connected()
    return {"connected": connected, "account": _mask_account(os.environ.get("QMT_ACCOUNT_ID", ""))}


@app.post("/order")
async def place_order(
    req: OrderReq,
    _: None = Depends(_verify_token),
    __: None = Depends(_verify_ip),
) -> JSONResponse:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        order = gateway.broker.place(
            symbol=req.symbol, qty=req.qty, side=req.side,
            order_type=req.order_type, price=req.price, ts=req.ts,
        )
        if order is None:
            raise HTTPException(status_code=500, detail="place() returned None")
        return JSONResponse({
            "order_id": order.order_id, "symbol": order.symbol,
            "qty": order.qty, "side": order.side, "order_type": order.order_type,
            "price": order.price, "status": order.status, "ts": order.ts,
        })
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("下单异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/cancel")
async def cancel_order(
    req: CancelReq,
    _: None = Depends(_verify_token),
    _ip: None = Depends(_verify_ip),
) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        from ms_strategy.src.execution.broker_api import Order
        order = gateway.broker.orders.get(req.order_id) or Order(
            order_id=req.order_id, symbol="", qty=0, side="",
            order_type="", price=0.0,
        )
        ok = gateway.broker.cancel(order)
        return {"cancelled": ok, "order_id": req.order_id}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("撤单异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/wait_fill")
async def wait_fill(
    req: WaitFillReq,
    _: None = Depends(_verify_token),
    _ip: None = Depends(_verify_ip),
) -> JSONResponse:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        order = gateway.broker.orders.get(req.order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"order {req.order_id} not found")
        fill = gateway.broker.wait_fill(order, timeout=req.timeout)
        if fill is None:
            return JSONResponse({"filled": False, "order_id": req.order_id})
        return JSONResponse({"filled": True, **fill})
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("等待成交异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/positions")
async def get_positions(_: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        return {"positions": gateway.broker.get_positions()}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("查询持仓异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/account")
async def get_account(_: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        return gateway.broker.get_account_info()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("查询账户异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/orders")
async def get_orders(_: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        orders = {}
        for oid, o in gateway.broker.orders.items():
            orders[oid] = {
                "order_id": o.order_id, "symbol": o.symbol, "qty": o.qty,
                "side": o.side, "order_type": o.order_type, "price": o.price,
                "status": o.status, "filled_qty": getattr(o, "filled_qty", 0),
                "avg_price": getattr(o, "avg_price", 0.0), "ts": o.ts,
            }
        return {"orders": orders}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        logger.error("查询订单异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
# 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="QMT RPC Gateway (Win 实盘机侧)")
    parser.add_argument("--host", default=os.environ.get("QMT_RPC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QMT_RPC_PORT", "8765")))
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    _assert_safe_bind(args.host)  # fail-closed: 非回环且无白名单则拒绝启动

    import uvicorn
    logger.info("启动 QMT RPC Gateway: %s:%d", args.host, args.port)
    uvicorn.run(
        "utils.execution.qmt_rpc_server:app",
        host=args.host, port=args.port,
        reload=args.reload, log_level="info",
    )


if __name__ == "__main__":
    main()
