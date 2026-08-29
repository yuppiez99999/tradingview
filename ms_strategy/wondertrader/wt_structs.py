"""
WonderTrader 风格统一数据结构

参考 wtpy/structs.py 设计,统一全系统的 Tick/Bar/Order/Trade/Position 数据格式。
解决当前系统中字段名不一致问题(如 close/last/price 混用)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TickData:
    """Tick 行情数据(统一格式)"""
    code: str               # 标的代码,如 "510300.SH"
    exchange: str            # 交易所: SSE/SZSE/CFFEX
    price: float             # 最新价
    open: float              # 开盘价
    high: float              # 最高价
    low: float               # 最低价
    pre_close: float         # 昨收价
    volume: float            # 成交量(股/手)
    amount: float            # 成交额
    bid_prices: list[float] = field(default_factory=list)  # 买价队列
    ask_prices: list[float] = field(default_factory=list)  # 卖价队列
    bid_volumes: list[float] = field(default_factory=list)  # 买量队列
    ask_volumes: list[float] = field(default_factory=list)  # 卖量队列
    timestamp: float = 0.0   # Unix 时间戳
    datetime_str: str = ""   # "YYYY-MM-DD HH:MM:SS"
    date: int = 0            # YYYYMMDD
    time: int = 0            # HHMMSS


@dataclass
class BarData:
    """K 线 Bar 数据(统一格式)"""
    code: str
    exchange: str
    period: str              # "1m"/"5m"/"15m"/"30m"/"1h"/"1d"/"1w"
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    date: int = 0            # YYYYMMDD
    time: int = 0            # HHMMSS (日K为0)
    turnover: float = 0.0   # 换手率
    open_interest: float = 0.0  # 持仓量(期货)


@dataclass
class OrderData:
    """委托订单(统一格式)"""
    order_id: str            # 订单ID
    code: str
    exchange: str
    direction: str           # "BUY"/"SELL"
    offset: str = "OPEN"     # "OPEN"/"CLOSE"/"CLOSETODAY"
    order_type: str = "LIMIT"  # "LIMIT"/"MARKET"/"FAK"/"FOK"
    price: float = 0.0
    volume: float = 0.0
    traded_volume: float = 0.0
    status: str = "NOT_REPORTED"  # "NOT_REPORTED"/"REPORTED"/"PART_TRADED"/"ALL_TRADED"/"CANCELLED"/"REJECTED"
    timestamp: float = 0.0
    datetime_str: str = ""


@dataclass
class TradeData:
    """成交数据(统一格式)"""
    trade_id: str
    order_id: str
    code: str
    exchange: str
    direction: str           # "BUY"/"SELL"
    offset: str              # "OPEN"/"CLOSE"
    price: float
    volume: float
    amount: float            # price * volume
    timestamp: float = 0.0
    datetime_str: str = ""


@dataclass
class PositionData:
    """持仓数据(统一格式)"""
    code: str
    exchange: str
    direction: str = "LONG"  # "LONG"/"SHORT"
    volume: float = 0.0      # 总持仓
    frozen_volume: float = 0.0  # 冻结持仓(挂单中)
    available_volume: float = 0.0  # 可平持仓
    avg_price: float = 0.0   # 持仓均价
    last_price: float = 0.0  # 最新价
    position_profit: float = 0.0  # 持仓盈亏
    position_cost: float = 0.0   # 持仓成本


@dataclass
class ContractData:
    """合约规格(统一格式)"""
    code: str
    exchange: str
    name: str
    product_class: str = "STOCK"  # "STOCK"/"ETF"/"FUTURE"/"OPTION"
    contract_multiplier: float = 1.0  # 合约乘数(股票=1, 股指期货=300)
    price_tick: float = 0.01    # 最小变动价位
    margin_rate: float = 1.0   # 保证金率(股票=1.0, 期货<1.0)
    commission_rate: float = 0.0003  # 手续费率
    stamp_duty: float = 0.0    # 印花税(仅卖出)
    min_commission: float = 5.0  # 最小手续费
    volume_multiple: float = 1.0  # 数量乘数(ETF=100, 股票=1)
    listing_date: str = ""     # 上市日期
    expiry_date: str = ""      # 到期日(期货/期权)


def tick_to_dict(tick: TickData) -> dict:
    """Tick 转为字典"""
    return {
        "code": tick.code, "exchange": tick.exchange,
        "price": tick.price, "open": tick.open, "high": tick.high,
        "low": tick.low, "pre_close": tick.pre_close,
        "volume": tick.volume, "amount": tick.amount,
        "bid_prices": tick.bid_prices, "ask_prices": tick.ask_prices,
        "bid_volumes": tick.bid_volumes, "ask_volumes": tick.ask_volumes,
        "timestamp": tick.timestamp, "datetime_str": tick.datetime_str,
        "date": tick.date, "time": tick.time,
    }


def bar_to_dict(bar: BarData) -> dict:
    """Bar 转为字典"""
    return {
        "code": bar.code, "exchange": bar.exchange, "period": bar.period,
        "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
        "volume": bar.volume, "amount": bar.amount,
        "date": bar.date, "time": bar.time,
        "turnover": bar.turnover, "open_interest": bar.open_interest,
    }


__all__ = [
    "BarData",
    "ContractData",
    "OrderData",
    "PositionData",
    "TickData",
    "TradeData",
    "bar_to_dict",
    "tick_to_dict",
]
