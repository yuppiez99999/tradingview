"""05_03 — ComboRiskManager 单元测试.

六重预检: Kill Switch -> 回撤分级 -> 保证金 -> 行权预警 -> Greeks -> 肥手指.
fail-closed: 异常时 approved=False.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils.etf_option_combo.combo_base import ComboLeg, LegSide, StrategyType
from utils.etf_option_combo.combo_risk_manager import ComboRiskManager


pytestmark = pytest.mark.unit


@pytest.fixture
def risk_mgr():
    return ComboRiskManager(total_capital=2_000_000, config={"fat_finger_limit": 500_000})


@pytest.fixture
def make_leg():
    def _make(
        instrument="OPTION",
        side=LegSide.SELL,
        strike=3.15,
        premium=0.05,
        quantity=1,
        multiplier=10000,
        expiry=None,
    ) -> ComboLeg:
        if expiry is None:
            expiry = date.today() + timedelta(days=45)
        return ComboLeg(
            instrument=instrument,
            underlying="510050.SH",
            option_type="CALL" if instrument == "OPTION" else None,
            side=side,
            strike=strike,
            expiry=expiry,
            quantity=quantity,
            multiplier=multiplier,
            premium=premium,
        )
    return _make


# ============================================================
# Kill Switch
# ============================================================

class TestKillSwitch:
    def test_kill_switch_block(self, risk_mgr, make_leg):
        """Kill Switch 触发且有新卖开仓时拒绝."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(legs, {"kill_switch_active": True})
        assert result["approved"] is False
        assert result["rejected_reason"] == "KILL_SWITCH_ACTIVE"
        assert "KILL_SWITCH" in result["risk_flags"]

    def test_kill_switch_allow_buy_only(self, risk_mgr, make_leg):
        """Kill Switch 触发但仅买入时放行."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"kill_switch_active": True})
        assert result["approved"] is True

    def test_kill_switch_inactive(self, risk_mgr, make_leg):
        """Kill Switch 未触发时放行."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(legs, {"kill_switch_active": False})
        assert result["approved"] is True


# ============================================================
# 回撤分级
# ============================================================

class TestDrawdown:
    def test_drawdown_l2_block_cc_csp(self, risk_mgr, make_leg):
        """L2 阻断 COVERED_CALL / CASH_SECURED_PUT."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(
            legs, {"drawdown_level": "L2", "strategy_type": StrategyType.COVERED_CALL},
        )
        assert result["approved"] is False
        assert result["rejected_reason"] == "DRAWDOWN_L2_BLOCKED"

    def test_drawdown_l2_block_csp(self, risk_mgr, make_leg):
        """L2 阻断 CSP."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(
            legs, {"drawdown_level": "L2", "strategy_type": StrategyType.CASH_SECURED_PUT},
        )
        assert result["approved"] is False
        assert result["rejected_reason"] == "DRAWDOWN_L2_BLOCKED"

    def test_drawdown_l2_allow_collar(self, risk_mgr, make_leg):
        """L2 放行 COLLAR (仅阻断 CC/CSP)."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(
            legs, {"drawdown_level": "L2", "strategy_type": StrategyType.COLLAR},
        )
        assert result["approved"] is True

    def test_drawdown_l2_allow_vs(self, risk_mgr, make_leg):
        """L2 放行 VERTICAL_SPREAD."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(
            legs, {"drawdown_level": "L2", "strategy_type": StrategyType.VERTICAL_SPREAD},
        )
        assert result["approved"] is True

    def test_drawdown_l3_block_all_new_sell(self, risk_mgr, make_leg):
        """L3 阻断所有新卖开仓."""
        legs = (make_leg(side=LegSide.SELL),)
        for st in StrategyType:
            result = risk_mgr.pre_check(
                legs, {"drawdown_level": "L3", "strategy_type": st},
            )
            assert result["approved"] is False
            assert result["rejected_reason"] == "DRAWDOWN_L3_BLOCKED"

    def test_drawdown_l3_allow_buy(self, risk_mgr, make_leg):
        """L3 放行买入 (仅平仓/开多)."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"drawdown_level": "L3"})
        assert result["approved"] is True

    def test_drawdown_l0_allow(self, risk_mgr, make_leg):
        """L0 放行."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(legs, {"drawdown_level": "L0"})
        assert result["approved"] is True


# ============================================================
# 保证金
# ============================================================

class TestMargin:
    def test_margin_exceeded(self, risk_mgr, make_leg):
        """保证金使用率超限返回 MARGIN_EXCEEDED."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(legs, {"margin_usage_pct": 0.25})  # > 0.20
        assert result["approved"] is False
        assert result["rejected_reason"] == "MARGIN_EXCEEDED"

    def test_margin_within_limit(self, risk_mgr, make_leg):
        """保证金使用率未超限放行."""
        legs = (make_leg(side=LegSide.SELL),)
        result = risk_mgr.pre_check(legs, {"margin_usage_pct": 0.15})
        assert result["approved"] is True

    def test_margin_insufficient_funds(self, make_leg):
        """可用资金 < 0 返回 MARGIN_INSUFFICIENT."""
        mgr = ComboRiskManager(
            total_capital=2_000_000,
            margin_monitor=object(),  # 触发 margin_monitor 分支
        )
        legs = (make_leg(side=LegSide.SELL),)
        result = mgr.pre_check(legs, {"available_funds": -1000})
        assert result["approved"] is False
        assert result["rejected_reason"] == "MARGIN_INSUFFICIENT"

    def test_margin_no_sell_legs_skip(self, risk_mgr, make_leg):
        """无卖腿时跳过保证金检查."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"margin_usage_pct": 0.99})
        assert result["approved"] is True


# ============================================================
# 行权预警
# ============================================================

class TestExerciseWarning:
    def test_exercise_warning_period(self, risk_mgr, make_leg):
        """卖腿 DTE ≤ 3 返回 EXERCISE_WARNING_PERIOD."""
        legs = (make_leg(side=LegSide.SELL, expiry=date.today() + timedelta(days=2)),)
        result = risk_mgr.pre_check(legs, {})
        assert result["approved"] is False
        assert result["rejected_reason"] == "EXERCISE_WARNING_PERIOD"

    def test_exercise_warning_safe_dte(self, risk_mgr, make_leg):
        """卖腿 DTE > 3 放行."""
        legs = (make_leg(side=LegSide.SELL, expiry=date.today() + timedelta(days=10)),)
        result = risk_mgr.pre_check(legs, {})
        assert result["approved"] is True

    def test_exercise_warning_skip_buy(self, risk_mgr, make_leg):
        """买腿不检查行权预警."""
        legs = (make_leg(side=LegSide.BUY, expiry=date.today() + timedelta(days=1)),)
        result = risk_mgr.pre_check(legs, {})
        assert result["approved"] is True


# ============================================================
# Greeks 检查
# ============================================================

class TestGreeksCheck:
    def test_delta_severe_drift(self, risk_mgr, make_leg):
        """|delta| > severe 阈值标记 DELTA_SEVERE_DRIFT."""
        legs = (make_leg(side=LegSide.BUY),)  # 买入不触发 kill/drawdown/margin
        result = risk_mgr.pre_check(legs, {"portfolio_delta": 0.6})  # > 0.5
        assert result["approved"] is True  # 仅标记不拒绝
        assert "DELTA_SEVERE_DRIFT" in result["risk_flags"]

    def test_delta_rebalance_needed(self, risk_mgr, make_leg):
        """|delta| > rebalance 阈值标记 DELTA_REBALANCE_NEEDED."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"portfolio_delta": 0.35})  # > 0.3, < 0.5
        assert "DELTA_REBALANCE_NEEDED" in result["risk_flags"]

    def test_vega_exceed_dynamic_limit(self, risk_mgr, make_leg):
        """|vega| > max_vega_limit 标记 VEGA_EXCEEDED."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"portfolio_vega": 200_000, "max_vega_limit": 100_000})
        assert "VEGA_EXCEEDED" in result["risk_flags"]

    def test_greeks_within_limit(self, risk_mgr, make_leg):
        """Greeks 在限内无标记."""
        legs = (make_leg(side=LegSide.BUY),)
        result = risk_mgr.pre_check(legs, {"portfolio_delta": 0.1, "portfolio_vega": 1000})
        assert result["approved"] is True
        assert "DELTA_SEVERE_DRIFT" not in result["risk_flags"]


# ============================================================
# 肥手指
# ============================================================

class TestFatFinger:
    def test_fat_finger_option(self, make_leg):
        """期权订单金额 > 50万 标记 FAT_FINGER + requires_confirmation."""
        mgr = ComboRiskManager(config={"fat_finger_limit": 500_000})
        # premium=0.05, qty=1, multiplier=10000 -> 500, 不触发
        # premium=0.06, qty=10, multiplier=10000 -> 6000, 不触发
        # 需要 > 500000: premium=0.05, qty=11, multiplier=10000 -> 5500 不够
        # 用大 premium: premium=60, qty=1, multiplier=10000 -> 600000 > 500000
        legs = (make_leg(side=LegSide.BUY, premium=60.0, quantity=1),)
        result = mgr.pre_check(legs, {})
        assert "FAT_FINGER" in result["risk_flags"]
        assert result["requires_confirmation"] is True

    def test_fat_finger_spot(self, make_leg):
        """现货订单金额 > 50万 标记 FAT_FINGER."""
        mgr = ComboRiskManager(config={"fat_finger_limit": 500_000})
        # spot: strike * quantity = 3.0 * 200000 = 600000 > 500000
        legs = (make_leg(instrument="SPOT", side=LegSide.BUY, strike=3.0, quantity=200_000, multiplier=1),)
        result = mgr.pre_check(legs, {})
        assert "FAT_FINGER" in result["risk_flags"]

    def test_no_fat_finger(self, risk_mgr, make_leg):
        """正常金额不触发肥手指."""
        legs = (make_leg(side=LegSide.BUY, premium=0.05, quantity=1),)
        result = risk_mgr.pre_check(legs, {})
        assert "FAT_FINGER" not in result["risk_flags"]
        assert result["requires_confirmation"] is False


# ============================================================
# fail-closed
# ============================================================

class TestFailClosed:
    def test_risk_fail_closed_on_exception(self, make_leg, monkeypatch):
        """风控检查异常时 fail-closed (approved=False)."""
        mgr = ComboRiskManager()

        # 让 _check_kill_switch 抛异常
        def boom(legs, state):
            raise RuntimeError("CHECK_BOMB")

        monkeypatch.setattr(mgr, "_check_kill_switch", boom)
        legs = (make_leg(side=LegSide.SELL),)
        result = mgr.pre_check(legs, {})
        assert result["approved"] is False
        assert "RISK_CHECK_ERROR" in result["rejected_reason"]
        assert "ERROR" in result["risk_flags"]


# ============================================================
# 预算检查 & 风控状态
# ============================================================

class TestBudgetAndState:
    def test_check_budget_pass(self, risk_mgr):
        """check_budget 默认通过."""
        ok, err = risk_mgr.check_budget("covered_call", 1000.0)
        assert ok is True
        assert err == ""

    def test_get_risk_state_default(self, risk_mgr):
        """默认风控状态 L0 + kill_switch=False."""
        state = risk_mgr.get_risk_state()
        assert state["drawdown_level"] == "L0"
        assert state["kill_switch_active"] is False

    def test_update_risk_state(self, risk_mgr):
        """update_risk_state 更新状态."""
        risk_mgr.update_risk_state(drawdown_level="L2", kill_switch_active=True)
        state = risk_mgr.get_risk_state()
        assert state["drawdown_level"] == "L2"
        assert state["kill_switch_active"] is True

    def test_monitor_returns_dict(self, risk_mgr):
        """monitor 返回结构化字典."""
        result = risk_mgr.monitor([], {})
        assert "margin_checks" in result
        assert "exercise_risks" in result
        assert "greeks_alerts" in result
        assert "liquidation_orders" in result

    def test_generate_rebalance_orders(self, risk_mgr):
        """generate_rebalance_orders 返回列表."""

        class FakeGreeks:
            delta = 0.6

        orders = risk_mgr.generate_rebalance_orders(FakeGreeks(), target_delta=0.0)
        assert isinstance(orders, list)