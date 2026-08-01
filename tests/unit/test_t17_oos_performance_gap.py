"""T17: OOS Performance Gap monitoring."""
from datetime import datetime

import pytest

from ms_strategy.src.ml.drift_detector import (
    DriftType,
    ModelDriftDetector,
    Severity,
)


class TestT17UpdateIsIc:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_update_is_ic_records_history(self):
        detector = ModelDriftDetector()
        detector.update_is_ic("v20260728", 0.08)
        assert len(detector._is_ic_history) == 1
        record = detector._is_ic_history[-1]
        assert record['model_version'] == "v20260728"
        assert record['is_ic'] == 0.08
        assert isinstance(record['timestamp'], datetime)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_update_is_ic_multiple_versions(self):
        detector = ModelDriftDetector()
        detector.update_is_ic("v20260701", 0.07)
        detector.update_is_ic("v20260715", 0.08)
        detector.update_is_ic("v20260728", 0.09)
        assert len(detector._is_ic_history) == 3
        assert detector._is_ic_history[-1]['model_version'] == "v20260728"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_is_ic_history_maxlen_10(self):
        detector = ModelDriftDetector()
        for i in range(15):
            detector.update_is_ic(f"v{i}", 0.05 + i * 0.001)
        assert len(detector._is_ic_history) == 10
        assert detector._is_ic_history[0]['model_version'] == "v5"
        assert detector._is_ic_history[-1]['model_version'] == "v14"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_update_oos_ic_insufficient_samples_no_alert(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(5):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.01)
        assert alert is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_update_oos_ic_no_is_record_no_alert(self):
        detector = ModelDriftDetector(oos_window=20)
        for i in range(25):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.01)
        assert alert is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_gap_below_warning_no_alert(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.05)
        for i in range(20):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.03)
        assert alert is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_gap_zero_when_oos_better_than_is(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.05)
        for i in range(20):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.08)
        assert alert is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_gap_at_warning_boundary(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.08)
        for i in range(20):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.05)
        assert alert is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_gap_at_critical_boundary(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(20):
            alert = detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.05)
        assert alert is not None
        assert alert.severity == Severity.WARNING


class TestT17CheckOosGap:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_check_oos_gap_returns_none_without_is(self):
        detector = ModelDriftDetector()
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.01)
        assert detector.check_oos_gap() is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_check_oos_gap_uses_oos_window_mean(self):
        detector = ModelDriftDetector(oos_window=10)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(5):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.01)
        for i in range(5):
            detector.update_oos_ic(f"2026-08-{i+1:02d}", 0.07)
        alert = detector.check_oos_gap()
        assert alert is not None
        assert alert.severity == Severity.CRITICAL

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_get_stats_with_is_only(self):
        detector = ModelDriftDetector()
        detector.update_is_ic("v20260728", 0.08)
        stats = detector.get_oos_gap_stats()
        assert stats['is_ic'] == 0.08
        assert stats['oos_ic_mean'] is None
        assert stats['gap'] is None
        assert stats['n_oos_samples'] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_get_stats_with_is_and_oos(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(25):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.05)
        stats = detector.get_oos_gap_stats()
        assert stats['is_ic'] == 0.10
        assert stats['oos_ic_mean'] == pytest.approx(0.05, abs=1e-6)
        assert stats['gap'] == pytest.approx(0.05, abs=1e-6)
        assert stats['n_oos_samples'] == 25

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_get_stats_uses_window_for_oos_mean(self):
        detector = ModelDriftDetector(oos_window=10)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(20):
            detector.update_oos_ic(f"2026-06-{i+1:02d}", 0.01)
        for i in range(10):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.07)
        stats = detector.get_oos_gap_stats()
        assert stats['oos_ic_mean'] == pytest.approx(0.07, abs=1e-6)
        assert stats['gap'] == pytest.approx(0.03, abs=1e-6)


class TestT17ModelVersionSwitch:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_new_version_resets_gap_calculation(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v1", 0.10)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.03)
        alert_v1 = detector.check_oos_gap()
        assert alert_v1 is not None
        assert alert_v1.severity == Severity.CRITICAL
        detector.update_is_ic("v2", 0.05)
        alert_v2 = detector.check_oos_gap()
        assert alert_v2 is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_alert_message_contains_model_version(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728_special", 0.10)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.02)
        alert = detector.check_oos_gap()
        assert alert is not None
        assert "v20260728_special" in alert.message

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_alert_message_contains_gap_value(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v1", 0.10)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.03)
        alert = detector.check_oos_gap()
        assert alert is not None
        assert "0.07" in alert.message


class TestT17GenerateReport:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_report_contains_oos_gap_stats_key(self):
        detector = ModelDriftDetector()
        report = detector.generate_report()
        assert 'oos_gap_stats' in report

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_report_oos_gap_stats_without_data(self):
        detector = ModelDriftDetector()
        report = detector.generate_report()
        oos_stats = report['oos_gap_stats']
        assert oos_stats['is_ic'] is None
        assert oos_stats['gap'] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t17_report_oos_gap_stats_with_data(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(25):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.05)
        report = detector.generate_report()
        oos_stats = report['oos_gap_stats']
        assert oos_stats['is_ic'] == 0.10
        assert oos_stats['oos_ic_mean'] == pytest.approx(0.05, abs=1e-6)
        assert oos_stats['gap'] == pytest.approx(0.05, abs=1e-6)
        assert oos_stats['n_oos_samples'] == 25

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_report_includes_oos_alert_in_alerts_24h(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.update_is_ic("v20260728", 0.10)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.02)
        report = detector.generate_report()
        oos_alerts = [
            a for a in report["alerts_24h"]
            if a["type"] == DriftType.OOS_PERFORMANCE_GAP.value
        ]
        assert len(oos_alerts) >= 1
        assert oos_alerts[0]["severity"] == Severity.CRITICAL.value


class TestT17CustomThresholds:
    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_custom_warning_threshold(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.oos_gap_warning = 0.02
        detector.oos_gap_critical = 0.05
        detector.update_is_ic("v1", 0.06)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.03)
        alert = detector.check_oos_gap()
        assert alert is not None
        assert alert.severity == Severity.WARNING
        assert alert.threshold == 0.02

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t17_custom_critical_threshold(self):
        detector = ModelDriftDetector(oos_window=20)
        detector.oos_gap_warning = 0.03
        detector.oos_gap_critical = 0.10
        detector.update_is_ic("v1", 0.15)
        for i in range(20):
            detector.update_oos_ic(f"2026-07-{i+1:02d}", 0.06)
        alert = detector.check_oos_gap()
        assert alert is not None
        assert alert.severity == Severity.WARNING
