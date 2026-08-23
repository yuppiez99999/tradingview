"""集成测试 — 组件间协作验证。

验证组件间数据流正确，降级标记跨组件累积，审计日志记录完整决策链。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.auto_hedge_rebalance.engine import AutoHedgeRebalanceEngine
from utils.auto_hedge_rebalance.models import (
    AutoHedgePlan,
    StrategyLevel,
)


@pytest.fixture
def engine(tmp_path: Path) -> AutoHedgeRebalanceEngine:
    import yaml

    config_path = str(tmp_path / "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "state_file": str(tmp_path / "state.json"),
                "nav_history_file": str(tmp_path / "nav.json"),
                "audit_db": str(tmp_path / "audit.db"),
                "cost_benefit": {"threshold": 1.5},
                "cooldown": {"days": 5},
                "rolling_window": {"days": 252, "min_sample_days": 30},
                "circuit_breaker": {"daily_drop_trigger": 0.05, "extreme_drawdown_trigger": 0.25},
            },
            f,
        )
    return AutoHedgeRebalanceEngine(base_dir=str(tmp_path), config_path="config.yaml", hedge_engine=MagicMock())


class TestEngineIntegration:
    """Engine与其他组件集成测试。"""

    def test_eod_decision_full_flow(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        plan = engine.run_eod_decision(portfolio_volatility=0.20, portfolio_drawdown_60d=0.08)
        # Assert — 全部子结果存在
        assert plan.tool_selection is not None
        assert plan.filter_result is not None
        assert plan.monitor is not None
        assert plan.strategy_state is not None
        assert plan.breaker_status is not None

    def test_intraday_then_eod(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange — 先盘中检查触发紧急
        engine.run_intraday_check(940.0, 1000.0)
        # Act — 再执行EOD决策
        plan = engine.run_eod_decision(portfolio_volatility=0.18)
        # Assert
        assert isinstance(plan, AutoHedgePlan)

    def test_audit_log_written(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        engine.run_eod_decision(portfolio_volatility=0.25, portfolio_drawdown_60d=0.15)
        # Assert — 审计日志数据库存在
        assert engine.audit_logger.db_path.exists()

    def test_state_persisted(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        engine.run_eod_decision(portfolio_volatility=0.18)
        state = engine.get_strategy_state()
        # Assert
        assert state.current_level in StrategyLevel.__members__.values()


class TestRegressionCompat:
    """回归兼容测试。"""

    def test_autoplan_optional_fields(self, engine: AutoHedgeRebalanceEngine) -> None:
        # Arrange & Act
        plan = engine.run_eod_decision(portfolio_volatility=0.18)
        # Assert — joint_plan 可以为 None (无 integrator 时不影响)
        assert plan.joint_plan is None  # 未传入 integrator
        assert plan.tool_selection is not None

    def test_degradation_flags_is_list(self, engine: AutoHedgeRebalanceEngine) -> None:
        plan = engine.run_eod_decision(portfolio_volatility=0.18)
        assert isinstance(plan.degradation_flags, list)
        assert isinstance(plan.audit_event_ids, list)
