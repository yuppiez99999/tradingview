"""DriftShadowIntegrator 单元测试 — W1.3b Day 1.

测试覆盖目标:
    - 构造器参数校验
    - run_daily_integration 核心流程 (读 daily_returns → 漂移检测 → 更新标签 → 计算 IC)
    - backfill_history 历史回填
    - calibrate_psi_thresholds 骨架
    - 粒度处理 (symbol 级 vs 组合级降级)
    - IC_IR 退化检测
    - fail-safe 行为 (模块失败不中断)
    - 持久化与历史累积

设计原则:
    - 使用 MockDriftMonitor / MockDelayedLabelTracker 隔离真实模块
    - 使用 tmp_path 隔离 daily_returns.jsonl 和报告文件
    - 不依赖真实 V9 模型 / 真实数据源
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.drift_shadow_integrator import (  # noqa: E402
    DEFAULT_IC_DEGRADATION_THRESHOLD,
    DriftShadowIntegrator,
    IntegrationResult,
    PSICalibrationResult,
)

# ============================================================
# Mock 对象
# ============================================================


@dataclass
class MockDriftReport:
    """模拟 DriftReport."""

    timestamp: str = "2026-08-07T10:00:00"
    model_name: str = "v9_lgb"
    model_version: str = "v9_test"
    feature_name: str = "test_feature"
    drift_score: float = 0.15
    psi: float = 0.12
    severity: str = "MEDIUM"
    baseline_mean: float = 0.5
    current_mean: float = 0.55
    baseline_size: int = 100
    current_size: int = 100
    owner: str = "test_owner"
    runbook_url: str = "docs/runbooks/test.md"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "feature_name": self.feature_name,
            "psi": self.psi,
            "severity": self.severity,
        }


class MockDriftMonitor:
    """模拟 SimModeDriftMonitor."""

    def __init__(
        self,
        reports: list[MockDriftReport] | None = None,
        raise_on_check: bool = False,
        baseline_panel: Any = None,
        feature_columns: list[str] | None = None,
    ) -> None:
        self._reports = reports or []
        self._raise_on_check = raise_on_check
        self._baseline_panel = baseline_panel
        self._feature_columns = feature_columns or []
        self.call_count = 0

    def run_daily_check(self, current_panel: Any) -> list[MockDriftReport]:
        self.call_count += 1
        if self._raise_on_check:
            raise RuntimeError("mock drift monitor error")
        return list(self._reports)


@dataclass
class MockDelayedMetrics:
    """模拟 DelayedMetrics."""

    model_version: str = "v9_test"
    n_predictions: int = 30
    n_observed: int = 25
    n_pending: int = 5
    observation_rate: float = 25 / 30
    ic: float = 0.08
    rank_ic: float = 0.07
    ic_ir: float = 0.85
    mean_predicted: float = 0.05
    mean_actual: float = 0.06
    std_predicted: float = 0.02
    std_actual: float = 0.025
    timestamp: str = "2026-08-07T10:00:00"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "n_predictions": self.n_predictions,
            "n_observed": self.n_observed,
            "ic": self.ic,
            "ic_ir": self.ic_ir,
            "observation_rate": self.observation_rate,
        }


@dataclass
class MockPredictionRecord:
    """模拟 PredictionRecord."""

    date: str
    symbol: str
    predicted_score: float
    model_name: str = "v9_lgb"
    model_version: str = "v9_test"
    recorded_at: str = ""
    label_date: str = ""
    actual_label: float | None = None
    label_observed_at: str | None = None


class MockDelayedLabelTracker:
    """模拟 DelayedLabelTracker."""

    def __init__(
        self,
        records: list[MockPredictionRecord] | None = None,
        metrics: MockDelayedMetrics | None = None,
        raise_on_record: bool = False,
        raise_on_metrics: bool = False,
    ) -> None:
        # 用 append 而非覆盖 (同一日期可有多个 symbol 的 record)
        self._records: dict[str, list[MockPredictionRecord]] = {}
        for r in records or []:
            self._records.setdefault(r.date, []).append(r)
        self._metrics = metrics or MockDelayedMetrics()
        self._raise_on_record = raise_on_record
        self._raise_on_metrics = raise_on_metrics
        self.record_count = 0
        self.update_count = 0

    def record_predictions_batch(
        self, date: str, predictions: dict[str, float], model_version: str
    ) -> list[MockPredictionRecord]:
        if self._raise_on_record:
            raise RuntimeError("mock record error")
        self.record_count += 1
        records = [
            MockPredictionRecord(date=date, symbol=s, predicted_score=v)
            for s, v in predictions.items()
        ]
        self._records.setdefault(date, []).extend(records)
        return records

    def update_actual_labels_batch(self, labels: dict[str, dict[str, float]]) -> int:
        count = 0
        for date, symbol_labels in labels.items():
            for symbol, actual in symbol_labels.items():
                if date in self._records:
                    for r in self._records[date]:
                        if r.symbol == symbol:
                            r.actual_label = actual
                            count += 1
        self.update_count += count
        return count

    def compute_delayed_metrics(
        self, model_version: str | None = None
    ) -> MockDelayedMetrics:
        if self._raise_on_metrics:
            raise RuntimeError("mock metrics error")
        return self._metrics

    def get_records(self, date: str) -> list[MockPredictionRecord]:
        return self._records.get(date, [])


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_monitor():
    """基础 Mock DriftMonitor."""
    return MockDriftMonitor(reports=[MockDriftReport(psi=0.12)])


@pytest.fixture
def mock_tracker():
    """基础 Mock DelayedLabelTracker (含 1 条已记录预测)."""
    records = [
        MockPredictionRecord(
            date="2026-08-01",
            symbol="600276",
            predicted_score=0.05,
        ),
    ]
    return MockDelayedLabelTracker(records=records)


@pytest.fixture
def daily_returns_file(tmp_path):
    """创建 daily_returns.jsonl 测试文件."""
    path = tmp_path / "daily_returns.jsonl"
    records = [
        {"date": "2026-08-01", "daily_return": 0.0085, "source": "test"},
        {"date": "2026-08-04", "daily_return": -0.003, "source": "test"},
        {"date": "2026-08-05", "daily_return": 0.012, "source": "test"},
        {"date": "2026-08-06", "daily_return": 0.005, "source": "test"},
        {"date": "2026-08-07", "daily_return": 0.015, "source": "test"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


@pytest.fixture
def integrator(mock_monitor, mock_tracker, daily_returns_file, tmp_path):
    """构造 DriftShadowIntegrator."""
    return DriftShadowIntegrator(
        drift_monitor=mock_monitor,
        label_tracker=mock_tracker,
        daily_returns_path=daily_returns_file,
        reports_dir=tmp_path / "drift_reports",
        baseline_ic_ir=0.88,
    )


# ============================================================
# 构造器测试
# ============================================================


class TestInit:
    """测试构造器参数校验."""

    def test_init_with_valid_params(self, mock_monitor, mock_tracker, tmp_path):
        """正常初始化."""
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            daily_returns_path=tmp_path / "returns.jsonl",
            reports_dir=tmp_path / "reports",
        )
        assert integrator._baseline_ic_ir is None
        assert integrator._ic_degradation_threshold == DEFAULT_IC_DEGRADATION_THRESHOLD

    def test_init_with_none_monitor_raises(self, mock_tracker, tmp_path):
        """drift_monitor=None 应抛 ValueError."""
        with pytest.raises(ValueError, match="drift_monitor 不能为 None"):
            DriftShadowIntegrator(
                drift_monitor=None,
                label_tracker=mock_tracker,
            )

    def test_init_with_none_tracker_raises(self, mock_monitor, tmp_path):
        """label_tracker=None 应抛 ValueError."""
        with pytest.raises(ValueError, match="label_tracker 不能为 None"):
            DriftShadowIntegrator(
                drift_monitor=mock_monitor,
                label_tracker=None,
            )

    def test_init_with_invalid_monitor_raises(self, mock_tracker, tmp_path):
        """drift_monitor 无 run_daily_check 方法应抛 ValueError."""

        class NoMethodMonitor:
            pass

        with pytest.raises(ValueError, match="必须实现 run_daily_check"):
            DriftShadowIntegrator(
                drift_monitor=NoMethodMonitor(),
                label_tracker=mock_tracker,
            )

    def test_init_with_invalid_tracker_raises(self, mock_monitor, tmp_path):
        """label_tracker 无 update_actual_labels_batch 方法应抛 ValueError."""

        class NoMethodTracker:
            pass

        with pytest.raises(ValueError, match="必须实现 update_actual_labels_batch"):
            DriftShadowIntegrator(
                drift_monitor=mock_monitor,
                label_tracker=NoMethodTracker(),
            )

    def test_init_with_negative_threshold_raises(self, mock_monitor, mock_tracker):
        """ic_degradation_threshold < 0 应抛 ValueError."""
        with pytest.raises(ValueError, match="ic_degradation_threshold 必须 >= 0"):
            DriftShadowIntegrator(
                drift_monitor=mock_monitor,
                label_tracker=mock_tracker,
                ic_degradation_threshold=-0.1,
            )

    def test_init_creates_reports_dir(self, mock_monitor, mock_tracker, tmp_path):
        """reports_dir 不存在时应自动创建."""
        reports_dir = tmp_path / "new_dir" / "subdir"
        DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            reports_dir=reports_dir,
        )
        assert reports_dir.exists()

    def test_init_with_baseline_ic_ir(self, mock_monitor, mock_tracker):
        """baseline_ic_ir 参数."""
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            baseline_ic_ir=0.92,
        )
        assert integrator._baseline_ic_ir == 0.92


# ============================================================
# run_daily_integration 测试
# ============================================================


class TestRunDailyIntegration:
    """测试 run_daily_integration 核心流程."""

    def test_success_with_daily_return(self, integrator, daily_returns_file):
        """成功读取当日收益并执行集成."""
        result = integrator.run_daily_integration("2026-08-07")
        assert result.date == "2026-08-07"
        assert result.daily_return == pytest.approx(0.015)
        assert result.skipped is False
        assert result.error is None

    def test_skipped_when_no_shadow_data(self, integrator):
        """当日无 Shadow 数据应跳过."""
        result = integrator.run_daily_integration("2026-12-31")
        assert result.skipped is True
        assert result.error == "no_shadow_data_for_date"
        assert result.daily_return == 0.0

    def test_skipped_when_file_missing(self, mock_monitor, mock_tracker, tmp_path):
        """daily_returns.jsonl 不存在应跳过."""
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            daily_returns_path=tmp_path / "nonexistent.jsonl",
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert result.skipped is True
        assert (
            "daily_returns_read_failed" in result.error
            or result.error == "no_shadow_data_for_date"
        )

    def test_drift_monitor_called_with_panel(
        self, mock_tracker, daily_returns_file, tmp_path
    ):
        """传入 current_panel 时应调用 drift_monitor.run_daily_check."""
        monitor = MockDriftMonitor(reports=[MockDriftReport(psi=0.12)])
        integrator = DriftShadowIntegrator(
            drift_monitor=monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        result = integrator.run_daily_integration(
            "2026-08-07", current_panel={"feature1": [1, 2, 3]}
        )
        assert monitor.call_count == 1
        assert len(result.drift_reports) == 1
        assert result.drift_reports[0].psi == 0.12

    def test_drift_monitor_not_called_without_panel(self, integrator):
        """无 current_panel 时不应调用 drift_monitor."""
        result = integrator.run_daily_integration("2026-08-07")
        assert integrator._monitor.call_count == 0
        assert len(result.drift_reports) == 0

    def test_drift_monitor_failure_fail_safe(self, mock_tracker, daily_returns_file):
        """DriftMonitor 失败时应 fail-safe, 不中断集成."""
        monitor = MockDriftMonitor(raise_on_check=True)
        integrator = DriftShadowIntegrator(
            drift_monitor=monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        result = integrator.run_daily_integration(
            "2026-08-07", current_panel={"feature1": [1, 2, 3]}
        )
        assert any("drift_monitor_failed" in a for a in result.alerts)
        # 集成应继续 (daily_return 仍读取)
        assert result.daily_return == pytest.approx(0.015)

    def test_psi_high_triggers_alert(self, mock_tracker, daily_returns_file):
        """PSI >= 0.25 应触发告警."""
        monitor = MockDriftMonitor(
            reports=[
                MockDriftReport(feature_name="rsi_14d", psi=0.30),
                MockDriftReport(feature_name="momentum_5d", psi=0.10),
            ]
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        result = integrator.run_daily_integration(
            "2026-08-07", current_panel={"rsi_14d": [1, 2, 3]}
        )
        psi_alerts = [a for a in result.alerts if "psi_high" in a]
        assert len(psi_alerts) == 1
        assert "rsi_14d" in psi_alerts[0]
        assert "0.3000" in psi_alerts[0]


# ============================================================
# 标签更新与 IC 计算测试
# ============================================================


class TestLabelUpdateAndIC:
    """测试标签更新和 IC 计算."""

    def test_record_predictions_called(self, mock_monitor, daily_returns_file):
        """传入 current_predictions 时应调用 record_predictions_batch."""
        tracker = MockDelayedLabelTracker(records=[])
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
        )
        integrator.run_daily_integration(
            "2026-08-07",
            current_predictions={"600276": 0.05, "588000": 0.03},
            model_version="v9_test",
        )
        assert tracker.record_count == 1

    def test_record_predictions_not_called_without_predictions(self, integrator):
        """无 current_predictions 时不应调用 record_predictions_batch."""
        integrator.run_daily_integration("2026-08-07")
        assert integrator._tracker.record_count == 0

    def test_record_predictions_failure_fail_safe(
        self, mock_monitor, daily_returns_file
    ):
        """record_predictions_batch 失败应 fail-safe."""
        tracker = MockDelayedLabelTracker(raise_on_record=True)
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
        )
        result = integrator.run_daily_integration(
            "2026-08-07",
            current_predictions={"600276": 0.05},
            model_version="v9_test",
        )
        assert any("record_predictions_failed" in a for a in result.alerts)

    def test_compute_metrics_called(self, integrator):
        """应调用 compute_delayed_metrics."""
        result = integrator.run_daily_integration("2026-08-07")
        assert result.delayed_metrics is not None
        assert result.delayed_metrics.ic == pytest.approx(0.08)
        assert result.delayed_metrics.ic_ir == pytest.approx(0.85)

    def test_compute_metrics_failure_fail_safe(self, mock_monitor, daily_returns_file):
        """compute_delayed_metrics 失败应 fail-safe."""
        tracker = MockDelayedLabelTracker(raise_on_metrics=True)
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert result.delayed_metrics is None
        assert any("compute_metrics_failed" in a for a in result.alerts)


# ============================================================
# IC_IR 退化检测测试
# ============================================================


class TestICDegradation:
    """测试 IC_IR 退化检测."""

    def test_no_degradation_alert_when_no_baseline(
        self, mock_monitor, daily_returns_file
    ):
        """无 baseline_ic_ir 时不检测退化."""
        tracker = MockDelayedLabelTracker(
            metrics=MockDelayedMetrics(ic_ir=0.5, n_observed=30)
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            baseline_ic_ir=None,
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert result.ic_degradation is None
        assert not any("ic_ir_degradation" in a for a in result.alerts)

    def test_degradation_alert_when_exceeds_threshold(
        self, mock_monitor, daily_returns_file
    ):
        """IC_IR 退化 > 0.3 应触发告警."""
        tracker = MockDelayedLabelTracker(
            metrics=MockDelayedMetrics(ic_ir=0.40, n_observed=30)
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            baseline_ic_ir=0.88,
            ic_degradation_threshold=0.3,
        )
        result = integrator.run_daily_integration("2026-08-07")
        # degradation = |0.88 - 0.40| = 0.48 > 0.3
        assert result.ic_degradation == pytest.approx(0.48, abs=0.01)
        degradation_alerts = [a for a in result.alerts if "ic_ir_degradation" in a]
        assert len(degradation_alerts) == 1
        assert "0.8800" in degradation_alerts[0]
        assert "0.4000" in degradation_alerts[0]

    def test_no_degradation_alert_when_below_threshold(
        self, mock_monitor, daily_returns_file
    ):
        """IC_IR 退化 < 0.3 不应触发告警."""
        tracker = MockDelayedLabelTracker(
            metrics=MockDelayedMetrics(ic_ir=0.80, n_observed=30)
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            baseline_ic_ir=0.88,
            ic_degradation_threshold=0.3,
        )
        result = integrator.run_daily_integration("2026-08-07")
        # degradation = |0.88 - 0.80| = 0.08 < 0.3
        assert result.ic_degradation == pytest.approx(0.08, abs=0.01)
        assert not any("ic_ir_degradation" in a for a in result.alerts)

    def test_insufficient_samples_triggers_alert(
        self, mock_monitor, daily_returns_file
    ):
        """样本不足 (<20) 应触发 insufficient_samples 告警 (降级模式)."""
        tracker = MockDelayedLabelTracker(
            metrics=MockDelayedMetrics(ic_ir=0.40, n_observed=10)
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            baseline_ic_ir=0.88,
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert any("insufficient_samples" in a for a in result.alerts)
        # 样本不足时不做退化检测
        assert result.ic_degradation is None


# ============================================================
# backfill_history 测试
# ============================================================


class TestBackfillHistory:
    """测试 backfill_history 历史回填."""

    def test_backfill_success(self, integrator, daily_returns_file):
        """成功回填多个交易日."""
        results = integrator.backfill_history("2026-08-01", "2026-08-07")
        # 2026-08-01 周六 / 08-02 周日 跳过
        # 工作日: 8-03(周一)/04/05/06/07, 共 5 天
        assert len(results) == 5
        dates = [r.date for r in results]
        assert "2026-08-03" in dates
        assert "2026-08-07" in dates
        assert "2026-08-01" not in dates  # 周六
        assert "2026-08-02" not in dates  # 周日

    def test_backfill_skips_weekend(self, integrator):
        """回填应跳过周末."""
        results = integrator.backfill_history("2026-08-01", "2026-08-09")
        # 8-01 周六 / 8-02 周日 / 8-08 周六 / 8-09 周日 跳过
        dates = [r.date for r in results]
        assert "2026-08-02" not in dates  # 周日
        assert "2026-08-08" not in dates  # 周六
        assert "2026-08-09" not in dates  # 周日

    def test_backfill_invalid_date_format_raises(self, integrator):
        """非法日期格式应抛 ValueError."""
        with pytest.raises(ValueError, match="日期格式错误"):
            integrator.backfill_history("2026/08/01", "2026-08-07")

    def test_backfill_start_after_end_raises(self, integrator):
        """start_date > end_date 应抛 ValueError."""
        with pytest.raises(ValueError, match="不能晚于"):
            integrator.backfill_history("2026-08-10", "2026-08-07")

    def test_backfill_with_predictions(self, mock_monitor, daily_returns_file):
        """回填时传入 prediction_history 应记录预测."""
        tracker = MockDelayedLabelTracker(records=[])
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
        )
        prediction_history = {
            "2026-08-04": {"600276": 0.05, "588000": 0.03},
            "2026-08-05": {"600276": 0.04, "588000": 0.02},
        }
        integrator.backfill_history(
            "2026-08-04",
            "2026-08-05",
            prediction_history=prediction_history,
            model_version="v9_test",
        )
        assert tracker.record_count == 2

    def test_backfill_summary(self, integrator):
        """回填后 get_summary 应返回正确统计."""
        integrator.backfill_history("2026-08-04", "2026-08-07")
        summary = integrator.get_summary()
        assert summary["total_days"] == 4
        assert summary["success_days"] >= 1
        assert summary["total_alerts"] >= 0


# ============================================================
# calibrate_psi_thresholds 测试 (Day 3 骨架)
# ============================================================


class TestCalibratePSI:
    """测试 PSI 阈值校准 (Day 3 骨架)."""

    def test_calibrate_returns_empty_without_baseline(
        self, mock_monitor, mock_tracker, daily_returns_file
    ):
        """无基线 panel 时应返回空列表."""
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        results = integrator.calibrate_psi_thresholds()
        assert results == []

    def test_calibrate_returns_industrial_defaults(
        self, mock_monitor, mock_tracker, daily_returns_file
    ):
        """Day 1 骨架应返回工业标准阈值."""

        class MockBaseline:
            columns = ["rsi_14d", "momentum_5d", "code", "date"]

        monitor = MockDriftMonitor(baseline_panel=MockBaseline())
        integrator = DriftShadowIntegrator(
            drift_monitor=monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        results = integrator.calibrate_psi_thresholds()
        # 排除 code, date, 应有 2 个特征
        assert len(results) == 2
        feature_names = [r.feature_name for r in results]
        assert "rsi_14d" in feature_names
        assert "momentum_5d" in feature_names
        # Day 1 骨架未校准
        for r in results:
            assert r.is_calibrated is False
            assert r.high_threshold == 0.25  # 工业标准
            assert r.critical_threshold == 0.5

    def test_exceeds_industrial_2x_property(self):
        """测试 exceeds_industrial_2x 属性."""
        result = PSICalibrationResult(
            feature_name="test",
            high_threshold=0.6,  # > 0.25 * 2 = 0.5
            critical_threshold=0.5,
        )
        assert result.exceeds_industrial_2x is True

        result2 = PSICalibrationResult(
            feature_name="test",
            high_threshold=0.3,  # < 0.5
            critical_threshold=0.6,  # > 0.5 * 2 = 1.0? No, 0.6 < 1.0
        )
        assert result2.exceeds_industrial_2x is False


# ============================================================
# 持久化与历史测试
# ============================================================


class TestPersistenceAndHistory:
    """测试持久化和历史累积."""

    def test_persist_result_creates_file(self, integrator, tmp_path):
        """run_daily_integration 应持久化结果到 JSON."""
        integrator.run_daily_integration("2026-08-07")
        report_path = tmp_path / "drift_reports" / "integration_2026-08-07.json"
        assert report_path.exists()
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["date"] == "2026-08-07"
        assert data["daily_return"] == pytest.approx(0.015)

    def test_history_accumulates(self, integrator):
        """多次 run_daily_integration 应累积历史."""
        integrator.run_daily_integration("2026-08-04")
        integrator.run_daily_integration("2026-08-05")
        history = integrator.get_history()
        assert len(history) == 2
        assert history[0].date == "2026-08-04"
        assert history[1].date == "2026-08-05"

    def test_get_summary_empty(self, mock_monitor, mock_tracker, daily_returns_file):
        """无历史时 get_summary 应返回空统计."""
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=mock_tracker,
            daily_returns_path=daily_returns_file,
        )
        summary = integrator.get_summary()
        assert summary["total_days"] == 0
        assert summary["success_days"] == 0

    def test_get_summary_with_history(self, integrator):
        """有历史时 get_summary 应返回正确统计."""
        integrator.run_daily_integration("2026-08-07")
        summary = integrator.get_summary()
        assert summary["total_days"] == 1
        assert summary["success_days"] == 1
        assert summary["avg_ic"] == pytest.approx(0.08)
        assert summary["avg_ic_ir"] == pytest.approx(0.85)


# ============================================================
# 粒度处理测试
# ============================================================


class TestGranularityHandling:
    """测试 symbol 级 vs 组合级降级."""

    def test_symbol_level_mode(self, mock_monitor, daily_returns_file):
        """symbol_returns_provider 模式应使用 symbol 级收益."""
        tracker = MockDelayedLabelTracker(
            records=[
                MockPredictionRecord(
                    date="2026-08-07", symbol="600276", predicted_score=0.05
                ),
                MockPredictionRecord(
                    date="2026-08-07", symbol="588000", predicted_score=0.03
                ),
            ]
        )

        def symbol_provider(date):
            return {"600276": 0.01, "588000": 0.02}

        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            symbol_returns_provider=symbol_provider,
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert result.symbols_updated == 2

    def test_symbol_provider_failure_falls_back_to_portfolio(
        self, mock_monitor, daily_returns_file
    ):
        """symbol_returns_provider 失败应降级为组合级."""
        tracker = MockDelayedLabelTracker(
            records=[
                MockPredictionRecord(
                    date="2026-08-07", symbol="600276", predicted_score=0.05
                ),
            ]
        )

        def failing_provider(date):
            raise RuntimeError("provider error")

        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            symbol_returns_provider=failing_provider,
        )
        result = integrator.run_daily_integration("2026-08-07")
        # 降级为组合级, 应更新 1 个 symbol
        assert result.symbols_updated == 1

    def test_portfolio_level_fallback(self, mock_monitor, daily_returns_file):
        """无 symbol_returns_provider 时用组合级降级."""
        tracker = MockDelayedLabelTracker(
            records=[
                MockPredictionRecord(
                    date="2026-08-07", symbol="600276", predicted_score=0.05
                ),
                MockPredictionRecord(
                    date="2026-08-07", symbol="588000", predicted_score=0.03
                ),
            ]
        )
        integrator = DriftShadowIntegrator(
            drift_monitor=mock_monitor,
            label_tracker=tracker,
            daily_returns_path=daily_returns_file,
            # 无 symbol_returns_provider
        )
        result = integrator.run_daily_integration("2026-08-07")
        assert result.symbols_updated == 2


# ============================================================
# IntegrationResult 数据类测试
# ============================================================


class TestIntegrationResult:
    """测试 IntegrationResult 数据类."""

    def test_is_success_with_drift_reports(self):
        """有 drift_reports 时 is_success=True."""
        result = IntegrationResult(
            date="2026-08-07",
            drift_reports=[MockDriftReport()],
        )
        assert result.is_success is True

    def test_is_success_with_metrics(self):
        """有 delayed_metrics 时 is_success=True."""
        result = IntegrationResult(
            date="2026-08-07",
            delayed_metrics=MockDelayedMetrics(),
        )
        assert result.is_success is True

    def test_is_success_false_when_skipped(self):
        """skipped=True 时 is_success=False."""
        result = IntegrationResult(
            date="2026-08-07",
            skipped=True,
            drift_reports=[MockDriftReport()],
        )
        assert result.is_success is False

    def test_is_success_false_when_empty(self):
        """空结果时 is_success=False."""
        result = IntegrationResult(date="2026-08-07")
        assert result.is_success is False

    def test_to_dict_serializable(self):
        """to_dict 应可 JSON 序列化."""
        result = IntegrationResult(
            date="2026-08-07",
            daily_return=0.015,
            drift_reports=[MockDriftReport()],
            delayed_metrics=MockDelayedMetrics(),
            alerts=["test_alert"],
            symbols_updated=2,
        )
        d = result.to_dict()
        # 应可序列化
        json_str = json.dumps(d, ensure_ascii=False)
        assert "2026-08-07" in json_str
        assert "test_alert" in json_str

    def test_to_dict_with_none_metrics(self):
        """delayed_metrics=None 时 to_dict 应处理."""
        result = IntegrationResult(date="2026-08-07")
        d = result.to_dict()
        assert d["delayed_metrics"] is None
        assert d["drift_reports"] == []
