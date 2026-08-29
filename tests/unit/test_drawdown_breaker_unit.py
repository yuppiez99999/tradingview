"""drawdown_breaker 单元测试 — 回撤分级熔断全分支覆盖"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.drawdown_breaker import (
    DrawdownCircuitBreaker,
    DrawdownDecision,
    DrawdownLevel,
)  # noqa: E402


@pytest.fixture
def breaker() -> DrawdownCircuitBreaker:
    return DrawdownCircuitBreaker()


class TestDrawdownLevel:
    def test_enum_values(self):
        assert DrawdownLevel.NORMAL.value == "NORMAL"
        assert DrawdownLevel.WATCH.value == "WATCH"
        assert DrawdownLevel.REDUCE.value == "REDUCE"
        assert DrawdownLevel.FORCE_HEDGE.value == "FORCE_HEDGE"
        assert DrawdownLevel.HALT.value == "HALT"

    def test_enum_is_str(self):
        for level in DrawdownLevel:
            assert isinstance(level, str)


class TestDrawdownDecisionToDict:
    def test_to_dict_format(self):
        d = DrawdownDecision(
            level=DrawdownLevel.REDUCE,
            current_drawdown=-0.123456,
            action="减仓",
            allow_new_buy=False,
            breach_hard_limit=False,
        )
        result = d.to_dict()
        assert result == {
            "level": "REDUCE",
            "current_drawdown": -0.1235,
            "action": "减仓",
            "allow_new_buy": False,
            "breach_hard_limit": False,
        }

    def test_to_dict_rounding_precision(self):
        d = DrawdownDecision(
            level=DrawdownLevel.NORMAL,
            current_drawdown=-0.01999999,
            action="正常交易",
            allow_new_buy=True,
            breach_hard_limit=False,
        )
        assert d.to_dict()["current_drawdown"] == -0.02

    def test_to_dict_halt_level(self):
        d = DrawdownDecision(
            level=DrawdownLevel.HALT,
            current_drawdown=-0.16,
            action="去风险",
            allow_new_buy=False,
            breach_hard_limit=True,
        )
        result = d.to_dict()
        assert result["level"] == "HALT"
        assert result["breach_hard_limit"] is True


class TestInitNormalization:
    def test_default_construction(self):
        b = DrawdownCircuitBreaker()
        assert b.max_drawdown == -0.15
        assert b.reduce_threshold == -0.08
        assert b.force_hedge_threshold == -0.12
        assert b.watch_threshold == -0.05

    def test_positive_max_drawdown_normalized(self):
        b = DrawdownCircuitBreaker(max_drawdown=0.15)
        assert b.max_drawdown == -0.15

    def test_positive_reduce_threshold_normalized(self):
        b = DrawdownCircuitBreaker(reduce_threshold=0.08)
        assert b.reduce_threshold == -0.08

    def test_positive_force_hedge_threshold_normalized(self):
        b = DrawdownCircuitBreaker(force_hedge_threshold=0.12)
        assert b.force_hedge_threshold == -0.12

    def test_positive_watch_threshold_normalized(self):
        b = DrawdownCircuitBreaker(watch_threshold=0.05)
        assert b.watch_threshold == -0.05

    def test_all_positive_normalized(self):
        b = DrawdownCircuitBreaker(
            max_drawdown=0.20,
            reduce_threshold=0.10,
            force_hedge_threshold=0.15,
            watch_threshold=0.06,
        )
        assert b.max_drawdown == -0.20
        assert b.reduce_threshold == -0.10
        assert b.force_hedge_threshold == -0.15
        assert b.watch_threshold == -0.06

    def test_negative_values_preserved(self):
        b = DrawdownCircuitBreaker(
            max_drawdown=-0.18,
            reduce_threshold=-0.09,
            force_hedge_threshold=-0.13,
            watch_threshold=-0.04,
        )
        assert b.max_drawdown == -0.18
        assert b.reduce_threshold == -0.09
        assert b.force_hedge_threshold == -0.13
        assert b.watch_threshold == -0.04

    def test_zero_normalized_to_zero(self):
        b = DrawdownCircuitBreaker(max_drawdown=0, watch_threshold=0)
        assert b.max_drawdown == 0.0
        assert b.watch_threshold == 0.0

    def test_int_input_converted_to_float(self):
        b = DrawdownCircuitBreaker(max_drawdown=1, watch_threshold=1)
        assert isinstance(b.max_drawdown, float)
        assert isinstance(b.watch_threshold, float)
        assert b.max_drawdown == -1.0


class TestEvaluateLevels:
    def test_normal_level(self, breaker):
        d = breaker.evaluate(-0.02)
        assert d.level == DrawdownLevel.NORMAL
        assert d.current_drawdown == -0.02
        assert d.allow_new_buy is True
        assert d.breach_hard_limit is False
        assert d.action == "正常交易"

    def test_watch_level(self, breaker):
        d = breaker.evaluate(-0.06)
        assert d.level == DrawdownLevel.WATCH
        assert d.allow_new_buy is True
        assert d.breach_hard_limit is False
        assert d.action == "监控并降低杠杆"

    def test_reduce_level(self, breaker):
        d = breaker.evaluate(-0.10)
        assert d.level == DrawdownLevel.REDUCE
        assert d.allow_new_buy is False
        assert d.breach_hard_limit is False
        assert d.action == "减仓至目标权重的 50%，禁止新建多头"

    def test_force_hedge_level(self, breaker):
        d = breaker.evaluate(-0.13)
        assert d.level == DrawdownLevel.FORCE_HEDGE
        assert d.allow_new_buy is False
        assert d.breach_hard_limit is False
        assert d.action == "强制买入尾部保护（股指 Put / 期货对冲）"

    def test_halt_level(self, breaker):
        d = breaker.evaluate(-0.16)
        assert d.level == DrawdownLevel.HALT
        assert d.allow_new_buy is False
        assert d.breach_hard_limit is True
        assert d.action == "全面停止买入并启动去风险（清杠杆）"


class TestEvaluateBoundaries:
    def test_exact_watch_threshold(self, breaker):
        d = breaker.evaluate(-0.05)
        assert d.level == DrawdownLevel.WATCH
        assert d.allow_new_buy is True

    def test_exact_reduce_threshold(self, breaker):
        d = breaker.evaluate(-0.08)
        assert d.level == DrawdownLevel.REDUCE
        assert d.allow_new_buy is False

    def test_exact_force_hedge_threshold(self, breaker):
        d = breaker.evaluate(-0.12)
        assert d.level == DrawdownLevel.FORCE_HEDGE
        assert d.breach_hard_limit is False

    def test_exact_max_drawdown_triggers_halt(self, breaker):
        d = breaker.evaluate(-0.15)
        assert d.level == DrawdownLevel.HALT
        assert d.breach_hard_limit is True
        assert d.allow_new_buy is False

    def test_just_above_watch_threshold(self, breaker):
        d = breaker.evaluate(-0.0499)
        assert d.level == DrawdownLevel.NORMAL

    def test_just_above_reduce_threshold(self, breaker):
        d = breaker.evaluate(-0.0799)
        assert d.level == DrawdownLevel.WATCH

    def test_just_above_force_hedge_threshold(self, breaker):
        d = breaker.evaluate(-0.1199)
        assert d.level == DrawdownLevel.REDUCE


class TestBreachHardLimit:
    def test_breach_false_below_max_drawdown(self, breaker):
        d = breaker.evaluate(-0.14)
        assert d.breach_hard_limit is False

    def test_breach_true_at_max_drawdown(self, breaker):
        d = breaker.evaluate(-0.15)
        assert d.breach_hard_limit is True

    def test_breach_true_beyond_max_drawdown(self, breaker):
        d = breaker.evaluate(-0.20)
        assert d.breach_hard_limit is True
        assert d.level == DrawdownLevel.HALT

    def test_breach_false_in_normal(self, breaker):
        d = breaker.evaluate(-0.01)
        assert d.breach_hard_limit is False

    def test_custom_max_drawdown_breach(self):
        b = DrawdownCircuitBreaker(
            max_drawdown=0.10,
            force_hedge_threshold=0.08,
            reduce_threshold=0.05,
        )
        d = b.evaluate(-0.10)
        assert d.breach_hard_limit is True
        assert d.level == DrawdownLevel.HALT


class TestEvaluateReturnType:
    def test_returns_drawdown_decision(self, breaker):
        d = breaker.evaluate(-0.05)
        assert isinstance(d, DrawdownDecision)

    def test_current_drawdown_preserved(self, breaker):
        d = breaker.evaluate(-0.0731)
        assert d.current_drawdown == -0.0731

    def test_int_input_converted(self, breaker):
        d = breaker.evaluate(0)
        assert d.level == DrawdownLevel.NORMAL
        assert d.current_drawdown == 0.0


class TestTargetScale:
    def test_full_scale_normal(self, breaker):
        assert breaker.target_scale(-0.02) == 1.0

    def test_full_scale_zero(self, breaker):
        assert breaker.target_scale(0.0) == 1.0

    def test_reduce_scale_watch(self, breaker):
        assert breaker.target_scale(-0.06) == 0.8

    def test_half_scale_reduce(self, breaker):
        assert breaker.target_scale(-0.10) == 0.5

    def test_half_scale_force_hedge(self, breaker):
        assert breaker.target_scale(-0.13) == 0.5

    def test_half_scale_halt(self, breaker):
        assert breaker.target_scale(-0.16) == 0.5

    def test_boundary_watch(self, breaker):
        assert breaker.target_scale(-0.05) == 0.8

    def test_boundary_reduce(self, breaker):
        assert breaker.target_scale(-0.08) == 0.5

    def test_positive_input_auto_convert_warns(self, breaker, caplog):
        with caplog.at_level(logging.WARNING, logger="drawdown_breaker"):
            result = breaker.target_scale(0.10)
        assert result == 0.5
        assert any("target_scale 收到正数回撤" in r.message for r in caplog.records)

    def test_positive_input_small_auto_convert(self, breaker, caplog):
        with caplog.at_level(logging.WARNING, logger="drawdown_breaker"):
            result = breaker.target_scale(0.02)
        assert result == 1.0

    def test_positive_input_watch_range(self, breaker, caplog):
        with caplog.at_level(logging.WARNING, logger="drawdown_breaker"):
            result = breaker.target_scale(0.06)
        assert result == 0.8

    def test_int_input_target_scale(self, breaker):
        assert breaker.target_scale(-1) == 0.5


class TestCustomThresholds:
    def test_custom_thresholds_evaluate(self):
        b = DrawdownCircuitBreaker(
            max_drawdown=0.20,
            reduce_threshold=-0.10,
            force_hedge_threshold=-0.15,
            watch_threshold=-0.06,
        )
        d_normal = b.evaluate(-0.05)
        assert d_normal.level == DrawdownLevel.NORMAL

        d_watch = b.evaluate(-0.06)
        assert d_watch.level == DrawdownLevel.WATCH

        d_reduce = b.evaluate(-0.10)
        assert d_reduce.level == DrawdownLevel.REDUCE

        d_force = b.evaluate(-0.15)
        assert d_force.level == DrawdownLevel.FORCE_HEDGE
        assert d_force.breach_hard_limit is False

        d_halt = b.evaluate(-0.20)
        assert d_halt.level == DrawdownLevel.HALT
        assert d_halt.breach_hard_limit is True

    def test_custom_thresholds_target_scale(self):
        b = DrawdownCircuitBreaker(
            reduce_threshold=-0.10,
            watch_threshold=-0.06,
        )
        assert b.target_scale(-0.05) == 1.0
        assert b.target_scale(-0.06) == 0.8
        assert b.target_scale(-0.10) == 0.5
