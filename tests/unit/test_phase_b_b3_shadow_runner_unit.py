"""Phase B B3 shadow runner 单元测试.

覆盖场景:
    1. 正常重训: shadow 路径产出有效权重 + 夏普, 降级护栏未触发
    2. 重训失败回退: 模拟重训失败, need_rollback=True + rollback_reason 记录
    3. shadow 数据缺失 fail-closed: observation_progress / drift_monitor 文件缺失时降级

对齐 tasks T1.2.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.phase_b_b3_shadow_runner import (
    SHARPE_DEGRADATION_THRESHOLD,
    WEIGHT_DRIFT_THRESHOLD,
    B2Status,
    B3ShadowResult,
    _simulate_auto_retrain,
    check_b2_status,
    check_degradation,
    check_flag_invariant,
    run_shadow,
)

# ============================================================
# 场景 1: 正常重训
# ============================================================


class TestNormalRetrain:
    """正常重训路径: shadow 产出有效权重 + 夏普, 降级护栏未触发."""

    def test_run_shadow_returns_valid_result(self):
        result = run_shadow()
        assert isinstance(result, B3ShadowResult)
        assert result.retrain_success is True
        assert len(result.shadow_weights) > 0
        assert len(result.prod_weights) > 0

    def test_run_shadow_weights_normalized(self):
        result = run_shadow()
        total = sum(result.shadow_weights.values())
        assert abs(total - 1.0) < 1e-6, f"shadow 权重未归一化: sum={total}"

    def test_run_shadow_sharpe_positive(self):
        result = run_shadow()
        assert result.shadow_sharpe > 0, f"shadow 夏普应为正: {result.shadow_sharpe}"

    def test_run_shadow_no_rollback_when_stable(self):
        result = run_shadow()
        assert (
            result.need_rollback is False
        ), f"稳定路径不应回退: {result.rollback_reason}"

    def test_run_shadow_suggestion_present(self):
        result = run_shadow()
        assert len(result.suggestion) > 0


# ============================================================
# 场景 2: 重训失败回退
# ============================================================


class TestRetrainFailureRollback:
    """重训失败: need_rollback=True + rollback_reason 记录."""

    def test_empty_weights_retrain_failure(self):
        result = _simulate_auto_retrain({})
        assert result.retrain_success is False
        assert "为空" in result.error_message or "empty" in result.error_message.lower()

    def test_run_shadow_with_empty_baseline_rollback(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.phase_b_b3_shadow_runner._build_mock_baseline", lambda: ({}, 1.52)
        )
        result = run_shadow()
        assert result.retrain_success is False
        assert result.need_rollback is True
        assert "重训失败" in result.rollback_reason

    def test_degradation_guard_sharpe_rollback(self):
        degradation = check_degradation(
            retrain_sharpe=1.0,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.5, "b": 0.5},
            baseline_weights={"a": 0.5, "b": 0.5},
        )
        assert degradation.need_rollback is True
        assert "夏普退化" in degradation.rollback_reason
        assert degradation.sharpe_degradation > SHARPE_DEGRADATION_THRESHOLD

    def test_degradation_guard_weight_drift_rollback(self):
        degradation = check_degradation(
            retrain_sharpe=1.5,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.9, "b": 0.1},
            baseline_weights={"a": 0.1, "b": 0.9},
        )
        assert degradation.need_rollback is True
        assert "权重漂移" in degradation.rollback_reason
        assert degradation.weight_drift > WEIGHT_DRIFT_THRESHOLD

    def test_degradation_guard_both_triggers_combined_reason(self):
        degradation = check_degradation(
            retrain_sharpe=1.0,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.9, "b": 0.1},
            baseline_weights={"a": 0.1, "b": 0.9},
        )
        assert degradation.need_rollback is True
        assert "夏普退化" in degradation.rollback_reason
        assert "权重漂移" in degradation.rollback_reason


# ============================================================
# 场景 3: shadow 数据缺失 fail-closed
# ============================================================


class TestShadowDataMissingFailClosed:
    """shadow 数据缺失: observation_progress / drift_monitor 文件缺失时降级."""

    def test_run_shadow_with_nonexistent_paths(self):
        nonexistent_path = Path("/nonexistent/path/that/does/not/exist.json")
        result = run_shadow(
            observation_progress_path=nonexistent_path,
            drift_monitor_data_path=nonexistent_path,
        )
        assert result.retrain_success is True
        assert len(result.shadow_weights) > 0

    def test_simulate_retrain_with_none_paths(self):
        baseline = {"a": 0.5, "b": 0.5}
        result = _simulate_auto_retrain(baseline, None, None)
        assert result.retrain_success is True
        assert len(result.retrained_weights) == 2

    def test_simulate_retrain_with_valid_observation_progress(self, tmp_path):
        obs_file = tmp_path / "obs_progress.json"
        obs_file.write_text(json.dumps({"progress_ratio": 0.5}), encoding="utf-8")
        baseline = {"a": 0.5, "b": 0.5}
        result = _simulate_auto_retrain(baseline, obs_file, None)
        assert result.retrain_success is True
        assert result.retrain_sharpe > 1.5

    def test_simulate_retrain_with_corrupted_file(self, tmp_path):
        obs_file = tmp_path / "corrupted.json"
        obs_file.write_text("{invalid json}", encoding="utf-8")
        baseline = {"a": 0.5, "b": 0.5}
        result = _simulate_auto_retrain(baseline, obs_file, None)
        assert result.retrain_success is False
        assert len(result.error_message) > 0


# ============================================================
# 辅助测试: B2 状态检查 + Flag 不变式
# ============================================================


class TestB2StatusCheck:
    """B2 状态前置检查."""

    def test_check_b2_status_returns_b2status(self):
        result = check_b2_status()
        assert isinstance(result, B2Status)
        assert hasattr(result, "enabled")
        assert hasattr(result, "healthy")
        assert hasattr(result, "shadow_days")

    def test_check_b2_status_message_present(self):
        result = check_b2_status()
        assert len(result.message) > 0


class TestFlagInvariant:
    """Feature flag 不变式校验."""

    def test_check_flag_invariant_returns_bool(self):
        result = check_flag_invariant()
        assert isinstance(result, bool)


# ============================================================
# 辅助测试: 降级护栏边界
# ============================================================


class TestDegradationGuardBoundary:
    """降级护栏边界条件."""

    def test_no_degradation_when_identical(self):
        degradation = check_degradation(
            retrain_sharpe=1.5,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.5, "b": 0.5},
            baseline_weights={"a": 0.5, "b": 0.5},
        )
        assert degradation.need_rollback is False
        assert degradation.sharpe_degradation == 0.0
        assert degradation.weight_drift == 0.0

    def test_sharpe_improvement_no_rollback(self):
        degradation = check_degradation(
            retrain_sharpe=2.0,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.5, "b": 0.5},
            baseline_weights={"a": 0.5, "b": 0.5},
        )
        assert degradation.need_rollback is False
        assert degradation.sharpe_degradation == 0.0

    def test_exactly_at_sharpe_threshold(self):
        degradation = check_degradation(
            retrain_sharpe=1.5 - SHARPE_DEGRADATION_THRESHOLD + 1e-9,
            baseline_sharpe=1.5,
            retrained_weights={"a": 0.5, "b": 0.5},
            baseline_weights={"a": 0.5, "b": 0.5},
        )
        assert degradation.sharpe_degradation < SHARPE_DEGRADATION_THRESHOLD
        assert degradation.need_rollback is False
