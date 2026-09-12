"""U5 GAP-2 E2E: PipelineOrchestrator 完整周期端到端测试.

测试目标 (对齐 docs/GAP-2_E2E测试方案.md §三):
    场景 1: 默认配置完整周期 (data_cleaning + risk_monitor 执行, 其余跳过)
    场景 2: 数据清洗失败降级 (stage=DATA_CLEANING, success=False)
    场景 3: 执行模块禁用跳过 (execution_enabled=False)
    场景 4: 风控模块禁用跳过 (risk_monitor_enabled=False)
    场景 5: Alpha 阶段异常捕获 (stage=FAILED, error_count 递增)
    场景 6: 状态机快照持久化 (to_dict() 包含所有字段)

签名核对 (2026-08-05):
    - PipelineOrchestrator.__init__(config: Optional[PipelineConfig] = None)
    - run_full_cycle(mode="auto", market_data=None, symbols=None, current_positions=None) -> PipelineResult
    - PipelineResult: stage(PipelineStage) / success / error / metrics / reports
    - PipelineStage: IDLE / DATA_CLEANING / ALPHA_GENERATION / BACKTEST_GATE / EXECUTION / RISK_MONITOR / COMPLETED /
    FAILED
    - orchestrator._alpha 是 AlphaPipeline 实例 (可 monkeypatch)
    - orchestrator._status 是 PipelineStatus 实例, get_status() 返回 to_dict()
"""

from __future__ import annotations

import logging

import pytest

from utils.datetime_utils import now_bj
from utils.pipeline.orchestrator import PipelineOrchestrator
from utils.pipeline.types import PipelineConfig, PipelineResult, PipelineStage

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]


# ============================================================
# 场景 1: 默认配置完整周期 ✅
# ============================================================


class TestFullPipelineDefaultCycle:
    """默认配置完整周期 E2E."""

    def test_full_cycle_default_config(self, pipeline_config_overrides):
        """默认配置完整周期: data_cleaning + risk_monitor 执行, 其余跳过, success=True.

        验收标准:
            - result.success == True
            - result.stage == PipelineStage.COMPLETED
            - 无异常抛出
            - duration_ms >= 0
        """
        orchestrator = PipelineOrchestrator(config=pipeline_config_overrides)
        result = orchestrator.run_full_cycle(mode="dry_run")

        # 核心断言: 状态机正确迁移到 COMPLETED
        assert result.success is True, f"流水线应成功完成, error={result.error}"
        assert (
            result.stage == PipelineStage.COMPLETED
        ), f"stage 应为 COMPLETED, 实际 {result.stage}"
        # 时间断言
        assert result.duration_ms >= 0
        assert result.started_at is not None
        assert result.completed_at is not None
        # metrics 结构完整性
        assert "data_cleaning" in result.metrics
        assert "alpha" in result.metrics
        assert "execution" in result.metrics

    def test_full_cycle_run_count_increments(self, pipeline_config_overrides):
        """连续两次运行: run_count 递增."""
        orchestrator = PipelineOrchestrator(config=pipeline_config_overrides)
        orchestrator.run_full_cycle(mode="dry_run")
        orchestrator.run_full_cycle(mode="dry_run")

        status = orchestrator.get_status()
        assert status["run_count"] == 2


# ============================================================
# 场景 2: 数据清洗失败降级 ⚠️
# ============================================================


class TestDataCleaningFailure:
    """数据清洗失败时的降级行为."""

    def test_data_cleaning_failure_returns_failed_stage(
        self, pipeline_config_overrides, monkeypatch
    ):
        """数据清洗失败: stage=DATA_CLEANING, success=False, 不抛异常.

        通过 monkeypatch _data_cleaning.run 返回失败结果模拟数据清洗失败.
        """
        orchestrator = PipelineOrchestrator(config=pipeline_config_overrides)

        # Mock 数据清洗返回失败
        def fake_run(market_data, symbols):
            failed_result = PipelineResult(
                stage=PipelineStage.DATA_CLEANING,
                success=False,
                started_at=now_bj(),
                error="empty market data (mocked)",
            )
            return [], failed_result

        monkeypatch.setattr(orchestrator._data_cleaning, "run", fake_run)

        result = orchestrator.run_full_cycle(mode="auto", market_data={}, symbols=[])

        # 失败断言
        assert result.success is False
        assert result.stage == PipelineStage.DATA_CLEANING
        # 不应抛异常, 应优雅降级


# ============================================================
# 场景 3: 执行模块禁用跳过 ⏭
# ============================================================


class TestExecutionDisabled:
    """execution_enabled=False 时跳过阶段 4."""

    def test_execution_disabled_completes_successfully(self, pipeline_config_overrides):
        """execution_enabled=False: 跳过执行阶段, 仍到达 COMPLETED."""
        # 显式禁用执行
        config = pipeline_config_overrides
        config.execution_enabled = False
        orchestrator = PipelineOrchestrator(config=config)

        result = orchestrator.run_full_cycle(mode="dry_run")

        assert result.success is True
        assert result.stage == PipelineStage.COMPLETED
        # execution 阶段未执行 (metrics 中 fill_rate=0)
        assert result.metrics["execution"]["fill_rate"] == 0


# ============================================================
# 场景 4: 风控模块禁用跳过 ⏭
# ============================================================


class TestRiskMonitorDisabled:
    """risk_monitor_enabled=False 时跳过阶段 5."""

    def test_risk_monitor_disabled_completes_successfully(
        self, pipeline_config_overrides
    ):
        """risk_monitor_enabled=False: 跳过风控阶段, 仍到达 COMPLETED."""
        config = pipeline_config_overrides
        config.risk_monitor_enabled = False
        orchestrator = PipelineOrchestrator(config=config)

        result = orchestrator.run_full_cycle(mode="dry_run")

        assert result.success is True
        assert result.stage == PipelineStage.COMPLETED


# ============================================================
# 场景 5: Alpha 阶段异常捕获与状态机回退 🛡️
# ============================================================


class TestExceptionRecovery:
    """Alpha 阶段抛异常时的状态机回退."""

    def test_alpha_exception_transitions_to_failed(self, monkeypatch):
        """Alpha 阶段抛 RuntimeError: stage=FAILED, error_count 递增.

        需要 alpha_enabled=True 才能进入 Alpha 阶段 (默认 False 会跳过).
        """
        # 启用 alpha 以进入该阶段
        config = PipelineConfig(
            mode="dry_run",
            data_cleaning_enabled=False,  # 跳过数据清洗, 直接到 alpha
            alpha_enabled=True,
            execution_enabled=False,
            risk_monitor_enabled=False,
        )
        orchestrator = PipelineOrchestrator(config=config)

        # Mock Alpha 抛异常
        def _raise(*args, **kwargs):
            raise RuntimeError("Alpha pipeline crashed (mocked)")

        monkeypatch.setattr(orchestrator._alpha, "run", _raise)

        result = orchestrator.run_full_cycle(mode="dry_run")

        # 异常捕获断言
        assert result.success is False
        assert result.stage == PipelineStage.FAILED
        assert "Alpha pipeline crashed" in (result.error or "")
        # error_count 递增
        assert orchestrator._status.error_count == 1


# ============================================================
# 场景 6: 状态机快照持久化 📸
# ============================================================


class TestStatusSnapshot:
    """状态机快照 to_dict() 完整性."""

    def test_status_snapshot_contains_all_fields(self, pipeline_config_overrides):
        """状态机快照: to_dict() 包含 current_stage / last_run_at / run_count / error_count."""
        orchestrator = PipelineOrchestrator(config=pipeline_config_overrides)
        orchestrator.run_full_cycle(mode="dry_run")

        snapshot = orchestrator.get_status()

        # 字段完整性
        assert "current_stage" in snapshot
        assert "last_run_at" in snapshot
        assert "run_count" in snapshot
        assert "error_count" in snapshot
        # 值正确性
        assert snapshot["run_count"] == 1
        assert snapshot["error_count"] == 0
        assert snapshot["current_stage"] == PipelineStage.COMPLETED.value
        assert snapshot["last_run_at"] is not None

    def test_status_snapshot_error_count_after_failure(self, monkeypatch):
        """失败后 error_count 递增."""
        config = PipelineConfig(
            mode="dry_run",
            data_cleaning_enabled=False,
            alpha_enabled=True,
            execution_enabled=False,
            risk_monitor_enabled=False,
        )
        orchestrator = PipelineOrchestrator(config=config)

        def _raise(*args, **kwargs):
            raise RuntimeError("crash for error_count test")

        monkeypatch.setattr(orchestrator._alpha, "run", _raise)

        orchestrator.run_full_cycle(mode="dry_run")
        snapshot = orchestrator.get_status()
        assert snapshot["error_count"] == 1
        assert snapshot["current_stage"] == PipelineStage.FAILED.value
