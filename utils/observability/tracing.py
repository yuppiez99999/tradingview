"""Phase C 分布式追踪 (OpenTelemetry).

为关键交易链路提供 span 埋点:
    - 订单提交/成交
    - 风控检查/触发
    - 管线执行

用法:
    from utils.observability.tracing import tracer, trace_order, trace_risk

    with trace_order("510300", "buy", 100):
        # 下单逻辑
        ...
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

_TRACER_PROVIDER: Optional[TracerProvider] = None
tracer: trace.Tracer


def setup_tracing(service_name: str = "quant-trading-system") -> trace.Tracer:
    """初始化 OpenTelemetry 追踪.

    当前使用 ConsoleSpanExporter (控制台输出),
    后续可替换为 OTLPExporter (Jaeger/Tempo).
    """
    global _TRACER_PROVIDER, tracer

    if _TRACER_PROVIDER is not None:
        return tracer

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(ConsoleSpanExporter())
    )
    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    tracer = trace.get_tracer(service_name)
    return tracer


tracer = setup_tracing()


@contextmanager
def trace_span(
    name: str,
    **attributes: Any,
) -> Iterator[trace.Span]:
    """通用 span 上下文管理器.

    Args:
        name: span 名称
        **attributes: span 属性 (key-value)
    """
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
        yield span


@contextmanager
def trace_order(
    symbol: str,
    side: str,
    qty: float,
    order_id: Optional[str] = None,
    price: Optional[float] = None,
) -> Iterator[trace.Span]:
    """订单链路 span 埋点."""
    attrs: dict[str, Any] = {
        "order.symbol": symbol,
        "order.side": side,
        "order.qty": qty,
    }
    if order_id is not None:
        attrs["order.id"] = order_id
    if price is not None:
        attrs["order.price"] = price

    with trace_span("order.submit", **attrs) as span:
        yield span


@contextmanager
def trace_risk(
    risk_level: str,
    trigger: str,
    action: str,
) -> Iterator[trace.Span]:
    """风控链路 span 埋点."""
    with trace_span(
        "risk.check",
        **{
            "risk.level": risk_level,
            "risk.trigger": trigger,
            "risk.action": action,
        },
    ) as span:
        yield span


@contextmanager
def trace_pipeline(
    pipeline_name: str,
    step: str,
) -> Iterator[trace.Span]:
    """管线链路 span 埋点."""
    with trace_span(
        "pipeline.execute",
        **{
            "pipeline.name": pipeline_name,
            "pipeline.step": step,
        },
    ) as span:
        yield span
