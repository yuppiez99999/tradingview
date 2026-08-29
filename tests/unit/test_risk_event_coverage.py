"""utils/risk/risk_event.py 覆盖率补测 (W7.4.5 覆盖率冲刺)

验证目标:
    1. RiskEvent.__post_init__: 自动时间戳 / TypeError / ValueError
    2. RiskEvent.to_dict / from_dict: 往返 + 无效枚举降级
    3. RiskDecision.__post_init__: TypeError / ValueError / confidence clamp / reduce_pct clamp
    4. RiskDecision.to_dict: 序列化
    5. make_margin_breach_event: 自动 severity 推断
    6. make_drawdown_breach_event: 自动 severity 推断
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.risk_event import (  # noqa: E402
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
    make_drawdown_breach_event,
    make_margin_breach_event,
)


# ============================================================
# RiskEvent __post_init__ 测试
# ============================================================
class TestRiskEventPostInit:
    """RiskEvent 构造校验测试."""

    def test_auto_fill_timestamp(self):
        """timestamp 为空时自动填充."""
        event = RiskEvent(
            event_type=RiskEventType.MARGIN_BREACH,
            source="kill_switch",
            severity=RiskSeverity.CRITICAL,
        )
        assert event.timestamp != ""
        assert event.timestamp.endswith("Z")

    def test_preserve_existing_timestamp(self):
        """timestamp 已有时保留原值."""
        event = RiskEvent(
            event_type=RiskEventType.MARGIN_BREACH,
            source="kill_switch",
            severity=RiskSeverity.CRITICAL,
            timestamp="2026-08-27T10:00:00Z",
        )
        assert event.timestamp == "2026-08-27T10:00:00Z"

    def test_invalid_event_type_raises_typeerror(self):
        """event_type 非 RiskEventType 时 TypeError."""
        with pytest.raises(TypeError, match="event_type"):
            RiskEvent(
                event_type="invalid",  # 非 RiskEventType
                source="test",
                severity=RiskSeverity.INFO,
            )

    def test_invalid_severity_raises_typeerror(self):
        """severity 非 RiskSeverity 时 TypeError."""
        with pytest.raises(TypeError, match="severity"):
            RiskEvent(
                event_type=RiskEventType.MARGIN_BREACH,
                source="test",
                severity="invalid",
            )

    def test_empty_source_raises_valueerror(self):
        """source 为空时 ValueError."""
        with pytest.raises(ValueError, match="source"):
            RiskEvent(
                event_type=RiskEventType.MARGIN_BREACH,
                source="",
                severity=RiskSeverity.INFO,
            )

    def test_non_string_source_raises_valueerror(self):
        """source 非 str 时 ValueError."""
        with pytest.raises(ValueError, match="source"):
            RiskEvent(
                event_type=RiskEventType.MARGIN_BREACH,
                source=123,
                severity=RiskSeverity.INFO,
            )


# ============================================================
# RiskEvent 序列化测试
# ============================================================
class TestRiskEventSerialization:
    """RiskEvent to_dict / from_dict 测试."""

    def test_to_dict_contains_all_fields(self):
        """to_dict 包含所有字段."""
        event = RiskEvent(
            event_type=RiskEventType.VAR_BREACH,
            source="var_monitor",
            severity=RiskSeverity.WARN,
            payload={"var_95": 0.025},
            symbol="600519",
        )
        d = event.to_dict()
        assert d["event_type"] == "var_breach"
        assert d["source"] == "var_monitor"
        assert d["severity"] == "warn"
        assert d["symbol"] == "600519"
        assert d["payload"]["var_95"] == 0.025

    def test_from_dict_valid(self):
        """from_dict 合法数据正确反序列化."""
        data = {
            "event_type": "margin_breach",
            "source": "kill_switch",
            "severity": "critical",
            "payload": {"level": 2},
            "timestamp": "2026-08-27T10:00:00Z",
            "symbol": "000858",
        }
        event = RiskEvent.from_dict(data)
        assert event.event_type == RiskEventType.MARGIN_BREACH
        assert event.severity == RiskSeverity.CRITICAL
        assert event.symbol == "000858"

    def test_from_dict_invalid_event_type_degrades(self):
        """from_dict 无效 event_type 降级到 KILL_SWITCH_TRIGGERED."""
        data = {"event_type": "unknown_type", "source": "test", "severity": "info"}
        event = RiskEvent.from_dict(data)
        assert event.event_type == RiskEventType.KILL_SWITCH_TRIGGERED

    def test_from_dict_empty_event_type_degrades(self):
        """from_dict 空 event_type 降级到 KILL_SWITCH_TRIGGERED."""
        data = {"event_type": "", "source": "test", "severity": "info"}
        event = RiskEvent.from_dict(data)
        assert event.event_type == RiskEventType.KILL_SWITCH_TRIGGERED

    def test_from_dict_invalid_severity_degrades(self):
        """from_dict 无效 severity 降级到 INFO."""
        data = {"event_type": "margin_breach", "source": "test", "severity": "unknown"}
        event = RiskEvent.from_dict(data)
        assert event.severity == RiskSeverity.INFO

    def test_from_dict_empty_severity_degrades(self):
        """from_dict 空 severity 降级到 INFO."""
        data = {"event_type": "margin_breach", "source": "test", "severity": ""}
        event = RiskEvent.from_dict(data)
        assert event.severity == RiskSeverity.INFO

    def test_to_from_roundtrip(self):
        """to_dict → from_dict 往返一致."""
        original = RiskEvent(
            event_type=RiskEventType.DRAWDOWN_BREACH,
            source="shadow_account",
            severity=RiskSeverity.CRITICAL,
            payload={"drawdown_pct": 0.08},
            symbol="510300",
        )
        restored = RiskEvent.from_dict(original.to_dict())
        assert restored.event_type == original.event_type
        assert restored.severity == original.severity
        assert restored.symbol == original.symbol


# ============================================================
# RiskDecision __post_init__ 测试
# ============================================================
class TestRiskDecisionPostInit:
    """RiskDecision 构造校验测试."""

    def test_valid_decision(self):
        """合法 RiskDecision 构造."""
        decision = RiskDecision(
            action=RiskAction.PASS,
            reason="通过风控检查",
            confidence=0.95,
            source="pretrade_guard",
        )
        assert decision.action == RiskAction.PASS
        assert decision.confidence == 0.95

    def test_invalid_action_raises_typeerror(self):
        """action 非 RiskAction 时 TypeError."""
        with pytest.raises(TypeError, match="action"):
            RiskDecision(action="invalid", reason="test")

    def test_empty_reason_raises_valueerror(self):
        """reason 为空时 ValueError."""
        with pytest.raises(ValueError, match="reason"):
            RiskDecision(action=RiskAction.PASS, reason="")

    def test_confidence_clamped_to_0_1(self):
        """confidence > 1 被截断到 1.0."""
        decision = RiskDecision(action=RiskAction.PASS, reason="test", confidence=1.5)
        assert decision.confidence == 1.0

    def test_confidence_clamped_to_0(self):
        """confidence < 0 被截断到 0.0."""
        decision = RiskDecision(action=RiskAction.PASS, reason="test", confidence=-0.5)
        assert decision.confidence == 0.0

    def test_reduce_pct_clamped_to_0_1(self):
        """reduce_pct > 1 被截断到 1.0."""
        decision = RiskDecision(
            action=RiskAction.REDUCE_POSITION, reason="test", reduce_pct=2.0
        )
        assert decision.reduce_pct == 1.0

    def test_reduce_pct_clamped_to_0(self):
        """reduce_pct < 0 被截断到 0.0."""
        decision = RiskDecision(
            action=RiskAction.REDUCE_POSITION, reason="test", reduce_pct=-0.3
        )
        assert decision.reduce_pct == 0.0

    def test_to_dict_serialization(self):
        """to_dict 序列化所有字段."""
        decision = RiskDecision(
            action=RiskAction.FORCE_LIQUIDATE,
            reason="保证金不足",
            confidence=0.8,
            source="kill_switch",
            reduce_pct=1.0,
        )
        d = decision.to_dict()
        assert d["action"] == "force_liquidate"
        assert d["reason"] == "保证金不足"
        assert d["confidence"] == 0.8
        assert d["reduce_pct"] == 1.0


# ============================================================
# 便捷工厂函数测试
# ============================================================
class TestFactoryFunctions:
    """make_margin_breach_event / make_drawdown_breach_event 工厂测试."""

    def test_make_margin_breach_event_level3_critical(self):
        """level >= 2 时 severity 为 CRITICAL."""
        event = make_margin_breach_event(
            source="kill_switch", margin_usage=0.85, level=3
        )
        assert event.event_type == RiskEventType.MARGIN_BREACH
        assert event.severity == RiskSeverity.CRITICAL
        assert event.payload["margin_usage"] == 0.85
        assert event.payload["level"] == 3

    def test_make_margin_breach_event_level1_warn(self):
        """level == 1 时 severity 为 WARN."""
        event = make_margin_breach_event(
            source="kill_switch", margin_usage=0.6, level=1
        )
        assert event.severity == RiskSeverity.WARN

    def test_make_margin_breach_event_level0_info(self):
        """level == 0 时 severity 为 INFO."""
        event = make_margin_breach_event(
            source="kill_switch", margin_usage=0.3, level=0
        )
        assert event.severity == RiskSeverity.INFO

    def test_make_margin_breach_event_explicit_severity(self):
        """显式 severity 覆盖自动推断."""
        event = make_margin_breach_event(
            source="kill_switch",
            margin_usage=0.85,
            level=3,
            severity=RiskSeverity.INFO,
        )
        assert event.severity == RiskSeverity.INFO

    def test_make_margin_breach_event_extra_payload(self):
        """extra 参数合并到 payload."""
        event = make_margin_breach_event(
            source="kill_switch",
            margin_usage=0.7,
            level=2,
            extra_field="value",
        )
        assert event.payload["extra_field"] == "value"

    def test_make_drawdown_breach_event_critical(self):
        """回撤 >= 5% 时 CRITICAL."""
        event = make_drawdown_breach_event(source="shadow_account", drawdown_pct=0.08)
        assert event.event_type == RiskEventType.DRAWDOWN_BREACH
        assert event.severity == RiskSeverity.CRITICAL
        assert event.payload["drawdown_pct"] == 0.08

    def test_make_drawdown_breach_event_warn(self):
        """回撤 >= 3% 且 < 5% 时 WARN."""
        event = make_drawdown_breach_event(source="shadow_account", drawdown_pct=0.04)
        assert event.severity == RiskSeverity.WARN

    def test_make_drawdown_breach_event_info(self):
        """回撤 < 3% 时 INFO."""
        event = make_drawdown_breach_event(source="shadow_account", drawdown_pct=0.02)
        assert event.severity == RiskSeverity.INFO

    def test_make_drawdown_breach_event_custom_window(self):
        """自定义 window 参数."""
        event = make_drawdown_breach_event(
            source="shadow_account",
            drawdown_pct=0.06,
            window="cumulative_3d",
        )
        assert event.payload["window"] == "cumulative_3d"

    def test_make_drawdown_breach_event_explicit_severity(self):
        """显式 severity 覆盖自动推断."""
        event = make_drawdown_breach_event(
            source="shadow_account",
            drawdown_pct=0.06,
            severity=RiskSeverity.INFO,
        )
        assert event.severity == RiskSeverity.INFO
