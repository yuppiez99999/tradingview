"""test_stress_test_scenario_library_unit.py — 压力测试情景库单元测试

覆盖要点:
    - ShockFactors / StressScenario / StressTestResult dataclass
    - _build_default_scenarios (10个场景)
    - StressTestEngine 构造
    - run_scenario (股票/ETF/债券/黄金/期货/风格/行业/流动性)
    - run_all_scenarios
    - add_custom_scenario / create_custom_shock
    - get_worst_scenario / get_breached_scenarios / summarize
"""
from __future__ import annotations

import pytest

from utils.stress_test_scenario_library import (
    ShockFactors,
    StressScenario,
    StressTestEngine,
    _build_default_scenarios,
)

# ============================================================
# Dataclass
# ============================================================


class TestShockFactors:
    @pytest.mark.unit
    def test_defaults(self):
        s = ShockFactors()
        assert s.equity_market == 0.0
        assert s.etf_limit_down_pct == 0.0
        assert s.put_premium_spike == 0.0


class TestStressScenario:
    @pytest.mark.unit
    def test_construction(self):
        s = StressScenario(
            name="test", description="d", start_date="2020-01-01", end_date="2020-06-01",
            severity="moderate", shocks=ShockFactors(equity_market=-0.20),
        )
        assert s.name == "test"
        assert s.historical_market_return == 0.0


# ============================================================
# _build_default_scenarios
# ============================================================


class TestDefaultScenarios:
    @pytest.mark.unit
    def test_has_10_scenarios(self):
        scenarios = _build_default_scenarios()
        assert len(scenarios) == 10

    @pytest.mark.unit
    def test_2008_exists(self):
        scenarios = _build_default_scenarios()
        names = [s.name for s in scenarios]
        assert "2008全球金融危机" in names

    @pytest.mark.unit
    def test_liquidity_trap_exists(self):
        scenarios = _build_default_scenarios()
        names = [s.name for s in scenarios]
        assert any("流动性陷阱" in n for n in names)


# ============================================================
# StressTestEngine 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_defaults(self):
        e = StressTestEngine()
        assert e.var_confidence == 0.95
        assert e.risk_threshold == -0.10
        assert len(e.scenarios) == 10


# ============================================================
# run_scenario
# ============================================================


class TestRunScenario:
    @pytest.fixture
    def engine(self):
        return StressTestEngine()

    @pytest.fixture
    def positions(self):
        return [
            {"code": "600276", "amount": 500000, "sector": "医药", "style": "growth", "type": "STOCK"},
            {"code": "510300", "amount": 300000, "sector": "指数", "style": "", "type": "ETF"},
        ]

    @pytest.mark.unit
    def test_stock_pnl(self, engine, positions):
        scenario = engine.scenarios[0]  # 2008
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert result.scenario_name == "2008全球金融危机"
        assert result.portfolio_pnl < 0  # 金融危机 → 亏损
        assert "600276" in result.by_asset

    @pytest.mark.unit
    def test_breach(self, engine):
        """极端场景 → 突破阈值"""
        positions = [{"code": "600276", "amount": 1_000_000, "sector": "金融", "type": "STOCK"}]
        scenario = engine.scenarios[0]  # 2008 extreme
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert result.is_breach is True
        assert result.breach_reason != ""

    @pytest.mark.unit
    def test_no_breach(self, engine):
        """温和场景 + 小仓位 → 不突破"""
        positions = [{"code": "600276", "amount": 100, "sector": "医药", "type": "STOCK"}]
        scenario = StressScenario(
            name="mild", description="", start_date="", end_date="",
            severity="mild", shocks=ShockFactors(equity_market=-0.01),
        )
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert result.is_breach is False

    @pytest.mark.unit
    def test_var_change(self, engine, positions):
        scenario = engine.scenarios[0]
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert result.var_after > result.var_before  # 波动率放大

    @pytest.mark.unit
    def test_empty_positions(self, engine):
        scenario = engine.scenarios[0]
        result = engine.run_scenario(scenario, [], 1_000_000)
        assert result.portfolio_pnl == 0.0

    @pytest.mark.unit
    def test_etf_limit_down(self, engine):
        """ETF 跌停 → 额外冲击"""
        positions = [{"code": "510300", "amount": 500000, "sector": "指数", "type": "ETF"}]
        scenario = StressScenario(
            name="etf_test", description="", start_date="", end_date="",
            severity="extreme",
            shocks=ShockFactors(equity_market=-0.10, etf_limit_down_pct=0.6),
        )
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert "liquidity" in result.by_factor

    @pytest.mark.unit
    def test_futures_liquidity(self, engine):
        positions = [{"code": "IF2406", "amount": 500000, "sector": "期货", "type": "FUTURES"}]
        scenario = StressScenario(
            name="fut_test", description="", start_date="", end_date="",
            severity="extreme",
            shocks=ShockFactors(equity_market=-0.10, futures_liquidity_dry_up=0.8, hedge_slippage_bps=200),
        )
        result = engine.run_scenario(scenario, positions, 1_000_000)
        assert "liquidity" in result.by_factor


# ============================================================
# run_all_scenarios
# ============================================================


class TestRunAllScenarios:
    @pytest.mark.unit
    def test_all(self):
        engine = StressTestEngine()
        positions = [{"code": "600276", "amount": 500000, "sector": "医药", "type": "STOCK"}]
        results = engine.run_all_scenarios(positions, 1_000_000)
        assert len(results) == 10


# ============================================================
# 自定义场景
# ============================================================


class TestCustomScenario:
    @pytest.mark.unit
    def test_add_custom(self):
        engine = StressTestEngine()
        initial = len(engine.scenarios)
        scenario = StressScenario(
            name="custom", description="", start_date="", end_date="",
            severity="moderate", shocks=ShockFactors(equity_market=-0.15),
        )
        engine.add_custom_scenario(scenario)
        assert len(engine.scenarios) == initial + 1

    @pytest.mark.unit
    def test_create_custom_shock(self):
        engine = StressTestEngine()
        scenario = engine.create_custom_shock(
            name="test_shock", description="test", shocks=ShockFactors(equity_market=-0.20),
        )
        assert scenario.name == "test_shock"
        assert scenario in engine.scenarios


# ============================================================
# 分析工具
# ============================================================


class TestAnalysisTools:
    @pytest.mark.unit
    def test_worst_scenario(self):
        engine = StressTestEngine()
        positions = [{"code": "600276", "amount": 500000, "sector": "金融", "type": "STOCK"}]
        results = engine.run_all_scenarios(positions, 1_000_000)
        worst = engine.get_worst_scenario(results)
        assert worst is not None
        assert worst.portfolio_return == min(r.portfolio_return for r in results)

    @pytest.mark.unit
    def test_worst_empty(self):
        engine = StressTestEngine()
        assert engine.get_worst_scenario([]) is None

    @pytest.mark.unit
    def test_breached_scenarios(self):
        engine = StressTestEngine()
        positions = [{"code": "600276", "amount": 1_000_000, "sector": "金融", "type": "STOCK"}]
        results = engine.run_all_scenarios(positions, 1_000_000)
        breaches = engine.get_breached_scenarios(results)
        assert all(r.is_breach for r in breaches)

    @pytest.mark.unit
    def test_summarize(self):
        engine = StressTestEngine()
        positions = [{"code": "600276", "amount": 500000, "sector": "医药", "type": "STOCK"}]
        results = engine.run_all_scenarios(positions, 1_000_000)
        summary = engine.summarize(results)
        assert summary["n_scenarios"] == 10
        assert "worst_scenario" in summary
        assert "n_breaches" in summary

    @pytest.mark.unit
    def test_summarize_empty(self):
        engine = StressTestEngine()
        summary = engine.summarize([])
        assert summary["n_scenarios"] == 0
