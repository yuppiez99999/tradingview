#!/usr/bin/env python
"""
test_phase_b_shadow_stable.py — Phase B shadow 守卫核心逻辑单元测试

测试范围:
    - evaluate_daily_shadow_health() 三维健康判定
    - update_stable_days() 异常归零
    - _check_kill_switch_inactive() kill_switch 状态检测
    - _check_no_lookahead_bias() 前视偏差检测
    - PhaseBStatus.from_dict() 向后兼容
    - generate_shadow_stable_report() 7天报告生成
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from phase_b_progressive_enabler import (  # noqa: E402
    DailyHealthVerdict,
    PhaseBStatus,
    _check_kill_switch_inactive,
    _check_no_lookahead_bias,
    evaluate_daily_shadow_health,
    generate_shadow_stable_report,
    should_run_daily_health_check,
    update_stable_days,
)


class TestShouldRunDailyHealthCheck:
    """should_run_daily_health_check() 阶段覆盖测试 (v8.6.16 回归).

    修复背景: cmd_auto 健康检查原仅 drift_monitor 阶段执行, 推进到 abtest 后
    D11 consecutive_stable_days 断链卡 2/7。回归断言所有已运行阶段都须记录。
    """

    def test_all_running_stages_checked(self) -> None:
        """STAGE_1~STAGE_4 全部已运行阶段都须执行每日健康检查."""
        for stage in ("drift_monitor", "abtest", "auto_retrain", "orchestrator"):
            assert (
                should_run_daily_health_check(stage) is True
            ), f"{stage} 应执行健康检查"

    def test_non_running_stages_skipped(self) -> None:
        """未启动/非运行态不执行."""
        for stage in ("waiting_observation", "ready", "paused", "rollback"):
            assert (
                should_run_daily_health_check(stage) is False
            ), f"{stage} 不应执行健康检查"


class TestPhaseBStatusFromDict:
    """PhaseBStatus.from_dict() 向后兼容测试."""

    def test_full_dict(self) -> None:
        d = {
            "stage": "stage_1",
            "observation_days_completed": 5,
            "consecutive_stable_days": 3,
            "stable_days_target": 7,
            "min_shadow_samples": 20,
            "daily_health_log": [{"date": "2026-08-20", "healthy": True}],
        }
        status = PhaseBStatus.from_dict(d)
        assert status.stage == "stage_1"
        assert status.observation_days_completed == 5
        assert status.consecutive_stable_days == 3
        assert status.stable_days_target == 7
        assert status.min_shadow_samples == 20
        assert len(status.daily_health_log) == 1

    def test_old_dict_without_new_fields(self) -> None:
        """旧 phase_b_status.json 无新字段时用默认值回退."""
        d = {"stage": "waiting_observation", "observation_days_completed": 10}
        status = PhaseBStatus.from_dict(d)
        assert status.stage == "waiting_observation"
        assert status.observation_days_completed == 10
        assert status.consecutive_stable_days == 0
        assert status.stable_days_target == 7
        assert status.min_shadow_samples == 20
        assert status.daily_health_log == []

    def test_empty_dict(self) -> None:
        status = PhaseBStatus.from_dict({})
        assert status.stage == "waiting_observation"
        assert status.consecutive_stable_days == 0


class TestCheckKillSwitchInactive:
    """_check_kill_switch_inactive() 测试."""

    def test_no_state_file_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler._KILL_SWITCH_STATE",
            tmp_path / "nonexistent.json",
        )
        ok, reason = _check_kill_switch_inactive("2026-08-20")
        assert ok is True
        assert reason == ""

    def test_triggered_on_date_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state_file = tmp_path / "kill_switch_state.json"
        state_file.write_text(
            json.dumps(
                {
                    "USE_DRIFT_DETECTOR": {"triggered_date": "2026-08-20"},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "phase_b_progressive_enabler._KILL_SWITCH_STATE", state_file
        )
        ok, reason = _check_kill_switch_inactive("2026-08-20")
        assert ok is False
        assert "USE_DRIFT_DETECTOR" in reason

    def test_triggered_on_different_date_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state_file = tmp_path / "kill_switch_state.json"
        state_file.write_text(
            json.dumps(
                {
                    "USE_DRIFT_DETECTOR": {"triggered_date": "2026-08-19"},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "phase_b_progressive_enabler._KILL_SWITCH_STATE", state_file
        )
        ok, reason = _check_kill_switch_inactive("2026-08-20")
        assert ok is True


class TestCheckNoLookaheadBias:
    """_check_no_lookahead_bias() 测试."""

    def test_no_report_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler._HONEST_VALIDATION_DIR", tmp_path
        )
        ok, reason = _check_no_lookahead_bias("2026-08-20")
        assert ok is True
        assert reason == ""

    def test_lookahead_detected_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = tmp_path / "2026-08-20.json"
        report.write_text(
            json.dumps({"lookahead_bias_detected": True}), encoding="utf-8"
        )
        monkeypatch.setattr(
            "phase_b_progressive_enabler._HONEST_VALIDATION_DIR", tmp_path
        )
        ok, reason = _check_no_lookahead_bias("2026-08-20")
        assert ok is False
        assert "lookahead" in reason

    def test_no_lookahead_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = tmp_path / "2026-08-20.json"
        report.write_text(
            json.dumps({"lookahead_bias_detected": False}), encoding="utf-8"
        )
        monkeypatch.setattr(
            "phase_b_progressive_enabler._HONEST_VALIDATION_DIR", tmp_path
        )
        ok, reason = _check_no_lookahead_bias("2026-08-20")
        assert ok is True


class TestEvaluateDailyShadowHealth:
    """evaluate_daily_shadow_health() 三维健康判定测试."""

    def _setup_shadow_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict]
    ) -> None:
        """设置 shadow daily_returns.jsonl 测试数据."""
        data_file = tmp_path / "daily_returns.jsonl"
        with data_file.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        monkeypatch.setattr("phase_b_progressive_enabler.OBSERVATION_DATA", data_file)
        monkeypatch.setattr(
            "phase_b_progressive_enabler._KILL_SWITCH_STATE", tmp_path / "no_ks.json"
        )
        monkeypatch.setattr(
            "phase_b_progressive_enabler._HONEST_VALIDATION_DIR", tmp_path / "hv"
        )

    def test_data_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler.OBSERVATION_DATA",
            tmp_path / "nonexistent.jsonl",
        )
        verdict = evaluate_daily_shadow_health("2026-08-20")
        assert verdict.healthy is False
        assert verdict.reason == "shadow_data_missing"

    def test_sample_insufficient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [{"date": "2026-08-20", "daily_return": 0.01}]
        self._setup_shadow_data(tmp_path, monkeypatch, records)
        verdict = evaluate_daily_shadow_health("2026-08-20", min_samples=20)
        assert verdict.healthy is False
        assert verdict.reason == "sample_insufficient"

    def test_no_daily_return_for_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [
            {"date": f"2026-08-{i:02d}", "daily_return": 0.01} for i in range(1, 21)
        ]
        self._setup_shadow_data(tmp_path, monkeypatch, records)
        verdict = evaluate_daily_shadow_health("2026-08-25")
        assert verdict.healthy is False
        assert verdict.reason == "no_daily_return"

    def test_healthy_when_all_three_green(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [
            {"date": f"2026-08-{i:02d}", "daily_return": 0.001} for i in range(1, 21)
        ]
        records.append({"date": "2026-08-20", "daily_return": 0.005})
        self._setup_shadow_data(tmp_path, monkeypatch, records)
        verdict = evaluate_daily_shadow_health("2026-08-20", min_samples=20)
        assert verdict.healthy is True
        assert verdict.reason == "ok"
        assert verdict.daily_return == 0.005

    def test_kill_switch_triggered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [
            {"date": f"2026-08-{i:02d}", "daily_return": 0.001} for i in range(1, 21)
        ]
        self._setup_shadow_data(tmp_path, monkeypatch, records)
        ks_file = tmp_path / "kill_switch_state.json"
        ks_file.write_text(
            json.dumps({"USE_DRIFT_DETECTOR": {"triggered_date": "2026-08-20"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr("phase_b_progressive_enabler._KILL_SWITCH_STATE", ks_file)
        verdict = evaluate_daily_shadow_health("2026-08-20", min_samples=20)
        assert verdict.healthy is False
        assert "kill_switch" in verdict.reason or "USE_DRIFT" in verdict.reason


class TestUpdateStableDays:
    """update_stable_days() 异常归零测试."""

    def test_healthy_increments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler._SHADOW_DAILY_HEALTH_LOG",
            tmp_path / "health.jsonl",
        )
        status = PhaseBStatus(consecutive_stable_days=3)
        verdict = DailyHealthVerdict(date="2026-08-20", healthy=True, reason="ok")
        new_status = update_stable_days(status, verdict)
        assert new_status.consecutive_stable_days == 4

    def test_unhealthy_resets_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler._SHADOW_DAILY_HEALTH_LOG",
            tmp_path / "health.jsonl",
        )
        status = PhaseBStatus(consecutive_stable_days=5)
        verdict = DailyHealthVerdict(
            date="2026-08-20", healthy=False, reason="kill_switch_triggered"
        )
        new_status = update_stable_days(status, verdict)
        assert new_status.consecutive_stable_days == 0

    def test_no_daily_return_does_not_reset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v8.7 回归: no_daily_return (当日数据未生成) 跳过, 不归零不记录.

        修复背景: 盘前/盘中误跑 --auto 时 daily_returns.jsonl 尚无当日条目,
        evaluate_daily_shadow_health 返回 no_daily_return, 旧实现把它当 unhealthy
        归零连续稳定天数 (实测 08-26 从 3→0)。数据未生成是时序问题而非健康异常。
        """
        monkeypatch.setattr(
            "phase_b_progressive_enabler._SHADOW_DAILY_HEALTH_LOG",
            tmp_path / "health.jsonl",
        )
        status = PhaseBStatus(
            consecutive_stable_days=3,
            daily_health_log=[{"date": "2026-08-25", "healthy": True}],
        )
        verdict = DailyHealthVerdict(
            date="2026-08-26", healthy=False, reason="no_daily_return"
        )
        new_status = update_stable_days(status, verdict)
        assert new_status.consecutive_stable_days == 3  # 保持, 不归零
        assert len(new_status.daily_health_log) == 1  # 不追加污染记录

    def test_reject_historical_backfill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """补录历史样本 (date 早于最后记录) → 拒绝, 不修改状态."""
        monkeypatch.setattr(
            "phase_b_progressive_enabler._SHADOW_DAILY_HEALTH_LOG",
            tmp_path / "health.jsonl",
        )
        status = PhaseBStatus(
            consecutive_stable_days=3,
            daily_health_log=[{"date": "2026-08-20", "healthy": True}],
        )
        verdict = DailyHealthVerdict(date="2026-08-19", healthy=True, reason="ok")
        new_status = update_stable_days(status, verdict)
        assert new_status.consecutive_stable_days == 3  # 未变
        assert len(new_status.daily_health_log) == 1  # 未追加

    def test_appends_to_health_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "phase_b_progressive_enabler._SHADOW_DAILY_HEALTH_LOG",
            tmp_path / "health.jsonl",
        )
        status = PhaseBStatus(consecutive_stable_days=0)
        verdict = DailyHealthVerdict(
            date="2026-08-20", healthy=True, reason="ok", daily_return=0.005
        )
        new_status = update_stable_days(status, verdict)
        assert len(new_status.daily_health_log) == 1
        assert new_status.daily_health_log[0]["date"] == "2026-08-20"
        assert new_status.daily_health_log[0]["cumulative_stable_days"] == 1


class TestGenerateShadowStableReport:
    """generate_shadow_stable_report() 7天报告生成测试."""

    def test_report_generated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("phase_b_progressive_enabler.PROJECT_ROOT", tmp_path)
        health_log = [
            {
                "date": f"2026-08-{i:02d}",
                "healthy": True,
                "reason": "ok",
                "daily_return": 0.001,
            }
            for i in range(1, 21)
        ]
        status = PhaseBStatus(
            consecutive_stable_days=7,
            stable_days_target=7,
            min_shadow_samples=20,
            daily_health_log=health_log,
        )
        report_path = generate_shadow_stable_report(status)
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["summary"]["stable_days"] == 7
        assert report["summary"]["target_met"] is True
        assert report["summary"]["sprint1_admission"] is True

    def test_target_not_met(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("phase_b_progressive_enabler.PROJECT_ROOT", tmp_path)
        status = PhaseBStatus(
            consecutive_stable_days=3,
            stable_days_target=7,
            min_shadow_samples=20,
            daily_health_log=[
                {"date": f"2026-08-{i:02d}", "healthy": True, "reason": "ok"}
                for i in range(18, 21)
            ],
        )
        report_path = generate_shadow_stable_report(status)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["summary"]["target_met"] is False
        assert report["summary"]["sprint1_admission"] is False
