"""performance_report 单元测试 (Wave 12-A #5).

被测模块: reporting/performance_report.py
验收门禁: 标准绩效指标(Sharpe/Sortino/MaxDD/Calmar) / HTML报告可生成 / 单测覆盖
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from reporting.performance_report import PerformanceReporter  # noqa: E402

_RNG = np.random.default_rng(42)
_POSITIVE_RETURNS = _RNG.normal(0.001, 0.01, 252)
_NEGATIVE_RETURNS = _RNG.normal(-0.001, 0.01, 252)
_MIXED_RETURNS = np.array([0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02, 0.01, -0.01] * 25)
_BENCHMARK = _RNG.normal(0.0005, 0.008, 252)


class TestCalculateMetrics:
    """calculate_metrics 方法测试."""

    def test_returns_dict(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_POSITIVE_RETURNS)
        assert isinstance(result, dict)

    def test_has_standard_metrics(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_POSITIVE_RETURNS)
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert "max_drawdown" in result
        assert "calmar_ratio" in result
        assert "annual_return" in result
        assert "annual_volatility" in result

    def test_empty_returns(self):
        r = PerformanceReporter()
        result = r.calculate_metrics([])
        assert "error" in result

    def test_win_rate(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_MIXED_RETURNS)
        assert 0 <= result["win_rate"] <= 1
        assert result["positive_periods"] + result.get("negative_periods", 0) <= result["n_periods"]

    def test_max_drawdown_non_positive(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_MIXED_RETURNS)
        assert result["max_drawdown"] <= 0

    def test_with_benchmark(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_POSITIVE_RETURNS, benchmark=_BENCHMARK)
        assert "alpha" in result
        assert "beta" in result
        assert "information_ratio" in result

    def test_total_return(self):
        r = PerformanceReporter()
        rets = np.array([0.1, 0.1])
        result = r.calculate_metrics(rets)
        expected = (1.1 * 1.1) - 1
        assert abs(result["total_return"] - expected) < 1e-6

    def test_n_periods(self):
        r = PerformanceReporter()
        result = r.calculate_metrics(_POSITIVE_RETURNS)
        assert result["n_periods"] == 252


class TestGenerateHtmlReport:
    """generate_html_report 方法测试."""

    def test_returns_html_string(self):
        r = PerformanceReporter()
        html = r.generate_html_report(_POSITIVE_RETURNS)
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_title(self):
        r = PerformanceReporter()
        html = r.generate_html_report(_POSITIVE_RETURNS, title="测试报告")
        assert "测试报告" in html

    def test_contains_metrics_table(self):
        r = PerformanceReporter()
        html = r.generate_html_report(_POSITIVE_RETURNS)
        assert "<table>" in html
        assert "Sharpe" in html
        assert "Sortino" in html
        assert "最大回撤" in html
        assert "Calmar" in html

    def test_contains_chart_script(self):
        r = PerformanceReporter()
        html = r.generate_html_report(_POSITIVE_RETURNS)
        assert "<script>" in html
        assert "cumData" in html

    def test_dark_theme(self):
        r = PerformanceReporter()
        html = r.generate_html_report(_POSITIVE_RETURNS)
        assert "#0d1117" in html
        assert "#1890FF" in html


class TestGenerateSummary:
    """generate_summary 方法测试."""

    def test_returns_string(self):
        r = PerformanceReporter()
        summary = r.generate_summary(_POSITIVE_RETURNS)
        assert isinstance(summary, str)

    def test_contains_key_metrics(self):
        r = PerformanceReporter()
        summary = r.generate_summary(_POSITIVE_RETURNS)
        assert "Sharpe" in summary
        assert "Sortino" in summary
        assert "最大回撤" in summary
        assert "Calmar" in summary
        assert "胜率" in summary

    def test_empty_returns_error(self):
        r = PerformanceReporter()
        summary = r.generate_summary([])
        assert "失败" in summary


class TestManualFallback:
    """手动降级计算测试."""

    def test_manual_calculation(self):
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "reporting.performance_report._EMPYRICAL_AVAILABLE", False
        ):
            r = PerformanceReporter()
            result = r.calculate_metrics(_POSITIVE_RETURNS)
            assert "sharpe_ratio" in result
            assert "annual_return" in result
            assert isinstance(result["sharpe_ratio"], float)


class TestEdgeCases:
    """边界情况测试."""

    def test_all_positive_returns(self):
        r = PerformanceReporter()
        rets = np.array([0.01, 0.02, 0.03, 0.01])
        result = r.calculate_metrics(rets)
        assert result["win_rate"] == 1.0
        assert result["max_drawdown"] == 0.0

    def test_all_negative_returns(self):
        r = PerformanceReporter()
        rets = np.array([-0.01, -0.02, -0.03, -0.01])
        result = r.calculate_metrics(rets)
        assert result["win_rate"] == 0.0
        assert result["max_drawdown"] < 0

    def test_single_period(self):
        r = PerformanceReporter()
        rets = np.array([0.05])
        result = r.calculate_metrics(rets)
        assert result["n_periods"] == 1
        assert result["win_rate"] == 1.0
