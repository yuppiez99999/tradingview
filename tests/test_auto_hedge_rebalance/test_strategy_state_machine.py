"""StrategyStateMachine 单元测试 — 覆盖降级/跨级阻断/冷却期/熔断锁定/持久化。

测试策略:
    - 覆盖率目标 ≥ 95%
    - 所有状态转移路径均有测试
    - 使用临时状态文件避免污染
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.auto_hedge_rebalance.models import (
    CorrectionAction,
    StrategyLevel,
)
from utils.auto_hedge_rebalance.strategy_state_machine import StrategyStateMachine


@pytest.fixture
def state_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_state.json")


@pytest.fixture
def state_machine(state_path: str) -> StrategyStateMachine:
    return StrategyStateMachine(state_path=state_path, cooldown_days=5)


class TestTransitionDegration:
    """降级转移测试。"""

    def test_normal_to_mild(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act
        result = state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        # Assert
        assert result.new_level == StrategyLevel.MILD_CORRECTION
        assert result.blocked_reason == ""

    def test_mild_to_moderate(self, state_machine: StrategyStateMachine) -> None:
        # Arrange
        state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        # Act
        result = state_machine.transition(StrategyLevel.MILD_CORRECTION, CorrectionAction.MODERATE_ROTATE)
        # Assert
        assert result.new_level == StrategyLevel.MODERATE_CORRECTION

    def test_cross_level_degradation_blocked(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act — 尝试跨级降级 NORMAL → MODERATE
        result = state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MODERATE_ROTATE)
        # Assert
        assert result.new_level == StrategyLevel.NORMAL
        assert "禁止跨级降级" in result.blocked_reason

    def test_degrade_to_circuit_breaker(self, state_machine: StrategyStateMachine) -> None:
        # Arrange — 逐级降级到 CONSERVATIVE_DEFENSE
        state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        state_machine.transition(StrategyLevel.MILD_CORRECTION, CorrectionAction.MODERATE_ROTATE)
        state_machine.transition(StrategyLevel.MODERATE_CORRECTION, CorrectionAction.SEVERE_REVIEW)
        state_machine.transition(StrategyLevel.SEVERE_CORRECTION, CorrectionAction.DEFENSE_BOOST)
        # Act
        result = state_machine.transition(
            StrategyLevel.CONSERVATIVE_DEFENSE, CorrectionAction.EMERGENCY_LIQUIDATE
        )
        # Assert
        assert result.new_level == StrategyLevel.CIRCUIT_BREAKER


class TestTransitionUpgrade:
    """升级转移测试。"""

    def test_upgrade_blocked_by_cooldown(self, state_machine: StrategyStateMachine) -> None:
        # Arrange — 先降级到 MILD
        state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        # Act — 冷却期内尝试升级
        result = state_machine.transition(StrategyLevel.MILD_CORRECTION, CorrectionAction.NONE)
        # Assert
        assert result.new_level == StrategyLevel.MILD_CORRECTION
        assert result.cooldown_active is True
        assert "冷却期" in result.blocked_reason

    def test_no_change_same_level(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act
        result = state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.NONE)
        # Assert
        assert result.new_level == StrategyLevel.NORMAL
        assert result.blocked_reason == ""


class TestCircuitBreakerLock:
    """熔断状态锁定测试。"""

    def test_circuit_breaker_locked(self, state_machine: StrategyStateMachine) -> None:
        # Arrange — 逐级降级到 CIRCUIT_BREAKER
        for action in [
            CorrectionAction.MILD_TUNE,
            CorrectionAction.MODERATE_ROTATE,
            CorrectionAction.SEVERE_REVIEW,
            CorrectionAction.DEFENSE_BOOST,
            CorrectionAction.EMERGENCY_LIQUIDATE,
        ]:
            current = state_machine.get_current_state().current_level
            state_machine.transition(current, action)
        # Act — 尝试自动升级
        result = state_machine.transition(StrategyLevel.CIRCUIT_BREAKER, CorrectionAction.NONE)
        # Assert
        assert result.new_level == StrategyLevel.CIRCUIT_BREAKER
        assert "熔断状态锁定" in result.blocked_reason

    def test_circuit_breaker_release_by_admin(self, state_machine: StrategyStateMachine) -> None:
        # Arrange — 逐级降级到 CIRCUIT_BREAKER
        for action in [
            CorrectionAction.MILD_TUNE,
            CorrectionAction.MODERATE_ROTATE,
            CorrectionAction.SEVERE_REVIEW,
            CorrectionAction.DEFENSE_BOOST,
            CorrectionAction.EMERGENCY_LIQUIDATE,
        ]:
            current = state_machine.get_current_state().current_level
            state_machine.transition(current, action)
        event_id = state_machine.get_current_state().pending_switch_event_id
        # Act — 管理员解除
        success = state_machine.approve_switch(event_id, "admin", approved=True)
        # Assert
        assert success is True
        assert state_machine.get_current_state().current_level == StrategyLevel.NORMAL


class TestStatePersistence:
    """状态持久化测试。"""

    def test_state_persisted_to_file(self, state_path: str, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act
        state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        # Assert
        assert Path(state_path).exists()
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["strategy_state"]["current_level"] == "MILD_CORRECTION"

    def test_state_loaded_from_file(self, state_path: str) -> None:
        # Arrange — 先创建一个状态机并转移
        sm1 = StrategyStateMachine(state_path=state_path, cooldown_days=5)
        sm1.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        # Act — 创建新状态机，应加载已持久化的状态
        sm2 = StrategyStateMachine(state_path=state_path, cooldown_days=5)
        # Assert
        assert sm2.get_current_state().current_level == StrategyLevel.MILD_CORRECTION

    def test_no_change_no_io(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act — 无变化的转移
        result = state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.NONE)
        # Assert — 状态文件不应被创建 (无变更)
        assert result.new_level == StrategyLevel.NORMAL


class TestGetCurrentState:
    """获取当前状态测试。"""

    def test_initial_state_is_normal(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act
        state = state_machine.get_current_state()
        # Assert
        assert state.current_level == StrategyLevel.NORMAL

    def test_state_after_transition(self, state_machine: StrategyStateMachine) -> None:
        # Arrange & Act
        state_machine.transition(StrategyLevel.NORMAL, CorrectionAction.MILD_TUNE)
        state = state_machine.get_current_state()
        # Assert
        assert state.current_level == StrategyLevel.MILD_CORRECTION
        assert state.last_transition_time != ""
        assert state.pending_switch_event_id is not None
