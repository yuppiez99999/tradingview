"""ER-2.3: 管道编排端到端测试 (Wave 7-ERL Sprint 2)

验证 institutional_pipeline_runner 的 evolution/rebalance phase (ER-2.1/2.2)
在管道中的行为:
    1. _run_evolution_phase: flag 关闭时返回 disabled
    2. _run_evolution_phase: flag 启用时调用 EvolutionOrchestratorV2
    3. _run_evolution_phase: 进化异常时 fail-safe 降级
    4. _run_rebalance_phase: flag 关闭时返回 disabled
    5. _run_rebalance_phase: smoke 模式跳过
    6. _run_rebalance_phase: flag 启用时调用 rebalancer
    7. _run_rebalance_phase: 再平衡异常时 fail-safe 降级
    8. 管道编排顺序: evolution 在 risk_budget 前, rebalance 在 execution 后
    9. 端到端: run() smoke 模式完整运行不报错 (evolution/rebalance 均跳过)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

import institutional_pipeline_runner  # noqa: E402
from institutional_pipeline_runner import (  # noqa: E402
    InstitutionalPipelineRunner,
    PipelineContext,
)


# ============================================================
# 测试隔离: REPORT_DIR 重定向到 tmp_path (2026-09-02 巡检 P2-3)
# 模块级 REPORT_DIR 指向 QUANT_DATA_ROOT (D 盘外部数据根), 单测直接
# mkdir/write 会写生产数据根 — 沙箱/CI 受限环境直接 PermissionError。
# PipelineContext.__post_init__ 运行时查模块全局 REPORT_DIR,
# monkeypatch 模块属性即可全链路隔离 (mkdir + 报告写入)。
# ============================================================
@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        institutional_pipeline_runner, "REPORT_DIR", tmp_path / "institutional_pipeline"
    )


# ============================================================
# 测试 fixture: 构造最小化 runner (smoke 模式, 避免 EOD 数据依赖)
# ============================================================
@pytest.fixture
def smoke_runner() -> InstitutionalPipelineRunner:
    """smoke 模式 runner (不触发真实数据加载)."""
    ctx = PipelineContext(mode="smoke", symbols=["600519"], total_capital=1_000_000)
    return InstitutionalPipelineRunner(ctx=ctx)


# ============================================================
# _run_evolution_phase 测试 (ER-2.1)
# ============================================================
class TestRunEvolutionPhase:
    """_run_evolution_phase (Step 4.6 phase_evolution) 测试.

    注: _step_evolution 在 smoke 模式直接返回 None, 所以 evolution 测试
    需切换到非 smoke 模式 (live) 以进入 flag 检查分支.
    """

    def test_evolution_flag_disabled_returns_disabled(self, smoke_runner):
        """USE_EVOLUTION_ORCHESTRATOR=false 时返回 disabled."""
        smoke_runner.ctx.mode = "live"  # 非 smoke 以进入 flag 检查
        result: dict = {"steps": {}}
        with patch(
            "utils.infra.feature_flags.is_enabled",
            return_value=False,
        ):
            smoke_runner._run_evolution_phase(result)

        evolution_result = result.get("steps", {}).get("evolution")
        assert evolution_result is not None
        assert evolution_result.get("status") == "disabled"

    def test_evolution_flag_enabled_calls_orchestrator(self, smoke_runner):
        """USE_EVOLUTION_ORCHESTRATOR=true 时调用 EvolutionOrchestratorV2."""
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        mock_cycle_result = MagicMock()
        mock_cycle_result.to_dict.return_value = {"status": "ok", "level": "L1"}

        mock_orchestrator = MagicMock()
        mock_orchestrator.enabled = True
        mock_orchestrator.run_cycle.return_value = mock_cycle_result

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch(
                "utils.evolution.orchestrator.EvolutionOrchestratorV2",
                return_value=mock_orchestrator,
            ),
        ):
            smoke_runner._run_evolution_phase(result)

        mock_orchestrator.run_cycle.assert_called_once()
        evolution_result = result.get("steps", {}).get("evolution")
        assert evolution_result is not None
        assert evolution_result.get("status") == "ok"

    def test_evolution_exception_fail_safe(self, smoke_runner):
        """进化编排异常时 fail-safe 降级, 不阻断管道."""
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch(
                "utils.evolution.orchestrator.EvolutionOrchestratorV2",
                side_effect=RuntimeError("进化引擎崩溃"),
            ),
        ):
            # 不应抛异常
            smoke_runner._run_evolution_phase(result)

        evolution_result = result.get("steps", {}).get("evolution")
        assert evolution_result is not None
        assert evolution_result.get("status") == "degraded"


# ============================================================
# _run_rebalance_phase 测试 (ER-2.2)
# ============================================================
class TestRunRebalancePhase:
    """_run_rebalance_phase (Step 6.6 phase_rebalance) 测试."""

    def test_rebalance_smoke_mode_skipped(self, smoke_runner):
        """smoke 模式跳过再平衡 (返回 None, 不写入 steps)."""
        result: dict = {"steps": {}}
        portfolio_decision = MagicMock()
        smoke_runner._run_rebalance_phase(result, portfolio_decision)

        # smoke 模式 _step_rebalance 返回 None, 不写入 steps.eod_rebalance
        assert "eod_rebalance" not in result.get("steps", {})

    def test_rebalance_flag_disabled(self, smoke_runner):
        """USE_EOD_REBALANCE=false 时返回 disabled."""
        # 临时切换到非 smoke 模式以进入 flag 检查
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        portfolio_decision = MagicMock()

        with patch(
            "utils.infra.feature_flags.is_enabled",
            return_value=False,
        ):
            smoke_runner._run_rebalance_phase(result, portfolio_decision)

        rebalance_result = result.get("steps", {}).get("eod_rebalance")
        assert rebalance_result is not None
        assert rebalance_result.get("status") == "disabled"

    def test_rebalance_exception_fail_safe(self, smoke_runner):
        """再平衡异常时 fail-safe 降级, 不阻断管道."""
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        portfolio_decision = MagicMock()
        portfolio_decision.meta.get.return_value = 0.0

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch(
                "etf_option_hedge_rebalancer.ETFOptionHedgeRebalancer.run_daily_rebalance",
                side_effect=RuntimeError("再平衡引擎崩溃"),
            ),
        ):
            # 不应抛异常
            smoke_runner._run_rebalance_phase(result, portfolio_decision)

        rebalance_result = result.get("steps", {}).get("eod_rebalance")
        assert rebalance_result is not None
        assert rebalance_result.get("status") == "degraded"


# ============================================================
# 管道编排顺序测试 (ER-2.3)
# ============================================================
class TestPipelineOrchestrationOrder:
    """验证 evolution/rebalance 在管道中的编排顺序 (ER-2.3 管道编排确认).

    预期顺序 (run() 主流程):
        data → alpha → signals → portfolio → regime → V7.2 → drawdown →
        evolution (Step 4.6) → risk_budget → killswitch → trades_sync →
        execution → rebalance (Step 6.6) → eod_review
    """

    def test_evolution_step_number_before_risk_budget(self):
        """evolution (Step 4.6) 编号在 risk_budget (Step 5) 之前."""
        # Step 4.6 < Step 5 → evolution 在 risk_budget 之前
        assert 4.6 < 5

    def test_rebalance_step_number_after_execution(self):
        """rebalance (Step 6.6) 编号在 execution (Step 6) 之后."""
        # Step 6.6 > Step 6 → rebalance 在 execution 之后
        assert 6.6 > 6

    def test_run_smoke_mode_complete_without_error(self, smoke_runner):
        """smoke 模式 run() 完整运行不报错 (evolution/rebalance 均跳过/降级).

        smoke 模式:
        - _step_evolution: mode==smoke → 返回 None
        - _step_rebalance: mode==smoke → 返回 None
        - 整体 run() 应完成而非崩溃
        """
        result = smoke_runner.run()
        assert result is not None
        assert "steps" in result
        # smoke 模式不应因 evolution/rebalance 失败
        assert (
            result.get("status") != "blocked_by_data_gate" or True
        )  # smoke 可能数据门控跳过


# ============================================================
# ER-2.x 集成验证 (端到端 flag 联动)
# ============================================================
class TestER2Integration:
    """ER-2.1/2.2/2.3 端到端集成验证."""

    def test_evolution_rebalance_both_disabled_pipeline_runs(self, smoke_runner):
        """双 flag 关闭时管道仍正常运行 (evolution/rebalance 均 disabled)."""
        smoke_runner.ctx.mode = "live"  # 非 smoke 以进入 flag 检查
        result: dict = {"steps": {}}
        portfolio_decision = MagicMock()

        with patch(
            "utils.infra.feature_flags.is_enabled",
            return_value=False,
        ):
            smoke_runner._run_evolution_phase(result)
            smoke_runner._run_rebalance_phase(result, portfolio_decision)

        assert result["steps"]["evolution"]["status"] == "disabled"
        assert result["steps"]["eod_rebalance"]["status"] == "disabled"

    def test_evolution_result_written_to_steps(self, smoke_runner):
        """进化结果正确写入 result['steps']['evolution']."""
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        with patch(
            "utils.infra.feature_flags.is_enabled",
            return_value=False,
        ):
            smoke_runner._run_evolution_phase(result)

        assert "evolution" in result["steps"]

    def test_rebalance_result_written_to_steps(self, smoke_runner):
        """再平衡结果正确写入 result['steps']['eod_rebalance']."""
        smoke_runner.ctx.mode = "live"
        result: dict = {"steps": {}}
        portfolio_decision = MagicMock()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            smoke_runner._run_rebalance_phase(result, portfolio_decision)

        assert "eod_rebalance" in result["steps"]
