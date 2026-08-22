"""test_gradual_rollout_unit.py — 灰度发布管理器单元测试

覆盖:
  - should_run_on_date() 灰度判断
  - RolloutStatus 序列化/反序列化
  - load/save 状态持久化
  - check_health() 健康度检查
  - advance_stage() 阶段推进
  - rollback() 紧急回滚
  - observation_days_elapsed() 观察天数计算
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# should_run_on_date 灰度判断
# ============================================================

class TestShouldRunOnDate:
    """should_run_on_date() 灰度比例判断."""

    @pytest.mark.unit
    def test_zero_percent_always_false(self):
        from scripts.gradual_rollout_manager import should_run_on_date
        assert should_run_on_date("2026-08-22", 0) is False
        assert should_run_on_date("2026-01-01", 0) is False

    @pytest.mark.unit
    def test_hundred_percent_always_true(self):
        from scripts.gradual_rollout_manager import should_run_on_date
        assert should_run_on_date("2026-08-22", 100) is True
        assert should_run_on_date("2026-01-01", 100) is True

    @pytest.mark.unit
    def test_same_date_same_result(self):
        """同一日期 + 同一比例 → 同一结果 (确定性)."""
        from scripts.gradual_rollout_manager import should_run_on_date
        r1 = should_run_on_date("2026-08-22", 30)
        r2 = should_run_on_date("2026-08-22", 30)
        assert r1 == r2

    @pytest.mark.unit
    def test_distribution_approximates_percent(self):
        """大量日期的灰度分布近似百分比."""
        from datetime import date, timedelta

        from scripts.gradual_rollout_manager import should_run_on_date

        total = 1000
        enabled = sum(
            1 for i in range(total)
            if should_run_on_date((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 30)
        )
        ratio = enabled / total
        assert 0.25 < ratio < 0.35  # ~30% ± 5%

    @pytest.mark.unit
    def test_different_dates_different_results(self):
        """不同日期有不同结果 (不是全 true 或全 false)."""
        from scripts.gradual_rollout_manager import should_run_on_date
        results = [should_run_on_date(f"2026-01-{d:02d}", 50) for d in range(1, 31)]
        assert True in results
        assert False in results


# ============================================================
# RolloutStatus 序列化
# ============================================================

class TestRolloutStatus:
    """RolloutStatus 序列化/反序列化."""

    @pytest.mark.unit
    def test_default_status(self):
        from scripts.gradual_rollout_manager import RolloutStage, RolloutStatus
        s = RolloutStatus()
        assert s.stage == RolloutStage.NOT_STARTED
        assert s.percent == 0

    @pytest.mark.unit
    def test_to_dict_from_dict_round_trip(self):
        from scripts.gradual_rollout_manager import RolloutStage, RolloutStatus
        s = RolloutStatus()
        s.stage = RolloutStage.STAGE_1_10PCT
        s.percent = 10
        s.stage_start_date = "2026-08-22"
        d = s.to_dict()
        s2 = RolloutStatus.from_dict(d)
        assert s2.percent == 10
        assert s2.stage_start_date == "2026-08-22"

    @pytest.mark.unit
    def test_from_dict_invalid_stage(self):
        from scripts.gradual_rollout_manager import RolloutStage, RolloutStatus
        d = {"stage": "INVALID_STAGE", "percent": 50}
        s = RolloutStatus.from_dict(d)
        assert s.stage == RolloutStage.NOT_STARTED


# ============================================================
# load/save 状态持久化
# ============================================================

class TestStatusPersistence:
    """load_status / save_status 持久化."""

    @pytest.mark.unit
    def test_save_then_load(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            load_status,
            save_status,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        s = RolloutStatus()
        s.stage = RolloutStage.STAGE_2_50PCT
        s.percent = 50
        s.stage_start_date = "2026-08-20"
        save_status(s)

        loaded = load_status()
        assert loaded.stage == RolloutStage.STAGE_2_50PCT
        assert loaded.percent == 50
        assert loaded.stage_start_date == "2026-08-20"

    @pytest.mark.unit
    def test_load_nonexistent_returns_default(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            load_status,
        )

        fake_path = tmp_path / "nonexistent.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)
        s = load_status()
        assert s.stage == RolloutStage.NOT_STARTED


# ============================================================
# check_health 健康度检查
# ============================================================

class TestCheckHealth:
    """check_health() 健康度检查."""

    @pytest.mark.unit
    def test_metrics_unavailable_fails(self, monkeypatch):
        from scripts.gradual_rollout_manager import RolloutStatus, check_health

        with patch("scripts.gradual_rollout_manager.collect_health_metrics", return_value={}):
            status = RolloutStatus()
            passed, reason = check_health(status)
        assert passed is False
        assert "metrics_unavailable" in reason

    @pytest.mark.unit
    def test_insufficient_cycles_fails(self, monkeypatch):
        from scripts.gradual_rollout_manager import RolloutStatus, check_health

        metrics = {"total_cycles": 2, "l2_promote_rate": 0.8, "avg_latency_ms": 100, "evolution_trigger_rate": 0.5}
        with patch("scripts.gradual_rollout_manager.collect_health_metrics", return_value=metrics):
            status = RolloutStatus()
            passed, reason = check_health(status)
        assert passed is False
        assert "insufficient" in reason

    @pytest.mark.unit
    def test_low_promote_rate_fails(self, monkeypatch):
        from scripts.gradual_rollout_manager import RolloutStatus, check_health

        metrics = {"total_cycles": 10, "l2_promote_rate": 0.1, "avg_latency_ms": 100, "evolution_trigger_rate": 0.5}
        with patch("scripts.gradual_rollout_manager.collect_health_metrics", return_value=metrics):
            status = RolloutStatus()
            passed, reason = check_health(status)
        assert passed is False
        assert "l2_promote_rate" in reason

    @pytest.mark.unit
    def test_high_latency_fails(self, monkeypatch):
        from scripts.gradual_rollout_manager import RolloutStatus, check_health

        metrics = {"total_cycles": 10, "l2_promote_rate": 0.8, "avg_latency_ms": 10000, "evolution_trigger_rate": 0.5}
        with patch("scripts.gradual_rollout_manager.collect_health_metrics", return_value=metrics):
            status = RolloutStatus()
            passed, reason = check_health(status)
        assert passed is False
        assert "latency" in reason

    @pytest.mark.unit
    def test_all_checks_pass(self, monkeypatch):
        from scripts.gradual_rollout_manager import RolloutStatus, check_health

        metrics = {"total_cycles": 10, "l2_promote_rate": 0.8, "avg_latency_ms": 200, "evolution_trigger_rate": 0.5}
        with patch("scripts.gradual_rollout_manager.collect_health_metrics", return_value=metrics):
            status = RolloutStatus()
            passed, reason = check_health(status)
        assert passed is True
        assert "passed" in reason


# ============================================================
# advance_stage 阶段推进
# ============================================================

class TestAdvanceStage:
    """advance_stage() 阶段推进."""

    @pytest.mark.unit
    def test_advance_from_not_started(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            advance_stage,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        success, reason = advance_stage(status)
        assert success is True
        assert status.stage == RolloutStage.STAGE_1_10PCT
        assert status.percent == 30  # Stage 1 调整为 30% (2026-08-22)

    @pytest.mark.unit
    def test_advance_paused_fails(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            advance_stage,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        status.stage = RolloutStage.PAUSED
        success, reason = advance_stage(status)
        assert success is False
        assert "cannot advance" in reason

    @pytest.mark.unit
    def test_advance_rolled_back_fails(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            advance_stage,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        status.stage = RolloutStage.ROLLED_BACK
        success, reason = advance_stage(status)
        assert success is False

    @pytest.mark.unit
    def test_advance_max_stage_fails(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            advance_stage,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        status.stage = RolloutStage.STAGE_3_100PCT
        success, reason = advance_stage(status)
        assert success is False
        assert "max_stage" in reason

    @pytest.mark.unit
    def test_advance_with_observation_not_elapsed(self, tmp_path, monkeypatch):
        from datetime import date

        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            advance_stage,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        status.stage = RolloutStage.STAGE_1_10PCT
        status.percent = 10
        status.stage_start_date = date.today().isoformat()  # 今天开始, 0天观察
        success, reason = advance_stage(status)
        assert success is False
        assert "observation" in reason


# ============================================================
# rollback 紧急回滚
# ============================================================

class TestRollback:
    """rollback() 紧急回滚."""

    @pytest.mark.unit
    def test_rollback_sets_rolled_back(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            rollback,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)
        monkeypatch.setattr("utils.infra.feature_flags.disable", lambda *a, **k: None)

        status = RolloutStatus()
        status.stage = RolloutStage.STAGE_2_50PCT
        status.percent = 50
        rollback(status, "test_rollback")
        assert status.stage == RolloutStage.ROLLED_BACK
        assert status.percent == 0

    @pytest.mark.unit
    def test_rollback_adds_history(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            rollback,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)
        monkeypatch.setattr("utils.infra.feature_flags.disable", lambda *a, **k: None)

        status = RolloutStatus()
        status.stage = RolloutStage.STAGE_1_10PCT
        rollback(status, "test")
        assert len(status.history) >= 1
        assert status.history[-1]["action"] == "rollback"


# ============================================================
# observation_days_elapsed 观察天数
# ============================================================

class TestObservationDays:
    """observation_days_elapsed() 观察天数计算."""

    @pytest.mark.unit
    def test_no_start_date(self):
        from scripts.gradual_rollout_manager import RolloutStatus, observation_days_elapsed
        status = RolloutStatus()
        assert observation_days_elapsed(status) == 0

    @pytest.mark.unit
    def test_three_days_elapsed(self):
        from datetime import date, timedelta

        from scripts.gradual_rollout_manager import RolloutStatus, observation_days_elapsed

        status = RolloutStatus()
        status.stage_start_date = (date.today() - timedelta(days=3)).isoformat()
        assert observation_days_elapsed(status) == 3


# ============================================================
# should_run_today 今日灰度判断
# ============================================================

class TestShouldRunToday:
    """should_run_today() 今日灰度判断."""

    @pytest.mark.unit
    def test_zero_percent_false(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import should_run_today

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)
        assert should_run_today("2026-08-22") is False

    @pytest.mark.unit
    def test_hundred_percent_true(self, tmp_path, monkeypatch):
        from scripts.gradual_rollout_manager import (
            RolloutStage,
            RolloutStatus,
            save_status,
            should_run_today,
        )

        fake_path = tmp_path / "rollout_status.json"
        monkeypatch.setattr("scripts.gradual_rollout_manager.STATUS_PATH", fake_path)

        status = RolloutStatus()
        status.stage = RolloutStage.STAGE_3_100PCT
        status.percent = 100
        save_status(status)
        assert should_run_today("2026-08-22") is True


# ============================================================
# orchestrator _check_rollout_eligible
# ============================================================

class TestOrchestratorRolloutCheck:
    """EvolutionOrchestratorV2._check_rollout_eligible() 灰度检查."""

    @pytest.mark.unit
    def test_rollout_manager_unavailable_returns_true(self):
        """灰度管理器不可用 → 不阻塞 (返回 True)."""
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        mock_memory = MagicMock()
        mock_memory.record.return_value = "pid"
        orch = EvolutionOrchestratorV2(memory=mock_memory, guard=MagicMock(), kill_switch=None)
        with patch("scripts.gradual_rollout_manager.should_run_today", side_effect=ImportError("no module")):
            result = orch._check_rollout_eligible()
        assert result is True

    @pytest.mark.unit
    def test_rollout_excluded_disables_cycle(self):
        """灰度未命中 → run_cycle 返回 DISABLED."""
        from utils.evolution.orchestrator import (
            CYCLE_STATUS_DISABLED,
            EvolutionOrchestratorV2,
        )

        mock_memory = MagicMock()
        mock_memory.record.return_value = "pid"
        orch = EvolutionOrchestratorV2(memory=mock_memory, guard=MagicMock(), kill_switch=None)
        orch._enabled = True
        with patch("scripts.gradual_rollout_manager.should_run_today", return_value=False):
            result = orch.run_cycle()
        assert result.status == CYCLE_STATUS_DISABLED
        assert "rollout" in result.reason

    @pytest.mark.unit
    def test_rollout_included_proceeds(self):
        """灰度命中 → run_cycle 正常执行 (不因灰度被禁用)."""
        from utils.evolution.orchestrator import (
            EvolutionOrchestratorV2,
        )

        mock_memory = MagicMock()
        mock_memory.record.return_value = "pid"
        orch = EvolutionOrchestratorV2(memory=mock_memory, guard=MagicMock(), kill_switch=None)
        orch._enabled = True
        with patch("scripts.gradual_rollout_manager.should_run_today", return_value=True):
            result = orch.run_cycle()
        # 不应因灰度被禁用 (可能因其他原因 disabled/degraded, 但不是 rollout)
        assert "rollout" not in result.reason
