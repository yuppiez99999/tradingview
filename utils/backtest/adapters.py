"""G15 事件驱动回测 — 适配器模块。

职责:
    - _EngineBackedHedgeContext: 把 HedgeContext 的 open_hedge/close_hedge
      从"直接改持仓"改为"提交 OrderData 到引擎队列"
    - StrategyAdapter: 把 HedgeStrategy + _EngineBackedHedgeContext 绑定,
      提供 dispatch_tick/dispatch_bar/dispatch_rebalance 回调入口

设计原则:
    - 不可变性: _EngineBackedHedgeContext.open_hedge/close_hedge 不修改
      strategy.short_positions,订单成交由引擎回调处理(Day 4)
    - 延迟接受: calc_target_hedge_volume 读取的 short_positions 可能滞后
      于在途订单,此为已知行为
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from utils.wt_hedge_strategy import HedgeContext, HedgeStrategy
from utils.wt_structs import BarData, OrderData, TickData

# 订单提交回调类型: 接受 OrderData,返回 bool (是否成功入队)
OrderSubmitter = Callable[[OrderData], bool]


class _EngineBackedHedgeContext(HedgeContext):
    """引擎绑定的对冲上下文。

    重写 open_hedge/close_hedge,将直接持仓变更替换为订单提交:
        - open_hedge → 提交 SELL OPEN 订单
        - close_hedge → 提交 BUY CLOSE 订单

    与 HedgeContext 的关键区别:
        1. 不修改 strategy.short_positions (订单在途,尚未成交)
        2. 订单提交到引擎队列,等待撮合
        3. 成交由引擎回调处理 (Day 4 实现)

    已知行为:
        calc_target_hedge_volume() 读取的 short_positions 可能滞后
        (尚未成交的挂单不计入),导致对冲量计算有微小偏差。
        这是事件驱动回测的固有特性,应在策略层面容忍。
    """

    def __init__(
        self,
        strategy: HedgeStrategy,
        order_submitter: OrderSubmitter,
    ) -> None:
        """
        Args:
            strategy: 绑定的对冲策略实例
            order_submitter: 订单提交回调,由引擎注入
        """
        super().__init__(strategy)
        self._order_submitter = order_submitter
        self._submitted_orders: list[OrderData] = []

    def open_hedge(
        self,
        hedge_code: str,
        hands: float,
        price: float | None = None,
    ) -> bool:
        """开空对冲 — 提交 SELL OPEN 订单到引擎。

        与父类 HedgeContext.open_hedge() 不同:
            - 不调用 add_short_position (不在此处修改持仓)
            - 创建 OrderData 并通过 order_submitter 提交
            - 记录到 self._submitted_orders 供审计

        Args:
            hedge_code: 对冲工具代码 (如 "IF.CFFEX")
            hands: 对冲手数 (必须 > 0)
            price: 委托价格 (None 时用当前市价)

        Returns:
            True: 订单成功入队
            False: 参数无效或入队失败
        """
        if hands <= 0:
            return False

        hedge_price = price or self.current_prices.get(hedge_code, 0)
        if hedge_price <= 0:
            return False

        order = self._create_hedge_order(
            code=hedge_code,
            direction="SELL",
            offset="OPEN",
            volume=hands,
            price=hedge_price,
        )

        success = self._order_submitter(order)
        if success:
            self._submitted_orders.append(order)
            self.hedge_orders.append(
                {
                    "action": "OPEN_SHORT",
                    "code": hedge_code,
                    "hands": hands,
                    "price": hedge_price,
                    "order_id": order.order_id,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        return success

    def close_hedge(
        self,
        hedge_code: str,
        hands: float,
        price: float | None = None,
    ) -> bool:
        """平空对冲 — 提交 BUY CLOSE 订单到引擎。

        与父类 HedgeContext.close_hedge() 不同:
            - 不直接减少 strategy.short_positions 中的 volume
            - 创建 OrderData 并通过 order_submitter 提交

        Args:
            hedge_code: 对冲工具代码
            hands: 平空手数 (必须 > 0,由 adjust_hedge 取绝对值传入)
            price: 委托价格

        Returns:
            True: 订单成功入队
            False: 参数无效或入队失败
        """
        if hands <= 0:
            return False

        hedge_price = price or self.current_prices.get(hedge_code, 0)
        if hedge_price <= 0:
            return False

        order = self._create_hedge_order(
            code=hedge_code,
            direction="BUY",
            offset="CLOSE",
            volume=hands,
            price=hedge_price,
        )

        success = self._order_submitter(order)
        if success:
            self._submitted_orders.append(order)
            self.hedge_orders.append(
                {
                    "action": "CLOSE_SHORT",
                    "code": hedge_code,
                    "hands": hands,
                    "price": hedge_price,
                    "order_id": order.order_id,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        return success

    def get_submitted_orders(self) -> list[OrderData]:
        """获取已提交订单列表(供审计/测试)。"""
        return list(self._submitted_orders)

    @staticmethod
    def _create_hedge_order(
        code: str,
        direction: str,
        offset: str,
        volume: float,
        price: float,
    ) -> OrderData:
        """创建对冲订单(静态方法,纯函数)。

        Args:
            code: 合约代码
            direction: "BUY" / "SELL"
            offset: "OPEN" / "CLOSE"
            volume: 手数
            price: 委托价

        Returns:
            新的 OrderData 实例
        """
        order_id = f"hedge_{uuid.uuid4().hex[:12]}"
        # 提取交易所后缀 (如 "IF.CFFEX" → "CFFEX")
        exchange = code.split(".")[-1] if "." in code else "UNKNOWN"

        return OrderData(
            order_id=order_id,
            code=code,
            exchange=exchange,
            direction=direction,
            offset=offset,
            order_type="LIMIT",
            price=price,
            volume=volume,
            timestamp=datetime.now().timestamp(),
            datetime_str=datetime.now().isoformat(),
        )


class StrategyAdapter:
    """策略适配器 — 绑定 HedgeStrategy + _EngineBackedHedgeContext。

    将外部事件 (TickData/BarData) 分发到策略的回调方法:
        - dispatch_tick(tick) → strategy.on_tick(ctx, tick)
        - dispatch_bar(bar)   → strategy.on_bar(ctx, bar)
        - dispatch_rebalance() → strategy.on_rebalance(ctx)

    职责:
        1. 管理 context.current_prices (从市场事件更新)
        2. 调用策略回调
        3. 返回 context 供审计

    使用示例:
        strategy = BetaHedgeStrategy(config)
        adapter = StrategyAdapter(strategy, order_submitter=engine.enqueue)

        for bar in feed:
            adapter.dispatch_bar(bar)
            engine.process_bar(bar)  # 撮合订单
        adapter.dispatch_rebalance()  # 触发对冲调整
    """

    def __init__(
        self,
        strategy: HedgeStrategy,
        order_submitter: OrderSubmitter,
    ) -> None:
        """
        Args:
            strategy: 对冲策略实例
            order_submitter: 订单提交回调 (由引擎注入)
        """
        self.strategy = strategy
        self.context = _EngineBackedHedgeContext(strategy, order_submitter)

    def dispatch_tick(self, tick: TickData) -> None:
        """分发 TickData 到策略回调。

        更新 context.current_prices 后调用 strategy.on_tick(ctx, tick)。
        """
        self.context.current_prices[tick.code] = tick.price
        self.strategy.on_tick(self.context, tick)

    def dispatch_bar(self, bar: BarData) -> None:
        """分发 BarData 到策略回调。

        更新 context.current_prices 后调用 strategy.on_bar(ctx, bar)。
        """
        self.context.current_prices[bar.code] = bar.close
        self.strategy.on_bar(self.context, bar)

    def dispatch_rebalance(self) -> None:
        """触发再平衡回调 (不依赖市场事件)。

        通常在 EOD 或特定时间点调用。
        """
        self.strategy.on_rebalance(self.context)

    def update_prices(self, prices: dict[str, float]) -> None:
        """批量更新价格 (供外部调用)。

        Args:
            prices: {code: price} 字典
        """
        self.context.current_prices.update(prices)
