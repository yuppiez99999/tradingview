"""Phase B 可观测性: 结构化事件 Schema + 日志配置.

提供 pydantic 事件模型约束可观测事件字段,
为后续 structlog 接入提供类型安全基础.
"""

from utils.observability.event_schema import (
    ExecutionEvent,
    ObservabilityEvent,
    OrderEvent,
    PipelineEvent,
    RiskEvent,
)

__all__ = [
    "OrderEvent",
    "RiskEvent",
    "ExecutionEvent",
    "PipelineEvent",
    "ObservabilityEvent",
]
