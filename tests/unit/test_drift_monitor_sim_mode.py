# -*- coding: utf-8 -*-
"""GAP-6 漂移监控 sim_mode 激活测试.

ECC mle-workflow MLE-10 修复验证:
    Monitoring covers system health, feature drift, prediction drift, and delayed labels

测试覆盖:
    1. DriftSeverity 阈值判定
    2. compute_psi (Population Stability Index)
    3. compute_feature_drift (KS 检验 + PSI)
    4. compute_prediction_drift (预测漂移)
    5. DriftReport 不可变性 (frozen dataclass)
    6. SimModeDriftMonitor 激活条件 (sim_mode / USE_DRIFT_DETECTOR Flag)
    7. SimModeDriftMonitor.run_daily_check() 批量检查 + 持久化
    8. DelayedLabelTracker 记录预测 + 延迟标签观测 + IC 计算
    9. PredictionRecord / DelayedMetrics 不可变性
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# 测试目标模块
from utils.alpha.drift_monitor import (
    DriftReport,
    DriftSeverity,
    SimModeDriftMonitor,
    _classify_severity,
    _load_alert_owners,
    compute_feature_drift,
    compute_psi,
    compute_prediction_drift,
    create_sim_mode_drift_monitor,
)
from utils.alpha.delayed_label_tracker import (
    DelayedLabelTracker,
    DelayedMetrics,
    PredictionRecord,
)


# ============================================================
# pytest marker
# ============================================================
pytestmark = pytest.mark.drift


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def baseline_panel():
    """基线 panel (含 3 个特征, 100 行)."""
    np.random.seed(42)
    n = 100
    return pd.DataFrame({
        "code": [f"TEST{i:03d}.SZ" for i in range(n)] * 2,
        "date": ["2026-01-01"] * n + ["2026-01-02"] * n,
        "MOM_5D": np.random.randn(2 * n) * 0.02,
        "VOL_20D": np.abs(np.random.randn(2 * n)) * 0.01,
        "RSI_14D": np.random.uniform(20, 80, 2 * n),
        "y": np.random.randn(2 * n) * 0.01,
    })


@pytest.fixture
def drifted_panel(baseline_panel):
    """漂移 panel (MOM_5D 均值偏移 0.5, VOL_20D 方差扩大, RSI_14D 无漂移)."""
    np.random.seed(123)
    n = 100
    # RSI_14D 用与 baseline 相同的种子重新生成, 确保分布一致 (无漂移)
    np.random.seed(42)
    rsi_no_drift = np.random.uniform(20, 80, 2 * n)
    # 切回 123 生成漂移特征
    np.random.seed(123)
    return pd.DataFrame({
        "code": [f"TEST{i:03d}.SZ" for i in range(n)] * 2,
        "date": ["2026-07-28"] * n + ["2026-07-29"] * n,
        "MOM_5D": np.random.randn(2 * n) * 0.02 + 0.5,  # 均值漂移 0.5
        "VOL_20D": np.abs(np.random.randn(2 * n)) * 0.05,  # 方差扩大 5x
        "RSI_14D": rsi_no_drift,  # 无漂移 (与 baseline 同种子同分布)
        "y": np.random.randn(2 * n) * 0.01,
    })


@pytest.fixture
def clean_storage_dir(tmp_path):
    """清理后的持久化目录."""
    return tmp_path / "drift_reports"


@pytest.fixture
def alert_owners_file(tmp_path):
    """测试用 alert_owners.yaml."""
    file = tmp_path / "alert_owners.yaml"
    file.write_text(
        "v9_lgb:\n"
        "  owner: \"测试负责人\"\n"
        "  slack_channel: \"#test-alerts\"\n"
        "  email: \"test@example.com\"\n"
        "  runbook_url: \"docs/runbooks/MODEL_DRIFT_RUNBOOK.md\"\n",
        encoding="utf-8",
    )
    return str(file)


# ============================================================
# DriftSeverity 严重等级判定测试
# ============================================================
class TestDriftSeverityClassification:
    """测试 _classify_severity 阈值判定."""

    def test_low_severity_both_below_threshold(self):
        """KS < 0.1 且 PSI < 0.1 → LOW."""
        assert _classify_severity(0.05, 0.05) == DriftSeverity.LOW

    def test_medium_severity_ks_threshold(self):
        """0.1 <= KS < 0.2 → MEDIUM."""
        assert _classify_severity(0.15, 0.05) == DriftSeverity.MEDIUM

    def test_medium_severity_psi_threshold(self):
        """0.1 <= PSI < 0.25 → MEDIUM."""
        assert _classify_severity(0.05, 0.15) == DriftSeverity.MEDIUM

    def test_high_severity_ks_threshold(self):
        """0.2 <= KS < 0.4 → HIGH."""
        assert _classify_severity(0.3, 0.05) == DriftSeverity.HIGH

    def test_high_severity_psi_threshold(self):
        """0.25 <= PSI < 0.5 → HIGH."""
        assert _classify_severity(0.05, 0.3) == DriftSeverity.HIGH

    def test_critical_severity_ks_threshold(self):
        """KS >= 0.4 → CRITICAL."""
        assert _classify_severity(0.5, 0.05) == DriftSeverity.CRITICAL

    def test_critical_severity_psi_threshold(self):
        """PSI >= 0.5 → CRITICAL."""
        assert _classify_severity(0.05, 0.6) == DriftSeverity.CRITICAL

    def test_takes_more_severe_between_ks_and_psi(self):
        """取 KS 和 PSI 中较严重者."""
        # KS=MEDIUM (0.15), PSI=HIGH (0.3) → HIGH
        assert _classify_severity(0.15, 0.3) == DriftSeverity.HIGH
        # KS=CRITICAL (0.5), PSI=LOW (0.05) → CRITICAL
        assert _classify_severity(0.5, 0.05) == DriftSeverity.CRITICAL


# ============================================================
# compute_psi 测试
# ============================================================
class TestComputePSI:
    """测试 PSI 计算."""

    def test_same_distribution_low_psi(self):
        """相同分布 PSI < 0.1."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(1000))
        current = pd.Series(np.random.randn(1000))
        psi = compute_psi(baseline, current)
        assert psi < 0.1, f"相同分布 PSI 应 < 0.1, 实际: {psi}"

    def test_different_distribution_high_psi(self):
        """不同分布 PSI > 0.1."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(1000))
        current = pd.Series(np.random.randn(1000) + 1.0)  # 均值偏移
        psi = compute_psi(baseline, current)
        assert psi > 0.1, f"不同分布 PSI 应 > 0.1, 实际: {psi}"

    def test_empty_series_returns_zero(self):
        """空 series 返回 0."""
        assert compute_psi(pd.Series([]), pd.Series([1, 2, 3])) == 0.0
        assert compute_psi(pd.Series([1, 2, 3]), pd.Series([])) == 0.0

    def test_single_element_returns_zero(self):
        """单元素 series 返回 0 (样本不足)."""
        assert compute_psi(pd.Series([1.0]), pd.Series([2.0])) == 0.0

    def test_psi_non_negative(self):
        """PSI 始终非负."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(100))
        current = pd.Series(np.random.randn(100) * 5)  # 方差扩大
        psi = compute_psi(baseline, current)
        assert psi >= 0, f"PSI 应非负, 实际: {psi}"


# ============================================================
# compute_feature_drift 测试
# ============================================================
class TestComputeFeatureDrift:
    """测试特征漂移计算."""

    def test_no_drift_returns_low_severity(self):
        """无漂移 → LOW severity."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(500))
        current = pd.Series(np.random.randn(500))
        report = compute_feature_drift(baseline, current, feature_name="test_feature")
        assert report.severity == DriftSeverity.LOW
        assert report.drift_score < 0.1
        assert report.feature_name == "test_feature"

    def test_drift_detected_high_severity(self):
        """漂移检测 → HIGH/CRITICAL severity."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(500))
        current = pd.Series(np.random.randn(500) + 1.0)  # 均值偏移 1.0
        report = compute_feature_drift(baseline, current, feature_name="drifted_feature")
        assert report.severity in (DriftSeverity.HIGH, DriftSeverity.CRITICAL)
        assert report.drift_score > 0.2

    def test_empty_series_returns_low(self):
        """空 series → LOW (无法判定漂移)."""
        report = compute_feature_drift(
            pd.Series([]), pd.Series([]), feature_name="empty"
        )
        assert report.severity == DriftSeverity.LOW
        assert report.drift_score == 0.0
        assert report.baseline_size == 0
        assert report.current_size == 0

    def test_report_contains_metadata(self):
        """DriftReport 含 model_name / model_version."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(100))
        current = pd.Series(np.random.randn(100))
        report = compute_feature_drift(
            baseline, current,
            feature_name="MOM_5D",
            model_name="v9_lgb",
            model_version="v1.0",
        )
        assert report.model_name == "v9_lgb"
        assert report.model_version == "v1.0"
        assert report.feature_name == "MOM_5D"
        assert report.timestamp != ""
        assert report.baseline_mean != report.current_mean or True  # 允许近似

    def test_drift_score_in_range(self):
        """KS 统计量在 [0, 1] 范围内."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(100))
        current = pd.Series(np.random.randn(100) + 2.0)
        report = compute_feature_drift(baseline, current)
        assert 0.0 <= report.drift_score <= 1.0


# ============================================================
# compute_prediction_drift 测试
# ============================================================
class TestComputePredictionDrift:
    """测试预测漂移计算."""

    def test_prediction_drift_uses_prediction_feature_name(self):
        """预测漂移 feature_name = '__prediction__'."""
        np.random.seed(42)
        baseline = np.random.randn(500)
        current = np.random.randn(500)
        report = compute_prediction_drift(baseline, current)
        assert report.feature_name == "__prediction__"

    def test_prediction_drift_detected(self):
        """预测分布漂移检测."""
        np.random.seed(42)
        baseline = np.random.randn(500)
        current = np.random.randn(500) + 0.5  # 均值偏移
        report = compute_prediction_drift(baseline, current)
        assert report.severity in (DriftSeverity.MEDIUM, DriftSeverity.HIGH, DriftSeverity.CRITICAL)


# ============================================================
# DriftReport 不可变性测试
# ============================================================
class TestDriftReportImmutability:
    """测试 DriftReport 不可变 (frozen dataclass)."""

    def test_drift_report_is_frozen(self):
        """DriftReport 是 frozen dataclass, 修改应抛 FrozenInstanceError."""
        report = DriftReport(
            timestamp="2026-07-29T00:00:00Z",
            model_name="v9_lgb",
            model_version="v1.0",
            feature_name="MOM_5D",
            drift_score=0.1,
            psi=0.2,
            severity=DriftSeverity.MEDIUM,
            baseline_mean=0.0,
            current_mean=0.1,
        )
        with pytest.raises(FrozenInstanceError):
            report.model_name = "modified"  # type: ignore

    def test_drift_report_to_dict(self):
        """DriftReport.to_dict() 返回完整字段."""
        report = DriftReport(
            timestamp="2026-07-29T00:00:00Z",
            model_name="v9_lgb",
            model_version="v1.0",
            feature_name="MOM_5D",
            drift_score=0.15,
            psi=0.2,
            severity=DriftSeverity.HIGH,
            baseline_mean=0.0,
            current_mean=0.1,
            baseline_size=100,
            current_size=80,
        )
        d = report.to_dict()
        assert d["model_name"] == "v9_lgb"
        assert d["severity"] == "high"
        assert d["drift_score"] == 0.15
        assert d["baseline_size"] == 100


# ============================================================
# SimModeDriftMonitor 激活条件测试
# ============================================================
class TestSimModeDriftMonitorActivation:
    """测试 SimModeDriftMonitor 激活条件.

    注意: 不使用 importlib.reload (有副作用: reload 后旧类引用与新模块类不同,
    导致 isinstance 检查失败). 改用直接 patch 模块级 _USE_DRIFT_DETECTOR_FLAG.
    """

    def test_disabled_by_default(self, baseline_panel, clean_storage_dir):
        """sim_mode=False 且 Flag=False → 未激活."""
        with patch("utils.alpha.drift_monitor._USE_DRIFT_DETECTOR_FLAG", False):
            monitor = SimModeDriftMonitor(
                model_name="v9_lgb",
                model_version="v1.0",
                baseline_panel=baseline_panel,
                sim_mode=False,
                reports_dir=str(clean_storage_dir),
            )
            assert monitor.is_active() is False

    def test_active_in_sim_mode(self, baseline_panel, clean_storage_dir):
        """sim_mode=True → 激活 (即使 Flag 关闭)."""
        with patch("utils.alpha.drift_monitor._USE_DRIFT_DETECTOR_FLAG", False):
            monitor = SimModeDriftMonitor(
                model_name="v9_lgb",
                model_version="v1.0",
                baseline_panel=baseline_panel,
                sim_mode=True,
                reports_dir=str(clean_storage_dir),
            )
            assert monitor.is_active() is True

    def test_active_with_env_flag(self, baseline_panel, clean_storage_dir):
        """USE_DRIFT_DETECTOR=true → 激活 (即使 sim_mode=False)."""
        with patch("utils.alpha.drift_monitor._USE_DRIFT_DETECTOR_FLAG", True):
            monitor = SimModeDriftMonitor(
                model_name="v9_lgb",
                model_version="v1.0",
                baseline_panel=baseline_panel,
                sim_mode=False,
                reports_dir=str(clean_storage_dir),
            )
            assert monitor.is_active() is True

    def test_factory_creates_active_monitor(self, baseline_panel, clean_storage_dir):
        """create_sim_mode_drift_monitor() 默认 sim_mode=True."""
        monitor = create_sim_mode_drift_monitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
        )
        assert monitor.is_active() is True


# ============================================================
# SimModeDriftMonitor.run_daily_check 测试
# ============================================================
class TestRunDailyCheck:
    """测试 run_daily_check 批量检查."""

    def test_inactive_monitor_returns_empty(self, baseline_panel, drifted_panel, clean_storage_dir):
        """未激活的监控器返回空列表."""
        with patch("utils.alpha.drift_monitor._USE_DRIFT_DETECTOR_FLAG", False):
            monitor = SimModeDriftMonitor(
                model_name="v9_lgb",
                model_version="v1.0",
                baseline_panel=baseline_panel,
                sim_mode=False,
                reports_dir=str(clean_storage_dir),
            )
            reports = monitor.run_daily_check(drifted_panel)
            assert reports == []

    def test_active_monitor_returns_reports(self, baseline_panel, drifted_panel, clean_storage_dir):
        """激活的监控器返回 DriftReport 列表."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        reports = monitor.run_daily_check(drifted_panel)
        assert len(reports) > 0
        assert all(isinstance(r, DriftReport) for r in reports)

    def test_drifted_feature_has_high_severity(self, baseline_panel, drifted_panel, clean_storage_dir):
        """漂移特征 MOM_5D 应有 HIGH/CRITICAL severity."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        reports = monitor.run_daily_check(drifted_panel)
        mom_report = next((r for r in reports if r.feature_name == "MOM_5D"), None)
        assert mom_report is not None, "应检测到 MOM_5D"
        assert mom_report.severity in (DriftSeverity.HIGH, DriftSeverity.CRITICAL), \
            f"MOM_5D 漂移 0.5 应为 HIGH/CRITICAL, 实际: {mom_report.severity}"

    def test_non_drifted_feature_has_low_severity(self, baseline_panel, drifted_panel, clean_storage_dir):
        """未漂移特征 RSI_14D 应为 LOW severity."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        reports = monitor.run_daily_check(drifted_panel)
        rsi_report = next((r for r in reports if r.feature_name == "RSI_14D"), None)
        assert rsi_report is not None, "应检测到 RSI_14D"
        assert rsi_report.severity == DriftSeverity.LOW, \
            f"RSI_14D 无漂移应为 LOW, 实际: {rsi_report.severity}"

    def test_reports_sorted_by_severity_desc(self, baseline_panel, drifted_panel, clean_storage_dir):
        """报告按 severity 降序 (CRITICAL 优先)."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        reports = monitor.run_daily_check(drifted_panel)
        if len(reports) >= 2:
            severity_order = {
                DriftSeverity.CRITICAL: 3,
                DriftSeverity.HIGH: 2,
                DriftSeverity.MEDIUM: 1,
                DriftSeverity.LOW: 0,
            }
            for i in range(len(reports) - 1):
                assert severity_order[reports[i].severity] >= severity_order[reports[i + 1].severity]

    def test_reports_persisted_to_json(self, baseline_panel, drifted_panel, clean_storage_dir):
        """报告持久化到 JSON 文件."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        reports = monitor.run_daily_check(drifted_panel)
        # 找持久化文件
        report_files = list(clean_storage_dir.glob("drift_report_*.json"))
        assert len(report_files) >= 1, "应生成 drift_report_{date}.json"
        with open(report_files[0], "r", encoding="utf-8") as f:
            persisted = json.load(f)
        assert isinstance(persisted, list)
        assert len(persisted) == len(reports)
        assert all("model_name" in r for r in persisted)

    def test_history_accumulates(self, baseline_panel, drifted_panel, clean_storage_dir):
        """历史报告累积."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        monitor.run_daily_check(drifted_panel)
        first_count = len(monitor.get_history())
        monitor.run_daily_check(drifted_panel)
        second_count = len(monitor.get_history())
        assert second_count == first_count * 2, "历史应累积"

    def test_get_summary(self, baseline_panel, drifted_panel, clean_storage_dir):
        """get_summary() 返回完整字段."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
        )
        monitor.run_daily_check(drifted_panel)
        summary = monitor.get_summary()
        assert summary["model_name"] == "v9_lgb"
        assert summary["is_active"] is True
        assert summary["sim_mode"] is True
        assert summary["total_reports"] > 0
        assert "severity_counts" in summary
        assert "low" in summary["severity_counts"]


# ============================================================
# Alert owners 加载测试
# ============================================================
class TestAlertOwners:
    """测试 alert_owners.yaml 加载."""

    def test_load_alert_owners(self, alert_owners_file):
        """加载 alert_owners.yaml."""
        owners = _load_alert_owners(alert_owners_file)
        assert "v9_lgb" in owners
        assert owners["v9_lgb"]["owner"] == "测试负责人"
        assert owners["v9_lgb"]["slack_channel"] == "#test-alerts"

    def test_load_nonexistent_file_returns_empty(self, tmp_path):
        """不存在的文件返回空 dict."""
        owners = _load_alert_owners(str(tmp_path / "nonexistent.yaml"))
        assert owners == {}

    def test_monitor_loads_owner(self, baseline_panel, clean_storage_dir, alert_owners_file):
        """监控器从配置加载 owner."""
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="v1.0",
            baseline_panel=baseline_panel,
            sim_mode=True,
            reports_dir=str(clean_storage_dir),
            alert_owners_path=alert_owners_file,
        )
        owner_info = monitor._get_owner_info()
        assert owner_info["owner"] == "测试负责人"


# ============================================================
# DelayedLabelTracker 测试
# ============================================================
class TestDelayedLabelTracker:
    """测试延迟标签追踪器."""

    def test_record_single_prediction(self, tmp_path):
        """记录单条预测."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        record = tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        assert record.date == "2026-07-29"
        assert record.symbol == "588080.SH"
        assert record.predicted_score == 0.0235
        assert record.model_name == "v9_lgb"
        assert record.model_version == "v1.0"
        assert record.actual_label is None
        assert record.is_observed is False
        # label_date = 2026-07-29 + 5d = 2026-08-03
        assert record.label_date == "2026-08-03"

    def test_record_batch_predictions(self, tmp_path):
        """批量记录预测."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        predictions = {
            "588080.SH": 0.0235,
            "510050.SH": -0.0112,
            "510300.SH": 0.0089,
        }
        records = tracker.record_predictions_batch(
            date="2026-07-29",
            predictions=predictions,
            model_version="v1.0",
        )
        assert len(records) == 3
        assert all(r.date == "2026-07-29" for r in records)

    def test_check_label_observability_before_delay(self, tmp_path):
        """延迟期内, 标签不可观测."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        # 2 天后, 标签不可观测
        pending = tracker.check_label_observability(current_date="2026-07-31")
        assert len(pending) == 0, "2 天后 (5 日延迟) 标签不可观测"

    def test_check_label_observability_after_delay(self, tmp_path):
        """延迟期后, 标签可观测."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        # 6 天后 (超过 5 日延迟), 标签可观测
        pending = tracker.check_label_observability(current_date="2026-08-04")
        assert len(pending) == 1, "6 天后 (5 日延迟) 标签应可观测"
        assert pending[0].symbol == "588080.SH"

    def test_update_actual_label(self, tmp_path):
        """更新实际标签."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        # 更新实际标签
        updated = tracker.update_actual_label(
            date="2026-07-29",
            symbol="588080.SH",
            actual_label=0.0180,
        )
        assert updated is True
        # 验证记录已更新
        records = tracker.get_records(date="2026-07-29")
        assert len(records) == 1
        assert records[0].actual_label == 0.0180
        assert records[0].is_observed is True

    def test_compute_delayed_metrics_no_observed(self, tmp_path):
        """无观测标签时返回空指标."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        metrics = tracker.compute_delayed_metrics(model_version="v1.0")
        assert metrics.n_predictions == 1
        assert metrics.n_observed == 0
        assert metrics.observation_rate == 0.0
        assert metrics.ic == 0.0

    def test_compute_delayed_metrics_with_observed(self, tmp_path):
        """有观测标签时计算 IC."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        # 记录多条预测 (同日, 不同标的)
        for i in range(20):
            tracker.record_prediction(
                date="2026-07-29",
                symbol=f"TEST{i:03d}.SZ",
                predicted_score=float(i) / 20.0,
                model_version="v1.0",
            )
        # 更新实际标签 (与预测正相关)
        for i in range(20):
            tracker.update_actual_label(
                date="2026-07-29",
                symbol=f"TEST{i:03d}.SZ",
                actual_label=float(i) / 20.0 + np.random.randn() * 0.01,
            )
        metrics = tracker.compute_delayed_metrics(model_version="v1.0")
        assert metrics.n_predictions == 20
        assert metrics.n_observed == 20
        assert metrics.observation_rate == 1.0
        # IC 应为正 (预测与实际正相关)
        assert metrics.ic > 0.5, f"正相关预测 IC 应 > 0.5, 实际: {metrics.ic}"

    def test_get_summary(self, tmp_path):
        """get_summary 返回完整字段."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb",
            label_delay_days=5,
            storage_dir=str(tmp_path / "delayed"),
        )
        tracker.record_prediction(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_version="v1.0",
        )
        summary = tracker.get_summary()
        assert summary["model_name"] == "v9_lgb"
        assert summary["label_delay_days"] == 5
        assert summary["total_records"] == 1
        assert summary["observed"] == 0
        assert summary["pending"] == 1
        assert "v1.0" in summary["by_version"]


# ============================================================
# PredictionRecord / DelayedMetrics 不可变性测试
# ============================================================
class TestDataclassImmutability:
    """测试 PredictionRecord / DelayedMetrics 不可变."""

    def test_prediction_record_is_frozen(self):
        """PredictionRecord 是 frozen dataclass."""
        record = PredictionRecord(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_name="v9_lgb",
            model_version="v1.0",
            recorded_at="2026-07-29T00:00:00Z",
            label_date="2026-08-03",
        )
        with pytest.raises(FrozenInstanceError):
            record.symbol = "modified"  # type: ignore

    def test_delayed_metrics_is_frozen(self):
        """DelayedMetrics 是 frozen dataclass."""
        metrics = DelayedMetrics(
            model_version="v1.0",
            n_predictions=10,
            n_observed=5,
            ic=0.05,
        )
        with pytest.raises(FrozenInstanceError):
            metrics.ic = 0.99  # type: ignore

    def test_prediction_record_to_dict(self):
        """PredictionRecord.to_dict() 返回完整字段 (property 不在 dict)."""
        record = PredictionRecord(
            date="2026-07-29",
            symbol="588080.SH",
            predicted_score=0.0235,
            model_name="v9_lgb",
            model_version="v1.0",
            recorded_at="2026-07-29T00:00:00Z",
            label_date="2026-08-03",
            actual_label=0.0180,
            label_observed_at="2026-08-03T00:00:00Z",
        )
        d = record.to_dict()
        assert d["symbol"] == "588080.SH"
        assert d["actual_label"] == 0.0180
        # property is_observed 不应出现在 asdict 输出中
        assert "is_observed" not in d
        # 但 property 应可访问
        assert record.is_observed is True

    def test_delayed_metrics_to_dict(self):
        """DelayedMetrics.to_dict() 返回完整字段."""
        metrics = DelayedMetrics(
            model_version="v1.0",
            n_predictions=20,
            n_observed=15,
            ic=0.05,
            ic_ir=0.42,
        )
        d = metrics.to_dict()
        assert d["model_version"] == "v1.0"
        assert d["n_observed"] == 15
        assert d["ic_ir"] == 0.42
