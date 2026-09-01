"""G15 事件驱动回测 — 涨跌停/停牌约束工具。

从 utils.wt_backtest_engine.BacktestEngine._is_suspended (L181) 提取算法,
适配 TickData/BarData 对象(原算法基于 dict)。

不修改原 BacktestEngine,作为独立工具函数供事件驱动引擎使用。

设计原则(AGENTS.md):
    - 单一职责: 只判断约束,不撮合、不下单
    - 复用数据类: 接受 utils.wt_structs.TickData/BarData
    - 不可变: 纯函数,无副作用
    - 向后兼容: limit_up_prices/limit_down_prices 为 None 时不约束(与原行为一致)

约束规则:
    停牌: Tick/Bar 的 volume <= 0 且 amount <= 0 → 不可交易
    涨停: 当前价 >= 涨停价 → 不可买入(可卖出)
    跌停: 当前价 <= 跌停价 → 不可卖出(可买入)
"""

from __future__ import annotations

from typing import Union

from utils.wt_structs import BarData, TickData

MarketEvent = Union[TickData, BarData]  # noqa: UP007  # 运行时类型别名, py38 兼容


def _get_current_price(market_event: MarketEvent) -> float:
    """从 TickData/BarData 提取当前价。

    TickData 用 .price(最新价),BarData 用 .close(收盘价)。
    """
    if hasattr(market_event, "price"):
        return float(market_event.price)
    if hasattr(market_event, "close"):
        return float(market_event.close)
    return 0.0


def is_suspended(market_event: MarketEvent, code: str) -> bool:
    """判断标的当日是否停牌。

    判定规则(与 wt_backtest_engine._is_suspended 一致):
        Tick/Bar 的 volume <= 0 且 amount <= 0 → 停牌

    停牌标的不可交易,持仓估值冻结(用上一收盘价)。

    Args:
        market_event: TickData 或 BarData
        code: 标的代码(预留,当前未使用,因 Tick/Bar 已含 code 字段)

    Returns:
        True: 停牌
        False: 正常交易
    """
    volume = getattr(market_event, "volume", 0) or 0
    amount = getattr(market_event, "amount", 0) or 0
    return float(volume) <= 0 and float(amount) <= 0


def is_at_limit(
    market_event: MarketEvent,
    code: str,
    direction: str,
    limit_up_prices: dict | None = None,
    limit_down_prices: dict | None = None,
) -> bool:
    """判断该方向的交易是否被涨跌停限制。

    规则(与 wt_backtest_engine.run L244-255 一致):
        买入: 当前价 >= 涨停价 → 涨停,不可买入
        卖出: 当前价 <= 跌停价 → 跌停,不可卖出

    未提供 limit_up_prices/limit_down_prices 时向后兼容(不约束),
    与原 BacktestEngine 行为一致。

    Args:
        market_event: TickData 或 BarData
        code: 标的代码
        direction: "BUY" 或 "SELL"
        limit_up_prices: 涨停价字典 {code: price},None/空表示无约束
        limit_down_prices: 跌停价字典 {code: price},None/空表示无约束

    Returns:
        True: 该方向交易被涨跌停限制(应 REJECTED)
        False: 可交易
    """
    if direction == "BUY":
        if not limit_up_prices:
            return False
        lu = limit_up_prices.get(code)
        if lu is None:
            return False
        price = _get_current_price(market_event)
        return price >= float(lu)
    if direction == "SELL":
        if not limit_down_prices:
            return False
        ld = limit_down_prices.get(code)
        if ld is None:
            return False
        price = _get_current_price(market_event)
        return price <= float(ld)
    return False


def check_tradable(
    market_event: MarketEvent,
    code: str,
    direction: str,
    limit_up_prices: dict | None = None,
    limit_down_prices: dict | None = None,
) -> tuple[bool, str]:
    """综合可交易性检查(停牌 + 涨跌停)。

    Args:
        同 is_at_limit

    Returns:
        (tradable, reason)
        tradable=True: 可交易, reason="OK"
        tradable=False: 不可交易, reason 为 "SUSPENDED" / "PRICE_LIMIT_UP" / "PRICE_LIMIT_DOWN"
    """
    if is_suspended(market_event, code):
        return False, "SUSPENDED"
    if direction == "BUY" and is_at_limit(
        market_event, code, "BUY", limit_up_prices, limit_down_prices
    ):
        return False, "PRICE_LIMIT_UP"
    if direction == "SELL" and is_at_limit(
        market_event, code, "SELL", limit_up_prices, limit_down_prices
    ):
        return False, "PRICE_LIMIT_DOWN"
    return True, "OK"
