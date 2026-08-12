"""Shadow 数据质量闭环门槛测试 — 验证 5 天/14 天两层门槛拦截逻辑

测试目标:
    1. check_gates(): 看门狗双重门槛 (GATE-A 天数 + GATE-B 真实数据量)
    2. run_drift_detection(): 集成器层 MIN_REAL_SAMPLES_FOR_DRIFT=5 门槛
    3. run_watchdog(): 触发决策 (all_passed / force_trigger / dry_run 组合)
    4. 切分逻辑: BASELINE_RATIO=0.6 的边界情况
    5. 两层协同: 看门狗 14 天 + 集成器 5 天的兜底关系

共享 fixture (tests/conftest.py):
    - mock_drift_report: 模拟 DriftReport 对象
    - mock_compute_prediction_drift: patch compute_prediction_drift

辅助函数 (tests/shadow_helpers.py):
    - make_real_records / make_progress_dict

关联文档: cairn/shadow-data-quality-loop.md §4.3 门槛设计: 两层防护
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# 项目根目录加入 sys.path
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from shadow_helpers import make_real_records, make_progress_dict
from scripts.observation_watchdog import check_gates, run_watchdog
from scripts.integrate_cleaned_to_drift import (
    run_drift_detection,
    MIN_REAL_SAMPLES_FOR_DRIFT,
    BASELINE_RATIO,
)


# ============================================================
# 1. check_gates() 双重门槛判定 (纯函数, 无需 mock)
# ============================================================


class TestCheckGates:
    """测试 check_gates() 双重门槛判定逻辑.

    GATE-A: days_completed >= required_days (日历时间门槛)
    GATE-B: real_count >= required_days (真实数据量门槛)
    all_passed = gate_a_passed AND gate_b_passed
    """

    @pytest.mark.unit
    def test_both_fail_below_threshold(self):
        """两者都不足: 3天/3条, required=14 → 都 FAIL"""
        progress = make_progress_dict(days_completed=3)
        records = make_real_records(3)
        gates = check_gates(progress, records, required_days=14)
        assert gates["gate_a_passed"] is False
        assert gates["gate_b_passed"] is False
        assert gates["all_passed"] is False
        assert gates["days_remaining"] == 11
        assert gates["samples_remaining"] == 11

    @pytest.mark.unit
    def test_only_gate_a_passes(self):
        """仅 GATE-A 通过: 14天/3条 → GATE-B FAIL (回测回填污染场景)"""
        progress = make_progress_dict(days_completed=14)
        records = make_real_records(3)
        gates = check_gates(progress, records, required_days=14)
        assert gates["gate_a_passed"] is True
        assert gates["gate_b_passed"] is False
        assert gates["all_passed"] is False

    @pytest.mark.unit
    def test_only_gate_b_passes(self):
        """仅 GATE-B 通过: 3天/14条 → GATE-A FAIL (日历时间不足场景)"""
        progress = make_progress_dict(days_completed=3)
        records = make_real_records(14)
        gates = check_gates(progress, records, required_days=14)
        assert gates["gate_a_passed"] is False
        assert gates["gate_b_passed"] is True
        assert gates["all_passed"] is False

    @pytest.mark.unit
    def test_both_pass_at_threshold(self):
        """刚好达标: 14天/14条 → 都 PASS (边界值)"""
        progress = make_progress_dict(days_completed=14)
        records = make_real_records(14)
        gates = check_gates(progress, records, required_days=14)
        assert gates["gate_a_passed"] is True
        assert gates["gate_b_passed"] is True
        assert gates["all_passed"] is True
        assert gates["days_remaining"] == 0
        assert gates["samples_remaining"] == 0

    @pytest.mark.unit
    def test_both_pass_above_threshold(self):
        """超过达标: 20天/20条 → 都 PASS"""
        progress = make_progress_dict(days_completed=20)
        records = make_real_records(20)
        gates = check_gates(progress, records, required_days=14)
        assert gates["all_passed"] is True

    @pytest.mark.unit
    def test_boundary_just_below(self):
        """边界: 13天/13条 → 都 FAIL (13 < 14, 差一天)"""
        progress = make_progress_dict(days_completed=13)
        records = make_real_records(13)
        gates = check_gates(progress, records, required_days=14)
        assert gates["gate_a_passed"] is False
        assert gates["gate_b_passed"] is False
        assert gates["all_passed"] is False
        assert gates["days_remaining"] == 1
        assert gates["samples_remaining"] == 1

    @pytest.mark.unit
    def test_custom_required_days(self):
        """自定义达标天数: required=5, 5天/5条 → 都 PASS"""
        progress = make_progress_dict(days_completed=5)
        records = make_real_records(5)
        gates = check_gates(progress, records, required_days=5)
        assert gates["all_passed"] is True

    @pytest.mark.unit
    def test_empty_records(self):
        """空记录: 14天/0条 → GATE-B FAIL (数据完全缺失)"""
        progress = make_progress_dict(days_completed=14)
        gates = check_gates(progress, [], required_days=14)
        assert gates["gate_a_passed"] is True
        assert gates["gate_b_passed"] is False
        assert gates["real_count"] == 0


# ============================================================
# 2. run_drift_detection() 集成器层 5 天门槛
# ============================================================


class TestIntegratorThreshold:
    """测试 run_drift_detection() 的 MIN_REAL_SAMPLES_FOR_DRIFT=5 门槛.

    集成器层是看门狗 14 天门槛的兜底: 即使强制触发跳过看门狗,
    <5 条真实数据仍返回 skipped, 不产生无意义告警.
    """

    @pytest.mark.unit
    def test_skip_when_zero_records(self):
        """0 条 real → skipped"""
        result = run_drift_detection([])
        assert result["status"] == "skipped"
        assert "insufficient_real_data" in result["reason"]
        assert "0" in result["reason"]

    @pytest.mark.unit
    def test_skip_when_below_5(self):
        """4 条 real (< 5) → skipped"""
        records = make_real_records(4)
        result = run_drift_detection(records)
        assert result["status"] == "skipped"
        assert "insufficient_real_data" in result["reason"]

    @pytest.mark.unit
    def test_execute_at_5(self, mock_compute_prediction_drift):
        """5 条 real (= MIN_REAL_SAMPLES_FOR_DRIFT) → 执行漂移检测"""
        records = make_real_records(5)
        result = run_drift_detection(records)
        assert result["status"] == "completed"
        assert mock_compute_prediction_drift.call_count == 1

    @pytest.mark.unit
    def test_execute_at_14(self, mock_compute_prediction_drift):
        """14 条 real → 执行漂移检测"""
        records = make_real_records(14)
        result = run_drift_detection(records)
        assert result["status"] == "completed"

    @pytest.mark.unit
    def test_threshold_value_is_5(self):
        """验证 MIN_REAL_SAMPLES_FOR_DRIFT 常量值为 5"""
        assert MIN_REAL_SAMPLES_FOR_DRIFT == 5

    @pytest.mark.unit
    def test_baseline_ratio_is_0_6(self):
        """验证 BASELINE_RATIO 常量值为 0.6"""
        assert BASELINE_RATIO == 0.6


# ============================================================
# 3. 切分逻辑边界测试 (BASELINE_RATIO=0.6)
# ============================================================


class TestSplitLogic:
    """测试基线/当前切分逻辑.

    split_idx = max(2, int(n * 0.6))
    if split_idx >= n: split_idx = n - 1  (至少给 current 留 1 条)
    baseline = records[:split_idx], current = records[split_idx:]
    """

    @pytest.mark.unit
    def test_split_5_days(self, mock_compute_prediction_drift):
        """5 天切分: baseline=3, current=2 (max(2,int(3.0))=3)"""
        records = make_real_records(5)
        run_drift_detection(records)
        call = mock_compute_prediction_drift.call_args
        assert len(call.kwargs["baseline"]) == 3
        assert len(call.kwargs["current"]) == 2

    @pytest.mark.unit
    def test_split_6_days(self, mock_compute_prediction_drift):
        """6 天切分: baseline=3, current=3 (max(2,int(3.6))=3)"""
        records = make_real_records(6)
        run_drift_detection(records)
        call = mock_compute_prediction_drift.call_args
        assert len(call.kwargs["baseline"]) == 3
        assert len(call.kwargs["current"]) == 3

    @pytest.mark.unit
    def test_split_14_days(self, mock_compute_prediction_drift):
        """14 天切分: baseline=8, current=6 (max(2,int(8.4))=8) — 文档 §4.3 核心"""
        records = make_real_records(14)
        run_drift_detection(records)
        call = mock_compute_prediction_drift.call_args
        assert len(call.kwargs["baseline"]) == 8
        assert len(call.kwargs["current"]) == 6

    @pytest.mark.unit
    def test_split_20_days(self, mock_compute_prediction_drift):
        """20 天切分: baseline=12, current=8 (max(2,int(12.0))=12)"""
        records = make_real_records(20)
        run_drift_detection(records)
        call = mock_compute_prediction_drift.call_args
        assert len(call.kwargs["baseline"]) == 12
        assert len(call.kwargs["current"]) == 8

    @pytest.mark.unit
    def test_baseline_dates_recorded(self, mock_compute_prediction_drift):
        """验证告警条目记录了 baseline_dates / current_dates"""
        records = make_real_records(14)
        result = run_drift_detection(records)
        assert "baseline_dates" in result
        assert "current_dates" in result
        assert len(result["baseline_dates"]) == 8
        assert len(result["current_dates"]) == 6


# ============================================================
# 4. run_watchdog() 触发决策 (mock 依赖)
# ============================================================


class TestWatchdogTriggerDecision:
    """测试 run_watchdog() 的触发决策逻辑.

    should_trigger = (all_passed OR force_trigger) AND NOT dry_run
    """

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_no_trigger_when_both_gates_fail(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """6/14 天未达标 → 不触发 (正常流程)"""
        mock_progress.return_value = make_progress_dict(6)
        mock_records.return_value = (make_real_records(6), {"real": 6})
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        assert mock_trigger.call_count == 0
        assert result["trigger_result"]["triggered"] is False

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_trigger_when_both_gates_pass(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """14/14 天达标 → 触发漂移判定"""
        mock_progress.return_value = make_progress_dict(14)
        mock_records.return_value = (make_real_records(14), {"real": 14})
        mock_trigger.return_value = {"triggered": True, "alert_status": "completed"}
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        assert mock_trigger.call_count == 1
        assert result["trigger_result"]["triggered"] is True

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_force_trigger_skips_gate_check(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """6/14 天 + force_trigger → 跳过门槛, 触发漂移判定"""
        mock_progress.return_value = make_progress_dict(6)
        mock_records.return_value = (make_real_records(6), {"real": 6})
        mock_trigger.return_value = {"triggered": True, "alert_status": "completed"}
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)
        assert mock_trigger.call_count == 1
        assert result["gates"]["all_passed"] is False  # 门槛确实没过
        assert result["trigger_result"]["triggered"] is True  # 但强制触发了

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_dry_run_never_triggers(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """14/14 天达标 + dry_run → 不触发 (试运行)"""
        mock_progress.return_value = make_progress_dict(14)
        mock_records.return_value = (make_real_records(14), {"real": 14})
        result = run_watchdog(required_days=14, dry_run=True, force_trigger=False)
        assert mock_trigger.call_count == 0
        assert mock_log.call_count == 0  # dry_run 不写日志

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_dry_run_with_force_still_no_trigger(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """6/14 天 + force_trigger + dry_run → 不触发 (dry_run 优先级最高)"""
        mock_progress.return_value = make_progress_dict(6)
        mock_records.return_value = (make_real_records(6), {"real": 6})
        result = run_watchdog(required_days=14, dry_run=True, force_trigger=True)
        assert mock_trigger.call_count == 0

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_only_gate_a_passes_no_trigger(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """14天/3条 (仅 GATE-A 通过) → 不触发 (GATE-B 拦截回测污染)"""
        mock_progress.return_value = make_progress_dict(14)
        mock_records.return_value = (make_real_records(3), {"real": 3})
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        assert mock_trigger.call_count == 0
        assert result["gates"]["gate_a_passed"] is True
        assert result["gates"]["gate_b_passed"] is False


# ============================================================
# 5. 两层协同测试 (看门狗 14 天 + 集成器 5 天)
# ============================================================


class TestTwoLayerInteraction:
    """测试两层门槛协同: 看门狗 14 天 (统计可信) + 集成器 5 天 (最低可运行).

    场景矩阵:
        - 正常流程 < 14 天: 看门狗拦截, 集成器不被调用
        - 正常流程 >= 14 天: 看门狗放行, 集成器执行
        - 强制触发 + < 5 条: 看门狗放行, 集成器兜底返回 skipped
        - 强制触发 + >= 5 条: 看门狗放行, 集成器执行
    """

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_normal_flow_below_14_days_no_trigger(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """正常流程 6 天: 看门狗 14 天门槛拦截, 集成器 5 天门槛不触发"""
        mock_progress.return_value = make_progress_dict(6)
        mock_records.return_value = (make_real_records(6), {"real": 6})
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        assert mock_trigger.call_count == 0

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_normal_flow_at_14_days_triggers(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """正常流程 14 天: 看门狗放行, 集成器执行"""
        mock_progress.return_value = make_progress_dict(14)
        mock_records.return_value = (make_real_records(14), {"real": 14})
        mock_trigger.return_value = {"triggered": True, "alert_status": "completed"}
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        assert mock_trigger.call_count == 1

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_force_trigger_with_3_samples_skipped_by_integrator(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """强制触发 + 3 条样本: 看门狗放行, 集成器兜底返回 skipped (3 < 5)"""
        mock_progress.return_value = make_progress_dict(3)
        mock_records.return_value = (make_real_records(3), {"real": 3})
        mock_trigger.return_value = {
            "triggered": True,
            "alert_status": "skipped",
            "error": "insufficient_real_data (3 < 5)",
        }
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)
        assert mock_trigger.call_count == 1
        assert result["trigger_result"]["alert_status"] == "skipped"

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_force_trigger_with_4_samples_skipped_by_integrator(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """强制触发 + 4 条样本: 集成器兜底返回 skipped (4 < 5, 边界值)"""
        mock_progress.return_value = make_progress_dict(4)
        mock_records.return_value = (make_real_records(4), {"real": 4})
        mock_trigger.return_value = {
            "triggered": True,
            "alert_status": "skipped",
            "error": "insufficient_real_data (4 < 5)",
        }
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)
        assert mock_trigger.call_count == 1
        assert result["trigger_result"]["alert_status"] == "skipped"

    @pytest.mark.unit
    @patch("scripts.observation_watchdog.write_watchdog_log")
    @patch("scripts.observation_watchdog.trigger_drift_evaluation")
    @patch("scripts.observation_watchdog.load_cleaned_real_records")
    @patch("scripts.observation_watchdog.load_observation_progress")
    def test_force_trigger_with_5_samples_executes(
        self, mock_progress, mock_records, mock_trigger, mock_log,
    ):
        """强制触发 + 5 条样本: 集成器执行 (5 >= 5, 刚好过兜底门槛)"""
        mock_progress.return_value = make_progress_dict(5)
        mock_records.return_value = (make_real_records(5), {"real": 5})
        mock_trigger.return_value = {"triggered": True, "alert_status": "completed"}
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)
        assert mock_trigger.call_count == 1
        assert result["trigger_result"]["alert_status"] == "completed"

    @pytest.mark.unit
    def test_two_layer_threshold_values(self):
        """验证两层门槛常量: 集成器 5 天 + 看门狗 21 天 (2026-08-09 由 14 上调)"""
        assert MIN_REAL_SAMPLES_FOR_DRIFT == 5  # 集成器层
        from scripts.observation_watchdog import DEFAULT_REQUIRED_DAYS
        assert DEFAULT_REQUIRED_DAYS == 21  # 看门狗层


# ============================================================
# 6. 集成器层门槛的直接验证 (不通过看门狗, 直接测试 run_drift_detection)
# ============================================================


class TestIntegratorThresholdDirect:
    """直接测试集成器层 5 天门槛的 skipped 返回值.

    这组测试不 mock compute_prediction_drift, 验证 < 5 条时
    run_drift_detection 确实返回 skipped 状态 (不走漂移计算路径).
    """

    @pytest.mark.unit
    @pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
    def test_skip_below_5(self, n):
        """参数化: 0~4 条 real 全部返回 skipped"""
        records = make_real_records(n)
        result = run_drift_detection(records)
        assert result["status"] == "skipped"
        assert "insufficient_real_data" in result["reason"]
        assert str(n) in result["reason"]

    @pytest.mark.unit
    @pytest.mark.parametrize("n", [5, 6, 7, 14, 20, 30])
    def test_execute_at_or_above_5(self, mock_compute_prediction_drift, n):
        """参数化: 5~30 条 real 全部执行漂移检测"""
        records = make_real_records(n)
        result = run_drift_detection(records)
        assert result["status"] == "completed"
        assert mock_compute_prediction_drift.call_count == 1
