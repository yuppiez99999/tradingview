"""G15 事件驱动回测 — 撮合引擎模块。

支持三种撮合模式:
    TICK:   基于 TickData 五档 bid/ask 撮合,支持部分成交(消费五档流动性)
    BAR:    基于 BarData OHLC 撮合,与向量化回测口径对齐
    HYBRID: 自动选择(TickData→TICK 模式, BarData→BAR 模式)

撮合规则:
    限价买: order.price >= ask[0](TICK) 或 order.price >= bar.low(BAR)
    限价卖: order.price <= bid[0](TICK) 或 order.price <= bar.high(BAR)
    市价:   立即按 ask[0]/bid[0](TICK) 或 bar.open(BAR) 成交
    部分成交:
        TICK: 消费 ask_volumes[0..4] 五档
        BAR:  fill_volume = min(order.volume, bar.volume × participation_rate)

订单类型:
    LIMIT:  限价单,可部分成交(若 allow_partial_fill=True)
    MARKET: 市价单,立即成交
    FAK:    Fill-And-Kill,部分成交后剩余撤单
    FOK:    Fill-Or-Kill,必须全部成交否则全部撤单

设计原则(AGENTS.md):
    - 不可变性(§5.1): 不修改入参 order 对象
    - 单一职责: 只撮合,不管订单生命周期(由 OrderQueue 管)
    - 复用数据类: 使用 utils.wt_structs.TickData/BarData/OrderData
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Union

from utils.backtest.constraints import check_tradable
from utils.wt_structs import BarData, OrderData, TickData

MarketEvent = Union[TickData, BarData]


class MatchingMode(StrEnum):
    """撮合模式。"""
    TICK = "TICK"
    BAR = "BAR"
    HYBRID = "HYBRID"


class OrderType:
    """订单类型常量(与 OrderData.order_type 字符串对齐)。"""
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    FAK = "FAK"  # Fill And Kill
    FOK = "FOK"  # Fill Or Kill


class RejectReason:
    """撮合拒绝原因常量。"""
    SUSPENDED = "REJECTED_SUSPENDED"
    LIMIT_UP = "REJECTED_LIMIT_UP"
    LIMIT_DOWN = "REJECTED_LIMIT_DOWN"
    NO_LIQUIDITY = "REJECTED_NO_LIQUIDITY"
    NO_FULL_LIQUIDITY = "REJECTED_NO_FULL_LIQUIDITY"
    UNKNOWN_EVENT = "REJECTED_UNKNOWN_EVENT"


class FillReason:
    """成交原因常量。"""
    FULL_MATCH = "FULL_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"


@dataclass
class FillEvent:
    """撮合结果事件(不可变)。

    无论成交/部分成交/拒绝,都产出 FillEvent 供日志和审计。
    """
    order_id: str
    fill_price: float
    fill_volume: float
    is_partial: bool
    reason: str


class MatchingEngine:
    """撮合引擎 — 限价单/市价单撮合,基于 tick 或 bar。

    不可变性保证:
        - match(orders, ...): 不修改入参 orders 列表或其中的 OrderData 对象
        - 所有回调(on_fill/on_partial_fill/on_reject)接收的是原 order 引用,
          引擎不就地修改 order.status

    线程安全:
        非线程安全(回测为单线程)
    """

    def __init__(
        self,
        mode: str = "BAR",
        allow_partial_fill: bool = True,
        max_participation_rate: float = 0.10,
        enforce_price_limit: bool = True,
    ) -> None:
        """
        Args:
            mode: 撮合模式 "TICK"/"BAR"/"HYBRID"
            allow_partial_fill: 是否允许部分成交;False 时订单要么全成要么不成交
            max_participation_rate: 单标的单 bar 最大成交量占 bar.volume 比例
            enforce_price_limit: 是否启用涨跌停/停牌约束
        """
        self.mode = MatchingMode(mode)
        self.allow_partial_fill = allow_partial_fill
        self.max_participation_rate = max_participation_rate
        self.enforce_price_limit = enforce_price_limit

    def match(
        self,
        orders: list[OrderData],
        market_event: MarketEvent,
        contracts: Optional[object] = None,
        on_fill: Optional[Callable[[OrderData, float, float], None]] = None,
        on_partial_fill: Optional[Callable[[OrderData, float, float], None]] = None,
        on_reject: Optional[Callable[[OrderData, str], None]] = None,
        limit_up_prices: Optional[dict] = None,
        limit_down_prices: Optional[dict] = None,
    ) -> list[FillEvent]:
        """对一批订单逐一撮合。

        Args:
            orders: 待撮合订单列表(不修改)
            market_event: 当前市场事件(TickData/BarData)
            contracts: 合约管理器(预留,当前未使用)
            on_fill: 全部成交回调 (order, fill_price, fill_volume) -> None
            on_partial_fill: 部分成交回调 (order, fill_price, fill_volume) -> None
            on_reject: 拒单回调 (order, reason) -> None
            limit_up_prices: 涨停价字典 {code: price}
            limit_down_prices: 跌停价字典 {code: price}

        Returns:
            FillEvent 列表(每个订单一个,顺序与 orders 一致)
        """
        events: list[FillEvent] = []
        for order in orders:
            event = self._match_single(
                order, market_event, contracts,
                on_fill, on_partial_fill, on_reject,
                limit_up_prices, limit_down_prices,
            )
            if event is not None:
                events.append(event)
        return events

    def _match_single(
        self,
        order: OrderData,
        market_event: MarketEvent,
        contracts: Optional[object],
        on_fill: Optional[Callable],
        on_partial_fill: Optional[Callable],
        on_reject: Optional[Callable],
        limit_up_prices: Optional[dict],
        limit_down_prices: Optional[dict],
    ) -> Optional[FillEvent]:
        """撮合单个订单。"""
        # 1. 涨跌停/停牌约束检查
        if self.enforce_price_limit:
            tradable, reason = check_tradable(
                market_event, order.code, order.direction,
                limit_up_prices, limit_down_prices,
            )
            if not tradable:
                reject_reason = self._reason_to_reject(reason)
                if on_reject is not None:
                    on_reject(order, reject_reason)
                return FillEvent(order.order_id, 0.0, 0.0, False, reject_reason)

        # 2. 根据模式分发
        if self.mode == MatchingMode.HYBRID:
            # HYBRID: 自动按 event 类型选择
            if isinstance(market_event, TickData) and market_event.ask_prices:
                return self._match_tick(order, market_event, on_fill, on_partial_fill, on_reject)
            elif isinstance(market_event, BarData):
                return self._match_bar(order, market_event, on_fill, on_partial_fill, on_reject)
            else:
                return self._reject_unknown(order, on_reject)
        elif self.mode == MatchingMode.TICK:
            if not isinstance(market_event, TickData):
                return self._reject_unknown(order, on_reject)
            return self._match_tick(order, market_event, on_fill, on_partial_fill, on_reject)
        elif self.mode == MatchingMode.BAR:
            if not isinstance(market_event, BarData):
                return self._reject_unknown(order, on_reject)
            return self._match_bar(order, market_event, on_fill, on_partial_fill, on_reject)
        return self._reject_unknown(order, on_reject)

    # ============================================================
    # TICK 模式撮合 — 基于五档 bid/ask
    # ============================================================

    def _match_tick(
        self,
        order: OrderData,
        tick: TickData,
        on_fill: Optional[Callable],
        on_partial_fill: Optional[Callable],
        on_reject: Optional[Callable],
    ) -> Optional[FillEvent]:
        """TICK 模式撮合: 消费五档 bid/ask。"""
        if order.direction == "BUY":
            return self._match_tick_buy(order, tick, on_fill, on_partial_fill, on_reject)
        elif order.direction == "SELL":
            return self._match_tick_sell(order, tick, on_fill, on_partial_fill, on_reject)
        return self._reject_unknown(order, on_reject)

    def _match_tick_buy(
        self, order: OrderData, tick: TickData,
        on_fill: Optional[Callable[[OrderData, float, float], None]],
        on_partial_fill: Optional[Callable[[OrderData, float, float], None]],
        on_reject: Optional[Callable[[OrderData, str], None]],
    ) -> Optional[FillEvent]:
        """TICK 买入撮合: 消费 ask_prices/ask_volumes。"""
        if not tick.ask_prices or not tick.ask_volumes:
            return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)

        # 消费五档
        fill_volume = 0.0
        fill_value = 0.0
        remaining = order.volume

        for price, vol in zip(tick.ask_prices, tick.ask_volumes):
            if remaining <= 0 or vol <= 0:
                break
            # 限价单: 出价低于该档价位则停止
            if order.order_type == OrderType.LIMIT and order.price < price:
                break
            matched = min(remaining, vol)
            fill_volume += matched
            fill_value += price * matched
            remaining -= matched

        if fill_volume <= 0:
            # 无流动性可成交;限价单等待,市价/FAK/FOK 拒单
            if order.order_type in (OrderType.MARKET, OrderType.FAK, OrderType.FOK):
                return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)
            return None  # 限价单挂起,等待后续 tick

        avg_price = fill_value / fill_volume
        return self._finalize_fill(
            order, avg_price, fill_volume, on_fill, on_partial_fill, on_reject,
        )

    def _match_tick_sell(
        self, order: OrderData, tick: TickData,
        on_fill: Optional[Callable[[OrderData, float, float], None]],
        on_partial_fill: Optional[Callable[[OrderData, float, float], None]],
        on_reject: Optional[Callable[[OrderData, str], None]],
    ) -> Optional[FillEvent]:
        """TICK 卖出撮合: 消费 bid_prices/bid_volumes。"""
        if not tick.bid_prices or not tick.bid_volumes:
            return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)

        fill_volume = 0.0
        fill_value = 0.0
        remaining = order.volume

        for price, vol in zip(tick.bid_prices, tick.bid_volumes):
            if remaining <= 0 or vol <= 0:
                break
            # 限价单: 要价高于该档价位则停止
            if order.order_type == OrderType.LIMIT and order.price > price:
                break
            matched = min(remaining, vol)
            fill_volume += matched
            fill_value += price * matched
            remaining -= matched

        if fill_volume <= 0:
            if order.order_type in (OrderType.MARKET, OrderType.FAK, OrderType.FOK):
                return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)
            return None

        avg_price = fill_value / fill_volume
        return self._finalize_fill(
            order, avg_price, fill_volume, on_fill, on_partial_fill, on_reject,
        )

    # ============================================================
    # BAR 模式撮合 — 基于 OHLC
    # ============================================================

    def _match_bar(
        self,
        order: OrderData,
        bar: BarData,
        on_fill: Optional[Callable],
        on_partial_fill: Optional[Callable],
        on_reject: Optional[Callable],
    ) -> Optional[FillEvent]:
        """BAR 模式撮合: 基于 [low, high] 区间。"""
        if order.direction == "BUY":
            return self._match_bar_buy(order, bar, on_fill, on_partial_fill, on_reject)
        elif order.direction == "SELL":
            return self._match_bar_sell(order, bar, on_fill, on_partial_fill, on_reject)
        return self._reject_unknown(order, on_reject)

    def _match_bar_buy(
        self, order: OrderData, bar: BarData,
        on_fill: Optional[Callable[[OrderData, float, float], None]],
        on_partial_fill: Optional[Callable[[OrderData, float, float], None]],
        on_reject: Optional[Callable[[OrderData, str], None]],
    ) -> Optional[FillEvent]:
        """BAR 买入撮合: order.price >= bar.low 则成交于 max(order.price, bar.open)。"""
        # 限价单: 出价低于 bar.low 则不成交
        if order.order_type == OrderType.LIMIT and order.price < bar.low:
            return None  # 挂起等待

        # 成交价: 限价单 max(order.price, bar.open), 市价单 bar.open
        if order.order_type == OrderType.MARKET:
            fill_price = bar.open
        else:
            fill_price = max(order.price, bar.open)

        # 部分成交: max_participation_rate 限制
        max_volume = bar.volume * self.max_participation_rate
        fill_volume = min(order.volume, max_volume)

        if fill_volume <= 0:
            return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)

        return self._finalize_fill(
            order, fill_price, fill_volume, on_fill, on_partial_fill, on_reject,
        )

    def _match_bar_sell(
        self, order: OrderData, bar: BarData,
        on_fill: Optional[Callable[[OrderData, float, float], None]],
        on_partial_fill: Optional[Callable[[OrderData, float, float], None]],
        on_reject: Optional[Callable[[OrderData, str], None]],
    ) -> Optional[FillEvent]:
        """BAR 卖出撮合: order.price <= bar.high 则成交于 min(order.price, bar.open)。"""
        # 限价单: 要价高于 bar.high 则不成交
        if order.order_type == OrderType.LIMIT and order.price > bar.high:
            return None

        if order.order_type == OrderType.MARKET:
            fill_price = bar.open
        else:
            fill_price = min(order.price, bar.open)

        max_volume = bar.volume * self.max_participation_rate
        fill_volume = min(order.volume, max_volume)

        if fill_volume <= 0:
            return self._reject(order, on_reject, RejectReason.NO_LIQUIDITY)

        return self._finalize_fill(
            order, fill_price, fill_volume, on_fill, on_partial_fill, on_reject,
        )

    # ============================================================
    # 成交收尾 — 统一处理部分成交/FOK/FAK/回调
    # ============================================================

    def _finalize_fill(
        self,
        order: OrderData,
        fill_price: float,
        fill_volume: float,
        on_fill: Optional[Callable],
        on_partial_fill: Optional[Callable],
        on_reject: Optional[Callable],
    ) -> FillEvent:
        """统一处理成交结果: 全成/部分成/FOK 拒单/FAK 部分成。"""
        is_partial = fill_volume < order.volume

        # FOK: 必须全部成交,否则拒单
        if order.order_type == OrderType.FOK and is_partial:
            return self._reject(order, on_reject, RejectReason.NO_FULL_LIQUIDITY)

        # 不允许部分成交 + 实际部分成交 → 拒单
        if is_partial and not self.allow_partial_fill:
            return self._reject(order, on_reject, RejectReason.NO_FULL_LIQUIDITY)

        # 触发回调
        if is_partial:
            if on_partial_fill is not None:
                on_partial_fill(order, fill_price, fill_volume)
            return FillEvent(order.order_id, fill_price, fill_volume, True, FillReason.PARTIAL_MATCH)
        else:
            if on_fill is not None:
                on_fill(order, fill_price, fill_volume)
            return FillEvent(order.order_id, fill_price, fill_volume, False, FillReason.FULL_MATCH)

    # ============================================================
    # 工具方法
    # ============================================================

    def _reject(
        self,
        order: OrderData,
        on_reject: Optional[Callable],
        reason: str,
    ) -> FillEvent:
        """统一拒单处理。"""
        if on_reject is not None:
            on_reject(order, reason)
        return FillEvent(order.order_id, 0.0, 0.0, False, reason)

    def _reject_unknown(
        self,
        order: OrderData,
        on_reject: Optional[Callable],
    ) -> FillEvent:
        """未知事件类型拒单。"""
        return self._reject(order, on_reject, RejectReason.UNKNOWN_EVENT)

    @staticmethod
    def _reason_to_reject(check_reason: str) -> str:
        """check_tradable 返回的 reason → RejectReason 映射。"""
        mapping = {
            "SUSPENDED": RejectReason.SUSPENDED,
            "PRICE_LIMIT_UP": RejectReason.LIMIT_UP,
            "PRICE_LIMIT_DOWN": RejectReason.LIMIT_DOWN,
        }
        return mapping.get(check_reason, RejectReason.UNKNOWN_EVENT)
