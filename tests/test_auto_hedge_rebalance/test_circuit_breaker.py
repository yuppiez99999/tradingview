"""CircuitBreaker 单元测试 — 覆盖触发/边界/锁定/解除/持久化。

测试策略:
    - 覆盖率目标 ≥ 95%
    - 边界值精确测试
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.auto_hedge_rebalance.circuit_breaker import CircuitBreaker


@pytest.fixture
def state_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_breaker_state.json")


@pytest.fixture
def breaker(state_path: str) -> CircuitBreaker:
    return CircuitBreaker(state_path=state_path)


class TestCheckNoTrigger:
    """不触发测试。"""

    def test_normal_no_trigger(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act
        status = breaker.check(daily_drop=0.02, max_drawdown=0.10)
        # Assert
        assert status.active is False

    def test_daily_drop_boundary_not_trigger(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act — 恰好 5% 不触发 (严格大于)
        status = breaker.check(daily_drop=0.05, max_drawdown=0.10)
        # Assert
        assert status.active is False

    def test_drawdown_boundary_not_trigger(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act — 恰好 25% 不触发
        status = breaker.check(daily_drop=0.02, max_drawdown=0.25)
        # Assert
        assert status.active is False


class TestCheckTrigger:
    """触发测试。"""

    def test_daily_drop_trigger_emergency_reassess(
        self, breaker: CircuitBreaker
    ) -> None:
        # Arrange & Act — 5.1% 触发紧急再评估
        status = breaker.check(daily_drop=0.051, max_drawdown=0.10)
        # Assert
        assert status.active is False  # 紧急再评估不锁定熔断
        assert status.trigger_reason is not None

    def test_drawdown_trigger_circuit_break(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act — 25.1% 触发熔断
        status = breaker.check(daily_drop=0.02, max_drawdown=0.251)
        # Assert
        assert status.active is True
        assert "回撤" in status.trigger_reason

    def test_circuit_breaker_stays_active(self, breaker: CircuitBreaker) -> None:
        # Arrange — 先触发熔断
        breaker.check(daily_drop=0.02, max_drawdown=0.26)
        # Act — 再次检查，应保持锁定
        status = breaker.check(daily_drop=0.01, max_drawdown=0.15)
        # Assert
        assert status.active is True


class TestTriggerEmergency:
    """紧急动作测试。"""

    def test_trigger_circuit_break_action(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act
        action = breaker.trigger_emergency("circuit_break")
        # Assert
        assert action.action_type == "circuit_break"
        assert action.hedge_ratio_target == 0.40
        assert action.rebalance_paused is True
        assert breaker.is_active() is True

    def test_trigger_emergency_reassess_action(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act
        action = breaker.trigger_emergency("emergency_reassess")
        # Assert
        assert action.action_type == "emergency_reassess"
        assert action.hedge_ratio_target == 0.40
        assert action.rebalance_paused is True


class TestRelease:
    """解除测试。"""

    def test_release_active_breaker(self, breaker: CircuitBreaker) -> None:
        # Arrange — 先触发熔断
        breaker.check(daily_drop=0.02, max_drawdown=0.26)
        # Act
        success = breaker.release("admin", "风险已控制")
        # Assert
        assert success is True
        assert breaker.is_active() is False

    def test_release_inactive_breaker(self, breaker: CircuitBreaker) -> None:
        # Arrange & Act — 未激活时尝试解除
        success = breaker.release("admin", "测试")
        # Assert
        assert success is False


class TestStatePersistence:
    """状态持久化测试。"""

    def test_status_persisted(self, state_path: str, breaker: CircuitBreaker) -> None:
        # Arrange & Act
        breaker.check(daily_drop=0.02, max_drawdown=0.26)
        # Assert
        assert Path(state_path).exists()
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["breaker_status"]["active"] is True

    def test_status_loaded_from_file(self, state_path: str) -> None:
        # Arrange — 先触发熔断
        cb1 = CircuitBreaker(state_path=state_path)
        cb1.check(daily_drop=0.02, max_drawdown=0.26)
        # Act — 创建新实例，应加载已持久化的状态
        cb2 = CircuitBreaker(state_path=state_path)
        # Assert
        assert cb2.is_active() is True

    def test_release_persisted(self, state_path: str, breaker: CircuitBreaker) -> None:
        # Arrange
        breaker.check(daily_drop=0.02, max_drawdown=0.26)
        # Act
        breaker.release("admin", "解除")
        # Assert
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["breaker_status"]["active"] is False
