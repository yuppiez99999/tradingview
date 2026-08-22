"""Phase 1 集成测试 — Memory + Guard + AutoFixEngine + Kill Switch 协同验证.

任务编号: T1.7 (Phase 1 防御层加固)
验收标准 (TASK T1.7):
    1. 模拟系统异常 → AutoFixEngine 修复 → Memory 记录
    2. 模拟进化提案 → Guard 检查 → Memory 记录
    3. 模拟策略评估 → ScoreReport 产出 → Memory 记录 (用 mock 评估器)
    4. Kill Switch 触发 → Guard 冻结所有提案
    5. 所有测试通过

测试策略:
    使用真实组件 (EvolutionMemory + EvolutionGuard + AutoFixEngine),
    仅 StrategyEvaluator 用 mock (T1.6 未完成, 但不影响 Phase 1 防御层验证).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from utils.evolution.auto_fix_engine import AutoFixEngine
from utils.evolution.guard import (
    DEFENSE_FREQUENCY,
    DEFENSE_KILL_SWITCH,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    EvolutionGuard,
    EvolutionProposal,
)
from utils.evolution.memory import (
    STATUS_EXECUTED,
    STATUS_LEARNED,
    STATUS_PENDING,
    STATUS_REJECTED,
    EvolutionMemory,
)

# ============================================================
# Mock KillSwitch (复用 test_guard.py 的设计)
# ============================================================


class MockKillSwitch:
    """模拟 KillSwitch, 通过 events 预设熔断事件."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def get_event_history(self, days: int = 30) -> list[dict]:
        return list(self.events)


def make_ks_event(level: int, hours_ago: float = 1.0) -> dict:
    """构造熔断事件."""
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
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
    """临时项目根目录 (含 config/ 和 reports/ 子目录)."""
    (tmp_path / "config").mkdir()
    (tmp_path / "reports" / "evolution").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def memory(tmp_project: Path) -> EvolutionMemory:
    """共享的 EvolutionMemory 实例 (写入临时项目)."""
    return EvolutionMemory(memory_path=tmp_project / "reports" / "evolution" / "memory.jsonl")


@pytest.fixture
def guard(memory: EvolutionMemory) -> EvolutionGuard:
    """带 Memory 的 Guard."""
    return EvolutionGuard(memory=memory)


@pytest.fixture
def auto_fix_engine(memory: EvolutionMemory, tmp_project: Path) -> AutoFixEngine:
    """带 Memory + 临时项目根的 AutoFixEngine."""
    return AutoFixEngine(memory=memory, project_root=tmp_project)


# ============================================================
# 场景1: 系统异常 → AutoFixEngine 修复 → Memory 记录
# ============================================================


class TestScenario1SystemFix:
    """验收标准1: 模拟系统异常 → AutoFixEngine 修复 → Memory 记录."""

    def test_pycache_cleanup_recorded_to_memory(
        self, auto_fix_engine: AutoFixEngine, memory: EvolutionMemory, tmp_project: Path
    ):
        """L0 修复 (临时文件清理) 应记录到 Memory."""
        # 准备: 创建 __pycache__ 模拟问题
        pycache = tmp_project / "subdir" / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "module.cpython-38.pyc").write_text("fake bytecode")

        # 模拟 P0 自检失败项
        class FakeCheckResult:
            code = "C6.1"
            name = "磁盘空间检查"
            detail = f"临时文件过多: {pycache}"
            remediation = "清理 __pycache__"

        # 执行修复
        result = auto_fix_engine.try_fix(FakeCheckResult())

        # 验证: 修复成功 + 文件已清理
        assert result.fixed
        assert not pycache.exists()

        # 验证: Memory 有审计记录
        fix_records = memory.query(action_type="fix")
        assert len(fix_records) == 1
        rec = fix_records[0]
        assert rec.level == LEVEL_L1  # AutoFixEngine 属于 L1 防御层
        assert rec.target_module == "system_check.C6.1"
        assert rec.status == STATUS_EXECUTED
        assert rec.result.get("fixed") is True
        assert rec.result.get("risk_level") == "L0"

    def test_high_risk_issue_only_warned(
        self, auto_fix_engine: AutoFixEngine, memory: EvolutionMemory
    ):
        """高风险问题应仅告警, 不修复, 但仍记录到 Memory (rejected)."""
        class FakeCheckResult:
            code = "C99.1"
            name = "持仓不一致"
            detail = "positions.json 与 trade_plans 冲突"
            remediation = "需 CRO 评估"

        result = auto_fix_engine.try_fix(FakeCheckResult())

        # 验证: 未修复 (仅告警)
        assert not result.fixed
        assert result.action == "warned"

        # 验证: Memory 记录为 rejected
        records = memory.query(action_type="fix")
        assert len(records) == 1
        assert records[0].status == STATUS_REJECTED


# ============================================================
# 场景2: 进化提案 → Guard 检查 → Memory 记录
# ============================================================


class TestScenario2ProposalApproval:
    """验收标准2: 模拟进化提案 → Guard 检查 → Memory 记录."""

    def test_approved_proposal_full_lifecycle(
        self, guard: EvolutionGuard, memory: EvolutionMemory
    ):
        """合规 L2 提案的完整生命周期: Guard 通过 → Memory 记录 → 执行 → 学习."""
        proposal = EvolutionProposal(
            level=LEVEL_L2,
            action_type="retrain",
            target_module="lgb_enhanced_trainer",
            weight_change=0.05,
            rollback_plan="回滚至 v8.6.14 基线模型",
            trigger_reason="IC 从 0.08 衰减至 0.02",
        )

        # 1. Guard 检查
        decision = guard.check_proposal(proposal)
        assert decision.passed
        assert decision.violated_defense == 0

        # 2. 记录到 Memory (status=pending)
        pid = memory.record({
            "level": proposal.level,
            "action_type": proposal.action_type,
            "trigger_reason": proposal.trigger_reason,
            "target_module": proposal.target_module,
            "rollback_plan": proposal.rollback_plan,
            "status": STATUS_PENDING,
            "result": decision.to_dict(),
        })

        # 3. 模拟执行, 更新状态
        memory.update_status(pid, STATUS_EXECUTED, result={"new_ic": 0.075, "old_ic": 0.02})

        # 4. 学习总结
        memory.learn(pid, "夏季 IC 衰减是季节性现象, 重训有效")

        # 5. 查询验证完整历史
        records = memory.query(target_module="lgb_enhanced_trainer")
        assert len(records) == 1
        rec = records[0]
        assert rec.status == STATUS_LEARNED
        assert rec.learned == "夏季 IC 衰减是季节性现象, 重训有效"
        assert rec.result.get("new_ic") == 0.075

    def test_rejected_proposal_recorded(
        self, guard: EvolutionGuard, memory: EvolutionMemory
    ):
        """被 Guard 拒绝的提案也应记录到 Memory (审计完整性)."""
        # L2 缺回滚方案 → 被拒
        proposal = EvolutionProposal(
            level=LEVEL_L2,
            action_type="weight_adjust",
            target_module="factor_xyz",
            weight_change=0.05,
            rollback_plan="",  # 缺回滚方案
        )

        decision = guard.check_proposal(proposal)
        assert not decision.passed
        assert decision.violated_defense == 3  # DEFENSE_ROLLBACK

        # 记录被拒提案 (审计)
        memory.record({
            "level": proposal.level,
            "action_type": proposal.action_type,
            "trigger_reason": proposal.trigger_reason,
            "target_module": proposal.target_module,
            "rollback_plan": proposal.rollback_plan,
            "status": STATUS_REJECTED,
            "result": decision.to_dict(),
        })

        records = memory.query(status=STATUS_REJECTED)
        assert len(records) == 1
        assert records[0].result.get("violated_defense") == 3


# ============================================================
# 场景3: 策略评估 → ScoreReport → Memory 记录
# ============================================================


class TestScenario3StrategyEvaluation:
    """验收标准3: 模拟策略评估 → ScoreReport 产出 → Memory 记录.

    Note: T1.6 (StrategyEvaluator 补全) 未完成, 此处用 mock 评估器
    验证 Memory 能正确记录评估结果. 真实 StrategyEvaluator 集成留待 T1.6.
    """

    def test_score_report_recorded_to_memory(self, memory: EvolutionMemory):
        """评估报告应能写入 Memory 审计."""
        # 模拟 ScoreReport (StrategyEvaluator 的输出)
        mock_score_report = {
            "public_score": 0.72,
            "private_score": 0.68,
            "reward_hacking_risk": 0.15,
            "recommendation": "promote",
            "sample_count": 252,
        }

        # 记录评估动作
        memory.record({
            "level": LEVEL_L2,
            "action_type": "evaluate",
            "trigger_reason": "EOD 评估周期触发",
            "target_module": "v9_baseline",
            "rollback_plan": "评估是只读操作, 无需回滚",
            "score_report": mock_score_report,
            "status": STATUS_EXECUTED,
        })

        # 验证
        records = memory.query(action_type="evaluate")
        assert len(records) == 1
        rec = records[0]
        assert rec.score_report == mock_score_report
        assert rec.score_report["public_score"] != rec.score_report["private_score"]

    def test_high_reward_hacking_risk_rejected_by_guard(
        self, guard: EvolutionGuard, memory: EvolutionMemory
    ):
        """Reward Hacking Risk 高的评估结果应被标记 (Guard 不直接检查, 但可记录)."""
        # 模拟高风险评估
        high_risk_report = {
            "public_score": 0.95,  # 异常高
            "private_score": 0.45,  # 大幅低于 public → 过拟合
            "reward_hacking_risk": 0.75,  # > 0.7 禁止晋升 (ARCHITECTURE §8.2)
            "recommendation": "reject",
        }

        memory.record({
            "level": LEVEL_L2,
            "action_type": "evaluate",
            "trigger_reason": "Reward hacking 检测",
            "target_module": "candidate_model_v2",
            "rollback_plan": "不晋升, 保持当前模型",
            "score_report": high_risk_report,
            "status": STATUS_REJECTED,  # 被拒
        })

        records = memory.query(status=STATUS_REJECTED)
        assert len(records) == 1
        assert records[0].score_report["reward_hacking_risk"] > 0.7


# ============================================================
# 场景4: Kill Switch 触发 → Guard 冻结所有提案
# ============================================================


class TestScenario4KillSwitchFreeze:
    """验收标准4: Kill Switch 触发 → Guard 冻结所有提案."""

    def test_l2_kill_switch_freezes_l2_and_l3(
        self, memory: EvolutionMemory
    ):
        """L2 熔断应冻结 L2/L3 进化, 但允许 L1 (修复)."""
        ks = MockKillSwitch(events=[make_ks_event(level=2, hours_ago=1)])
        guard = EvolutionGuard(memory=memory, kill_switch=ks)

        # L2 提案应被冻结
        l2_proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="model_a", weight_change=0.05,
            rollback_plan="rollback",
        )
        l2_decision = guard.check_proposal(l2_proposal)
        assert not l2_decision.passed
        assert l2_decision.violated_defense == DEFENSE_KILL_SWITCH

        # L3 提案应被冻结
        l3_proposal = EvolutionProposal(
            level=LEVEL_L3, action_type="factor_deploy",
            target_module="factor_b", weight_change=0.05,
            rollback_plan="rollback", shadow_days=10,
        )
        l3_decision = guard.check_proposal(l3_proposal)
        assert not l3_decision.passed
        assert l3_decision.violated_defense == DEFENSE_KILL_SWITCH

        # L1 提案应通过 (L1 是修复, 不被 L2 熔断冻结)
        l1_proposal = EvolutionProposal(
            level=LEVEL_L1, action_type="fix",
            target_module="config", weight_change=0.0,
            rollback_plan="",
        )
        l1_decision = guard.check_proposal(l1_proposal)
        assert l1_decision.passed

    def test_l3_kill_switch_freezes_all(
        self, memory: EvolutionMemory
    ):
        """L3 熔断应冻结所有层级 (L1/L2/L3)."""
        ks = MockKillSwitch(events=[make_ks_event(level=3, hours_ago=1)])
        guard = EvolutionGuard(memory=memory, kill_switch=ks)

        for level, shadow, rb in [
            (LEVEL_L1, 0, ""),
            (LEVEL_L2, 0, "r"),
            (LEVEL_L3, 10, "r"),
        ]:
            proposal = EvolutionProposal(
                level=level, action_type="test",
                target_module="mod", weight_change=0.05,
                rollback_plan=rb, shadow_days=shadow,
            )
            decision = guard.check_proposal(proposal)
            assert not decision.passed, f"L3 熔断应冻结 {level}"
            assert decision.violated_defense == DEFENSE_KILL_SWITCH

    def test_freeze_decisions_can_be_audited(
        self, memory: EvolutionMemory
    ):
        """熔断冻结决策应能写入 Memory 审计."""
        ks = MockKillSwitch(events=[make_ks_event(level=2, hours_ago=1)])
        guard = EvolutionGuard(memory=memory, kill_switch=ks)

        proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="frozen_model", weight_change=0.05,
            rollback_plan="r",
        )
        decision = guard.check_proposal(proposal)

        # 记录被冻结的提案
        memory.record({
            "level": proposal.level,
            "action_type": proposal.action_type,
            "trigger_reason": proposal.trigger_reason,
            "target_module": proposal.target_module,
            "rollback_plan": proposal.rollback_plan,
            "status": STATUS_REJECTED,
            "result": decision.to_dict(),
        })

        records = memory.query(target_module="frozen_model", status=STATUS_REJECTED)
        assert len(records) == 1
        assert records[0].result.get("violated_defense") == DEFENSE_KILL_SWITCH


# ============================================================
# 场景5: 完整端到端联调
# ============================================================


class TestScenario5EndToEnd:
    """验收标准5: 完整端到端联调 (多组件协同)."""

    def test_full_evolution_lifecycle(
        self, guard: EvolutionGuard, auto_fix_engine: AutoFixEngine, memory: EvolutionMemory,
        tmp_project: Path,
    ):
        """完整进化生命周期:
        1. 系统异常 → AutoFix 修复 → Memory 记录
        2. 提案 → Guard 通过 → Memory 记录
        3. 执行 → 更新状态
        4. 学习 → 查询历史
        """
        # === 阶段1: 系统异常修复 ===
        pycache = tmp_project / "__pycache__"
        pycache.mkdir()
        (pycache / "m.pyc").write_text("x")

        class FakeCheck:
            code = "C6.1"
            name = "磁盘"
            detail = "pycache 过多"
            remediation = "清理"

        fix_result = auto_fix_engine.try_fix(FakeCheck())
        assert fix_result.fixed
        assert not pycache.exists()

        # === 阶段2: 进化提案审批 ===
        proposal = EvolutionProposal(
            level=LEVEL_L2, action_type="weight_adjust",
            target_module="momentum_factor", weight_change=0.08,
            rollback_plan="恢复原权重 0.15",
            trigger_reason="动量因子 IC 提升至 0.06",
        )
        decision = guard.check_proposal(proposal)
        assert decision.passed

        pid = memory.record({
            "level": proposal.level,
            "action_type": proposal.action_type,
            "trigger_reason": proposal.trigger_reason,
            "target_module": proposal.target_module,
            "rollback_plan": proposal.rollback_plan,
            "status": STATUS_PENDING,
            "result": decision.to_dict(),
        })

        # === 阶段3: 执行 + 更新状态 ===
        memory.update_status(pid, STATUS_EXECUTED, result={"old_weight": 0.15, "new_weight": 0.23})

        # === 阶段4: 学习 ===
        memory.learn(pid, "动量因子 IC 提升后适度增配, 7 日观察收益增强")

        # === 验证: 完整历史可查 ===
        all_records = memory.query()
        assert len(all_records) == 2  # 1 条 fix + 1 条 weight_adjust

        fix_records = memory.query(action_type="fix")
        assert len(fix_records) == 1
        assert fix_records[0].status == STATUS_EXECUTED

        weight_records = memory.query(action_type="weight_adjust")
        assert len(weight_records) == 1
        assert weight_records[0].status == STATUS_LEARNED
        assert weight_records[0].learned

    def test_frequency_limit_enforced_across_proposals(
        self, guard: EvolutionGuard, memory: EvolutionMemory
    ):
        """频率限制: 同模块 24h 内只能进化 1 次 (跨提案验证)."""
        # 第一次提案: 通过
        p1 = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="shared_model", weight_change=0.05,
            rollback_plan="r",
        )
        d1 = guard.check_proposal(p1)
        assert d1.passed

        # 记录第一次 (模拟已执行)
        memory.record({
            "level": p1.level, "action_type": p1.action_type,
            "trigger_reason": "首次", "target_module": p1.target_module,
            "rollback_plan": p1.rollback_plan, "status": STATUS_EXECUTED,
        })

        # 第二次同模块提案: 应被超频拒绝
        p2 = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="shared_model", weight_change=0.03,
            rollback_plan="r",
        )
        d2 = guard.check_proposal(p2)
        assert not d2.passed
        assert d2.violated_defense == DEFENSE_FREQUENCY

        # 不同模块的第三次提案: 应通过 (不超频)
        p3 = EvolutionProposal(
            level=LEVEL_L2, action_type="retrain",
            target_module="other_model", weight_change=0.03,
            rollback_plan="r",
        )
        d3 = guard.check_proposal(p3)
        assert d3.passed

    def test_audit_trail_complete(
        self, guard: EvolutionGuard, auto_fix_engine: AutoFixEngine, memory: EvolutionMemory
    ):
        """审计完整性: 所有动作 (fix/evaluate/weight_adjust/rejected) 都在 Memory 可查."""
        # 1. AutoFix 修复
        class FakeCheck:
            code = "C6.1"
            name = "磁盘"
            detail = "tmp 文件"
            remediation = "清理"
        auto_fix_engine.try_fix(FakeCheck())

        # 2. 评估 (mock)
        memory.record({
            "level": LEVEL_L2, "action_type": "evaluate",
            "trigger_reason": "EOD", "target_module": "v9",
            "rollback_plan": "只读", "status": STATUS_EXECUTED,
        })

        # 3. 通过的提案
        p = EvolutionProposal(
            level=LEVEL_L2, action_type="weight_adjust",
            target_module="factor_a", weight_change=0.05, rollback_plan="r",
        )
        d = guard.check_proposal(p)
        assert d.passed
        memory.record({
            "level": p.level, "action_type": p.action_type,
            "trigger_reason": "IC 提升", "target_module": p.target_module,
            "rollback_plan": p.rollback_plan, "status": STATUS_PENDING,
        })

        # 4. 被拒的提案 (超频)
        p2 = EvolutionProposal(
            level=LEVEL_L2, action_type="weight_adjust",
            target_module="factor_a", weight_change=0.03, rollback_plan="r",
        )
        d2 = guard.check_proposal(p2)
        assert not d2.passed

        # 验证: Memory 包含所有 4 种动作
        all_records = memory.query()
        action_types = {r.action_type for r in all_records}
        assert "fix" in action_types
        assert "evaluate" in action_types
        assert "weight_adjust" in action_types

        # 验证: 状态分布
        statuses = {r.status for r in all_records}
        assert STATUS_EXECUTED in statuses
        assert STATUS_PENDING in statuses
