"""test_wt_risk_control_unit.py — WonderTrader风格风控模块单元测试

覆盖要点:
    - _load_cvar_config (默认/文件)
    - RiskControl (构造/reset_daily/update_equity/check_circuit_breaker/check_position_concentration
      /check_single_trade/check_daily_trade_count/check_daily_volume/record_trade/get_risk_status/pre_trade_check)
    - StopLossManager (set/check/update/remove/get_status)
    - PortfolioRiskAnalyzer (_normalize_positions/calculate_var/calculate_cvar/calculate_position_concentration
      /analyze_sector_distribution/analyze_portfolio)
    - RiskReportGenerator.generate_risk_report
    - create_risk_control / create_stop_loss_manager
"""

from __future__ import annotations

import pytest

from utils.wt_risk_control import (
    _CVAR_CONFIG_DEFAULT,
    PortfolioRiskAnalyzer,
    RiskControl,
    RiskReportGenerator,
    StopLossManager,
    _load_cvar_config,
    create_risk_control,
    create_stop_loss_manager,
)

# ============================================================
# _load_cvar_config
# ============================================================


class TestLoadCvarConfig:
    @pytest.mark.unit
    def test_default(self):
        cfg = _load_cvar_config()
        assert cfg["method"] == "monte_carlo"
        assert cfg["distribution"] == "student_t"
        assert cfg["dof"] == 5

    @pytest.mark.unit
    def test_default_constant(self):
        assert _CVAR_CONFIG_DEFAULT["confidence_level"] == 0.95
        assert _CVAR_CONFIG_DEFAULT["n_paths"] == 50000


# ============================================================
# RiskControl
# ============================================================


class TestRiskControl:
    @pytest.mark.unit
    def test_default_config(self):
        rc = RiskControl()
        assert rc.config["max_daily_loss_pct"] == 0.03
        assert rc.trading_enabled is True
        assert rc.circuit_breaker_tripped is False

    @pytest.mark.unit
    def test_custom_config(self):
        rc = RiskControl(
            config={
                "max_daily_loss_pct": 0.05,
                "max_portfolio_drawdown_pct": 0.10,
                "max_position_concentration_pct": 0.2,
                "max_single_trade_pct": 0.05,
                "max_daily_trades": 50,
                "max_daily_volume": 500000000,
                "circuit_breaker_enabled": True,
                "stop_loss_enabled": True,
                "position_limit_enabled": True,
            }
        )
        assert rc.config["max_daily_loss_pct"] == 0.05

    @pytest.mark.unit
    def test_reset_daily(self):
        rc = RiskControl()
        rc.daily_trades = 10
        rc.daily_volume = 1000
        rc.reset_daily()
        assert rc.daily_trades == 0
        assert rc.daily_volume == 0.0

    @pytest.mark.unit
    def test_update_equity(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        assert rc.current_equity == 1_000_000
        assert rc.max_equity == 1_000_000
        rc.update_equity(900_000)
        assert rc.max_equity == 1_000_000

    @pytest.mark.unit
    def test_circuit_breaker_normal(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        ok, reason = rc.check_circuit_breaker()
        assert ok is True

    @pytest.mark.unit
    def test_circuit_breaker_drawdown(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        rc.update_equity(900_000)  # 10% drawdown > 5%
        ok, reason = rc.check_circuit_breaker()
        assert ok is False
        assert "回撤" in reason

    @pytest.mark.unit
    def test_circuit_breaker_tripped(self):
        rc = RiskControl()
        rc.circuit_breaker_tripped = True
        rc.circuit_breaker_reason = "test"
        ok, _ = rc.check_circuit_breaker()
        assert ok is False

    @pytest.mark.unit
    def test_position_concentration_ok(self):
        rc = RiskControl()
        ok, _ = rc.check_position_concentration("000001", 20000, 100000)
        assert ok is True

    @pytest.mark.unit
    def test_position_concentration_exceed(self):
        rc = RiskControl()
        ok, reason = rc.check_position_concentration("000001", 40000, 100000)
        assert ok is False

    @pytest.mark.unit
    def test_single_trade_ok(self):
        rc = RiskControl()
        ok, _ = rc.check_single_trade(5000, 100000)
        assert ok is True

    @pytest.mark.unit
    def test_single_trade_exceed(self):
        rc = RiskControl()
        ok, _ = rc.check_single_trade(15000, 100000)
        assert ok is False

    @pytest.mark.unit
    def test_daily_trade_count_ok(self):
        rc = RiskControl()
        rc.daily_trades = 10
        ok, _ = rc.check_daily_trade_count()
        assert ok is True

    @pytest.mark.unit
    def test_daily_trade_count_exceed(self):
        rc = RiskControl()
        rc.daily_trades = 100
        ok, _ = rc.check_daily_trade_count()
        assert ok is False

    @pytest.mark.unit
    def test_record_trade(self):
        rc = RiskControl()
        rc.record_trade(10000, 1000, pnl=-500)
        assert rc.daily_trades == 1
        assert rc.daily_volume == 1000
        assert rc.daily_pnl == -500
        assert rc.daily_loss == 500

    @pytest.mark.unit
    def test_get_risk_status(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        status = rc.get_risk_status()
        assert "trading_enabled" in status
        assert "current_drawdown" in status

    @pytest.mark.unit
    def test_pre_trade_check_pass(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        ok, reasons = rc.pre_trade_check("000001", 5000, 100, 20000, 100000)
        assert ok is True
        assert reasons == []

    @pytest.mark.unit
    def test_pre_trade_check_fail(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        ok, reasons = rc.pre_trade_check("000001", 50000, 100, 20000, 100000)
        assert ok is False
        assert len(reasons) > 0


# ============================================================
# StopLossManager
# ============================================================


class TestStopLossManager:
    @pytest.mark.unit
    def test_set_stop_loss(self):
        m = StopLossManager(stop_loss_pct=0.05, take_profit_pct=0.10)
        m.set_stop_loss("000001", 10.0, 1000)
        order = m.stop_loss_orders["000001"]
        assert order["stop_price"] == pytest.approx(9.5)
        assert order["take_profit_price"] == pytest.approx(11.0)
        assert order["status"] == "active"

    @pytest.mark.unit
    def test_check_stop_loss_none(self):
        m = StopLossManager()
        action, order = m.check_stop_loss("999999", 10.0)
        assert action == "none"
        assert order is None

    @pytest.mark.unit
    def test_check_stop_loss_trigger(self):
        m = StopLossManager(stop_loss_pct=0.05)
        m.set_stop_loss("000001", 10.0, 1000)
        action, order = m.check_stop_loss("000001", 9.0)
        assert action == "stop_loss"
        assert order["status"] == "triggered_stop_loss"

    @pytest.mark.unit
    def test_check_take_profit_trigger(self):
        m = StopLossManager(take_profit_pct=0.10)
        m.set_stop_loss("000001", 10.0, 1000)
        action, order = m.check_stop_loss("000001", 12.0)
        assert action == "take_profit"

    @pytest.mark.unit
    def test_update_stop_loss(self):
        m = StopLossManager(stop_loss_pct=0.05)
        m.set_stop_loss("000001", 10.0, 1000)
        m.update_stop_loss("000001", 12.0)
        assert m.stop_loss_orders["000001"]["stop_price"] == pytest.approx(11.4)

    @pytest.mark.unit
    def test_remove_stop_loss(self):
        m = StopLossManager()
        m.set_stop_loss("000001", 10.0, 1000)
        m.remove_stop_loss("000001")
        assert "000001" not in m.stop_loss_orders


# ============================================================
# PortfolioRiskAnalyzer
# ============================================================


class TestPortfolioRiskAnalyzer:
    @pytest.mark.unit
    def test_normalize_positions(self):
        positions = {
            "000001": {"qty": 100, "avg_cost": 10.0},
            "000002": {"shares": 200, "avg_cost": 20.0},
        }
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        assert normalized["000001"]["qty"] == 100
        assert normalized["000002"]["qty"] == 200

    @pytest.mark.unit
    def test_calculate_var(self):
        positions = {"000001": {"qty": 100, "avg_cost": 10.0}}
        var = PortfolioRiskAnalyzer.calculate_var(
            positions, volatility=0.02, confidence_level=0.95
        )
        # 100*10 * 0.02 * 1.645 = 32.9
        assert var == pytest.approx(32.9, abs=0.1)

    @pytest.mark.unit
    def test_calculate_var_99(self):
        positions = {"000001": {"qty": 100, "avg_cost": 10.0}}
        var = PortfolioRiskAnalyzer.calculate_var(
            positions, volatility=0.02, confidence_level=0.99
        )
        assert var > 0

    @pytest.mark.unit
    def test_calculate_cvar_analytic(self):
        positions = {"000001": {"qty": 100, "avg_cost": 10.0}}
        cvar = PortfolioRiskAnalyzer.calculate_cvar(positions, method="analytic")
        assert cvar > 0

    @pytest.mark.unit
    def test_calculate_cvar_monte_carlo_normal(self):
        positions = {"000001": {"qty": 100, "avg_cost": 10.0}}
        cvar = PortfolioRiskAnalyzer.calculate_cvar(
            positions, method="monte_carlo", n_paths=10000, dist="normal"
        )
        assert cvar > 0

    @pytest.mark.unit
    def test_calculate_cvar_monte_carlo_student_t(self):
        positions = {"000001": {"qty": 100, "avg_cost": 10.0}}
        cvar = PortfolioRiskAnalyzer.calculate_cvar(
            positions, method="monte_carlo", n_paths=10000, dist="student_t", dof=5
        )
        assert cvar > 0

    @pytest.mark.unit
    def test_position_concentration(self):
        positions = {
            "000001": {"qty": 100, "avg_cost": 10.0},
            "000002": {"qty": 50, "avg_cost": 20.0},
        }
        conc = PortfolioRiskAnalyzer.calculate_position_concentration(positions)
        assert "000001" in conc
        assert conc["000001"]["percentage"] == pytest.approx(0.5)

    @pytest.mark.unit
    def test_position_concentration_empty(self):
        conc = PortfolioRiskAnalyzer.calculate_position_concentration({})
        assert conc == {}

    @pytest.mark.unit
    def test_analyze_sector_distribution(self):
        positions = {
            "000001": {"qty": 100, "avg_cost": 10.0},
            "000002": {"qty": 50, "avg_cost": 20.0},
        }
        sector_map = {"000001": "银行", "000002": "地产"}
        sectors = PortfolioRiskAnalyzer.analyze_sector_distribution(
            positions, sector_map
        )
        assert "银行" in sectors
        assert "地产" in sectors

    @pytest.mark.unit
    def test_analyze_portfolio(self):
        positions = {
            "000001": {"qty": 100, "avg_cost": 10.0},
            "000002": {"qty": 50, "avg_cost": 20.0},
        }
        analyzer = PortfolioRiskAnalyzer()
        result = analyzer.analyze_portfolio(positions, total_built=1500, target=2000)
        assert "risk_score" in result
        assert "var_95" in result
        assert "cvar_95" in result
        assert 0 <= result["risk_score"] <= 100


# ============================================================
# RiskReportGenerator
# ============================================================


class TestRiskReportGenerator:
    @pytest.mark.unit
    def test_generate_report(self):
        rc = RiskControl()
        rc.update_equity(1_000_000)
        slm = StopLossManager()
        slm.set_stop_loss("000001", 10.0, 1000)
        positions = {"000001": {"qty": 1000, "avg_cost": 10.0}}
        report = RiskReportGenerator.generate_risk_report(rc, slm, positions)
        assert "风险监控报告" in report
        assert "交易状态" in report


# ============================================================
# 工厂函数
# ============================================================


class TestFactory:
    @pytest.mark.unit
    def test_create_risk_control(self):
        rc = create_risk_control()
        assert isinstance(rc, RiskControl)

    @pytest.mark.unit
    def test_create_stop_loss_manager(self):
        slm = create_stop_loss_manager()
        assert isinstance(slm, StopLossManager)
