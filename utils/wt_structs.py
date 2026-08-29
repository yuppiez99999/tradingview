"""
WonderTrader 风格统一数据结构

参考 wtpy/structs.py 设计,统一全系统的 Tick/Bar/Order/Trade/Position 数据格式。
解决当前系统中字段名不一致问题(如 close/last/price 混用)。
"""

from __future__ import annotations

import os
import warnings as _warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from utils.contracts.symbols import SymbolParseError, normalize_exchange

# ============================================================
# W6.3.3 Step 4: code/exchange 一致性校验
#
# 动机 (cairn/w633_secid_contract_parsing_challenges.md §4):
#   每个数据类都有 code + exchange 两个独立字段, 但无运行时校验保证两者一致。
#   撮合引擎/风控引擎用 order.code == event.code 裸串比较, 若构造事件时一方写裸码
#   一方写 Wind 码, 会静默不匹配 (订单被跳过) → 组合权益失真。
#
# 门禁范式 (与 W6.3.2 NonMonotonicTimestampError / symbols.SymbolParseError 同源):
#   默认 strict=False 仅 RuntimeWarning, 不阻断 (向后兼容);
#   strict=True 时抛 CodeExchangeMismatchError 不降级 (防前视偏差硬门禁)。
# ============================================================


class CodeExchangeMismatchWarning(RuntimeWarning):
    """code 字段 Wind 后缀与 exchange 字段不一致警告 (默认行为, 不阻断)。

    场景: code="600519.SH" 但 exchange="SZSE" → 撮合引擎 order.code == event.code
    裸串比较虽匹配, 但语义错误 (跨市场代码); 默认仅 warning, strict 模式抛
    CodeExchangeMismatchError。

    独立 Warning 子类便于上层用 warnings.simplefilter("error", ...) 精准升级。
    """


class CodeExchangeMismatchError(ValueError):
    """code 字段 Wind 后缀与 exchange 字段不一致 (strict 模式抛出, 不降级)。

    与 utils.contracts.symbols.SymbolParseError 同属"防前视偏差硬门禁"范式:
    错误的 code/exchange 组合可能导致撮合引擎/风控引擎静默跳过订单。

    Attributes:
        code: code 字段值
        exchange: exchange 字段值
        expected_exchange: 由 code 后缀规范化的预期交易所
        cls_name: 触发校验的数据类名
    """

    def __init__(
        self,
        code: str,
        exchange: str,
        expected_exchange: str,
        cls_name: str,
    ) -> None:
        self.code = code
        self.exchange = exchange
        self.expected_exchange = expected_exchange
        self.cls_name = cls_name
        super().__init__(
            f"{cls_name}: code={code!r} 后缀暗示交易所 {expected_exchange!r}, "
            f"但 exchange={exchange!r}; "
            f"撮合引擎 order.code == event.code 比较可能不匹配"
        )


# strict 模式开关
#   默认 False (仅 warning, 向后兼容)
#   环境变量 WT_STRUCTS_STRICT_SYMBOL=1/true/yes/on 启用 (生产 hot path 临时收紧)
#   strict_symbol_validation() 上下文管理器用于测试与临时收紧/放宽
_STRICT_VALIDATION: bool = os.environ.get("WT_STRUCTS_STRICT_SYMBOL", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


@contextmanager
def strict_symbol_validation(enabled: bool = True) -> Iterator[None]:
    """临时启用/禁用 strict 校验 (上下文管理器)。

    用法 1 — 测试中临时启用 strict:
        with strict_symbol_validation():
            TickData(code="600519.SH", exchange="SZSE", ...)  # 抛 CodeExchangeMismatchError

    用法 2 — 批量构造历史数据时临时关闭:
        with strict_symbol_validation(enabled=False):
            ...
    """
    global _STRICT_VALIDATION
    prev = _STRICT_VALIDATION
    _STRICT_VALIDATION = enabled
    try:
        yield
    finally:
        _STRICT_VALIDATION = prev


def is_strict_symbol_validation() -> bool:
    """查询当前 strict 校验开关状态。"""
    return _STRICT_VALIDATION


def _validate_code_exchange(code: str, exchange: str, cls_name: str) -> None:
    """校验 code 字段的 Wind 后缀与 exchange 字段一致。

    跳过条件 (向后兼容, 不阻断既有调用):
        - code 为空或不含 "." (裸码 "600519")
        - exchange 为空或为 "UNKNOWN" (adapters.py 退化场景)
        - code 后缀不是已知交易所后缀 (".FOO") — 交由 parse_symbol strict 模式负责

    触发条件:
        - code 后缀规范化的交易所 ≠ exchange 字段 (大小写不敏感)
        - 默认 RuntimeWarning; strict 模式抛 CodeExchangeMismatchError
    """
    if not code or "." not in code:
        return
    if not exchange or exchange.upper() == "UNKNOWN":
        return
    suffix = code.rsplit(".", 1)[-1].upper()
    try:
        expected = normalize_exchange(suffix)
    except SymbolParseError:
        return  # 未知后缀, 不校验 (避免误报)
    if exchange.upper() != expected:
        msg = (
            f"{cls_name}: code={code!r} 后缀 {suffix!r} 暗示交易所 {expected!r}, "
            f"但 exchange={exchange!r}; "
            f"撮合引擎 order.code == event.code 比较可能不匹配"
        )
        if _STRICT_VALIDATION:
            raise CodeExchangeMismatchError(code, exchange, expected, cls_name)
        _warnings.warn(msg, CodeExchangeMismatchWarning, stacklevel=3)


@dataclass
class TickData:
    """Tick 行情数据(统一格式)"""

    code: str  # 标的代码,如 "510300.SH"
    exchange: str  # 交易所: SSE/SZSE/CFFEX
    price: float  # 最新价
    open: float  # 开盘价
    high: float  # 最高价
    low: float  # 最低价
    pre_close: float  # 昨收价
    volume: float  # 成交量(股/手)
    amount: float  # 成交额
    bid_prices: list[float] = field(default_factory=list)  # 买价队列
    ask_prices: list[float] = field(default_factory=list)  # 卖价队列
    bid_volumes: list[float] = field(default_factory=list)  # 买量队列
    ask_volumes: list[float] = field(default_factory=list)  # 卖量队列
    timestamp: float = 0.0  # Unix 时间戳 (秒, 浮点)
    datetime_str: str = ""  # "YYYY-MM-DD HH:MM:SS"
    date: int = 0  # YYYYMMDD
    time: int = 0  # HHMMSS
    # W6.3.2 新增: 确定性事件时钟字段 (nautilus_trader 风格)
    #   ts_event: 事件发生时间, 纳秒级 UNIX 时间戳 int64, 默认 0 表示未设置 (退化到 MONOTONIC_INDEX)
    #   ts_init:  系统接收时间, 纳秒级 UNIX 时间戳 int64, 用于延迟监控 (可选, 默认 0)
    # 向后兼容: 未设置时, EventDrivenEngine 默认用 MONOTONIC_INDEX 模式
    ts_event: int = 0  # int, 纳秒级 UNIX 时间戳 (uint64 范围, 用 Python int 不会溢出)
    ts_init: int = 0  # int, 纳秒级 UNIX 时间戳

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "TickData")


@dataclass
class BarData:
    """K 线 Bar 数据(统一格式)"""

    code: str
    exchange: str
    period: str  # "1m"/"5m"/"15m"/"30m"/"1h"/"1d"/"1w"
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    date: int = 0  # YYYYMMDD
    time: int = 0  # HHMMSS (日K为0)
    turnover: float = 0.0  # 换手率
    open_interest: float = 0.0  # 持仓量(期货)
    # W6.3.2 新增: 确定性事件时钟字段 (nautilus_trader 风格)
    #   含义同 TickData.ts_event/ts_init, Bar 用 period 结束时刻作为 ts_event
    #   向后兼容: 默认 0, EventDrivenEngine 自动退化到 MONOTONIC_INDEX
    ts_event: int = 0  # int, 纳秒级 UNIX 时间戳
    ts_init: int = 0  # int, 纳秒级 UNIX 时间戳

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "BarData")


@dataclass
class OrderData:
    """委托订单(统一格式)"""

    order_id: str  # 订单ID
    code: str
    exchange: str
    direction: str  # "BUY"/"SELL"
    offset: str = "OPEN"  # "OPEN"/"CLOSE"/"CLOSETODAY"
    order_type: str = "LIMIT"  # "LIMIT"/"MARKET"/"FAK"/"FOK"
    price: float = 0.0
    volume: float = 0.0
    traded_volume: float = 0.0
    status: str = (
        "NOT_REPORTED"  # "NOT_REPORTED"/"REPORTED"/"PART_TRADED"/"ALL_TRADED"/"CANCELLED"/"REJECTED"
    )
    timestamp: float = 0.0
    datetime_str: str = ""

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "OrderData")


@dataclass
class TradeData:
    """成交数据(统一格式)"""

    trade_id: str
    order_id: str
    code: str
    exchange: str
    direction: str  # "BUY"/"SELL"
    offset: str  # "OPEN"/"CLOSE"
    price: float
    volume: float
    amount: float  # price * volume
    timestamp: float = 0.0
    datetime_str: str = ""

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "TradeData")


@dataclass
class PositionData:
    """持仓数据(统一格式)"""

    code: str
    exchange: str
    direction: str = "LONG"  # "LONG"/"SHORT"
    volume: float = 0.0  # 总持仓
    frozen_volume: float = 0.0  # 冻结持仓(挂单中)
    available_volume: float = 0.0  # 可平持仓
    avg_price: float = 0.0  # 持仓均价
    last_price: float = 0.0  # 最新价
    position_profit: float = 0.0  # 持仓盈亏
    position_cost: float = 0.0  # 持仓成本

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "PositionData")


@dataclass
class ContractData:
    """合约规格(统一格式)"""

    code: str
    exchange: str
    name: str
    product_class: str = "STOCK"  # "STOCK"/"ETF"/"FUTURE"/"OPTION"
    contract_multiplier: float = 1.0  # 合约乘数(股票=1, 股指期货=300)
    price_tick: float = 0.01  # 最小变动价位
    margin_rate: float = 1.0  # 保证金率(股票=1.0, 期货<1.0)
    commission_rate: float = 0.0003  # 手续费率
    stamp_duty: float = 0.0  # 印花税(仅卖出)
    min_commission: float = 5.0  # 最小手续费
    volume_multiple: float = 1.0  # 数量乘数(ETF=100, 股票=1)
    listing_date: str = ""  # 上市日期
    expiry_date: str = ""  # 到期日(期货/期权)

    def __post_init__(self) -> None:
        _validate_code_exchange(self.code, self.exchange, "ContractData")


def tick_to_dict(tick: TickData) -> dict:
    """Tick 转为字典"""
    return {
        "code": tick.code,
        "exchange": tick.exchange,
        "price": tick.price,
        "open": tick.open,
        "high": tick.high,
        "low": tick.low,
        "pre_close": tick.pre_close,
        "volume": tick.volume,
        "amount": tick.amount,
        "bid_prices": tick.bid_prices,
        "ask_prices": tick.ask_prices,
        "bid_volumes": tick.bid_volumes,
        "ask_volumes": tick.ask_volumes,
        "timestamp": tick.timestamp,
        "datetime_str": tick.datetime_str,
        "date": tick.date,
        "time": tick.time,
    }


def bar_to_dict(bar: BarData) -> dict:
    """Bar 转为字典"""
    return {
        "code": bar.code,
        "exchange": bar.exchange,
        "period": bar.period,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
        "date": bar.date,
        "time": bar.time,
        "turnover": bar.turnover,
        "open_interest": bar.open_interest,
    }


__all__ = [
    "BarData",
    "CodeExchangeMismatchError",
    "CodeExchangeMismatchWarning",
    "ContractData",
    "OrderData",
    "PositionData",
    "TickData",
    "TradeData",
    "bar_to_dict",
    "is_strict_symbol_validation",
    "strict_symbol_validation",
    "tick_to_dict",
]
