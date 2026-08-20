"""T12 单元测试 — KillSwitchManager 三级熔断门面."""
from __future__ import annotations

import pytest

from utils.risk.kill_switch_manager import (
    KillLevel,
    KillSwitchManager,
)


class TestThresholdAndLevelClassification:
    def test_default_thresholds_strictly_increasing(self):
        # 默认阈值 50% → 75% → 95%
        ks = KillSwitchManager()
        assert ks.thresholds[KillLevel.CAUTION] == 0.50
        assert ks.thresholds[KillLevel.REDUCTION] == 0.75
        assert ks.thresholds[KillLevel.LIQUIDATE] == 0.95

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            KillSwitchManager(thresholds={
                KillLevel.CAUTION: 0.80,    # CAUTION 比 REDUCTION 还高 → 违反约束
                KillLevel.REDUCTION: 0.50,
                KillLevel.LIQUIDATE: 0.95,
            })

    def test_level_classification(self):
        ks = KillSwitchManager()
        assert ks.update_margin_usage(0.49) == KillLevel.NORMAL
        assert ks.update_margin_usage(0.50) == KillLevel.CAUTION
        assert ks.update_margin_usage(0.74) == KillLevel.CAUTION
        assert ks.update_margin_usage(0.75) == KillLevel.REDUCTION
        assert ks.update_margin_usage(0.94) == KillLevel.REDUCTION
        assert ks.update_margin_usage(0.95) == KillLevel.LIQUIDATE
        assert ks.update_margin_usage(1.00) == KillLevel.LIQUIDATE

    def test_margin_out_of_range_clamped(self):
        ks = KillSwitchManager()
        assert ks.update_margin_usage(-0.1) == KillLevel.NORMAL  # clamp 到 0
        assert ks.update_margin_usage(2.0) == KillLevel.LIQUIDATE  # clamp 到 1


class TestL1CautionLevel:
    def setup_method(self):
        self.ks = KillSwitchManager()
        self.ks.update_margin_usage(0.62)  # L1

    def test_l1_blocks_new_buy(self):
        d = self.ks.evaluate_trade("sh1", "buy", 100_000, is_open_new=True)
        assert not d.allowed
        assert "L1" in d.reason
        assert "禁止开新仓" in d.reason

    def test_l1_allows_reducing_sell(self):
        d = self.ks.evaluate_trade("sh1", "sell", 100_000, is_open_new=False)
        assert d.allowed
        d2 = self.ks.evaluate_trade("sh1", "close", 100_000, is_open_new=False)
        assert d2.allowed

    def test_l1_can_open_new_position(self):
        assert self.ks.can_open_new_position() is False


class TestL2ReductionLevel:
    def setup_method(self):
        self.ks = KillSwitchManager()
        self.ks.update_margin_usage(0.80)  # L2

    def test_l2_blocks_any_new_exposure(self):
        d = self.ks.evaluate_trade("sh1", "buy", 10_000, is_open_new=True)
        assert not d.allowed
        assert "L2" in d.reason
        # 即使不是"明显开新仓"但 side 不是减仓类, 也拦截
        d2 = self.ks.evaluate_trade("sh1", "buy_to_open", 1000, is_open_new=True)
        assert not d2.allowed

    def test_l2_allows_reducing(self):
        d = self.ks.evaluate_trade("sh1", "sell", 10_000, is_open_new=False)
        assert d.allowed


class TestL3LiquidateLevel:
    def setup_method(self):
        self.ks = KillSwitchManager()
        self.ks.update_margin_usage(0.99)  # L3

    def test_l3_only_allows_sell_close_cover(self):
        # 买入类: 拒绝
        assert not self.ks.evaluate_trade("s1", "buy", 100, is_open_new=False).allowed
        # 卖出/平仓/回补: 允许
        assert self.ks.evaluate_trade("s1", "sell", 100).allowed
        assert self.ks.evaluate_trade("s1", "close", 100).allowed
        assert self.ks.evaluate_trade("s1", "short_cover", 100).allowed


class TestAuditCounters:
    def test_level_changes_increment_trigger_counters(self):
        ks = KillSwitchManager()
        ks.update_margin_usage(0.60)  # → L1
        ks.update_margin_usage(0.40)  # → L0
        ks.update_margin_usage(0.80)  # → L2
        ks.update_margin_usage(0.50)  # → L1
        ks.update_margin_usage(0.99)  # → L3
        a = ks.audit()
        assert a.total_triggered_L1 >= 2
        assert a.total_triggered_L2 >= 1
        assert a.total_triggered_L3 >= 1

    def test_blocked_order_counter(self):
        ks = KillSwitchManager()
        ks.update_margin_usage(0.60)
        for _ in range(5):
            ks.evaluate_trade("s1", "buy", 1000, is_open_new=True)
        assert ks.audit().blocked_orders == 5


class TestLevelTransitionBackToNormal:
    def test_recovering_margin_allows_new_trades(self):
        ks = KillSwitchManager()
        ks.update_margin_usage(0.80)  # L2
        assert not ks.evaluate_trade("s", "buy", 100, is_open_new=True).allowed
        ks.update_margin_usage(0.30)  # 回到 L0
        d = ks.evaluate_trade("s", "buy", 100, is_open_new=True)
        assert d.allowed
        assert d.level == KillLevel.NORMAL
        assert ks.can_open_new_position()
