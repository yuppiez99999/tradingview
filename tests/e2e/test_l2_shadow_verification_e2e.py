"""test_l2_shadow_verification_e2e.py — L2 影子验证 ShadowAccountAdapter 端到端测试

在真实 ShadowAccountAdapter 环境中验证 L2 路由的完整闭环:
    场景1: 足够样本 + 高 DSR → promote (SUCCESS)
    场景2: 足够样本 + 低 DSR → rollback (ROLLED_BACK)
    场景3: 样本不足 → 降级假设通过
    场景4: adapter 不可用 → 降级假设通过
    场景5: 多次 L2 循环 → 闭环健康度指标正确
    场景6: 再平衡反馈链 → daily_returns.jsonl → L2 读取验证
    场景7: DSR 恰好等于阈值 → promote
    场景8: fail-fast 触发 → rollback
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.shadow_account_adapter import ShadowAccountAdapter
from utils.evolution.orchestrator import (
    CYCLE_STATUS_DEGRADED,
    CYCLE_STATUS_ROLLED_BACK,
    CYCLE_STATUS_SUCCESS,
    EvolutionOrchestratorV2,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]


# ============================================================
# Helper: mock compute_dsr (与 test_shadow_account_lifecycle_e2e 同模式)
# ============================================================

from dataclasses import dataclass


@dataclass
class _MockDsrResult:
    deflated_sharpe_ratio: float = 0.85


def _make_mock_dsr_func(dsr_value: float):
    def _mock_compute_dsr(self):
        return _MockDsrResult(deflated_sharpe_ratio=dsr_value)

    return _mock_compute_dsr


@pytest.fixture(autouse=True)
def mock_dsr_if_missing(monkeypatch):
    try:
        import deflated_sharpe  # noqa: F401
    except ImportError:
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )


# ============================================================
# 共享 fixture
# ============================================================


@pytest.fixture
def shadow_adapter():
    return ShadowAccountAdapter(
        account_id="shadow_l2_e2e",
        strategy_id="l2_shadow_verification",
        initial_capital=500_000,
    )


@pytest.fixture
def good_daily_returns():
    """30天正收益序列 (DSR 高)."""
    return [0.005, -0.003, 0.008, -0.002, 0.004] * 6


@pytest.fixture
def poor_daily_returns():
    """30天负收益序列 (DSR 低)."""
    return [-0.008, 0.002, -0.005, 0.001, -0.006] * 6


@pytest.fixture
def orchestrator(shadow_adapter):
    """构造 orchestrator with real shadow_adapter + mock memory."""
    mock_memory = MagicMock()
    mock_memory.record.return_value = "e2e-pid-001"
    return EvolutionOrchestratorV2(
        memory=mock_memory,
        guard=MagicMock(),
        kill_switch=None,
        v1_orchestrator=None,
        shadow_adapter=shadow_adapter,
        l2_dsr_threshold=0.5,
    )


def _write_shadow_returns(tmp_path, returns, start_date="2026-01-01"):
    """写入 daily_returns.jsonl 并 patch 路径."""
    shadow_dir = tmp_path / "reports" / "shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    path = shadow_dir / "daily_returns.jsonl"
    from datetime import date, timedelta

    base = date.fromisoformat(start_date)
    lines = []
    for i, ret in enumerate(returns):
        d = (base + timedelta(days=i)).isoformat()
        lines.append(json.dumps({"date": d, "daily_return": ret}))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _make_proposal(action_type="promote"):
    proposal = MagicMock()
    proposal.action_type = action_type
    proposal.trigger_reason = "e2e_test_trigger"
    proposal.target_module = "v9_baseline"
    proposal.rollback_plan = "rollback_to_baseline"
    return proposal


def _make_guard_decision():
    decision = MagicMock()
    decision.to_dict.return_value = {"approved": True, "level": "L2"}
    return decision


# ============================================================
# 场景1: 足够样本 + 高 DSR → promote
# ============================================================


class TestL2Promote:
    """L2 影子验证通过 → promote."""

    def test_high_dsr_promote(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )

        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.executed is True
        assert result.level == "L2"
        assert "promote" in result.reason

    def test_memory_status_executed(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )

        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        # memory.update_status 应以 "executed" 调用
        calls = orchestrator._get_memory().update_status.call_args_list
        executed_calls = [c for c in calls if c.args and c.args[1] == "executed"]
        assert len(executed_calls) >= 1

    def test_loop_health_promote_count(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )

        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert orchestrator._loop_health["l2_promote_count"] == 1


# ============================================================
# 场景2: 足够样本 + 低 DSR → rollback
# ============================================================


class TestL2Rollback:
    """L2 影子验证未通过 → rollback."""

    def test_low_dsr_rollback(
        self, orchestrator, poor_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, poor_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.2)
        )

        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert result.status == CYCLE_STATUS_ROLLED_BACK
        assert result.executed is False
        assert "rollback" in result.reason

    def test_memory_status_rolled_back(
        self, orchestrator, poor_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, poor_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.2)
        )

        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        calls = orchestrator._get_memory().update_status.call_args_list
        rolled_back_calls = [c for c in calls if c.args and c.args[1] == "rolled_back"]
        assert len(rolled_back_calls) >= 1

    def test_loop_health_rollback_count(
        self, orchestrator, poor_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, poor_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.2)
        )

        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert orchestrator._loop_health["l2_rollback_count"] == 1


# ============================================================
# 场景3: 样本不足 → 降级
# ============================================================


class TestL2InsufficientSamples:
    """样本不足 → 降级假设通过."""

    def test_few_returns_degraded(self, orchestrator, tmp_path, monkeypatch):
        path = _write_shadow_returns(tmp_path, [0.01, 0.02, 0.005])
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )

        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True
        assert "样本不足" in result.reason or "降级" in result.reason


# ============================================================
# 场景4: adapter 不可用 → 降级
# ============================================================


class TestL2AdapterUnavailable:
    """adapter 不可用 → 降级假设通过."""

    def test_no_adapter_degraded(self, tmp_path, monkeypatch):
        mock_memory = MagicMock()
        mock_memory.record.return_value = "e2e-pid-no-adapter"
        orch = EvolutionOrchestratorV2(
            memory=mock_memory,
            guard=MagicMock(),
            kill_switch=None,
            v1_orchestrator=None,
            shadow_adapter=None,
            l2_dsr_threshold=0.5,
        )
        # 强制 _get_shadow_adapter 返回 None
        with patch.object(orch, "_get_shadow_adapter", return_value=None):
            result = orch._route_l2(
                _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
            )
        assert result.status == CYCLE_STATUS_DEGRADED
        assert result.executed is True


# ============================================================
# 场景5: 多次 L2 循环 → 闭环健康度指标
# ============================================================


class TestLoopHealthMultipleCycles:
    """多次 L2 循环后健康度指标正确."""

    def test_mixed_promote_rollback(
        self,
        orchestrator,
        good_daily_returns,
        poor_daily_returns,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )

        # 第1次: promote (DSR=0.85)
        path1 = _write_shadow_returns(
            tmp_path, good_daily_returns, start_date="2026-01-01"
        )
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path1)
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )
        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )

        # 第2次: rollback (DSR=0.2) — 需要新的 adapter (因为 run_shadow 会累积)
        new_adapter = ShadowAccountAdapter(
            account_id="shadow_l2_e2e_2", strategy_id="l2_2"
        )
        orchestrator._shadow_adapter = new_adapter
        path2 = _write_shadow_returns(
            tmp_path, poor_daily_returns, start_date="2026-02-01"
        )
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path2)
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.2)
        )
        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-02-01T00:00:00"
        )

        # 第3次: promote (DSR=0.9)
        new_adapter2 = ShadowAccountAdapter(
            account_id="shadow_l2_e2e_3", strategy_id="l2_3"
        )
        orchestrator._shadow_adapter = new_adapter2
        path3 = _write_shadow_returns(
            tmp_path, good_daily_returns, start_date="2026-03-01"
        )
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path3)
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.9)
        )
        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-03-01T00:00:00"
        )

        assert orchestrator._loop_health["l2_promote_count"] == 2
        assert orchestrator._loop_health["l2_rollback_count"] == 1


# ============================================================
# 场景6: 再平衡反馈链 → daily_returns.jsonl → L2 读取
# ============================================================


class TestRebalanceFeedbackToL2:
    """再平衡回写 daily_returns.jsonl → L2 读取验证."""

    def test_rebalance_written_returns_read_by_l2(
        self, orchestrator, tmp_path, monkeypatch
    ):
        """模拟再平衡回写 daily_returns.jsonl, 然后 L2 读取."""
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )

        # 模拟再平衡回写 (与 _write_rebalance_feedback_to_shadow 格式一致)
        shadow_dir = tmp_path / "reports" / "shadow"
        shadow_dir.mkdir(parents=True, exist_ok=True)
        path = shadow_dir / "daily_returns.jsonl"
        from datetime import date, timedelta

        base = date(2026, 1, 1)
        lines = []
        for i in range(30):
            d = (base + timedelta(days=i)).isoformat()
            ret = 0.005 if i % 2 == 0 else -0.002
            lines.append(
                json.dumps(
                    {
                        "date": d,
                        "daily_return": ret,
                        "source": "rebalance_feedback_v86",
                        "rebalance_executed": True,
                    }
                )
            )
        path.write_text("\n".join(lines), encoding="utf-8")

        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)

        # L2 读取并验证
        returns, dates = orchestrator._load_shadow_daily_returns()
        assert len(returns) == 30
        assert len(dates) == 30
        assert returns[0] == 0.005

        # 完整 L2 路由
        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-31T00:00:00"
        )
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.executed is True


# ============================================================
# 场景7: DSR 恰好等于阈值 → promote
# ============================================================


class TestL2DsrBoundary:
    """DSR 恰好等于阈值 → promote (>= 判断)."""

    def test_dsr_equals_threshold(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        # DSR 恰好等于阈值 0.5
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.5)
        )

        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert result.status == CYCLE_STATUS_SUCCESS
        assert result.executed is True

    def test_dsr_just_below_threshold(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        # DSR = 0.499 < 0.5
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.499)
        )

        result = orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        assert result.status == CYCLE_STATUS_ROLLED_BACK
        assert result.executed is False


# ============================================================
# 场景8: get_loop_health_metrics 端到端
# ============================================================


class TestLoopHealthE2E:
    """闭环健康度指标端到端验证."""

    def test_metrics_after_l2_promote(
        self, orchestrator, good_daily_returns, tmp_path, monkeypatch
    ):
        path = _write_shadow_returns(tmp_path, good_daily_returns)
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path)
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )

        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )
        metrics = orchestrator.get_loop_health_metrics()
        assert metrics["l2_promote_count"] == 1
        assert metrics["l2_rollback_count"] == 0
        assert metrics["l2_promote_rate"] == 1.0

    def test_metrics_after_mixed_cycles(
        self,
        orchestrator,
        good_daily_returns,
        poor_daily_returns,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "utils.alpha.shadow_account_adapter.MIN_SAMPLES_FOR_DSR", 20
        )

        # promote
        path1 = _write_shadow_returns(
            tmp_path, good_daily_returns, start_date="2026-01-01"
        )
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path1)
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.85)
        )
        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-01-01T00:00:00"
        )

        # rollback
        adapter2 = ShadowAccountAdapter(
            account_id="shadow_e2e_mix_2", strategy_id="mix_2"
        )
        orchestrator._shadow_adapter = adapter2
        path2 = _write_shadow_returns(
            tmp_path, poor_daily_returns, start_date="2026-02-01"
        )
        monkeypatch.setattr("utils.evolution.orchestrator._SHADOW_RETURNS_PATH", path2)
        monkeypatch.setattr(
            ShadowAccountAdapter, "compute_dsr", _make_mock_dsr_func(0.2)
        )
        orchestrator._route_l2(
            _make_proposal("promote"), _make_guard_decision(), "2026-02-01T00:00:00"
        )

        metrics = orchestrator.get_loop_health_metrics()
        assert metrics["l2_promote_count"] == 1
        assert metrics["l2_rollback_count"] == 1
        assert metrics["l2_promote_rate"] == 0.5
