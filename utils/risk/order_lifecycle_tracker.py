"""T16 订单生命周期跟踪器 — 实盘订单状态机 + 超时撤单 + 孤儿单检测.

属于「实盘验证四件套」第 2 位, 核心目的: **管理实盘订单从 PENDING 到终态的完整生命周期,
处理 broker 异步回调、超时重试、孤儿单清理, 确保不遗漏任何一笔实盘订单的状态**.

8 态状态机:
    PENDING → SUBMITTED → PARTIAL_FILL → FILLED     (正常路径)
                       ↘                  ↘ CANCELLED (主动撤单)
                        ↘ REJECTED                   ↘ ERROR (系统异常)
    SUBMITTED →(超时)→ ORPHANED  (未收到任何回报)

关键能力:
    1. register(): 注册新订单, 设定超时 deadline
    2. poll_once(): 轮询 broker, 更新状态, 触发回调
    3. cancel_stale_orders(): 批量撤销超时未成交订单
    4. force_cancel(): 强制撤单 (用于 T11/T12 触发回滚)
    5. get_all_active(): 获取所有未终态订单

设计原则:
    - 线程安全: 内部加锁, 支持多线程 poll
    - 事件驱动: 每次状态转移写 T14 审计
    - 可测试: broker 为注入接口, timeout_sec 可配置

用法:
    from utils.risk.order_lifecycle_tracker import (
        OrderLifecycleTracker, OrderState, TrackedOrder,
    )
    tracker = OrderLifecycleTracker(broker=broker, audit_logger=logger)
    tracked = tracker.register(order, callback=on_fill)
    tracker.poll_once()
    stale_ids = tracker.cancel_stale_orders()
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from utils.risk.risk_audit_logger import RiskAuditLogger

logger = logging.getLogger("order_lifecycle")


# ============================================================
# 协议定义
# ============================================================


class BrokerProtocol(Protocol):
    """T16 需要的 broker 最小接口."""

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        """返回 {state: str, filled_qty: int, avg_price: float, rejection_reason: str}."""
        ...

    def cancel_order(self, broker_order_id: str) -> bool: ...


# ============================================================
# 数据结构
# ============================================================


class OrderState(StrEnum):
    """订单 8 态状态机."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ERROR = "error"
    ORPHANED = "orphaned"  # 超时未收到任何回报

    @property
    def is_terminal(self) -> bool:
        """是否终态 (不会再转移)."""
        return self in (
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.ERROR,
            OrderState.ORPHANED,
        )

    @property
    def is_active(self) -> bool:
        """是否活跃态 (仍需跟踪)."""
        return not self.is_terminal


# 状态转移合法表 (from → set of valid targets)
_VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PENDING: {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.ERROR},
    OrderState.SUBMITTED: {
        OrderState.PARTIAL_FILL,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.ERROR,
        OrderState.ORPHANED,
    },
    OrderState.PARTIAL_FILL: {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.ERROR,
    },
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.ERROR: set(),
    OrderState.ORPHANED: set(),
}


@dataclass
class TrackedOrder:
    """被跟踪的订单."""

    order_id: str  # 内部订单 ID
    broker_order_id: str  # broker 返回的委托编号
    symbol: str
    side: str
    planned_qty: int
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    state: OrderState = OrderState.PENDING
    submitted_at: datetime = field(default_factory=datetime.now)
    last_update_at: datetime = field(default_factory=datetime.now)
    timeout_deadline: datetime = field(default_factory=datetime.now)
    rejection_reason: str = ""
    callback: Callable[..., Any] | None = None
    transition_history: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (from, to, ts)

    @property
    def is_timed_out(self) -> bool:
        """是否已超时 (未到终态且超过 deadline)."""
        return self.state.is_active and datetime.now() > self.timeout_deadline

    @property
    def fill_rate(self) -> float:
        """成交率."""
        return self.filled_qty / self.planned_qty if self.planned_qty > 0 else 0.0


# ============================================================
# broker 状态码映射 (兼容 QMT / 通用)
# ============================================================


def map_broker_state(raw_state: str) -> OrderState:
    """把 broker 返回的原始状态字符串映射到 OrderState.

    兼容 QMT (xtquant 48-58) 和通用字符串:
        48 / NOT_REPORTED   → PENDING
        49 / REPORTED       → SUBMITTED
        50 / PART_TRADED    → PARTIAL_FILL
        52 / / 53 / ALL_TRADED → FILLED
        54 / 55 / CANCELLED → CANCELLED
        56 / REJECTED       → REJECTED
        其他                → ERROR
    """
    s = str(raw_state).strip().upper()
    mapping = {
        "48": OrderState.PENDING,
        "NOT_REPORTED": OrderState.PENDING,
        "49": OrderState.SUBMITTED,
        "REPORTED": OrderState.SUBMITTED,
        "50": OrderState.PARTIAL_FILL,
        "PART_TRADED": OrderState.PARTIAL_FILL,
        "PARTIAL": OrderState.PARTIAL_FILL,
        "52": OrderState.FILLED,
        "53": OrderState.FILLED,
        "ALL_TRADED": OrderState.FILLED,
        "FILLED": OrderState.FILLED,
        "54": OrderState.CANCELLED,
        "55": OrderState.CANCELLED,
        "CANCELLED": OrderState.CANCELLED,
        "CANCELED": OrderState.CANCELLED,
        "56": OrderState.REJECTED,
        "REJECTED": OrderState.REJECTED,
    }
    return mapping.get(s, OrderState.ERROR)


# ============================================================
# 主类
# ============================================================


class OrderLifecycleTracker:
    """订单生命周期跟踪器 — 状态机 + 超时撤单 + 孤儿单检测."""

    def __init__(
        self,
        broker: BrokerProtocol,
        audit_logger: RiskAuditLogger,
        timeout_sec: int = 30,
        orphan_sec: int = 10,
    ) -> None:
        if timeout_sec < 1:
            raise ValueError(f"timeout_sec 应 ≥1, 实际 {timeout_sec}")
        if orphan_sec < 1:
            raise ValueError(f"orphan_sec 应 ≥1, 实际 {orphan_sec}")
        self.broker = broker
        self.audit = audit_logger
        self.timeout_sec = timeout_sec
        self.orphan_sec = orphan_sec

        self._orders: dict[str, TrackedOrder] = {}  # order_id → TrackedOrder
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    # 注册与查询
    # ------------------------------------------------------------

    def register(
        self,
        order_id: str,
        broker_order_id: str,
        symbol: str,
        side: str,
        planned_qty: int,
        callback: Callable[..., Any] | None = None,
    ) -> TrackedOrder:
        """注册一笔新订单到跟踪器.

        Args:
            order_id: 内部订单 ID
            broker_order_id: broker 返回的委托编号
            symbol: 标的代码
            side: buy/sell
            planned_qty: 计划数量
            callback: 状态变化时的回调 (callable, 接收 TrackedOrder)
        """
        if not order_id:
            raise ValueError("order_id 不能为空")
        if planned_qty <= 0:
            raise ValueError(f"planned_qty 应 >0, 实际 {planned_qty}")

        now = datetime.now()
        tracked = TrackedOrder(
            order_id=order_id,
            broker_order_id=broker_order_id,
            symbol=symbol,
            side=side,
            planned_qty=planned_qty,
            state=OrderState.SUBMITTED,
            submitted_at=now,
            last_update_at=now,
            timeout_deadline=now + timedelta(seconds=self.timeout_sec),
            callback=callback,
        )
        tracked.transition_history.append(("PENDING", "SUBMITTED", now.isoformat()))

        with self._lock:
            self._orders[order_id] = tracked

        self.audit.log(
            module="T16_LIFECYCLE",
            action="OTHER",
            severity="INFO",
            symbol=symbol,
            reason=f"注册订单 order_id={order_id} broker_id={broker_order_id} qty={planned_qty}",
        )
        logger.info(
            f"[T16] 注册订单 {order_id} → {broker_order_id} ({symbol} {side} {planned_qty})"
        )
        return tracked

    def get_state(self, order_id: str) -> OrderState | None:
        """查询订单状态."""
        with self._lock:
            tracked = self._orders.get(order_id)
            return tracked.state if tracked else None

    def get_all_active(self) -> list[TrackedOrder]:
        """获取所有未终态订单."""
        with self._lock:
            return [o for o in self._orders.values() if o.state.is_active]

    def get_all(self) -> list[TrackedOrder]:
        """获取全部已跟踪订单."""
        with self._lock:
            return list(self._orders.values())

    # ------------------------------------------------------------
    # 轮询与状态更新
    # ------------------------------------------------------------

    def poll_once(self) -> list[TrackedOrder]:
        """轮询 broker 一次, 更新所有活跃订单的状态.

        Returns:
            发生状态变化的 TrackedOrder 列表
        """
        changed: list[TrackedOrder] = []
        active_orders = self.get_all_active()

        for tracked in active_orders:
            try:
                status = self.broker.get_order_status(tracked.broker_order_id)
            except Exception as exc:
                logger.warning(
                    f"[T16] 查询 broker 状态异常 {tracked.broker_order_id}: {exc}"
                )
                continue

            raw_state = status.get("state", "")
            new_state = map_broker_state(raw_state)
            filled_qty = int(status.get("filled_qty", 0))
            avg_price = float(status.get("avg_price", 0.0))
            rejection_reason = status.get("rejection_reason", "")

            if self._transition(
                tracked, new_state, filled_qty, avg_price, rejection_reason
            ):
                changed.append(tracked)

        # 检查超时
        for tracked in active_orders:
            if tracked.is_timed_out and tracked.state == OrderState.SUBMITTED:
                # 超时且仍为 SUBMITTED → 标记 ORPHANED
                if self._transition(tracked, OrderState.ORPHANED):
                    changed.append(tracked)
            elif tracked.is_timed_out and tracked.state == OrderState.PARTIAL_FILL:
                # 部分成交超时 → 尝试撤单
                if self._try_cancel(tracked):
                    changed.append(tracked)

        return changed

    def _transition(
        self,
        tracked: TrackedOrder,
        new_state: OrderState,
        filled_qty: int | None = None,
        avg_price: float | None = None,
        rejection_reason: str = "",
    ) -> bool:
        """执行状态转移 (带合法性检查). 返回是否实际转移."""
        old_state = tracked.state

        # 相同状态不转移 (但更新成交数据)
        if new_state == old_state:
            if filled_qty is not None and filled_qty > tracked.filled_qty:
                tracked.filled_qty = filled_qty
                tracked.last_update_at = datetime.now()
            if avg_price is not None and avg_price > 0:
                tracked.avg_fill_price = avg_price
            return False

        # 合法性检查
        if new_state not in _VALID_TRANSITIONS.get(old_state, set()):
            logger.warning(
                f"[T16] 非法状态转移 {old_state}→{new_state} "
                f"(order={tracked.order_id}), 跳过"
            )
            return False

        # 执行转移
        tracked.state = new_state
        tracked.last_update_at = datetime.now()
        if filled_qty is not None:
            tracked.filled_qty = filled_qty
        if avg_price is not None and avg_price > 0:
            tracked.avg_fill_price = avg_price
        if rejection_reason:
            tracked.rejection_reason = rejection_reason

        now_str = datetime.now().isoformat()
        tracked.transition_history.append((old_state.value, new_state.value, now_str))

        # 审计日志
        severity = (
            "INFO"
            if new_state in (OrderState.FILLED, OrderState.PARTIAL_FILL)
            else "WARN"
        )
        self.audit.log(
            module="T16_LIFECYCLE",
            action="OTHER",
            severity=severity,
            symbol=tracked.symbol,
            reason=(
                f"状态转移 {old_state}→{new_state} order={tracked.order_id} "
                f"filled={tracked.filled_qty}/{tracked.planned_qty}"
            ),
        )
        logger.info(
            f"[T16] {tracked.order_id}: {old_state}→{new_state} "
            f"({tracked.symbol} {tracked.filled_qty}/{tracked.planned_qty})"
        )

        # 触发回调
        if tracked.callback is not None:
            try:
                tracked.callback(tracked)
            except Exception as exc:
                logger.warning(f"[T16] 回调异常 order={tracked.order_id}: {exc}")

        return True

    # ------------------------------------------------------------
    # 撤单
    # ------------------------------------------------------------

    def cancel_stale_orders(self) -> list[str]:
        """撤销所有超时未成交订单. 返回成功撤单的 order_id 列表."""
        cancelled_ids: list[str] = []
        for tracked in self.get_all_active():
            if tracked.is_timed_out:
                if self._try_cancel(tracked):
                    cancelled_ids.append(tracked.order_id)
        return cancelled_ids

    def force_cancel(self, order_id: str) -> bool:
        """强制撤单 (用于 T11/T12 触发回滚)."""
        with self._lock:
            tracked = self._orders.get(order_id)
        if tracked is None or not tracked.state.is_active:
            return False
        return self._try_cancel(tracked)

    def force_cancel_all(self) -> list[str]:
        """强制撤销所有活跃订单 (紧急回滚)."""
        cancelled: list[str] = []
        for tracked in self.get_all_active():
            if self._try_cancel(tracked):
                cancelled.append(tracked.order_id)
        return cancelled

    def _try_cancel(self, tracked: TrackedOrder) -> bool:
        """尝试撤单. 返回是否成功."""
        try:
            ok = self.broker.cancel_order(tracked.broker_order_id)
            if ok:
                self._transition(tracked, OrderState.CANCELLED)
                logger.info(f"[T16] 撤单成功 {tracked.order_id}")
                return True
            # broker 返回 False (撤单失败), 标记 ERROR
            self._transition(
                tracked, OrderState.ERROR, rejection_reason="broker 拒绝撤单"
            )
            logger.warning(f"[T16] 撤单被拒 {tracked.order_id}")
            return False
        except Exception as exc:
            logger.error(f"[T16] 撤单异常 {tracked.order_id}: {exc}")
            self._transition(
                tracked, OrderState.ERROR, rejection_reason=f"撤单异常: {exc}"
            )
            return False

    # ------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """返回当前跟踪器状态快照."""
        with self._lock:
            orders = list(self._orders.values())
        active = [o for o in orders if o.state.is_active]
        return {
            "total_tracked": len(orders),
            "active_count": len(active),
            "terminal_count": len(orders) - len(active),
            "by_state": {
                s.value: sum(1 for o in orders if o.state == s) for s in OrderState
            },
            "active_order_ids": [o.order_id for o in active],
        }
