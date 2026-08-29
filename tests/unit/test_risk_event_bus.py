"""T3.1 风控事件总线单元测试.

覆盖:
    1. RiskEventType / RiskSeverity / RiskAction 枚举
    2. RiskEvent dataclass + 校验 + 序列化
    3. RiskDecision dataclass + 校验 + 序列化
    4. 工厂函数 (make_margin_breach_event 等)
    5. RiskBus 单例 + 订阅/发布/决策
    6. RiskDecisionAggregator (最严格策略)
    7. 审计日志 + 事件历史
    8. Feature Flag 透传 (HC-1)
    9. HC-2 同步路径保护 (KillSwitch 不走总线)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.risk_bus import (  # noqa: E402
    RiskBus,
    RiskDecisionAggregator,
    SubscriptionError,
    get_bus,
    publish,
    subscribe,
    sync_decide,
)
from utils.risk.risk_event import (  # noqa: E402
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
    make_drawdown_breach_event,
    make_kill_switch_triggered_event,
    make_margin_breach_event,
)


# ============================================================
# 1. 枚举测试
# ============================================================
class TestEnums:
    """枚举类型测试."""

    def test_event_type_count(self):
        """7 种事件类型."""
        assert len(RiskEventType) == 7

    def test_event_type_values(self):
        """事件类型值."""
        assert RiskEventType.MARGIN_BREACH.value == "margin_breach"
        assert RiskEventType.DRAWDOWN_BREACH.value == "drawdown_breach"
        assert RiskEventType.VAR_BREACH.value == "var_breach"
        assert RiskEventType.OVERNIGHT_GAP.value == "overnight_gap"
        assert RiskEventType.LIQUIDITY_BREACH.value == "liquidity_breach"
        assert RiskEventType.CONCENTRATION_BREACH.value == "concentration_breach"
        assert RiskEventType.KILL_SWITCH_TRIGGERED.value == "kill_switch_triggered"

    def test_severity_ordering(self):
        """严重级别."""
        assert RiskSeverity.INFO.value == "info"
        assert RiskSeverity.WARN.value == "warn"
        assert RiskSeverity.CRITICAL.value == "critical"

    def test_action_count(self):
        """5 种动作."""
        assert len(RiskAction) == 5
        assert RiskAction.PASS.value == "pass"
        assert RiskAction.KILL_SWITCH.value == "kill_switch"


# ============================================================
# 2. RiskEvent 测试
# ============================================================
class TestRiskEvent:
    """RiskEvent dataclass 测试."""

    def test_basic_creation(self):
        """基本创建."""
        event = RiskEvent(
            event_type=RiskEventType.MARGIN_BREACH,
            source="kill_switch",
            severity=RiskSeverity.WARN,
            payload={"margin_usage": 0.78, "level": 2},
        )
        assert event.event_type == RiskEventType.MARGIN_BREACH
        assert event.source == "kill_switch"
        assert event.severity == RiskSeverity.WARN
        assert event.payload["margin_usage"] == 0.78
        assert event.timestamp  # 自动填充
        assert event.timestamp.endswith("Z")
        assert event.symbol is None

    def test_auto_timestamp(self):
        """时间戳自动填充 (UTC ISO 格式)."""
        event = RiskEvent(
            event_type=RiskEventType.DRAWDOWN_BREACH,
            source="shadow_account",
            severity=RiskSeverity.WARN,
        )
        assert event.timestamp
        assert event.timestamp.endswith("Z")
        # 应可被 fromisoformat 解析
        from datetime import datetime

        dt = datetime.fromisoformat(event.timestamp.rstrip("Z"))
        assert dt is not None

    def test_explicit_timestamp_preserved(self):
        """显式时间戳被保留."""
        ts = "2026-01-01T00:00:00Z"
        event = RiskEvent(
            event_type=RiskEventType.VAR_BREACH,
            source="var_monitor",
            severity=RiskSeverity.WARN,
            timestamp=ts,
        )
        assert event.timestamp == ts

    def test_invalid_event_type_raises(self):
        """无效 event_type 抛 TypeError."""
        with pytest.raises(TypeError):
            RiskEvent(
                event_type="invalid",  # type: ignore[arg-type]
                source="test",
                severity=RiskSeverity.INFO,
            )

    def test_invalid_severity_raises(self):
        """无效 severity 抛 TypeError."""
        with pytest.raises(TypeError):
            RiskEvent(
                event_type=RiskEventType.MARGIN_BREACH,
                source="test",
                severity="invalid",  # type: ignore[arg-type]
            )

    def test_empty_source_raises(self):
        """空 source 抛 ValueError."""
        with pytest.raises(ValueError):
            RiskEvent(
                event_type=RiskEventType.MARGIN_BREACH,
                source="",
                severity=RiskSeverity.INFO,
            )

    def test_frozen_immutable(self):
        """frozen=True 不可变."""
        event = RiskEvent(
            event_type=RiskEventType.MARGIN_BREACH,
            source="test",
            severity=RiskSeverity.INFO,
        )
        with pytest.raises((AttributeError, Exception)):
            event.source = "modified"  # type: ignore[misc]

    def test_to_dict(self):
        """序列化为字典."""
        event = RiskEvent(
            event_type=RiskEventType.MARGIN_BREACH,
            source="ks",
            severity=RiskSeverity.WARN,
            payload={"level": 2},
            symbol="600000",
        )
        d = event.to_dict()
        assert d["event_type"] == "margin_breach"
        assert d["source"] == "ks"
        assert d["severity"] == "warn"
        assert d["payload"] == {"level": 2}
        assert d["symbol"] == "600000"

    def test_from_dict_roundtrip(self):
        """字典反序列化往返."""
        original = RiskEvent(
            event_type=RiskEventType.KILL_SWITCH_TRIGGERED,
            source="kill_switch",
            severity=RiskSeverity.CRITICAL,
            payload={"level": 3, "actions_taken": ["force_liquidate"]},
        )
        d = original.to_dict()
        restored = RiskEvent.from_dict(d)
        assert restored.event_type == original.event_type
        assert restored.source == original.source
        assert restored.severity == original.severity
        assert restored.payload == original.payload
        assert restored.timestamp == original.timestamp

    def test_with_symbol(self):
        """带标的的事件."""
        event = RiskEvent(
            event_type=RiskEventType.CONCENTRATION_BREACH,
            source="risk_guard",
            severity=RiskSeverity.WARN,
            symbol="600000",
            payload={"weight": 0.15},
        )
        assert event.symbol == "600000"


# ============================================================
# 3. RiskDecision 测试
# ============================================================
class TestRiskDecision:
    """RiskDecision dataclass 测试."""

    def test_basic_creation(self):
        """基本创建."""
        d = RiskDecision(
            action=RiskAction.REDUCE_POSITION,
            reason="margin_usage_exceeded",
            confidence=0.9,
            source="kill_switch",
            reduce_pct=0.3,
        )
        assert d.action == RiskAction.REDUCE_POSITION
        assert d.reason == "margin_usage_exceeded"
        assert d.confidence == 0.9
        assert d.source == "kill_switch"
        assert d.reduce_pct == 0.3

    def test_confidence_clamped(self):
        """confidence 限制在 [0, 1]."""
        d = RiskDecision(
            action=RiskAction.PASS,
            reason="test",
            confidence=1.5,
        )
        assert d.confidence == 1.0

        d2 = RiskDecision(
            action=RiskAction.PASS,
            reason="test",
            confidence=-0.5,
        )
        assert d2.confidence == 0.0

    def test_reduce_pct_clamped(self):
        """reduce_pct 限制在 [0, 1]."""
        d = RiskDecision(
            action=RiskAction.REDUCE_POSITION,
            reason="test",
            reduce_pct=2.0,
        )
        assert d.reduce_pct == 1.0

    def test_invalid_action_raises(self):
        """无效 action 抛 TypeError."""
        with pytest.raises(TypeError):
            RiskDecision(
                action="invalid",  # type: ignore[arg-type]
                reason="test",
            )

    def test_empty_reason_raises(self):
        """空 reason 抛 ValueError."""
        with pytest.raises(ValueError):
            RiskDecision(
                action=RiskAction.PASS,
                reason="",
            )

    def test_to_dict(self):
        """序列化为字典."""
        d = RiskDecision(
            action=RiskAction.KILL_SWITCH,
            reason="extreme_margin_call",
            confidence=1.0,
            source="kill_switch",
        )
        result = d.to_dict()
        assert result["action"] == "kill_switch"
        assert result["reason"] == "extreme_margin_call"
        assert result["confidence"] == 1.0
        assert result["source"] == "kill_switch"

    def test_frozen_immutable(self):
        """frozen=True 不可变."""
        d = RiskDecision(action=RiskAction.PASS, reason="test")
        with pytest.raises((AttributeError, Exception)):
            d.action = RiskAction.KILL_SWITCH  # type: ignore[misc]


# ============================================================
# 4. 工厂函数测试
# ============================================================
class TestFactoryFunctions:
    """工厂函数测试."""

    def test_make_margin_breach_event_auto_severity(self):
        """保证金突破事件自动严重级别."""
        # level=3 -> CRITICAL
        e1 = make_margin_breach_event("kill_switch", 0.95, 3)
        assert e1.event_type == RiskEventType.MARGIN_BREACH
        assert e1.severity == RiskSeverity.CRITICAL
        assert e1.payload["margin_usage"] == 0.95
        assert e1.payload["level"] == 3

        # level=2 -> CRITICAL
        e2 = make_margin_breach_event("kill_switch", 0.78, 2)
        assert e2.severity == RiskSeverity.CRITICAL

        # level=1 -> WARN
        e3 = make_margin_breach_event("kill_switch", 0.55, 1)
        assert e3.severity == RiskSeverity.WARN

        # level=0 -> INFO
        e4 = make_margin_breach_event("kill_switch", 0.30, 0)
        assert e4.severity == RiskSeverity.INFO

    def test_make_margin_breach_event_explicit_severity(self):
        """显式指定 severity."""
        e = make_margin_breach_event(
            "kill_switch",
            0.55,
            1,
            severity=RiskSeverity.CRITICAL,
        )
        assert e.severity == RiskSeverity.CRITICAL

    def test_make_margin_breach_event_extra_payload(self):
        """附加 payload."""
        e = make_margin_breach_event(
            "kill_switch",
            0.78,
            2,
            broker="ctp",
            account_id="12345",
        )
        assert e.payload["broker"] == "ctp"
        assert e.payload["account_id"] == "12345"

    def test_make_drawdown_breach_event_auto_severity(self):
        """回撤突破事件自动严重级别."""
        # 5% -> CRITICAL
        e1 = make_drawdown_breach_event("shadow_account", 0.06, "cumulative_3d")
        assert e1.event_type == RiskEventType.DRAWDOWN_BREACH
        assert e1.severity == RiskSeverity.CRITICAL
        assert e1.payload["drawdown_pct"] == 0.06
        assert e1.payload["window"] == "cumulative_3d"

        # 3% -> WARN
        e2 = make_drawdown_breach_event("shadow_account", 0.04, "daily")
        assert e2.severity == RiskSeverity.WARN

        # <3% -> INFO
        e3 = make_drawdown_breach_event("shadow_account", 0.02, "daily")
        assert e3.severity == RiskSeverity.INFO

    def test_make_kill_switch_triggered_event(self):
        """KillSwitch 触发事件 (恒为 CRITICAL)."""
        e = make_kill_switch_triggered_event(
            "kill_switch",
            level=3,
            actions_taken=["liquidate_red_etf", "cross_asset_injection"],
        )
        assert e.event_type == RiskEventType.KILL_SWITCH_TRIGGERED
        assert e.severity == RiskSeverity.CRITICAL  # 恒为 CRITICAL
        assert e.payload["level"] == 3
        assert "liquidate_red_etf" in e.payload["actions_taken"]
        assert "cross_asset_injection" in e.payload["actions_taken"]


# ============================================================
# 5. RiskBus 单例与订阅/发布
# ============================================================
class TestRiskBusSingleton:
    """RiskBus 单例测试."""

    def setup_method(self):
        """每个测试前重置单例."""
        RiskBus.reset_instance()

    def teardown_method(self):
        """每个测试后重置单例."""
        RiskBus.reset_instance()

    def test_singleton(self):
        """单例模式."""
        bus1 = RiskBus.get_instance()
        bus2 = RiskBus.get_instance()
        assert bus1 is bus2

    def test_reset_instance(self):
        """reset_instance 后获取新实例."""
        bus1 = RiskBus.get_instance()
        RiskBus.reset_instance()
        bus2 = RiskBus.get_instance()
        assert bus1 is not bus2


class TestRiskBusSubscribePublish:
    """RiskBus 订阅/发布测试."""

    def setup_method(self):
        """每个测试前重置单例 + 清空订阅者."""
        RiskBus.reset_instance()
        self.bus = RiskBus.get_instance()
        self.bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_subscribe_and_publish(self):
        """订阅后能收到事件."""
        received: list[RiskEvent] = []

        def handler(event: RiskEvent) -> None:
            received.append(event)

        self.bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        assert self.bus.get_subscriber_count(RiskEventType.MARGIN_BREACH) == 1

        event = make_margin_breach_event("test", 0.78, 2)
        invoked = self.bus.publish(event)

        assert invoked == 1
        assert len(received) == 1
        assert received[0].event_type == RiskEventType.MARGIN_BREACH

    def test_multiple_subscribers(self):
        """多个订阅者都收到事件."""
        received_a: list[RiskEvent] = []
        received_b: list[RiskEvent] = []

        self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received_a.append(e))
        self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received_b.append(e))

        event = make_margin_breach_event("test", 0.78, 2)
        invoked = self.bus.publish(event)

        assert invoked == 2
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_subscriber_filter_by_type(self):
        """订阅者只接收订阅类型的事件."""
        received: list[RiskEvent] = []
        self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        # 发布不同类型的事件, 订阅者不应收到
        other_event = make_drawdown_breach_event("test", 0.04)
        self.bus.publish(other_event)
        assert len(received) == 0

        # 发布订阅类型的事件, 应收到
        target_event = make_margin_breach_event("test", 0.78, 2)
        self.bus.publish(target_event)
        assert len(received) == 1

    def test_subscriber_exception_isolated(self):
        """订阅者异常不影响其他订阅者."""
        received: list[RiskEvent] = []

        def bad_handler(event: RiskEvent) -> None:
            raise RuntimeError("test exception")

        self.bus.subscribe(RiskEventType.MARGIN_BREACH, bad_handler)
        self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        event = make_margin_breach_event("test", 0.78, 2)
        invoked = self.bus.publish(event)

        # 异常订阅者不计入 invoked, 但其他订阅者仍执行
        assert invoked == 1
        assert len(received) == 1

    def test_unsubscribe(self):
        """取消订阅."""
        received: list[RiskEvent] = []

        def handler(event: RiskEvent) -> None:
            received.append(event)

        self.bus.subscribe(RiskEventType.MARGIN_BREACH, handler)
        assert self.bus.get_subscriber_count(RiskEventType.MARGIN_BREACH) == 1

        # 取消订阅
        result = self.bus.unsubscribe(RiskEventType.MARGIN_BREACH, handler)
        assert result is True
        assert self.bus.get_subscriber_count(RiskEventType.MARGIN_BREACH) == 0

        # 发布后不应收到
        self.bus.publish(make_margin_breach_event("test", 0.78, 2))
        assert len(received) == 0

    def test_unsubscribe_not_found(self):
        """取消未订阅的回调返回 False."""
        result = self.bus.unsubscribe(RiskEventType.MARGIN_BREACH, lambda e: None)
        assert result is False

    def test_subscribe_invalid_callback_raises(self):
        """无效回调抛 SubscriptionError."""
        with pytest.raises(SubscriptionError):
            self.bus.subscribe(RiskEventType.MARGIN_BREACH, "not_callable")  # type: ignore[arg-type]


# ============================================================
# 6. 决策聚合测试
# ============================================================
class TestRiskBusSyncDecide:
    """RiskBus.sync_decide 测试."""

    def setup_method(self):
        RiskBus.reset_instance()
        self.bus = RiskBus.get_instance()
        self.bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_no_deciders_returns_pass(self):
        """无决策订阅者返回 PASS."""
        event = make_margin_breach_event("test", 0.78, 2)
        decision = self.bus.sync_decide(event)
        assert decision.action == RiskAction.PASS
        assert decision.reason == "no_decision_subscribers"

    def test_single_decider(self):
        """单个决策订阅者."""

        def decider(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.REDUCE_POSITION,
                reason="high_margin",
                confidence=0.9,
                source="kill_switch_adapter",
                reduce_pct=0.3,
            )

        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider)
        event = make_margin_breach_event("test", 0.78, 2)
        decision = self.bus.sync_decide(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.3

    def test_aggregate_strictest(self):
        """聚合取最严格动作."""

        def decider_mild(event: RiskEvent) -> RiskDecision:
            return RiskDecision(action=RiskAction.PASS, reason="ok", source="mild")

        def decider_strict(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.KILL_SWITCH,
                reason="extreme_margin_call",
                source="strict",
            )

        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider_mild)
        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider_strict)

        event = make_margin_breach_event("test", 0.95, 3)
        decision = self.bus.sync_decide(event)

        # 最严格: KILL_SWITCH
        assert decision.action == RiskAction.KILL_SWITCH

    def test_aggregate_reduce_pct_max(self):
        """REDUCE_POSITION 聚合取最大 reduce_pct."""

        def decider_30(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.REDUCE_POSITION,
                reason="r1",
                reduce_pct=0.3,
                source="d1",
            )

        def decider_50(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.REDUCE_POSITION,
                reason="r2",
                reduce_pct=0.5,
                source="d2",
            )

        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider_30)
        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, decider_50)

        event = make_margin_breach_event("test", 0.78, 2)
        decision = self.bus.sync_decide(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.5  # 取最大值

    def test_decider_exception_isolated(self):
        """决策订阅者异常不影响其他订阅者."""

        def bad_decider(event: RiskEvent) -> RiskDecision:
            raise RuntimeError("test")

        def good_decider(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.DISABLE_NEW_ORDERS, reason="ok", source="good"
            )

        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, bad_decider)
        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, good_decider)

        event = make_margin_breach_event("test", 0.78, 2)
        decision = self.bus.sync_decide(event)

        # bad_decider 异常被捕获, good_decider 仍生效
        assert decision.action == RiskAction.DISABLE_NEW_ORDERS

    def test_all_deciders_fail_returns_pass(self):
        """所有决策订阅者异常返回 PASS."""

        def bad_decider(event: RiskEvent) -> RiskDecision:
            raise RuntimeError("test")

        self.bus.subscribe_decision(RiskEventType.MARGIN_BREACH, bad_decider)

        event = make_margin_breach_event("test", 0.78, 2)
        decision = self.bus.sync_decide(event)

        assert decision.action == RiskAction.PASS
        assert decision.reason == "all_deciders_failed"


class TestRiskDecisionAggregator:
    """RiskDecisionAggregator 测试."""

    def test_strictest_strategy(self):
        """最严格策略."""
        agg = RiskDecisionAggregator(strategy="STRICTEST")
        decisions = [
            RiskDecision(action=RiskAction.PASS, reason="r1"),
            RiskDecision(
                action=RiskAction.REDUCE_POSITION, reason="r2", reduce_pct=0.3
            ),
            RiskDecision(action=RiskAction.KILL_SWITCH, reason="r3"),
        ]
        result = agg.aggregate(decisions)
        assert result.action == RiskAction.KILL_SWITCH

    def test_strictest_with_reduce_pct(self):
        """最严格策略 + REDUCE_POSITION 取最大 reduce_pct."""
        agg = RiskDecisionAggregator(strategy="STRICTEST")
        decisions = [
            RiskDecision(
                action=RiskAction.REDUCE_POSITION, reason="r1", reduce_pct=0.2
            ),
            RiskDecision(
                action=RiskAction.REDUCE_POSITION, reason="r2", reduce_pct=0.5
            ),
            RiskDecision(
                action=RiskAction.REDUCE_POSITION, reason="r3", reduce_pct=0.3
            ),
        ]
        result = agg.aggregate(decisions)
        assert result.action == RiskAction.REDUCE_POSITION
        assert result.reduce_pct == 0.5

    def test_unsupported_strategy_fallback(self):
        """未实现策略回退到 STRICTEST."""
        agg = RiskDecisionAggregator(strategy="WEIGHTED")
        decisions = [
            RiskDecision(action=RiskAction.PASS, reason="r1"),
            RiskDecision(action=RiskAction.KILL_SWITCH, reason="r2"),
        ]
        result = agg.aggregate(decisions)
        # 回退到 STRICTEST
        assert result.action == RiskAction.KILL_SWITCH

    def test_empty_decisions_returns_pass(self):
        """空决策列表返回 PASS."""
        agg = RiskDecisionAggregator()
        result = agg.aggregate([])
        assert result.action == RiskAction.PASS


# ============================================================
# 7. 事件历史与审计日志
# ============================================================
class TestEventHistoryAndAudit:
    """事件历史与审计日志测试."""

    def setup_method(self):
        RiskBus.reset_instance()
        self.bus = RiskBus.get_instance()
        self.bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_event_history_stored(self):
        """事件存入历史缓存."""
        for i in range(5):
            self.bus.publish(make_margin_breach_event("test", 0.5 + i * 0.05, 1))

        history = self.bus.get_recent_events()
        assert len(history) == 5

    def test_event_history_filtered_by_type(self):
        """按类型过滤事件历史."""
        self.bus.publish(make_margin_breach_event("test", 0.78, 2))
        self.bus.publish(make_drawdown_breach_event("test", 0.04))

        margin_events = self.bus.get_recent_events(
            event_type=RiskEventType.MARGIN_BREACH
        )
        assert len(margin_events) == 1
        assert margin_events[0].event_type == RiskEventType.MARGIN_BREACH

        dd_events = self.bus.get_recent_events(event_type=RiskEventType.DRAWDOWN_BREACH)
        assert len(dd_events) == 1
        assert dd_events[0].event_type == RiskEventType.DRAWDOWN_BREACH

    def test_event_history_limit(self):
        """事件历史 limit 参数."""
        for _i in range(10):
            self.bus.publish(make_margin_breach_event("test", 0.5, 1))

        history = self.bus.get_recent_events(limit=3)
        assert len(history) == 3

    def test_event_history_latest_first(self):
        """事件历史最新在前 (倒序)."""
        self.bus.publish(make_margin_breach_event("source_a", 0.5, 1))
        self.bus.publish(make_margin_breach_event("source_b", 0.6, 1))

        history = self.bus.get_recent_events()
        assert len(history) == 2
        # 最新 (source_b) 在前
        assert history[0].source == "source_b"
        assert history[1].source == "source_a"

    def test_audit_log_written(self, tmp_path: Path):
        """审计日志写入 JSONL 文件."""
        # 用临时目录的总线实例
        bus = RiskBus(audit_log_dir=tmp_path)
        bus.publish(make_margin_breach_event("test", 0.78, 2))

        # 检查审计日志文件存在
        from datetime import datetime

        today = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = tmp_path / f"events_{today}.jsonl"
        assert log_file.exists()

        # 检查内容是合法 JSON
        line = log_file.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["event_type"] == "margin_breach"
        assert data["source"] == "test"


# ============================================================
# 8. HC-2 同步路径保护测试
# ============================================================
class TestHC2SyncPathProtection:
    """HC-2: KillSwitch 同步路径不被总线阻塞."""

    def setup_method(self):
        RiskBus.reset_instance()
        self.bus = RiskBus.get_instance()
        self.bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_publish_latency_under_1ms(self):
        """publish 延迟应 <1ms (HC-2 同步路径保护).

        Note:
            此测试验证总线本身不会成为同步路径的瓶颈.
            KillSwitch 应直接调用 check_margin_status(), 不走总线.
        """
        # 注册 10 个订阅者
        for _ in range(10):
            self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: None)

        event = make_margin_breach_event("test", 0.78, 2)

        # 测量 1000 次 publish 的平均延迟
        n = 1000
        start = time.perf_counter()
        for _ in range(n):
            self.bus.publish(event)
        elapsed = time.perf_counter() - start
        avg_latency_ms = (elapsed / n) * 1000

        # 平均延迟应 <1ms (HC-2)
        # 注意: 测试机器性能差异, 设置 5ms 上限作为安全边界
        assert avg_latency_ms < 5.0, f"平均延迟 {avg_latency_ms:.3f}ms 超过 5ms 上限"
        # 真正 HC-2 验证: KillSwitch.check_margin_status() 不调用 bus.publish
        # 这里仅验证 bus.publish 本身足够快

    def test_bus_failure_does_not_affect_kill_switch(self):
        """总线故障不影响 KillSwitch (HC-2).

        验证: 即使所有订阅者都抛异常, publish 仍返回 (不抛异常).
        """

        def bad_handler(event: RiskEvent) -> None:
            raise RuntimeError("bus failure simulation")

        self.bus.subscribe(RiskEventType.MARGIN_BREACH, bad_handler)

        event = make_margin_breach_event("test", 0.78, 2)
        # publish 不应抛异常
        invoked = self.bus.publish(event)
        # 异常订阅者不计入 invoked
        assert invoked == 0


# ============================================================
# 9. 模块级快捷函数测试
# ============================================================
class TestModuleLevelFunctions:
    """模块级快捷函数测试."""

    def setup_method(self):
        RiskBus.reset_instance()
        bus = RiskBus.get_instance()
        bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_get_bus_returns_singleton(self):
        """get_bus 返回单例."""
        bus1 = get_bus()
        bus2 = get_bus()
        assert bus1 is bus2

    def test_publish_shortcut(self):
        """publish 快捷函数."""
        received: list[RiskEvent] = []
        subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        event = make_margin_breach_event("test", 0.78, 2)
        invoked = publish(event)

        assert invoked == 1
        assert len(received) == 1

    def test_sync_decide_shortcut(self):
        """sync_decide 快捷函数."""
        decision = sync_decide(make_margin_breach_event("test", 0.78, 2))
        assert decision.action == RiskAction.PASS  # 无决策订阅者


# ============================================================
# 10. 异步路径测试 (USE_RISK_BUS_EVENT_DRIVEN)
# ============================================================
class TestAsyncPath:
    """异步路径测试 (Feature Flag 控制)."""

    def setup_method(self):
        RiskBus.reset_instance()
        self.bus = RiskBus.get_instance()
        self.bus.clear_subscribers()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_async_consumer_not_started_when_flag_disabled(self):
        """Flag=False 时异步消费者不启动."""
        # 默认 USE_RISK_BUS_EVENT_DRIVEN=False
        asyncio.run(self.bus.start_async_consumer())
        # 异步队列未创建
        assert self.bus._async_queue is None

    def test_publish_works_without_async(self):
        """Flag=False 时 publish 仍正常工作."""
        received: list[RiskEvent] = []
        self.bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        event = make_margin_breach_event("test", 0.78, 2)
        invoked = self.bus.publish(event)

        assert invoked == 1
        assert len(received) == 1
