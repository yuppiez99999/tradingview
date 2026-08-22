"""test_l2_shadow_integration_unit.py — L2 影子验证 ShadowAccountAdapter 集成 + 闭环健康度指标单元测试

覆盖:
  - _get_shadow_adapter() 懒加载
  - _load_shadow_daily_returns() 文件读取
  - _route_l2() DSR promote/rollback/fail-fast/降级
  - get_loop_health_metrics() 指标快照
  - _update_loop_health() L1/L2/L3 计数
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 辅助: 构造 orchestrator + mock 组件
# ============================================================

def _make_orchestrator(
    shadow_adapter=None,
    l2_dsr_threshold=0.5,
    enabled=True,
):
    """构造 EvolutionOrchestratorV2 (mock memory/guard, 可选 shadow_adapter)."""
    from utils.evolution.orchestrator import EvolutionOrchestratorV2

    mock_memory = MagicMock()
    mock_memory.record.return_value = "test-pid-001"

    orch = EvolutionOrchestratorV2(
        memory=mock_memory,
        guard=MagicMock(),
        kill_switch=None,
        v1_orchestrator=None,
        shadow_adapter=shadow_adapter,
        l2_dsr_threshold=l2_dsr_threshold,
    )
    orch._enabled = enabled
    return orch


def _make_mock_adapter(dsr=0.8, run_success=True, fail_fast=False, fail_fast_reason=None):
    """构造 mock ShadowAccountAdapter."""
    adapter = MagicMock()

    # run_shadow 返回
    run_result = MagicMock()
    run_result.success = run_success
    run_result.fail_fast_triggered = fail_fast
    run_result.fail_fast_reason = fail_fast_reason
    run_result.final_nav = 1.05
    adapter.run_shadow.return_value = run_result

    # get_metrics 返回
    metrics = MagicMock()
    metrics.dsr = dsr
    metrics.annual_return = 0.12
    metrics.max_drawdown = -0.08
    metrics.sharpe_cv = 0.3
    adapter.get_metrics.return_value = metrics

    return adapter


def _make_proposal(action_type="promote"):
    """构造 mock EvolutionProposal."""
    proposal = MagicMock()
    proposal.action_type = action_type
    proposal.trigger_reason = "test_trigger"
    proposal.target_module = "v9_baseline"
    proposal.rollback_plan = "rollback_to_baseline"
    return proposal


def _make_guard_decision():
    """构造 mock GuardDecision."""
    decision = MagicMock()
    decision.to_dict.return_value = {"approved": True, "level": "L2"}
    return decision


def _write_shadow_returns(tmp_path, returns, dates=None):
    """写入 daily_returns.jsonl 到临时目录."""
    shadow_dir = tmp_path / "reports" / "shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    path = shadow_dir / "daily_returns.jsonl"
    lines = []
    for i, ret in enumerate(returns):
        d = dates[i] if dates else f"2026-01-{i + 1:02d}"
        lines.append(json.dumps({"date": d, "daily_return": ret}))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ============================================================
# _get_shadow_adapter 懒加载
# ============================================================

class TestGetShadowAdapter:
    """_get_shadow_adapter() 懒加载."""

    @pytest.mark.unit
    def test_explicit_adapter_returned(self):
        adapter = MagicMock()
        orch = _make_orchestrator(shadow_adapter=adapter)
        assert orch._get_shadow_adapter() is adapter

    @pytest.mark.unit
    def test_lazy_load_returns_none_on_import_error(self):
        orch = _make_orchestrator(shadow_adapter=None)
        with patch("utils.alpha.shadow_account_adapter.ShadowAccountAdapter", side_effect=ImportError("no module")):
            result = orch._get_shadow_adapter()
        assert result is None

    @pytest.mark.unit
    def test_lazy_load_caches_instance(self):
        orch = _make_orchestrator(shadow_adapter=None)
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        with patch("utils.alpha.shadow_account_adapter.ShadowAccountAdapter", mock_cls):
            first = orch._get_shadow_adapter()
            second = orch._get_shadow_adapter()
        assert first is mock_instance
        assert second is mock_instance
        assert mock_cls.call_count == 1


# ============================================================
# _load_shadow_daily_returns
# ============================================================

class TestLoadShadowDailyReturns:
    """_load_shadow_daily_returns() 文件读取."""

    @pytest.mark.unit
    def test_file_not_exist_returns_empty(self, tmp_path):
        orch = _make_orchestrator()
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", tmp_path / "nonexistent.jsonl"):
            returns, dates = orch._load_shadow_daily_returns()
        assert returns == []
        assert dates == []

    @pytest.mark.unit
    def test_valid_data_loaded(self, tmp_path):
        path = _write_shadow_returns(tmp_path, [0.01, -0.005, 0.008])
        orch = _make_orchestrator()
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            returns, dates = orch._load_shadow_daily_returns()
        assert returns == [0.01, -0.005, 0.008]
        assert len(dates) == 3

    @pytest.mark.unit
    def test_empty_file_returns_empty(self, tmp_path):
        shadow_dir = tmp_path / "reports" / "shadow"
        shadow_dir.mkdir(parents=True)
        path = shadow_dir / "daily_returns.jsonl"
        path.write_text("", encoding="utf-8")
        orch = _make_orchestrator()
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            returns, dates = orch._load_shadow_daily_returns()
        assert returns == []
        assert dates == []

    @pytest.mark.unit
    def test_invalid_json_line_skipped(self, tmp_path):
        shadow_dir = tmp_path / "reports" / "shadow"
        shadow_dir.mkdir(parents=True)
        path = shadow_dir / "daily_returns.jsonl"
        path.write_text(
            json.dumps({"date": "2026-01-01", "daily_return": 0.01}) + "\n" +
            "not json\n" +
            json.dumps({"date": "2026-01-02", "daily_return": -0.005}),
            encoding="utf-8",
        )
        orch = _make_orchestrator()
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            returns, dates = orch._load_shadow_daily_returns()
        assert len(returns) == 2
        assert returns == [0.01, -0.005]


# ============================================================
# _route_l2 DSR promote/rollback/fail-fast/降级
# ============================================================

class TestRouteL2ShadowIntegration:
    """_route_l2() 直接调用 ShadowAccountAdapter."""

    @pytest.mark.unit
    def test_adapter_unavailable_degraded_assume_pass(self):
        """adapter=None → 降级假设通过."""
        orch = _make_orchestrator(shadow_adapter=None)
        # 强制 _get_shadow_adapter 返回 None
        orch._shadow_adapter = None
        with patch.object(orch, "_get_shadow_adapter", return_value=None):
            result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_DEGRADED
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True
        assert "降级" in result.reason

    @pytest.mark.unit
    def test_insufficient_samples_degraded(self, tmp_path):
        """样本不足 → 降级假设通过."""
        adapter = _make_mock_adapter(dsr=0.9)
        orch = _make_orchestrator(shadow_adapter=adapter)
        path = _write_shadow_returns(tmp_path, [0.01, 0.02])  # 仅2条
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_DEGRADED
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True
        # adapter.run_shadow 不应被调用 (样本不足提前返回)
        adapter.run_shadow.assert_not_called()

    @pytest.mark.unit
    def test_dsr_above_threshold_promote(self, tmp_path):
        """DSR >= 阈值 → promote (SUCCESS)."""
        adapter = _make_mock_adapter(dsr=0.8)
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30  # 30条足够
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_SUCCESS
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.executed is True
        assert "promote" in result.reason
        adapter.run_shadow.assert_called_once()
        adapter.get_metrics.assert_called_once()

    @pytest.mark.unit
    def test_dsr_below_threshold_rollback(self, tmp_path):
        """DSR < 阈值 → rollback (ROLLED_BACK)."""
        adapter = _make_mock_adapter(dsr=0.3)
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_ROLLED_BACK
        assert result.status == CYCLE_STATUS_ROLLED_BACK
        assert result.executed is False
        assert "rollback" in result.reason

    @pytest.mark.unit
    def test_fail_fast_triggered_rollback(self, tmp_path):
        """run_shadow fail-fast → rollback."""
        adapter = _make_mock_adapter(
            dsr=0.9, run_success=False, fail_fast=True, fail_fast_reason="max_drawdown_exceeded"
        )
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_ROLLED_BACK
        assert result.status == CYCLE_STATUS_ROLLED_BACK
        assert result.executed is False
        assert "fail-fast" in result.reason

    @pytest.mark.unit
    def test_adapter_exception_degraded(self, tmp_path):
        """adapter.run_shadow 抛异常 → 降级假设通过."""
        adapter = MagicMock()
        adapter.run_shadow.side_effect = RuntimeError("adapter crashed")
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_DEGRADED
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True

    @pytest.mark.unit
    def test_get_metrics_exception_degraded(self, tmp_path):
        """adapter.get_metrics 狂异常 → 降级假设通过."""
        adapter = MagicMock()
        run_result = MagicMock()
        run_result.success = True
        run_result.fail_fast_triggered = False
        run_result.fail_fast_reason = None
        adapter.run_shadow.return_value = run_result
        adapter.get_metrics.side_effect = RuntimeError("metrics crashed")
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                result = orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        from utils.evolution.orchestrator import CYCLE_STATUS_DEGRADED
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True

    @pytest.mark.unit
    def test_l2_promote_count_incremented(self, tmp_path):
        """promote 时 l2_promote_count +1."""
        adapter = _make_mock_adapter(dsr=0.8)
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        assert orch._loop_health["l2_promote_count"] == 1
        assert orch._loop_health["l2_rollback_count"] == 0

    @pytest.mark.unit
    def test_l2_rollback_count_incremented(self, tmp_path):
        """rollback 时 l2_rollback_count +1."""
        adapter = _make_mock_adapter(dsr=0.3)
        orch = _make_orchestrator(shadow_adapter=adapter, l2_dsr_threshold=0.5)
        returns = [0.01] * 30
        path = _write_shadow_returns(tmp_path, returns)
        with patch("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path):
            with patch("utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20):
                orch._route_l2(_make_proposal(), _make_guard_decision(), "2026-01-01T00:00:00")
        assert orch._loop_health["l2_rollback_count"] == 1
        assert orch._loop_health["l2_promote_count"] == 0


# ============================================================
# get_loop_health_metrics
# ============================================================

class TestLoopHealthMetrics:
    """get_loop_health_metrics() 指标快照."""

    @pytest.mark.unit
    def test_initial_state(self):
        orch = _make_orchestrator()
        metrics = orch.get_loop_health_metrics()
        assert metrics["total_cycles"] == 0
        assert metrics["evolution_trigger_rate"] == 0.0
        assert metrics["l2_promote_rate"] == 0.0
        assert metrics["avg_latency_ms"] == 0.0
        assert metrics["avg_weight_adjustment_magnitude"] == 0.0

    @pytest.mark.unit
    def test_after_promote_cycle(self):
        """模拟一次 promote 循环后指标正确."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_SUCCESS, CycleResult

        result = CycleResult(
            status=CYCLE_STATUS_SUCCESS,
            level="L2",
            action="promote",
            executed=True,
            weight_adjustments={"600519": 1.2, "000858": 0.8},
        )
        orch._update_loop_health(result, time.perf_counter())
        metrics = orch.get_loop_health_metrics()
        assert metrics["total_cycles"] == 1
        assert metrics["evolution_trigger_rate"] == 1.0
        assert metrics["l2_promote_count"] == 1
        assert metrics["l2_promote_rate"] == 1.0
        # avg |1.2-1.0| + |0.8-1.0| / 2 = (0.2 + 0.2) / 2 = 0.2
        assert metrics["avg_weight_adjustment_magnitude"] == 0.2

    @pytest.mark.unit
    def test_after_rollback_cycle(self):
        """模拟一次 rollback 循环后指标正确."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_ROLLED_BACK, CycleResult

        result = CycleResult(
            status=CYCLE_STATUS_ROLLED_BACK,
            level="L2",
            action="rollback",
            executed=False,
        )
        orch._update_loop_health(result, time.perf_counter())
        metrics = orch.get_loop_health_metrics()
        assert metrics["total_cycles"] == 1
        assert metrics["evolution_trigger_rate"] == 1.0
        assert metrics["l2_rollback_count"] == 1
        assert metrics["l2_promote_rate"] == 0.0

    @pytest.mark.unit
    def test_after_no_action_cycle(self):
        """no_action 循环不计入 evolution_trigger_count."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_NO_ACTION, CycleResult

        result = CycleResult(status=CYCLE_STATUS_NO_ACTION, level="L2")
        orch._update_loop_health(result, time.perf_counter())
        metrics = orch.get_loop_health_metrics()
        assert metrics["total_cycles"] == 1
        assert metrics["evolution_trigger_rate"] == 0.0

    @pytest.mark.unit
    def test_l1_count_tracked(self):
        """L1 层级计数."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_SUCCESS, CycleResult

        result = CycleResult(status=CYCLE_STATUS_SUCCESS, level="L1", action="fix")
        orch._update_loop_health(result, time.perf_counter())
        metrics = orch.get_loop_health_metrics()
        assert metrics["l1_count"] == 1

    @pytest.mark.unit
    def test_l3_count_tracked(self):
        """L3 层级计数."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_NO_ACTION, CycleResult

        result = CycleResult(status=CYCLE_STATUS_NO_ACTION, level="L3", action="pending")
        orch._update_loop_health(result, time.perf_counter())
        metrics = orch.get_loop_health_metrics()
        assert metrics["l3_pending_count"] == 1

    @pytest.mark.unit
    def test_latency_tracked(self):
        """延迟计时正确."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_SUCCESS, CycleResult

        t_start = time.perf_counter()
        time.sleep(0.01)  # 10ms
        result = CycleResult(status=CYCLE_STATUS_SUCCESS, level="L2")
        orch._update_loop_health(result, t_start)
        metrics = orch.get_loop_health_metrics()
        assert metrics["last_latency_ms"] > 5.0  # 至少 5ms
        assert metrics["avg_latency_ms"] > 5.0

    @pytest.mark.unit
    def test_multiple_cycles_trigger_rate(self):
        """多循环后 trigger_rate 正确."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import (
            CYCLE_STATUS_NO_ACTION,
            CYCLE_STATUS_ROLLED_BACK,
            CYCLE_STATUS_SUCCESS,
            CycleResult,
        )

        # 3次 promote + 1次 rollback + 1次 no_action = 5 total, 4 triggered
        for _ in range(3):
            orch._update_loop_health(
                CycleResult(status=CYCLE_STATUS_SUCCESS, level="L2"), time.perf_counter()
            )
        orch._update_loop_health(
            CycleResult(status=CYCLE_STATUS_ROLLED_BACK, level="L2"), time.perf_counter()
        )
        orch._update_loop_health(
            CycleResult(status=CYCLE_STATUS_NO_ACTION, level="L2"), time.perf_counter()
        )
        metrics = orch.get_loop_health_metrics()
        assert metrics["total_cycles"] == 5
        assert metrics["evolution_trigger_count"] == 4
        assert metrics["evolution_trigger_rate"] == 0.8
        assert metrics["l2_promote_count"] == 3
        assert metrics["l2_rollback_count"] == 1
        assert metrics["l2_promote_rate"] == 0.75  # 3 / (3+1)

    @pytest.mark.unit
    def test_weight_adjustment_magnitude_accumulated(self):
        """多次循环的权重调整幅度累积."""
        orch = _make_orchestrator()
        from utils.evolution.orchestrator import CYCLE_STATUS_SUCCESS, CycleResult

        # 第一次: avg|0.2, 0.0| = 0.1
        orch._update_loop_health(
            CycleResult(
                status=CYCLE_STATUS_SUCCESS,
                level="L2",
                weight_adjustments={"A": 1.2, "B": 1.0},
            ),
            time.perf_counter(),
        )
        # 第二次: avg|0.1, 0.3| = 0.2
        orch._update_loop_health(
            CycleResult(
                status=CYCLE_STATUS_SUCCESS,
                level="L2",
                weight_adjustments={"A": 1.1, "B": 0.7},
            ),
            time.perf_counter(),
        )
        metrics = orch.get_loop_health_metrics()
        assert metrics["avg_weight_adjustment_magnitude"] == 0.15  # (0.1 + 0.2) / 2


# ============================================================
# CYCLE_STATUS_ROLLED_BACK 常量
# ============================================================

class TestRolledBackConstant:
    """CYCLE_STATUS_ROLLED_BACK 常量存在."""

    @pytest.mark.unit
    def test_constant_value(self):
        from utils.evolution.orchestrator import CYCLE_STATUS_ROLLED_BACK
        assert CYCLE_STATUS_ROLLED_BACK == "rolled_back"

    @pytest.mark.unit
    def test_constant_in_to_dict(self):
        """rolled_back 状态能正确序列化."""
        from utils.evolution.orchestrator import CYCLE_STATUS_ROLLED_BACK, CycleResult

        result = CycleResult(
            status=CYCLE_STATUS_ROLLED_BACK,
            level="L2",
            reason="test rollback",
            executed=False,
        )
        d = result.to_dict()
        assert d["status"] == "rolled_back"
        assert d["executed"] is False
