#!/usr/bin/env python
"""
闭环流水线编排器 (PipelineOrchestrator)
=====================================

职责:
1. 管理流水线状态机 (IDLE → DATA_READY → ALPHA_READY → BACKTEST_PASSED → EXECUTING → MONITORING)
2. 协调五大模块按序执行
3. 失败时回退到安全状态
4. 提供 run_full_cycle / run_*_only 快捷方法
5. 记录执行报告和审计日志

安全设计:
- Feature Flag 默认关闭，需显式启用
- 每阶段独立 fail-closed
- 全部事件写入 EvolutionMemory
- 支持 dry_run / manual / auto 三种模式

作者: 终极量化交易系统 v8.4
日期: 2026-08-02
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from utils.datetime_utils import now_bj

from .alpha_pipeline import AlphaPipeline
from .backtest_gate import BacktestGate
from .config import get_pipeline_config
from .data_cleaning import DataCleaningPipeline
from .execution_pipeline import ExecutionPipeline
from .risk_monitor import RiskMonitor
from .types import (
    AlphaSignalResult,
    BacktestGateResult,
    DataQualityReport,
    ExecutionResult,
    PipelineConfig,
    PipelineResult,
    PipelineStage,
    RiskAlert,
)

logger = logging.getLogger("pipeline.orchestrator")


class PipelineStatus:
    """流水线状态快照"""

    def __init__(self):
        self.current_stage: PipelineStage = PipelineStage.IDLE
        self.last_result: PipelineResult | None = None
        self.last_run_at: str | None = None
        self.run_count: int = 0
        self.error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_stage": self.current_stage.value,
            "last_run_at": self.last_run_at,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_success": self.last_result.success if self.last_result else None,
        }


class PipelineOrchestrator:
    """
    闭环流水线编排器

    状态机: IDLE → DATA_READY → ALPHA_READY → BACKTEST_PASSED → EXECUTING → MONITORING
    失败回退到上一安全状态。

    使用示例:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_full_cycle(mode="dry_run")
        logger.info(result.to_dict())
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or get_pipeline_config()
        self._status = PipelineStatus()
        self._data_reports: list[DataQualityReport] = []
        self._alpha_result: AlphaSignalResult | None = None
        self._backtest_result: BacktestGateResult | None = None
        self._execution_result: ExecutionResult | None = None
        self._risk_alerts: list[RiskAlert] = []

        # 初始化各阶段流水线
        self._data_cleaning = DataCleaningPipeline(self.config)
        self._alpha = AlphaPipeline(self.config)
        self._backtest = BacktestGate(self.config)
        self._execution = ExecutionPipeline(self.config)
        self._risk = RiskMonitor(self.config)

        logger.info("PipelineOrchestrator 初始化完成")

    def run_full_cycle(
        self,
        mode: str = "auto",
        market_data: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        current_positions: dict[str, float] | None = None,
    ) -> PipelineResult:
        """
        执行完整闭环周期

        Args:
            mode: 运行模式 (auto / manual / dry_run)
            market_data: 市场数据 (可选)
            symbols: 标的列表 (可选)
            current_positions: 当前持仓 (可选)

        Returns:
            PipelineResult: 完整周期执行结果
        """
        started_at = now_bj()
        logger.info("=" * 70)
        logger.info(f"闭环流水线启动 | mode={mode}")
        logger.info("=" * 70)

        self._status.current_stage = PipelineStage.IDLE

        try:
            # 阶段 1: 数据清洗
            if not self._run_stage_data_cleaning(market_data, symbols):
                return self._build_result(
                    PipelineStage.DATA_CLEANING, started_at, success=False
                )

            # 阶段 2: Alpha 信号
            if not self._run_stage_alpha(symbols):
                return self._build_result(
                    PipelineStage.ALPHA_GENERATION, started_at, success=False
                )

            # 阶段 3: 回测验证
            if not self._run_stage_backtest():
                return self._build_result(
                    PipelineStage.BACKTEST_GATE, started_at, success=False
                )

            # 阶段 4: 执行
            if self.config.execution_enabled:
                if not self._run_stage_execution(current_positions):
                    return self._build_result(
                        PipelineStage.EXECUTION, started_at, success=False
                    )
            else:
                logger.info("执行模块未启用，跳过")

            # 阶段 5: 风控监控
            if self.config.risk_monitor_enabled:
                self._run_stage_risk()
            else:
                logger.info("风控模块未启用，跳过")

            # 完成
            self._status.current_stage = PipelineStage.COMPLETED
            self._status.last_run_at = now_bj().isoformat()
            self._status.run_count += 1

            result = self._build_result(
                PipelineStage.COMPLETED, started_at, success=True
            )
            logger.info("=" * 70)
            logger.info(f"闭环流水线完成 | 耗时 {result.duration_ms:.0f}ms")
            logger.info("=" * 70)

            return result

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"闭环流水线异常: {e}", exc_info=True)
            self._status.current_stage = PipelineStage.FAILED
            self._status.error_count += 1

            return self._build_result(
                PipelineStage.FAILED, started_at, success=False, error=str(e)
            )

    def _run_stage_data_cleaning(
        self,
        market_data: dict[str, Any] | None,
        symbols: list[str] | None,
    ) -> bool:
        """执行数据清洗阶段"""
        self._status.current_stage = PipelineStage.DATA_CLEANING
        logger.info("[阶段 1/5] 数据清洗...")

        if not self.config.data_cleaning_enabled:
            logger.info("数据清洗已禁用")
            return True

        reports, result = self._data_cleaning.run(market_data, symbols)
        self._data_reports = reports

        if not result.success:
            logger.error(f"数据清洗失败: {result.error}")
            return False

        # 检查质量评分
        failed = [r for r in reports if not r.passed]
        if failed:
            logger.warning(f"{len(failed)} 只标的未通过数据质量检查")
            # 记录但不阻断 (可根据策略调整)

        logger.info(
            f"数据清洗完成: {len(reports)} 只标的, "
            f"平均质量 {np_mean([r.quality_score for r in reports]):.1f}"
        )
        return True

    def _run_stage_alpha(self, symbols: list[str] | None) -> bool:
        """执行 Alpha 信号阶段"""
        self._status.current_stage = PipelineStage.ALPHA_GENERATION
        logger.info("[阶段 2/5] Alpha 信号生成...")

        if not self.config.alpha_enabled:
            logger.info("Alpha 信号已禁用")
            return True

        signal_result, result = self._alpha.run(force_retrain=False, symbols=symbols)

        if signal_result is None:
            logger.error(f"Alpha 信号生成失败: {result.error}")
            return False

        self._alpha_result = signal_result
        logger.info(
            f"Alpha 信号完成: {len(signal_result.signals)} 只标的, "
            f"模型 {signal_result.model_name}"
        )
        return True

    def _run_stage_backtest(self) -> bool:
        """执行回测验证阶段"""
        self._status.current_stage = PipelineStage.BACKTEST_GATE
        logger.info("[阶段 3/5] 回测验证...")

        if not self.config.backtest_gate_enabled:
            logger.info("回测验证已禁用")
            return True

        if self._alpha_result is None or not self._alpha_result.signals:
            logger.warning("无 Alpha 信号，跳过回测")
            return True

        gate_result, result = self._backtest.run(self._alpha_result)

        if not gate_result.passed:
            logger.error(f"回测验证未通过: {gate_result.rejection_reason}")
            return False

        self._backtest_result = gate_result
        logger.info(
            f"回测验证通过: IC={gate_result.ic:.3f}, DSR={gate_result.dsr:.2f}, "
            f"Sharpe={gate_result.sharpe:.2f}"
        )
        return True

    def _run_stage_execution(
        self,
        current_positions: dict[str, float] | None,
    ) -> bool:
        """执行交易阶段"""
        self._status.current_stage = PipelineStage.EXECUTION
        logger.info("[阶段 4/5] 执行交易...")

        if self._alpha_result is None or not self._alpha_result.signals:
            logger.warning("无 Alpha 信号，跳过执行")
            return True

        exec_result, result = self._execution.run(
            signal_result=self._alpha_result,
            current_positions=current_positions,
            dry_run=(self.config.mode in ["dry_run", "auto"]),
        )

        self._execution_result = exec_result
        self._status.last_result = result

        if not result.success:
            logger.error(f"执行失败: {result.error}")
            return False

        logger.info(
            f"执行完成: {exec_result.filled_orders}/{exec_result.total_orders} "
            f"({exec_result.fill_rate:.1%})"
        )
        return True

    def _run_stage_risk(self) -> bool:
        """执行风控监控阶段"""
        self._status.current_stage = PipelineStage.RISK_MONITOR
        logger.info("[阶段 5/5] 风控监控...")

        result = self._risk.run_check()
        self._status.last_result = result

        if not result.success:
            logger.warning(f"风控告警: {result.metrics.get('max_alert_level', 0)}")
            return True  # 风控不阻断流水线，只告警

        logger.info("风控检查通过")
        return True

    def _build_result(
        self,
        stage: PipelineStage,
        started_at: datetime,
        success: bool,
        error: str | None = None,
    ) -> PipelineResult:
        """构建 PipelineResult"""
        return PipelineResult(
            stage=stage,
            success=success,
            started_at=started_at,
            completed_at=now_bj(),
            duration_ms=(now_bj() - started_at).total_seconds() * 1000,
            error=error,
            metrics=self._collect_metrics(),
            reports=self._collect_reports(),
        )

    def _collect_metrics(self) -> dict[str, Any]:
        """收集各阶段指标"""
        metrics: dict[str, Any] = {
            "data_cleaning": {
                "reports_count": len(self._data_reports),
                "avg_quality": (
                    np_mean([r.quality_score for r in self._data_reports])
                    if self._data_reports
                    else 0
                ),
            },
            "alpha": {
                "signals_count": (
                    len(self._alpha_result.signals) if self._alpha_result else 0
                ),
                "model": self._alpha_result.model_name if self._alpha_result else "",
            },
            "backtest": {
                "passed": (
                    self._backtest_result.passed if self._backtest_result else False
                ),
                "ic": self._backtest_result.ic if self._backtest_result else 0,
                "dsr": self._backtest_result.dsr if self._backtest_result else 0,
            },
            "execution": {
                "fill_rate": (
                    self._execution_result.fill_rate if self._execution_result else 0
                ),
                "total_orders": (
                    self._execution_result.total_orders if self._execution_result else 0
                ),
            },
        }
        return metrics

    def _collect_reports(self) -> list[str]:
        """收集报告路径"""
        reports = []
        if self._alpha_result:
            reports.append(f"alpha_signals_{now_bj().strftime('%Y%m%d')}.json")
        if self._backtest_result:
            reports.append(f"backtest_gate_{now_bj().strftime('%Y%m%d')}.json")
        return reports

    def get_status(self) -> dict[str, Any]:
        """获取当前状态"""
        return self._status.to_dict()

    def run_data_cleaning_only(
        self,
        market_data: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
    ) -> tuple[list[DataQualityReport], PipelineResult]:
        """仅执行数据清洗"""
        return self._data_cleaning.run(market_data, symbols)

    def run_alpha_only(
        self,
        force_retrain: bool = False,
        symbols: list[str] | None = None,
    ) -> tuple[AlphaSignalResult | None, PipelineResult]:
        """仅执行 Alpha 信号生成"""
        return self._alpha.run(force_retrain=force_retrain, symbols=symbols)

    def run_execution_only(
        self,
        signal_result: AlphaSignalResult | None = None,
        current_positions: dict[str, float] | None = None,
        dry_run: bool = True,
    ) -> tuple[ExecutionResult, PipelineResult]:
        """仅执行交易"""
        if signal_result is None:
            signal_result = self._alpha_result

        return self._execution.run(
            signal_result=signal_result,
            current_positions=current_positions,
            dry_run=dry_run,
        )


def np_mean(values: list[float], default: float = 0.0) -> float:
    """安全的均值计算"""
    if not values:
        return default
    try:
        return sum(values) / len(values)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return default
