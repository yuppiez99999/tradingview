"""GAP-1 API 烟雾测试 — 验证关键 API 可调用.

设计原则:
    1. 只验证 API "能调用" (不验证结果正确性)
    2. 用空/最小输入 (不依赖外部数据)
    3. 每个测试 < 1s
"""

import pandas as pd
import pytest

pytestmark = pytest.mark.smoke


def test_v9_contract_validate_callable():
    """V9_DEFAULT_CONTRACT.validate 可调用."""
    from utils.alpha.data_contract import V9_DEFAULT_CONTRACT

    empty_panel = pd.DataFrame(
        columns=["code", "date", "close", "open", "high", "low", "volume", "y"]
    )
    result = V9_DEFAULT_CONTRACT.validate(empty_panel, mode="warn_only")
    assert result is not None


def test_drift_severity_enum():
    """DriftSeverity 枚举值正确."""
    from utils.alpha.drift_monitor import DriftSeverity

    assert DriftSeverity.LOW.value == "low"
    assert DriftSeverity.MEDIUM.value == "medium"
    assert DriftSeverity.HIGH.value == "high"
    assert DriftSeverity.CRITICAL.value == "critical"


def test_compute_psi_callable():
    """compute_psi 可计算."""
    from utils.alpha.drift_monitor import compute_psi

    psi = compute_psi(pd.Series([1, 2, 3]), pd.Series([1, 2, 3]))
    assert psi >= 0


def test_compute_feature_drift_callable():
    """compute_feature_drift 可计算."""
    from utils.alpha.drift_monitor import compute_feature_drift

    report = compute_feature_drift(
        baseline=pd.Series([1, 2, 3, 4, 5]),
        current=pd.Series([1, 2, 3, 4, 5]),
        feature_name="test",
    )
    assert report is not None
    assert report.feature_name == "test"


def test_training_config_constructable():
    """TrainingConfig 可构造."""
    from utils.lgbm_reproducibility import TrainingConfig

    config = TrainingConfig(model_name="smoke", seed=42)
    assert config.model_name == "smoke"


def test_artifact_name_callable():
    """artifact_name 可计算."""
    from utils.lgbm_reproducibility import TrainingConfig, artifact_name

    config = TrainingConfig(model_name="smoke", seed=42).with_config_hash()
    name = artifact_name(config)
    assert "smoke" in name


def test_delayed_label_tracker_instantiable():
    """DelayedLabelTracker 可实例化."""
    from utils.alpha.delayed_label_tracker import DelayedLabelTracker

    tracker = DelayedLabelTracker(model_name="smoke", label_delay_days=5)
    assert tracker.model_name == "smoke"


def test_sim_mode_drift_monitor_instantiable():
    """SimModeDriftMonitor 可实例化."""
    from utils.alpha.drift_monitor import SimModeDriftMonitor

    monitor = SimModeDriftMonitor(
        model_name="smoke",
        model_version="v1.0",
        sim_mode=False,
    )
    assert monitor.model_name == "smoke"
