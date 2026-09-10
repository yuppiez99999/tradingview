"""T18 灰度发布编排器 — 4 阶段状态机 + 准入/回滚门禁 + 资金比例管理.

属于「实盘验证四件套」最后一环, 核心目的: **把 "10% → 50% → 100%" 灰度路线落代码,
每阶段设准入门槛和回滚触发器, 确保实盘上线不可跳阶段、可回滚、可审计**.

4 阶段 (不可跳阶段):
    PAPER_TRADING  : 影子账户 + dry_run=true, 0% 实盘 (当前已具备)
    LIVE_SHADOW    : 10% 实盘 + 90% 影子, 实盘单独立账户
    LIVE_PARALLEL  : 50% 实盘 + 50% 影子对照
    LIVE_FULL      : 100% 实盘, 影像账户退役

准入门禁 (升级到下一阶段需连续 N 天满足):
    - min_running_days       : 该阶段最小运行天数
    - max_drawdown_pct       : 最大允许回撤
    - max_drift_pct          : T17 检测的最大 drift
    - max_kill_switch_triggers: T12 L1+ 触发次数上限
    - max_reconcile_issues   : T13/T17 对账问题数上限
    - min_fill_rate          : 订单成交率下限

回滚触发 (任一命中 → 自动降级到前一阶段):
    - T12 KillSwitchManager 升级到 L2 (REDUCTION)
    - T17 drift > halt 阈值
    - 单日回撤 > max_drawdown_pct
    - T11 IntradayCircuitBreaker 熔断 OPEN

用法:
    from utils.risk.gradual_rollout_orchestrator import (
        GradualRolloutOrchestrator, RolloutStage, StageAdmissionCriteria,
    )
    orchestrator = GradualRolloutOrchestrator(
        audit_logger=logger,
        criteria=default_criteria(),
    )
    can_promote, reason = orchestrator.evaluate_promotion(metrics)
    if can_promote:
        orchestrator.promote()
    should_rollback, reason, target = orchestrator.evaluate_rollback(metrics)
    if should_rollback:
        orchestrator.rollback(reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from utils.datetime_utils import now_bj
from utils.risk.risk_audit_logger import RiskAuditLogger

logger = logging.getLogger("rollout_orchestrator")


# ============================================================
# 数据结构
# ============================================================


class RolloutStage(StrEnum):
    """灰度发布 4 阶段 (严格递进, 不可跳)."""

    PAPER_TRADING = "paper_trading"  # 0% 实盘
    LIVE_SHADOW = "live_shadow"  # 10% 实盘
    LIVE_PARALLEL = "live_parallel"  # 50% 实盘
    LIVE_FULL = "live_full"  # 100% 实盘

    @property
    def capital_ratio(self) -> float:
        """该阶段的实盘资金比例."""
        return {
            RolloutStage.PAPER_TRADING: 0.0,
            RolloutStage.LIVE_SHADOW: 0.10,
            RolloutStage.LIVE_PARALLEL: 0.50,
            RolloutStage.LIVE_FULL: 1.00,
        }[self]

    @property
    def next_stage(self) -> RolloutStage | None:
        """下一阶段 (LIVE_FULL 无下一阶段)."""
        order = list(RolloutStage)
        idx = order.index(self)
        return order[idx + 1] if idx + 1 < len(order) else None

    @property
    def prev_stage(self) -> RolloutStage | None:
        """前一阶段 (PAPER_TRADING 无前一阶段)."""
        order = list(RolloutStage)
        idx = order.index(self)
        return order[idx - 1] if idx > 0 else None


@dataclass
class StageAdmissionCriteria:
    """阶段升级准入条件."""

    min_running_days: int = 5
    max_drawdown_pct: float = 0.05  # 5%
    max_drift_pct: float = 0.02  # 2%
    max_kill_switch_triggers: int = 0  # L1+ 触发次数
    max_reconcile_issues: int = 0
    min_fill_rate: float = 0.95  # 95%


@dataclass
class StageMetrics:
    """当前阶段的运行指标 (由调用方填入)."""

    running_days: int = 0
    current_drawdown_pct: float = 0.0
    max_drift_pct: float = 0.0
    kill_switch_triggers: int = 0
    reconcile_issues: int = 0
    fill_rate: float = 1.0
    cb_is_open: bool = False
    kill_switch_level: int = 0  # 0=NORMAL, 1=CAUTION, 2=REDUCTION, 3=LIQUIDATE


@dataclass
class RolloutState:
    """灰度发布状态快照."""

    current_stage: RolloutStage = RolloutStage.PAPER_TRADING
    stage_entered_at: str = ""
    total_promotions: int = 0
    total_rollbacks: int = 0
    history: list[dict] = field(default_factory=list)
    last_rollback_reason: str = ""


# ============================================================
# 默认准入条件
# ============================================================


def default_criteria() -> dict[RolloutStage, StageAdmissionCriteria]:
    """返回默认的阶段准入条件 (保守策略)."""
    return {
        RolloutStage.PAPER_TRADING: StageAdmissionCriteria(
            min_running_days=7,
            max_drawdown_pct=0.05,
            max_drift_pct=0.0,  # paper 阶段不应有 drift
            max_kill_switch_triggers=0,
            max_reconcile_issues=0,
            min_fill_rate=0.95,
        ),
        RolloutStage.LIVE_SHADOW: StageAdmissionCriteria(
            min_running_days=10,
            max_drawdown_pct=0.03,
            max_drift_pct=0.02,
            max_kill_switch_triggers=0,
            max_reconcile_issues=2,
            min_fill_rate=0.95,
        ),
        RolloutStage.LIVE_PARALLEL: StageAdmissionCriteria(
            min_running_days=14,
            max_drawdown_pct=0.03,
            max_drift_pct=0.02,
            max_kill_switch_triggers=0,
            max_reconcile_issues=1,
            min_fill_rate=0.97,
        ),
        # LIVE_FULL 无升级条件 (终态)
        RolloutStage.LIVE_FULL: StageAdmissionCriteria(
            min_running_days=0,
            max_drawdown_pct=0.0,
            max_drift_pct=0.0,
            max_kill_switch_triggers=0,
            max_reconcile_issues=0,
            min_fill_rate=1.0,
        ),
    }


# ============================================================
# 主类
# ============================================================


class GradualRolloutOrchestrator:
    """灰度发布编排器 — 4 阶段状态机 + 准入/回滚门禁."""

    def __init__(
        self,
        audit_logger: RiskAuditLogger,
        criteria: dict[RolloutStage, StageAdmissionCriteria] | None = None,
        initial_stage: RolloutStage = RolloutStage.PAPER_TRADING,
    ) -> None:
        self.audit = audit_logger
        self.criteria = criteria or default_criteria()
        self._state = RolloutState(
            current_stage=initial_stage,
            stage_entered_at=now_bj().isoformat(timespec="seconds"),
        )

    # ------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------

    @property
    def current_stage(self) -> RolloutStage:
        return self._state.current_stage

    @property
    def capital_ratio(self) -> float:
        """当前阶段的实盘资金比例."""
        return self._state.current_stage.capital_ratio

    def get_state_snapshot(self) -> RolloutState:
        """返回状态快照."""
        return RolloutState(
            current_stage=self._state.current_stage,
            stage_entered_at=self._state.stage_entered_at,
            total_promotions=self._state.total_promotions,
            total_rollbacks=self._state.total_rollbacks,
            history=list(self._state.history),
            last_rollback_reason=self._state.last_rollback_reason,
        )

    # ------------------------------------------------------------
    # 准入评估
    # ------------------------------------------------------------

    def evaluate_promotion(self, metrics: StageMetrics) -> tuple[bool, str]:
        """评估是否满足升级到下一阶段的条件.

        Returns:
            (can_promote, reason) — can_promote=True 时 reason 为空, 否则为拒绝原因
        """
        current = self._state.current_stage
        target = current.next_stage

        if target is None:
            return False, f"已处于终态 {current.value}, 无可升级阶段"

        crit = self.criteria.get(current, StageAdmissionCriteria())

        # 逐项检查
        checks: list[tuple[bool, str]] = [
            (
                metrics.running_days >= crit.min_running_days,
                f"运行天数不足: {metrics.running_days} < {crit.min_running_days}",
            ),
            (
                metrics.current_drawdown_pct <= crit.max_drawdown_pct,
                f"回撤超标: {metrics.current_drawdown_pct:.2%} > {crit.max_drawdown_pct:.2%}",
            ),
            (
                metrics.max_drift_pct <= crit.max_drift_pct,
                f"drift 超标: {metrics.max_drift_pct:.2%} > {crit.max_drift_pct:.2%}",
            ),
            (
                metrics.kill_switch_triggers <= crit.max_kill_switch_triggers,
                f"熔断触发次数超标: {metrics.kill_switch_triggers} > {crit.max_kill_switch_triggers}",
            ),
            (
                metrics.reconcile_issues <= crit.max_reconcile_issues,
                f"对账问题超标: {metrics.reconcile_issues} > {crit.max_reconcile_issues}",
            ),
            (
                metrics.fill_rate >= crit.min_fill_rate,
                f"成交率不足: {metrics.fill_rate:.2%} < {crit.min_fill_rate:.2%}",
            ),
        ]

        for ok, msg in checks:
            if not ok:
                return False, msg

        return True, ""

    # ------------------------------------------------------------
    # 回滚评估
    # ------------------------------------------------------------

    def evaluate_rollback(
        self, metrics: StageMetrics
    ) -> tuple[bool, str, RolloutStage]:
        """评估是否需要回滚.

        Returns:
            (should_rollback, reason, target_stage)
        """
        current = self._state.current_stage
        if current == RolloutStage.PAPER_TRADING:
            return False, "", current

        target = current.prev_stage or RolloutStage.PAPER_TRADING

        # 回滚触发器 (任一命中即回滚)
        triggers: list[tuple[bool, str]] = [
            (
                metrics.kill_switch_level >= 2,
                f"T12 KillSwitch 升级到 L2+ (level={metrics.kill_switch_level})",
            ),
            (
                metrics.cb_is_open,
                "T11 IntradayCircuitBreaker 熔断 OPEN",
            ),
            (
                metrics.current_drawdown_pct
                > self.criteria.get(current, StageAdmissionCriteria()).max_drawdown_pct
                * 2,
                f"单日回撤超 2x 阈值: {metrics.current_drawdown_pct:.2%}",
            ),
            (
                metrics.max_drift_pct > 0.10,
                f"持仓 drift > 10%: {metrics.max_drift_pct:.2%}",
            ),
        ]

        for triggered, msg in triggers:
            if triggered:
                return True, msg, target

        return False, "", current

    # ------------------------------------------------------------
    # 阶段切换
    # ------------------------------------------------------------

    def promote(self) -> RolloutStage:
        """升级到下一阶段 (调用前应先 evaluate_promotion)."""
        current = self._state.current_stage
        target = current.next_stage

        if target is None:
            logger.warning(f"[T18] 已处于终态 {current.value}, 无法升级")
            return current

        self._record_transition(current, target, "promotion")
        self._state.current_stage = target
        self._state.stage_entered_at = now_bj().isoformat(timespec="seconds")
        self._state.total_promotions += 1

        self.audit.log(
            module="T18_ROLLOUT",
            action="LEVEL_CHANGE",
            severity="INFO",
            reason=f"灰度升级 {current.value} → {target.value} (资金比例 {target.capital_ratio:.0%})",
        )
        logger.info(
            f"[T18] 灰度升级: {current.value} → {target.value} (ratio={target.capital_ratio:.0%})"
        )
        return target

    def rollback(self, reason: str) -> RolloutStage:
        """回滚到前一阶段."""
        current = self._state.current_stage
        target = current.prev_stage

        if target is None:
            logger.warning(f"[T18] 已处于初始阶段 {current.value}, 无法回滚")
            return current

        self._record_transition(current, target, f"rollback: {reason}")
        self._state.current_stage = target
        self._state.stage_entered_at = now_bj().isoformat(timespec="seconds")
        self._state.total_rollbacks += 1
        self._state.last_rollback_reason = reason

        self.audit.log(
            module="T18_ROLLOUT",
            action="LEVEL_CHANGE",
            severity="CRITICAL",
            reason=f"灰度回滚 {current.value} → {target.value} (原因: {reason})",
        )
        logger.warning(
            f"[T18] 灰度回滚: {current.value} → {target.value} (原因: {reason})"
        )
        return target

    def force_stage(self, stage: RolloutStage, reason: str = "manual") -> None:
        """强制设置阶段 (仅限运维紧急操作, 审计留痕)."""
        old = self._state.current_stage
        self._record_transition(old, stage, f"force: {reason}")
        self._state.current_stage = stage
        self._state.stage_entered_at = now_bj().isoformat(timespec="seconds")

        self.audit.log(
            module="T18_ROLLOUT",
            action="LEVEL_CHANGE",
            severity="CRITICAL",
            reason=f"强制设置阶段 {old.value} → {stage.value} (原因: {reason})",
        )
        logger.warning(
            f"[T18] 强制设置阶段: {old.value} → {stage.value} (原因: {reason})"
        )

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _record_transition(
        self, from_s: RolloutStage, to_s: RolloutStage, action: str
    ) -> None:
        self._state.history.append(
            {
                "timestamp": now_bj().isoformat(timespec="seconds"),
                "from": from_s.value,
                "to": to_s.value,
                "action": action,
            }
        )

    # ------------------------------------------------------------
    # 资金切分
    # ------------------------------------------------------------

    def split_capital(self, total_capital: float) -> tuple[float, float]:
        """按当前阶段比例切分实盘/影子资金.

        Returns:
            (live_capital, shadow_capital)
        """
        ratio = self.capital_ratio
        live = total_capital * ratio
        shadow = total_capital * (1.0 - ratio)
        return live, shadow
