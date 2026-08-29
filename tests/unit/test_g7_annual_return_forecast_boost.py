"""
G7 Coverage Boost: utils/annual_return_forecast.py (224 lines, 0% -> target ~80%)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.annual_return_forecast import (
    DEFAULT_CASH_INTEREST,
    DEFAULT_TARGET_STOCK_RATIO,
    MAX_DRAWDOWN_LIMIT,
    RF_RATE,
    TARGET_ANNUAL_RETURN,
    _build_summary,
    _calc_scenario,
    _extract_baseline,
    _extract_date_str,
    _extract_key_risks,
    _find_latest_pnl_report,
    _find_latest_trade_plan,
    _load_json,
    _safe_float,
    forecast_annual_return,
    main,
    print_forecast,
    write_to_trade_plan,
)


class TestSafeFloat:
    def test_valid_int(self):
        assert _safe_float(42) == 42.0

    def test_valid_float(self):
        assert _safe_float(3.14) == 3.14

    def test_valid_string(self):
        assert _safe_float("2.5") == 2.5

    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0

    def test_inf_returns_default(self):
        assert _safe_float(float("inf")) == 0.0

    def test_neg_inf_returns_default(self):
        assert _safe_float(float("-inf")) == 0.0

    def test_nan_returns_default(self):
        assert _safe_float(float("nan")) == 0.0

    def test_invalid_string_returns_default(self):
        assert _safe_float("abc") == 0.0

    def test_custom_default(self):
        assert _safe_float("bad", default=5.0) == 5.0


class TestExtractDateStr:
    def test_underscore_prefix(self):
        assert _extract_date_str("trade_plan_20260720", "trade_plan_") == "20260720"

    def test_hyphen_prefix(self):
        assert (
            _extract_date_str("daily_pnl_report_2026-07-20", "daily_pnl_report_")
            == "20260720"
        )

    def test_multiple_hyphens(self):
        assert _extract_date_str("trade_plan_2026-07-20", "trade_plan_") == "20260720"

    def test_no_prefix_match(self):
        assert _extract_date_str("other_20260720", "trade_plan_") == "other_20260720"


class TestFindLatestTradePlan:
    def test_dir_missing(self, tmp_path: Path):
        with patch("utils.annual_return_forecast.PLAN_DIR", tmp_path / "missing"):
            assert _find_latest_trade_plan() is None

    def test_target_date_found(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "trade_plan_20260720.json").write_text("{}")
        (plan_dir / "trade_plan_20260721.json").write_text("{}")
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            result = _find_latest_trade_plan("2026-07-20")
        assert result is not None
        assert result.name == "trade_plan_20260720.json"

    def test_target_date_fallback_compact(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "trade_plan_20260720.json").write_text("{}")
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            result = _find_latest_trade_plan("2026-07-21")
        assert result is not None
        assert result.name == "trade_plan_20260720.json"

    def test_latest_when_no_target(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "trade_plan_20260719.json").write_text("{}")
        (plan_dir / "trade_plan_20260721.json").write_text("{}")
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            result = _find_latest_trade_plan()
        assert result is not None
        assert result.name == "trade_plan_20260721.json"

    def test_empty_dir(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            assert _find_latest_trade_plan() is None


class TestFindLatestPnlReport:
    def test_dir_missing(self, tmp_path: Path):
        with patch("utils.annual_return_forecast.REPORTS_DIR", tmp_path / "missing"):
            assert _find_latest_pnl_report() is None

    def test_target_date_found(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "daily_pnl_report_2026-07-20.json").write_text("{}")
        (reports_dir / "daily_pnl_report_2026-07-21.json").write_text("{}")
        with patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir):
            result = _find_latest_pnl_report("2026-07-20")
        assert result is not None
        assert result.name == "daily_pnl_report_2026-07-20.json"

    def test_latest_when_no_target(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "daily_pnl_report_2026-07-19.json").write_text("{}")
        (reports_dir / "daily_pnl_report_2026-07-21.json").write_text("{}")
        with patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir):
            result = _find_latest_pnl_report()
        assert result is not None
        assert result.name == "daily_pnl_report_2026-07-21.json"

    def test_ignores_snapshots_with_double_hyphen(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "daily_pnl_report_2026-07-20--1.json").write_text("{}")
        (reports_dir / "daily_pnl_report_2026-07-21.json").write_text("{}")
        with patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir):
            result = _find_latest_pnl_report()
        assert result is not None
        assert result.name == "daily_pnl_report_2026-07-21.json"

    def test_empty_dir(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        with patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir):
            assert _find_latest_pnl_report() is None


class TestLoadJson:
    def test_valid_json(self, tmp_path: Path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"a": 1}))
        assert _load_json(p) == {"a": 1}

    def test_none_path(self):
        assert _load_json(None) is None

    def test_missing_path(self, tmp_path: Path):
        assert _load_json(tmp_path / "missing.json") is None

    def test_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        assert _load_json(p) is None


class TestExtractBaseline:
    def _make_trade_plan(self, overrides: dict | None = None) -> dict:
        data = {
            "capital": 5_000_000.0,
            "hedge_fund_overlays": {
                "theta_engine": {
                    "portfolio_yield_annualized": 0.065,
                    "total_premium": 0.0,
                    "positions_count": 1,
                }
            },
        }
        if overrides:
            data.update(overrides)
        return data

    def _make_pnl_report(self, overrides: dict | None = None) -> dict:
        data = {
            "portfolio_pnl": {
                "summary": {
                    "total_market_value": 4_500_000.0,
                    "total_cost": 4_200_000.0,
                }
            },
            "hedge_position": {
                "summary": {
                    "total_premium_budget": 25_000.0,
                }
            },
            "risk_metrics": {
                "max_drawdown_pct": -8.0,
            },
        }
        if overrides:
            data.update(overrides)
        return data

    def test_baseline_values(self):
        trade_plan = self._make_trade_plan()
        pnl_report = self._make_pnl_report()
        baseline = _extract_baseline(trade_plan, pnl_report)
        assert baseline["portfolio_base"] == 5_000_000.0
        assert baseline["stock_market_value"] == 4_500_000.0
        assert baseline["stock_cost"] == 4_200_000.0
        assert baseline["cash_unallocated"] == 500_000.0
        assert baseline["cc_premium_annual"] == 0.065
        assert baseline["put_premium_cost"] == 25_000.0
        assert baseline["actual_stock_ratio"] == pytest.approx(0.9)
        assert baseline["target_stock_ratio"] == DEFAULT_TARGET_STOCK_RATIO

    def test_missing_theta_engine_uses_default_cc(self):
        trade_plan = self._make_trade_plan()
        trade_plan["hedge_fund_overlays"] = {}
        pnl_report = self._make_pnl_report()
        baseline = _extract_baseline(trade_plan, pnl_report)
        assert baseline["cc_premium_annual"] == pytest.approx(0.065)

    def test_monthly_premium_converted_to_annual(self):
        trade_plan = self._make_trade_plan()
        trade_plan["hedge_fund_overlays"]["theta_engine"][
            "portfolio_yield_annualized"
        ] = 0.0
        trade_plan["hedge_fund_overlays"]["theta_engine"]["total_premium"] = 50_000.0
        pnl_report = self._make_pnl_report()
        pnl_report["portfolio_pnl"]["summary"]["total_market_value"] = 5_000_000.0
        baseline = _extract_baseline(trade_plan, pnl_report)
        assert baseline["cc_premium_annual"] == pytest.approx(
            (50_000.0 * 12) / 5_000_000.0
        )

    def test_missing_cost_uses_market_value(self):
        trade_plan = self._make_trade_plan()
        pnl_report = self._make_pnl_report()
        pnl_report["portfolio_pnl"]["summary"]["total_cost"] = None
        baseline = _extract_baseline(trade_plan, pnl_report)
        assert baseline["stock_cost"] == 4_500_000.0


class TestCalcScenario:
    def _base_baseline(self) -> dict:
        return {
            "portfolio_base": 5_000_000.0,
            "stock_market_value": 4_500_000.0,
            "stock_cost": 4_200_000.0,
            "cash_unallocated": 500_000.0,
            "cc_premium_annual": 0.065,
            "cc_premium_monthly": 0.0,
            "cash_interest_annual": DEFAULT_CASH_INTEREST,
            "put_premium_cost": 0.0,
            "actual_stock_ratio": 0.9,
            "target_stock_ratio": DEFAULT_TARGET_STOCK_RATIO,
        }

    def test_neutral_scenario_returns(self):
        baseline = self._base_baseline()
        result = _calc_scenario(
            spot_annual_return=0.08,
            hedge_impact=-0.005,
            baseline=baseline,
            max_drawdown=-0.12,
        )
        assert result["spot_annual_return"] == pytest.approx(0.08)
        assert result["total_annual_return"] > 0
        assert result["meets_target"] is True
        assert result["drawdown_breached"] is False

    def test_conservative_scenario_flags_drawdown(self):
        baseline = self._base_baseline()
        result = _calc_scenario(
            spot_annual_return=-0.17,
            hedge_impact=0.07,
            baseline=baseline,
            max_drawdown=-0.16,
        )
        assert result["drawdown_breached"] is True

    def test_put_cost_reduces_return(self):
        baseline = self._base_baseline()
        baseline["put_premium_cost"] = 1.0
        result = _calc_scenario(
            spot_annual_return=0.0,
            hedge_impact=0.0,
            baseline=baseline,
            max_drawdown=-0.10,
        )
        assert result["put_premium_cost"] == pytest.approx(1.0)
        assert result["total_annual_return"] < 0.0


class TestExtractKeyRisks:
    def _trade_plan(
        self,
        theta_positions: int = 0,
        layer1: bool = False,
        capital: float = 5_000_000.0,
    ) -> dict:
        return {
            "capital": capital,
            "hedge_fund_overlays": {
                "theta_engine": {"positions_count": theta_positions}
            },
            "hedge_config": {"layers": {"layer1_futures": layer1}},
        }

    def _pnl_report(
        self, max_drawdown: float = -0.05, market_value: float = 4_500_000.0
    ) -> dict:
        return {
            "portfolio_pnl": {"summary": {"total_market_value": market_value}},
            "risk_metrics": {"max_drawdown_pct": max_drawdown * 100},
        }

    def test_theta_exercise_risk(self):
        risks = _extract_key_risks(
            self._trade_plan(theta_positions=1), self._pnl_report()
        )
        assert any("Covered Call" in r for r in risks)

    def test_futures_hedge_risk(self):
        risks = _extract_key_risks(self._trade_plan(layer1=True), self._pnl_report())
        assert any("IF" in r for r in risks)

    def test_drawdown_risk(self):
        risks = _extract_key_risks(
            self._trade_plan(), self._pnl_report(max_drawdown=-0.12)
        )
        assert any("回撤" in r for r in risks)

    def test_low_position_risk(self):
        risks = _extract_key_risks(
            self._trade_plan(), self._pnl_report(market_value=1_000_000.0)
        )
        assert any("建仓期未完成" in r for r in risks)

    def test_no_risk_when_healthy(self):
        risks = _extract_key_risks(self._trade_plan(), self._pnl_report())
        assert risks == []


class TestBuildSummary:
    def test_empty_scenarios(self):
        assert _build_summary([]) == {}

    def test_summary_stats(self):
        scenarios = [
            {
                "total_annual_return": 0.10,
                "sharpe_ratio": 1.2,
                "meets_target": True,
                "max_drawdown": -0.10,
                "drawdown_breached": False,
            },
            {
                "total_annual_return": -0.05,
                "sharpe_ratio": 0.1,
                "meets_target": False,
                "max_drawdown": -0.20,
                "drawdown_breached": True,
            },
        ]
        summary = _build_summary(scenarios)
        assert summary["best_case"] == pytest.approx(0.10)
        assert summary["worst_case"] == pytest.approx(-0.05)
        assert summary["average"] == pytest.approx(0.025)
        assert summary["best_sharpe"] == pytest.approx(1.2)
        assert summary["worst_drawdown"] == pytest.approx(-0.20)
        assert summary["scenarios_meeting_target"] == 1
        assert summary["target_achievement_rate"] == pytest.approx(0.5)

    def test_hard_constraints_false(self):
        scenarios = [
            {
                "total_annual_return": 0.10,
                "sharpe_ratio": 1.0,
                "meets_target": False,
                "max_drawdown": -0.10,
                "drawdown_breached": True,
            }
        ]
        summary = _build_summary(scenarios)
        assert summary["hard_constraints"]["annual_return_8pct_met"] is False
        assert summary["hard_constraints"]["drawdown_under_15pct"] is False
        assert summary["hard_constraints"]["all_constraints_met"] is False


class TestForecastAnnualReturn:
    def test_no_files_returns_default_scenarios(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        reports_dir = tmp_path / "reports"
        plan_dir.mkdir()
        reports_dir.mkdir()
        with (
            patch("utils.annual_return_forecast.PLAN_DIR", plan_dir),
            patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir),
        ):
            forecast = forecast_annual_return("2099-01-01")
        assert len(forecast["scenarios"]) == 3
        assert forecast["baseline"]["portfolio_base"] == pytest.approx(5_000_000.0)
        assert "key_risks" in forecast

    def test_with_files(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        reports_dir = tmp_path / "reports"
        plan_dir.mkdir()
        reports_dir.mkdir()
        plan = {
            "capital": 5_000_000.0,
            "hedge_fund_overlays": {
                "theta_engine": {"portfolio_yield_annualized": 0.07}
            },
        }
        report = {
            "portfolio_pnl": {
                "summary": {
                    "total_market_value": 4_000_000.0,
                    "total_cost": 3_800_000.0,
                }
            },
            "hedge_position": {"summary": {"total_premium_budget": 20_000.0}},
            "risk_metrics": {"max_drawdown_pct": -6.0},
        }
        (plan_dir / "trade_plan_20260720.json").write_text(
            json.dumps(plan, ensure_ascii=False)
        )
        (reports_dir / "daily_pnl_report_2026-07-20.json").write_text(
            json.dumps(report, ensure_ascii=False)
        )
        with (
            patch("utils.annual_return_forecast.PLAN_DIR", plan_dir),
            patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir),
        ):
            forecast = forecast_annual_return("2026-07-20")
        assert len(forecast["scenarios"]) == 3
        assert forecast["source"].startswith("trade_plan_20260720.json")
        assert "summary" in forecast
        assert "hard_constraints" in forecast

    def test_drawdown_breach_inserted_first(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        reports_dir = tmp_path / "reports"
        plan_dir.mkdir()
        reports_dir.mkdir()
        plan = {
            "capital": 5_000_000.0,
            "hedge_fund_overlays": {
                "theta_engine": {"portfolio_yield_annualized": 0.0}
            },
        }
        report = {
            "portfolio_pnl": {
                "summary": {
                    "total_market_value": 4_000_000.0,
                    "total_cost": 4_000_000.0,
                }
            },
            "hedge_position": {"summary": {"total_premium_budget": 0.0}},
            "risk_metrics": {"max_drawdown_pct": -20.0},
        }
        (plan_dir / "trade_plan_20260720.json").write_text(
            json.dumps(plan, ensure_ascii=False)
        )
        (reports_dir / "daily_pnl_report_2026-07-20.json").write_text(
            json.dumps(report, ensure_ascii=False)
        )
        with (
            patch("utils.annual_return_forecast.PLAN_DIR", plan_dir),
            patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir),
        ):
            forecast = forecast_annual_return("2026-07-20")
        assert any("回撤红线突破" in r for r in forecast["key_risks"])
        assert forecast["key_risks"][0].startswith("⚠️ 回撤红线突破")


class TestWriteToTradePlan:
    def test_write_success(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        plan_path = plan_dir / "trade_plan_20260720.json"
        plan_path.write_text(json.dumps({"capital": 5_000_000.0}, ensure_ascii=False))
        forecast = {"generated_at": "2026-07-20T10:00:00", "scenarios": []}
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            result = write_to_trade_plan(forecast, "2026-07-20")
        assert result == plan_path
        updated = json.loads(plan_path.read_text(encoding="utf-8"))
        assert updated["annual_return_forecast"] == forecast

    def test_write_failure_missing_plan(self, tmp_path: Path):
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        with patch("utils.annual_return_forecast.PLAN_DIR", plan_dir):
            result = write_to_trade_plan({"scenarios": []}, "2099-01-01")
        assert result is None


class TestPrintForecast:
    def test_prints_without_error(self, capsys: pytest.CaptureFixture):
        forecast = {
            "generated_at": "2026-07-20T10:00:00",
            "source": "plan + report",
            "methodology": "test",
            "target": {
                "annual_return": TARGET_ANNUAL_RETURN,
                "max_drawdown": MAX_DRAWDOWN_LIMIT,
                "rf_rate": RF_RATE,
            },
            "baseline": {
                "portfolio_base": 5_000_000.0,
                "stock_market_value": 4_500_000.0,
                "target_stock_ratio": DEFAULT_TARGET_STOCK_RATIO,
                "cc_premium_annual": 0.065,
                "cash_interest_annual": DEFAULT_CASH_INTEREST,
                "put_premium_cost": 0.0,
            },
            "scenarios": [
                {
                    "name": "中性情景",
                    "spot_annual_return": 0.08,
                    "spot_contribution": 0.072,
                    "hedge_impact": -0.005,
                    "cc_contribution": 0.0585,
                    "cash_interest_annual": 0.0005,
                    "total_annual_return": 0.126,
                    "sharpe_ratio": 1.0,
                    "max_drawdown": -0.12,
                    "meets_target": True,
                    "drawdown_breached": False,
                }
            ],
            "summary": {
                "best_case": 0.15,
                "worst_case": -0.05,
                "average": 0.05,
                "best_sharpe": 1.2,
                "worst_drawdown": -0.20,
                "scenarios_meeting_target": 2,
                "scenarios_total": 3,
                "target_achievement_rate": 0.67,
                "hard_constraints": {
                    "annual_return_8pct_met": True,
                    "drawdown_under_15pct": False,
                    "all_constraints_met": False,
                },
            },
            "key_risks": ["测试风险"],
        }
        print_forecast(forecast)
        captured = capsys.readouterr()
        assert "年化收益测算" in captured.out
        assert "中性情景" in captured.out
        assert "测试风险" in captured.out


class TestMain:
    def test_main_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        plan_dir = tmp_path / "plans"
        reports_dir = tmp_path / "reports"
        plan_dir.mkdir()
        reports_dir.mkdir()
        plan = {
            "capital": 5_000_000.0,
            "hedge_fund_overlays": {
                "theta_engine": {"portfolio_yield_annualized": 0.0}
            },
        }
        report = {
            "portfolio_pnl": {
                "summary": {
                    "total_market_value": 4_000_000.0,
                    "total_cost": 4_000_000.0,
                }
            },
            "hedge_position": {"summary": {"total_premium_budget": 0.0}},
            "risk_metrics": {"max_drawdown_pct": -5.0},
        }
        (plan_dir / "trade_plan_20260720.json").write_text(
            json.dumps(plan, ensure_ascii=False)
        )
        (reports_dir / "daily_pnl_report_2026-07-20.json").write_text(
            json.dumps(report, ensure_ascii=False)
        )
        with (
            patch("utils.annual_return_forecast.PLAN_DIR", plan_dir),
            patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir),
            patch("sys.argv", ["annual_return_forecast.py", "2026-07-20", "--json"]),
        ):
            code = main()
        assert code == 0
        captured = capsys.readouterr()
        assert "保守情景" in captured.out

    def test_main_no_write(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        plan_dir = tmp_path / "plans"
        reports_dir = tmp_path / "reports"
        plan_dir.mkdir()
        reports_dir.mkdir()
        with (
            patch("utils.annual_return_forecast.PLAN_DIR", plan_dir),
            patch("utils.annual_return_forecast.REPORTS_DIR", reports_dir),
            patch("sys.argv", ["annual_return_forecast.py"]),
        ):
            code = main()
        assert code == 0
        captured = capsys.readouterr()
        assert "年化收益测算" in captured.out
