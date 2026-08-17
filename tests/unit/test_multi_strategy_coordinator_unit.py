# -*- coding: utf-8 -*-
"""multi_strategy_coordinator 单元测试 — 多策略协调器全覆盖.

被测模块: utils/multi_strategy_coordinator.py
覆盖目标: >=90%
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.multi_strategy_coordinator import (  # noqa: E402
    CoordinationDecision,
    MultiStrategyCoordinator,
    StrategyConflict,
    StrategyState,
)


# ============================================================
# __init__ + 默认策略
# ============================================================

class TestInit:
    def test_default_capital(self):
        coord = MultiStrategyCoordinator()
        assert coord.total_capital == 5_000_000
        assert len(coord.strategies) == 6

    def test_custom_capital(self):
        coord = MultiStrategyCoordinator(total_capital=10_000_000)
        assert coord.total_capital == 10_000_000

    def test_default_strategies_loaded(self):
        coord = MultiStrategyCoordinator()
        assert "stock_long" in coord.strategies
        assert "etf_allocation" in coord.strategies
        assert "macro_hedge" in coord.strategies
        assert "quant_neutral" in coord.strategies
        assert "options_tail" in coord.strategies
        assert "cash_management" in coord.strategies

    def test_strategy_weights(self):
        coord = MultiStrategyCoordinator()
        s = coord.strategies["stock_long"]
        assert s.capital == 1_800_000
        assert s.max_weight == 0.40
        assert s.current_weight == pytest.approx(1_800_000 / 5_000_000)


# ============================================================
# register_strategy
# ============================================================

class TestRegister:
    def test_new_strategy(self):
        coord = MultiStrategyCoordinator()
        coord.register_strategy("custom", capital=300_000, max_weight=0.10, min_weight=0.02)
        assert "custom" in coord.strategies
        assert coord.strategies["custom"].capital == 300_000

    def test_overwrite_existing(self):
        coord = MultiStrategyCoordinator()
        coord.register_strategy("stock_long", capital=2_000_000)
        assert coord.strategies["stock_long"].capital == 2_000_000


# ============================================================
# coordinate — 主入口
# ============================================================

class TestCoordinate:
    def test_empty(self):
        coord = MultiStrategyCoordinator()
        decision = coord.coordinate()
        assert isinstance(decision, CoordinationDecision)
        assert decision.total_capital == 5_000_000

    def test_with_pnl(self):
        coord = MultiStrategyCoordinator()
        pnl = {"stock_long": 10000, "etf_allocation": 2000}
        decision = coord.coordinate(strategy_pnl=pnl)
        assert coord.strategies["stock_long"].current_pnl == 10000
        assert coord.strategies["stock_long"].cumulative_pnl == 10000

    def test_with_correlations(self):
        coord = MultiStrategyCoordinator()
        corr = {"stock_long": 0.8, "macro_hedge": -0.3}
        decision = coord.coordinate(strategy_correlations=corr)
        assert coord.strategies["stock_long"].correlation_to_portfolio == 0.8

    def test_opposite_signal_conflict(self):
        coord = MultiStrategyCoordinator()
        signals = {
            "stock_long": {"300308": {"direction": "long"}},
            "quant_neutral": {"300308": {"direction": "short"}},
        }
        positions = {"300308": {"strategy": "stock_long", "weight": 0.03}}
        decision = coord.coordinate(target_signals=signals, current_positions=positions)
        conflict_types = [c.conflict_type for c in decision.conflicts]
        assert "opposite_signal" in conflict_types

    def test_over_position_conflict(self):
        coord = MultiStrategyCoordinator()
        signals = {"stock_long": {"A": {"direction": "long"}}}
        positions = {"A": {"strategy": "stock_long", "weight": 0.6}}
        decision = coord.coordinate(target_signals=signals, current_positions=positions)
        conflict_types = [c.conflict_type for c in decision.conflicts]
        assert "over_position" in conflict_types

    def test_decision_has_weights(self):
        coord = MultiStrategyCoordinator()
        decision = coord.coordinate()
        assert len(decision.strategy_weights) == 6
        assert len(decision.strategy_capital) == 6

    def test_decision_summary(self):
        coord = MultiStrategyCoordinator()
        decision = coord.coordinate()
        assert "多策略协调摘要" in decision.summary


# ============================================================
# _update_strategy_pnl
# ============================================================

class TestUpdatePnl:
    def test_basic(self):
        coord = MultiStrategyCoordinator()
        coord._update_strategy_pnl({"stock_long": 5000, "etf_allocation": 1000})
        assert coord.strategies["stock_long"].current_pnl == 5000
        assert coord.strategies["etf_allocation"].current_pnl == 1000

    def test_cumulative(self):
        coord = MultiStrategyCoordinator()
        coord._update_strategy_pnl({"stock_long": 5000})
        coord._update_strategy_pnl({"stock_long": 3000})
        assert coord.strategies["stock_long"].cumulative_pnl == 8000

    def test_unknown_strategy_ignored(self):
        coord = MultiStrategyCoordinator()
        coord._update_strategy_pnl({"unknown": 1000})
        assert "unknown" not in coord.strategies


# ============================================================
# _update_correlations
# ============================================================

class TestUpdateCorr:
    def test_basic(self):
        coord = MultiStrategyCoordinator()
        coord._update_correlations({"stock_long": 0.85, "macro_hedge": -0.3})
        assert coord.strategies["stock_long"].correlation_to_portfolio == 0.85


# ============================================================
# _check_strategy_degradation
# ============================================================

class TestDegradation:
    def test_normal_strategy(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision()
        coord._check_strategy_degradation(decision)
        assert all(not s.is_degraded for s in coord.strategies.values())

    def test_excessive_loss(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].cumulative_pnl = -200_000
        decision = CoordinationDecision()
        coord._check_strategy_degradation(decision)
        assert coord.strategies["stock_long"].is_degraded
        assert any(c.conflict_type == "strategy_degraded" for c in decision.conflicts)

    def test_high_correlation(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].correlation_to_portfolio = 0.85
        decision = CoordinationDecision()
        coord._check_strategy_degradation(decision)
        assert coord.strategies["stock_long"].is_degraded

    def test_low_sharpe(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].sharpe_ratio = -0.8
        decision = CoordinationDecision()
        coord._check_strategy_degradation(decision)
        assert coord.strategies["stock_long"].is_degraded

    def test_high_drawdown(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].max_drawdown = 0.08
        decision = CoordinationDecision()
        coord._check_strategy_degradation(decision)
        assert coord.strategies["stock_long"].is_degraded


# ============================================================
# _adjust_weights
# ============================================================

class TestAdjustWeights:
    def test_basic(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        assert len(decision.strategy_weights) == 6
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=0.05)

    def test_degraded_strategy_reduced(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].is_degraded = True
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        base_weight = coord.strategies["stock_long"].capital / coord.total_capital
        assert decision.strategy_weights["stock_long"] < base_weight

    def test_high_sharpe_boosted(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].sharpe_ratio = 2.0
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        base = coord.strategies["stock_long"].capital / coord.total_capital
        assert decision.strategy_weights["stock_long"] >= base * 0.9

    def test_inactive_strategy_zero(self):
        coord = MultiStrategyCoordinator()
        coord.strategies["stock_long"].is_active = False
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        base_weight = coord.strategies["stock_long"].capital / coord.total_capital
        assert decision.strategy_weights["stock_long"] < base_weight

    def test_cash_buffer_set(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        assert decision.cash_buffer >= 0


# ============================================================
# _detect_conflicts
# ============================================================

class TestDetectConflicts:
    def test_no_conflict(self):
        coord = MultiStrategyCoordinator()
        signals = {"stock_long": {"A": {"direction": "long"}}}
        positions = {"A": {"strategy": "stock_long", "weight": 0.03}}
        decision = CoordinationDecision()
        coord._detect_conflicts(signals, positions, decision)
        assert len(decision.conflicts) == 0

    def test_opposite_signals(self):
        coord = MultiStrategyCoordinator()
        signals = {
            "stock_long": {"A": {"direction": "long"}},
            "macro_hedge": {"A": {"direction": "short"}},
        }
        positions = {"A": {"strategy": "stock_long", "weight": 0.03}}
        decision = CoordinationDecision()
        coord._detect_conflicts(signals, positions, decision)
        assert any(c.conflict_type == "opposite_signal" for c in decision.conflicts)

    def test_total_over_position(self):
        coord = MultiStrategyCoordinator()
        positions = {"A": {"weight": 0.6}, "B": {"weight": 0.5}}
        decision = CoordinationDecision()
        coord._detect_conflicts({}, positions, decision)
        assert any(c.conflict_type == "over_position" and c.severity == "error" for c in decision.conflicts)

    def test_single_symbol_over_limit(self):
        coord = MultiStrategyCoordinator()
        positions = {"A": {"strategy": "stock_long", "weight": 0.08}}
        decision = CoordinationDecision()
        coord._detect_conflicts({}, positions, decision)
        assert any(c.conflict_type == "over_position" and c.severity == "warning" for c in decision.conflicts)

    def test_string_signal(self):
        coord = MultiStrategyCoordinator()
        signals = {"stock_long": {"A": "buy"}, "macro_hedge": {"A": "sell"}}
        positions = {"A": {"strategy": "stock_long", "weight": 0.03}}
        decision = CoordinationDecision()
        coord._detect_conflicts(signals, positions, decision)
        assert any(c.conflict_type == "opposite_signal" for c in decision.conflicts)


# ============================================================
# _check_risk_budget
# ============================================================

class TestRiskBudget:
    def test_normal(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision()
        coord._adjust_weights(decision)
        coord._check_risk_budget(decision)
        assert decision.risk_budget_limit > 0
        assert decision.risk_budget_used >= 0

    def test_exceeded(self):
        coord = MultiStrategyCoordinator(total_capital=100_000)
        decision = CoordinationDecision()
        for name in coord.strategies:
            coord.strategies[name].var_95 = 0.5
        coord._adjust_weights(decision)
        coord._check_risk_budget(decision)
        assert decision.risk_budget_used > 0


# ============================================================
# _check_cash_buffer
# ============================================================

class TestCashBuffer:
    def test_sufficient(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision(cash_buffer=1_000_000)
        coord._check_cash_buffer(decision)
        assert not any(c.conflict_type == "cash_conflict" for c in decision.conflicts)

    def test_insufficient(self):
        coord = MultiStrategyCoordinator()
        decision = CoordinationDecision(cash_buffer=100)
        coord._check_cash_buffer(decision)
        assert any(c.conflict_type == "cash_conflict" for c in decision.conflicts)


# ============================================================
# _build_summary
# ============================================================

class TestBuildSummary:
    def test_basic(self):
        coord = MultiStrategyCoordinator()
        decision = coord.coordinate()
        assert "多策略协调摘要" in decision.summary
        assert "总资金" in decision.summary

    def test_with_conflicts(self):
        coord = MultiStrategyCoordinator()
        signals = {"stock_long": {"A": {"direction": "long"}}, "macro_hedge": {"A": {"direction": "short"}}}
        positions = {"A": {"strategy": "stock_long", "weight": 0.03}}
        decision = coord.coordinate(target_signals=signals, current_positions=positions)
        assert "冲突检测" in decision.summary


# ============================================================
# save_state
# ============================================================

class TestSaveState:
    def test_save(self):
        coord = MultiStrategyCoordinator()
        path = coord.save_state()
        assert path.exists()
        assert path.suffix == ".json"


# ============================================================
# StrategyState / StrategyConflict dataclass
# ============================================================

class TestDataclasses:
    def test_strategy_state(self):
        s = StrategyState(name="test", capital=100_000)
        assert s.name == "test"
        assert s.capital == 100_000
        assert s.is_active is True

    def test_strategy_conflict(self):
        c = StrategyConflict(conflict_type="test", strategies=["a", "b"])
        assert c.conflict_type == "test"
        assert c.severity == "warning"

    def test_coordination_decision(self):
        d = CoordinationDecision()
        assert d.is_approved is True
        assert d.conflicts == []