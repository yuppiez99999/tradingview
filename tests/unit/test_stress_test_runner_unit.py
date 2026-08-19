"""test_stress_test_runner_unit.py — 压力测试自动化模块单元测试

覆盖要点:
    - STRESS_SCENARIOS 常量 (4 个场景定义)
    - run_all_scenarios (全部通过/有超限/worst_scenario/report_path)
    - _run_scenario (资产类别识别: stock/etf/quant_neutral/options_tail/futures_hedge/cash)
    - _run_scenario (with_intervention True/False 干预收益)
    - _save_report (落盘 json)
    - 仓位映射 (strategy/style 识别)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.stress_test_runner import STRESS_SCENARIOS, StressTestRunner

# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_four_scenarios(self):
        assert len(STRESS_SCENARIOS) == 4
        assert "crash_2015" in STRESS_SCENARIOS
        assert "slow_bear_2018" in STRESS_SCENARIOS
        assert "v_shock_2020" in STRESS_SCENARIOS
        assert "liquidity_crisis" in STRESS_SCENARIOS

    @pytest.mark.unit
    def test_all_have_limits(self):
        for sid, sdef in STRESS_SCENARIOS.items():
            assert "limit" in sdef, f"{sid} missing limit"
            assert sdef["limit"] < 0

    @pytest.mark.unit
    def test_all_have_asset_impacts(self):
        for _sid, sdef in STRESS_SCENARIOS.items():
            assert "asset_impacts" in sdef
            assert "stock" in sdef["asset_impacts"]


# ============================================================
# _run_scenario 资产类别识别
# ============================================================


class TestRunScenarioAssetClass:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.mark.unit
    def test_stock_strategy(self, runner):
        positions = [{"code": "001", "amount": 1_000_000, "strategy": "stock_long"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        # stock impact = -0.40, pnl = 1_000_000 * -0.40 = -400_000
        assert result["total_pnl_no_intervention"] == pytest.approx(-400_000)

    @pytest.mark.unit
    def test_etf_strategy(self, runner):
        positions = [{"code": "etf", "amount": 1_000_000, "strategy": "etf"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        # etf impact = -0.35
        assert result["total_pnl_no_intervention"] == pytest.approx(-350_000)

    @pytest.mark.unit
    def test_quant_neutral_strategy(self, runner):
        positions = [{"code": "q", "amount": 1_000_000, "strategy": "quant_neutral"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        assert result["total_pnl_no_intervention"] == pytest.approx(-150_000)

    @pytest.mark.unit
    def test_options_strategy(self, runner):
        positions = [{"code": "o", "amount": 200_000, "strategy": "options_tail"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        # options_tail impact = 0.75 (put payoff)
        assert result["total_pnl_no_intervention"] == pytest.approx(150_000)

    @pytest.mark.unit
    def test_futures_strategy(self, runner):
        positions = [{"code": "f", "amount": 500_000, "strategy": "futures_hedge"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        assert result["total_pnl_no_intervention"] == pytest.approx(150_000)

    @pytest.mark.unit
    def test_cash_strategy(self, runner):
        positions = [{"code": "c", "amount": 1_000_000, "strategy": "cash"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        assert result["total_pnl_no_intervention"] == pytest.approx(0)

    @pytest.mark.unit
    def test_style_based_identification(self, runner):
        """style 字段识别 (科技/制造等)"""
        positions = [{"code": "001", "amount": 1_000_000, "strategy": "", "style": "科技"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 1_000_000, with_intervention=False,
        )
        assert result["total_pnl_no_intervention"] == pytest.approx(-400_000)


# ============================================================
# _run_scenario 干预措施
# ============================================================


class TestIntervention:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.mark.unit
    def test_slow_bear_intervention_benefit(self, runner):
        positions = [{"code": "s", "amount": 5_000_000, "strategy": "stock_long"}]
        result = runner._run_scenario(
            "slow_bear_2018", STRESS_SCENARIOS["slow_bear_2018"],
            positions, 5_000_000, with_intervention=True,
        )
        assert result["intervention_benefit"] > 0
        assert result["actual_pnl"] > result["total_pnl_no_intervention"]

    @pytest.mark.unit
    def test_slow_bear_no_intervention(self, runner):
        positions = [{"code": "s", "amount": 5_000_000, "strategy": "stock_long"}]
        result = runner._run_scenario(
            "slow_bear_2018", STRESS_SCENARIOS["slow_bear_2018"],
            positions, 5_000_000, with_intervention=False,
        )
        assert result["intervention_benefit"] == 0

    @pytest.mark.unit
    def test_liquidity_crisis_intervention(self, runner):
        positions = [{"code": "s", "amount": 5_000_000, "strategy": "stock_long"}]
        result = runner._run_scenario(
            "liquidity_crisis", STRESS_SCENARIOS["liquidity_crisis"],
            positions, 5_000_000, with_intervention=True,
        )
        assert result["intervention_benefit"] > 0

    @pytest.mark.unit
    def test_crash_no_intervention_benefit(self, runner):
        """crash_2015 无干预收益"""
        positions = [{"code": "s", "amount": 5_000_000, "strategy": "stock_long"}]
        result = runner._run_scenario(
            "crash_2015", STRESS_SCENARIOS["crash_2015"],
            positions, 5_000_000, with_intervention=True,
        )
        assert result["intervention_benefit"] == 0


# ============================================================
# run_all_scenarios
# ============================================================


class TestRunAllScenarios:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.fixture
    def sim_positions(self):
        return [
            {"code": "stock", "amount": 1_800_000, "strategy": "stock_long"},
            {"code": "etf", "amount": 500_000, "strategy": "etf"},
            {"code": "quant", "amount": 700_000, "strategy": "quant_neutral"},
            {"code": "option", "amount": 200_000, "strategy": "options_tail"},
            {"code": "future", "amount": 500_000, "strategy": "futures_hedge"},
            {"code": "cash", "amount": 1_300_000, "strategy": "cash"},
        ]

    @pytest.mark.unit
    def test_returns_all_scenarios(self, runner, sim_positions):
        result = runner.run_all_scenarios(sim_positions, 5_000_000)
        assert len(result["scenarios"]) == 4
        assert "all_pass" in result
        assert "worst_scenario" in result
        assert "worst_dd" in result
        assert "report_path" in result

    @pytest.mark.unit
    def test_worst_dd_is_minimum(self, runner, sim_positions):
        result = runner.run_all_scenarios(sim_positions, 5_000_000)
        dds = [s["actual_portfolio_dd"] for s in result["scenarios"].values()]
        assert result["worst_dd"] == min(dds)

    @pytest.mark.unit
    def test_report_path_exists(self, runner, sim_positions):
        result = runner.run_all_scenarios(sim_positions, 5_000_000)
        assert Path(result["report_path"]).exists()

    @pytest.mark.unit
    def test_report_content_valid(self, runner, sim_positions):
        result = runner.run_all_scenarios(sim_positions, 5_000_000)
        with open(result["report_path"], encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["portfolio_value"] == 5_000_000
        assert len(saved["scenarios"]) == 4

    @pytest.mark.unit
    def test_with_intervention_flag(self, runner, sim_positions):
        result = runner.run_all_scenarios(sim_positions, 5_000_000, with_intervention=False)
        assert result["with_intervention"] is False
        for s in result["scenarios"].values():
            assert s["with_intervention"] is False

    @pytest.mark.unit
    def test_zero_portfolio_value(self, runner):
        """portfolio_value=0 → actual_dd=0 (避免除零)"""
        positions = [{"code": "s", "amount": 0, "strategy": "stock_long"}]
        result = runner.run_all_scenarios(positions, 0)
        for s in result["scenarios"].values():
            assert s["actual_portfolio_dd"] == 0


# ============================================================
# _save_report
# ============================================================


class TestSaveReport:
    @pytest.mark.unit
    def test_save_report(self, tmp_path):
        runner = StressTestRunner()
        results = {"timestamp": "2026-08-18", "scenarios": {}}
        with patch.object(runner, "_save_report", wraps=runner._save_report):
            path = runner._save_report(results)
        assert path.exists()
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["timestamp"] == "2026-08-18"
