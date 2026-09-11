"""报告项 15 (批次三) — 模拟/实盘对账任务执行器.

背景 (`代码质量审计报告_20260909.md` 第 15 项):
    T13 (订单级对账器) 与 T17 (实盘对账循环) 早已实现且门禁自检通过, 但从未被
    任何生产入口/调度调用 → 属"有组件无任务"的调度断链。本模块把它变成可调度
    的真实任务: 读当日 `trade_plan` 计划单 + `FillsStore` 成交回报, 执行 4 维度
    对账 (覆盖/数量/价格/孤儿成交), 落盘 `reports/reconciliation/reconciliation_<date>.json`。

数据契约与已知缺口 (如实记录, 不粉饰):
    1. `trade_plan` 的 `execution_plan.morning_orders/afternoon_orders` **无 order_id**
       → 按确定性规则合成 `{date}_{session}_{code}_{side}_{idx}`。
    2. `FillsStore` JSONL 顶层仅 `symbol/side/...`, order_id 只出现在 `meta.order_id`
       (仅 rebalance 路径写入) → 成交侧 order_id 覆盖率不足阈值时, 自动降级为
       `(symbol, side)` 聚合对账, 并在报告中标记 `linkage_mode="symbol_side"` +
       `linkage_degraded=True`。**订单级精度不足时绝不上报"订单级通过"**。
    3. 期权对冲单 (hedge_execution.options_orders / covered_call_orders) 由
       `hedge_order_executor` 撮合, 但**未落盘 FillsStore** → 默认 `scope="etf"`
       不纳入, 避免制造假 ORDER_COVERAGE; `scope="all"` 纳入并显式附注该缺口。

计数口径 (绝不假 PASS):
    status ∈ {ok, no_plan, no_planned_orders, no_fills} — 任一环节缺数据即如实标记,
    `verified` 仅在 status=="ok" 且 issues 为空时为 True。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from utils.risk.trade_order_reconciler import (
    FillRecord,
    PlannedOrder,
    TradeOrderReconciler,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADE_PLANS_DIR = _PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
FILLS_DIR = _PROJECT_ROOT / "reports" / "fills"
OUTPUT_DIR = _PROJECT_ROOT / "reports" / "reconciliation"
POSITIONS_FILE = _PROJECT_ROOT / "config" / "positions.json"

# 成交侧 order_id 覆盖率低于该阈值 → 降级 (symbol, side) 聚合对账
LINKAGE_MIN_COVERAGE = 0.5
REPORT_VERSION = "1.0"
VALID_SCOPES = ("etf", "all")

_SYMBOL_RE = re.compile(r"\d{6}")


# ----------------------------------------------------------------------
# 归一化辅助
# ----------------------------------------------------------------------
def _norm_symbol(raw: Any) -> str:
    """归一化标的: 提取 6 位数字主干 (588000 / 600519.SH / sh600519 → 588000/600519)."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    m = _SYMBOL_RE.search(text)
    return m.group(0) if m else text.upper()


def _norm_side(raw: Any) -> str:
    """归一化买卖方向 → BUY / SELL (对账器按大写比较)."""
    text = str(raw or "BUY").strip().upper()
    if text in ("B", "BUY", "BUY_PUT"):
        return "BUY"
    if text in ("S", "SELL", "SELL_CALL_COVERED"):
        return "SELL"
    return text


def _compact(date_str: str) -> str:
    return str(date_str).replace("-", "").replace("/", "")


def plan_path_for(date_str: str, plan_path: str | Path | None = None) -> Path:
    """解析当日交易计划文件路径 (显式路径优先)."""
    if plan_path:
        return Path(plan_path)
    return TRADE_PLANS_DIR / f"trade_plan_{_compact(date_str)}.json"


def fills_path_for(date_str: str, fills_path: str | Path | None = None) -> Path:
    """解析当日成交回报文件路径 (显式路径优先)."""
    if fills_path:
        return Path(fills_path)
    return FILLS_DIR / f"fills_{date_str}.jsonl"


# ----------------------------------------------------------------------
# 数据载入
# ----------------------------------------------------------------------
def load_plan(date_str: str, plan_path: str | Path | None = None) -> dict[str, Any] | None:
    """载入交易计划; 不存在/非法 JSON → None (调用方如实标记 no_plan)."""
    p = plan_path_for(date_str, plan_path)
    if not p.exists():
        logger.warning("[reconcile] 交易计划不存在: %s", p)
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("[reconcile] 交易计划解析失败 %s: %s", p, exc)
        return None
    return data if isinstance(data, dict) else None


def build_planned_orders(
    plan: dict[str, Any], date_str: str, scope: str = "etf"
) -> list[PlannedOrder]:
    """从 trade_plan 提取计划指令单.

    Args:
        plan: 交易计划 dict
        date_str: 交易日 (YYYY-MM-DD), 用于合成 order_id
        scope: "etf" = 仅股票/ETF (可与 FillsStore 对齐);
               "all" = 额外纳入期权对冲单 (附未接入 FillsStore 的显式缺口提示)

    Returns:
        PlannedOrder 列表 (股票/ETF 单 order_id 为合成值, 期权单沿用计划内 order_id)
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope ∈ {VALID_SCOPES}, 实际 {scope!r}")

    compact = _compact(date_str)
    orders: list[PlannedOrder] = []

    ep = plan.get("execution_plan") or {}
    for session in ("morning", "afternoon"):
        for idx, o in enumerate(ep.get(f"{session}_orders") or [], 1):
            if not isinstance(o, dict):
                continue
            code = _norm_symbol(o.get("code") or o.get("symbol"))
            qty = int(o.get("shares") or o.get("qty") or 0)
            if not code or qty <= 0:
                continue
            side = _norm_side(o.get("side") or "BUY")
            limit = o.get("limit_price")
            orders.append(
                PlannedOrder(
                    order_id=f"{compact}_{session}_{code}_{side}_{idx:02d}",
                    symbol=code,
                    side=side,
                    planned_qty=qty,
                    limit_price=float(limit) if limit else None,
                )
            )

    if scope == "all":
        he = plan.get("hedge_execution") or {}
        for key in ("options_orders", "covered_call_orders"):
            for o in he.get(key) or []:
                if not isinstance(o, dict):
                    continue
                oid = str(o.get("order_id") or "").strip()
                code = _norm_symbol(o.get("underlying_code") or o.get("underlying"))
                contracts = int(o.get("contracts") or 0)
                if not oid or not code or contracts <= 0:
                    continue
                premium = o.get("premium_per_unit") or o.get("limit_price")
                orders.append(
                    PlannedOrder(
                        order_id=oid,
                        symbol=code,
                        side=_norm_side(o.get("direction") or "BUY"),
                        planned_qty=contracts,
                        limit_price=float(premium) if premium else None,
                    )
                )
    return orders


def load_fill_records(
    date_str: str, fills_path: str | Path | None = None
) -> list[FillRecord]:
    """载入当日成交回报 (含 `meta.order_id` 关联字段), 非法行跳过并告警."""
    p = fills_path_for(date_str, fills_path)
    fills: list[FillRecord] = []
    if not p.exists():
        logger.warning("[reconcile] 成交回报不存在: %s", p)
        return fills
    with open(p, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("[reconcile] 跳过非法 JSONL 行 %s:%d", p.name, lineno)
                continue
            if not isinstance(d, dict):
                continue
            raw_meta = d.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            fills.append(
                FillRecord(
                    fill_id=str(
                        d.get("fill_id") or d.get("id") or f"{date_str}-{lineno}"
                    ),
                    order_id=str(
                        d.get("order_id")
                        or d.get("parent_order_id")
                        or meta.get("order_id")
                        or ""
                    ),
                    symbol=_norm_symbol(d.get("symbol") or d.get("code")),
                    side=_norm_side(d.get("side") or "BUY"),
                    filled_qty=int(float(d.get("filled_qty") or d.get("qty") or 0)),
                    avg_price=float(d.get("avg_price") or d.get("price") or 0.0),
                    timestamp=str(d.get("ts") or d.get("timestamp") or ""),
                )
            )
    return fills


# ----------------------------------------------------------------------
# 关联模式判定
# ----------------------------------------------------------------------
def linkage_coverage(fills: list[FillRecord]) -> float:
    """成交侧 order_id 覆盖率 ∈ [0, 1]; 无成交返回 0.0."""
    if not fills:
        return 0.0
    linked = sum(1 for f in fills if f.order_id)
    return linked / len(fills)


def aggregate_symbol_side(
    orders: list[PlannedOrder], fills: list[FillRecord]
) -> tuple[list[PlannedOrder], list[FillRecord]]:
    """把计划单与成交按 (symbol, side) 聚合, 用合成 order_id 对齐.

    用于成交侧缺失 order_id 的场景: 保留 T13 四维度检查的语义 (数量偏差/价格偏差/
    未覆盖计划/孤儿成交), 只是粒度从"订单级"降为"标的×方向级"。
    """
    agg_orders: dict[str, PlannedOrder] = {}
    for o in orders:
        key = f"{o.symbol}|{o.side}"
        if key in agg_orders:
            prev = agg_orders[key]
            prev.planned_qty += o.planned_qty
            if prev.limit_price is None:
                prev.limit_price = o.limit_price
        else:
            agg_orders[key] = PlannedOrder(
                order_id=key,
                symbol=o.symbol,
                side=o.side,
                planned_qty=o.planned_qty,
                limit_price=o.limit_price,
            )

    agg_fills: dict[str, FillRecord] = {}
    for f in fills:
        key = f"{f.symbol}|{f.side}"
        if key in agg_fills:
            prev_fill = agg_fills[key]
            total_qty = prev_fill.filled_qty + f.filled_qty
            if total_qty > 0:
                prev_fill.avg_price = (
                    prev_fill.avg_price * prev_fill.filled_qty + f.avg_price * f.filled_qty
                ) / total_qty
            prev_fill.filled_qty = total_qty
        else:
            agg_fills[key] = FillRecord(
                fill_id=f"{key}#1",
                order_id=key,
                symbol=f.symbol,
                side=f.side,
                filled_qty=f.filled_qty,
                avg_price=f.avg_price,
                timestamp=f.timestamp,
            )
    return list(agg_orders.values()), list(agg_fills.values())


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def reconcile_date(
    date_str: str,
    plan_path: str | Path | None = None,
    fills_path: str | Path | None = None,
    scope: str = "etf",
    qty_tol: float = 0.10,
    price_tol_bps: float = 50.0,
    write: bool = True,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """执行单日对账并 (可选) 落盘报告.

    Returns:
        dict: 对账报告 (含 status / linkage_mode / issues / verified 等字段)
    """
    plan = load_plan(date_str, plan_path)
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "date": date_str,
        "scope": scope,
        "linkage_mode": "order_id",
        "linkage_degraded": False,
        "linkage_coverage": 0.0,
        "status": "ok",
        "verified": False,
        "plan_file": str(plan_path_for(date_str, plan_path)),
        "fills_file": str(fills_path_for(date_str, fills_path)),
        "planned_count": 0,
        "fills_count": 0,
        "issues_count": 0,
        "issues": [],
        "unexpected_fills": [],
        "items": [],
        "notes": [],
    }

    if plan is None:
        report["status"] = "no_plan"
        report["notes"].append("交易计划缺失或非法 → 无法对账 (不下 PASS 结论)")
        if write:
            _write_report(report, date_str, output_dir)
        return report

    orders = build_planned_orders(plan, date_str, scope=scope)
    fills = load_fill_records(date_str, fills_path)
    report["planned_count"] = len(orders)
    report["fills_count"] = len(fills)

    if scope == "all":
        report["notes"].append(
            "期权对冲单已纳入, 但 hedge_order_executor 暂未落盘 FillsStore, "
            "期权单会出现 ORDER_COVERAGE (属已知接口缺口, 非真实漏成交)"
        )

    if not orders:
        report["status"] = "no_planned_orders"
        report["notes"].append("计划中无可对账指令单 → 无法对账 (不下 PASS 结论)")
        if write:
            _write_report(report, date_str, output_dir)
        return report

    # 关联模式: 成交侧 order_id 覆盖率不足 → 降级 (symbol, side) 聚合
    coverage = linkage_coverage(fills)
    report["linkage_coverage"] = round(coverage, 4)
    if fills and coverage < LINKAGE_MIN_COVERAGE:
        report["linkage_mode"] = "symbol_side"
        report["linkage_degraded"] = True
        report["notes"].append(
            f"成交侧 order_id 覆盖率 {coverage:.1%} < {LINKAGE_MIN_COVERAGE:.0%}, "
            "已降级为 (symbol, side) 聚合对账 — 精度为标的方向级, 非订单级"
        )
        orders, fills = aggregate_symbol_side(orders, fills)

    if not fills:
        report["status"] = "no_fills"
        report["notes"].append(
            "当日无任何成交回报 → 计划单全部未覆盖 (如实标记, 不判 PASS)"
        )

    reconciler = TradeOrderReconciler(
        qty_deviation_pct=qty_tol, price_deviation_bps=price_tol_bps
    )
    result = reconciler.reconcile(date_str, orders, fills)

    report["issues"] = list(result.issues_summary)
    report["issues_count"] = len(result.issues_summary)
    report["unexpected_fills"] = [
        {
            "fill_id": f.fill_id,
            "symbol": f.symbol,
            "side": f.side,
            "filled_qty": f.filled_qty,
            "avg_price": f.avg_price,
        }
        for f in result.unexpected_fills
    ]
    report["items"] = [
        {
            "order_id": it.order_id,
            "symbol": it.symbol,
            "planned_qty": it.planned_qty,
            "filled_qty": it.filled_qty,
            "planned_price": it.planned_price,
            "avg_fill_price": it.avg_fill_price,
            "qty_deviation_pct": round(it.qty_deviation_pct, 6),
            "price_deviation_bps": it.price_deviation_bps,
            "issues": list(it.issues),
        }
        for it in result.items.values()
    ]

    report["verified"] = report["status"] == "ok" and report["issues_count"] == 0
    if write:
        _write_report(report, date_str, output_dir)
    return report


def report_path_for(
    date_str: str, scope: str = "etf", output_dir: str | Path | None = None
) -> Path:
    """报告落盘路径 (按 scope 区分文件名, 避免不同 scope 互相覆盖)."""
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    return out_dir / f"reconciliation_{date_str}_{scope}.json"


def _write_report(
    report: dict[str, Any], date_str: str, output_dir: str | Path | None = None
) -> Path | None:
    """落盘报告 (观测路径 fail-open: 写盘失败只告警, 不抛).

    文件名含 scope: EOD 产出的 `_etf` 报告不会被手工 `--scope all` 运行静默覆盖.
    """
    path = report_path_for(date_str, str(report.get("scope", "etf")), output_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        report["report_path"] = str(path)
        return path
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("[reconcile] 报告落盘失败 %s: %s", path, exc)
        return None


# ----------------------------------------------------------------------
# T17 持仓 drift 对账 (显式调用, 需真实 broker 接口)
# ----------------------------------------------------------------------
def load_local_position_book(positions_file: str | Path | None = None) -> dict[str, int]:
    """从 config/positions.json 构建本地持仓账本 {symbol: shares}."""
    p = Path(positions_file) if positions_file else POSITIONS_FILE
    book: dict[str, int] = {}
    if not p.exists():
        logger.warning("[reconcile] 本地持仓账本不存在: %s", p)
        return book
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("[reconcile] 本地持仓账本解析失败: %s", exc)
        return book
    positions = data.get("positions") if isinstance(data, dict) else None
    if not isinstance(positions, dict):
        return book
    for code, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        shares = pos.get("shares") or pos.get("total_shares") or 0
        try:
            qty = int(float(shares))
        except (TypeError, ValueError):
            qty = 0
        if qty:
            book[_norm_symbol(code)] = qty
    return book


def run_position_drift(
    broker: Any,
    local_book: dict[str, int] | None = None,
    drift_alert_pct: float = 0.02,
    drift_halt_pct: float = 0.10,
) -> dict[str, Any]:
    """调用 T17 `LiveReconciliationLoop.detect_position_drift` 做持仓 drift 对账.

    Returns:
        {"available": bool, "verdict": str, "drifts": [...], "halt_count": int, "note": str}
    """
    from utils.risk.live_reconciliation_loop import (
        LiveReconciliationLoop,
        RiskAuditLogger,
    )
    from utils.risk.trade_order_reconciler import TradeOrderReconciler as _Reconciler

    book = local_book if local_book is not None else load_local_position_book()
    out: dict[str, Any] = {
        "available": False,
        "verdict": "unknown",
        "halt_count": 0,
        "drift_count": 0,
        "drifts": [],
        "note": "",
    }
    if broker is None:
        out["note"] = "无 broker 接口 → 跳过持仓 drift 对账"
        return out
    if not book:
        out["note"] = "本地持仓账本为空 → 跳过持仓 drift 对账"
        return out
    try:
        loop = LiveReconciliationLoop(
            reconciler=_Reconciler(),
            broker=broker,
            local_position_book=book,
            audit_logger=RiskAuditLogger(),
            drift_alert_pct=drift_alert_pct,
            drift_halt_pct=drift_halt_pct,
        )
        drifts = loop.detect_position_drift()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:  # 观测路径 fail-open
        out["note"] = f"持仓 drift 对账失败 (fail-open): {exc}"
        logger.warning("[reconcile] %s", out["note"])
        return out

    out["available"] = True
    out["drifts"] = [
        {
            "symbol": d.symbol,
            "local_qty": d.local_qty,
            "broker_qty": d.broker_qty,
            "drift_qty": d.drift_qty,
            "drift_pct": round(d.drift_pct, 6),
            "suggested_action": d.suggested_action,
        }
        for d in drifts
    ]
    out["drift_count"] = sum(1 for d in drifts if d.has_drift)
    out["halt_count"] = sum(1 for d in drifts if d.suggested_action == "halt")
    out["verdict"] = "halt" if out["halt_count"] else ("warn" if out["drift_count"] else "pass")
    return out


def is_live_intent() -> bool:
    """执行器入口 fail-closed 校验复用: 当前是否"真实下单就绪"."""
    from utils.execution.broker_factory import is_live_intent as _is_live

    return _is_live()


def format_summary(report: dict[str, Any]) -> str:
    """人类可读摘要 (供 CLI / EOD 日志)."""
    lines = [
        f"[对账] {report['date']} scope={report['scope']} status={report['status']}",
        f"  关联模式={report['linkage_mode']}"
        f"(覆盖率 {report['linkage_coverage']:.1%})"
        f" 计划单={report['planned_count']} 成交={report['fills_count']}",
        f"  问题={report['issues_count']} 孤儿成交={len(report['unexpected_fills'])}"
        f" verified={report['verified']}",
    ]
    for note in report.get("notes", []):
        lines.append(f"  · {note}")
    for issue in report.get("issues", [])[:10]:
        lines.append(f"  ! {issue}")
    if len(report.get("issues", [])) > 10:
        lines.append(f"  ! ... 其余 {len(report['issues']) - 10} 条见报告 JSON")
    if report.get("report_path"):
        lines.append(f"  报告: {report['report_path']}")
    return "\n".join(lines)


def strict_exit_code(report: dict[str, Any]) -> int:
    """严格模式退出码: 仅在真实验证通过时 0, 否则 1.

    `RECONCILE_STRICT=1` 或"真实下单就绪"时应以该退出码判定 (不得假 PASS);
    默认观察模式由 CLI 决定 (始终 0, 只做记录)。
    """
    strict_env = os.environ.get("RECONCILE_STRICT", "").strip().lower() in ("1", "true", "yes")
    if not strict_env and not is_live_intent():
        return 0
    return 0 if report.get("verified") else 1
