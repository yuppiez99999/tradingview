#!/usr/bin/env python
"""
test_g7_risk_bus_boost.py — 风控事件总线覆盖率补强测试

覆盖 P0 risk 链路: utils/risk/risk_bus.py
测试范围:
    - RiskBus 单例模式
    - subscribe/unsubscribe 订阅管理
    - publish 事件发布 (同步路径)
    - sync_decide 决策聚合
    - get_recent_events 事件查询
    - 订阅者异常隔离
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.risk.risk_bus import (
    RiskBus,
    SubscriptionError,
    get_bus,
)
from utils.risk.risk_event import (
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
)


@pytest.fixture
def bus(tmp_path: Path) -> RiskBus:
    """每个测试用独立 RiskBus 实例."""
    b = RiskBus(audit_log_dir=tmp_path / "audit")
    return b


def _make_event(
    event_type: RiskEventType = RiskEventType.MARGIN_BREACH,
    severity: RiskSeverity = RiskSeverity.WARN,
) -> RiskEvent:
    return RiskEvent(
        event_type=event_type,
        source="test",
        severity=severity,
        payload={"key": "value"},
    )


class TestRiskBusSingleton:
    """RiskBus 单例模式测试."""

    def test_get_instance(self) -> None:
        RiskBus.reset_instance()
        b1 = RiskBus.get_instance()
        b2 = RiskBus.get_instance()
        assert b1 is b2

    def test_reset_instance(self) -> None:
        RiskBus.get_instance()
        RiskBus.reset_instance()
        assert RiskBus._instance is None

    def test_get_bus_alias(self) -> None:
        RiskBus.reset_instance()
        b = get_bus()
        assert isinstance(b, RiskBus)


class TestSubscribe:
    """subscribe/unsubscribe 测试."""

    def test_subscribe_callable(self, bus: RiskBus) -> None:
        def handler(event: RiskEvent) -> None:
            pass

        bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        assert bus.get_subscriber_count(RiskEventType.MARGIN_BREACH) == 1

    def test_subscribe_non_callable_raises(self, bus: RiskBus) -> None:
        with pytest.raises(SubscriptionError):
            bus.subscribe(RiskEventType.MARGIN_BREACH, "not_callable")  # type: ignore[arg-type]

    def test_unsubscribe_existing(self, bus: RiskBus) -> None:
        def handler(event: RiskEvent) -> None:
            pass

        bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        assert bus.unsubscribe(RiskEventType.MARGIN_BREACH, handler) is True
        assert bus.get_subscriber_count(RiskEventType.MARGIN_BREACH) == 0

    def test_unsubscribe_nonexistent(self, bus: RiskBus) -> None:
        def handler(event: RiskEvent) -> None:
            pass

        assert bus.unsubscribe(RiskEventType.MARGIN_BREACH, handler) is False

    def test_clear_subscribers(self, bus: RiskBus) -> None:
        def handler(event: RiskEvent) -> None:
            pass

        bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        bus.clear_subscribers()
        assert bus.get_subscriber_count() == 0


class TestPublish:
    """publish 事件发布测试."""

    def test_publish_no_subscribers(self, bus: RiskBus) -> None:
        event = _make_event()
        invoked = bus.publish(event)
        assert invoked == 0

    def test_publish_invokes_subscriber(self, bus: RiskBus) -> None:
        called: list[RiskEvent] = []

        def handler(event: RiskEvent) -> None:
            called.append(event)

        bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        event = _make_event()
        invoked = bus.publish(event)
        assert invoked == 1
        assert len(called) == 1

    def test_publish_subscriber_exception_isolated(self, bus: RiskBus) -> None:
        def bad_handler(event: RiskEvent) -> None:
            raise ValueError("test error")

        bus.subscribe(RiskEventType.MARGIN_BREACH, bad_handler)
        event = _make_event()
        invoked = bus.publish(event)
        assert invoked == 0

    def test_publish_multiple_subscribers(self, bus: RiskBus) -> None:
        count = [0]

        def h1(e: RiskEvent) -> None:
            count[0] += 1

        def h2(e: RiskEvent) -> None:
            count[0] += 1

        bus.subscribe(RiskEventType.MARGIN_BREACH, h1)
        bus.subscribe(RiskEventType.MARGIN_BREACH, h2)
        bus.publish(_make_event())
        assert count[0] == 2

    def test_publish_stores_history(self, bus: RiskBus) -> None:
        bus.publish(_make_event())
        events = bus.get_recent_events()
        assert len(events) >= 1


class TestSyncDecide:
    """sync_decide 决策聚合测试."""

    def test_no_deciders_returns_pass(self, bus: RiskBus) -> None:
        event = _make_event()
        decision = bus.sync_decide(event)
        assert decision.action == RiskAction.PASS
        assert decision.reason == "no_decision_subscribers"

    def test_single_decider(self, bus: RiskBus) -> None:
        def decider(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.REDUCE_POSITION,
                reason="test",
                confidence=0.8,
                source="test_decider",
            )

        bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider)
        decision = bus.sync_decide(_make_event())
        assert decision.action == RiskAction.REDUCE_POSITION

    def test_decider_exception_isolated(self, bus: RiskBus) -> None:
        def bad_decider(event: RiskEvent) -> RiskDecision:
            raise RuntimeError("test error")

        bus.subscribe_decision(RiskEventType.MARGIN_BREACH, bad_decider)
        decision = bus.sync_decide(_make_event())
        assert decision.action == RiskAction.PASS
        assert decision.reason == "all_deciders_failed"

    def test_aggregate_strictest(self, bus: RiskBus) -> None:
        def mild(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.PASS, reason="mild", confidence=0.5, source="m"
            )

        def strict(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.KILL_SWITCH,
                reason="strict",
                confidence=0.9,
                source="s",
            )

        bus.subscribe_decision(RiskEventType.MARGIN_BREACH, mild)
        bus.subscribe_decision(RiskEventType.MARGIN_BREACH, strict)
        decision = bus.sync_decide(_make_event())
        assert decision.action == RiskAction.KILL_SWITCH


class TestQueryAPI:
    """查询 API 测试."""

    def test_get_recent_events_filtered(self, bus: RiskBus) -> None:
        bus.publish(_make_event(RiskEventType.MARGIN_BREACH))
        bus.publish(_make_event(RiskEventType.VAR_BREACH))
        margin_events = bus.get_recent_events(event_type=RiskEventType.MARGIN_BREACH)
        assert all(e.event_type == RiskEventType.MARGIN_BREACH for e in margin_events)

    def test_get_recent_events_limit(self, bus: RiskBus) -> None:
        for _ in range(5):
            bus.publish(_make_event())
        events = bus.get_recent_events(limit=3)
        assert len(events) <= 3

    def test_get_subscriber_count_all(self, bus: RiskBus) -> None:
        def h(e: RiskEvent) -> None:
            pass

        bus.subscribe(RiskEventType.MARGIN_BREACH, h)
        bus.subscribe(RiskEventType.VAR_BREACH, h)
        assert bus.get_subscriber_count() == 2

    def test_get_decision_subscriber_count(self, bus: RiskBus) -> None:
        def d(e: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.PASS, reason="", confidence=0.0, source="t"
            )

        bus.subscribe_decision(RiskEventType.MARGIN_BREACH, d)
        assert bus.get_decision_subscriber_count(RiskEventType.MARGIN_BREACH) == 1
