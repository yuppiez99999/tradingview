#!/usr/bin/env python
"""
test_g7_kill_switch_mgr_boost.py — 三级熔断管理器覆盖率补强测试

覆盖 P0 risk 链路: utils/risk/kill_switch_manager.py
"""
from __future__ import annotations

from utils.risk.kill_switch_manager import (
    KillDecision,
    KillLevel,
    KillSwitchAudit,
    KillSwitchManager,
)


class TestKillLevel:
    def test_order(self) -> None:
        assert KillLevel.NORMAL < KillLevel.CAUTION
        assert KillLevel.CAUTION < KillLevel.REDUCTION
        assert KillLevel.REDUCTION < KillLevel.LIQUIDATE

    def test_values(self) -> None:
        assert KillLevel.NORMAL == 0
        assert KillLevel.LIQUIDATE == 3


class TestKillSwitchManagerInit:
    def test_default_thresholds(self) -> None:
        ksm = KillSwitchManager()
        assert ksm.thresholds[KillLevel.CAUTION] == 0.50
        assert ksm.thresholds[KillLevel.REDUCTION] == 0.75
        assert ksm.thresholds[KillLevel.LIQUIDATE] == 0.95

    def test_custom_thresholds(self) -> None:
        custom = {KillLevel.CAUTION: 0.40, KillLevel.REDUCTION: 0.60, KillLevel.LIQUIDATE: 0.80}
        ksm = KillSwitchManager(thresholds=custom)
        assert ksm.thresholds[KillLevel.CAUTION] == 0.40


class TestUpdateMarginUsage:
    def test_normal_level(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.30)
        assert ksm._audit.level == KillLevel.NORMAL

    def test_caution_level(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)
        assert ksm._audit.level == KillLevel.CAUTION

    def test_reduction_level(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.78)
        assert ksm._audit.level == KillLevel.REDUCTION

    def test_liquidate_level(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.96)
        assert ksm._audit.level == KillLevel.LIQUIDATE

    def test_boundary_caution(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.50)
        assert ksm._audit.level == KillLevel.CAUTION

    def test_boundary_reduction(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.75)
        assert ksm._audit.level == KillLevel.REDUCTION

    def test_boundary_liquidate(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.95)
        assert ksm._audit.level == KillLevel.LIQUIDATE


class TestEvaluateTrade:
    def test_allowed_in_normal(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.30)
        decision = ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
        assert isinstance(decision, KillDecision)
        assert decision.allowed is True

    def test_blocked_in_caution_new_buy(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)
        decision = ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
        assert decision.allowed is False

    def test_sell_allowed_in_caution(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)
        decision = ksm.evaluate_trade(symbol="sh600519", side="sell", notional=100_000)
        assert decision.allowed is True

    def test_blocked_in_liquidate(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.96)
        decision = ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
        assert decision.allowed is False
        assert decision.level == KillLevel.LIQUIDATE

    def test_decision_has_reason(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.96)
        decision = ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
        assert isinstance(decision.reason, str)


class TestKillSwitchAudit:
    def test_initial_state(self) -> None:
        audit = KillSwitchAudit()
        assert audit.level == KillLevel.NORMAL
        assert audit.margin_usage == 0.0
        assert audit.total_triggered_L1 == 0

    def test_triggered_counts(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.55)
        assert ksm._audit.total_triggered_L1 >= 1

    def test_blocked_order_count(self) -> None:
        ksm = KillSwitchManager()
        ksm.update_margin_usage(0.96)
        ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
        assert ksm._audit.blocked_orders >= 1
