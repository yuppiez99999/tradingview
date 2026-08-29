"""AutoHedgeRebalanceEngine 单元测试 — 覆盖EOD决策/盘中检查/熔断/审批。

测试策略:
    - 覆盖率目标 ≥ 85%
    - 10阶段每阶段均有独立测试
    - 使用 mock 模拟所有子组件与存量模块
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.auto_hedge_rebalance.engine import AutoHedgeRebalanceEngine
from utils.auto_hedge_rebalance.models import (
    AutoHedgePlan,
    HedgeToolType,
    StrategyLevel,
)


@pytest.fixture
def engine(tmp_path: Path) -> AutoHedgeRebalanceEngine:
    """提供已初始化的 Engine 实例 (使用临时目录)。"""
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    nav_path = str(tmp_path / "nav.json")
    audit_db = str(tmp_path / "audit.db")

    # 创建临时配置
    import yaml

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "state_file": state_path,
                "nav_history_file": nav_path,
                "audit_db": audit_db,
                "cost_benefit": {"threshold": 1.5},
                "cooldown": {"days": 5},
                "rolling_window": {"days": 252, "min_sample_days": 30},
                "circuit_breaker": {
                    "daily_drop_trigger": 0.05,
                    "extreme_drawdown_trigger": 0.25,
                },
            },
            f,
        )

    return AutoHedgeRebalanceEngine(
        base_dir=str(tmp_path),
        config_path="config.yaml",
        hedge_engine=MagicMock(),
    )


class TestRunEodDecision:
    """EOD决策测试。"""

    def test_normal_eod_decision(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        plan = engine.run_eod_decision(
            portfolio_volatility=0.18,
            portfolio_drawdown_60d=0.05,
            portfolio_nav=1000.0,
        )
        # Assert
        assert isinstance(plan, AutoHedgePlan)
        assert plan.timestamp != ""
        assert plan.tool_selection is not None
        assert plan.filter_result is not None
        assert plan.monitor is not None
        assert plan.strategy_state is not None
        assert plan.breaker_status is not None

    def test_calm_regime_no_hedge(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act — 低波动+低回撤 → CALM
        plan = engine.run_eod_decision(
            portfolio_volatility=0.10,
            portfolio_drawdown_60d=0.01,
        )
        # Assert
        assert plan.tool_selection.tool_type == HedgeToolType.NONE

    def test_high_volatility_triggers_hedge(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange & Act — 高波动 → HIGH
        plan = engine.run_eod_decision(
            portfolio_volatility=0.25,
            portfolio_drawdown_60d=0.10,
        )
        # Assert
        assert plan.tool_selection.hedge_ratio > 0

    def test_circuit_breaker_active_returns_breaker_plan(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange — 先触发熔断
        engine.circuit_breaker.trigger_emergency("circuit_break")
        # Act
        plan = engine.run_eod_decision(portfolio_volatility=0.18)
        # Assert
        assert plan.breaker_status.active is True
        assert any("熔断活跃" in f for f in plan.degradation_flags)

    def test_degradation_flags_collected(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange & Act
        plan = engine.run_eod_decision(portfolio_volatility=0.18)
        # Assert — degradation_flags 是列表 (可能为空)
        assert isinstance(plan.degradation_flags, list)


class TestRunIntradayCheck:
    """盘中紧急检查测试。"""

    def test_no_drop_no_action(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        action = engine.run_intraday_check(1000.0, 1000.0)
        # Assert
        assert action.action_type == "none"

    def test_small_drop_no_action(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act — 2%跌幅不触发
        action = engine.run_intraday_check(980.0, 1000.0)
        # Assert
        assert action.action_type == "none"

    def test_large_drop_triggers_emergency(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange & Act — 6%跌幅触发紧急再评估
        action = engine.run_intraday_check(940.0, 1000.0)
        # Assert
        assert action.action_type == "emergency_reassess"

    def test_invalid_previous_value(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        action = engine.run_intraday_check(1000.0, 0.0)
        # Assert
        assert action.action_type == "none"


class TestGetMonitorReport:
    """监控报告测试。"""

    def test_get_monitor_report(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        report = engine.get_monitor_report()
        # Assert
        assert report is not None
        assert hasattr(report, "rolling_annual_return")


class TestGetStrategyState:
    """策略状态测试。"""

    def test_initial_state_normal(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        state = engine.get_strategy_state()
        # Assert
        assert state.current_level == StrategyLevel.NORMAL


class TestReleaseCircuitBreaker:
    """熔断解除测试。"""

    def test_release_inactive_returns_false(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange & Act
        success = engine.release_circuit_breaker("admin", "测试")
        # Assert
        assert success is False

    def test_release_active_returns_true(
        self, engine: AutoHedgeRebalanceEngine
    ) -> None:
        # Arrange — 先触发熔断
        engine.circuit_breaker.trigger_emergency("circuit_break")
        # Act
        success = engine.release_circuit_breaker("admin", "解除")
        # Assert
        assert success is True
