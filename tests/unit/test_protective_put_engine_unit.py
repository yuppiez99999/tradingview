"""test_protective_put_engine_unit.py — 认沽期权保护引擎单元测试

覆盖要点:
    - 类常量 (MIN_PORTFOLIO_VALUE / OTM_PCT / PROTECTION_TARGETS 4 ETF)
    - _estimate_put_premium (Black-Scholes, spot/strike/dte 边界)
    - _calc_next_expiry (第4个周三 / months_ahead=2 / 跨年)
    - should_buy_protection (市值不足/有有效put/预算用完/应买)
    - generate_put_orders (回撤加码 1.0/1.2/1.5/2.0 / 不执行 / 预算截断)
    - check_and_roll (无到期/有到期滚仓)
    - record_execution (更新 active_puts + ytd_spent)
    - get_protection_status (覆盖率/预算使用率/needs_action)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from utils.protective_put_engine import ProtectivePutEngine


@pytest.fixture(autouse=True)
def _no_io(monkeypatch):
    """避免读写真实文件"""
    monkeypatch.setattr(ProtectivePutEngine, "_save_state", lambda self: None)


# ============================================================
# 类常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_protection_targets_count(self):
        assert len(ProtectivePutEngine.PROTECTION_TARGETS) == 4

    @pytest.mark.unit
    def test_protection_target_codes(self):
        codes = [t["code"] for t in ProtectivePutEngine.PROTECTION_TARGETS]
        assert codes == ["510050", "588080", "159915", "510300"]

    @pytest.mark.unit
    def test_budget_pct_sums_to_one(self):
        total = sum(t["budget_pct"] for t in ProtectivePutEngine.PROTECTION_TARGETS)
        assert total == pytest.approx(1.0)

    @pytest.mark.unit
    def test_constants(self):
        assert ProtectivePutEngine.MIN_PORTFOLIO_VALUE == 1_000_000
        assert ProtectivePutEngine.OTM_PCT == 0.05
        assert ProtectivePutEngine.MAX_ANNUAL_COST_PCT == 0.025


# ============================================================
# _estimate_put_premium
# ============================================================


class TestEstimatePutPremium:
    @pytest.mark.unit
    def test_zero_spot_returns_zero(self):
        ppe = ProtectivePutEngine()
        assert ppe._estimate_put_premium(0, 100, 30) == 0

    @pytest.mark.unit
    def test_zero_strike_returns_zero(self):
        ppe = ProtectivePutEngine()
        assert ppe._estimate_put_premium(100, 0, 30) == 0

    @pytest.mark.unit
    def test_zero_dte_returns_zero(self):
        ppe = ProtectivePutEngine()
        assert ppe._estimate_put_premium(100, 95, 0) == 0

    @pytest.mark.unit
    def test_otm_put_positive_premium(self):
        """OTM put (strike < spot) 应有正权利金"""
        ppe = ProtectivePutEngine()
        premium = ppe._estimate_put_premium(spot=3.0, strike=2.85, dte=45, iv=0.25)
        assert premium > 0

    @pytest.mark.unit
    def test_itm_put_higher_than_otm(self):
        """ITM put 权利金 > OTM put 权利金"""
        ppe = ProtectivePutEngine()
        otm = ppe._estimate_put_premium(spot=3.0, strike=2.85, dte=45, iv=0.25)
        itm = ppe._estimate_put_premium(spot=3.0, strike=3.15, dte=45, iv=0.25)
        assert itm > otm

    @pytest.mark.unit
    def test_min_premium_floor(self):
        """最低价 0.0001"""
        ppe = ProtectivePutEngine()
        # 极度 OTM, 几乎无价值
        premium = ppe._estimate_put_premium(spot=100, strike=0.01, dte=1, iv=0.01)
        assert premium >= 0.0001


# ============================================================
# _calc_next_expiry
# ============================================================


class TestCalcNextExpiry:
    @pytest.mark.unit
    def test_returns_wednesday(self):
        ppe = ProtectivePutEngine()
        expiry = ppe._calc_next_expiry(months_ahead=1)
        assert expiry.weekday() == 2  # 周三

    @pytest.mark.unit
    def test_fourth_wednesday(self):
        """应是第4个周三"""
        ppe = ProtectivePutEngine()
        expiry = ppe._calc_next_expiry(months_ahead=1)
        # 计算该月有几个周三在它之前
        wed_count = 0
        for day in range(1, expiry.day):
            d = datetime(expiry.year, expiry.month, day)
            if d.weekday() == 2:
                wed_count += 1
        assert wed_count == 3  # 前面有3个, 它是第4个

    @pytest.mark.unit
    def test_months_ahead_2(self):
        ppe = ProtectivePutEngine()
        expiry = ppe._calc_next_expiry(months_ahead=2)
        assert expiry.weekday() == 2

    @pytest.mark.unit
    def test_future_date(self):
        """到期日应在未来"""
        ppe = ProtectivePutEngine()
        expiry = ppe._calc_next_expiry(months_ahead=1)
        assert expiry > datetime.now()


# ============================================================
# should_buy_protection
# ============================================================


class TestShouldBuyProtection:
    @pytest.mark.unit
    def test_portfolio_below_threshold(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 500_000)
        should, reason = ppe.should_buy_protection()
        assert should is False
        assert "100万" in reason or "1,000,000" in reason

    @pytest.mark.unit
    def test_has_valid_put(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        future_expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        ppe.state = {"active_puts": [{"expiry_date": future_expiry}]}

        should, reason = ppe.should_buy_protection()
        assert should is False
        assert "有效" in reason

    @pytest.mark.unit
    def test_expired_put_triggers_buy(self, monkeypatch):
        """过期 put (<=5天) 不算有效, 应买"""
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        past_expiry = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        ppe.state = {"active_puts": [{"expiry_date": past_expiry}]}

        should, reason = ppe.should_buy_protection()
        assert should is True

    @pytest.mark.unit
    def test_budget_exhausted(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 200_000}
        # annual_budget = 5_000_000 * 0.025 = 125_000 < 200_000

        should, reason = ppe.should_buy_protection()
        assert should is False
        assert "预算" in reason

    @pytest.mark.unit
    def test_should_buy(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        should, reason = ppe.should_buy_protection()
        assert should is True
        assert "建仓" in reason


# ============================================================
# generate_put_orders
# ============================================================


class TestGeneratePutOrders:
    @pytest.mark.unit
    def test_not_executing_returns_empty(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 500_000)

        result = ppe.generate_put_orders()
        assert result["should_execute"] is False
        assert result["orders"] == []

    @pytest.mark.unit
    def test_drawdown_level_0_multiplier_1(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders(drawdown_level=0)
        assert result["should_execute"] is True
        assert len(result["orders"]) == 4
        # 基础手数不变
        assert result["orders"][0]["contracts"] == 60  # 510050

    @pytest.mark.unit
    def test_drawdown_level_1_multiplier_12(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders(drawdown_level=1)
        # 60 * 1.2 = 72
        assert result["orders"][0]["contracts"] == 72

    @pytest.mark.unit
    def test_drawdown_level_2_multiplier_15(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders(drawdown_level=2)
        # 60 * 1.5 = 90
        assert result["orders"][0]["contracts"] == 90

    @pytest.mark.unit
    def test_drawdown_level_3_multiplier_20(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders(drawdown_level=3)
        # 60 * 2.0 = 120
        assert result["orders"][0]["contracts"] == 120

    @pytest.mark.unit
    def test_spot_zero_skips_target(self, monkeypatch):
        """现价=0 的 ETF 被跳过"""
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)

        def mock_spot(code):
            return 3.0 if code != "510050" else 0
        monkeypatch.setattr(ppe, "_get_etf_spot_price", mock_spot)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders()
        codes = [o["underlying"] for o in result["orders"]]
        assert "510050" not in codes
        assert len(codes) == 3

    @pytest.mark.unit
    def test_order_structure(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        result = ppe.generate_put_orders()
        order = result["orders"][0]
        assert order["type"] == "BUY_PUT"
        assert order["underlying"] == "510050"
        assert order["otm_pct"] == 0.05
        assert order["multiplier"] == 10000
        assert order["status"] == "PENDING"
        # OTM 5%: strike = 3.0 * 0.95 = 2.85
        assert order["strike"] == pytest.approx(2.85, abs=0.01)


# ============================================================
# check_and_roll
# ============================================================


class TestCheckAndRoll:
    @pytest.mark.unit
    def test_no_expiring_puts(self, monkeypatch):
        ppe = ProtectivePutEngine()
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        ppe.state = {"active_puts": [{"expiry_date": future, "underlying": "510050"}]}

        result = ppe.check_and_roll()
        assert result["needs_roll"] is False
        assert result["expiring_puts"] == []

    @pytest.mark.unit
    def test_expiring_put_triggers_roll(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        monkeypatch.setattr(ppe, "_get_etf_spot_price", lambda code: 3.0)
        soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        ppe.state = {
            "active_puts": [{"expiry_date": soon, "underlying": "510050", "contracts": 60}],
            "ytd_premium_spent": 0,
        }

        result = ppe.check_and_roll()
        assert result["needs_roll"] is True
        assert len(result["expiring_puts"]) == 1
        assert len(result["close_orders"]) == 1
        assert result["close_orders"][0]["type"] == "CLOSE_PUT"

    @pytest.mark.unit
    def test_empty_active_puts(self):
        ppe = ProtectivePutEngine()
        ppe.state = {"active_puts": []}

        result = ppe.check_and_roll()
        assert result["needs_roll"] is False


# ============================================================
# record_execution
# ============================================================


class TestRecordExecution:
    @pytest.mark.unit
    def test_records_filled_order(self):
        ppe = ProtectivePutEngine()
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        orders = [{
            "status": "FILLED",
            "underlying": "510050",
            "strike": 2.85,
            "contracts": 60,
            "expiry_date": "2026-09-24",
            "premium_total": 50000,
        }]
        ppe.record_execution(orders, actual_premium=50000)

        assert len(ppe.state["active_puts"]) == 1
        assert ppe.state["ytd_premium_spent"] == 50000

    @pytest.mark.unit
    def test_actual_premium_defaults_to_sum(self):
        ppe = ProtectivePutEngine()
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        orders = [
            {"status": "FILLED", "premium_total": 30000},
            {"status": "FILLED", "premium_total": 20000},
        ]
        ppe.record_execution(orders)
        assert ppe.state["ytd_premium_spent"] == 50000

    @pytest.mark.unit
    def test_pending_order_also_recorded(self):
        ppe = ProtectivePutEngine()
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        orders = [{"status": "PENDING", "premium_total": 10000}]
        ppe.record_execution(orders)
        assert len(ppe.state["active_puts"]) == 1


# ============================================================
# get_protection_status
# ============================================================


class TestGetProtectionStatus:
    @pytest.mark.unit
    def test_no_active_puts(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        status = ppe.get_protection_status()
        assert status["portfolio_value"] == 2_000_000
        assert status["protection_active"] is False
        assert status["active_put_groups"] == 0
        assert status["coverage_pct"] == 0
        assert status["needs_action"] is True

    @pytest.mark.unit
    def test_with_active_puts(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 2_000_000)
        ppe.state = {
            "active_puts": [{"strike": 2.85, "contracts": 60}],
            "ytd_premium_spent": 50000,
        }

        status = ppe.get_protection_status()
        assert status["protection_active"] is True
        assert status["active_put_groups"] == 1
        # notional = 2.85 * 60 * 10000 = 1_710_000
        assert status["notional_protected"] == pytest.approx(1_710_000)
        # coverage = 1_710_000 / 2_000_000 = 0.855
        assert status["coverage_pct"] == pytest.approx(0.855, abs=0.001)
        assert status["needs_action"] is False

    @pytest.mark.unit
    def test_zero_portfolio_value(self, monkeypatch):
        ppe = ProtectivePutEngine()
        monkeypatch.setattr(ppe, "_get_portfolio_value", lambda: 0)
        ppe.state = {"active_puts": [], "ytd_premium_spent": 0}

        status = ppe.get_protection_status()
        assert status["coverage_pct"] == 0
        assert status["needs_action"] is False  # 0 < 100万
