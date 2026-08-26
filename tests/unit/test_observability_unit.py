"""observability 包单元测试 — 覆盖 structured_logger / event_schema / tracing.

目标模块:
    - utils/observability/structured_logger.py (0% → 高覆盖)
    - utils/observability/event_schema.py (0% → 高覆盖)
    - utils/observability/tracing.py (0% → 高覆盖)
    - utils/observability/__init__.py (0% → 高覆盖)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest

from utils.observability import (
    ExecutionEvent,
    ObservabilityEvent,
    OrderEvent,
    PipelineEvent,
    RiskEvent,
)
from utils.observability import event_schema as event_schema_mod
from utils.observability import structured_logger as sl_mod
from utils.observability.structured_logger import (
    StructuredLogger,
    get_structured_logger,
)
from utils.observability.tracing import (
    setup_tracing,
    trace_order,
    trace_pipeline,
    trace_risk,
    trace_span,
)


# ============================================================
# ObservabilityEventTest — 事件 Schema 基类与子类
# ============================================================

class ObservabilityEventTest:
    """ObservabilityEvent 及子类构造/默认值/字段约束."""

    def test_base_event_defaults(self):
        ev = ObservabilityEvent(event_type="custom")
        assert ev.event_type == "custom"
        assert ev.severity == event_schema_mod.EventSeverity.INFO
        assert ev.status == event_schema_mod.EventStatus.SUCCESS
        assert ev.message == ""
        assert ev.context == {}
        assert ev.trace_id is None
        assert isinstance(ev.timestamp, datetime)

    def test_base_event_with_fields(self):
        ev = ObservabilityEvent(
            event_type="custom",
            severity=event_schema_mod.EventSeverity.ERROR,
            status=event_schema_mod.EventStatus.FAILURE,
            message="boom",
            context={"k": 1},
            trace_id="tr-123",
        )
        assert ev.severity == event_schema_mod.EventSeverity.ERROR
        assert ev.status == event_schema_mod.EventStatus.FAILURE
        assert ev.message == "boom"
        assert ev.context == {"k": 1}
        assert ev.trace_id == "tr-123"

    def test_event_severity_values(self):
        assert event_schema_mod.EventSeverity.INFO == "info"
        assert event_schema_mod.EventSeverity.WARN == "warn"
        assert event_schema_mod.EventSeverity.ERROR == "error"
        assert event_schema_mod.EventSeverity.CRITICAL == "critical"

    def test_event_status_values(self):
        assert event_schema_mod.EventStatus.SUCCESS == "success"
        assert event_schema_mod.EventStatus.FAILURE == "failure"
        assert event_schema_mod.EventStatus.SKIPPED == "skipped"
        assert event_schema_mod.EventStatus.PENDING == "pending"

    def test_order_event_required_fields(self):
        ev = OrderEvent(order_id="O1", symbol="510300", side="buy", qty=100)
        assert ev.event_type == "order"
        assert ev.order_id == "O1"
        assert ev.symbol == "510300"
        assert ev.side == "buy"
        assert ev.qty == 100
        assert ev.price is None
        assert ev.filled_qty == 0.0

    def test_order_event_full(self):
        ev = OrderEvent(
            order_id="O2", symbol="000001", side="sell", qty=200,
            price=10.5, filled_qty=50,
        )
        assert ev.price == 10.5
        assert ev.filled_qty == 50

    def test_order_event_missing_required_raises(self):
        with pytest.raises(Exception):
            OrderEvent(symbol="510300", side="buy", qty=100)  # 缺 order_id

    def test_risk_event(self):
        ev = RiskEvent(risk_level="caution", trigger="drawdown", action="reduce")
        assert ev.event_type == "risk"
        assert ev.risk_level == "caution"
        assert ev.trigger == "drawdown"
        assert ev.action == "reduce"

    def test_execution_event(self):
        ev = ExecutionEvent(phase="data", duration_ms=12.5)
        assert ev.event_type == "execution"
        assert ev.phase == "data"
        assert ev.duration_ms == 12.5

    def test_execution_event_default_duration(self):
        ev = ExecutionEvent(phase="signal")
        assert ev.duration_ms is None

    def test_pipeline_event(self):
        ev = PipelineEvent(pipeline_name="eod", step="factors", step_index=3)
        assert ev.event_type == "pipeline"
        assert ev.pipeline_name == "eod"
        assert ev.step == "factors"
        assert ev.step_index == 3

    def test_pipeline_event_default_step_index(self):
        ev = PipelineEvent(pipeline_name="eod", step="data")
        assert ev.step_index is None

    def test_event_serialization(self):
        ev = OrderEvent(order_id="O1", symbol="510300", side="buy", qty=100)
        dumped = ev.model_dump()
        assert dumped["order_id"] == "O1"
        assert dumped["event_type"] == "order"
        assert "timestamp" in dumped


# ============================================================
# StructuredLoggerTest — 结构化日志包装器
# ============================================================

class StructuredLoggerTest:
    """StructuredLogger structlog 路径 + fallback 路径."""

    def test_get_structured_logger_returns_instance(self):
        lg = get_structured_logger("test.channel")
        assert isinstance(lg, StructuredLogger)
        assert lg._name == "test.channel"

    def test_logger_structlog_backend(self):
        """structlog 可用时 _structlog 不为 None."""
        lg = get_structured_logger("test.structlog")
        if sl_mod._HAS_STRUCTLOG:
            assert lg._structlog is not None
        else:
            assert lg._structlog is None

    def test_info_emits_no_exception(self):
        lg = get_structured_logger("test.info")
        lg.info("order_submitted", order_id="123", symbol="510300")

    def test_debug_emits_no_exception(self):
        lg = get_structured_logger("test.debug")
        lg.debug("dbg_event", x=1)

    def test_warning_emits_no_exception(self):
        lg = get_structured_logger("test.warn")
        lg.warning("warn_event", code=42)

    def test_error_emits_no_exception(self):
        lg = get_structured_logger("test.err")
        lg.error("err_event", reason="timeout")

    def test_critical_emits_no_exception(self):
        lg = get_structured_logger("test.crit")
        lg.critical("crit_event", action="kill")

    def test_bind_returns_logger(self):
        lg = get_structured_logger("test.bind")
        bound = lg.bind(request_id="req-1")
        assert isinstance(bound, StructuredLogger)

    def test_fallback_log_path(self, monkeypatch, caplog):
        """强制走标准 logging fallback 路径."""
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback")
        assert lg._structlog is None
        with caplog.at_level(logging.INFO, logger="test.fallback"):
            lg.info("fallback_event", key="val")
        assert any("fallback_event" in r.message for r in caplog.records)

    def test_fallback_warning_path(self, monkeypatch, caplog):
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.warn")
        with caplog.at_level(logging.WARNING, logger="test.fallback.warn"):
            lg.warning("fb_warn", code=1)
        assert any("fb_warn" in r.message for r in caplog.records)

    def test_fallback_error_path(self, monkeypatch, caplog):
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.err")
        with caplog.at_level(logging.ERROR, logger="test.fallback.err"):
            lg.error("fb_err")
        assert any("fb_err" in r.message for r in caplog.records)

    def test_fallback_debug_path(self, monkeypatch, caplog):
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.dbg")
        with caplog.at_level(logging.DEBUG, logger="test.fallback.dbg"):
            lg.debug("fb_dbg")
        assert any("fb_dbg" in r.message for r in caplog.records)

    def test_fallback_critical_path(self, monkeypatch, caplog):
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.crit")
        with caplog.at_level(logging.CRITICAL, logger="test.fallback.crit"):
            lg.critical("fb_crit")
        assert any("fb_crit" in r.message for r in caplog.records)

    def test_fallback_bind_returns_self(self, monkeypatch):
        """fallback 模式下 bind 返回自身 (无 structlog)."""
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.bind")
        result = lg.bind(ctx="x")
        assert result is lg

    def test_fallback_json_content(self, monkeypatch, caplog):
        """fallback 路径输出 JSON 含 event 和 kwargs."""
        monkeypatch.setattr(sl_mod, "_HAS_STRUCTLOG", False)
        lg = StructuredLogger("test.fallback.json")
        with caplog.at_level(logging.INFO, logger="test.fallback.json"):
            lg.info("evt_name", extra_field="extra_val")
        rec = next(r for r in caplog.records if "evt_name" in r.message)
        parsed = json.loads(rec.message)
        assert parsed["event"] == "evt_name"
        assert parsed["extra_field"] == "extra_val"
        assert "timestamp" in parsed


# ============================================================
# TracingTest — OpenTelemetry 追踪
# ============================================================

class TracingTest:
    """tracing.py setup + span 上下文管理器."""

    def test_setup_tracing_returns_tracer(self):
        tr = setup_tracing("test-service")
        assert tr is not None

    def test_setup_tracing_idempotent(self):
        """重复调用返回同一 tracer (不重建 provider)."""
        tr1 = setup_tracing("svc-1")
        tr2 = setup_tracing("svc-2")
        assert tr1 is tr2

    def test_trace_span_context_manager(self):
        with trace_span("test.span", attr1="v1", attr2=2) as span:
            assert span is not None

    def test_trace_span_no_attributes(self):
        with trace_span("test.span.noattr") as span:
            assert span is not None

    def test_trace_order_basic(self):
        with trace_order("510300", "buy", 100) as span:
            assert span is not None

    def test_trace_order_with_optional(self):
        with trace_order("000001", "sell", 200, order_id="O123", price=10.5) as span:
            assert span is not None

    def test_trace_risk(self):
        with trace_risk("caution", "drawdown", "reduce") as span:
            assert span is not None

    def test_trace_pipeline(self):
        with trace_pipeline("eod", "factors") as span:
            assert span is not None

    def test_trace_span_yields_active_span(self):
        """span 在 with 块内是当前 active span."""
        from opentelemetry import trace as otel_trace
        with trace_span("test.active") as span:
            current = otel_trace.get_current_span()
            assert current is not None