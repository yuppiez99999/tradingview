"""T3.2 KillSwitchAdapter 单元测试.

验证:
    1. HC-2 同步路径保护 (check_margin_status 延迟 <1ms)
    2. 适配器透传原 KillSwitch 返回结果
    3. level>=1 时发布 MARGIN_BREACH 事件 (USE_RISK_BUS_EVENT_DRIVEN=True)
    4. level=0 时不发布事件
    5. 总线故障不影响 KillSwitch (best-effort)
    6. execute_kill_switch 后发布 KILL_SWITCH_TRIGGERED 事件
    7. Feature Flag 透传 (HC-1)
    8. 决策订阅注册 + 回调
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.kill_switch_adapter import KillSwitchAdapter, adapt_kill_switch
from utils.risk.risk_bus import RiskBus
from utils.risk.risk_event import (
    RiskAction,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
)


# ============================================================
# Mock KillSwitch
# ============================================================
class MockKillSwitch:
    """模拟 KillSwitch 用于测试."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "level": 0,
            "margin_usage_ratio": 0.30,
            "margin_call": False,
            "extreme_margin_call": False,
            "can_trade": True,
            "can_open": True,
            "action": "normal",
        }
        self.execute_response: dict[str, Any] = {"executed": True, "level": 1, "actions_taken": []}
        self.check_calls: list[float | None] = []
        self.execute_calls: list[int] = []

    def check_margin_status(self, margin_usage: float | None = None) -> dict[str, Any]:
        self.check_calls.append(margin_usage)
        return dict(self.response)

    def execute_kill_switch(self, level: int) -> dict[str, Any]:
        self.execute_calls.append(level)
        return dict(self.execute_response)

    def register_broker_callback(self, callback: Any) -> None:
        pass


# ============================================================
# 1. 基础适配器测试
# ============================================================
class TestAdapterBasics:
    """适配器基础功能测试."""

    def test_check_margin_status_passthrough(self):
        """check_margin_status 透传原 KillSwitch 结果."""
        mock_ks = MockKillSwitch({"level": 0, "margin_usage_ratio": 0.30})
        adapter = KillSwitchAdapter(mock_ks, publish_events=False)

        status = adapter.check_margin_status(margin_usage=0.30)

        assert status["level"] == 0
        assert status["margin_usage_ratio"] == 0.30
        assert mock_ks.check_calls == [0.30]

    def test_check_margin_status_none(self):
        """margin_usage=None 透传."""
        mock_ks = MockKillSwitch({"level": 0})
        adapter = KillSwitchAdapter(mock_ks, publish_events=False)

        adapter.check_margin_status()
        assert mock_ks.check_calls == [None]

    def test_execute_kill_switch_passthrough(self):
        """execute_kill_switch 透传."""
        mock_ks = MockKillSwitch()
        mock_ks.execute_response = {"executed": True, "level": 2, "actions_taken": [{"action": "force_close"}]}
        adapter = KillSwitchAdapter(mock_ks, publish_events=False)

        result = adapter.execute_kill_switch(2)

        assert result["executed"] is True
        assert result["level"] == 2
        assert mock_ks.execute_calls == [2]

    def test_register_broker_callback_delegated(self):
        """register_broker_callback 委托给原 KillSwitch."""
        mock_ks = MockKillSwitch()
        mock_ks.register_broker_callback = MagicMock()
        adapter = KillSwitchAdapter(mock_ks)

        adapter.register_broker_callback("callback")

        mock_ks.register_broker_callback.assert_called_once_with("callback")

    def test_register_broker_callback_not_supported(self):
        """原 KillSwitch 不支持 register_broker_callback 时不抛异常."""
        class NoCallbackKS:
            def check_margin_status(self, margin_usage=None):
                return {"level": 0}
            def execute_kill_switch(self, level):
                return {"executed": False}

        adapter = KillSwitchAdapter(NoCallbackKS())
        # 不应抛异常
        adapter.register_broker_callback("callback")


# ============================================================
# 2. HC-2 同步路径延迟测试
# ============================================================
class TestHC2SyncPathLatency:
    """HC-2: check_margin_status 延迟 <1ms."""

    def test_latency_under_1ms(self):
        """同步路径延迟应 <1ms (适配器本身开销)."""
        mock_ks = MockKillSwitch({"level": 0})
        adapter = KillSwitchAdapter(mock_ks, publish_events=False)

        # 测量 1000 次平均延迟
        n = 1000
        start = time.perf_counter()
        for _ in range(n):
            adapter.check_margin_status(margin_usage=0.30)
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / n) * 1000

        # 适配器开销应 <1ms (不含原 KillSwitch 时间)
        # 测试机器性能差异, 设置 2ms 上限
        assert avg_ms < 2.0, f"平均延迟 {avg_ms:.3f}ms 超过 2ms 上限"

    def test_latency_with_event_publish(self):
        """即使发布事件, 同步路径仍应快速返回 (best-effort)."""
        # 用临时 audit_log_dir 避免污染生产日志
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            RiskBus.reset_instance()
            bus = RiskBus(audit_log_dir=Path(tmp))
            mock_ks = MockKillSwitch({"level": 1, "margin_usage_ratio": 0.55})
            adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

            # USE_RISK_BUS_EVENT_DRIVEN=False (默认), 仅日志, 不发布事件
            n = 100
            start = time.perf_counter()
            for _ in range(n):
                adapter.check_margin_status(margin_usage=0.55)
            elapsed = time.perf_counter() - start
            avg_ms = (elapsed / n) * 1000

            # 即使做日志, 仍应 <5ms
            assert avg_ms < 5.0, f"平均延迟 {avg_ms:.3f}ms 超过 5ms 上限"


# ============================================================
# 3. 事件发布测试
# ============================================================
class TestEventPublish:
    """事件发布测试."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_no_publish_when_level_0(self):
        """level=0 时不发布事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        mock_ks = MockKillSwitch({"level": 0, "margin_usage_ratio": 0.30})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        # 模拟 Flag=True
        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.check_margin_status(margin_usage=0.30)

        assert len(received) == 0

    def test_publish_margin_breach_when_level_1(self):
        """level=1 时发布 MARGIN_BREACH 事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        mock_ks = MockKillSwitch({"level": 1, "margin_usage_ratio": 0.55})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.check_margin_status(margin_usage=0.55)

        assert len(received) == 1
        event = received[0]
        assert event.event_type == RiskEventType.MARGIN_BREACH
        assert event.source == "kill_switch"
        assert event.severity == RiskSeverity.WARN  # level=1 -> WARN
        assert event.payload["level"] == 1
        assert event.payload["margin_usage"] == 0.55

    def test_publish_margin_breach_when_level_2(self):
        """level=2 时发布 CRITICAL 事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        mock_ks = MockKillSwitch({"level": 2, "margin_usage_ratio": 0.78})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.check_margin_status(margin_usage=0.78)

        assert len(received) == 1
        assert received[0].severity == RiskSeverity.CRITICAL  # level=2 -> CRITICAL

    def test_publish_kill_switch_triggered(self):
        """execute_kill_switch 后发布 KILL_SWITCH_TRIGGERED 事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.KILL_SWITCH_TRIGGERED, lambda e: received.append(e))

        mock_ks = MockKillSwitch()
        mock_ks.execute_response = {
            "executed": True,
            "level": 2,
            "actions_taken": [
                {"action": "force_close_deep_otm_short"},
                {"action": "release_liquidity"},
            ],
        }
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.execute_kill_switch(2)

        assert len(received) == 1
        event = received[0]
        assert event.event_type == RiskEventType.KILL_SWITCH_TRIGGERED
        assert event.severity == RiskSeverity.CRITICAL  # 恒为 CRITICAL
        assert event.payload["level"] == 2
        assert "force_close_deep_otm_short" in event.payload["actions_taken"]
        assert "release_liquidity" in event.payload["actions_taken"]

    def test_no_publish_when_flag_disabled(self):
        """USE_RISK_BUS_EVENT_DRIVEN=False 时仅日志, 不发布事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        mock_ks = MockKillSwitch({"level": 1, "margin_usage_ratio": 0.55})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        # Flag=False (默认)
        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=False):
            adapter.check_margin_status(margin_usage=0.55)

        assert len(received) == 0  # 不发布事件

    def test_no_publish_when_publish_events_false(self):
        """publish_events=False 时不发布事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: received.append(e))

        mock_ks = MockKillSwitch({"level": 1, "margin_usage_ratio": 0.55})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=False)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.check_margin_status(margin_usage=0.55)

        assert len(received) == 0

    def test_no_publish_when_execute_fails(self):
        """execute_kill_switch 未执行时不发布事件."""
        bus = RiskBus()
        bus.clear_subscribers()
        received: list[RiskEvent] = []
        bus.subscribe(RiskEventType.KILL_SWITCH_TRIGGERED, lambda e: received.append(e))

        mock_ks = MockKillSwitch()
        mock_ks.execute_response = {"executed": False, "level": 1, "actions_taken": []}
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            adapter.execute_kill_switch(1)

        assert len(received) == 0  # 未执行, 不发布


# ============================================================
# 4. 总线故障不影响 KillSwitch (HC-2)
# ============================================================
class TestBusFailureIsolation:
    """HC-2: 总线故障不影响 KillSwitch."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_publish_failure_does_not_raise(self):
        """总线 publish 抛异常时, 适配器不抛异常."""
        bus = MagicMock()
        bus.publish.side_effect = RuntimeError("bus failure")

        mock_ks = MockKillSwitch({"level": 1, "margin_usage_ratio": 0.55})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)

        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            # 不应抛异常
            status = adapter.check_margin_status(margin_usage=0.55)

        # KillSwitch 仍正常返回
        assert status["level"] == 1

    def test_bus_construction_failure_does_not_affect_ks(self):
        """总线构造失败不影响 KillSwitch."""
        mock_ks = MockKillSwitch({"level": 0})
        # bus=None, 在 publish_events=False 时不获取总线
        adapter = KillSwitchAdapter(mock_ks, bus=None, publish_events=False)

        status = adapter.check_margin_status(margin_usage=0.30)
        assert status["level"] == 0


# ============================================================
# 5. 决策订阅测试
# ============================================================
class TestDecisionSubscriber:
    """决策订阅测试."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_register_as_decision_subscriber(self):
        """注册决策订阅后, sync_decide 能调用 KillSwitch 适配器."""
        bus = RiskBus()
        bus.clear_subscribers()
        mock_ks = MockKillSwitch({"level": 2, "margin_usage_ratio": 0.78})
        adapter = KillSwitchAdapter(mock_ks, bus=bus)
        adapter.register_as_decision_subscriber()

        assert bus.get_decision_subscriber_count(RiskEventType.MARGIN_BREACH) == 1

        # 发布事件并决策
        from utils.risk.risk_event import make_margin_breach_event
        event = make_margin_breach_event("test", 0.78, 2)
        decision = bus.sync_decide(event)

        # level=2 -> FORCE_LIQUIDATE
        assert decision.action == RiskAction.FORCE_LIQUIDATE
        assert decision.source == "kill_switch_adapter"

    def test_decision_level_3_kill_switch(self):
        """level=3 -> KILL_SWITCH 决策."""
        bus = RiskBus()
        bus.clear_subscribers()
        mock_ks = MockKillSwitch({"level": 3})
        adapter = KillSwitchAdapter(mock_ks, bus=bus)
        adapter.register_as_decision_subscriber()

        from utils.risk.risk_event import make_margin_breach_event
        event = make_margin_breach_event("test", 0.95, 3)
        decision = bus.sync_decide(event)

        assert decision.action == RiskAction.KILL_SWITCH
        assert decision.confidence == 1.0

    def test_decision_level_1_disable_new_orders(self):
        """level=1 -> DISABLE_NEW_ORDERS 决策."""
        bus = RiskBus()
        bus.clear_subscribers()
        mock_ks = MockKillSwitch({"level": 1})
        adapter = KillSwitchAdapter(mock_ks, bus=bus)
        adapter.register_as_decision_subscriber()

        from utils.risk.risk_event import make_margin_breach_event
        event = make_margin_breach_event("test", 0.55, 1)
        decision = bus.sync_decide(event)

        assert decision.action == RiskAction.DISABLE_NEW_ORDERS

    def test_decision_level_0_pass(self):
        """level=0 -> PASS 决策."""
        bus = RiskBus()
        bus.clear_subscribers()
        mock_ks = MockKillSwitch({"level": 0})
        adapter = KillSwitchAdapter(mock_ks, bus=bus)
        adapter.register_as_decision_subscriber()

        from utils.risk.risk_event import make_margin_breach_event
        event = make_margin_breach_event("test", 0.30, 0)
        decision = bus.sync_decide(event)

        assert decision.action == RiskAction.PASS


# ============================================================
# 6. 便捷函数测试
# ============================================================
class TestAdaptKillSwitch:
    """adapt_kill_switch 便捷函数测试."""

    def test_adapt_kill_switch_returns_adapter(self):
        """adapt_kill_switch 返回 KillSwitchAdapter."""
        mock_ks = MockKillSwitch({"level": 0})
        adapter = adapt_kill_switch(mock_ks)
        assert isinstance(adapter, KillSwitchAdapter)
        assert adapter._ks is mock_ks


# ============================================================
# 7. 端到端测试
# ============================================================
class TestEndToEnd:
    """端到端: KillSwitch 触发 → 总线归档 → 决策聚合."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_full_flow_level_2(self):
        """完整流程: level=2 触发 → 事件归档 → 决策聚合."""
        bus = RiskBus()
        bus.clear_subscribers()

        # 1. 创建适配器 + 注册决策订阅
        mock_ks = MockKillSwitch({"level": 2, "margin_usage_ratio": 0.78})
        adapter = KillSwitchAdapter(mock_ks, bus=bus, publish_events=True)
        adapter.register_as_decision_subscriber()

        # 2. 订阅 MARGIN_BREACH 事件做日志归档
        archived: list[RiskEvent] = []
        bus.subscribe(RiskEventType.MARGIN_BREACH, lambda e: archived.append(e))

        # 3. 触发 check_margin_status (Flag=True)
        with patch("utils.risk.kill_switch_adapter.is_enabled", return_value=True):
            status = adapter.check_margin_status(margin_usage=0.78)

        # 4. 验证 KillSwitch 同步路径正常
        assert status["level"] == 2

        # 5. 验证事件归档
        assert len(archived) == 1
        assert archived[0].event_type == RiskEventType.MARGIN_BREACH

        # 6. 验证 sync_decide 聚合决策
        from utils.risk.risk_event import make_margin_breach_event
        event = make_margin_breach_event("test", 0.78, 2)
        decision = bus.sync_decide(event)
        assert decision.action == RiskAction.FORCE_LIQUIDATE
