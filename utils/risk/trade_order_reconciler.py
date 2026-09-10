"""T13 盘后对账器 — 指令单 vs fills 成交回报的差异闭环告警.

属于「不崩风控六件套」第 5 位, 核心目的: **收盘后必须知道「计划下的单」和「真实成交」之间的差距,
 不得有漏成交、错成交无人知晓**.

对账维度 (4 项):
    1. ORDER_COVERAGE  : 所有计划指令单是否至少有 1 笔 fill 对应 (允许部分成交)
    2. QTY_DEVIATION   : 指令计划数量 vs 实际成交数量的偏差率 (默认阈值 10%, 避免成交过多/过少)
    3. PRICE_DEVIATION : 指令限价 vs 真实成交均价的偏差 bps (默认阈值 50bps)
    4. UNEXPECTED_FILLS: fills 中存在无法映射到任何计划指令单的异常成交 (防止错撮合/下错账户)

输出:
    ReconciliationReport, 包含 4 个维度的通过/失败 + 明细清单, 供 EOD 报告章节引用.

零行为变更: 只读 fills 文件 + 只读指令单计划 JSON, 不改任何对象, 不触发任何交易.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from utils.datetime_utils import now_bj

logger = logging.getLogger("trade_reconciler")


@dataclass
class PlannedOrder:
    """盘前/盘中生成的计划指令单 (最小字段集)."""

    order_id: str
    symbol: str
    side: str  # buy / sell
    planned_qty: int
    limit_price: float | None = None


@dataclass
class FillRecord:
    """单笔成交回报 (与 reports/fills/fills_*.jsonl 的字段对齐)."""

    fill_id: str
    order_id: str
    symbol: str
    side: str
    filled_qty: int
    avg_price: float
    timestamp: str = ""


@dataclass
class ReconciliationItem:
    order_id: str
    symbol: str
    planned_qty: int = 0
    filled_qty: int = 0
    planned_price: float | None = None
    avg_fill_price: float | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def qty_deviation_pct(self) -> float:
        if self.planned_qty == 0:
            return 0.0 if self.filled_qty == 0 else float("inf")
        return (self.filled_qty - self.planned_qty) / self.planned_qty

    @property
    def price_deviation_bps(self) -> float | None:
        if not (self.planned_price and self.avg_fill_price):
            return None
        return (self.avg_fill_price - self.planned_price) / self.planned_price * 10_000


@dataclass
class ReconciliationReport:
    date: str
    total_planned: int = 0
    total_filled_orders: int = 0
    unexpected_fills: list[FillRecord] = field(default_factory=list)
    items: dict[str, ReconciliationItem] = field(default_factory=dict)
    issues_summary: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: now_bj().isoformat(timespec="seconds")
    )

    @property
    def all_pass(self) -> bool:
        return not self.issues_summary and not self.unexpected_fills

    def summary_text(self) -> str:
        lines = [
            f"=== 对账报告 {self.date} ===",
            f"计划单数: {self.total_planned}, 有成交的: {self.total_filled_orders}",
            f"未映射 fill: {len(self.unexpected_fills)}",
            f"总问题数: {len(self.issues_summary)}",
        ]
        lines.extend(f"  - {iss}" for iss in self.issues_summary)
        if self.unexpected_fills:
            lines.append("未映射成交明细:")
            for f in self.unexpected_fills[:10]:
                lines.append(
                    f"  fill_id={f.fill_id} symbol={f.symbol} side={f.side} "
                    f"qty={f.filled_qty}@{f.avg_price:.2f}"
                )
        return "\n".join(lines)


class TradeOrderReconciler:
    """T13 盘后对账器 — 4 维度差异检查."""

    def __init__(
        self,
        qty_deviation_pct: float = 0.10,
        price_deviation_bps: float = 50.0,
    ) -> None:
        if not (0 <= qty_deviation_pct <= 1.0):
            raise ValueError(f"qty_deviation_pct ∈ [0, 1], 实际 {qty_deviation_pct}")
        if price_deviation_bps < 0:
            raise ValueError(f"price_deviation_bps ≥ 0, 实际 {price_deviation_bps}")
        self.qty_tol = qty_deviation_pct
        self.price_tol_bps = price_deviation_bps

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def reconcile(
        self,
        date: str,
        planned_orders: Iterable[PlannedOrder],
        fills: Iterable[FillRecord],
    ) -> ReconciliationReport:
        """执行对账, 返回报告."""
        report = ReconciliationReport(date=date)
        planned_list = list(planned_orders)
        fills_list = list(fills)
        report.total_planned = len(planned_list)

        # Step 1: 构建 items (计划单 → 聚合 fills)
        fills_by_order: dict[str, list[FillRecord]] = {}
        used_fill_ids: set[str] = set()
        for f in fills_list:
            fills_by_order.setdefault(f.order_id, []).append(f)

        for po in planned_list:
            item = ReconciliationItem(
                order_id=po.order_id,
                symbol=po.symbol,
                planned_qty=po.planned_qty,
                planned_price=po.limit_price,
            )
            matching = fills_by_order.get(po.order_id, [])
            total_qty = 0
            if matching:
                total_qty = sum(f.filled_qty for f in matching)
                total_notional = sum(f.filled_qty * f.avg_price for f in matching)
                item.filled_qty = total_qty
                item.avg_fill_price = (
                    total_notional / total_qty if total_qty > 0 else None
                )
                used_fill_ids.update(f.fill_id for f in matching)
                report.total_filled_orders += 1

            # 检查: 1. ORDER_COVERAGE (完全没成交 → warning)
            if total_qty == 0 and po.planned_qty > 0:
                issue = (
                    f"[ORDER_COVERAGE] {po.symbol} order_id={po.order_id} "
                    f"计划 {po.planned_qty} 股, 实际 0 成交"
                )
                item.issues.append(issue)
                report.issues_summary.append(issue)

            # 检查: 2. QTY_DEVIATION
            if po.planned_qty > 0 and abs(item.qty_deviation_pct) > self.qty_tol:
                issue = (
                    f"[QTY_DEVIATION] {po.symbol} order_id={po.order_id} "
                    f"数量偏差 {item.qty_deviation_pct:.2%} 超阈值 ±{self.qty_tol:.2%} "
                    f"(计划 {po.planned_qty} / 成交 {item.filled_qty})"
                )
                item.issues.append(issue)
                report.issues_summary.append(issue)

            # 检查: 3. PRICE_DEVIATION (仅当有限价时)
            if item.planned_price is not None and item.avg_fill_price is not None:
                p_bps = item.price_deviation_bps
                if p_bps is not None and abs(p_bps) > self.price_tol_bps:
                    issue = (
                        f"[PRICE_DEVIATION] {po.symbol} order_id={po.order_id} "
                        f"价差 {p_bps:.1f}bps 超阈值 ±{self.price_tol_bps:.1f}bps "
                        f"(限价 {item.planned_price:.2f} / 成交价 {item.avg_fill_price:.2f})"
                    )
                    item.issues.append(issue)
                    report.issues_summary.append(issue)

            report.items[po.order_id] = item

        # Step 2: 未映射 fill
        for f in fills_list:
            if f.fill_id not in used_fill_ids:
                report.unexpected_fills.append(f)
        if report.unexpected_fills:
            report.issues_summary.append(
                f"[UNEXPECTED_FILLS] 发现 {len(report.unexpected_fills)} 笔无法映射到任何计划指令单的成交, "
                "请核查是否为错撮合/下错账户"
            )

        if report.issues_summary:
            logger.warning(
                f"[T13] 对账 {date} 发现 {len(report.issues_summary)} 个问题 + "
                f"{len(report.unexpected_fills)} 笔未映射成交"
            )
        else:
            logger.info(f"[T13] 对账 {date} 全部通过")
        return report

    # ------------------------------------------------------------
    # 辅助: 从 FillsStore 读 fills (可选, 供 EOD 直接调用)
    # ------------------------------------------------------------

    @staticmethod
    def load_fills_from_jsonl(path: str | Path) -> list[FillRecord]:
        """从 JSONL 文件加载 fills (字段按最小交集映射)."""
        p = Path(path)
        fills: list[FillRecord] = []
        if not p.exists():
            return fills
        import json

        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fills.append(
                    FillRecord(
                        fill_id=str(d.get("fill_id") or d.get("id") or ""),
                        order_id=str(
                            d.get("order_id") or d.get("parent_order_id") or ""
                        ),
                        symbol=str(d.get("symbol") or d.get("code") or ""),
                        side=str(d.get("side") or "buy"),
                        filled_qty=int(d.get("filled_qty") or d.get("qty") or 0),
                        avg_price=float(d.get("avg_price") or d.get("price") or 0.0),
                        timestamp=str(d.get("timestamp") or d.get("ts") or ""),
                    )
                )
        return fills
