"""G15 事件驱动回测 — 订单队列模块。

职责:
    - 维护活动订单队列(等待撮合)
    - 维护已完成订单(供结果生成)
    - 撤单时产出 CANCELLED 新对象,不就地修改(不可变性)

状态机:
    NOT_REPORTED → REPORTED → PART_TRADED → ALL_TRADED
                              ↓             ↓
                          CANCELLED     CANCELLED
                              ↓             ↓
                          REJECTED     REJECTED

设计原则(AGENTS.md):
    - 不可变性(§5.1): 所有状态变更走 dataclasses.replace()
    - 单一职责: 只管订单生命周期,不撮合、不计算成本
    - 复用数据类: 全量使用 utils.wt_structs.OrderData
"""

from __future__ import annotations

import dataclasses
from collections import deque
from typing import Optional

from utils.wt_structs import OrderData


class OrderState:
    """订单状态枚举(与 OrderData.status 字符串对齐)。

    6 个状态:
        NOT_REPORTED: 已提交,未报到交易所(回测中等价于"待撮合")
        REPORTED:     已报到交易所,待成交
        PART_TRADED:  部分成交
        ALL_TRADED:   全部成交(终态)
        CANCELLED:    已撤(终态)
        REJECTED:     已拒(终态)
    """

    NOT_REPORTED = "NOT_REPORTED"
    REPORTED = "REPORTED"
    PART_TRADED = "PART_TRADED"
    ALL_TRADED = "ALL_TRADED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @classmethod
    def is_active(cls, status: str) -> bool:
        """判断订单是否仍活动(可撮合/可撤单)。"""
        return status in (cls.NOT_REPORTED, cls.REPORTED, cls.PART_TRADED)

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """判断订单是否已终止(不可再变更)。"""
        return status in (cls.ALL_TRADED, cls.CANCELLED, cls.REJECTED)


class OrderQueue:
    """订单队列 — FIFO + 撤单 + 状态机。

    不可变性保证:
        - enqueue(order): 不修改入参 order,内部引用同一对象
        - cancel(order_id): 产出新 OrderData(status=CANCELLED),不修改原对象
        - mark_filled/mark_partial/mark_rejected: 同上,均产出新对象

    线程安全:
        非线程安全(回测为单线程模型)
    """

    def __init__(self) -> None:
        # 活动订单 FIFO 队列: 等待撮合
        self._pending: "deque[OrderData]" = deque()
        # 活动订单索引: order_id -> OrderData (O(1) 查找/撤单)
        self._active: dict[str, OrderData] = {}
        # 已完成订单: order_id -> OrderData (终态: ALL_TRADED/REJECTED)
        self._completed: dict[str, OrderData] = {}
        # 已撤单订单: order_id -> OrderData
        self._cancelled: dict[str, OrderData] = {}

    def enqueue(self, order: OrderData) -> None:
        """订单入队。

        Args:
            order: 待入队订单(状态应为 NOT_REPORTED 或 REPORTED)

        Raises:
            ValueError: order_id 重复
        """
        if order.order_id in self._active:
            raise ValueError(f"重复 order_id: {order.order_id}")
        self._active[order.order_id] = order
        self._pending.append(order)

    def cancel(self, order_id: str) -> bool:
        """撤单 — 产出 CANCELLED 新对象,不就地修改。

        Args:
            order_id: 订单ID

        Returns:
            True: 撤单成功
            False: 订单不存在或已终止
        """
        order = self._active.get(order_id)
        if order is None or not OrderState.is_active(order.status):
            return False
        # 不可变: 产出新对象
        cancelled = dataclasses.replace(order, status=OrderState.CANCELLED)
        self._cancelled[order_id] = cancelled
        del self._active[order_id]
        # 从 deque 中物理移除(线性扫描,但队列通常很短)
        self._pending = deque(o for o in self._pending if o.order_id != order_id)
        return True

    def pop_next(self) -> Optional[OrderData]:
        """弹出下一个活动订单(FIFO)。

        跳过已不在 _active 中的订单(如被撤单)。
        """
        while self._pending:
            order = self._pending.popleft()
            if order.order_id in self._active:
                return order
        return None

    def mark_filled(self, order_id: str, fill_price: float, fill_volume: float) -> None:
        """标记订单全部成交 — 产出 ALL_TRADED 新对象。

        Args:
            order_id: 订单ID
            fill_price: 成交价(预留,当前不存储到 OrderData)
            fill_volume: 成交量(预留)

        Raises:
            KeyError: order_id 不在活动队列中
        """
        if order_id not in self._active:
            raise KeyError(f"订单不在活动队列: {order_id}")
        order = self._active.pop(order_id)
        # 不可变: 产出新对象,traded_volume 设为 order.volume(全部成交)
        filled = dataclasses.replace(
            order,
            status=OrderState.ALL_TRADED,
            traded_volume=order.volume,
        )
        self._completed[order_id] = filled

    def mark_partial(
        self, order_id: str, fill_price: float, fill_volume: float
    ) -> None:
        """标记订单部分成交 — 累加 traded_volume,产出 PART_TRADED 新对象。

        若累计 traded_volume >= volume,自动转 ALL_TRADED。

        Args:
            order_id: 订单ID
            fill_price: 本次成交价
            fill_volume: 本次成交量

        Raises:
            KeyError: order_id 不在活动队列中
        """
        if order_id not in self._active:
            raise KeyError(f"订单不在活动队列: {order_id}")
        order = self._active[order_id]
        new_traded = order.traded_volume + fill_volume
        if new_traded >= order.volume:
            # 自动转 ALL_TRADED
            del self._active[order_id]
            updated = dataclasses.replace(
                order,
                status=OrderState.ALL_TRADED,
                traded_volume=order.volume,
            )
            self._completed[order_id] = updated
        else:
            # 不可变: 用新对象替换 _active 中的引用
            updated = dataclasses.replace(
                order,
                status=OrderState.PART_TRADED,
                traded_volume=new_traded,
            )
            self._active[order_id] = updated

    def mark_rejected(self, order_id: str, reason: str = "") -> None:
        """标记订单被拒 — 产出 REJECTED 新对象。

        Args:
            order_id: 订单ID
            reason: 拒单原因(预留,当前不存储到 OrderData)

        Raises:
            KeyError: order_id 不在活动队列中
        """
        if order_id not in self._active:
            raise KeyError(f"订单不在活动队列: {order_id}")
        order = self._active.pop(order_id)
        rejected = dataclasses.replace(order, status=OrderState.REJECTED)
        self._completed[order_id] = rejected

    # ===== 查询接口 =====

    @property
    def active_count(self) -> int:
        """活动订单数(可撮合/可撤单)。"""
        return len(self._active)

    @property
    def pending_count(self) -> int:
        """待撮合队列长度。"""
        return len(self._pending)

    @property
    def completed_count(self) -> int:
        """已终止订单数(ALL_TRADED + REJECTED)。"""
        return len(self._completed)

    @property
    def cancelled_count(self) -> int:
        """已撤单订单数。"""
        return len(self._cancelled)

    def get_order(self, order_id: str) -> Optional[OrderData]:
        """查询订单(活动/已完成/已撤单)。"""
        if order_id in self._active:
            return self._active[order_id]
        if order_id in self._completed:
            return self._completed[order_id]
        if order_id in self._cancelled:
            return self._cancelled[order_id]
        return None

    def all_active(self) -> "list[OrderData]":
        """返回所有活动订单(快照)。"""
        return list(self._active.values())

    def all_completed(self) -> "list[OrderData]":
        """返回所有已完成订单(快照)。"""
        return list(self._completed.values())

    def all_cancelled(self) -> "list[OrderData]":
        """返回所有已撤单订单(快照)。"""
        return list(self._cancelled.values())
