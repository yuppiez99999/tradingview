"""T17 实盘对账循环 — 包装 T13 扩展盘中定时 + 盘后全量 + 持仓 drift 检测.

属于「实盘验证四件套」第 3 位, 核心目的: **把 T13 的 EOD 一次性对账升级为盘中定时 + 盘后全量双循环,
新增 broker 持仓 vs 本地账本 drift 检测, 超阈值自动告警或 halt**.

三层检查:
    1. 计划单 vs 成交对账 (复用 T13 TradeOrderReconciler, 不修改)
    2. broker 持仓 vs 本地账本 drift 检测 (新增)
    3. 综合 verdict: pass / warn / halt

drift 阈值:
    - drift_pct < 2%  → ignore
    - 2% ≤ drift_pct < 10% → alert (写审计 + 日志)
    - drift_pct ≥ 10% → halt (触发 T12 KillSwitchManager 升级)

用法:
    from utils.risk.live_reconciliation_loop import (
        LiveReconciliationLoop, LiveReconciliationReport,
    )
    loop = LiveReconciliationLoop(
        reconciler=TradeOrderReconciler(),
        broker=broker,
        local_position_book={"sh600519": 1000, "sz000001": 500},
        audit_logger=logger,
    )
    intraday = loop.run_intraday_tick()
    eod = loop.run_eod_final()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from utils.risk.risk_audit_logger import RiskAuditLogger
from utils.risk.trade_order_reconciler import (
    FillRecord,
    PlannedOrder,
    ReconciliationReport,
    TradeOrderReconciler,
)

logger = logging.getLogger("live_reconciliation")


# ============================================================
# 协议定义
# ============================================================

class BrokerPositionProtocol(Protocol):
    """T17 需要的 broker 持仓查询接口."""

    def get_positions(self) -> dict[str, int]:
        """返回 {symbol: quantity} (正数=多头, 负数=空头, 0=清仓)."""
        ...


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PositionDrift:
    """单个标的的持仓 drift."""

    symbol: str
    local_qty: int
    broker_qty: int
    drift_qty: int
    drift_pct: float
    suggested_action: str = "ignore"  # ignore / alert / auto_repair / halt

    @property
    def has_drift(self) -> bool:
        return self.drift_qty != 0


@dataclass
class LiveReconciliationReport:
    """实盘对账综合报告."""

    timestamp: str
    mode: str = "intraday"  # intraday / eod
    base_reconciliation: ReconciliationReport | None = None
    position_drifts: list[PositionDrift] = field(default_factory=list)
    verdict: str = "pass"  # pass / warn / halt
    issues_count: int = 0
    drift_count: int = 0
    halt_count: int = 0

    @property
    def all_pass(self) -> bool:
        return self.verdict == "pass"

    def summary_text(self) -> str:
        lines = [
            f"=== 实盘对账报告 ({self.mode}) {self.timestamp} ===",
            f"verdict: {self.verdict}",
            f"对账问题: {self.issues_count}",
            f"持仓 drift: {self.drift_count} (halt: {self.halt_count})",
        ]
        if self.base_reconciliation is not None:
            lines.append(f"T13 基础对账 all_pass={self.base_reconciliation.all_pass}")
        for d in self.position_drifts:
            if d.has_drift:
                lines.append(
                    f"  drift {d.symbol}: local={d.local_qty} broker={d.broker_qty} "
                    f"diff={d.drift_qty} ({d.drift_pct:+.2%}) → {d.suggested_action}"
                )
        return "\n".join(lines)


# ============================================================
# 主类
# ============================================================

class LiveReconciliationLoop:
    """实盘对账循环 — 盘中定时 + 盘后全量 + 持仓 drift."""

    def __init__(
        self,
        reconciler: TradeOrderReconciler,
        broker: BrokerPositionProtocol,
        local_position_book: dict[str, int],
        audit_logger: RiskAuditLogger,
        intraday_interval_sec: int = 300,
        drift_alert_pct: float = 0.02,
        drift_halt_pct: float = 0.10,
    ) -> None:
        if intraday_interval_sec < 10:
            raise ValueError(f"intraday_interval_sec 应 ≥10, 实际 {intraday_interval_sec}")
        if not (0 < drift_alert_pct < drift_halt_pct < 1):
            raise ValueError(
                f"需 0 < drift_alert_pct({drift_alert_pct}) "
                f"< drift_halt_pct({drift_halt_pct}) < 1"
            )
        self.reconciler = reconciler
        self.broker = broker
        self.local_book = dict(local_position_book)
        self.audit = audit_logger
        self.intraday_interval_sec = intraday_interval_sec
        self.drift_alert_pct = drift_alert_pct
        self.drift_halt_pct = drift_halt_pct

        self._last_intraday_at: datetime | None = None

    # ------------------------------------------------------------
    # 盘中对账 (定时 tick)
    # ------------------------------------------------------------

    def run_intraday_tick(
        self,
        planned_orders: list[PlannedOrder] | None = None,
        fills: list[FillRecord] | None = None,
    ) -> LiveReconciliationReport:
        """执行一次盘中对账 tick.

        Args:
            planned_orders: 当日计划单 (可选, 为 None 时跳过 T13 基础对账)
            fills: 当日成交记录 (可选)
        """
        now = datetime.now()
        report = LiveReconciliationReport(
            timestamp=now.isoformat(timespec="seconds"),
            mode="intraday",
        )

        # 1. T13 基础对账 (如果提供了数据)
        if planned_orders is not None and fills is not None:
            date_str = now.strftime("%Y-%m-%d")
            report.base_reconciliation = self.reconciler.reconcile(date_str, planned_orders, fills)
            report.issues_count = len(report.base_reconciliation.issues_summary)

        # 2. 持仓 drift 检测
        drifts = self.detect_position_drift()
        report.position_drifts = drifts
        report.drift_count = sum(1 for d in drifts if d.has_drift)
        report.halt_count = sum(1 for d in drifts if d.suggested_action == "halt")

        # 3. 综合判定
        if report.halt_count > 0:
            report.verdict = "halt"
        elif report.drift_count > 0 or report.issues_count > 0:
            report.verdict = "warn"
        else:
            report.verdict = "pass"

        self._last_intraday_at = now

        # 审计
        self.audit.log(
            module="T17_RECONCILE",
            action="RECONCILE_ISSUE" if report.verdict != "pass" else "ALLOW",
            severity="CRITICAL" if report.verdict == "halt" else ("WARN" if report.verdict == "warn" else "INFO"),
            reason=f"盘中对账 verdict={report.verdict} issues={report.issues_count} drifts={report.drift_count} halts={report.halt_count}",
        )

        logger.info(
            f"[T17] 盘中对账 verdict={report.verdict} "
            f"issues={report.issues_count} drifts={report.drift_count} halts={report.halt_count}"
        )
        return report

    # ------------------------------------------------------------
    # 盘后全量对账
    # ------------------------------------------------------------

    def run_eod_final(
        self,
        planned_orders: list[PlannedOrder],
        fills: list[FillRecord],
    ) -> LiveReconciliationReport:
        """执行盘后全量对账 (含 T13 基础对账 + 持仓 drift)."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        report = LiveReconciliationReport(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            mode="eod",
        )

        # 1. T13 全量对账
        report.base_reconciliation = self.reconciler.reconcile(date_str, planned_orders, fills)
        report.issues_count = len(report.base_reconciliation.issues_summary)

        # 2. 持仓 drift
        drifts = self.detect_position_drift()
        report.position_drifts = drifts
        report.drift_count = sum(1 for d in drifts if d.has_drift)
        report.halt_count = sum(1 for d in drifts if d.suggested_action == "halt")

        # 3. 综合判定
        if report.halt_count > 0:
            report.verdict = "halt"
        elif report.drift_count > 0 or report.issues_count > 0 or not report.base_reconciliation.all_pass:
            report.verdict = "warn"
        else:
            report.verdict = "pass"

        # 审计
        self.audit.log(
            module="T17_RECONCILE",
            action="RECONCILE_ISSUE" if report.verdict != "pass" else "ALLOW",
            severity="CRITICAL" if report.verdict == "halt" else ("WARN" if report.verdict == "warn" else "INFO"),
            reason=(
                f"EOD全量对账 verdict={report.verdict} "
                f"issues={report.issues_count} drifts={report.drift_count} "
                f"unexpected={len(report.base_reconciliation.unexpected_fills)}"
            ),
        )

        logger.info(f"[T17] EOD 对账 verdict={report.verdict} | {report.summary_text()}")
        return report

    # ------------------------------------------------------------
    # 持仓 drift 检测
    # ------------------------------------------------------------

    def detect_position_drift(self) -> list[PositionDrift]:
        """检测本地账本与 broker 持仓的 drift."""
        try:
            broker_positions = self.broker.get_positions()
        except Exception as exc:
            logger.error(f"[T17] 查询 broker 持仓异常: {exc}")
            self.audit.log(
                module="T17_RECONCILE",
                action="OTHER",
                severity="ERROR",
                reason=f"查询 broker 持仓异常: {exc}",
            )
            return []

        all_symbols = set(self.local_book.keys()) | set(broker_positions.keys())
        drifts: list[PositionDrift] = []

        for symbol in all_symbols:
            local_qty = self.local_book.get(symbol, 0)
            broker_qty = broker_positions.get(symbol, 0)
            drift_qty = broker_qty - local_qty

            if drift_qty == 0:
                continue

            base = max(abs(local_qty), abs(broker_qty), 1)
            drift_pct = abs(drift_qty) / base

            if drift_pct >= self.drift_halt_pct:
                action = "halt"
            elif drift_pct >= self.drift_alert_pct:
                action = "alert"
            else:
                action = "ignore"

            drifts.append(PositionDrift(
                symbol=symbol,
                local_qty=local_qty,
                broker_qty=broker_qty,
                drift_qty=drift_qty,
                drift_pct=drift_pct,
                suggested_action=action,
            ))

        return drifts

    # ------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------

    def update_local_book(self, symbol: str, qty: int) -> None:
        """更新本地账本 (成交后调用)."""
        self.local_book[symbol] = qty

    def should_run_intraday(self) -> bool:
        """是否到了下一次盘中对账的时间."""
        if self._last_intraday_at is None:
            return True
        elapsed = (datetime.now() - self._last_intraday_at).total_seconds()
        return elapsed >= self.intraday_interval_sec
