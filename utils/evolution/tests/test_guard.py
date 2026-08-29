"""EvolutionGuard 单元测试.

任务编号: T1.3 (Phase 1 防御层加固)
验收要求: 单测覆盖率 >= 90%

测试范围:
    - 防线1 频率限制: 超频拒绝 / 首次通过 / 被拒提案不占配额 / Memory 异常容错
    - 防线2 幅度限制: 未超幅通过 / 超幅截断 / 负权重截断 / 边界值
    - 防线3 回滚就绪: L1 无需 / L2 缺回滚拒绝 / L3 缺回滚拒绝 / 有回滚通过
    - 防线4 影子隔离: L1/L2 无需 / L3 天数不足拒绝 / L3 天数足够通过
    - 防线5 熔断冻结: 无事件通过 / L2 冻结 L2/L3 / L3 冻结所有 / L1 不冻结 / 过期解冻
    - 组合: 五道全过 / 字段校验 / 截断后继续后续防线
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from utils.evolution.guard import (
    DEFAULT_MAX_WEIGHT_CHANGE,
    DEFAULT_SHADOW_DAYS_REQUIRED,
    DEFENSE_FREQUENCY,
    DEFENSE_KILL_SWITCH,
    DEFENSE_ROLLBACK,
    DEFENSE_SHADOW,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    EvolutionGuard,
    EvolutionProposal,
    GuardDecision,
    GuardValidationError,
)
from utils.evolution.memory import STATUS_REJECTED, EvolutionMemory

# ============================================================
# Mock KillSwitch
# ============================================================


class MockKillSwitch:
    """模拟 KillSwitch, 用于测试防线5.

    通过 events 属性预设熔断事件历史.
    """

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def get_event_history(self, days: int = 30) -> list[dict]:
        """返回预设的事件列表 (忽略 days 参数, 由测试构造合适的时间)."""
        return list(self.events)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_memory(tmp_path: Path) -> EvolutionMemory:
    """临时 Memory 实例 (每个测试独立)."""
    return EvolutionMemory(memory_path=tmp_path / "memory.jsonl")


@pytest.fixture
def guard(tmp_memory: EvolutionMemory) -> EvolutionGuard:
    """带 Memory 但无 KillSwitch 的 Guard."""
    return EvolutionGuard(memory=tmp_memory)


@pytest.fixture
def guard_no_deps() -> EvolutionGuard:
    """无 Memory 无 KillSwitch 的 Guard (测试容错)."""
    return EvolutionGuard()


def make_proposal(
    level: str = LEVEL_L2,
    action_type: str = "retrain",
    target_module: str = "lgb_trainer",
    weight_change: float = 0.05,
    rollback_plan: str = "回滚至基线",
    shadow_days: int = 0,
) -> EvolutionProposal:
    """构造标准提案 (便于测试)."""
    return EvolutionProposal(
        level=level,
        action_type=action_type,
        target_module=target_module,
        weight_change=weight_change,
        rollback_plan=rollback_plan,
        shadow_days=shadow_days,
    )


# ============================================================
# 字段校验测试
# ============================================================


class TestProposalValidation:
    def test_invalid_level_raises(self, guard: EvolutionGuard):
        """无效 level 应抛 GuardValidationError."""
        with pytest.raises(GuardValidationError):
            guard.check_proposal(make_proposal(level="L9"))

    def test_empty_action_type_raises(self, guard: EvolutionGuard):
        """空 action_type 应抛 GuardValidationError."""
        with pytest.raises(GuardValidationError):
            guard.check_proposal(make_proposal(action_type=""))

    def test_empty_target_module_raises(self, guard: EvolutionGuard):
        """空 target_module 应抛 GuardValidationError."""
        with pytest.raises(GuardValidationError):
            guard.check_proposal(make_proposal(target_module=""))


# ============================================================
# 防线1 频率限制测试
# ============================================================


class TestFrequencyLimit:
    def test_first_proposal_passes(self, guard: EvolutionGuard):
        """首次提案应通过频率检查."""
        decision = guard.check_proposal(make_proposal())
        assert decision.passed
        assert decision.violated_defense == 0

    def test_second_proposal_same_module_rejected(
        self, guard: EvolutionGuard, tmp_memory: EvolutionMemory
    ):
        """同模块 24h 内第二次提案应被拒 (超频)."""
        # 先记录一条 (模拟已执行的进化)
        tmp_memory.record(
            {
                "level": LEVEL_L2,
                "action_type": "retrain",
                "target_module": "lgb_trainer",
                "trigger_reason": "r",
                "rollback_plan": "rollback",
                "status": "executed",
            }
        )

        decision = guard.check_proposal(make_proposal())
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_FREQUENCY
        assert "超频" in decision.reason

    def test_different_module_passes(
        self, guard: EvolutionGuard, tmp_memory: EvolutionMemory
    ):
        """不同模块的提案不冲突."""
        tmp_memory.record(
            {
                "level": LEVEL_L2,
                "action_type": "retrain",
                "target_module": "lgb_trainer",
                "trigger_reason": "r",
                "rollback_plan": "rollback",
                "status": "executed",
            }
        )

        decision = guard.check_proposal(make_proposal(target_module="other_module"))
        assert decision.passed

    def test_rejected_proposal_not_counted(
        self, guard: EvolutionGuard, tmp_memory: EvolutionMemory
    ):
        """被 Guard 拒绝的提案不占频率配额."""
        tmp_memory.record(
            {
                "level": LEVEL_L2,
                "action_type": "retrain",
                "target_module": "lgb_trainer",
                "trigger_reason": "r",
                "rollback_plan": "rollback",
                "status": STATUS_REJECTED,  # 被拒
            }
        )

        # 仍可通过 (被拒不占配额)
        decision = guard.check_proposal(make_proposal())
        assert decision.passed

    def test_no_memory_skips_frequency(self, guard_no_deps: EvolutionGuard):
        """无 Memory 时应跳过频率检查 (容错放行)."""
        decision = guard_no_deps.check_proposal(make_proposal())
        assert decision.passed
        assert "跳过" in decision.reason or "通过" in decision.reason

    def test_memory_query_exception_tolerated(self, tmp_path: Path):
        """Memory 查询异常应容错放行 (不阻塞进化)."""
        # 用目录作为 memory_path 触发查询异常
        mem = EvolutionMemory(memory_path=tmp_path)  # tmp_path 是目录
        guard = EvolutionGuard(memory=mem)
        decision = guard.check_proposal(make_proposal())
        assert decision.passed  # 容错放行


# ============================================================
# 防线2 幅度限制测试
# ============================================================


class TestMagnitudeLimit:
    def test_within_limit_passes(self, guard: EvolutionGuard):
        """未超幅应通过."""
        decision = guard.check_proposal(make_proposal(weight_change=0.05))
        assert decision.passed
        assert decision.truncated_weight_change is None

    def test_at_limit_passes(self, guard: EvolutionGuard):
        """等于上限应通过 (边界)."""
        decision = guard.check_proposal(
            make_proposal(weight_change=DEFAULT_MAX_WEIGHT_CHANGE)
        )
        assert decision.passed
        assert decision.truncated_weight_change is None

    def test_over_limit_truncated(self, guard: EvolutionGuard):
        """超幅应截断 (不拒绝)."""
        decision = guard.check_proposal(make_proposal(weight_change=0.25))
        assert decision.passed  # 截断后通过
        assert decision.truncated_weight_change == pytest.approx(
            DEFAULT_MAX_WEIGHT_CHANGE
        )
        assert decision.original_weight_change == 0.25
        assert "截断" in decision.reason

    def test_negative_weight_truncated(self, guard: EvolutionGuard):
        """负权重超幅应保留符号截断."""
        decision = guard.check_proposal(make_proposal(weight_change=-0.30))
        assert decision.passed
        assert decision.truncated_weight_change == pytest.approx(
            -DEFAULT_MAX_WEIGHT_CHANGE
        )
        assert decision.original_weight_change == -0.30

    def test_zero_weight_passes(self, guard: EvolutionGuard):
        """零权重应通过 (无调整)."""
        decision = guard.check_proposal(make_proposal(weight_change=0.0))
        assert decision.passed
        assert decision.truncated_weight_change is None


# ============================================================
# 防线3 回滚就绪测试
# ============================================================


class TestRollbackReady:
    def test_l1_without_rollback_passes(self, guard: EvolutionGuard):
        """L1 无需回滚方案."""
        decision = guard.check_proposal(make_proposal(level=LEVEL_L1, rollback_plan=""))
        assert decision.passed

    def test_l2_without_rollback_rejected(self, guard: EvolutionGuard):
        """L2 缺回滚方案应被拒."""
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2, rollback_plan=""))
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_ROLLBACK
        assert "rollback_plan" in decision.reason

    def test_l3_without_rollback_rejected(self, guard: EvolutionGuard):
        """L3 缺回滚方案应被拒."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L3, rollback_plan="", shadow_days=10)
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_ROLLBACK

    def test_l2_with_rollback_passes(self, guard: EvolutionGuard):
        """L2 有回滚方案应通过防线3."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L2, rollback_plan="回滚至基线")
        )
        assert decision.passed

    def test_whitespace_rollback_rejected(self, guard: EvolutionGuard):
        """纯空白的 rollback_plan 应被拒."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L2, rollback_plan="   ")
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_ROLLBACK


# ============================================================
# 防线4 影子隔离测试
# ============================================================


class TestShadowIsolation:
    def test_l1_without_shadow_passes(self, guard: EvolutionGuard):
        """L1 无需影子验证."""
        decision = guard.check_proposal(make_proposal(level=LEVEL_L1, rollback_plan=""))
        assert decision.passed

    def test_l2_without_shadow_passes(self, guard: EvolutionGuard):
        """L2 无需影子验证 (有 A/B Test 兜底)."""
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert decision.passed

    def test_l3_insufficient_shadow_rejected(self, guard: EvolutionGuard):
        """L3 影子天数不足应被拒."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L3, shadow_days=3, rollback_plan="rollback")
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_SHADOW
        assert "影子" in decision.reason

    def test_l3_exact_required_days_passes(self, guard: EvolutionGuard):
        """L3 影子天数正好等于要求应通过 (边界)."""
        decision = guard.check_proposal(
            make_proposal(
                level=LEVEL_L3,
                shadow_days=DEFAULT_SHADOW_DAYS_REQUIRED,
                rollback_plan="rollback",
            )
        )
        assert decision.passed

    def test_l3_sufficient_shadow_passes(self, guard: EvolutionGuard):
        """L3 影子天数充足应通过."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L3, shadow_days=10, rollback_plan="rollback")
        )
        assert decision.passed


# ============================================================
# 防线5 熔断冻结测试
# ============================================================


class TestKillSwitchFreeze:
    def _make_event(self, level: int, hours_ago: float = 1.0) -> dict:
        """构造熔断事件 (hours_ago 小时前触发)."""
        ts = datetime.now(UTC) - timedelta(hours=hours_ago)
        return {
            "level": level,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "executed": True,
        }

    def test_no_kill_switch_skips(self, guard: EvolutionGuard):
        """无 KillSwitch 应跳过熔断检查 (容错放行)."""
        decision = guard.check_proposal(make_proposal())
        assert decision.passed

    def test_no_events_passes(self, tmp_memory: EvolutionMemory):
        """无熔断事件应通过."""
        ks = MockKillSwitch(events=[])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal())
        assert decision.passed

    def test_l2_freezes_l2(self, tmp_memory: EvolutionMemory):
        """L2 熔断应冻结 L2 进化."""
        ks = MockKillSwitch(events=[self._make_event(level=2, hours_ago=1)])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_KILL_SWITCH
        assert "L2" in decision.reason

    def test_l2_freezes_l3(self, tmp_memory: EvolutionMemory):
        """L2 熔断应冻结 L3 进化."""
        ks = MockKillSwitch(events=[self._make_event(level=2, hours_ago=1)])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L3, shadow_days=10, rollback_plan="r")
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_KILL_SWITCH

    def test_l2_does_not_freeze_l1(self, tmp_memory: EvolutionMemory):
        """L2 熔断不应冻结 L1 进化 (L1 是修复)."""
        ks = MockKillSwitch(events=[self._make_event(level=2, hours_ago=1)])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L1, rollback_plan=""))
        assert decision.passed

    def test_l3_freezes_all_levels(self, tmp_memory: EvolutionMemory):
        """L3 熔断应冻结所有层级."""
        ks = MockKillSwitch(events=[self._make_event(level=3, hours_ago=1)])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)

        for level, shadow, rb in [
            (LEVEL_L1, 0, ""),
            (LEVEL_L2, 0, "r"),
            (LEVEL_L3, 10, "r"),
        ]:
            decision = guard.check_proposal(
                make_proposal(level=level, shadow_days=shadow, rollback_plan=rb)
            )
            assert not decision.passed, f"L3 熔断应冻结 {level}"
            assert decision.violated_defense == DEFENSE_KILL_SWITCH

    def test_expired_event_unfreezes(self, tmp_memory: EvolutionMemory):
        """超过冻结期的事件应不再冻结 (24h+)."""
        ks = MockKillSwitch(events=[self._make_event(level=2, hours_ago=25)])  # 25h 前
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert decision.passed  # 已解冻

    def test_kill_switch_exception_tolerated(self, tmp_path: Path):
        """KillSwitch 查询异常应容错放行."""

        class FailingKS:
            def get_event_history(self, days: int = 30):
                raise RuntimeError("simulated failure")

        mem = EvolutionMemory(memory_path=tmp_path / "m.jsonl")
        guard = EvolutionGuard(memory=mem, kill_switch=FailingKS())
        decision = guard.check_proposal(make_proposal())
        assert decision.passed  # 容错放行

    def test_event_missing_timestamp_skipped(self, tmp_memory: EvolutionMemory):
        """缺 timestamp 的事件应被跳过."""
        ks = MockKillSwitch(events=[{"level": 2}])  # 无 timestamp
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert decision.passed

    def test_event_missing_level_skipped(self, tmp_memory: EvolutionMemory):
        """缺 level 的事件应被跳过."""
        ks = MockKillSwitch(
            events=[{"timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}]
        )
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert decision.passed

    def test_event_invalid_timestamp_skipped(self, tmp_memory: EvolutionMemory):
        """timestamp 格式错误的事件应被跳过."""
        ks = MockKillSwitch(events=[{"level": 2, "timestamp": "not-a-date"}])
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        decision = guard.check_proposal(make_proposal(level=LEVEL_L2))
        assert decision.passed


# ============================================================
# 组合 / 截断后继续检查 测试
# ============================================================


class TestCombinedDefenses:
    def test_all_defenses_pass_l2(self, guard: EvolutionGuard):
        """L2 提案五道全过."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L2, weight_change=0.05, rollback_plan="rollback")
        )
        assert decision.passed
        assert decision.violated_defense == 0
        assert decision.truncated_weight_change is None

    def test_all_defenses_pass_l3(self, guard: EvolutionGuard):
        """L3 提案五道全过 (含影子)."""
        decision = guard.check_proposal(
            make_proposal(
                level=LEVEL_L3,
                weight_change=0.08,
                rollback_plan="rollback",
                shadow_days=7,
            )
        )
        assert decision.passed

    def test_truncation_then_pass_subsequent(self, guard: EvolutionGuard):
        """超幅截断后应继续检查后续防线 (不拒绝)."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L2, weight_change=0.25, rollback_plan="rollback")
        )
        assert decision.passed  # 截断后通过
        assert decision.truncated_weight_change == pytest.approx(
            DEFAULT_MAX_WEIGHT_CHANGE
        )
        assert decision.original_weight_change == 0.25

    def test_frequency_rejected_before_magnitude(
        self, guard: EvolutionGuard, tmp_memory: EvolutionMemory
    ):
        """超频应在幅度检查前被拒 (短路)."""
        tmp_memory.record(
            {
                "level": LEVEL_L2,
                "action_type": "retrain",
                "target_module": "lgb_trainer",
                "trigger_reason": "r",
                "rollback_plan": "rollback",
                "status": "executed",
            }
        )
        # 即使超幅, 也应因超频被拒 (而非截断)
        decision = guard.check_proposal(make_proposal(weight_change=0.25))  # 超幅
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_FREQUENCY
        assert decision.truncated_weight_change is None

    def test_rollback_rejected_after_magnitude_truncation(self, guard: EvolutionGuard):
        """超幅截断后, 若缺回滚方案, 应被防线3拒绝."""
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L2, weight_change=0.25, rollback_plan="")
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_ROLLBACK

    def test_kill_switch_overrides_others(self, tmp_memory: EvolutionMemory):
        """熔断冻结应优先于其他防线 (即使其他防线也失败)."""
        ks = MockKillSwitch(
            events=[
                {
                    "level": 3,
                    "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                }
            ]
        )
        guard = EvolutionGuard(memory=tmp_memory, kill_switch=ks)
        # 注意: 熔断检查在防线5, 前面防线需先通过才会到防线5
        # 这里构造一个前4道都过、但防线5冻结的提案
        decision = guard.check_proposal(
            make_proposal(
                level=LEVEL_L3, weight_change=0.05, rollback_plan="r", shadow_days=10
            )
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_KILL_SWITCH


# ============================================================
# GuardDecision 数据类测试
# ============================================================


class TestGuardDecision:
    def test_to_dict_contains_all_fields(self):
        """to_dict 应包含所有字段."""
        d = GuardDecision(
            passed=True,
            reason="ok",
            violated_defense=0,
            violated_defense_name="",
            truncated_weight_change=0.1,
            original_weight_change=0.2,
        )
        result = d.to_dict()
        assert "passed" in result
        assert "reason" in result
        assert "violated_defense" in result
        assert "violated_defense_name" in result
        assert "truncated_weight_change" in result
        assert "original_weight_change" in result

    def test_to_dict_serializable(self):
        """to_dict 结果应可 JSON 序列化."""
        import json

        d = GuardDecision(passed=False, reason="rejected", violated_defense=2)
        json.dumps(d.to_dict())  # 不抛异常即可


# ============================================================
# 自定义阈值测试
# ============================================================


class TestCustomThresholds:
    def test_custom_daily_limit(self, tmp_memory: EvolutionMemory):
        """自定义 daily_evolution_limit=2 应允许 2 次."""
        guard = EvolutionGuard(memory=tmp_memory, daily_evolution_limit=2)
        # 记录 1 次
        tmp_memory.record(
            {
                "level": LEVEL_L2,
                "action_type": "retrain",
                "target_module": "m",
                "trigger_reason": "r",
                "rollback_plan": "r",
                "status": "executed",
            }
        )
        # 第 2 次应通过 (limit=2)
        decision = guard.check_proposal(make_proposal(target_module="m"))
        assert decision.passed

    def test_custom_max_weight_change(self, tmp_memory: EvolutionMemory):
        """自定义 max_weight_change=0.05 应截断 0.08."""
        guard = EvolutionGuard(memory=tmp_memory, max_weight_change=0.05)
        decision = guard.check_proposal(make_proposal(weight_change=0.08))
        assert decision.passed
        assert decision.truncated_weight_change == pytest.approx(0.05)

    def test_custom_shadow_days(self, tmp_memory: EvolutionMemory):
        """自定义 shadow_days_required=10 应拒绝 7 天的 L3."""
        guard = EvolutionGuard(memory=tmp_memory, shadow_days_required=10)
        decision = guard.check_proposal(
            make_proposal(level=LEVEL_L3, shadow_days=7, rollback_plan="r")
        )
        assert not decision.passed
        assert decision.violated_defense == DEFENSE_SHADOW
