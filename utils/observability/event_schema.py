"""Phase B 可观测事件 Schema (pydantic v2).

约束关键交易链路事件字段, 为 structlog 结构化日志提供类型安全基础.
事件类型: Order / Risk / Execution / Pipeline.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class EventStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    PENDING = "pending"


class ObservabilityEvent(BaseModel):
    """可观测事件基类."""

    timestamp: datetime = Field(default_factory=datetime.now)
    severity: EventSeverity = EventSeverity.INFO
    status: EventStatus = EventStatus.SUCCESS
    event_type: str = Field(..., description="事件类型标识")
    message: str = Field(default="", description="人类可读消息")
    context: dict[str, Any] = Field(default_factory=dict, description="扩展上下文")
    trace_id: str | None = Field(
        default=None, description="追踪 ID (后续 OpenTelemetry 接入)"
    )


class OrderEvent(ObservabilityEvent):
    """订单事件."""

    event_type: str = "order"
    order_id: str = Field(..., description="订单 ID")
    symbol: str = Field(..., description="标的代码")
    side: str = Field(..., description="买卖方向")
    qty: float = Field(..., description="数量")
    price: float | None = Field(default=None, description="价格")
    filled_qty: float = Field(default=0.0, description="已成交数量")


class RiskEvent(ObservabilityEvent):
    """风控事件."""

    event_type: str = "risk"
    risk_level: str = Field(..., description="风控级别 (caution/reduction/liquidate)")
    trigger: str = Field(..., description="触发原因")
    action: str = Field(
        ..., description="风控动作 (pass/reduce/disable/liquidate/kill)"
    )


class ExecutionEvent(ObservabilityEvent):
    """执行事件."""

    event_type: str = "execution"
    phase: str = Field(..., description="执行阶段")
    duration_ms: float | None = Field(default=None, description="耗时 (毫秒)")


class PipelineEvent(ObservabilityEvent):
    """管线事件."""

    event_type: str = "pipeline"
    pipeline_name: str = Field(..., description="管线名称")
    step: str = Field(..., description="管线步骤")
    step_index: int | None = Field(default=None, description="步骤序号")
