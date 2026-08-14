"""T18 单元测试 — GradualRolloutOrchestrator 灰度发布编排器."""
from __future__ import annotations

import pytest

from utils.risk.gradual_rollout_orchestrator import (
    GradualRolloutOrchestrator,
    RolloutStage,
    StageAdmissionCriteria,
    StageMetrics,
    RolloutState,
    default_criteria,
)
from utils.risk.risk_audit_logger import RiskAuditLogger


# ============================================================
# 测试夹具
# ============================================================

def _make_orchestrator(initial_stage: RolloutStage = RolloutStage.PAPER_TRADING) -> GradualRolloutOrchestrator:
    import tempfile
    from pathlib import Path
    audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
    return GradualRolloutOrchestrator(audit_logger=audit, initial_stage=initial_stage)


def _good_metrics(running_days: int = 10) -> StageMetrics:
    """满足 PAPER→SHADOW 升级条件的指标."""
    return StageMetrics(
        running_days=running_days,
        current_drawdown_pct=0.02,
        max_drift_pct=0.0,
        kill_switch_triggers=0,
        reconcile_issues=0,
        fill_rate=0.98,
        cb_is_open=False,
        kill_switch_level=0,
    )


# ============================================================
# RolloutStage 枚举测试
# ============================================================

class TestRolloutStage:
    def test_capital_ratio(self):
        assert RolloutStage.PAPER_TRADING.capital_ratio == 0.0
        assert RolloutStage.LIVE_SHADOW.capital_ratio == pytest.approx(0.10)
        assert RolloutStage.LIVE_PARALLEL.capital_ratio == pytest.approx(0.50)
        assert RolloutStage.LIVE_FULL.capital_ratio == pytest.approx(1.00)

    def test_next_stage(self):
        assert RolloutStage.PAPER_TRADING.next_stage == RolloutStage.LIVE_SHADOW
        assert RolloutStage.LIVE_SHADOW.next_stage == RolloutStage.LIVE_PARALLEL
        assert RolloutStage.LIVE_PARALLEL.next_stage == RolloutStage.LIVE_FULL
        assert RolloutStage.LIVE_FULL.next_stage is None

    def test_prev_stage(self):
        assert RolloutStage.PAPER_TRADING.prev_stage is None
        assert RolloutStage.LIVE_SHADOW.prev_stage == RolloutStage.PAPER_TRADING
        assert RolloutStage.LIVE_FULL.prev_stage == RolloutStage.LIVE_PARALLEL


# ============================================================
# 默认准入条件
# ============================================================

class TestDefaultCriteria:
    def test_all_stages_present(self):
        crit = default_criteria()
        assert len(crit) == 4
        assert RolloutStage.PAPER_TRADING in crit
        assert RolloutStage.LIVE_FULL in crit

    def test_paper_trading_strict(self):
        crit = default_criteria()
        paper = crit[RolloutStage.PAPER_TRADING]
        assert paper.min_running_days == 7
        assert paper.max_drift_pct == 0.0

    def test_live_shadow_conservative(self):
        crit = default_criteria()
        shadow = crit[RolloutStage.LIVE_SHADOW]
        assert shadow.min_running_days == 10
        assert shadow.max_drawdown_pct == 0.03


# ============================================================
# 准入评估
# ============================================================

class TestEvaluatePromotion:
    def test_paper_to_shadow_all_pass(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        can, reason = orch.evaluate_promotion(_good_metrics(running_days=7))
        assert can is True
        assert reason == ""

    def test_running_days_insufficient(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        can, reason = orch.evaluate_promotion(_good_metrics(running_days=3))
        assert can is False
        assert "运行天数不足" in reason

    def test_drawdown_exceeds(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        m = _good_metrics()
        m.current_drawdown_pct = 0.06  # 6% > 5%
        can, reason = orch.evaluate_promotion(m)
        assert can is False
        assert "回撤超标" in reason

    def test_drift_exceeds(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        m = _good_metrics()
        m.max_drift_pct = 0.01  # > 0 for PAPER
        can, reason = orch.evaluate_promotion(m)
        assert can is False
        assert "drift" in reason

    def test_kill_switch_triggered(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        m = _good_metrics()
        m.kill_switch_triggers = 1
        can, reason = orch.evaluate_promotion(m)
        assert can is False
        assert "熔断" in reason

    def test_fill_rate_low(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        m = _good_metrics()
        m.fill_rate = 0.90
        can, reason = orch.evaluate_promotion(m)
        assert can is False
        assert "成交率" in reason

    def test_live_full_no_promotion(self):
        orch = _make_orchestrator(RolloutStage.LIVE_FULL)
        can, reason = orch.evaluate_promotion(_good_metrics())
        assert can is False
        assert "终态" in reason


# ============================================================
# 回滚评估
# ============================================================

class TestEvaluateRollback:
    def test_no_rollback_when_healthy(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        m = _good_metrics()
        should, reason, target = orch.evaluate_rollback(m)
        assert should is False

    def test_rollback_on_kill_switch_l2(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        m = _good_metrics()
        m.kill_switch_level = 2  # L2 REDUCTION
        should, reason, target = orch.evaluate_rollback(m)
        assert should is True
        assert "L2" in reason
        assert target == RolloutStage.PAPER_TRADING

    def test_rollback_on_cb_open(self):
        orch = _make_orchestrator(RolloutStage.LIVE_PARALLEL)
        m = _good_metrics()
        m.cb_is_open = True
        should, reason, target = orch.evaluate_rollback(m)
        assert should is True
        assert "熔断" in reason
        assert target == RolloutStage.LIVE_SHADOW

    def test_rollback_on_large_drift(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        m = _good_metrics()
        m.max_drift_pct = 0.15  # 15% > 10%
        should, reason, _ = orch.evaluate_rollback(m)
        assert should is True
        assert "drift" in reason

    def test_rollback_on_excessive_drawdown(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        m = _good_metrics()
        m.current_drawdown_pct = 0.10  # 10% > 2x 3% = 6%
        should, reason, _ = orch.evaluate_rollback(m)
        assert should is True
        assert "回撤" in reason

    def test_no_rollback_from_paper(self):
        """PAPER_TRADING 是初始阶段, 不回滚."""
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        m = _good_metrics()
        m.kill_switch_level = 3
        should, _, target = orch.evaluate_rollback(m)
        assert should is False


# ============================================================
# 阶段切换
# ============================================================

class TestStageTransition:
    def test_promote_paper_to_shadow(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        new_stage = orch.promote()
        assert new_stage == RolloutStage.LIVE_SHADOW
        assert orch.current_stage == RolloutStage.LIVE_SHADOW
        assert orch.capital_ratio == pytest.approx(0.10)
        assert orch.get_state_snapshot().total_promotions == 1

    def test_promote_records_history(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        orch.promote()
        snap = orch.get_state_snapshot()
        assert len(snap.history) == 1
        assert snap.history[0]["from"] == "paper_trading"
        assert snap.history[0]["to"] == "live_shadow"

    def test_rollback_shadow_to_paper(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        new_stage = orch.rollback("test rollback")
        assert new_stage == RolloutStage.PAPER_TRADING
        assert orch.get_state_snapshot().total_rollbacks == 1
        assert orch.get_state_snapshot().last_rollback_reason == "test rollback"

    def test_promote_from_full_no_op(self):
        orch = _make_orchestrator(RolloutStage.LIVE_FULL)
        result = orch.promote()
        assert result == RolloutStage.LIVE_FULL  # 不变

    def test_rollback_from_paper_no_op(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        result = orch.rollback("cannot go back")
        assert result == RolloutStage.PAPER_TRADING  # 不变

    def test_force_stage(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        orch.force_stage(RolloutStage.LIVE_PARALLEL, "emergency override")
        assert orch.current_stage == RolloutStage.LIVE_PARALLEL


# ============================================================
# 资金切分
# ============================================================

class TestCapitalSplit:
    def test_paper_trading_zero_live(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        live, shadow = orch.split_capital(1_000_000)
        assert live == 0
        assert shadow == 1_000_000

    def test_live_shadow_10pct(self):
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)
        live, shadow = orch.split_capital(1_000_000)
        assert live == pytest.approx(100_000)
        assert shadow == pytest.approx(900_000)

    def test_live_parallel_50pct(self):
        orch = _make_orchestrator(RolloutStage.LIVE_PARALLEL)
        live, shadow = orch.split_capital(1_000_000)
        assert live == pytest.approx(500_000)
        assert shadow == pytest.approx(500_000)

    def test_live_full_100pct(self):
        orch = _make_orchestrator(RolloutStage.LIVE_FULL)
        live, shadow = orch.split_capital(1_000_000)
        assert live == pytest.approx(1_000_000)
        assert shadow == pytest.approx(0)


# ============================================================
# 状态快照
# ============================================================

class TestStateSnapshot:
    def test_initial_state(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        snap = orch.get_state_snapshot()
        assert snap.current_stage == RolloutStage.PAPER_TRADING
        assert snap.total_promotions == 0
        assert snap.total_rollbacks == 0
        assert len(snap.history) == 0

    def test_after_promotion_and_rollback(self):
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)
        orch.promote()
        orch.rollback("test")
        snap = orch.get_state_snapshot()
        assert snap.total_promotions == 1
        assert snap.total_rollbacks == 1
        assert len(snap.history) == 2
        assert snap.last_rollback_reason == "test"


# ============================================================
# 端到端流程
# ============================================================

class TestEndToEndFlow:
    def test_full_lifecycle(self):
        """完整灰度生命周期: PAPER → SHADOW → PARALLEL → FULL."""
        orch = _make_orchestrator(RolloutStage.PAPER_TRADING)

        # PAPER → SHADOW
        can, _ = orch.evaluate_promotion(_good_metrics(running_days=7))
        assert can
        orch.promote()
        assert orch.current_stage == RolloutStage.LIVE_SHADOW

        # SHADOW → PARALLEL
        can, _ = orch.evaluate_promotion(_good_metrics(running_days=10))
        assert can
        orch.promote()
        assert orch.current_stage == RolloutStage.LIVE_PARALLEL

        # PARALLEL → FULL
        can, _ = orch.evaluate_promotion(_good_metrics(running_days=14))
        assert can
        orch.promote()
        assert orch.current_stage == RolloutStage.LIVE_FULL

        # 最终状态
        snap = orch.get_state_snapshot()
        assert snap.total_promotions == 3
        assert snap.total_rollbacks == 0

    def test_rollback_then_repromote(self):
        """回滚后可以再次升级."""
        orch = _make_orchestrator(RolloutStage.LIVE_SHADOW)

        # 触发回滚
        m = _good_metrics()
        m.kill_switch_level = 2
        should, _, _ = orch.evaluate_rollback(m)
        assert should
        orch.rollback("L2 triggered")
        assert orch.current_stage == RolloutStage.PAPER_TRADING

        # 恢复后重新升级
        can, _ = orch.evaluate_promotion(_good_metrics(running_days=7))
        assert can
        orch.promote()
        assert orch.current_stage == RolloutStage.LIVE_SHADOW

        snap = orch.get_state_snapshot()
        assert snap.total_promotions == 1
        assert snap.total_rollbacks == 1
