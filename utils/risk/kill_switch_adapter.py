# -*- coding: utf-8 -*-
"""KillSwitch 总线适配器 — 模块整合 8.4 (T3.2).

任务: T3.2
责任层: L5 风控
依赖: T3.1 (risk_bus)

设计原则 (HC-2 铁律):
    1. KillSwitch 同步直调路径不变, 延迟 <1ms
       - 适配器包装 KillSwitch, 不修改其内部逻辑
       - check_margin_status() 仍走原同步路径, 不走总线
    2. 总线订阅做日志归档 (发布 KILL_SWITCH_TRIGGERED 事件)
       - 仅在 KillSwitch 触发 level>=1 后发布归档事件
       - 归档事件不影响同步路径, 失败不抛异常
    3. 总线故障不影响 KillSwitch
       - 适配器在 try-except 中发布事件, 异常被吞掉
       - 即使总线崩溃, KillSwitch 仍可独立触发
    4. Feature Flag 透传 (HC-1)
       - USE_RISK_BUS_EVENT_DRIVEN=False 时, 适配器仅做日志, 不发布事件
       - 默认 False, 保护 V9 基线

迁移策略:
    - 不迁移 KillSwitch 类到 utils/risk/ (避免破坏现有 import)
    - 在 utils/risk/kill_switch_adapter.py 提供适配器
    - 现有代码继续 from utils.kill_switch import KillSwitch
    - 新代码可选 from utils.risk.kill_switch_adapter import KillSwitchAdapter
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, cast

from utils.infra.feature_flags import is_enabled
from utils.risk.risk_bus import RiskBus, get_bus
from utils.risk.risk_event import (
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
    make_kill_switch_triggered_event,
    make_margin_breach_event,
)

logger = logging.getLogger("kill_switch_adapter")

# Flag 名称
FLAG_NAME = "USE_RISK_BUS_EVENT_DRIVEN"


class KillSwitchAdapter:
    """KillSwitch 总线适配器 (HC-2 同步路径保护).

    Usage:
        >>> from utils.kill_switch import KillSwitch
        >>> from utils.risk.kill_switch_adapter import KillSwitchAdapter
        >>> ks = KillSwitch()
        >>> adapter = KillSwitchAdapter(ks)
        >>> status = adapter.check_margin_status(margin_usage=0.78)
        >>> # status 与 KillSwitch.check_margin_status() 完全一致
        >>> # 但触发 level>=1 时会自动发布归档事件到总线

    特性:
        - 同步路径延迟 <1ms (HC-2)
        - 总线故障不影响 check_margin_status 结果
        - USE_RISK_BUS_EVENT_DRIVEN=False 时仅做日志, 不发布事件
    """

    def __init__(
        self,
        kill_switch: Any,
        bus: Optional[RiskBus] = None,
        publish_events: bool = True,
    ) -> None:
        """初始化适配器.

        Args:
            kill_switch: 被 wraps 的 KillSwitch 实例
            bus: 可选的 RiskBus 实例 (None 时使用默认单例)
            publish_events: 是否发布事件到总线 (False 时仅做日志)
        """
        self._ks = kill_switch
        self._bus = bus  # 延迟获取, 避免单例污染
        self._publish_events = publish_events
        self._flag_name = FLAG_NAME

    @property
    def bus(self) -> RiskBus:
        """获取总线实例 (延迟初始化)."""
        if self._bus is None:
            self._bus = get_bus()
        return self._bus

    def check_margin_status(
        self,
        margin_usage: Optional[float] = None,
    ) -> Dict[str, Any]:
        """检查保证金状态 (HC-2 同步路径, 不走总线).

        Args:
            margin_usage: 外部传入的真实保证金占用率 (0-1).
                          None 时从环境变量/模拟获取.

        Returns:
            与 KillSwitch.check_margin_status() 完全一致的状态字典

        Note:
            - HC-2: 此方法延迟 <1ms, 不走总线
            - 触发 level>=1 后会异步发布归档事件 (best-effort)
            - 总线故障不影响返回结果
        """
        # 同步直调原 KillSwitch (HC-2 铁律: 不走总线)
        start_ts = time.perf_counter()
        status = cast(Dict[str, Any], self._ks.check_margin_status(margin_usage=margin_usage))
        elapsed_ms = (time.perf_counter() - start_ts) * 1000

        # 仅在 level>=1 时发布归档事件 (best-effort, 不影响同步路径)
        level = int(status.get("level", 0))
        if level >= 1:
            self._safe_publish_margin_breach(status, margin_usage)

        # 延迟日志 (HC-2 监控)
        if elapsed_ms > 1.0:
            logger.warning(
                "check_margin_status 延迟 %.3fms 超过 1ms 阈值 (HC-2) | level=%d",
                elapsed_ms,
                level,
            )

        return status

    def execute_kill_switch(self, level: int) -> Dict[str, Any]:
        """执行熔断协议 (HC-2 同步路径).

        Args:
            level: 熔断级别 (1/2/3)

        Returns:
            与 KillSwitch.execute_kill_switch() 完全一致的结果字典

        Note:
            - HC-2: 此方法不走总线
            - 执行后会发布 KILL_SWITCH_TRIGGERED 归档事件 (best-effort)
        """
        # 同步直调原 KillSwitch (HC-2 铁律)
        result = cast(Dict[str, Any], self._ks.execute_kill_switch(level))

        # 执行成功后发布归档事件 (best-effort)
        if result.get("executed", False):
            self._safe_publish_kill_switch_triggered(level, result)

        return result

    def register_broker_callback(self, callback: Any) -> None:
        """注册 broker 回调 (委托给原 KillSwitch).

        BUG 修复: 原代码检查 hasattr(self._ks, "register_broker_callback") 永远返回 False,
        因为 kill_switch.py 的方法名是 set_broker_callback (不是 register_broker_callback).
        这导致 broker 回调从未注册, execute_kill_switch 时 _broker_callback is None 必抛
        RuntimeError, 熔断协议形同虚设.
        """
        if hasattr(self._ks, "set_broker_callback"):
            self._ks.set_broker_callback(callback)
        elif hasattr(self._ks, "register_broker_callback"):
            # 向后兼容: 如果未来版本改名为 register_broker_callback
            self._ks.register_broker_callback(callback)
        else:
            logger.warning("原 KillSwitch 不支持 set_broker_callback")

    # ============================================================
    # 内部方法: best-effort 事件发布
    # ============================================================
    def _safe_publish_margin_breach(
        self,
        status: Dict[str, Any],
        margin_usage: Optional[float],
    ) -> None:
        """best-effort 发布 MARGIN_BREACH 事件.

        - USE_RISK_BUS_EVENT_DRIVEN=False 时仅做日志
        - 总线故障时仅记录日志, 不抛异常
        """
        if not self._publish_events:
            return

        if not is_enabled(self._flag_name):
            # Flag 关闭: 仅日志, 不发布事件
            logger.info(
                "MARGIN_BREACH (Flag=off, 仅日志) | level=%d | status=%s",
                status.get("level", 0),
                {k: v for k, v in status.items() if k != "timestamp"},
            )
            return

        try:
            level = int(status.get("level", 0))
            usage = float(margin_usage if margin_usage is not None else status.get("margin_usage_ratio", 0.0))
            event = make_margin_breach_event(
                source="kill_switch",
                margin_usage=usage,
                level=level,
                severity=RiskSeverity.CRITICAL if level >= 2 else RiskSeverity.WARN,
                margin_call=status.get("margin_call", False),
                extreme_margin_call=status.get("extreme_margin_call", False),
            )
            self.bus.publish(event)
        except Exception as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # HC-2: 总线故障不影响 KillSwitch, 仅记录日志
            logger.error(
                "MARGIN_BREACH 事件发布失败 (总线故障, KillSwitch 仍正常) | error=%s",
                e,
                exc_info=True,
            )

    def _safe_publish_kill_switch_triggered(
        self,
        level: int,
        result: Dict[str, Any],
    ) -> None:
        """best-effort 发布 KILL_SWITCH_TRIGGERED 事件.

        - USE_RISK_BUS_EVENT_DRIVEN=False 时仅做日志
        - 总线故障时仅记录日志, 不抛异常
        """
        if not self._publish_events:
            return

        if not is_enabled(self._flag_name):
            logger.warning(
                "KILL_SWITCH_TRIGGERED (Flag=off, 仅日志) | level=%d | executed=%s",
                level,
                result.get("executed", False),
            )
            return

        try:
            actions_taken = result.get("actions_taken", [])
            # actions_taken 可能是 list of dict, 提取 action 名称
            action_names = []
            for a in actions_taken:
                if isinstance(a, dict):
                    action_names.append(str(a.get("action", "unknown")))
                else:
                    action_names.append(str(a))

            event = make_kill_switch_triggered_event(
                source="kill_switch",
                level=level,
                actions_taken=action_names,
            )
            self.bus.publish(event)
        except Exception as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
            # HC-2: 总线故障不影响 KillSwitch
            logger.error(
                "KILL_SWITCH_TRIGGERED 事件发布失败 (总线故障, KillSwitch 仍正常) | error=%s",
                e,
                exc_info=True,
            )

    # ============================================================
    # 决策订阅 (可选, 用于 sync_decide 聚合)
    # ============================================================
    def register_as_decision_subscriber(self) -> None:
        """注册 KillSwitch 为决策订阅者 (可选).

        注册后, 当其他模块发布 MARGIN_BREACH 事件并调用 sync_decide() 时,
        KillSwitch 适配器会被调用, 返回基于保证金状态的决策.

        Note:
            - 仅在 USE_RISK_BUS_EVENT_DRIVEN=True 时有意义
            - 决策基于当前保证金状态, 不修改 KillSwitch 状态
        """

        def decision_callback(event: RiskEvent) -> RiskDecision:
            return self._make_decision_from_event(event)

        self.bus.subscribe_decision(
            RiskEventType.MARGIN_BREACH,
            decision_callback,
        )
        logger.info("KillSwitch 决策订阅已注册")

    def _make_decision_from_event(self, event: RiskEvent) -> RiskDecision:
        """基于事件生成决策 (不调用 KillSwitch 同步路径)."""
        level = int(event.payload.get("level", 0))
        if level >= 3:
            return RiskDecision(
                action=RiskAction.KILL_SWITCH,
                reason=f"margin_level_{level}_extreme",
                confidence=1.0,
                source="kill_switch_adapter",
            )
        elif level == 2:
            return RiskDecision(
                action=RiskAction.FORCE_LIQUIDATE,
                reason="margin_level_2_force_close",
                confidence=0.95,
                source="kill_switch_adapter",
            )
        elif level == 1:
            return RiskDecision(
                action=RiskAction.DISABLE_NEW_ORDERS,
                reason="margin_level_1_defensive",
                confidence=0.9,
                source="kill_switch_adapter",
            )
        return RiskDecision(
            action=RiskAction.PASS,
            reason="margin_normal",
            confidence=0.5,
            source="kill_switch_adapter",
        )


def adapt_kill_switch(kill_switch: Any, bus: Optional[RiskBus] = None) -> KillSwitchAdapter:
    """便捷函数: 包装 KillSwitch 为适配器.

    Args:
        kill_switch: 原 KillSwitch 实例
        bus: 可选的总线实例

    Returns:
        KillSwitchAdapter 实例
    """
    return KillSwitchAdapter(kill_switch, bus=bus)


__all__ = [
    "KillSwitchAdapter",
    "adapt_kill_switch",
]
