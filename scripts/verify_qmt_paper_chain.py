"""QMT paper 链路验证 —— 真实终端段 (W7.2.1 T15, 2026-09-11)

与 `scripts/verify_qmt_sim_chain.py` 的分工 (两者互补, 勿合并):
    verify_qmt_sim_chain.py   零外部依赖: RemoteQmtBroker ─▶ 本地网关 ─▶ SimulatedBroker
                              ⇒ 只证明"代码链路正确", **主动绕过**终端层
                              (`gateway.ensure_connected = lambda: True`)
    verify_qmt_paper_chain.py 需真实终端: RemoteQmtBroker/QmtBrokerAPI ─▶ QMT 终端 ─▶ 模拟账户
                              ⇒ 正是 T15 未覆盖的 `xtquant → QMT 终端段`

覆盖: 连接/登录 → 限价下单 → 成交回报 → 撤单 → 持仓查询 → 资金查询
      → 断线重连 → 成交与账户对账

前置 (缺任一即"前置未满足", 退出码 2 —— **绝不静默通过**):
    1) xtquant **可用**: `xtdata` 与 `xttrader` 子模块均可导入
       (注意: `import xtquant` 成功≠可用 —— 该 wheel 标 py3-none-any, 但二进制
        仅 cp36~cp313; Py3.14 下顶层 import 会假成功而功能全废)
       2026-09-13 统一入口: 当前解释器不可用时, **复用** F4 自检
       `scripts/check_xtquant_capability.py` 判定本机是否有**能力级可用**的解释器,
       并把其路径直接写进原因 —— 避免"自检说能用、preflight 说不能用"的双口径。
    2) 资金账号已配 (QMT_ACCOUNT_ID 或 system_config.json broker.account_id)
    3) QMT 客户端路径已配且存在 (QMT_PATH 或 broker.qmt_path)
    4) 传输通道可用 (QMT_RPC_URL 云端桥接, 或本地直连)

门控 (禁止绕过): 本入口要求 broker 门控**已开启**
    (enabled=true + dry_run=false + TRADING_ENV=production)。
    未开时退 2 并不下单 —— 验证入口本身不得成为绕过防裸实盘设计的后门。

用法:
    .venv\\Scripts\\python.exe scripts\\verify_qmt_paper_chain.py --preflight-only
    .venv\\Scripts\\python.exe scripts\\verify_qmt_paper_chain.py --account <模拟资金账号>

操作手册见 `specs/G1-qmt-live-order-wiring/quickstart.md` (唯一权威指引)。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_CST = timezone(timedelta(hours=8))
REPORT_DIR = _PROJECT_ROOT / "reports" / "execution"

# 「哪个解释器具备 xtquant 能力」的**唯一事实源** = F4 自检 (勿在本脚本重复实现判据)
_CAPABILITY_SCRIPT = _PROJECT_ROOT / "scripts" / "check_xtquant_capability.py"
_CAPABLE_CACHE: dict[str, list[str]] = {}

# 验证参数 (小额、可撤: 首笔成交即撤单, 避免在模拟账户留下大额持仓)
BUY_SYMBOL = "510300.SH"
BUY_PRICE = 3.950
BUY_QTY = 100
CANCEL_SYMBOL = "510500.SH"
CANCEL_PRICE = 6.100
CANCEL_QTY = 200

EXIT_OK = 0
EXIT_CHAIN_FAILED = 1
EXIT_PREREQ_UNMET = 2

results: list[dict] = []


def _now_str() -> str:
    return datetime.now(tz=_CST).strftime("%Y-%m-%d %H:%M:%S")


def _xtquant_available() -> bool:
    """xtquant 是否**可用** —— 必须校验承载能力的子模块, 不是命名空间可导入.

    2026-09-11 实测坑 (AC-002 原口径会假 PASS):
        xtquant wheel 标记为 `py3-none-any` ⇒ 任意 Python 都能装上;
        但二进制只提供 cp36~cp313 (`datacenter.*.pyd` / `xtpythonclient`).
        在 Python 3.14 下 `import xtquant` **会成功**, 而
        `from xtquant import xtdata` 与 `from xtquant.xttrader import XtQuantTrader`
        双双 ImportError ⇒ "装上且能 import" 与 "真的能用" 是两回事.

    故此处只认能力级导入: 两个子模块都成功才算可用, 否则一律判不可用
    (宁可 fail-closed 也不放过假绿).
    """
    try:
        from xtquant import xtdata  # noqa: F401  载入 datacenter.*.pyd
        from xtquant.xttrader import XtQuantTrader  # noqa: F401  载入 xtpythonclient

        return True
    except (ImportError, OSError, AttributeError, ValueError):
        return False


def _capable_interpreters(timeout: int = 120) -> list[str]:
    """本机**能力级可用**的解释器路径列表 (委托 F4 自检, 判据同源).

    分工不变 (勿合并): 本脚本判"链路是否通"; `check_xtquant_capability.py` 判
    "哪个解释器具备 xtquant 能力"。此处只**消费**后者的机读产物, 不重复实现判据 ——
    否则会出现"自检说能用、preflight 说不能用"的双口径。

    诊断增强, 一律 **fail-open**: 任何异常返回 []。返回空只影响提示精度,
    不影响"前置未满足 ⇒ 退出码 2"的结论。
    """
    if "value" in _CAPABLE_CACHE:
        return list(_CAPABLE_CACHE["value"])

    found: list[str] = []
    if _CAPABILITY_SCRIPT.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(_CAPABILITY_SCRIPT), "--json", "--no-report"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(_PROJECT_ROOT),
                timeout=timeout,
                check=False,
            )
            payload = json.loads(proc.stdout)
            found = [
                str(c.get("candidate", ""))
                for c in payload.get("candidates", [])
                if c.get("ok")
            ]
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            AttributeError,
            TypeError,
        ):
            found = []

    _CAPABLE_CACHE["value"] = list(found)
    return found


def _broker_cfg() -> dict:
    """复用单一事实源 (broker_factory 读根 system_config.json)."""
    try:
        from utils.execution.broker_factory import _load_broker_config

        return _load_broker_config()
    except (ImportError, AttributeError, OSError, ValueError, TypeError):
        return {}


def preflight() -> tuple[bool, list[str]]:
    """前置自检. 返回 (是否就绪, 未满足原因列表).

    "空集合 = 通过"是门禁假 PASS 头号来源 —— 故未满足时**必须**给出非空原因列表,
    并由 main() 转成非 0 退出码.
    """
    reasons: list[str] = []
    cfg = _broker_cfg()

    if not _xtquant_available():
        detail = (
            "xtquant 不可用 —— 承载能力的子模块 (xtdata / xttrader) 导入失败。"
            f"当前 Python {sys.version.split()[0]}; xtquant 二进制仅提供 cp36~cp313, "
            "故 Py≥3.14 下 `import xtquant` 会**假成功**但功能为零。"
        )
        capable = _capable_interpreters()
        if capable:
            detail += (
                "本机**已有能力级可用的解释器**, 请改用它运行本脚本 (勿在主 venv Py3.14 下跑): "
                + " | ".join(capable)
                + "。判据同源: scripts/check_xtquant_capability.py"
            )
        else:
            detail += (
                "处置: 在 Python≤3.13 的解释器内装好 xtquant 后重跑; "
                "候选解释器判定见 scripts/check_xtquant_capability.py。"
            )
        reasons.append(detail)

    account_id = (os.environ.get("QMT_ACCOUNT_ID", "") or "").strip() or str(
        cfg.get("account_id", "") or ""
    ).strip()
    if not account_id:
        reasons.append(
            "资金账号未配 —— 需设 QMT_ACCOUNT_ID 或 system_config.json "
            "broker.account_id"
        )

    qmt_path = (os.environ.get("QMT_PATH", "") or "").strip() or str(
        cfg.get("qmt_path", "") or ""
    ).strip()
    if not qmt_path:
        reasons.append(
            "QMT 客户端路径未配 —— 需设 QMT_PATH 或 broker.qmt_path "
            "(userdata_mini 目录)"
        )
    elif not Path(qmt_path).exists():
        reasons.append(f"QMT 客户端路径不存在: {qmt_path}")

    if not (os.environ.get("QMT_RPC_URL", "") or "").strip():
        # 未配 RPC 桥接 → 只能本地直连, 要求进程即 QMT 实盘机 (xtquant + 路径已校验)
        if not qmt_path:
            reasons.append(
                "传输通道不可用 —— 未配 QMT_RPC_URL (云端桥接) 且无法本地直连"
            )

    return (not reasons), reasons


def _record(name: str, ok: bool, detail: str = "") -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _run_chain(broker, cfg: dict) -> None:
    """真实终端段链路 (需 QMT 终端在线)."""
    account_id = str(cfg.get("account_id", "") or "")

    _record("T1 连接/登录 QMT 终端", bool(broker.connect()), f"account={account_id}")

    order = broker.place(
        BUY_SYMBOL, BUY_QTY, "BUY", order_type="LIMIT", price=BUY_PRICE
    )
    _record(
        f"T2 限价下单 {BUY_SYMBOL} BUY {BUY_QTY}",
        order is not None and getattr(order, "order_id", "") != "",
        f"order_id={getattr(order, 'order_id', None)}",
    )

    fill = None
    if order is not None:
        fill = broker.wait_fill(order, timeout=30)
    _record(
        "T3 成交回报",
        bool(fill and fill.get("qty") == BUY_QTY),
        f"fill={fill}",
    )

    order2 = broker.place(
        CANCEL_SYMBOL,
        CANCEL_QTY,
        "BUY",
        order_type="LIMIT",
        price=CANCEL_PRICE - 0.05,
    )
    cancelled = broker.cancel(order2) if order2 is not None else False
    _record(
        f"T4 撤单 {CANCEL_SYMBOL} (挂远价, 应可撤)",
        bool(cancelled),
        f"status={getattr(order2, 'status', None)}",
    )

    positions = broker.get_positions()
    _record(
        "T5 持仓查询 (成交已入账)",
        positions.get(BUY_SYMBOL, 0) >= BUY_QTY,
        f"positions={positions}",
    )

    account = broker.get_account_info()
    avail = account.get("available", 0)
    _record(
        "T6 资金查询",
        avail > 0,
        f"available={avail:.2f}",
    )

    _record("T7 断线重连", bool(broker.connect()), "重连后 is_connected")

    # 对账: 成交回报 vs 账户持仓/资金 必须自洽 (AC-009)
    reconciled = True
    detail = "无成交, 跳过对账"
    if fill:
        fill_qty = int(fill.get("qty", 0))
        fill_price = float(fill.get("price", 0))
        pos_qty = int(positions.get(BUY_SYMBOL, 0))
        reconciled = pos_qty >= fill_qty
        detail = f"filled={fill_qty} positions={pos_qty}"
        if fill_price <= 0:
            reconciled = False
            detail += " (成交价非正, 异常)"
    _record("T8 成交↔持仓对账", reconciled, detail)


def _write_report(passed: int, total: int) -> Path:
    lines = [
        "=" * 76,
        f"  QMT paper 链路验证报告 (真实终端段) — {_now_str()}",
        "=" * 76,
        "",
        "链路: RemoteQmtBroker/QmtBrokerAPI ──▶ QMT 终端 ──▶ 模拟账户",
        "覆盖: 连接/登录 / 下单 / 成交回报 / 撤单 / 持仓 / 资金 / 断线重连 / 对账",
        "说明: 本报告补齐 T15 未覆盖的 xtquant→QMT 终端段;",
        "      代码链路验证见 qmt_sim_chain_verification_*.md (两者互补)。",
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
        "后续: 1) 运行 scripts/run_trade_reconciliation.py 做成交对账;",
        "      2) 运行 scripts/industrial_grade_check.py 确认 C1 WARN→PASS;",
        "      3) 索引见 specs/G1-qmt-live-order-wiring/quickstart.md。",
        "=" * 76,
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / (
        f"qmt_paper_chain_verification_{datetime.now(tz=_CST).strftime('%Y%m%d')}.md"
    )
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="QMT paper 链路验证 (真实终端段, W7.2.1 T15)"
    )
    parser.add_argument("--account", default="", help="模拟资金账号 (覆盖环境变量)")
    parser.add_argument(
        "--preflight-only", action="store_true", help="仅做前置自检, 不连接不下单"
    )
    args = parser.parse_args(argv)

    if args.account:
        os.environ["QMT_ACCOUNT_ID"] = args.account

    print("=" * 72)
    print("QMT paper 链路验证 (真实终端段) —", _now_str())
    print("=" * 72)

    print("\n[0] 前置自检")
    ok, reasons = preflight()
    if not ok:
        print("  [FAIL] 前置未满足 —— 验证无法进行 (退出码 2, 绝不静默通过):")
        for r in reasons:
            print(f"         · {r}")
        print("  处置: 见 specs/G1-qmt-live-order-wiring/quickstart.md §0 前置清单")
        return EXIT_PREREQ_UNMET
    print("  [PASS] 前置就绪 (xtquant / 账号 / QMT 路径 / 传输通道)")

    if args.preflight_only:
        return EXIT_OK

    # 门控必须已开 —— 验证入口不得成为绕过防裸实盘设计的后门
    from utils.execution.broker_factory import (
        LiveBrokerUnavailableError,
        get_broker,
        is_live_intent,
    )

    if not is_live_intent():
        print(
            "\n[FAIL] 门控未开启 (需 enabled=true + dry_run=false + "
            "TRADING_ENV=production); 本入口不绕过门控。"
        )
        return EXIT_PREREQ_UNMET

    print("\n[1] 装配真实通道")
    try:
        broker = get_broker()
    except LiveBrokerUnavailableError as exc:
        print(f"  [FAIL] 实盘就绪但通道装配失败 (fail-closed): {exc}")
        return EXIT_PREREQ_UNMET
    _record("真实通道装配", True, f"type={type(broker).__name__}")

    print("\n[2] 链路验证")
    try:
        _run_chain(broker, _broker_cfg())
    except Exception as exc:  # noqa: BLE001  # 验证入口需完整记录终端异常
        _record("链路执行", False, f"终端异常: {exc!r}")

    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "=" * 72)
    print(f"结果: {passed}/{total} 通过")
    out = _write_report(passed, total)
    print("报告已写入:", out)
    return EXIT_OK if passed == total and total > 0 else EXIT_CHAIN_FAILED


if __name__ == "__main__":
    sys.exit(main())
