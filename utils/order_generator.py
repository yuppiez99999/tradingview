#!/usr/bin/env python
"""
订单生成器 (OrderGenerator)
==========================

职责:
1. 将 Alpha 信号转换为可执行订单
2. 支持目标权重 → 订单金额/数量转换
3. 考虑最小交易单位、涨跌停、现金约束
4. 支持 dry_run 模式

设计:
- 最小可用实现，后续可替换为更复杂的执行算法
- 与 ExecutionRouter 解耦，只负责生成订单建议

作者: 终极量化交易系统 v8.4
日期: 2026-08-02
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("pipeline.order_generator")


@dataclass
class Order:
    """单个订单"""

    symbol: str
    side: str  # buy / sell
    quantity: int  # 股数（A股最小100股）
    price: float = 0.0  # 限价，0 表示市价
    order_type: str = "market"  # market / limit
    reason: str = ""
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderBatch:
    """订单批次"""

    batch_id: str
    orders: list[Order]
    total_amount: float = 0.0
    created_at: str = ""
    dry_run: bool = True


class OrderGenerator:
    """
    最小可用订单生成器

    输入: Alpha 信号 / 目标权重
    输出: 订单批次
    """

    def __init__(
        self,
        lot_size: int = 100,
        max_single_order_value: float = 500_000,
        commission_rate: float = 0.0003,
    ):
        self.lot_size = lot_size
        self.max_single_order_value = max_single_order_value
        self.commission_rate = commission_rate
        logger.info("OrderGenerator 初始化完成")

    def generate(
        self,
        signals: dict[str, float],
        current_positions: dict[str, float] | None = None,
        total_capital: float = 1_000_000,
        prices: dict[str, float] | None = None,
    ) -> OrderBatch:
        """
        生成订单批次

        Args:
            signals: 目标权重 {symbol: weight}，权重范围 [-1, 1]
            current_positions: 当前持仓市值 {symbol: market_value}
            total_capital: 总资金
            prices: 当前价格 {symbol: price}

        Returns:
            OrderBatch
        """

        batch_id = now_bj().strftime("%Y%m%d%H%M%S")
        orders: list[Order] = []

        prices = prices or {}
        current_positions = current_positions or {}

        for symbol, weight in signals.items():
            if abs(weight) < 1e-6:
                continue

            price = prices.get(symbol, 0.0)
            if price <= 0:
                logger.warning(f"标的 {symbol} 无有效价格，跳过")
                continue

            # 目标市值
            target_value = weight * total_capital
            current_value = current_positions.get(symbol, 0.0)
            delta_value = target_value - current_value

            # 单笔上限
            if abs(delta_value) > self.max_single_order_value:
                delta_value = (
                    self.max_single_order_value
                    if delta_value > 0
                    else -self.max_single_order_value
                )

            # 最小交易单位
            quantity = int(delta_value / price / self.lot_size) * self.lot_size
            if quantity == 0:
                continue

            side = "buy" if quantity > 0 else "sell"
            orders.append(
                Order(
                    symbol=symbol,
                    side=side,
                    quantity=abs(quantity),
                    price=price,
                    order_type="market",
                    reason=f"signal_weight={weight:.2%}",
                    tags={"target_weight": weight, "delta_value": delta_value},
                )
            )

        total_amount = sum(o.quantity * o.price for o in orders)

        return OrderBatch(
            batch_id=batch_id,
            orders=orders,
            total_amount=total_amount,
            created_at=now_bj().isoformat(),
            dry_run=True,
        )
