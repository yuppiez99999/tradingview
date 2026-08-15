"""
金融工程闭环流水线

数据清洗 → Alpha信号生成(Qlib) → 回测验证 → 自动执行 → 风控监控

使用方式:
    from utils.pipeline import PipelineOrchestrator, PipelineStatus
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_full_cycle(mode="dry_run")
"""

from utils.pipeline.alpha_pipeline import AlphaPipeline, AlphaSignalResult
from utils.pipeline.backtest_gate import BacktestGate, BacktestGateResult
from utils.pipeline.data_cleaning import DataCleaningPipeline, DataQualityReport
from utils.pipeline.execution_pipeline import ExecutionPipeline, ExecutionResult
from utils.pipeline.orchestrator import PipelineOrchestrator, PipelineStatus
from utils.pipeline.risk_monitor import RiskAlert, RiskMonitor
from utils.pipeline.types import PipelineConfig, PipelineResult, PipelineStage

__all__ = [
    "PipelineOrchestrator",
    "PipelineStatus",
    "DataCleaningPipeline",
    "DataQualityReport",
    "AlphaPipeline",
    "AlphaSignalResult",
    "BacktestGate",
    "BacktestGateResult",
    "ExecutionPipeline",
    "ExecutionResult",
    "RiskMonitor",
    "RiskAlert",
    "PipelineResult",
    "PipelineStage",
    "PipelineConfig",
]
