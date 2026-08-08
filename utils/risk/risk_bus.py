"""风控事件总线 — 模块整合 8.4 (T3.1).

任务: T3.1
责任层: L5 风控
依赖: T3.1 risk_event.py

设计原则 (对冲基金标准):
    1. 同步优先 + 异步归档 (HC-2)
       - KillSwitch 同步直调, 不走总线, 延迟 <1ms
       - 总线仅做事件归档订阅, 故障时 KillSwitch 仍可独立触发
    2. pub/sub 解耦
       - publish() 投递事件, 不阻塞发布者
       - subscribe() 注册订阅者回调, 支持事件类型过滤
    3. 决策聚合 (为 T3.3 预留)
       - sync_decide() 同步聚合多模块决策 (取最严格动作)
       - RiskDecisionAggregator 实现加权/最严格/多数表决策略
    4. 审计可追溯
       - 所有 publish 的事件写入审计日志 (JSONL)
       - 所有 subscribe 的决策写入决策日志

硬约束:
    - HC-2: KillSwitch 同步路径延迟 <1ms, 不走总线
    - HC-5: 配置走 ConfigManager 4 级优先级

Feature Flag:
    - USE_RISK_BUS_EVENT_DRIVEN (默认 False, critical_path: true)
    - 启用后: 总线订阅激活, 异步决策路径启用
    - 关闭时: 总线仅做日志归档, 不影响同步路径
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Callable, Sequence

from utils.infra.feature_flags import is_enabled
from utils.risk.risk_event import (
    RiskAction,
    RiskDecision,
    RiskEvent,
    RiskEventType,
)

logger = logging.getLogger("risk_bus")

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 审计日志路径
_AUDIT_LOG_DIR = _PROJECT_ROOT / "reports" / "risk_bus_audit"

# Flag 名称
FLAG_NAME = "USE_RISK_BUS_EVENT_DRIVEN"

# 同步决策超时 (秒), 防止订阅者卡死
SYNC_DECIDE_TIMEOUT = 0.5

# 事件历史缓存大小 (用于最近事件查询)
EVENT_HISTORY_SIZE = 1000


# ============================================================
# 订阅者类型
# ============================================================
Subscriber = Callable[[RiskEvent], None]
DecisionSubscriber = Callable[[RiskEvent], RiskDecision]


class SubscriptionError(Exception):
    """订阅操作异常."""


class RiskBus:
    """风控事件总线 (单例).

    设计:
        - 同步路径: publish() 直接调用同步订阅者 (日志归档等)
        - 异步路径: asyncio.Queue + 后台 consumer (仅 USE_RISK_BUS_EVENT_DRIVEN=True 时启用)
        - 决策路径: sync_decide() 同步聚合多模块决策

    特性:
        - 线程安全 (RLock 保护订阅者列表)
        - 单例模式 (全项目共享)
        - 事件历史缓存 (最近 N 个事件)
        - 审计日志 (JSONL 格式)

    Usage:
        >>> bus = RiskBus.get_instance()
        >>> bus.subscribe(RiskEventType.MARGIN_BREACH, my_handler)
        >>> bus.publish(event)
        >>> decision = bus.sync_decide(event)
    """

    _instance: RiskBus | None = None
    _lock: RLock = RLock()

    def __init__(self, audit_log_dir: Path | None = None) -> None:
        """初始化总线.

        Args:
            audit_log_dir: 审计日志目录 (None 时使用默认路径)
        """
        self._subscribers: dict[RiskEventType, list[Subscriber]] = defaultdict(list)
        self._decision_subscribers: dict[RiskEventType, list[DecisionSubscriber]] = defaultdict(list)
        self._event_history: deque[RiskEvent] = deque(maxlen=EVENT_HISTORY_SIZE)
        self._audit_log_dir = audit_log_dir or _AUDIT_LOG_DIR
        self._audit_log_dir.mkdir(parents=True, exist_ok=True)
        # 异步队列 (仅 USE_RISK_BUS_EVENT_DRIVEN=True 时启用)
        self._async_queue: asyncio.Queue | None = None
        self._async_consumer_task: asyncio.Task | None = None
        logger.info(
            "RiskBus 初始化 | audit_log_dir=%s | flag=%s",
            self._audit_log_dir,
            is_enabled(FLAG_NAME),
        )

    @classmethod
    def get_instance(cls) -> RiskBus:
        """获取单例 (线程安全)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (仅测试用)."""
        with cls._lock:
            cls._instance = None

    # ============================================================
    # 订阅 API
    # ============================================================
    def subscribe(
        self,
        event_type: RiskEventType,
        callback: Subscriber,
    ) -> None:
        """订阅事件 (同步回调).

        Args:
            event_type: 订阅的事件类型
            callback: 回调函数 (event) -> None

        Note:
            - 回调在发布线程同步执行, 应快速返回
            - 回调异常被捕获并记录, 不影响其他订阅者
        """
        if not callable(callback):
            raise SubscriptionError(f"callback must be callable, got {type(callback)}")
        with self._lock:
            self._subscribers[event_type].append(callback)
        logger.info(
            "订阅注册 | event_type=%s | callback=%s | 总数=%d",
            event_type.value,
            getattr(callback, "__name__", repr(callback)),
            len(self._subscribers[event_type]),
        )

    def subscribe_decision(
        self,
        event_type: RiskEventType,
        callback: DecisionSubscriber,
    ) -> None:
        """订阅决策 (同步回调, 返回 RiskDecision).

        Args:
            event_type: 订阅的事件类型
            callback: 决策回调 (event) -> RiskDecision

        Note:
            - 用于 sync_decide() 聚合多模块决策
            - 回调应快速返回 (建议 <100ms)
        """
        if not callable(callback):
            raise SubscriptionError(f"callback must be callable, got {type(callback)}")
        with self._lock:
            self._decision_subscribers[event_type].append(callback)
        logger.info(
            "决策订阅注册 | event_type=%s | callback=%s | 总数=%d",
            event_type.value,
            getattr(callback, "__name__", repr(callback)),
            len(self._decision_subscribers[event_type]),
        )

    def unsubscribe(
        self,
        event_type: RiskEventType,
        callback: Subscriber,
    ) -> bool:
        """取消订阅.

        Returns:
            True 如果找到并移除, False 如果未找到
        """
        with self._lock:
            subs = self._subscribers.get(event_type, [])
            try:
                subs.remove(callback)
                return True
            except ValueError:
                return False

    def clear_subscribers(self) -> None:
        """清空所有订阅者 (仅测试用)."""
        with self._lock:
            self._subscribers.clear()
            self._decision_subscribers.clear()
            self._event_history.clear()

    # ============================================================
    # 发布 API
    # ============================================================
    def publish(self, event: RiskEvent) -> int:
        """发布事件 (同步路径).

        Args:
            event: 风控事件

        Returns:
            成功调用的订阅者数量

        Note:
            - HC-2: 此方法不影响 KillSwitch 同步路径
            - 同步调用所有订阅者, 异常被捕获并记录
            - 事件写入审计日志 + 历史缓存
            - severity >= WARN 时自动发送告警 (G8 修复, 2026-08-06)
        """
        # 写入历史缓存
        with self._lock:
            self._event_history.append(event)

        # 写入审计日志
        self._write_audit_log(event)

        # G8 修复: severity >= WARN 时发送告警 (fail-open, 不阻断风控)
        try:
            sev = str(event.severity.value).upper() if event.severity else ""
            if sev in ("WARN", "WARNING", "CRITICAL", "FATAL", "ERROR"):
                level = "critical" if "CRITICAL" in sev or "FATAL" in sev else "warning"
                title = f"[风控] {event.event_type.value} ({event.source})"
                msg_parts = [f"source={event.source}", f"severity={sev}"]
                if event.payload:
                    payload_str = ", ".join(f"{k}={v}" for k, v in list(event.payload.items())[:5])
                    msg_parts.append(payload_str)
                from utils.notify import send_alert

                send_alert(title=title, content=" | ".join(msg_parts), level=level)
        except Exception:  # noqa: BLE001  # 告警 fail-open, 不阻断风控
            logger.warning("告警发送失败 (fail-open, 风控仍正常)", exc_info=True)

        # 同步调用订阅者
        with self._lock:
            subs = list(self._subscribers.get(event.event_type, []))
            # 同时通知订阅所有事件类型的订阅者 (通配符)
            # 这里简化: 只通知精确匹配的订阅者

        invoked = 0
        for cb in subs:
            try:
                cb(event)
                invoked += 1
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                    ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
                # 风险隔离边界: 单个订阅者/决策者异常不得影响其他
                # ValueError/TypeError — 数据格式/类型错误
                # KeyError/AttributeError — 字段/属性缺失
                # RuntimeError — 运行时错误
                # ZeroDivisionError/OverflowError — 数值计算异常
                # OSError — 文件/网络 IO 异常
                logger.error(
                    "订阅者异常 | event_type=%s | callback=%s | error=%s",
                    event.event_type.value,
                    getattr(cb, "__name__", repr(cb)),
                    e,
                    exc_info=True,
                )

        # 异步路径 (仅 USE_RISK_BUS_EVENT_DRIVEN=True 时启用)
        if is_enabled(FLAG_NAME) and self._async_queue is not None:
            try:
                self._async_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("异步队列已满, 事件丢弃: %s", event.event_type.value)

        logger.debug(
            "事件发布 | type=%s | source=%s | severity=%s | 订阅者调用=%d",
            event.event_type.value,
            event.source,
            event.severity.value,
            invoked,
        )
        return invoked

    # ============================================================
    # 决策 API (同步聚合)
    # ============================================================
    def sync_decide(
        self,
        event: RiskEvent,
        timeout: float = SYNC_DECIDE_TIMEOUT,
    ) -> RiskDecision:
        """同步聚合多模块决策 (取最严格动作).

        Args:
            event: 风控事件
            timeout: 单个订阅者超时 (秒), 实际用 try-except 保护

        Returns:
            聚合后的决策 (最严格动作)

        策略:
            - 取所有订阅者返回决策中 "最严格" 的动作
            - 严格度排序: KILL_SWITCH > FORCE_LIQUIDATE > DISABLE_NEW_ORDERS > REDUCE_POSITION > PASS
            - 如果所有订阅者都返回 PASS, 则返回 PASS
            - 如果没有决策订阅者, 返回默认 PASS 决策
        """
        with self._lock:
            deciders = list(self._decision_subscribers.get(event.event_type, []))

        if not deciders:
            return RiskDecision(
                action=RiskAction.PASS,
                reason="no_decision_subscribers",
                confidence=0.0,
                source="risk_bus",
            )

        decisions: list[RiskDecision] = []
        for decider in deciders:
            try:
                # 简单超时保护 (无法真正中断, 但至少捕获异常)
                d = decider(event)
                if isinstance(d, RiskDecision):
                    decisions.append(d)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                    ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
                # 风险隔离边界: 单个订阅者/决策者异常不得影响其他
                # ValueError/TypeError — 数据格式/类型错误
                # KeyError/AttributeError — 字段/属性缺失
                # RuntimeError — 运行时错误
                # ZeroDivisionError/OverflowError — 数值计算异常
                # OSError — 文件/网络 IO 异常
                logger.error(
                    "决策订阅者异常 | event_type=%s | decider=%s | error=%s",
                    event.event_type.value,
                    getattr(decider, "__name__", repr(decider)),
                    e,
                    exc_info=True,
                )

        if not decisions:
            return RiskDecision(
                action=RiskAction.PASS,
                reason="all_deciders_failed",
                confidence=0.0,
                source="risk_bus",
            )

        # 取最严格动作
        return _aggregate_strictest(decisions)

    # ============================================================
    # 查询 API
    # ============================================================
    def get_recent_events(
        self,
        event_type: RiskEventType | None = None,
        limit: int = 100,
    ) -> list[RiskEvent]:
        """获取最近的事件.

        Args:
            event_type: 过滤事件类型 (None 表示所有)
            limit: 返回数量上限

        Returns:
            事件列表 (按时间倒序)
        """
        with self._lock:
            events = list(self._event_history)
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        # 倒序 (最新在前)
        events.reverse()
        return events[:limit]

    def get_subscriber_count(
        self,
        event_type: RiskEventType | None = None,
    ) -> int:
        """获取订阅者数量."""
        with self._lock:
            if event_type is None:
                return sum(len(subs) for subs in self._subscribers.values())
            return len(self._subscribers.get(event_type, []))

    def get_decision_subscriber_count(
        self,
        event_type: RiskEventType | None = None,
    ) -> int:
        """获取决策订阅者数量."""
        with self._lock:
            if event_type is None:
                return sum(len(subs) for subs in self._decision_subscribers.values())
            return len(self._decision_subscribers.get(event_type, []))

    # ============================================================
    # 异步路径 (可选, USE_RISK_BUS_EVENT_DRIVEN=True 时启用)
    # ============================================================
    async def start_async_consumer(self) -> None:
        """启动异步消费者 (仅 USE_RISK_BUS_EVENT_DRIVEN=True 时启用).

        Note:
            - 必须在 asyncio 事件循环中调用
            - 同一事件类型会同时被同步和异步订阅者处理
        """
        if not is_enabled(FLAG_NAME):
            logger.info("异步消费者未启动 (USE_RISK_BUS_EVENT_DRIVEN=False)")
            return

        if self._async_queue is None:
            self._async_queue = asyncio.Queue(maxsize=10000)

        if self._async_consumer_task is None or self._async_consumer_task.done():
            self._async_consumer_task = asyncio.create_task(self._async_consume_loop())
            logger.info("异步消费者已启动")

    async def _async_consume_loop(self) -> None:
        """异步消费循环."""
        while True:
            try:
                event = await self._async_queue.get()  # type: ignore[union-attr]
                # 异步处理 (目前仅日志, 后续可扩展异步订阅者)
                logger.info(
                    "异步事件处理 | type=%s | source=%s",
                    event.event_type.value,
                    event.source,
                )
            except asyncio.CancelledError:
                logger.info("异步消费者已停止")
                break
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                    ZeroDivisionError, OverflowError, OSError) as e:  # noqa: BLE001  # risk pub/sub 隔离, fail-safe
                # 风险隔离边界: 单个订阅者/决策者异常不得影响其他
                # ValueError/TypeError — 数据格式/类型错误
                # KeyError/AttributeError — 字段/属性缺失
                # RuntimeError — 运行时错误
                # ZeroDivisionError/OverflowError — 数值计算异常
                # OSError — 文件/网络 IO 异常
                logger.error("异步消费异常: %s", e, exc_info=True)

    async def stop_async_consumer(self) -> None:
        """停止异步消费者."""
        if self._async_consumer_task is not None:
            self._async_consumer_task.cancel()
            try:
                await self._async_consumer_task
            except asyncio.CancelledError:
                pass
            self._async_consumer_task = None
            logger.info("异步消费者已停止")

    # ============================================================
    # 审计日志
    # ============================================================
    def _write_audit_log(self, event: RiskEvent) -> None:
        """写入审计日志 (JSONL 格式, 按日期分文件)."""
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = self._audit_log_dir / f"events_{today}.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("审计日志写入失败: %s", e)


# ============================================================
# 决策聚合器 (T3.3 预留)
# ============================================================
def _aggregate_strictest(decisions: Sequence[RiskDecision]) -> RiskDecision:
    """取最严格的决策.

    严格度排序 (从严格到宽松):
        KILL_SWITCH > FORCE_LIQUIDATE > DISABLE_NEW_ORDERS > REDUCE_POSITION > PASS

    Args:
        decisions: 决策列表

    Returns:
        最严格的决策
    """
    if not decisions:
        return RiskDecision(
            action=RiskAction.PASS,
            reason="no_decisions",
            confidence=0.0,
            source="aggregator",
        )

    # 严格度映射 (数值越大越严格)
    strictness = {
        RiskAction.KILL_SWITCH: 5,
        RiskAction.FORCE_LIQUIDATE: 4,
        RiskAction.DISABLE_NEW_ORDERS: 3,
        RiskAction.REDUCE_POSITION: 2,
        RiskAction.PASS: 1,
    }

    # 按 strictness 降序, 取第一个
    sorted_decisions = sorted(
        decisions,
        key=lambda d: strictness.get(d.action, 0),
        reverse=True,
    )
    best = sorted_decisions[0]

    # 聚合 reduce_pct (取最大值, 最严格)
    if best.action == RiskAction.REDUCE_POSITION:
        max_reduce = max(d.reduce_pct for d in decisions if d.action == RiskAction.REDUCE_POSITION)
        if max_reduce > best.reduce_pct:
            # 创建新决策 (frozen=True, 用 dataclasses.replace)
            from dataclasses import replace

            best = replace(best, reduce_pct=max_reduce, reason=f"aggregated: {best.reason}")

    return best


class RiskDecisionAggregator:
    """决策聚合器 (T3.3 预留, 当前仅提供最严格策略).

    策略:
        - STRICTEST: 取最严格动作 (默认, 对冲基金标准)
        - WEIGHTED: 按 confidence 加权 (T3.3 实现)
        - MAJORITY: 多数表决 (T3.3 实现)
    """

    def __init__(self, strategy: str = "STRICTEST") -> None:
        """初始化聚合器.

        Args:
            strategy: 聚合策略 ("STRICTEST" / "WEIGHTED" / "MAJORITY")
        """
        self.strategy = strategy.upper()

    def aggregate(self, decisions: Sequence[RiskDecision]) -> RiskDecision:
        """聚合决策.

        Args:
            decisions: 决策列表

        Returns:
            聚合后的决策
        """
        if self.strategy == "STRICTEST":
            return _aggregate_strictest(decisions)
        # WEIGHTED / MAJORITY 在 T3.3 实现
        logger.warning("策略 %s 尚未实现, 回退到 STRICTEST", self.strategy)
        return _aggregate_strictest(decisions)


# ============================================================
# 模块级快捷函数
# ============================================================
_default_bus: RiskBus | None = None


def get_bus() -> RiskBus:
    """获取默认总线实例 (单例)."""
    global _default_bus
    if _default_bus is None:
        _default_bus = RiskBus.get_instance()
    return _default_bus


def publish(event: RiskEvent) -> int:
    """快捷函数: 发布事件."""
    return get_bus().publish(event)


def subscribe(event_type: RiskEventType, callback: Subscriber) -> None:
    """快捷函数: 订阅事件."""
    get_bus().subscribe(event_type, callback)


def sync_decide(event: RiskEvent) -> RiskDecision:
    """快捷函数: 同步决策."""
    return get_bus().sync_decide(event)


__all__ = [
    "DecisionSubscriber",
    "RiskBus",
    "RiskDecisionAggregator",
    "Subscriber",
    "SubscriptionError",
    "get_bus",
    "publish",
    "subscribe",
    "sync_decide",
]
