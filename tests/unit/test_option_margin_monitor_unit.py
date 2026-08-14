# -*- coding: utf-8 -*-
"""option_margin_monitor 单元测试 — 期权卖方保证金监控全分支覆盖

被测模块: utils/option_margin_monitor.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.option_margin_monitor import (  # noqa: E402
    MarginCheckResult,
    OptionMarginMonitor,
    OptionPosition,
    calc_call_margin,
    calc_margin,
    calc_put_margin,
    parse_option_code,
)


def _far_future_date() -> str:
    return (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")


def _near_expiry_date(days: int = 2) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _expired_date() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


class TestParseOptionCode:
    def test_valid_call(self):
        r = parse_option_code("510050C2507M03000.SH")
        assert r is not None
        assert r["underlying_prefix"] == "510"
        assert r["option_type"] == "CALL"
        assert r["year"] == 2025
        assert r["month"] == 7
        assert r["strike"] == 3.0
        assert r["exchange"] == "SH"
        assert r["full_underlying"] == "510050.SH"

    def test_valid_put(self):
        r = parse_option_code("510050P2512M02500.SZ")
        assert r is not None
        assert r["option_type"] == "PUT"
        assert r["year"] == 2025
        assert r["month"] == 12
        assert r["strike"] == 2.5
        assert r["exchange"] == "SZ"
        assert r["full_underlying"] == "510050.SZ"

    def test_invalid_format_returns_none(self):
        assert parse_option_code("invalid") is None

    def test_invalid_exchange_returns_none(self):
        assert parse_option_code("510050C2507M03000.BJ") is None

    def test_missing_dot_returns_none(self):
        assert parse_option_code("510050C2507M03000SH") is None

    def test_wrong_length_prefix_returns_none(self):
        assert parse_option_code("51005C2507M03000.SH") is None


class TestCalcCallMargin:
    def test_atm(self):
        m = calc_call_margin(underlying_price=3.0, strike=3.0, premium=0.05)
        uv = 3.0 * 10000
        otm = 0
        risk = max(uv * 0.15 - otm, uv * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_itm(self):
        m = calc_call_margin(underlying_price=3.5, strike=3.0, premium=0.05)
        uv = 3.5 * 10000
        otm = 0
        risk = max(uv * 0.15 - otm, uv * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_otm(self):
        m = calc_call_margin(underlying_price=2.5, strike=3.0, premium=0.05)
        uv = 2.5 * 10000
        otm = (3.0 - 2.5) * 10000
        risk = max(uv * 0.15 - otm, uv * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_custom_multiplier(self):
        m = calc_call_margin(3.0, 3.0, 0.05, contract_multiplier=5000)
        uv = 3.0 * 5000
        risk = max(uv * 0.15, uv * 0.07)
        assert m == pytest.approx(0.05 + risk)


class TestCalcPutMargin:
    def test_atm(self):
        m = calc_put_margin(3.0, 3.0, 0.05)
        uv = 3.0 * 10000
        otm = 0
        risk = max(uv * 0.15 - otm, 3.0 * 10000 * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_itm(self):
        m = calc_put_margin(2.5, 3.0, 0.05)
        uv = 2.5 * 10000
        otm = 0
        risk = max(uv * 0.15 - otm, 3.0 * 10000 * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_otm(self):
        m = calc_put_margin(3.5, 3.0, 0.05)
        uv = 3.5 * 10000
        otm = (3.5 - 3.0) * 10000
        risk = max(uv * 0.15 - otm, 3.0 * 10000 * 0.07)
        assert m == pytest.approx(0.05 + risk)

    def test_custom_multiplier(self):
        m = calc_put_margin(3.0, 3.0, 0.05, contract_multiplier=5000)
        uv = 3.0 * 5000
        risk = max(uv * 0.15, 3.0 * 5000 * 0.07)
        assert m == pytest.approx(0.05 + risk)


class TestCalcMargin:
    def test_call_dispatch(self):
        m = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=30)
        assert m == pytest.approx(calc_call_margin(3.0, 3.0, 0.05))

    def test_put_dispatch(self):
        m = calc_margin("PUT", 3.0, 3.0, 0.05, 1, days_to_expiry=30)
        assert m == pytest.approx(calc_put_margin(3.0, 3.0, 0.05))

    def test_near_expiry_surcharge(self):
        m_far = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=10)
        m_near = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=3)
        assert m_near == pytest.approx(m_far * 1.2)

    def test_near_expiry_zero_days(self):
        m = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=0)
        base = calc_call_margin(3.0, 3.0, 0.05)
        assert m == pytest.approx(base * 1.2)

    def test_near_expiry_negative_days(self):
        m = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=-1)
        base = calc_call_margin(3.0, 3.0, 0.05)
        assert m == pytest.approx(base * 1.2)

    def test_quantity_scaling(self):
        m1 = calc_margin("CALL", 3.0, 3.0, 0.05, 1, days_to_expiry=30)
        m10 = calc_margin("CALL", 3.0, 3.0, 0.05, 10, days_to_expiry=30)
        assert m10 == pytest.approx(m1 * 10)

    def test_put_near_expiry_with_quantity(self):
        m = calc_margin("PUT", 3.0, 3.0, 0.05, 2, days_to_expiry=2)
        base = calc_put_margin(3.0, 3.0, 0.05)
        assert m == pytest.approx(base * 1.2 * 2)

    def test_unknown_type_dispatches_to_put(self):
        m = calc_margin("UNKNOWN", 3.0, 3.0, 0.05, 1, days_to_expiry=30)
        assert m == pytest.approx(calc_put_margin(3.0, 3.0, 0.05))


class TestOptionMarginMonitorPositions:
    def test_add_and_get_positions(self):
        mon = OptionMarginMonitor()
        p = OptionPosition(
            symbol="S1", underlying="U1", side="SELL",
            strike=3.0, quantity=1, premium=0.05,
            expiry_date=_far_future_date(),
        )
        mon.add_position(p)
        assert len(mon.get_positions()) == 1
        assert mon.get_positions()[0].symbol == "S1"

    def test_add_overwrite(self):
        mon = OptionMarginMonitor()
        mon.add_position(OptionPosition(symbol="S1", underlying="U1", quantity=1))
        mon.add_position(OptionPosition(symbol="S1", underlying="U1", quantity=5))
        assert len(mon.get_positions()) == 1
        assert mon.get_positions()[0].quantity == 5

    def test_remove_position(self):
        mon = OptionMarginMonitor()
        mon.add_position(OptionPosition(symbol="S1", underlying="U1"))
        mon.remove_position("S1")
        assert mon.get_positions() == []

    def test_remove_nonexistent(self):
        mon = OptionMarginMonitor()
        mon.remove_position("nonexistent")
        assert mon.get_positions() == []

    def test_get_status(self):
        mon = OptionMarginMonitor(
            warning_ratio=0.7, danger_ratio=0.9, expiry_warning_days=5,
        )
        mon.add_position(OptionPosition(symbol="S1", underlying="U1"))
        status = mon.get_status()
        assert status["positions_count"] == 1
        assert status["warning_ratio"] == 0.7
        assert status["danger_ratio"] == 0.9
        assert status["expiry_warning_days"] == 5

    def test_get_status_empty(self):
        mon = OptionMarginMonitor()
        status = mon.get_status()
        assert status["positions_count"] == 0
        assert status["warning_ratio"] == 0.80
        assert status["danger_ratio"] == 0.95


def _make_sell(
    symbol="S1", underlying="U1", option_type="CALL",
    strike=3.0, quantity=1, premium=0.05, expiry=None,
):
    return OptionPosition(
        symbol=symbol, underlying=underlying, option_type=option_type,
        side="SELL", strike=strike, quantity=quantity, premium=premium,
        expiry_date=expiry or _far_future_date(),
    )


class TestCheckAll:
    def test_empty_positions(self):
        mon = OptionMarginMonitor()
        assert mon.check_all({"U1": 3.0}, 100000) == []

    def test_buy_side_skipped(self):
        mon = OptionMarginMonitor()
        mon.add_position(OptionPosition(
            symbol="S1", underlying="U1", side="BUY",
            strike=3.0, quantity=1, premium=0.05,
            expiry_date=_far_future_date(),
        ))
        assert mon.check_all({"U1": 3.0}, 100000) == []

    def test_missing_underlying_price_skipped(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        assert mon.check_all({"OTHER": 3.0}, 100000) == []

    def test_zero_underlying_price_skipped(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        assert mon.check_all({"U1": 0.0}, 100000) == []

    def test_negative_underlying_price_skipped(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        assert mon.check_all({"U1": -1.0}, 100000) == []

    def test_ok_level(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].warning_level == "OK"
        assert results[0].is_sufficient is True
        assert results[0].message == ""

    def test_warning_level_ratio(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        results = mon.check_all({"U1": 3.0}, 5000)
        assert len(results) == 1
        assert results[0].warning_level == "WARNING"
        assert results[0].margin_ratio >= 0.80
        assert "保证金预警" in results[0].message

    def test_danger_level_ratio(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        results = mon.check_all({"U1": 3.0}, 4500)
        assert len(results) == 1
        assert results[0].warning_level == "DANGER"
        assert results[0].margin_ratio >= 0.95
        assert results[0].is_sufficient is False
        assert "保证金危险" in results[0].message

    def test_warning_level_near_expiry(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(expiry=_near_expiry_date(2)))
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].warning_level == "WARNING"
        assert results[0].days_to_expiry == 2
        assert "临近行权日" in results[0].message

    def test_available_funds_zero_ratio_inf(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell())
        results = mon.check_all({"U1": 3.0}, 0)
        assert len(results) == 1
        assert results[0].margin_ratio == float("inf")
        assert results[0].warning_level == "DANGER"
        assert results[0].is_sufficient is False

    def test_invalid_expiry_date(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(expiry="not-a-date"))
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].days_to_expiry == 999
        assert results[0].warning_level == "OK"

    def test_empty_expiry_date(self):
        mon = OptionMarginMonitor()
        mon.add_position(OptionPosition(
            symbol="S1", underlying="U1", side="SELL",
            strike=3.0, quantity=1, premium=0.05, expiry_date="",
        ))
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].days_to_expiry == 999

    def test_put_option_ok(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(option_type="PUT"))
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].warning_level == "OK"

    def test_multiple_positions_mixed(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(symbol="S1"))
        mon.add_position(OptionPosition(
            symbol="S2", underlying="U1", side="BUY",
            strike=3.0, quantity=1, premium=0.05,
            expiry_date=_far_future_date(),
        ))
        mon.add_position(_make_sell(symbol="S3", underlying="U2"))
        results = mon.check_all({"U1": 3.0, "U2": 3.0}, 1000000)
        assert len(results) == 2
        assert {r.symbol for r in results} == {"S1", "S3"}


class TestLiquidationAdvice:
    def test_danger_force_close(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=50,
            is_sufficient=False, margin_ratio=1.5, days_to_expiry=30,
            warning_level="DANGER", message="danger",
        )]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "FORCE_CLOSE"
        assert advice[0]["symbol"] == "S1"
        assert "required_margin" in advice[0]
        assert "available_funds" in advice[0]

    def test_expired_force_close(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=1000,
            is_sufficient=True, margin_ratio=0.1, days_to_expiry=0,
            warning_level="WARNING", message="expiry",
        )]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "FORCE_CLOSE"

    def test_negative_days_force_close(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=1000,
            is_sufficient=True, margin_ratio=0.1, days_to_expiry=-5,
            warning_level="WARNING", message="expired",
        )]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "FORCE_CLOSE"

    def test_near_expiry_warn_close(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=1000,
            is_sufficient=True, margin_ratio=0.1, days_to_expiry=2,
            warning_level="WARNING", message="near",
        )]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "WARN_CLOSE"

    def test_ok_no_advice(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=1000,
            is_sufficient=True, margin_ratio=0.1, days_to_expiry=30,
            warning_level="OK", message="",
        )]
        assert mon.get_liquidation_advice(results) == []

    def test_empty_results(self):
        mon = OptionMarginMonitor()
        assert mon.get_liquidation_advice([]) == []

    def test_mixed_results(self):
        mon = OptionMarginMonitor()
        results = [
            MarginCheckResult(
                symbol="S1", required_margin=100, available_funds=50,
                is_sufficient=False, margin_ratio=1.5, days_to_expiry=30,
                warning_level="DANGER",
            ),
            MarginCheckResult(
                symbol="S2", required_margin=100, available_funds=1000,
                is_sufficient=True, margin_ratio=0.1, days_to_expiry=30,
                warning_level="OK",
            ),
            MarginCheckResult(
                symbol="S3", required_margin=100, available_funds=1000,
                is_sufficient=True, margin_ratio=0.1, days_to_expiry=1,
                warning_level="WARNING",
            ),
        ]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 2
        actions = {a["symbol"]: a["action"] for a in advice}
        assert actions["S1"] == "FORCE_CLOSE"
        assert actions["S3"] == "WARN_CLOSE"

    def test_danger_takes_precedence_over_expiry(self):
        mon = OptionMarginMonitor()
        results = [MarginCheckResult(
            symbol="S1", required_margin=100, available_funds=50,
            is_sufficient=False, margin_ratio=1.5, days_to_expiry=0,
            warning_level="DANGER",
        )]
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "FORCE_CLOSE"


class TestEndToEnd:
    def test_full_monitoring_cycle(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(symbol="OK_POS", underlying="U1"))
        mon.add_position(_make_sell(symbol="DANGER_POS", underlying="U2"))
        results = mon.check_all(
            {"U1": 3.0, "U2": 3.0}, available_funds=4500,
        )
        advice = mon.get_liquidation_advice(results)
        danger_advice = [a for a in advice if a["symbol"] == "DANGER_POS"]
        assert len(danger_advice) == 1
        assert danger_advice[0]["action"] == "FORCE_CLOSE"

    def test_expired_position_end_to_end(self):
        mon = OptionMarginMonitor()
        mon.add_position(_make_sell(symbol="EXP", expiry=_expired_date()))
        results = mon.check_all({"U1": 3.0}, 1000000)
        assert len(results) == 1
        assert results[0].days_to_expiry < 0
        advice = mon.get_liquidation_advice(results)
        assert len(advice) == 1
        assert advice[0]["action"] == "FORCE_CLOSE"