"""
金融工程闭环流水线 — 共享数据结构

所有流水线模块共用此数据模型，确保各阶段输入/输出类型一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class PipelineStage(Enum):
    """流水线阶段"""
    IDLE = "idle"
    DATA_CLEANING = "data_cleaning"
    ALPHA_GENERATION = "alpha_generation"
    BACKTEST_GATE = "backtest_gate"
    EXECUTION = "execution"
    RISK_MONITOR = "risk_monitor"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PipelineConfig:
    """流水线配置（从 YAML 反序列化）"""
    mode: str = "auto"                     # auto / manual / dry_run
    interval_minutes: int = 15

    # 数据清洗
    data_cleaning_enabled: bool = True
    min_quality_score: float = 80.0
    multi_source_check: bool = True
    outlier_z_threshold: float = 3.0
    gap_fill_max_days: int = 3

    # Alpha 信号
    alpha_enabled: bool = False
    alpha_model: str = "auto"              # auto / lightgbm / transformer / lstm
    train_interval_days: int = 20
    retrain_on_drift: bool = True
    horizon: int = 5

    # 回测验证
    backtest_gate_enabled: bool = False
    min_ic: float = 0.03
    min_dsr: float = 1.0
    max_drawdown: float = 0.15
    walk_forward_windows: int = 6

    # 执行
    execution_enabled: bool = False
    execution_algo: str = "auto"
    max_slippage_bps: float = 10.0
    slice_minutes: int = 5
    execution_dry_run: bool = True
    execution_target_exposure: float = 0.95
    execution_max_order_value: float = 500_000.0
    execution_default_algo: str = "twap"

    # 风控
    risk_monitor_enabled: bool = True
    risk_check_interval_seconds: int = 30
    kill_switch_l1_margin: float = 0.50
    kill_switch_l2_margin: float = 0.65
    kill_switch_l3_margin: float = 0.75
    max_daily_drawdown: float = 0.05
    max_total_drawdown: float = 0.15

    # 日志
    log_level: str = "INFO"
    report_dir: str = "reports/pipeline"
    memory_write: bool = True


@dataclass
class PipelineResult:
    """单次流水线执行结果"""
    stage: PipelineStage
    success: bool
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: float = 0.0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    reports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "success": self.success,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "metrics": self.metrics,
            "reports": self.reports,
        }


@dataclass
class DataQualityReport:
    """数据清洗阶段输出"""
    symbol: str
    quality_score: float          # 0-100
    outlier_flags: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    gap_days: int = 0
    multi_source_deviation_pct: float = 0.0
    passed: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlphaSignalResult:
    """Alpha 信号生成阶段输出"""
    signals: dict[str, float] = field(default_factory=dict)      # {symbol: signal_strength}
    confidence: dict[str, float] = field(default_factory=dict)    # {symbol: confidence}
    model_name: str = ""
    model_metrics: dict[str, float] = field(default_factory=dict)
    training_date: str = ""
    n_stocks: int = 0


@dataclass
class BacktestGateResult:
    """回测验证阶段输出"""
    passed: bool = False
    ic: float = 0.0
    dsr: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    stress_test_passed: bool = False
    walk_forward_passed: bool = False
    rejection_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """执行阶段输出"""
    batch_id: str = ""
    total_orders: int = 0
    filled_orders: int = 0
    failed_orders: int = 0
    total_amount: float = 0.0
    filled_amount: float = 0.0
    avg_fill_price: float = 0.0
    fill_rate: float = 0.0
    dry_run: bool = True
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class RiskAlert:
    """风控告警"""
    level: int = 0                     # 0=OK / 1=警戒 / 2=熔断 / 3=互盲
    source: str = ""
    message: str = ""
    triggered_at: datetime = field(default_factory=datetime.now)
    metrics: dict[str, float] = field(default_factory=dict)
    actions_taken: list[str] = field(default_factory=list)