"""Data Quality Monitor 单元测试.

被测模块: utils/data_quality_monitor.py
覆盖目标: >=90%

测试覆盖:
    1. dataclass: QualityIssue / QualityReport
    2. 构造函数
    3. check_market_data 主入口
    4. 完整性检查 / 缺失值检查 / 异常值检查
    5. 统计异常检测: Z-score / IQR / MAD (含纯 Python 回退)
    6. _percentile_pure
    7. 一致性检查 / 延迟检查 / 时间戳解析
    8. 评分计算 / 摘要生成 / 保存报告
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.data_quality_monitor import (  # noqa: E402
    DataQualityMonitor,
    QualityIssue,
    QualityReport,
)


class TestQualityIssue:
    """QualityIssue dataclass 测试."""

    def test_creation_minimal(self):
        issue = QualityIssue(severity="warning", category="missing", field="close")
        assert issue.severity == "warning"
        assert issue.symbol == ""
        assert issue.value is None

    def test_creation_full(self):
        issue = QualityIssue(
            severity="critical",
            category="outlier",
            field="price",
            symbol="300308",
            description="异常",
            value=999.0,
            expected="<= 100",
        )
        assert issue.symbol == "300308"
        assert issue.value == 999.0


class TestQualityReport:
    """QualityReport dataclass 测试."""

    def test_default_values(self):
        r = QualityReport()
        assert r.issues == []
        assert r.passed is False

    def test_critical_count(self):
        r = QualityReport(
            issues=[
                QualityIssue("critical", "c", "f"),
                QualityIssue("error", "c", "f"),
                QualityIssue("critical", "c", "f"),
            ]
        )
        assert r.critical_count == 2

    def test_error_count(self):
        r = QualityReport(
            issues=[
                QualityIssue("error", "c", "f"),
                QualityIssue("warning", "c", "f"),
            ]
        )
        assert r.error_count == 1

    def test_warning_count(self):
        r = QualityReport(issues=[QualityIssue("warning", "c", "f")])
        assert r.warning_count == 1

    def test_to_dict(self):
        r = QualityReport(total_symbols=5, overall_score=90.0)
        d = r.to_dict()
        assert d["total_symbols"] == 5
        assert d["overall_score"] == 90.0


class TestDataQualityMonitorInit:
    """构造函数测试."""

    def test_default_init(self):
        m = DataQualityMonitor()
        assert m.max_latency_minutes == 30

    def test_custom_latency(self):
        m = DataQualityMonitor(max_latency_minutes=60)
        assert m.max_latency_minutes == 60

    def test_constants(self):
        m = DataQualityMonitor()
        assert m.Z_SCORE_THRESHOLD == 3.0
        assert m.IQR_MULTIPLIER == 1.5
        assert m.MAD_THRESHOLD == 3.5
        assert "close" in m.PRICE_FIELDS
        assert "volume" in m.VOLUME_FIELDS


class TestPercentilePure:
    """_percentile_pure 测试."""

    def test_empty(self):
        assert DataQualityMonitor._percentile_pure([], 50) == 0.0

    def test_single(self):
        assert DataQualityMonitor._percentile_pure([5.0], 50) == 5.0

    def test_median(self):
        result = DataQualityMonitor._percentile_pure([1.0, 2.0, 3.0, 4.0, 5.0], 50)
        assert result == 3.0

    def test_q1(self):
        result = DataQualityMonitor._percentile_pure([1.0, 2.0, 3.0, 4.0, 5.0], 25)
        assert 1.5 <= result <= 2.5

    def test_q3(self):
        result = DataQualityMonitor._percentile_pure([1.0, 2.0, 3.0, 4.0, 5.0], 75)
        assert 3.5 <= result <= 4.5


class TestZScoreOutliers:
    """Z-score 异常检测测试."""

    def test_no_outliers(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        mask = DataQualityMonitor.detect_zscore_outliers(data, threshold=3.0)
        assert sum(mask) == 0

    def test_with_outlier(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 100.0]
        mask = DataQualityMonitor.detect_zscore_outliers(data, threshold=2.0)
        assert any(mask)

    def test_short_sequence(self):
        mask = DataQualityMonitor.detect_zscore_outliers([1.0], threshold=3.0)
        assert sum(mask) == 0

    def test_zero_variance(self):
        data = [5.0, 5.0, 5.0, 5.0]
        mask = DataQualityMonitor.detect_zscore_outliers(data, threshold=3.0)
        assert sum(mask) == 0

    def test_empty(self):
        mask = DataQualityMonitor.detect_zscore_outliers([], threshold=3.0)
        assert len(mask) == 0


class TestIQROutliers:
    """IQR 异常检测测试."""

    def test_no_outliers(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        mask = DataQualityMonitor.detect_iqr_outliers(data, multiplier=1.5)
        assert sum(mask) == 0

    def test_with_outlier(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 100.0]
        mask = DataQualityMonitor.detect_iqr_outliers(data, multiplier=1.5)
        assert any(mask)

    def test_short_sequence(self):
        mask = DataQualityMonitor.detect_iqr_outliers([1.0, 2.0], multiplier=1.5)
        assert sum(mask) == 0

    def test_empty(self):
        mask = DataQualityMonitor.detect_iqr_outliers([], multiplier=1.5)
        assert len(mask) == 0


class TestMADOutliers:
    """MAD 异常检测测试."""

    def test_no_outliers(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        mask = DataQualityMonitor.detect_mad_outliers(data, threshold=3.5)
        assert sum(mask) == 0

    def test_with_outlier(self):
        data = [1.0, 2.0, 3.0, 4.0, 100.0]
        mask = DataQualityMonitor.detect_mad_outliers(data, threshold=2.0)
        assert any(mask)

    def test_short_sequence(self):
        mask = DataQualityMonitor.detect_mad_outliers([1.0], threshold=3.5)
        assert sum(mask) == 0

    def test_zero_mad(self):
        data = [5.0, 5.0, 5.0, 5.0]
        mask = DataQualityMonitor.detect_mad_outliers(data, threshold=3.5)
        assert sum(mask) == 0

    def test_empty(self):
        mask = DataQualityMonitor.detect_mad_outliers([], threshold=3.5)
        assert len(mask) == 0


class TestCheckCompleteness:
    """完整性检查测试."""

    def test_no_expected_symbols(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_completeness({"A": {"close": 1}}, None, report)
        assert len(report.issues) == 0

    def test_missing_symbols(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_completeness({"A": {"close": 1}}, ["A", "B", "C"], report)
        assert report.critical_count == 2

    def test_all_present(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_completeness(
            {"A": {"close": 1}, "B": {"close": 2}}, ["A", "B"], report
        )
        assert report.critical_count == 0
        assert report.checked_fields == 1


class TestCheckMissingValues:
    """缺失值检查测试."""

    def test_no_missing(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_missing_values({"A": {"close": 10.0, "open": 5.0}}, report)
        assert len(report.issues) == 0

    def test_required_field_missing(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_missing_values({"A": {"open": 5.0}}, report)
        assert report.error_count == 1

    def test_required_field_nan(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_missing_values({"A": {"close": float("nan")}}, report)
        assert report.error_count == 1

    def test_optional_field_missing(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_missing_values({"A": {"close": 10.0, "volume": None}}, report)
        assert report.warning_count == 1


class TestCheckOutliers:
    """异常值检查测试."""

    def test_high_low_logic_error(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_outliers({"A": {"high": 10.0, "low": 20.0, "close": 15.0}}, report)
        assert any(
            i.category == "outlier" and "high" in i.description for i in report.issues
        )

    def test_negative_close(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_outliers({"A": {"close": -5.0, "high": 10.0, "low": 1.0}}, report)
        assert any(i.category == "outlier" for i in report.issues)

    def test_negative_volume(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_outliers(
            {"A": {"close": 10.0, "high": 12.0, "low": 8.0, "volume": -100}}, report
        )
        assert any(i.category == "outlier" for i in report.issues)

    def test_normal_data(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_outliers(
            {"A": {"close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000}}, report
        )
        assert len(report.issues) == 0


class TestCheckConsistency:
    """一致性检查测试."""

    def test_normal_data(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_consistency({"A": {"close": 10.0, "high": 12.0, "low": 8.0}}, report)
        assert len(report.issues) == 0

    def test_close_outside_range(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_consistency({"A": {"close": 15.0, "high": 12.0, "low": 8.0}}, report)
        assert any(i.category == "consistency" for i in report.issues)


class TestParseTimestamp:
    """时间戳解析测试."""

    def test_datetime_object(self):
        m = DataQualityMonitor()
        dt = datetime(2026, 8, 14, 10, 30)
        assert m._parse_timestamp(dt) == dt

    def test_date_object(self):
        m = DataQualityMonitor()
        d = date(2026, 8, 14)
        result = m._parse_timestamp(d)
        assert result is not None
        assert result.date() == d

    def test_string_format_1(self):
        m = DataQualityMonitor()
        result = m._parse_timestamp("2026-08-14 10:30:00")
        assert result is not None
        assert result.year == 2026

    def test_string_format_2(self):
        m = DataQualityMonitor()
        result = m._parse_timestamp("2026-08-14")
        assert result is not None

    def test_string_format_3(self):
        m = DataQualityMonitor()
        result = m._parse_timestamp("20260814")
        assert result is not None

    def test_string_format_iso(self):
        m = DataQualityMonitor()
        result = m._parse_timestamp("2026-08-14T10:30:00")
        assert result is not None

    def test_invalid_format(self):
        m = DataQualityMonitor()
        assert m._parse_timestamp("invalid") is None


class TestCheckLatency:
    """延迟检查测试."""

    def test_no_timestamp(self):
        m = DataQualityMonitor()
        report = QualityReport()
        m._check_latency({"A": {"close": 10}}, "timestamp", report)
        assert len(report.issues) == 0

    def test_fresh_data(self):
        m = DataQualityMonitor()
        report = QualityReport()
        ts = now_bj().isoformat()
        m._check_latency({"A": {"close": 10, "timestamp": ts}}, "timestamp", report)
        assert len(report.issues) == 0

    def test_stale_data(self):
        m = DataQualityMonitor(max_latency_minutes=30)
        report = QualityReport()
        ts = (now_bj() - timedelta(hours=3)).isoformat()
        m._check_latency({"A": {"close": 10, "timestamp": ts}}, "timestamp", report)
        assert report.critical_count == 1

    def test_warning_latency(self):
        m = DataQualityMonitor(max_latency_minutes=30)
        report = QualityReport()
        ts = (now_bj() - timedelta(minutes=60)).isoformat()
        m._check_latency({"A": {"close": 10, "timestamp": ts}}, "timestamp", report)
        assert report.warning_count == 1


class TestCalculateScores:
    """评分计算测试."""

    def test_perfect_scores(self):
        m = DataQualityMonitor()
        report = QualityReport(total_symbols=5, checked_fields=10)
        m._calculate_scores(report)
        assert report.completeness_score == 100
        assert report.consistency_score == 100
        assert report.freshness_score == 100
        assert report.overall_score == 100

    def test_with_critical_completeness(self):
        m = DataQualityMonitor()
        report = QualityReport(
            issues=[
                QualityIssue("critical", "completeness", "f"),
                QualityIssue("critical", "completeness", "f"),
            ]
        )
        m._calculate_scores(report)
        assert report.completeness_score == 60

    def test_with_consistency_issues(self):
        m = DataQualityMonitor()
        report = QualityReport(
            issues=[
                QualityIssue("error", "consistency", "f"),
                QualityIssue("error", "outlier", "f"),
            ]
        )
        m._calculate_scores(report)
        assert report.consistency_score == 80

    def test_with_latency_issues(self):
        m = DataQualityMonitor()
        report = QualityReport(issues=[QualityIssue("warning", "latency", "f")])
        m._calculate_scores(report)
        assert report.freshness_score == 85


class TestBuildSummary:
    """摘要生成测试."""

    def test_basic_summary(self):
        m = DataQualityMonitor()
        report = QualityReport(total_symbols=5, checked_fields=10, overall_score=90.0)
        summary = m._build_summary(report)
        assert "数据质量报告" in summary
        assert "5" in summary

    def test_with_issues(self):
        m = DataQualityMonitor()
        report = QualityReport(
            issues=[QualityIssue("critical", "completeness", "symbol", symbol="A")]
        )
        summary = m._build_summary(report)
        assert "问题明细" in summary

    def test_many_issues_truncated(self):
        m = DataQualityMonitor()
        issues = [QualityIssue("warning", "missing", f"f{i}") for i in range(25)]
        report = QualityReport(issues=issues)
        summary = m._build_summary(report)
        assert "未显示" in summary


class TestSaveReport:
    """保存报告测试."""

    def test_save_success(self, tmp_path):
        m = DataQualityMonitor()
        report = QualityReport(total_symbols=5, overall_score=90.0)
        with patch("utils.data_quality_monitor.REPORT_DIR", tmp_path):
            path = m.save_report(report)
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["total_symbols"] == 5


class TestCheckMarketData:
    """check_market_data 主入口测试."""

    def test_empty_data(self):
        m = DataQualityMonitor()
        report = m.check_market_data({})
        assert report.total_symbols == 0
        assert isinstance(report.summary, str)

    def test_normal_data(self):
        m = DataQualityMonitor()
        ts = now_bj().isoformat()
        data = {
            "A": {
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 1000,
                "timestamp": ts,
            },
            "B": {
                "open": 20,
                "high": 22,
                "low": 19,
                "close": 21,
                "volume": 2000,
                "timestamp": ts,
            },
        }
        report = m.check_market_data(data)
        assert report.total_symbols == 2
        assert report.overall_score > 0

    def test_with_expected_symbols(self):
        m = DataQualityMonitor()
        data = {"A": {"close": 10}}
        report = m.check_market_data(data, expected_symbols=["A", "B"])
        assert report.critical_count == 1

    def test_passed_flag(self):
        m = DataQualityMonitor()
        ts = now_bj().isoformat()
        data = {
            "A": {
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 1000,
                "timestamp": ts,
            },
            "B": {
                "open": 20,
                "high": 22,
                "low": 19,
                "close": 21,
                "volume": 2000,
                "timestamp": ts,
            },
            "C": {
                "open": 30,
                "high": 32,
                "low": 29,
                "close": 31,
                "volume": 3000,
                "timestamp": ts,
            },
            "D": {
                "open": 40,
                "high": 42,
                "low": 39,
                "close": 41,
                "volume": 4000,
                "timestamp": ts,
            },
        }
        report = m.check_market_data(data)
        assert isinstance(report.passed, bool)

    def test_with_bad_data(self):
        m = DataQualityMonitor()
        data = {
            "A": {"high": 10, "low": 20, "close": 15},
        }
        report = m.check_market_data(data)
        assert report.error_count > 0
