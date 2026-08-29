"""test_annual_return_forecast_unit.py — 年化收益测算器单元测试

覆盖要点:
    - _safe_float (正常/None/NaN/异常)
    - _extract_date_str (YYYYMMDD/YYYY-MM-DD)
    - _extract_baseline (正常/空数据/默认值)
    - _calc_scenario (保守/中性/悲观)
    - _extract_key_risks (CC/IF/回撤/建仓期)
    - _build_summary (空/正常)
    - forecast_annual_return (集成)
    - SCENARIO_DEFINITIONS 常量
"""

from __future__ import annotations

import pytest

from utils.annual_return_forecast import (
    DEFAULT_CASH_INTEREST,
    DEFAULT_CC_YIELD,
    DEFAULT_TARGET_STOCK_RATIO,
    RF_RATE,
    SCENARIO_DEFINITIONS,
    TARGET_ANNUAL_RETURN,
    _build_summary,
    _calc_scenario,
    _extract_baseline,
    _extract_date_str,
    _extract_key_risks,
    _safe_float,
    forecast_annual_return,
)

# ============================================================
# _safe_float
# ============================================================


class TestSafeFloat:
    @pytest.mark.unit
    def test_normal(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float("2.5") == 2.5
        assert _safe_float(10) == 10.0

    @pytest.mark.unit
    def test_none(self):
        assert _safe_float(None) == 0.0
        assert _safe_float(None, default=-1.0) == -1.0

    @pytest.mark.unit
    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0
        assert _safe_float(float("inf")) == 0.0

    @pytest.mark.unit
    def test_invalid(self):
        assert _safe_float("abc") == 0.0
        assert _safe_float([1, 2]) == 0.0


# ============================================================
# _extract_date_str
# ============================================================


class TestExtractDateStr:
    @pytest.mark.unit
    def test_compact(self):
        assert _extract_date_str("trade_plan_20260818", "trade_plan_") == "20260818"

    @pytest.mark.unit
    def test_with_dashes(self):
        assert (
            _extract_date_str("daily_pnl_report_2026-08-18", "daily_pnl_report_")
            == "20260818"
        )


# ============================================================
# _extract_baseline
# ============================================================


class TestExtractBaseline:
    @pytest.mark.unit
    def test_empty_inputs(self):
        result = _extract_baseline({}, {})
        assert result["portfolio_base"] == 5_000_000.0
        assert result["stock_market_value"] == 0.0
        assert result["cc_premium_annual"] == DEFAULT_CC_YIELD
        assert result["cash_interest_annual"] == DEFAULT_CASH_INTEREST
        assert result["target_stock_ratio"] == DEFAULT_TARGET_STOCK_RATIO

    @pytest.mark.unit
    def test_with_trade_plan(self):
        plan = {
            "capital": 10_000_000,
            "hedge_fund_overlays": {
                "theta_engine": {"portfolio_yield_annualized": 0.08}
            },
        }
        result = _extract_baseline(plan, {})
        assert result["portfolio_base"] == 10_000_000
        assert result["cc_premium_annual"] == 0.08

    @pytest.mark.unit
    def test_with_pnl_report(self):
        pnl = {
            "portfolio_pnl": {
                "summary": {"total_market_value": 8_000_000, "total_cost": 7_500_000}
            }
        }
        result = _extract_baseline({}, pnl)
        assert result["stock_market_value"] == 8_000_000
        assert result["stock_cost"] == 7_500_000
        assert abs(result["actual_stock_ratio"] - 8_000_000 / 5_000_000) < 1e-6

    @pytest.mark.unit
    def test_monthly_premium_fallback(self):
        plan = {"hedge_fund_overlays": {"theta_engine": {"total_premium": 50000.0}}}
        pnl = {"portfolio_pnl": {"summary": {"total_market_value": 8_000_000}}}
        result = _extract_baseline(plan, pnl)
        # cc_premium_annual = 50000 * 12 / 8000000 = 0.075
        assert abs(result["cc_premium_annual"] - 0.075) < 1e-6


# ============================================================
# _calc_scenario
# ============================================================


class TestCalcScenario:
    @pytest.mark.unit
    def test_neutral_scenario(self):
        baseline = _extract_baseline({}, {})
        result = _calc_scenario(
            spot_annual_return=0.08,
            hedge_impact=-0.005,
            baseline=baseline,
            max_drawdown=-0.12,
        )
        assert "total_annual_return" in result
        assert "sharpe_ratio" in result
        assert "meets_target" in result
        assert "drawdown_breached" in result
        assert result["max_drawdown"] == -0.12

    @pytest.mark.unit
    def test_drawdown_breached(self):
        baseline = _extract_baseline({}, {})
        result = _calc_scenario(
            spot_annual_return=-0.42,
            hedge_impact=0.18,
            baseline=baseline,
            max_drawdown=-0.27,
        )
        assert result["drawdown_breached"] is True

    @pytest.mark.unit
    def test_drawdown_not_breached(self):
        baseline = _extract_baseline({}, {})
        result = _calc_scenario(
            spot_annual_return=0.08,
            hedge_impact=-0.005,
            baseline=baseline,
            max_drawdown=-0.12,
        )
        assert result["drawdown_breached"] is False

    @pytest.mark.unit
    def test_stock_ratio_capped(self):
        """actual_stock_ratio > target → 用 target"""
        plan = {"capital": 5_000_000}
        pnl = {
            "portfolio_pnl": {"summary": {"total_market_value": 5_000_000}}
        }  # 100% 仓位
        baseline = _extract_baseline(plan, pnl)
        result = _calc_scenario(0.08, -0.005, baseline, -0.12)
        # stock_ratio = min(1.0, 0.9) = 0.9
        assert result["stock_ratio"] == 0.9


# ============================================================
# _extract_key_risks
# ============================================================


class TestExtractKeyRisks:
    @pytest.mark.unit
    def test_empty(self):
        risks = _extract_key_risks({}, {})
        # 空数据: 建仓期未完成 (stock_mv/capital = 0 < 0.5)
        assert any("建仓期" in r for r in risks)

    @pytest.mark.unit
    def test_cc_risk(self):
        plan = {"hedge_fund_overlays": {"theta_engine": {"positions_count": 5}}}
        risks = _extract_key_risks(plan, {})
        assert any("Covered Call" in r for r in risks)

    @pytest.mark.unit
    def test_if_risk(self):
        plan = {"hedge_config": {"layers": {"layer1_futures": {"symbol": "IF"}}}}
        risks = _extract_key_risks(plan, {})
        assert any("IF" in r for r in risks)

    @pytest.mark.unit
    def test_drawdown_risk(self):
        pnl = {"risk_metrics": {"max_drawdown_pct": -0.12}}
        risks = _extract_key_risks({}, pnl)
        assert any("回撤" in r for r in risks)


# ============================================================
# _build_summary
# ============================================================


class TestBuildSummary:
    @pytest.mark.unit
    def test_empty(self):
        assert _build_summary([]) == {}

    @pytest.mark.unit
    def test_three_scenarios(self):
        baseline = _extract_baseline({}, {})
        scenarios = []
        for defn in SCENARIO_DEFINITIONS:
            s = _calc_scenario(
                defn["spot_annual_return"],
                defn["hedge_impact"],
                baseline,
                defn["max_drawdown"],
            )
            s["name"] = defn["name"]
            scenarios.append(s)
        summary = _build_summary(scenarios)
        assert "best_case" in summary
        assert "worst_case" in summary
        assert "average" in summary
        assert summary["scenarios_total"] == 3
        assert "hard_constraints" in summary


# ============================================================
# forecast_annual_return (集成)
# ============================================================


class TestForecastAnnualReturn:
    @pytest.mark.unit
    def test_returns_dict(self):
        result = forecast_annual_return()
        assert "generated_at" in result
        assert "scenarios" in result
        assert "baseline" in result
        assert "key_risks" in result
        assert "summary" in result
        assert "hard_constraints" in result
        assert len(result["scenarios"]) == 3

    @pytest.mark.unit
    def test_scenario_names(self):
        result = forecast_annual_return()
        names = [s["name"] for s in result["scenarios"]]
        assert "保守情景" in names
        assert "中性情景" in names
        assert "悲观情景" in names


# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_target_return(self):
        assert TARGET_ANNUAL_RETURN == 0.08

    @pytest.mark.unit
    def test_rf_rate(self):
        assert RF_RATE == 0.025

    @pytest.mark.unit
    def test_scenario_definitions(self):
        assert len(SCENARIO_DEFINITIONS) == 3
        for d in SCENARIO_DEFINITIONS:
            assert "name" in d
            assert "spot_annual_return" in d
            assert "hedge_impact" in d
            assert "max_drawdown" in d
