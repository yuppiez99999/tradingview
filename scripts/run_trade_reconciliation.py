#!/usr/bin/env python
"""CLI — 模拟/实盘对账任务 (报告项 15, 批次三 2026-09-10).

把 T13 (订单级对账) / T17 (持仓 drift 对账) 从"有组件无任务"接入为可调度任务。

用法:
    python scripts/run_trade_reconciliation.py --date 2026-09-10
    python scripts/run_trade_reconciliation.py --date 2026-09-10 --scope all
    python scripts/run_trade_reconciliation.py --date 2026-09-10 --with-drift
    $env:RECONCILE_STRICT="1"; python scripts/run_trade_reconciliation.py --date 2026-09-10

退出码:
    0 观察模式 (仅记录) / 严格模式且 verified=True
    1 严格模式 (RECONCILE_STRICT=1 或"真实下单就绪") 且未通过
    2 参数或运行错误
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.datetime_utils import now_bj  # noqa: E402
from utils.risk.trade_reconciliation_runner import (  # noqa: E402
    format_summary,
    is_live_intent,
    reconcile_date,
    run_position_drift,
    strict_exit_code,
)

logger = logging.getLogger("run_trade_reconciliation")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="模拟/实盘对账任务 (T13 + T17)")
    p.add_argument(
        "--date",
        default=now_bj().strftime("%Y-%m-%d"),
        help="交易日 YYYY-MM-DD (默认今天)",
    )
    p.add_argument("--plan", default=None, help="交易计划 JSON 路径 (默认按日期解析)")
    p.add_argument("--fills", default=None, help="成交回报 JSONL 路径 (默认按日期解析)")
    p.add_argument("--scope", default="etf", choices=("etf", "all"), help="对账范围")
    p.add_argument(
        "--qty-tol", type=float, default=0.10, help="数量偏差阈值 (默认 0.10 = 10%%)"
    )
    p.add_argument(
        "--price-tol-bps", type=float, default=50.0, help="价格偏差阈值 bps (默认 50)"
    )
    p.add_argument(
        "--with-drift",
        action="store_true",
        help="额外执行 T17 持仓 drift 对账 (需 broker 接口可用)",
    )
    p.add_argument("--out-dir", default=None, help="报告输出目录 (默认 reports/reconciliation)")
    return p.parse_args(argv)


def _alert(report: dict, drift: dict | None) -> None:
    """实盘就绪或严格模式下, 未通过时推送告警 (观测路径 fail-open)."""
    try:
        from utils.notify import send_alert

        head = (
            f"[对账未通过] {report['date']} status={report['status']} "
            f"问题 {report['issues_count']} 项"
        )
        body = format_summary(report)
        if drift and drift.get("available"):
            body += f"\n持仓 drift: {drift['drift_count']} (halt {drift['halt_count']})"
        send_alert(title="[WARNING] 模拟/实盘对账未通过", content=f"{head}\n{body}", level="warning")
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        # 告警通道 fail-open, 不影响对账结论
        logger.warning("告警发送失败 (fail-open): %s", exc)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    strict_env = os.environ.get("RECONCILE_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    live = is_live_intent()

    report = reconcile_date(
        args.date,
        plan_path=args.plan,
        fills_path=args.fills,
        scope=args.scope,
        qty_tol=args.qty_tol,
        price_tol_bps=args.price_tol_bps,
        write=True,
        output_dir=args.out_dir,
    )

    drift: dict | None = None
    if args.with_drift:
        broker = None
        try:
            from utils.execution.broker_factory import get_broker

            broker = get_broker()
        except (
            ImportError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
        ) as exc:
            # 观测路径 fail-open: 实盘就绪装配失败也仅记录, 不阻断对账报告产出
            logger.warning("drift 对账 broker 装配失败 (fail-open): %s", exc)
        drift = run_position_drift(broker)
        report["position_drift"] = drift

    print(format_summary(report))
    if drift is not None:
        print(
            f"  [drift] available={drift['available']} verdict={drift['verdict']} "
            f"drift={drift['drift_count']} halt={drift['halt_count']} {drift['note']}"
        )

    need_alert = (strict_env or live) and not report.get("verified")
    if drift and drift.get("verdict") == "halt":
        need_alert = True
    if need_alert:
        _alert(report, drift)

    return strict_exit_code(report)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
