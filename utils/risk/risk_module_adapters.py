"""风控模块适配器集合 — 模块整合 8.4 (T3.3).

任务: T3.3
责任层: L5 风控
依赖: T3.1 (risk_bus) + T3.2 (kill_switch_adapter)

适配 4 个已有风控模块到事件总线:
    1. CircuitBreaker (v8.3_institutional/src/risk/circuit_breaker.py)
       - 订阅 LIQUIDITY_BREACH 事件
       - 决策: 熔断 OPEN 时 DISABLE_NEW_ORDERS
    2. VaRMonitor (utils/var_monitor.py)
       - 订阅 VAR_BREACH 事件
       - 决策: 95% VaR 超限 → REDUCE_POSITION 10%, 99% VaR 超限 → REDUCE_POSITION 20%
    3. OvernightGapMonitor (utils/overnight_gap_monitor.py)
       - 订阅 OVERNIGHT_GAP 事件
       - 决策: L1 预警 → PASS, L2 熔断 → DISABLE_NEW_ORDERS, L3 全局平仓 → FORCE_LIQUIDATE
    4. RiskGuardIntegrator (utils/risk_guard_integrator.py)
       - 订阅 CONCENTRATION_BREACH 事件
       - 决策: 单标的/单行业权重超限 → REDUCE_POSITION

设计原则:
    1. 适配器模式 (HC-2): 不修改原模块, 仅包装决策订阅
    2. 异步决策不阻塞主路径: 原模块同步调用不变, 适配器仅响应总线事件
    3. RiskDecisionAggregator 聚合: 多模块决策取最严格 (STRICTEST)
    4. Feature Flag 透传 (HC-1): Flag=False 时仅注册但不发布事件

硬约束:
    - HC-2: 原模块同步路径不变, 适配器仅做事件订阅
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from utils.infra.feature_flags import is_enabled
from utils.risk.risk_bus import RiskBus, get_bus
from utils.risk.risk_event import (
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
)

logger = logging.getLogger("risk_module_adapters")

FLAG_NAME = "USE_RISK_BUS_EVENT_DRIVEN"


# ============================================================
# 适配器协议
# ============================================================
class RiskModuleAdapter(Protocol):
    """风控模块适配器协议."""

    module_name: str
    subscribed_events: list[RiskEventType]

    def register(self, bus: RiskBus | None = None) -> None:
        """注册到总线 (订阅事件 + 决策订阅)."""
        ...

    def make_decision(self, event: RiskEvent) -> RiskDecision:
        """基于事件生成决策."""
        ...


# ============================================================
# 1. CircuitBreakerAdapter
# ============================================================
class CircuitBreakerAdapter:
    """CircuitBreaker 适配器.

    订阅 LIQUIDITY_BREACH 事件, 当流动性突破时检查熔断状态:
        - CircuitState.OPEN → DISABLE_NEW_ORDERS (禁止新请求)
        - CircuitState.HALF_OPEN → PASS (允许探测)
        - CircuitState.CLOSED → PASS (正常)
    """

    module_name = "CircuitBreakerAdapter"
    subscribed_events = [RiskEventType.LIQUIDITY_BREACH]

    def __init__(self, circuit_breaker: Any) -> None:
        """初始化.

        Args:
            circuit_breaker: CircuitBreaker 实例 (需有 allow_request() 方法)
        """
        self._cb = circuit_breaker

    def register(self, bus: RiskBus | None = None) -> None:
        """注册到总线."""
        bus = bus or get_bus()
        bus.subscribe_decision(RiskEventType.LIQUIDITY_BREACH, self.make_decision)
        logger.info("[%s] 已注册 LIQUIDITY_BREACH 决策订阅", self.module_name)

    def make_decision(self, event: RiskEvent) -> RiskDecision:
        """基于熔断状态生成决策."""
        try:
            # 检查熔断器是否允许请求
            allowed = self._cb.allow_request() if hasattr(self._cb, "allow_request") else True

            if not allowed:
                # 熔断 OPEN, 禁止新请求
                return RiskDecision(
                    action=RiskAction.DISABLE_NEW_ORDERS,
                    reason="circuit_breaker_open",
                    confidence=0.95,
                    source=self.module_name,
                )

            # 检查是否处于半开状态
            state = self._cb.get_state() if hasattr(self._cb, "get_state") else None
            if state and hasattr(state, "value") and state.value == "half_open":
                return RiskDecision(
                    action=RiskAction.PASS,
                    reason="circuit_breaker_half_open_probing",
                    confidence=0.5,
                    source=self.module_name,
                )

            return RiskDecision(
                action=RiskAction.PASS,
                reason="circuit_breaker_closed",
                confidence=0.9,
                source=self.module_name,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("[%s] 决策异常: %s", self.module_name, e, exc_info=True)
            return RiskDecision(
                action=RiskAction.PASS,
                reason=f"circuit_breaker_error: {e}",
                confidence=0.0,
                source=self.module_name,
            )


# ============================================================
# 2. VaRMonitorAdapter
# ============================================================
class VaRMonitorAdapter:
    """VaRMonitor 适配器.

    订阅 VAR_BREACH 事件, 基于 VaR 超限级别生成决策:
        - var_95_breach (95% VaR < -3%) → REDUCE_POSITION 10%
        - var_99_breach (99% VaR < -5%) → REDUCE_POSITION 20%
    """

    module_name = "VaRMonitorAdapter"
    subscribed_events = [RiskEventType.VAR_BREACH]

    def __init__(self, var_monitor: Any) -> None:
        """初始化.

        Args:
            var_monitor: VaRMonitor 实例 (可选, 仅用于调用 calculate_var)
        """
        self._vm = var_monitor

    def register(self, bus: RiskBus | None = None) -> None:
        """注册到总线."""
        bus = bus or get_bus()
        bus.subscribe_decision(RiskEventType.VAR_BREACH, self.make_decision)
        logger.info("[%s] 已注册 VAR_BREACH 决策订阅", self.module_name)

    def make_decision(self, event: RiskEvent) -> RiskDecision:
        """基于 VaR 超限生成决策."""
        try:
            var_type = event.payload.get("var_type", "var_95")
            breach_pct = float(event.payload.get("breach_pct", 0.0))

            if var_type == "var_99":
                # 99% VaR 超限 → 减仓 20%
                return RiskDecision(
                    action=RiskAction.REDUCE_POSITION,
                    reason=f"var_99_breach_{breach_pct:.2%}",
                    confidence=0.95,
                    source=self.module_name,
                    reduce_pct=0.20,
                )
            elif var_type == "var_95":
                # 95% VaR 超限 → 减仓 10%
                return RiskDecision(
                    action=RiskAction.REDUCE_POSITION,
                    reason=f"var_95_breach_{breach_pct:.2%}",
                    confidence=0.9,
                    source=self.module_name,
                    reduce_pct=0.10,
                )

            return RiskDecision(
                action=RiskAction.PASS,
                reason=f"var_unknown_type_{var_type}",
                confidence=0.5,
                source=self.module_name,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("[%s] 决策异常: %s", self.module_name, e, exc_info=True)
            return RiskDecision(
                action=RiskAction.PASS,
                reason=f"var_monitor_error: {e}",
                confidence=0.0,
                source=self.module_name,
            )


# ============================================================
# 3. OvernightGapAdapter
# ============================================================
class OvernightGapAdapter:
    """OvernightGapMonitor 适配器.

    订阅 OVERNIGHT_GAP 事件, 基于外盘隔夜风险级别生成决策:
        - L1 预警 (S&P500 跌幅 >= 1%) → PASS (仅观察)
        - L2 熔断 (S&P500 跌幅 >= 2%) → DISABLE_NEW_ORDERS (禁止开仓)
        - L3 全局平仓 (S&P500 跌幅 >= 3%) → FORCE_LIQUIDATE (全局平仓)
    """

    module_name = "OvernightGapAdapter"
    subscribed_events = [RiskEventType.OVERNIGHT_GAP]

    def __init__(self, gap_monitor: Any) -> None:
        """初始化.

        Args:
            gap_monitor: OvernightGapMonitor 实例 (可选)
        """
        self._ogm = gap_monitor

    def register(self, bus: RiskBus | None = None) -> None:
        """注册到总线."""
        bus = bus or get_bus()
        bus.subscribe_decision(RiskEventType.OVERNIGHT_GAP, self.make_decision)
        logger.info("[%s] 已注册 OVERNIGHT_GAP 决策订阅", self.module_name)

    def make_decision(self, event: RiskEvent) -> RiskDecision:
        """基于隔夜跳空级别生成决策."""
        try:
            level = int(event.payload.get("level", 0))
            sp500_drop = float(event.payload.get("sp500_drop_pct", 0.0))

            if level >= 3:
                # L3 全局平仓
                return RiskDecision(
                    action=RiskAction.FORCE_LIQUIDATE,
                    reason=f"overnight_gap_l3_sp500_{sp500_drop:.2%}",
                    confidence=0.95,
                    source=self.module_name,
                )
            elif level == 2:
                # L2 禁止开仓
                return RiskDecision(
                    action=RiskAction.DISABLE_NEW_ORDERS,
                    reason=f"overnight_gap_l2_sp500_{sp500_drop:.2%}",
                    confidence=0.9,
                    source=self.module_name,
                )
            elif level == 1:
                # L1 预警 (仅观察)
                return RiskDecision(
                    action=RiskAction.PASS,
                    reason=f"overnight_gap_l1_sp500_{sp500_drop:.2%}",
                    confidence=0.7,
                    source=self.module_name,
                )

            return RiskDecision(
                action=RiskAction.PASS,
                reason="overnight_gap_normal",
                confidence=0.9,
                source=self.module_name,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("[%s] 决策异常: %s", self.module_name, e, exc_info=True)
            return RiskDecision(
                action=RiskAction.PASS,
                reason=f"overnight_gap_error: {e}",
                confidence=0.0,
                source=self.module_name,
            )


# ============================================================
# 4. RiskGuardAdapter
# ============================================================
class RiskGuardAdapter:
    """RiskGuardIntegrator 适配器.

    订阅 CONCENTRATION_BREACH 事件, 基于集中度超限生成决策:
        - 单标的权重 > 10% → REDUCE_POSITION 5%
        - 单标的权重 > 15% → REDUCE_POSITION 10%
        - 单行业权重 > 30% → REDUCE_POSITION 8%
    """

    module_name = "RiskGuardAdapter"
    subscribed_events = [RiskEventType.CONCENTRATION_BREACH]

    def __init__(self, risk_guard: Any) -> None:
        """初始化.

        Args:
            risk_guard: RiskGuardIntegrator 实例 (可选)
        """
        self._rg = risk_guard

    def register(self, bus: RiskBus | None = None) -> None:
        """注册到总线."""
        bus = bus or get_bus()
        bus.subscribe_decision(RiskEventType.CONCENTRATION_BREACH, self.make_decision)
        logger.info("[%s] 已注册 CONCENTRATION_BREACH 决策订阅", self.module_name)

    def make_decision(self, event: RiskEvent) -> RiskDecision:
        """基于集中度超限生成决策."""
        try:
            breach_type = event.payload.get("breach_type", "single_symbol")
            weight = float(event.payload.get("weight", 0.0))
            threshold = float(event.payload.get("threshold", 0.10))

            if breach_type == "single_symbol":
                if weight > 0.15:
                    # 严重超限 → 减仓 10%
                    return RiskDecision(
                        action=RiskAction.REDUCE_POSITION,
                        reason=f"concentration_single_{weight:.2%}_severe",
                        confidence=0.95,
                        source=self.module_name,
                        reduce_pct=0.10,
                    )
                elif weight > threshold:
                    # 一般超限 → 减仓 5%
                    return RiskDecision(
                        action=RiskAction.REDUCE_POSITION,
                        reason=f"concentration_single_{weight:.2%}",
                        confidence=0.9,
                        source=self.module_name,
                        reduce_pct=0.05,
                    )
            elif breach_type == "single_industry":
                if weight > 0.30:
                    return RiskDecision(
                        action=RiskAction.REDUCE_POSITION,
                        reason=f"concentration_industry_{weight:.2%}",
                        confidence=0.9,
                        source=self.module_name,
                        reduce_pct=0.08,
                    )

            return RiskDecision(
                action=RiskAction.PASS,
                reason="concentration_normal",
                confidence=0.9,
                source=self.module_name,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("[%s] 决策异常: %s", self.module_name, e, exc_info=True)
            return RiskDecision(
                action=RiskAction.PASS,
                reason=f"risk_guard_error: {e}",
                confidence=0.0,
                source=self.module_name,
            )


# ============================================================
# 5. 注册器: 一键注册全部 4 个适配器
# ============================================================
class RiskModuleRegistry:
    """风控模块注册器 — 一键注册全部 4 个适配器.

    Usage:
        >>> from utils.risk.risk_module_adapters import RiskModuleRegistry
        >>> registry = RiskModuleRegistry()
        >>> registry.register_all(
        ...     circuit_breaker=cb,
        ...     var_monitor=vm,
        ...     gap_monitor=ogm,
        ...     risk_guard=rg,
        ... )
    """

    def __init__(self, bus: RiskBus | None = None) -> None:
        """初始化.

        Args:
            bus: 可选的总线实例 (None 时使用默认单例)
        """
        self._bus = bus
        self._adapters: dict[str, RiskModuleAdapter] = {}

    @property
    def bus(self) -> RiskBus:
        """获取总线实例."""
        return self._bus or get_bus()

    def register_all(
        self,
        circuit_breaker: Any,
        var_monitor: Any,
        gap_monitor: Any,
        risk_guard: Any,
    ) -> dict[str, bool]:
        """一键注册全部 4 个适配器.

        Args:
            circuit_breaker: CircuitBreaker 实例
            var_monitor: VaRMonitor 实例
            gap_monitor: OvernightGapMonitor 实例
            risk_guard: RiskGuardIntegrator 实例

        Returns:
            {module_name: success} 注册结果
        """
        results: dict[str, bool] = {}
        bus = self.bus

        # 显式声明 Protocol 类型, 避免后续赋值触发 "Incompatible types in assignment"
        adapter: RiskModuleAdapter

        # 1. CircuitBreaker
        try:
            adapter = CircuitBreakerAdapter(circuit_breaker)
            adapter.register(bus)
            self._adapters[adapter.module_name] = adapter
            results[adapter.module_name] = True
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("CircuitBreakerAdapter 注册失败: %s", e, exc_info=True)
            results["CircuitBreakerAdapter"] = False

        # 2. VaRMonitor
        try:
            adapter = VaRMonitorAdapter(var_monitor)
            adapter.register(bus)
            self._adapters[adapter.module_name] = adapter
            results[adapter.module_name] = True
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("VaRMonitorAdapter 注册失败: %s", e, exc_info=True)
            results["VaRMonitorAdapter"] = False

        # 3. OvernightGapMonitor
        try:
            adapter = OvernightGapAdapter(gap_monitor)
            adapter.register(bus)
            self._adapters[adapter.module_name] = adapter
            results[adapter.module_name] = True
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("OvernightGapAdapter 注册失败: %s", e, exc_info=True)
            results["OvernightGapAdapter"] = False

        # 4. RiskGuard
        try:
            adapter = RiskGuardAdapter(risk_guard)
            adapter.register(bus)
            self._adapters[adapter.module_name] = adapter
            results[adapter.module_name] = True
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # 风险隔离边界: 任何风险模块异常不得阻断主链路
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # ZeroDivisionError/OverflowError — 数值计算异常
            # OSError — 文件/网络 IO 异常
            logger.error("RiskGuardAdapter 注册失败: %s", e, exc_info=True)
            results["RiskGuardAdapter"] = False

        logger.info(
            "[RiskModuleRegistry] 注册完成 | 成功=%d/4 | flag=%s",
            sum(1 for v in results.values() if v),
            is_enabled(FLAG_NAME),
        )
        return results

    def get_adapter(self, name: str) -> RiskModuleAdapter | None:
        """获取已注册的适配器."""
        return self._adapters.get(name)

    def list_adapters(self) -> list[str]:
        """列出已注册的适配器名称."""
        return list(self._adapters.keys())


__all__ = [
    "CircuitBreakerAdapter",
    "OvernightGapAdapter",
    "RiskGuardAdapter",
    "RiskModuleAdapter",
    "RiskModuleRegistry",
    "VaRMonitorAdapter",
]
