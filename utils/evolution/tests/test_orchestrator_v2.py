"""T3.1 进化编排器 v2 测试 — 三层路由 + Memory/Guard 集成.

任务编号: T3.1 (Phase 3 进化层)
验收标准 (TASK T3.1):
    1. run_cycle() 能从感知→决策→行动→学习完整执行
    2. L1 提案自动执行 + Memory 记录
    3. L2 提案影子验证 + 自动 Promote (本测试模拟)
    4. L3 提案生成人工审批工单 (不自动执行, HC-4)
    5. Kill Switch 触发时冻结

测试策略:
    - Memory/Guard/AutoFixEngine 用真实组件 (集成验证)
    - v1 原型 / KillSwitch 用 mock (避免依赖生产数据)
    - StrategyEvaluator 用 mock (T1.6 已单独测试)
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from utils.evolution.guard import (
    DEFENSE_KILL_SWITCH,
    EvolutionGuard,
    EvolutionProposal,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
)
from utils.evolution.memory import (
    EvolutionMemory,
    STATUS_EXECUTED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from utils.evolution.orchestrator import (
    CYCLE_STATUS_DISABLED,
    CYCLE_STATUS_FROZEN,
    CYCLE_STATUS_NO_ACTION,
    CYCLE_STATUS_SUCCESS,
    CycleResult,
    EvolutionOrchestratorV2,
)


# ============================================================
# Mock 组件
# ============================================================


@dataclass
class MockScoreReport:
    """模拟 ScoreReport (StrategyEvaluator 的输出)."""

    public_score: float = 0.5
    private_score: float = 0.5
    reward_hacking_risk: float = 0.2
    recommendation: str = "continue"
    sample_count: int = 252
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_score": self.public_score,
            "private_score": self.private_score,
            "reward_hacking_risk": self.reward_hacking_risk,
            "recommendation": self.recommendation,
            "sample_count": self.sample_count,
            "reason": self.reason,
        }


@dataclass
class MockMetricsSnapshot:
    """模拟 MetricsSnapshot (v1 原型的输出)."""

    daily_returns: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    sample_count: int = 0
    source: str = "mock"
    collected_at: str = ""
    is_degraded: bool = False
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "source": self.source,
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
        }


class MockV1Orchestrator:
    """模拟 v1 原型 (避免依赖生产 daily_returns.jsonl).

    report 参数语义:
        Ellipsis (默认): 未设置, evaluate_current 返回默认 MockScoreReport()
        None: 明确模拟"评估返回 None" (样本不足/评估器降级)
        MockScoreReport 实例: 返回该实例
    """

    def __init__(
        self,
        metrics: MockMetricsSnapshot | None = None,
        report: Any = Ellipsis,
    ) -> None:
        self._metrics = metrics
        self._report = report
        self.logged_decisions: list[dict[str, Any]] = []

    def collect_metrics(self) -> MockMetricsSnapshot:
        if self._metrics is None:
            return MockMetricsSnapshot(
                daily_returns=[0.001] * 30,
                dates=["2026-08-01"] * 30,
                sample_count=30,
                collected_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._metrics

    def evaluate_current(
        self,
        metrics: Any | None = None,
        signal_history: dict[str, Any] | None = None,
    ) -> MockScoreReport | None:
        if self._report is Ellipsis:
            return MockScoreReport()  # 默认
        return self._report  # None 或 MockScoreReport

    def log_decision(
        self,
        report: Any | None = None,
        action: str = "evaluate_only",
        metrics: Any | None = None,
    ) -> bool:
        self.logged_decisions.append({
            "action": action,
            "report": report.to_dict() if report and hasattr(report, "to_dict") else {},
        })
        return True


class MockKillSwitch:
    """模拟 KillSwitch, 通过 events 预设熔断事件."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def get_event_history(self, days: int = 30) -> list[dict]:
        return list(self.events)


def make_ks_event(level: int, hours_ago: float = 1.0) -> dict:
    """构造熔断事件."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "level": level,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "executed": True,
    }


# ============================================================
# 共享 Fixtures
# ============================================================


@pytest.fixture
def tmp_project(tmp_path: Path):
    """临时项目根 (含 reports/evolution/)."""
    (tmp_path / "reports" / "evolution").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def memory(tmp_project: Path) -> EvolutionMemory:
    """共享 EvolutionMemory (写入临时项目)."""
    return EvolutionMemory(memory_path=tmp_project / "reports" / "evolution" / "memory.jsonl")


@pytest.fixture
def guard(memory: EvolutionMemory) -> EvolutionGuard:
    """带 Memory 的 Guard (无 KillSwitch, 测试中按需注入)."""
    return EvolutionGuard(memory=memory)


@pytest.fixture
def orchestrator(memory: EvolutionMemory, guard: EvolutionGuard) -> EvolutionOrchestratorV2:
    """启用的编排器 (注入真实 Memory + Guard + Mock v1)."""
    # 强制 enabled=True (绕过 Feature Flag, 测试用)
    orch = EvolutionOrchestratorV2(
        memory=memory,
        guard=guard,
        v1_orchestrator=MockV1Orchestrator(),
    )
    orch._enabled = True  # 测试强制启用
    return orch


# ============================================================
# 测试1: Feature Flag 与初始化
# ============================================================


class TestFeatureFlagAndInit:
    """Feature Flag 检查 + 组件懒加载."""

    def test_disabled_flag_returns_disabled_cycle(self, memory: EvolutionMemory):
        """Flag 关闭时 run_cycle 返回 disabled."""
        orch = EvolutionOrchestratorV2(memory=memory)
        orch._enabled = False
        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_DISABLED
        assert "feature_flag_disabled" in result.reason

    def test_disabled_flag_rejects_proposal(self, memory: EvolutionMemory):
        """Flag 关闭时 route_proposal 返回 disabled."""
        orch = EvolutionOrchestratorV2(memory=memory)
        orch._enabled = False
        proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="m", weight_change=0.05, rollback_plan="r",
        )
        result = orch.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_DISABLED

    def test_lazy_loading_components(self, memory: EvolutionMemory):
        """组件懒加载: 首次访问才初始化."""
        orch = EvolutionOrchestratorV2(memory=memory)
        assert orch._guard is None
        g = orch._get_guard()
        assert g is not None
        assert orch._guard is g  # 第二次访问返回同一实例


# ============================================================
# 测试2: Kill Switch 冻结 (HC-5)
# ============================================================


class TestKillSwitchFreeze:
    """Kill Switch 触发时冻结所有进化 (HC-5)."""

    def test_l2_kill_switch_freezes_cycle(self, memory: EvolutionMemory, guard: EvolutionGuard):
        """L2 熔断冻结 run_cycle."""
        ks = MockKillSwitch(events=[make_ks_event(level=2)])
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, kill_switch=ks,
            v1_orchestrator=MockV1Orchestrator(),
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_FROZEN
        assert "kill_switch" in result.reason

    def test_l3_kill_switch_freezes_cycle(self, memory: EvolutionMemory, guard: EvolutionGuard):
        """L3 熔断冻结所有层级."""
        ks = MockKillSwitch(events=[make_ks_event(level=3)])
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, kill_switch=ks,
            v1_orchestrator=MockV1Orchestrator(),
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_FROZEN

    def test_no_kill_switch_event_continues(self, memory: EvolutionMemory, guard: EvolutionGuard):
        """无熔断事件时正常执行."""
        ks = MockKillSwitch(events=[])
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, kill_switch=ks,
            v1_orchestrator=MockV1Orchestrator(),
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status != CYCLE_STATUS_FROZEN


# ============================================================
# 测试3: L1 路由 (AutoFixEngine 自动执行)
# ============================================================


class TestL1Routing:
    """L1 路由: 自动修复, 不经 Guard, 记录到 Memory."""

    def test_l1_proposal_recorded_to_memory(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """L1 提案应记录到 Memory 且 status=executed."""
        proposal = EvolutionProposal(
            level=LEVEL_L1, action_type="fix",
            target_module="config", weight_change=0.0,
            rollback_plan="",
            trigger_reason="C6.1 磁盘空间不足",
        )

        result = orchestrator.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.level == LEVEL_L1
        assert result.executed is True
        assert result.proposal_id

        # Memory 验证
        records = memory.query(level=LEVEL_L1, action_type="fix")
        assert len(records) == 1
        assert records[0].status == STATUS_EXECUTED
        assert records[0].target_module == "config"


# ============================================================
# 测试4: L2 路由 (Guard 检查 + 自动 Promote)
# ============================================================


class TestL2Routing:
    """L2 路由: Guard 检查通过 → 影子验证 → executed."""

    def test_l2_proposal_passes_guard_and_executed(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """合规 L2 提案通过 Guard, 标记为 executed."""
        proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="lgb_model", weight_change=0.05,
            rollback_plan="回滚至 v8.6.14 基线",
            trigger_reason="IC 衰减",
        )

        result = orchestrator.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.level == LEVEL_L2
        assert result.executed is True
        assert result.proposal_id

        # Memory 验证
        records = memory.query(level=LEVEL_L2, action_type="retrain")
        assert len(records) == 1
        assert records[0].status == STATUS_EXECUTED

    def test_l2_proposal_rejected_by_guard(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """违规 L2 提案被 Guard 拒绝, 记录为 rejected."""
        proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="weight_adjust",
            target_module="factor_a", weight_change=0.05,
            rollback_plan="",  # 缺回滚方案 → 被拒
        )

        result = orchestrator.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_NO_ACTION
        assert "rejected_by_guard" in result.reason
        assert result.executed is False

        # Memory 验证 (被拒提案也记录, HC-2 审计完整性)
        records = memory.query(level=LEVEL_L2, status=STATUS_REJECTED)
        assert len(records) == 1


# ============================================================
# 测试5: L3 路由 (人工审批闸门, HC-4)
# ============================================================


class TestL3Routing:
    """L3 路由: Guard 检查通过 → 生成人工审批工单 (不自动执行, HC-4)."""

    def test_l3_proposal_pending_approval(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """合规 L3 提案通过 Guard, 标记为 pending (等待人工审批)."""
        proposal = EvolutionProposal(
            level=LEVEL_L3, action_type="factor_deploy",
            target_module="new_factor_a", weight_change=0.05,
            rollback_plan="下线新因子, 恢复原因子库",
            shadow_days=10,  # 满足影子隔离要求
            trigger_reason="发现 IC=0.08 的新因子",
        )

        result = orchestrator.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.level == LEVEL_L3
        assert result.executed is False  # HC-4: 不自动执行
        assert "人工审批" in result.reason

        # Memory 验证: status=pending (等待审批)
        records = memory.query(level=LEVEL_L3, action_type="factor_deploy")
        assert len(records) == 1
        assert records[0].status == STATUS_PENDING

    def test_l3_proposal_rejected_without_shadow_days(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """L3 提案影子天数不足 → 被 Guard 拒绝."""
        proposal = EvolutionProposal(
            level=LEVEL_L3, action_type="factor_deploy",
            target_module="new_factor_b", weight_change=0.05,
            rollback_plan="下线",
            shadow_days=2,  # 不足 5 天 → 被拒
        )

        result = orchestrator.route_proposal(proposal)
        assert result.status == CYCLE_STATUS_NO_ACTION
        assert result.executed is False

        records = memory.query(level=LEVEL_L3, status=STATUS_REJECTED)
        assert len(records) == 1


# ============================================================
# 测试6: run_cycle 完整循环
# ============================================================


class TestRunCycle:
    """run_cycle 完整循环: 感知→决策→行动→学习."""

    def test_run_cycle_with_continue_recommendation(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """recommendation=continue 时, 仅记录评估, 无进化动作."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                daily_returns=[0.001] * 30,
                sample_count=30,
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
            report=MockScoreReport(
                public_score=0.5, private_score=0.5,
                reward_hacking_risk=0.2,
                recommendation="continue",
            ),
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_NO_ACTION
        assert result.action == "evaluate"
        assert result.evaluator_report  # 含评估报告

        # v1 decisions.jsonl 也被调用 (向后兼容)
        assert len(v1.logged_decisions) == 1
        assert v1.logged_decisions[0]["action"] == "evaluate"

        # Memory 记录评估动作
        records = memory.query(action_type="evaluate")
        assert len(records) == 1
        assert records[0].status == STATUS_EXECUTED

    def test_run_cycle_with_promote_recommendation(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """recommendation=promote 时, 生成 L2 提案并路由."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                daily_returns=[0.001] * 30,
                sample_count=30,
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
            report=MockScoreReport(
                public_score=0.8, private_score=0.75,
                reward_hacking_risk=0.15,
                recommendation="promote",
            ),
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.level == LEVEL_L2
        assert result.action == "promote"
        assert result.executed is True

        # Memory 有 promote 记录
        records = memory.query(action_type="promote")
        assert len(records) == 1
        assert records[0].status == STATUS_EXECUTED

    def test_run_cycle_with_rollback_recommendation(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """recommendation=rollback 时, 生成 L2 回滚提案."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                daily_returns=[0.001] * 30,
                sample_count=30,
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
            report=MockScoreReport(
                public_score=0.3, private_score=0.2,
                reward_hacking_risk=0.8,
                recommendation="rollback",
            ),
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.level == LEVEL_L2
        assert result.action == "rollback"

        records = memory.query(action_type="rollback")
        assert len(records) == 1

    def test_run_cycle_with_degraded_metrics(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """指标降级时返回 degraded."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                is_degraded=True,
                degraded_reason="file_not_found",
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == "degraded"
        assert "metrics_degraded" in result.reason

    def test_run_cycle_with_none_evaluation(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """评估返回 None (样本不足) 时返回 no_action."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                daily_returns=[0.001] * 10,  # 样本不足
                sample_count=10,
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
            report=None,  # 评估跳过
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_NO_ACTION
        assert "evaluation_skipped" in result.reason


# ============================================================
# 测试7: 审计完整性 (HC-2)
# ============================================================


class TestAuditCompleteness:
    """所有动作 100% 审计留痕 (HC-2)."""

    def test_all_actions_recorded_to_memory(
        self, orchestrator: EvolutionOrchestratorV2, memory: EvolutionMemory
    ):
        """L1 + L2(通过) + L2(拒绝) + L3(待审批) 全部记录到 Memory."""
        # L1
        p1 = EvolutionProposal(
            level=LEVEL_L1, action_type="fix",
            target_module="m1", weight_change=0.0,
        )
        orchestrator.route_proposal(p1)

        # L2 通过
        p2 = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="m2", weight_change=0.05,
            rollback_plan="r",
        )
        orchestrator.route_proposal(p2)

        # L2 拒绝 (缺回滚)
        p3 = EvolutionProposal(
            level=LEVEL_L2, action_type="weight_adjust",
            target_module="m3", weight_change=0.05,
            rollback_plan="",
        )
        orchestrator.route_proposal(p3)

        # L3 待审批
        p4 = EvolutionProposal(
            level=LEVEL_L3, action_type="factor_deploy",
            target_module="m4", weight_change=0.05,
            rollback_plan="r", shadow_days=10,
        )
        orchestrator.route_proposal(p4)

        # 验证: Memory 含全部 4 条记录
        all_records = memory.query()
        assert len(all_records) == 4

        # 状态分布: 1 executed(L1) + 1 executed(L2通过) + 1 rejected(L2拒绝) + 1 pending(L3)
        statuses = {r.status for r in all_records}
        assert STATUS_EXECUTED in statuses
        assert STATUS_REJECTED in statuses
        assert STATUS_PENDING in statuses

        # 层级分布
        levels = {r.level for r in all_records}
        assert LEVEL_L1 in levels
        assert LEVEL_L2 in levels
        assert LEVEL_L3 in levels


# ============================================================
# 测试8: 端到端完整生命周期
# ============================================================


class TestEndToEndLifecycle:
    """完整生命周期: run_cycle → Memory 记录 → 查询验证."""

    def test_promote_lifecycle_with_learning(
        self, memory: EvolutionMemory, guard: EvolutionGuard
    ):
        """promote 完整生命周期: 评估→提案→执行→学习."""
        v1 = MockV1Orchestrator(
            metrics=MockMetricsSnapshot(
                daily_returns=[0.001] * 30,
                sample_count=30,
                collected_at=datetime.now(timezone.utc).isoformat(),
            ),
            report=MockScoreReport(
                public_score=0.85, private_score=0.78,
                reward_hacking_risk=0.12,
                recommendation="promote",
            ),
        )
        orch = EvolutionOrchestratorV2(
            memory=memory, guard=guard, v1_orchestrator=v1,
        )
        orch._enabled = True

        # 1. 运行循环
        result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_SUCCESS
        pid = result.proposal_id
        assert pid

        # 2. 模拟学习总结
        memory.learn(pid, "动量因子 IC 提升至 0.06, 适度增配有效")

        # 3. 查询验证完整历史
        records = memory.query(target_module="v9_baseline")
        assert len(records) == 1
        rec = records[0]
        assert rec.action_type == "promote"
        assert rec.status == "learned"  # learn() 会更新状态
        assert "动量因子" in rec.learned
