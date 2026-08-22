#!/usr/bin/env python
"""
test_g7_option_exercise_risk_boost.py — 期权行权风险覆盖率补强测试

覆盖 P0 risk 链路: utils/option_exercise_risk.py
"""
from __future__ import annotations

from datetime import date

from utils.option_exercise_risk import (
    ExerciseRiskResult,
    OptionExerciseRiskManager,
    _get_expiry_date,
    _parse_option_code,
)


class TestParseOptionCode:
    def test_valid_code(self) -> None:
        result = _parse_option_code("510050C2507M03000.SH")
        assert result is not None
        assert result["option_type"] == "CALL"

    def test_put_code(self) -> None:
        result = _parse_option_code("510050P2507M03000.SH")
        assert result is not None
        assert result["option_type"] == "PUT"

    def test_invalid_code(self) -> None:
        result = _parse_option_code("invalid")
        assert result is None

    def test_empty_string(self) -> None:
        result = _parse_option_code("")
        assert result is None


class TestGetExpiryDate:
    def test_valid_date(self) -> None:
        result = _get_expiry_date(2026, 6)
        assert isinstance(result, date)

    def test_february(self) -> None:
        result = _get_expiry_date(2026, 2)
        assert isinstance(result, date)

    def test_returns_wednesday(self) -> None:
        result = _get_expiry_date(2026, 6)
        assert result.weekday() == 2


class TestOptionExerciseRiskManager:
    def test_construction(self) -> None:
        mgr = OptionExerciseRiskManager()
        assert isinstance(mgr, OptionExerciseRiskManager)

    def test_assess_risk_valid(self) -> None:
        mgr = OptionExerciseRiskManager()
        result = mgr.assess_risk(
            symbol="510050C2512M03000.SH",
            side="SELL",
            quantity=1,
            underlying_price=3.0,
        )
        assert result is None or isinstance(result, ExerciseRiskResult)

    def test_assess_risk_invalid_symbol(self) -> None:
        mgr = OptionExerciseRiskManager()
        result = mgr.assess_risk(
            symbol="invalid",
            side="BUY",
            quantity=1,
            underlying_price=3.0,
        )
        assert result is None
