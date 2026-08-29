"""stop_loss 单元测试 — 止损止盈监控模块."""

from __future__ import annotations

import pytest

from utils.stop_loss import (
    AlertLevel,
    RiskType,
    StopLossMonitor,
    generate_risk_report,
)


class TestAlertLevel:
    def test_values(self):
        assert AlertLevel.NORMAL.value == "normal"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.CRITICAL.value == "critical"
        assert AlertLevel.TRIGGERED.value == "triggered"


class TestRiskType:
    def test_values(self):
        assert RiskType.STOP_LOSS.value == "stop_loss"
        assert RiskType.TAKE_PROFIT.value == "take_profit"


class TestStopLossMonitorInit:
    def test_defaults(self):
        m = StopLossMonitor()
        assert m.warning_threshold == 5.0
        assert m.critical_threshold == 2.0
        assert m.trailing_drawdown_pct == 10.0
        assert m.max_single_loss == 50_000

    def test_custom(self):
        m = StopLossMonitor(
            warning_threshold_pct=-8,
            critical_threshold_pct=-3,
            trailing_drawdown_pct=15,
        )
        assert m.warning_threshold == 8
        assert m.critical_threshold == 3
        assert m.trailing_drawdown_pct == 15


class TestDetermineLevel:
    def test_triggered(self):
        m = StopLossMonitor()
        assert m._determine_level(-1) == AlertLevel.TRIGGERED
        assert m._determine_level(0) == AlertLevel.TRIGGERED

    def test_critical(self):
        m = StopLossMonitor()
        assert m._determine_level(1) == AlertLevel.CRITICAL
        assert m._determine_level(2) == AlertLevel.CRITICAL

    def test_warning(self):
        m = StopLossMonitor()
        assert m._determine_level(3) == AlertLevel.WARNING
        assert m._determine_level(5) == AlertLevel.WARNING

    def test_normal(self):
        m = StopLossMonitor()
        assert m._determine_level(10) == AlertLevel.NORMAL
        assert m._determine_level(100) == AlertLevel.NORMAL


class TestGenerateAction:
    def test_triggered(self):
        m = StopLossMonitor()
        assert "立即执行" in m._generate_action(AlertLevel.TRIGGERED, -10, -1)

    def test_critical(self):
        m = StopLossMonitor()
        assert "危险" in m._generate_action(AlertLevel.CRITICAL, -5, 1)

    def test_warning(self):
        m = StopLossMonitor()
        assert "预警" in m._generate_action(AlertLevel.WARNING, -3, 3)

    def test_good_profit(self):
        m = StopLossMonitor()
        assert "移动止盈" in m._generate_action(AlertLevel.NORMAL, 25, 20)

    def test_small_loss(self):
        m = StopLossMonitor()
        assert "小幅浮亏" in m._generate_action(AlertLevel.NORMAL, -6, 20)

    def test_small_profit(self):
        m = StopLossMonitor()
        assert "持有观望" in m._generate_action(AlertLevel.NORMAL, 5, 20)

    def test_normal(self):
        m = StopLossMonitor()
        assert "正常持有" in m._generate_action(AlertLevel.NORMAL, 0, 20)


class TestCalculateRiskScore:
    def test_low_risk(self):
        m = StopLossMonitor()
        score = m._calculate_risk_score(5, 20, "low")
        assert score == 0.0

    def test_high_loss(self):
        m = StopLossMonitor()
        score = m._calculate_risk_score(-25, -1, "medium")
        assert score == 80.0

    def test_triggered_and_high_loss(self):
        m = StopLossMonitor()
        score = m._calculate_risk_score(-25, -1, "high")
        assert score == 96.0

    def test_medium_loss_close_to_sl(self):
        m = StopLossMonitor()
        score = m._calculate_risk_score(-12, 1, "medium")
        assert score == 55.0

    def test_risk_level_multiplier(self):
        m = StopLossMonitor()
        s_low = m._calculate_risk_score(-12, 1, "low")
        s_med = m._calculate_risk_score(-12, 1, "medium")
        s_high = m._calculate_risk_score(-12, 1, "high")
        assert s_low < s_med < s_high


class TestCheckSingle:
    def test_normal_case(self):
        m = StopLossMonitor()
        result = m.check_single(
            code="600276",
            name="药明康德",
            current_price=100,
            base_price=90,
            stop_loss_pct=-15,
            take_profit_pct=50,
        )
        assert result["code"] == "600276"
        assert result["pnl_pct"] == pytest.approx(11.11, abs=0.1)
        assert result["alert_level"] == "normal"

    def test_triggered(self):
        m = StopLossMonitor()
        result = m.check_single(
            code="A",
            name="A",
            current_price=80,
            base_price=100,
            stop_loss_pct=-15,
            take_profit_pct=50,
        )
        assert result["alert_level"] == "triggered"
        assert result["stop_loss"]["is_triggered"] is True

    def test_invalid_price(self):
        m = StopLossMonitor()
        result = m.check_single(
            code="A",
            name="A",
            current_price=None,
            base_price=100,
            stop_loss_pct=-15,
            take_profit_pct=50,
        )
        assert result["status"] == "unknown"

    def test_with_explicit_prices(self):
        m = StopLossMonitor()
        result = m.check_single(
            code="A",
            name="A",
            current_price=95,
            base_price=100,
            stop_loss_pct=-15,
            take_profit_pct=50,
            stop_loss_price=85,
            take_profit_price=150,
        )
        assert result["stop_loss"]["trigger_price"] == 85
        assert result["take_profit"]["trigger_price"] == 150

    def test_trailing_stop(self):
        m = StopLossMonitor()
        result = m.check_single(
            code="A",
            name="A",
            current_price=110,
            base_price=100,
            stop_loss_pct=-15,
            take_profit_pct=50,
            high_price=130,
            trailing_stop=True,
        )
        assert result["trailing_active"] is True

    def test_history_recorded(self):
        m = StopLossMonitor()
        m.check_single(
            code="A",
            name="A",
            current_price=100,
            base_price=90,
            stop_loss_pct=-15,
            take_profit_pct=50,
        )
        assert len(m.alerts_history) == 1


class TestCheckAll:
    def test_basic(self):
        m = StopLossMonitor()
        rules = [
            {
                "code": "A",
                "name": "A",
                "base_price": 100,
                "stop_loss_pct": -15,
                "take_profit_pct": 50,
            },
            {
                "code": "B",
                "name": "B",
                "base_price": 200,
                "stop_loss_pct": -10,
                "take_profit_pct": 30,
            },
        ]
        quotes = {"A": {"price": 90}, "B": {"price": 210}}
        results = m.check_all(rules, quotes)
        assert len(results) == 2
        assert results[0]["risk_score"] >= results[1]["risk_score"]

    def test_missing_quote(self):
        m = StopLossMonitor()
        rules = [{"code": "X", "name": "X"}]
        results = m.check_all(rules, {})
        assert len(results) == 1
        assert "error" in results[0]

    def test_zero_price(self):
        m = StopLossMonitor()
        rules = [{"code": "X", "name": "X"}]
        results = m.check_all(rules, {"X": {"price": 0}})
        assert "error" in results[0]


class TestGenerateRiskReport:
    def test_empty(self):
        report = generate_risk_report([])
        assert "止损止盈风险监控报告" in report
        assert "监控总数: 0" in report

    def test_with_alerts(self):
        alerts = [
            {
                "code": "A",
                "name": "StockA",
                "alert_level": "normal",
                "current_price": 100,
                "pnl_pct": 5,
                "stop_loss": {"trigger_price": 85},
                "distance_to_sl_pct": 15,
                "risk_score": 10,
            },
            {
                "code": "B",
                "name": "StockB",
                "alert_level": "triggered",
                "current_price": 80,
                "pnl_pct": -20,
                "stop_loss": {"trigger_price": 85},
                "distance_to_sl_pct": -5,
                "risk_score": 80,
                "action_suggestion": "立即执行止损",
            },
        ]
        report = generate_risk_report(alerts)
        assert "监控总数: 2" in report
        assert "已触发: 1" in report
        assert "需要立即关注" in report
        assert "StockB" in report

    def test_overall_assessment(self):
        alerts = [
            {
                "code": "A",
                "name": "A",
                "alert_level": "normal",
                "current_price": 100,
                "pnl_pct": 5,
                "stop_loss": {"trigger_price": 85},
                "distance_to_sl_pct": 15,
                "risk_score": 70,
            },
        ]
        report = generate_risk_report(alerts)
        assert "高风险" in report
