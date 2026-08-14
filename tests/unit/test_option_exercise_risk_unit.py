# -*- coding: utf-8 -*-
"""option_exercise_risk 单元测试 — 期权行权/指派风险管理全分支覆盖

被测模块: utils/option_exercise_risk.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.option_exercise_risk import (  # noqa: E402
    ExerciseRiskResult,
    OptionExerciseRiskManager,
    _get_expiry_date,
    _parse_option_code,
)

_CALL = "510050C2507M03000.SH"
_PUT = "510050P2507M03000.SH"


def _manager(days_to_expiry: int, code: str = _CALL) -> OptionExerciseRiskManager:
    mgr = OptionExerciseRiskManager()
    parsed = _parse_option_code(code)
    expiry = _get_expiry_date(parsed["year"], parsed["month"])
    mgr.today = expiry - timedelta(days=days_to_expiry)
    return mgr


class TestParseOptionCode:
    def test_valid_call(self):
        r = _parse_option_code(_CALL)
        assert r is not None
        assert r["underlying"] == "510050.SH"
        assert r["option_type"] == "CALL"
        assert r["year"] == 2025
        assert r["month"] == 7
        assert r["strike"] == 3.0
        assert r["exchange"] == "SH"

    def test_valid_put_sz(self):
        r = _parse_option_code("510050P2512M02500.SZ")
        assert r is not None
        assert r["underlying"] == "510050.SZ"
        assert r["option_type"] == "PUT"
        assert r["year"] == 2025
        assert r["month"] == 12
        assert r["strike"] == 2.5
        assert r["exchange"] == "SZ"

    def test_strike_scaling(self):
        r = _parse_option_code("510050C2507M03250.SH")
        assert r["strike"] == pytest.approx(3.25)

    def test_invalid_garbage(self):
        assert _parse_option_code("invalid") is None

    def test_invalid_exchange(self):
        assert _parse_option_code("510050C2507M03000.BJ") is None

    def test_missing_dot(self):
        assert _parse_option_code("510050C2507M03000SH") is None

    def test_wrong_prefix_length(self):
        assert _parse_option_code("51005C2507M03000.SH") is None

    def test_wrong_strike_length(self):
        assert _parse_option_code("510050C2507M0300.SH") is None


class TestGetExpiryDate:
    def test_july_2025_fourth_wednesday(self):
        assert _get_expiry_date(2025, 7) == date(2025, 7, 23)

    def test_august_2025_fourth_wednesday(self):
        assert _get_expiry_date(2025, 8) == date(2025, 8, 27)

    def test_october_2025_first_day_is_wednesday(self):
        assert _get_expiry_date(2025, 10) == date(2025, 10, 22)

    def test_january_2026(self):
        assert _get_expiry_date(2026, 1) == date(2026, 1, 28)


class TestAssessRiskInvalid:
    def test_unparseable_symbol_returns_none(self):
        mgr = _manager(10)
        assert mgr.assess_risk("garbage", "BUY", 10, 3.0, 0.05) is None

    def test_call_moneyness_zero_strike(self):
        mgr = _manager(10, "510050C2507M00000.SH")
        r = mgr.assess_risk("510050C2507M00000.SH", "SELL", 10, 3.3, 0.05)
        assert r is not None
        assert r.moneyness == 0
        assert r.is_itm is True

    def test_put_moneyness_zero_underlying(self):
        mgr = _manager(10, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 0.0, 0.05)
        assert r is not None
        assert r.moneyness == 0
        assert r.is_itm is True


class TestBuyerRiskCall:
    def test_expired_itm(self):
        mgr = _manager(-1)
        r = mgr.assess_risk(_CALL, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "EXPIRED"
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)
        assert r.side == "BUY"

    def test_expired_otm(self):
        mgr = _manager(-1)
        r = mgr.assess_risk(_CALL, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "EXPIRED"
        assert r.is_itm is False

    def test_certain_branch(self):
        mgr = _manager(1)
        r = mgr.assess_risk(_CALL, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "CERTAIN"
        assert r.is_itm is True
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)

    def test_certain_branch_day_zero(self):
        mgr = _manager(0)
        r = mgr.assess_risk(_CALL, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "CERTAIN"
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)

    def test_itm_low_branch(self):
        mgr = _manager(5)
        r = mgr.assess_risk(_CALL, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.is_itm is True
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)

    def test_otm_medium_near_expiry(self):
        mgr = _manager(2)
        r = mgr.assess_risk(_CALL, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.is_itm is False
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)
        assert "平仓止损" in r.recommended_action

    def test_otm_low_far_expiry(self):
        mgr = _manager(10)
        r = mgr.assess_risk(_CALL, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)
        assert r.moneyness == pytest.approx(round(2.7 / 3.0, 4))


class TestBuyerRiskPut:
    def test_expired_itm(self):
        mgr = _manager(-1, _PUT)
        r = mgr.assess_risk(_PUT, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "EXPIRED"
        assert r.option_type == "PUT"

    def test_certain_branch(self):
        mgr = _manager(1, _PUT)
        r = mgr.assess_risk(_PUT, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "CERTAIN"
        assert r.is_itm is True
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)

    def test_itm_low_branch(self):
        mgr = _manager(5, _PUT)
        r = mgr.assess_risk(_PUT, "BUY", 10, 2.7, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.is_itm is True
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)

    def test_otm_medium_near_expiry(self):
        mgr = _manager(2, _PUT)
        r = mgr.assess_risk(_PUT, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.is_itm is False
        assert r.moneyness == pytest.approx(round(3.0 / 3.3, 4))

    def test_otm_low_far_expiry(self):
        mgr = _manager(10, _PUT)
        r = mgr.assess_risk(_PUT, "BUY", 10, 3.3, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.potential_loss == pytest.approx(0.05 * 10 * 10000)


class TestSellerRiskCall:
    def test_expired(self):
        mgr = _manager(-1)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.3, 0.05)
        assert r.assignment_probability == "EXPIRED"
        assert r.potential_loss == 0
        assert r.side == "SELL"

    def test_certain_deep_itm_near_expiry(self):
        mgr = _manager(1)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.3, 0.05)
        assert r.assignment_probability == "CERTAIN"
        assert r.is_itm is True
        assert r.moneyness > 1.05
        assert r.potential_loss == pytest.approx((3.3 - 3.0) * 10 * 10000)
        assert "紧急" in r.recommended_action

    def test_high_itm_near_expiry(self):
        mgr = _manager(1)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.1, 0.05)
        assert r.assignment_probability == "HIGH"
        assert r.is_itm is True
        assert r.moneyness <= 1.05
        assert r.potential_loss == pytest.approx((3.1 - 3.0) * 10 * 10000)

    def test_medium_otm_near_expiry(self):
        mgr = _manager(1)
        r = mgr.assess_risk(_CALL, "SELL", 10, 2.9, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.is_itm is False
        assert r.potential_loss == 0

    def test_high_itm_warning_window(self):
        mgr = _manager(2)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.3, 0.05)
        assert r.assignment_probability == "HIGH"
        assert r.days_to_expiry == 2

    def test_medium_otm_warning_window(self):
        mgr = _manager(3)
        r = mgr.assess_risk(_CALL, "SELL", 10, 2.9, 0.05)
        assert r.assignment_probability == "MEDIUM"

    def test_medium_deep_itm_far_expiry(self):
        mgr = _manager(10)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.4, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.moneyness > 1.10
        assert "深度实值" in r.recommended_action

    def test_low_itm_far_expiry(self):
        mgr = _manager(10)
        r = mgr.assess_risk(_CALL, "SELL", 10, 3.1, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.is_itm is True
        assert r.moneyness <= 1.10

    def test_low_otm_far_expiry(self):
        mgr = _manager(10)
        r = mgr.assess_risk(_CALL, "SELL", 10, 2.9, 0.05)
        assert r.assignment_probability == "LOW"
        assert r.is_itm is False


class TestSellerRiskPut:
    def test_expired(self):
        mgr = _manager(-1, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 2.7, 0.05)
        assert r.assignment_probability == "EXPIRED"
        assert r.potential_loss == 0

    def test_certain_deep_itm_near_expiry(self):
        mgr = _manager(1, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 2.7, 0.05)
        assert r.assignment_probability == "CERTAIN"
        assert r.is_itm is True
        assert r.moneyness > 1.05
        assert r.potential_loss == pytest.approx((3.0 - 2.7) * 10 * 10000)

    def test_high_itm_near_expiry(self):
        mgr = _manager(1, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 2.9, 0.05)
        assert r.assignment_probability == "HIGH"
        assert r.potential_loss == pytest.approx((3.0 - 2.9) * 10 * 10000)

    def test_medium_otm_near_expiry(self):
        mgr = _manager(1, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 3.3, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.potential_loss == 0

    def test_medium_deep_itm_far_expiry(self):
        mgr = _manager(10, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 2.6, 0.05)
        assert r.assignment_probability == "MEDIUM"
        assert r.moneyness > 1.10

    def test_low_itm_far_expiry(self):
        mgr = _manager(10, _PUT)
        r = mgr.assess_risk(_PUT, "SELL", 10, 2.9, 0.05)
        assert r.assignment_probability == "LOW"


class TestCheckAll:
    def test_mix_valid_invalid_and_missing_price(self):
        mgr = _manager(2)
        positions = [
            {"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05},
            {"symbol": "garbage", "side": "SELL", "quantity": 10, "premium": 0.05},
            {"symbol": _PUT, "side": "BUY", "quantity": 5, "premium": 0.04},
            {"symbol": "159915C2507M03000.SH", "side": "SELL", "quantity": 3, "premium": 0.03},
        ]
        prices = {"510050.SH": 3.3}
        results = mgr.check_all(positions, prices)
        symbols = {r.symbol for r in results}
        assert _CALL in symbols
        assert _PUT in symbols
        assert "159915C2507M03000.SH" not in symbols

    def test_default_side_and_quantity(self):
        mgr = _manager(10)
        positions = [{"symbol": _CALL}]
        results = mgr.check_all(positions, {"510050.SH": 2.7})
        assert len(results) == 1
        assert results[0].side == "BUY"

    def test_sorted_by_risk_order(self):
        mgr = _manager(1)
        positions = [
            {"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05},
            {"symbol": _PUT, "side": "SELL", "quantity": 10, "premium": 0.05},
        ]
        prices = {"510050.SH": 3.3}
        results = mgr.check_all(positions, prices)
        assert results[0].assignment_probability == "CERTAIN"

    def test_empty_positions(self):
        mgr = _manager(10)
        assert mgr.check_all([], {"510050.SH": 3.0}) == []


class TestGenerateCloseOrders:
    def _make(self, prob, side, itm=True):
        return ExerciseRiskResult(
            symbol=_CALL, side=side, option_type="CALL", strike=3.0,
            underlying_price=3.3, days_to_expiry=1, moneyness=1.1,
            is_itm=itm, assignment_probability=prob,
            recommended_action="x", potential_loss=1000.0, message="m",
        )

    def test_sell_certain_order_high_urgency(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("CERTAIN", "SELL")
        orders = mgr.generate_close_orders([r], "HIGH")
        assert len(orders) == 1
        assert orders[0]["side"] == "BUY"
        assert orders[0]["offset"] == "CLOSE"
        assert orders[0]["urgency"] == "high"

    def test_sell_high_order_medium_urgency(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("HIGH", "SELL")
        orders = mgr.generate_close_orders([r], "HIGH")
        assert len(orders) == 1
        assert orders[0]["urgency"] == "medium"

    def test_buy_otm_close_order(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("MEDIUM", "BUY", itm=False)
        orders = mgr.generate_close_orders([r], "MEDIUM")
        assert len(orders) == 1
        assert orders[0]["side"] == "SELL"
        assert orders[0]["offset"] == "CLOSE"

    def test_buy_itm_no_order(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("HIGH", "BUY", itm=True)
        orders = mgr.generate_close_orders([r], "HIGH")
        assert orders == []

    def test_threshold_certain_filters_high(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("HIGH", "SELL")
        orders = mgr.generate_close_orders([r], "CERTAIN")
        assert orders == []

    def test_low_filtered_by_default(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("LOW", "SELL")
        orders = mgr.generate_close_orders([r], "HIGH")
        assert orders == []

    def test_invalid_threshold_defaults_to_high(self):
        mgr = OptionExerciseRiskManager()
        r = self._make("CERTAIN", "SELL")
        orders = mgr.generate_close_orders([r], "BOGUS")
        assert len(orders) == 1


class TestAutoCloseDeepItm:
    def test_qualifying_position_generates_order(self):
        mgr = _manager(3)
        positions = [
            {"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05},
        ]
        orders = mgr.auto_close_deep_itm(positions, {"510050.SH": 3.3})
        assert len(orders) == 1
        assert orders[0]["symbol"] == _CALL

    def test_buy_side_not_candidate(self):
        mgr = _manager(3)
        positions = [{"symbol": _CALL, "side": "BUY", "quantity": 10, "premium": 0.05}]
        assert mgr.auto_close_deep_itm(positions, {"510050.SH": 2.7}) == []

    def test_otm_not_candidate(self):
        mgr = _manager(3)
        positions = [{"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05}]
        assert mgr.auto_close_deep_itm(positions, {"510050.SH": 2.7}) == []

    def test_far_expiry_not_candidate(self):
        mgr = _manager(10)
        positions = [{"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05}]
        assert mgr.auto_close_deep_itm(positions, {"510050.SH": 3.3}, max_days=5) == []

    def test_shallow_itm_not_candidate(self):
        mgr = _manager(3)
        positions = [{"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05}]
        assert mgr.auto_close_deep_itm(positions, {"510050.SH": 3.1}, moneyness_threshold=1.05) == []

    def test_custom_threshold_allows_shallower(self):
        mgr = _manager(3)
        positions = [{"symbol": _CALL, "side": "SELL", "quantity": 10, "premium": 0.05}]
        orders = mgr.auto_close_deep_itm(positions, {"510050.SH": 3.1}, moneyness_threshold=1.02)
        assert len(orders) == 1