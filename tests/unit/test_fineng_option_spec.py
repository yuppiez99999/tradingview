"""utils.fineng.instruments.option_spec 单元测试 — OptionSpec 期权合约规格枚举与属性方法"""

from __future__ import annotations

from datetime import date

import pytest

from utils.fineng.instruments.option_spec import (
    ExerciseStyle,
    OptionSpec,
    OptionType,
)


def test_option_spec_call_put_flags():
    """is_call / is_put 标志正确"""
    call = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.CALL,
        strike=3.5,
        expiry=date(2026, 6, 25),
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    put = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.PUT,
        strike=3.5,
        expiry=date(2026, 6, 25),
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    assert call.is_call is True
    assert call.is_put is False
    assert put.is_call is False
    assert put.is_put is True


def test_option_spec_days_to_expiry():
    """days_to_expiry / years_to_expiry 计算正确"""
    today = date(2026, 8, 10)
    exp = date(2026, 9, 1)
    opt = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.CALL,
        strike=3.5,
        expiry=exp,
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    assert opt.days_to_expiry(today) == 22
    assert opt.years_to_expiry(today) == pytest.approx(22 / 365.0, abs=1e-6)
    assert opt.days_to_expiry(exp) == 0
    assert opt.days_to_expiry(date(2026, 9, 2)) < 0


def test_option_spec_payoff_call():
    """CALL 到期收益 = max(S - K, 0) (不含 multiplier)"""
    call = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.CALL,
        strike=3.5,
        expiry=date(2026, 6, 25),
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    assert call.payoff(4.0) == pytest.approx(4.0 - 3.5)
    assert call.payoff(3.0) == pytest.approx(0.0)


def test_option_spec_payoff_put():
    """PUT 到期收益 = max(K - S, 0) (不含 multiplier)"""
    put = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.PUT,
        strike=3.5,
        expiry=date(2026, 6, 25),
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    assert put.payoff(3.0) == pytest.approx(3.5 - 3.0)
    assert put.payoff(4.0) == pytest.approx(0.0)


def test_option_spec_moneyness():
    """moneyness(spot) = spot / strike"""
    call = OptionSpec(
        underlying="510050.SH",
        option_type=OptionType.CALL,
        strike=3.5,
        expiry=date(2026, 6, 25),
        multiplier=10000,
        exercise_style=ExerciseStyle.EUROPEAN,
    )
    assert call.moneyness(4.0) == pytest.approx(4.0 / 3.5, abs=1e-6)
    assert call.moneyness(3.0) == pytest.approx(3.0 / 3.5, abs=1e-6)


def test_option_spec_enum_values():
    """枚举值正确"""
    assert OptionType.CALL.value == "CALL"
    assert OptionType.PUT.value == "PUT"
    assert ExerciseStyle.EUROPEAN.value == "EUROPEAN"
    assert ExerciseStyle.AMERICAN.value == "AMERICAN"
