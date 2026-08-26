"""
QMT 执行链路模拟盘验证 (2026-08-25)

目的:
    在不依赖 QMT 终端/xtquant 的前提下, 验证完整执行链路的代码正确性:

        RemoteQmtBroker (云端客户端)
            ──HTTP+Token 鉴权──▶ qmt_rpc_server (Win 侧网关, FastAPI)
                                    └──▶ SimulatedBroker (模拟撮合后端, 接口与 QmtBrokerAPI 完全一致)

    覆盖: 健康检查 / token 鉴权拒绝 / 限价下单 / 等待成交(含滑点) / 撤单 /
          持仓查询 / 账户查询 / broker_factory 装配逻辑 (双签保护)

    未覆盖 (需真实 QMT 模拟账户): xtquant → QMT 终端段。装好 QMT 后改
    config/system_config.json broker 段即可无缝切换。

用法:
    .venv\\Scripts\\python.exe scripts\\verify_qmt_sim_chain.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

RPC_PORT = 18765
RPC_URL = f"http://127.0.0.1:{RPC_PORT}"
TOKEN = "sim-verify-token-20260825"

# 测试参数
CAPITAL = 2_000_000
BUY_SYMBOL, BUY_PRICE, BUY_QTY = "510300.SH", 3.950, 100     # 沪深300ETF
CANCEL_SYMBOL, CANCEL_PRICE, CANCEL_QTY = "510500.SH", 6.100, 200  # 中证500ETF

results: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    os.environ["QMT_RPC_TOKEN"] = TOKEN
    os.environ["QMT_RPC_HOST"] = "127.0.0.1"
    os.environ["QMT_RPC_PORT"] = str(RPC_PORT)

    # 静默告警通道 (验证过程不外发钉钉/飞书)
    try:
        import utils.notify as _notify
        _notify.send_alert = lambda *a, **k: {"log": True}
    except (ImportError, AttributeError):
        pass

    print("=" * 72)
    print("QMT 执行链路模拟盘验证 —", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 72)

    # ── 1. 构造模拟网关: qmt_rpc_server + SimulatedBroker 后端 ──
    print("\n[1] 启动本地 RPC 网关 (SimulatedBroker 撮合后端)")
    import utils.execution.qmt_rpc_server as rpc_server
    from ms_strategy.src.execution.broker_api import SimulatedBroker

    sim = SimulatedBroker(initial_capital=CAPITAL)
    sim.set_price(BUY_SYMBOL, BUY_PRICE, volume=1_000_000, volatility=0.015)
    sim.set_price(CANCEL_SYMBOL, CANCEL_PRICE, volume=500_000, volatility=0.018)
    rpc_server.gateway.broker = sim
    # 网关层直接视为已连接 (SimulatedBroker.is_connected 为只读 property,
    # 且 ensure_connected 会尝试导入需要 xtquant 的 QmtBrokerAPI, 双双绕过)
    rpc_server.gateway.ensure_connected = lambda: True
    rpc_server.gateway._connect = lambda: True
    record("网关后端装配 (SimulatedBroker 替身)", sim is rpc_server.gateway.broker)

    import uvicorn

    config = uvicorn.Config(rpc_server.app, host="127.0.0.1", port=RPC_PORT,
                            log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    record(f"RPC 网关启动 (127.0.0.1:{RPC_PORT})", server.started)

    try:
        # ── 2. 客户端链路验证 ──
        from utils.execution.remote_qmt_broker import RemoteQmtBroker

        print("\n[2] RemoteQmtBroker 客户端链路")
        broker = RemoteQmtBroker(rpc_url=RPC_URL, token=TOKEN, timeout=5)
        record("T1 connect (/health + Token 鉴权)", broker.connect())

        bad = RemoteQmtBroker(rpc_url=RPC_URL, token="wrong-token", timeout=5)
        record("T2 错误 Token 被拒 (401)", not bad.connect())

        order = broker.place(BUY_SYMBOL, BUY_QTY, "BUY", order_type="LIMIT",
                             price=BUY_PRICE)
        record(f"T3 限价下单 {BUY_SYMBOL} BUY {BUY_QTY}",
               order is not None and order.order_id != "",
               f"order_id={getattr(order, 'order_id', None)}")

        fill = broker.wait_fill(order, timeout=5) if order else None
        fill_ok = bool(fill and fill.get("qty") == BUY_QTY
                       and fill.get("price", 0) > BUY_PRICE)
        record("T4 等待成交 (BUY 滑点方向正确)", fill_ok,
               f"fill_price={fill.get('price', 0):.4f} > 委托 {BUY_PRICE:.3f}"
               if fill else "未成交")

        order2 = broker.place(CANCEL_SYMBOL, CANCEL_QTY, "BUY", order_type="LIMIT",
                              price=CANCEL_PRICE - 0.05)
        cancelled = broker.cancel(order2) if order2 else False
        record(f"T5 撤单 {CANCEL_SYMBOL}", cancelled,
               f"status={getattr(order2, 'status', None)}")

        positions = broker.get_positions()
        record("T6 持仓查询", positions.get(BUY_SYMBOL) == BUY_QTY,
               f"positions={positions}")

        account = broker.get_account_info()
        expected_avail = CAPITAL - BUY_QTY * (fill.get("price", 0) if fill else 0)
        avail_ok = abs(account.get("available", 0) - expected_avail) < 1.0
        record("T7 账户查询 (资金扣减正确)", avail_ok,
               f"available={account.get('available', 0):.2f} "
               f"(期望≈{expected_avail:.2f})")

        # ── 3. broker_factory 装配逻辑 (生产装配路径) ──
        print("\n[3] broker_factory 装配验证 (双签保护)")
        from utils.execution.broker_factory import get_broker

        os.environ["TRADING_ENV"] = "production"
        os.environ["QMT_RPC_URL"] = RPC_URL
        os.environ["QMT_RPC_TOKEN"] = TOKEN
        prod_broker = get_broker({"enabled": True, "dry_run": False})
        record("T8 production+RPC_URL → RemoteQmtBroker",
               type(prod_broker).__name__ == "RemoteQmtBroker",
               f"实际类型={type(prod_broker).__name__}")

        os.environ["TRADING_ENV"] = "shadow"
        shadow_broker = get_broker({"enabled": True, "dry_run": False})
        record("T9 TRADING_ENV=shadow → 强制降级模拟 (双签保护)",
               type(shadow_broker).__name__ == "SimulatedBroker",
               f"实际类型={type(shadow_broker).__name__}")

        dry_broker = get_broker({"enabled": True, "dry_run": True})
        record("T10 dry_run=true → 影子模拟",
               type(dry_broker).__name__ == "SimulatedBroker",
               f"实际类型={type(dry_broker).__name__}")
    finally:
        server.should_exit = True
        t.join(timeout=5)
        for k in ("TRADING_ENV", "QMT_RPC_URL", "QMT_RPC_TOKEN",
                  "QMT_RPC_HOST", "QMT_RPC_PORT"):
            os.environ.pop(k, None)

    # ── 4. 汇总 + 报告 ──
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "=" * 72)
    print(f"结果: {passed}/{total} 通过" + (" — 执行链路验证 PASS" if passed == total else " — 存在 FAIL"))

    lines = [
        "=" * 76,
        "  QMT 执行链路模拟盘验证报告 — " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "=" * 76,
        "",
        "链路: RemoteQmtBroker ──HTTP+Token──▶ qmt_rpc_server(FastAPI) ──▶ SimulatedBroker",
        "覆盖: 网关启动 / 鉴权 / 下单 / 成交(滑点) / 撤单 / 持仓 / 账户 / 装配双签保护",
        "局限: xtquant→QMT 终端段未覆盖 (需真实 QMT 模拟账户, 见下方切换指引)",
        "",
        f"  {'测试项':<44}{'结果':<8}说明",
        "  " + "-" * 72,
    ]
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        lines.append(f"  {r['name']:<44}{mark:<8}{r['detail']}")
    lines += [
        "  " + "-" * 72,
        f"  合计: {passed}/{total} 通过",
        "",
        "切换真实 QMT 模拟盘三步:",
        "  1) 券商 QMT 客户端登录模拟账户, 保持运行;",
        "  2) .venv 安装 xtquant (QMT 官网 python 库);",
        "  3) config/system_config.json broker 段: enabled=true, dry_run=false,",
        "     account_id=<模拟资金账号>, qmt_path=<QMT userdata_mini 路径>;",
        "     并设 QMT_ACCOUNT_ID/QMT_PATH/QMT_SESSION_ID 环境变量后启动",
        "     python utils/execution/qmt_rpc_server.py --port 8765",
        "=" * 76,
    ]
    out_dir = _PROJECT_ROOT / "reports" / "execution"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"qmt_sim_chain_verification_{datetime.now().strftime('%Y%m%d')}.md"
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    print("报告已写入:", out)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
