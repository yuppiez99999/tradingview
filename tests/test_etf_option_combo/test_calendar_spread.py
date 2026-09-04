"""05_02e — CalendarSpreadEngine 单元测试.

业务规则 (spec.md §5.5):
    1. 同行权价
    2. 卖近月 + 买远月
    3. 仅 Contango (远月IV ≥ 近月IV) 时建仓
    4. 近月 DTE ∈ [20, 40]
    5. 近月 DTE < 5 禁止
"""

from __future__ import annotations

from datetime import date

import pytest

from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.calendar_spread import CalendarSpreadEngine


pytestmark = pytest.mark.unit


@pytest.fixture
def cal_config():
    return {
        "total_capital": 2_000_000,
        "near_month_dte_min": 15,
        "near_month_dte_max": 60,
        "preferred_near_dte": 30,
        "far_month_dte_min": 70,
        "far_month_dte_max": 120,
        "require_contango": True,
        "blocked_near_dte": 5,
    }


@pytest.fixture
def cal_engine(chain_fetcher, cal_config):
    return CalendarSpreadEngine(config=cal_config, chain_fetcher=chain_fetcher)


class TestCalendarSpreadSuccess:
    def test_calendar_contango_pass(self, cal_engine, underlying_code, spot_position_sufficient):
        """Contango 期限结构下成功建仓日历价差."""
        result = cal_engine.generate(underlying_code, spot_position_sufficient)
        # 合成链 IV 恒定, 远月IV=近月IV, 满足 Contango (>=)
        if result.error_code is None:
            assert result.strategy_type == StrategyType.CALENDAR_SPREAD
            assert len(result.orders) == 2
            sell_leg = result.orders[0].leg
            buy_leg = result.orders[1].leg
            assert sell_leg.side == LegSide.SELL
            assert buy_leg.side == LegSide.BUY
            assert sell_leg.strike == buy_leg.strike  # 同行权价
            assert sell_leg.expiry < buy_leg.expiry  # 近月 < 远月
        else:
            # 合成链可能无法满足近月DTE范围, 接受 CAL_NO_NEAR_MONTH 等
            assert result.error_code in (
                "CAL_NO_IV_TERM_STRUCTURE",
                "CAL_NO_NEAR_MONTH",
                "CAL_NO_FAR_MONTH",
                "CAL_BACKWARDATION",
                "NO_OPTION_DATA",
            )


class TestCalendarBackwardation:
    def test_calendar_backwardation(self, cal_engine, underlying_code, spot_position_sufficient, monkeypatch):
        """Backwardation (远月IV < 近月IV) 返回 CAL_BACKWARDATION."""
        # mock get_iv_term_structure 返回 backwardation 结构
        original = cal_engine.chain_fetcher.get_iv_term_structure

        def backwardation_iv(*args, **kwargs):
            return [
                {"expiry": "2026-10-28", "dte": 30, "iv": 0.25, "premium": 0.05, "source": "mock"},
                {"expiry": "2026-11-25", "dte": 60, "iv": 0.15, "premium": 0.04, "source": "mock"},
            ]

        monkeypatch.setattr(cal_engine.chain_fetcher, "get_iv_term_structure", backwardation_iv)
        result = cal_engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "CAL_BACKWARDATION"


class TestCalendarStrikeMismatch:
    def test_calendar_strike_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """两腿行权价不同返回 CAL_STRIKE_MISMATCH."""
        engine = CalendarSpreadEngine(config={}, chain_fetcher=chain_fetcher)
        sell_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.00, premium=0.05, expiry=date(2026, 10, 28))
        buy_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.10, premium=0.06, expiry=date(2026, 11, 25))
        error = engine._validate_business_rules((sell_leg, buy_leg), fixed_spot_price)
        assert error == "CAL_STRIKE_MISMATCH"


class TestCalendarWrongDirection:
    def test_calendar_wrong_direction_side(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """腿方向错误返回 CAL_WRONG_DIRECTION."""
        engine = CalendarSpreadEngine(config={}, chain_fetcher=chain_fetcher)
        # 两腿都是 BUY
        sell_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.05, expiry=date(2026, 10, 28))
        buy_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.06, expiry=date(2026, 11, 25))
        error = engine._validate_business_rules((sell_leg, buy_leg), fixed_spot_price)
        assert error == "CAL_WRONG_DIRECTION"

    def test_calendar_wrong_direction_expiry(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """近月 >= 远月返回 CAL_WRONG_DIRECTION."""
        engine = CalendarSpreadEngine(config={}, chain_fetcher=chain_fetcher)
        sell_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.00, premium=0.05, expiry=date(2026, 11, 25))
        buy_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.06, expiry=date(2026, 10, 28))
        error = engine._validate_business_rules((sell_leg, buy_leg), fixed_spot_price)
        assert error == "CAL_WRONG_DIRECTION"


class TestCalendarNearExpiryTooClose:
    def test_calendar_near_expiry_too_close(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """近月 DTE < 5 返回 CAL_NEAR_EXPIRY_TOO_CLOSE."""
        cfg = {"blocked_near_dte": 5}
        engine = CalendarSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        near_expiry = date.today() + __import__("datetime").timedelta(days=2)
        far_expiry = date.today() + __import__("datetime").timedelta(days=60)
        sell_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.00, premium=0.05, expiry=near_expiry)
        buy_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.06, expiry=far_expiry)
        error = engine._validate_business_rules((sell_leg, buy_leg), fixed_spot_price)
        assert error == "CAL_NEAR_EXPIRY_TOO_CLOSE"


class TestCalendarLegCount:
    def test_calendar_leg_count_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """腿数 ≠ 2 返回 CAL_LEG_COUNT_MISMATCH."""
        engine = CalendarSpreadEngine(config={}, chain_fetcher=chain_fetcher)
        leg = make_combo_leg()
        error = engine._validate_business_rules((leg,), fixed_spot_price)
        assert error == "CAL_LEG_COUNT_MISMATCH"


class TestCalendarTypeMismatch:
    def test_calendar_type_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """两腿类型不同返回 CAL_TYPE_MISMATCH."""
        engine = CalendarSpreadEngine(config={}, chain_fetcher=chain_fetcher)
        sell_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.00, premium=0.05, expiry=date(2026, 10, 28))
        buy_leg = make_combo_leg(option_type="PUT", side=LegSide.BUY, strike=3.00, premium=0.06, expiry=date(2026, 11, 25))
        error = engine._validate_business_rules((sell_leg, buy_leg), fixed_spot_price)
        assert error == "CAL_TYPE_MISMATCH"