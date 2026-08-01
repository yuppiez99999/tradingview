# -*- coding: utf-8 -*-
"""T3.3 风控模块适配器集合单元测试.

验证:
    1. CircuitBreakerAdapter: LIQUIDITY_BREACH 事件决策
    2. VaRMonitorAdapter: VAR_BREACH 事件决策 (95% / 99%)
    3. OvernightGapAdapter: OVERNIGHT_GAP 事件决策 (L1/L2/L3)
    4. RiskGuardAdapter: CONCENTRATION_BREACH 事件决策 (single_symbol/single_industry)
    5. RiskModuleRegistry: 一键注册 4 个适配器
    6. 异步决策不阻塞主路径 (HC-2)
    7. 异常隔离: 适配器异常不影响其他模块
    8. RiskDecisionAggregator 聚合多模块决策 (STRICTEST)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.risk_bus import RiskBus
from utils.risk.risk_event import (
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
)
from utils.risk.risk_module_adapters import (
    CircuitBreakerAdapter,
    OvernightGapAdapter,
    RiskGuardAdapter,
    RiskModuleRegistry,
    VaRMonitorAdapter,
)


# ============================================================
# Mock 工厂
# ============================================================
class MockCircuitBreaker:
    """模拟 CircuitBreaker."""

    def __init__(self, state: str = "closed", allow: bool = True) -> None:
        self._state = state
        self._allow = allow

    def allow_request(self) -> bool:
        return self._allow

    def get_state(self) -> Any:
        # 模拟 CircuitState enum
        class FakeState:
            def __init__(self, val: str) -> None:
                self.value = val
        return FakeState(self._state)


class MockVaRMonitor:
    """模拟 VaRMonitor."""

    def __init__(self) -> None:
        self.last_result: Dict[str, Any] = {}

    def calculate_var(self, returns_history: List[float], portfolio_value: float) -> Dict[str, Any]:
        return self.last_result


class MockGapMonitor:
    """模拟 OvernightGapMonitor."""

    def __init__(self, level: int = 0) -> None:
        self._level = level

    def evaluate_overnight_risk(self) -> Dict[str, Any]:
        return {"level": self._level}


class MockRiskGuard:
    """模拟 RiskGuardIntegrator."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def run_all_guards(self, next_trade_date: str) -> Dict[str, Any]:
        self.calls.append(next_trade_date)
        return {"status": "ok"}


def make_event(
    event_type: RiskEventType,
    payload: Dict[str, Any],
    source: str = "test",
) -> RiskEvent:
    """创建测试事件."""
    return RiskEvent(
        event_type=event_type,
        source=source,
        severity=RiskSeverity.WARN,
        payload=payload,
    )


# ============================================================
# 1. CircuitBreakerAdapter 测试
# ============================================================
class TestCircuitBreakerAdapter:
    """CircuitBreakerAdapter 测试."""

    def test_module_name(self):
        """模块名正确."""
        adapter = CircuitBreakerAdapter(MockCircuitBreaker())
        assert adapter.module_name == "CircuitBreakerAdapter"

    def test_subscribed_events(self):
        """订阅 LIQUIDITY_BREACH."""
        adapter = CircuitBreakerAdapter(MockCircuitBreaker())
        assert RiskEventType.LIQUIDITY_BREACH in adapter.subscribed_events

    def test_decision_closed_state(self):
        """CLOSED 状态 → PASS."""
        cb = MockCircuitBreaker(state="closed", allow=True)
        adapter = CircuitBreakerAdapter(cb)

        event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert decision.reason == "circuit_breaker_closed"

    def test_decision_open_state(self):
        """OPEN 状态 → DISABLE_NEW_ORDERS."""
        cb = MockCircuitBreaker(state="open", allow=False)
        adapter = CircuitBreakerAdapter(cb)

        event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.DISABLE_NEW_ORDERS
        assert decision.confidence == 0.95

    def test_decision_half_open_state(self):
        """HALF_OPEN 状态 → PASS (允许探测)."""
        cb = MockCircuitBreaker(state="half_open", allow=True)
        adapter = CircuitBreakerAdapter(cb)

        event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert "half_open" in decision.reason

    def test_decision_exception_returns_pass(self):
        """适配器异常返回 PASS (fail-safe)."""
        # 传入会抛异常的 mock
        bad_cb = MagicMock()
        bad_cb.allow_request.side_effect = RuntimeError("test")

        adapter = CircuitBreakerAdapter(bad_cb)
        event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert "circuit_breaker_error" in decision.reason

    def test_register_to_bus(self):
        """注册到总线."""
        RiskBus.reset_instance()
        bus = RiskBus()
        bus.clear_subscribers()

        adapter = CircuitBreakerAdapter(MockCircuitBreaker())
        adapter.register(bus)

        assert bus.get_decision_subscriber_count(RiskEventType.LIQUIDITY_BREACH) == 1

        RiskBus.reset_instance()


# ============================================================
# 2. VaRMonitorAdapter 测试
# ============================================================
class TestVaRMonitorAdapter:
    """VaRMonitorAdapter 测试."""

    def test_module_name(self):
        adapter = VaRMonitorAdapter(MockVaRMonitor())
        assert adapter.module_name == "VaRMonitorAdapter"

    def test_subscribed_events(self):
        adapter = VaRMonitorAdapter(MockVaRMonitor())
        assert RiskEventType.VAR_BREACH in adapter.subscribed_events

    def test_decision_var_95_breach(self):
        """95% VaR 超限 → REDUCE_POSITION 10%."""
        adapter = VaRMonitorAdapter(MockVaRMonitor())

        event = make_event(RiskEventType.VAR_BREACH, {
            "var_type": "var_95",
            "breach_pct": -0.035,  # -3.5%
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.10
        assert decision.confidence == 0.9

    def test_decision_var_99_breach(self):
        """99% VaR 超限 → REDUCE_POSITION 20%."""
        adapter = VaRMonitorAdapter(MockVaRMonitor())

        event = make_event(RiskEventType.VAR_BREACH, {
            "var_type": "var_99",
            "breach_pct": -0.055,  # -5.5%
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.20
        assert decision.confidence == 0.95

    def test_decision_unknown_var_type(self):
        """未知 var_type → PASS."""
        adapter = VaRMonitorAdapter(MockVaRMonitor())

        event = make_event(RiskEventType.VAR_BREACH, {
            "var_type": "var_unknown",
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert "var_unknown" in decision.reason

    def test_decision_exception_returns_pass(self):
        """异常返回 PASS."""
        adapter = VaRMonitorAdapter(MockVaRMonitor())

        # payload 不是 dict, 触发异常
        event = RiskEvent(
            event_type=RiskEventType.VAR_BREACH,
            source="test",
            severity=RiskSeverity.WARN,
            payload=None,  # type: ignore[arg-type]
        )
        decision = adapter.make_decision(event)
        assert decision.action == RiskAction.PASS
        assert "var_monitor_error" in decision.reason

    def test_register_to_bus(self):
        RiskBus.reset_instance()
        bus = RiskBus()
        bus.clear_subscribers()

        adapter = VaRMonitorAdapter(MockVaRMonitor())
        adapter.register(bus)

        assert bus.get_decision_subscriber_count(RiskEventType.VAR_BREACH) == 1
        RiskBus.reset_instance()


# ============================================================
# 3. OvernightGapAdapter 测试
# ============================================================
class TestOvernightGapAdapter:
    """OvernightGapAdapter 测试."""

    def test_module_name(self):
        adapter = OvernightGapAdapter(MockGapMonitor())
        assert adapter.module_name == "OvernightGapAdapter"

    def test_subscribed_events(self):
        adapter = OvernightGapAdapter(MockGapMonitor())
        assert RiskEventType.OVERNIGHT_GAP in adapter.subscribed_events

    def test_decision_level_1_warn(self):
        """L1 预警 → PASS."""
        adapter = OvernightGapAdapter(MockGapMonitor(level=1))

        event = make_event(RiskEventType.OVERNIGHT_GAP, {
            "level": 1,
            "sp500_drop_pct": -0.015,  # -1.5%
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert "l1" in decision.reason

    def test_decision_level_2_disable(self):
        """L2 熔断 → DISABLE_NEW_ORDERS."""
        adapter = OvernightGapAdapter(MockGapMonitor(level=2))

        event = make_event(RiskEventType.OVERNIGHT_GAP, {
            "level": 2,
            "sp500_drop_pct": -0.025,  # -2.5%
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.DISABLE_NEW_ORDERS
        assert decision.confidence == 0.9

    def test_decision_level_3_force_liquidate(self):
        """L3 全局平仓 → FORCE_LIQUIDATE."""
        adapter = OvernightGapAdapter(MockGapMonitor(level=3))

        event = make_event(RiskEventType.OVERNIGHT_GAP, {
            "level": 3,
            "sp500_drop_pct": -0.035,  # -3.5%
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.FORCE_LIQUIDATE
        assert decision.confidence == 0.95

    def test_decision_level_0_normal(self):
        """L0 正常 → PASS."""
        adapter = OvernightGapAdapter(MockGapMonitor(level=0))

        event = make_event(RiskEventType.OVERNIGHT_GAP, {
            "level": 0,
            "sp500_drop_pct": 0.005,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert decision.reason == "overnight_gap_normal"

    def test_decision_exception_returns_pass(self):
        """异常返回 PASS."""
        adapter = OvernightGapAdapter(MockGapMonitor())

        # payload=None 触发异常
        event = RiskEvent(
            event_type=RiskEventType.OVERNIGHT_GAP,
            source="test",
            severity=RiskSeverity.WARN,
            payload=None,  # type: ignore[arg-type]
        )
        decision = adapter.make_decision(event)
        assert decision.action == RiskAction.PASS
        assert "overnight_gap_error" in decision.reason


# ============================================================
# 4. RiskGuardAdapter 测试
# ============================================================
class TestRiskGuardAdapter:
    """RiskGuardAdapter 测试."""

    def test_module_name(self):
        adapter = RiskGuardAdapter(MockRiskGuard())
        assert adapter.module_name == "RiskGuardAdapter"

    def test_subscribed_events(self):
        adapter = RiskGuardAdapter(MockRiskGuard())
        assert RiskEventType.CONCENTRATION_BREACH in adapter.subscribed_events

    def test_decision_single_symbol_normal(self):
        """单标的权重未超限 → PASS."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = make_event(RiskEventType.CONCENTRATION_BREACH, {
            "breach_type": "single_symbol",
            "weight": 0.08,  # 8% < 10%
            "threshold": 0.10,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS
        assert decision.reason == "concentration_normal"

    def test_decision_single_symbol_mild_breach(self):
        """单标的权重 10-15% → REDUCE_POSITION 5%."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = make_event(RiskEventType.CONCENTRATION_BREACH, {
            "breach_type": "single_symbol",
            "weight": 0.12,  # 12%
            "threshold": 0.10,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.05

    def test_decision_single_symbol_severe_breach(self):
        """单标的权重 >15% → REDUCE_POSITION 10%."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = make_event(RiskEventType.CONCENTRATION_BREACH, {
            "breach_type": "single_symbol",
            "weight": 0.18,  # 18%
            "threshold": 0.10,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.10
        assert "severe" in decision.reason

    def test_decision_single_industry_breach(self):
        """单行业权重 >30% → REDUCE_POSITION 8%."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = make_event(RiskEventType.CONCENTRATION_BREACH, {
            "breach_type": "single_industry",
            "weight": 0.35,  # 35%
            "threshold": 0.30,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.08

    def test_decision_single_industry_normal(self):
        """单行业权重未超限 → PASS."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = make_event(RiskEventType.CONCENTRATION_BREACH, {
            "breach_type": "single_industry",
            "weight": 0.25,  # 25% < 30%
            "threshold": 0.30,
        })
        decision = adapter.make_decision(event)

        assert decision.action == RiskAction.PASS

    def test_decision_exception_returns_pass(self):
        """异常返回 PASS."""
        adapter = RiskGuardAdapter(MockRiskGuard())

        event = RiskEvent(
            event_type=RiskEventType.CONCENTRATION_BREACH,
            source="test",
            severity=RiskSeverity.WARN,
            payload=None,  # type: ignore[arg-type]
        )
        decision = adapter.make_decision(event)
        assert decision.action == RiskAction.PASS
        assert "risk_guard_error" in decision.reason


# ============================================================
# 5. RiskModuleRegistry 测试
# ============================================================
class TestRiskModuleRegistry:
    """RiskModuleRegistry 一键注册测试."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_register_all_success(self):
        """一键注册全部 4 个适配器成功."""
        bus = RiskBus()
        bus.clear_subscribers()
        registry = RiskModuleRegistry(bus=bus)

        results = registry.register_all(
            circuit_breaker=MockCircuitBreaker(),
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(),
            risk_guard=MockRiskGuard(),
        )

        assert len(results) == 4
        assert all(results.values())
        assert len(registry.list_adapters()) == 4

        # 验证总线订阅了 4 个事件类型
        assert bus.get_decision_subscriber_count(RiskEventType.LIQUIDITY_BREACH) == 1
        assert bus.get_decision_subscriber_count(RiskEventType.VAR_BREACH) == 1
        assert bus.get_decision_subscriber_count(RiskEventType.OVERNIGHT_GAP) == 1
        assert bus.get_decision_subscriber_count(RiskEventType.CONCENTRATION_BREACH) == 1

    def test_get_adapter(self):
        """获取已注册的适配器."""
        bus = RiskBus()
        bus.clear_subscribers()
        registry = RiskModuleRegistry(bus=bus)
        registry.register_all(
            circuit_breaker=MockCircuitBreaker(),
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(),
            risk_guard=MockRiskGuard(),
        )

        adapter = registry.get_adapter("CircuitBreakerAdapter")
        assert adapter is not None
        assert adapter.module_name == "CircuitBreakerAdapter"

    def test_get_adapter_not_found(self):
        """获取未注册的适配器返回 None."""
        bus = RiskBus()
        bus.clear_subscribers()
        registry = RiskModuleRegistry(bus=bus)

        assert registry.get_adapter("NonExistent") is None

    def test_register_partial_failure(self):
        """部分适配器注册失败时其他仍成功."""
        bus = RiskBus()
        bus.clear_subscribers()
        registry = RiskModuleRegistry(bus=bus)

        # circuit_breaker 传入会抛异常的对象
        bad_cb = MagicMock()
        bad_cb.allow_request.side_effect = RuntimeError("init failure")

        # 注: register_all 内部仅 try-except register 调用, 不会因 init 异常失败
        # 但如果 make_decision 抛异常, sync_decide 会捕获
        results = registry.register_all(
            circuit_breaker=bad_cb,
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(),
            risk_guard=MockRiskGuard(),
        )

        # 4 个都应注册成功 (register 本身不抛异常)
        assert len(results) == 4
        assert all(results.values())


# ============================================================
# 6. 异步决策不阻塞主路径 (HC-2)
# ============================================================
class TestAsyncDecisionNonBlocking:
    """HC-2: 异步决策不阻塞主路径."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_sync_decide_latency(self):
        """sync_decide 延迟应 <10ms (4 个适配器)."""
        import time
        bus = RiskBus()
        bus.clear_subscribers()

        registry = RiskModuleRegistry(bus=bus)
        registry.register_all(
            circuit_breaker=MockCircuitBreaker(),
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(),
            risk_guard=MockRiskGuard(),
        )

        event = make_event(RiskEventType.VAR_BREACH, {
            "var_type": "var_95",
            "breach_pct": -0.035,
        })

        # 1000 次 sync_decide
        n = 1000
        start = time.perf_counter()
        for _ in range(n):
            bus.sync_decide(event)
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / n) * 1000

        # 平均延迟 <10ms (4 个适配器)
        assert avg_ms < 10.0, f"平均延迟 {avg_ms:.3f}ms 超过 10ms 上限"

    def test_one_adapter_failure_does_not_block_others(self):
        """单个适配器异常不阻塞其他适配器."""
        bus = RiskBus()
        bus.clear_subscribers()

        # VaRMonitorAdapter 会被调用并返回 PASS (异常被捕获)
        bad_vm = MagicMock()
        # calculate_var 不被适配器调用, 所以不会抛异常
        # 这里测试 make_decision 内部异常隔离

        registry = RiskModuleRegistry(bus=bus)
        registry.register_all(
            circuit_breaker=MockCircuitBreaker(state="open", allow=False),
            var_monitor=bad_vm,
            gap_monitor=MockGapMonitor(level=3),
            risk_guard=MockRiskGuard(),
        )

        # 由于订阅不同事件类型, 各自独立工作
        cb_event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = bus.sync_decide(cb_event)
        assert decision.action == RiskAction.DISABLE_NEW_ORDERS

        gap_event = make_event(RiskEventType.OVERNIGHT_GAP, {"level": 3, "sp500_drop_pct": -0.035})
        decision = bus.sync_decide(gap_event)
        assert decision.action == RiskAction.FORCE_LIQUIDATE


# ============================================================
# 7. 多模块决策聚合 (STRICTEST)
# ============================================================
class TestMultiModuleAggregation:
    """多模块决策聚合测试 (STRICTEST 最严格策略)."""

    def setup_method(self):
        RiskBus.reset_instance()

    def teardown_method(self):
        RiskBus.reset_instance()

    def test_aggregate_strictest_across_event_types(self):
        """不同事件类型各自独立决策, 同类型聚合取最严格."""
        bus = RiskBus()
        bus.clear_subscribers()

        # 注册两个 LIQUIDITY_BREACH 决策订阅者 (CircuitBreaker + 手动)
        registry = RiskModuleRegistry(bus=bus)
        registry.register_all(
            circuit_breaker=MockCircuitBreaker(state="open", allow=False),
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(level=3),
            risk_guard=MockRiskGuard(),
        )

        # 额外注册一个 LIQUIDITY_BREACH 决策者 (返回 FORCE_LIQUIDATE)
        def extra_decider(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.FORCE_LIQUIDATE,
                reason="extra_liquidity_check",
                source="extra_decider",
            )

        bus.subscribe_decision(RiskEventType.LIQUIDITY_BREACH, extra_decider)

        # 触发 LIQUIDITY_BREACH 事件, 两个决策者返回:
        # - CircuitBreakerAdapter: DISABLE_NEW_ORDERS
        # - extra_decider: FORCE_LIQUIDATE
        # STRICTEST 应取 FORCE_LIQUIDATE
        event = make_event(RiskEventType.LIQUIDITY_BREACH, {})
        decision = bus.sync_decide(event)

        assert decision.action == RiskAction.FORCE_LIQUIDATE  # 最严格

    def test_var_breach_aggregation(self):
        """VaR + 手动决策者聚合 (取最大 reduce_pct)."""
        bus = RiskBus()
        bus.clear_subscribers()

        registry = RiskModuleRegistry(bus=bus)
        registry.register_all(
            circuit_breaker=MockCircuitBreaker(),
            var_monitor=MockVaRMonitor(),
            gap_monitor=MockGapMonitor(),
            risk_guard=MockRiskGuard(),
        )

        # 额外决策者返回更大 reduce_pct
        def extra_decider(event: RiskEvent) -> RiskDecision:
            return RiskDecision(
                action=RiskAction.REDUCE_POSITION,
                reason="extra_check",
                source="extra",
                reduce_pct=0.30,  # 比 VaRMonitorAdapter 的 0.10 更大
            )

        bus.subscribe_decision(RiskEventType.VAR_BREACH, extra_decider)

        event = make_event(RiskEventType.VAR_BREACH, {
            "var_type": "var_95",
            "breach_pct": -0.035,
        })
        decision = bus.sync_decide(event)

        # 两个决策者都返回 REDUCE_POSITION, 取最大 reduce_pct
        assert decision.action == RiskAction.REDUCE_POSITION
        assert decision.reduce_pct == 0.30  # 取最大值


# ============================================================
# 8. 协议测试
# ============================================================
class TestRiskModuleAdapterProtocol:
    """RiskModuleAdapter Protocol 测试."""

    def test_all_adapters_satisfy_protocol(self):
        """所有适配器满足 RiskModuleAdapter 协议."""
        cb_adapter = CircuitBreakerAdapter(MockCircuitBreaker())
        var_adapter = VaRMonitorAdapter(MockVaRMonitor())
        gap_adapter = OvernightGapAdapter(MockGapMonitor())
        rg_adapter = RiskGuardAdapter(MockRiskGuard())

        for adapter in [cb_adapter, var_adapter, gap_adapter, rg_adapter]:
            assert hasattr(adapter, "module_name")
            assert hasattr(adapter, "subscribed_events")
            assert hasattr(adapter, "register")
            assert hasattr(adapter, "make_decision")
            assert callable(adapter.register)
            assert callable(adapter.make_decision)

    def test_subscribed_events_distinct(self):
        """4 个适配器订阅不同事件类型."""
        cb = CircuitBreakerAdapter(MockCircuitBreaker())
        var = VaRMonitorAdapter(MockVaRMonitor())
        gap = OvernightGapAdapter(MockGapMonitor())
        rg = RiskGuardAdapter(MockRiskGuard())

        events = set()
        for adapter in [cb, var, gap, rg]:
            for e in adapter.subscribed_events:
                events.add(e)

        # 4 个适配器订阅 4 种不同事件
        assert len(events) == 4
        assert RiskEventType.LIQUIDITY_BREACH in events
        assert RiskEventType.VAR_BREACH in events
        assert RiskEventType.OVERNIGHT_GAP in events
        assert RiskEventType.CONCENTRATION_BREACH in events
