"""stress_test 单元测试 — 压力测试报告生成"""

from utils.stress_test import (
    HARD_STOP_MAX_DRAWDOWN,
    STRESS_SCENARIOS,
    TAIL_HEDGE_THRESHOLD,
    generate_stress_report,
)


class TestConstants:
    def test_hard_stop(self):
        assert HARD_STOP_MAX_DRAWDOWN == 0.15

    def test_tail_hedge(self):
        assert TAIL_HEDGE_THRESHOLD == 0.05


class TestStressScenarios:
    def test_has_2008(self):
        assert "2008金融危机" in STRESS_SCENARIOS

    def test_has_2015(self):
        assert "2015股灾" in STRESS_SCENARIOS

    def test_has_2020(self):
        assert "2020疫情" in STRESS_SCENARIOS

    def test_scenario_fields(self):
        for _name, params in STRESS_SCENARIOS.items():
            assert "equity_shock" in params
            assert "volatility_spike" in params
            assert "correlation_increase" in params

    def test_equity_shock_negative(self):
        for _name, params in STRESS_SCENARIOS.items():
            assert params["equity_shock"] < 0

    def test_count(self):
        assert len(STRESS_SCENARIOS) == 6


class TestGenerateStressReport:
    def test_default_report(self):
        report = generate_stress_report()
        assert "极端压力测试报告" in report
        assert "历史极端情景回测" in report
        assert "蒙特卡洛模拟" in report
        assert "硬止损检查" in report

    def test_report_has_timestamp(self):
        report = generate_stress_report()
        assert "生成时间" in report

    def test_report_has_hard_stop_line(self):
        report = generate_stress_report()
        assert "硬止损线" in report
        assert "15%" in report

    def test_report_has_tail_hedge(self):
        report = generate_stress_report()
        assert "尾部对冲阈值" in report
        assert "5%" in report

    def test_custom_scenarios(self):
        custom = {
            "测试情景": {
                "equity_shock": -0.50,
                "volatility_spike": 3.0,
                "correlation_increase": 0.5,
            },
        }
        report = generate_stress_report(scenarios=custom)
        assert "测试情景" in report

    def test_custom_monte_carlo_runs(self):
        report = generate_stress_report(monte_carlo_runs=100)
        assert "100 次" in report

    def test_custom_portfolio(self):
        portfolio = {"600519": {"weight": 0.5}, "000001": {"weight": 0.5}}
        report = generate_stress_report(portfolio=portfolio)
        assert "极端压力测试报告" in report

    def test_empty_portfolio(self):
        report = generate_stress_report(portfolio={})
        assert "极端压力测试报告" in report

    def test_report_is_string(self):
        report = generate_stress_report()
        assert isinstance(report, str)

    def test_report_contains_var(self):
        report = generate_stress_report(monte_carlo_runs=500)
        assert "VaR" in report

    def test_report_contains_scenarios_table(self):
        report = generate_stress_report()
        assert "| 情景 |" in report
        assert "|------|" in report

    def test_trigger_hard_stop(self):
        custom = {
            "极端": {
                "equity_shock": -0.90,
                "volatility_spike": 5.0,
                "correlation_increase": 0.5,
            },
        }
        report = generate_stress_report(scenarios=custom)
        assert "🔴" in report

    def test_no_trigger(self):
        custom = {
            "温和": {
                "equity_shock": -0.05,
                "volatility_spike": 1.0,
                "correlation_increase": 0.0,
            },
        }
        report = generate_stress_report(scenarios=custom)
        assert "🟢" in report
