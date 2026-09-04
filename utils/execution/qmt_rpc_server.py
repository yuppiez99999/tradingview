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
    - QMT_RPC_TOKEN 环境变量鉴权 (X-Token header), 恒定时间比较
    - 仅允许配置的来源 IP (ALLOWED_IPS)
    - 来源 IP 暴力破解防护: 连续鉴权失败超过阈值临时封禁 (错误计数 + 封禁, 加固路线图 #5)
    - 默认只信直连对端 IP; 网关置于可信反代后时设置 QMT_RPC_TRUST_PROXY_HEADERS=1
      以按 X-Forwarded-For / X-Real-IP 解析真实客户端 IP (避免全员共享代理 IP)
"""

from __future__ import annotations

import argparse
import hashlib  # noqa: F401  (保留以备扩展)
import hmac
import ipaddress
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
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


class _TokenBruteForceGuard:
    """按来源 IP 对鉴权失败计数并在超过阈值后临时封禁 (防 Token 暴力破解, 加固路线图 #5)。

    - 失败记录按滑动窗口保留 (window 秒内超过 max_fail 次即触发封禁)
    - 封禁持续 lockout 秒, 期间该 IP 所有鉴权请求直接 429
    - 鉴权成功后清零该 IP 失败计数
    - 线程安全 (进程内 asyncio/多线程并发共享同一份计数)

    跨进程限制 (重要):
        计数与封禁按**进程内**共享 (threading.Lock), uvicorn --workers>1 时各 worker
        独立持有 _auth_guard 单例与计数, 封禁阈值等效放大为 N×max_fail。
        多 worker 部署请保持 --workers 1, 或将网关置于可信反代之后并设置
        QMT_RPC_TRUST_PROXY_HEADERS=1, 由反代层做 IP 级限流补充。
    """

    def __init__(
        self,
        max_fail: int = 5,
        window: float = 60.0,
        lockout: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_fail = max(max_fail, 1)
        self.window = float(window)
        self.lockout = float(lockout)
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}  # ip -> [失败时间戳]
        self._lockout_until: dict[str, float] = {}  # ip -> 解禁时刻

    def is_locked(self, ip: str) -> bool:
        with self._lock:
            now = self._clock()
            until = self._lockout_until.get(ip, 0.0)
            if now < until:
                return True
            if until:
                self._lockout_until.pop(ip, None)  # 封禁过期自动解除
            self._prune(ip, now)
            if len(self._fails.get(ip, [])) >= self.max_fail:
                self._lockout_until[ip] = now + self.lockout
                self._fails.pop(ip, None)
                logger.warning(
                    "IP %s 鉴权失败次数过多, 临时封禁 %.0fs", ip, self.lockout
                )
                return True
        return False

    def record_fail(self, ip: str) -> None:
        with self._lock:
            now = self._clock()
            self._fails.setdefault(ip, []).append(now)
            self._prune(ip, now)

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
            self._lockout_until.pop(ip, None)

    def _prune(self, ip: str, now: float) -> None:
        self._fails[ip] = [t for t in self._fails.get(ip, []) if now - t < self.window]
        if not self._fails[ip]:
            self._fails.pop(ip, None)


# 全局限流器: 不同路由共享同一份失败计数 (同一攻击者换路由打 token 也会被计)
_auth_guard = _TokenBruteForceGuard()


def _is_valid_ip(value: str) -> bool:
    """校验字符串是否为合法 IP 地址 (IPv4/IPv6)."""
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _trust_proxy_headers() -> bool:
    """是否信任代理头 (X-Forwarded-For / X-Real-IP).

    默认关闭 (fail-closed): X-Forwarded-For 可由客户端伪造, 仅在网关置于
    可信反代之后且确认反代覆写该头时显式设置 QMT_RPC_TRUST_PROXY_HEADERS=1。
    """
    return (
        os.environ.get("QMT_RPC_TRUST_PROXY_HEADERS", "").strip().lower()
        in {"1", "true", "yes"}
    )


def _client_ip(request: Request) -> str:
    """取客户端真实 IP, 用于暴力破解计数与 IP 白名单.

    默认只信 ASGI 直连对端 (request.client.host), 不使用可伪造的代理头;
    仅当显式设置 QMT_RPC_TRUST_PROXY_HEADERS=1 时解析代理头:
    - X-Forwarded-For 取最左侧合法 IP (格式 "client, proxy1, proxy2")
    - 其次取 X-Real-IP
    - 代理头缺失或全部非法 → 回退对端地址
    """
    direct = request.client.host if request.client is not None else "127.0.0.1"
    if not _trust_proxy_headers():
        return direct

    xff = str(request.headers.get("x-forwarded-for", "") or "")
    for segment in xff.split(","):
        candidate = segment.strip()
        if candidate and _is_valid_ip(candidate):
            return candidate

    xri = str(request.headers.get("x-real-ip", "") or "").strip()
    if xri and _is_valid_ip(xri):
        return xri
    return direct


def _verify_token(
    request: Request,
    x_token: str | None = Header(None, alias="X-Token"),
) -> None:
    """Token 鉴权 (恒定时间比较) + 来源 IP 暴力破解封禁.

    request 由 FastAPI 自动注入 (按来源 IP 独立计数与封禁)。
    """
    ip = _client_ip(request)
    if _auth_guard.is_locked(ip):
        raise HTTPException(
            status_code=429,
            detail="too many auth failures, temporarily blocked",
        )
    expected = os.environ.get("QMT_RPC_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503, detail="QMT_RPC_TOKEN 未配置, 网关拒绝服务"
        )
    # 恒定时间比较, 避免时序侧信道泄露 token 长度/内容
    if not x_token or not hmac.compare_digest(x_token, expected):
        _auth_guard.record_fail(ip)
        raise HTTPException(status_code=401, detail="invalid token")
    _auth_guard.record_success(ip)


def _verify_ip(request: Request) -> None:
    allowed = os.environ.get("QMT_RPC_ALLOWED_IPS", "").strip()
    if not allowed:
        return
    client_ip = _client_ip(request)
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
    allow_public = os.environ.get("QMT_RPC_ALLOW_PUBLIC", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not allowed and not allow_public:
        raise SystemExit(
            f"安全策略拒绝启动: 网关绑定到非回环地址 {host!r} 但未配置 QMT_RPC_ALLOWED_IPS。"
            "请设置白名单 (QMT_RPC_ALLOWED_IPS) 或显式 QMT_RPC_ALLOW_PUBLIC=1"
            "并确认已前置 TLS 终止且网络隔离到位。"
        )
    logger.warning(
        "网关以非回环地址 %s 启动, 请确认已前置 TLS 终止且网络隔离到位。", host
    )


def _warn_guard_limits(host: str) -> None:
    """启动期防御性提示 (P0-3/P1-1):

    - 非回环 + 未开启可信代理头: 反代后所有请求共享同一对端 IP, 封禁/白名单失效
    - 非回环 + 已开启可信代理头: 计数/白名单按真实客户端 IP 生效, 但多 worker 部署时
      各 worker 独立计数 (阈值放大); 建议 --workers 1 或反代层补充 IP 限流
    """
    if not host or host in {"127.0.0.1", "localhost", "::1"}:
        return
    if _trust_proxy_headers():
        logger.warning(
            "QMT_RPC_TRUST_PROXY_HEADERS=1: 按可信反代头的真实客户端 IP 计数与鉴白; "
            "但暴力破解计数为进程内共享, 多 worker 部署时阈值等效放大, "
            "建议 uvicorn --workers 1 或由反代层补充 IP 级限流"
        )
    else:
        logger.warning(
            "非回环地址 %s 启动且未设置 QMT_RPC_TRUST_PROXY_HEADERS: 若置于反代之后, "
            "所有请求将共享反代对端 IP 计数, 暴力破解封禁与 QMT_RPC_ALLOWED_IPS 白名单 "
            "将失效; 请确认网关直连客户端, 或在可信反代后设置该开关", host
        )


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
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            ImportError,
        ) as exc:
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
async def health(
    _: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)
) -> dict:
    connected = gateway.ensure_connected()
    return {
        "connected": connected,
        "account": _mask_account(os.environ.get("QMT_ACCOUNT_ID", "")),
    }


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
            symbol=req.symbol,
            qty=req.qty,
            side=req.side,
            order_type=req.order_type,
            price=req.price,
            ts=req.ts,
        )
        if order is None:
            raise HTTPException(status_code=500, detail="place() returned None")
        return JSONResponse(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "qty": order.qty,
                "side": order.side,
                "order_type": order.order_type,
                "price": order.price,
                "status": order.status,
                "ts": order.ts,
            }
        )
    except HTTPException:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
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
            order_id=req.order_id,
            symbol="",
            qty=0,
            side="",
            order_type="",
            price=0.0,
        )
        ok = gateway.broker.cancel(order)
        return {"cancelled": ok, "order_id": req.order_id}
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
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
            raise HTTPException(
                status_code=404, detail=f"order {req.order_id} not found"
            )
        fill = gateway.broker.wait_fill(order, timeout=req.timeout)
        if fill is None:
            return JSONResponse({"filled": False, "order_id": req.order_id})
        return JSONResponse({"filled": True, **fill})
    except HTTPException:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.error("等待成交异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/positions")
async def get_positions(
    _: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)
) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        return {"positions": gateway.broker.get_positions()}
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.error("查询持仓异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/account")
async def get_account(
    _: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)
) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        return gateway.broker.get_account_info()
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.error("查询账户异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/orders")
async def get_orders(
    _: None = Depends(_verify_token), _ip: None = Depends(_verify_ip)
) -> dict:
    if not gateway.ensure_connected():
        raise HTTPException(status_code=503, detail="QMT 未连接")
    try:
        orders = {}
        for oid, o in gateway.broker.orders.items():
            orders[oid] = {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "qty": o.qty,
                "side": o.side,
                "order_type": o.order_type,
                "price": o.price,
                "status": o.status,
                "filled_qty": getattr(o, "filled_qty", 0),
                "avg_price": getattr(o, "avg_price", 0.0),
                "ts": o.ts,
            }
        return {"orders": orders}
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.error("查询订单异常: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
# 入口
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="QMT RPC Gateway (Win 实盘机侧)")
    parser.add_argument("--host", default=os.environ.get("QMT_RPC_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("QMT_RPC_PORT", "8765"))
    )
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    _assert_safe_bind(args.host)  # fail-closed: 非回环且无白名单则拒绝启动
    _warn_guard_limits(args.host)

    import uvicorn

    logger.info("启动 QMT RPC Gateway: %s:%d", args.host, args.port)
    uvicorn.run(
        "utils.execution.qmt_rpc_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
