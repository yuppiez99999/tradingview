"""G15 事件驱动回测 — 延迟模型模块。

模拟交易系统的延迟行为,用于:
    - 策略提交订单后,订单并非立即撮合,而是等待 N 个事件(tick/bar)
    - 模拟真实交易中的网络延迟、排队延迟等

延迟单位为"事件数"(tick/bar),非墙钟秒——回测中无墙钟。

三种模型:
    FixedLatency:   固定延迟,与向量化对比验证用
    RandomLatency:  均匀分布随机延迟,可复现(固定 seed)
    QueueLatency:   基于队列积压的动态延迟

设计原则(AGENTS.md):
    - 不可变性: calculate_latency 为纯函数,不修改入参
    - 单一职责: 只计算延迟,不管理订单生命周期
    - 可复现: RandomLatency 使用 per-instance Random(seed),不污染全局状态
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Optional, Union

from utils.wt_structs import BarData, OrderData, TickData

MarketEvent = Union[TickData, BarData]


class LatencyModel(ABC):
    """延迟模型基类。

    所有延迟模型都实现 calculate_latency 方法,返回订单需要等待的事件数。
    """

    @abstractmethod
    def calculate_latency(
        self,
        order: OrderData,
        pending_count: int = 0,
        market_event: Optional[MarketEvent] = None,
    ) -> int:
        """计算订单延迟(事件数)。

        Args:
            order: 待撮合订单
            pending_count: 队列中待处理订单数(用于 QueueLatency)
            market_event: 当前市场事件(预留,未来可扩展不同事件类型的延迟)

        Returns:
            延迟事件数(非负整数)
        """
        ...


class FixedLatency(LatencyModel):
    """固定延迟模型 — 总是返回相同的延迟值。

    用途: 与向量化回测对比验证时使用 latency_ticks=0,
          模拟"即时成交"的理想情况。

    Args:
        latency_ticks: 固定延迟事件数(≥0)
    """

    def __init__(self, latency_ticks: int = 0) -> None:
        self.latency_ticks = max(0, latency_ticks)

    def calculate_latency(
        self,
        order: OrderData,
        pending_count: int = 0,
        market_event: Optional[MarketEvent] = None,
    ) -> int:
        """返回固定的 latency_ticks,忽略其他参数。"""
        return self.latency_ticks


class RandomLatency(LatencyModel):
    """随机延迟模型 — 均匀分布,可复现。

    使用 per-instance random.Random(seed) 确保:
        - 测试可复现(固定 seed)
        - 不污染全局 random 模块状态
        - 多实例独立

    Args:
        min_ticks: 最小延迟事件数(≥0)
        max_ticks: 最大延迟事件数(≥min_ticks)
        seed: 随机种子,用于可复现性
    """

    def __init__(self, min_ticks: int = 1, max_ticks: int = 3, seed: int = 42) -> None:
        self.min_ticks = max(0, min_ticks)
        self.max_ticks = max(self.min_ticks, max_ticks)
        self._rng = random.Random(seed)

    def calculate_latency(
        self,
        order: OrderData,
        pending_count: int = 0,
        market_event: Optional[MarketEvent] = None,
    ) -> int:
        """返回 [min_ticks, max_ticks] 范围内的随机整数。"""
        return self._rng.randint(self.min_ticks, self.max_ticks)


class QueueLatency(LatencyModel):
    """队列延迟模型 — 基于积压订单数的动态延迟。

    延迟公式:
        latency = clamp(base_ticks + per_pending_order_ticks * pending_count,
                        base_ticks, max_ticks)

    用途: 模拟真实交易系统中,订单队列积压导致的延迟增加。

    Args:
        base_ticks: 基础延迟事件数(≥0)
        per_pending_order_ticks: 每个积压订单的额外延迟(≥0,支持小数会向上取整)
        max_ticks: 最大延迟事件数(≥base_ticks)
    """

    def __init__(
        self,
        base_ticks: int = 1,
        per_pending_order_ticks: float = 0.5,
        max_ticks: int = 10,
    ) -> None:
        self.base_ticks = max(0, base_ticks)
        self.per_pending_order_ticks = max(0.0, per_pending_order_ticks)
        self.max_ticks = max(self.base_ticks, max_ticks)

    def calculate_latency(
        self,
        order: OrderData,
        pending_count: int = 0,
        market_event: Optional[MarketEvent] = None,
    ) -> int:
        """计算基于队列积压的延迟,clamp 到 [base_ticks, max_ticks]。"""
        raw_latency = self.base_ticks + self.per_pending_order_ticks * pending_count
        clamped = max(self.base_ticks, min(int(raw_latency), self.max_ticks))
        return clamped


__all__ = [
    "LatencyModel",
    "FixedLatency",
    "RandomLatency",
    "QueueLatency",
]
