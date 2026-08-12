"""constraints.py 单元测试 — 涨跌停/停牌约束工具。

覆盖:
    - is_suspended: 停牌判断(volume/amount 为 0)
    - is_at_limit: 涨跌停判断(买受限/卖受限)
    - check_tradable: 综合可交易性检查
    - 向后兼容: 无 limit_prices 时不约束
"""
from __future__ import annotations

import pytest

from utils.backtest.constraints import (
    is_suspended,
    is_at_limit,
    check_tradable,
    _get_current_price,
)
from utils.wt_structs import TickData, BarData


# ============================================================
# 用例 1: 停牌判断 — volume=0 且 amount=0
# ============================================================

def test_is_suspended_by_zero_volume_and_amount() -> None:
    """Tick/Bar 的 volume=0 且 amount=0 时判定为停牌。"""
    # Arrange: 停牌 Bar
    suspended_bar = BarData(
        code="600519.SH", exchange="SSE", period="1d",
        open=0, high=0, low=0, close=1800.0,  # 价格可能保留(上一收盘价)
        volume=0, amount=0,
        date=20231114, time=0,
    )
    # 停牌 Tick
    suspended_tick = TickData(
        code="600519.SH", exchange="SSE",
        price=1800.0, open=0, high=0, low=0, pre_close=1800.0,
        volume=0, amount=0,
        bid_prices=[], ask_prices=[], bid_volumes=[], ask_volumes=[],
        timestamp=1700000000.0, datetime_str="2023-11-14 10:00:00",
        date=20231114, time=100000,
    )
    
    # Act + Assert
    assert is_suspended(suspended_bar, "600519.SH") is True
    assert is_suspended(suspended_tick, "600519.SH") is True


def test_is_suspended_normal_bar(sample_bar: BarData) -> None:
    """正常 Bar(volume>0) 不停牌。"""
    # sample_bar fixture: volume=50000, amount=91000000
    assert is_suspended(sample_bar, "600519.SH") is False


# ============================================================
# 用例 2: 涨跌停判断 — 买入遇涨停
# ============================================================

def test_is_at_limit_buy_at_limit_up(sample_tick: TickData) -> None:
    """买入时当前价 >= 涨停价 → 涨停限制。"""
    # Arrange: sample_tick.price = 1800.0
    limit_up_prices = {"600519.SH": 1800.0}  # 恰好等于当前价
    
    # Act
    result = is_at_limit(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices=limit_up_prices,
    )
    
    # Assert
    assert result is True  # price >= limit_up → 涨停不可买


def test_is_at_limit_buy_below_limit_up(sample_tick: TickData) -> None:
    """买入时当前价 < 涨停价 → 可交易。"""
    # Arrange: sample_tick.price = 1800.0
    limit_up_prices = {"600519.SH": 1900.0}  # 涨停价更高
    
    # Act
    result = is_at_limit(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices=limit_up_prices,
    )
    
    # Assert
    assert result is False


# ============================================================
# 用例 3: 涨跌停判断 — 卖出遇跌停
# ============================================================

def test_is_at_limit_sell_at_limit_down(sample_tick: TickData) -> None:
    """卖出时当前价 <= 跌停价 → 跌停限制。"""
    # Arrange: sample_tick.price = 1800.0
    limit_down_prices = {"600519.SH": 1800.0}  # 恰好等于当前价
    
    # Act
    result = is_at_limit(
        sample_tick, "600519.SH", "SELL",
        limit_down_prices=limit_down_prices,
    )
    
    # Assert
    assert result is True  # price <= limit_down → 跌停不可卖


def test_is_at_limit_sell_above_limit_down(sample_tick: TickData) -> None:
    """卖出时当前价 > 跌停价 → 可交易。"""
    # Arrange: sample_tick.price = 1800.0
    limit_down_prices = {"600519.SH": 1700.0}  # 跌停价更低
    
    # Act
    result = is_at_limit(
        sample_tick, "600519.SH", "SELL",
        limit_down_prices=limit_down_prices,
    )
    
    # Assert
    assert result is False


# ============================================================
# 用例 4: 向后兼容 — 无 limit_prices 时不约束
# ============================================================

def test_is_at_limit_no_constraint_when_no_prices(sample_tick: TickData) -> None:
    """未提供 limit_up_prices/limit_down_prices 时不约束(向后兼容)。"""
    # Act + Assert: 买入
    assert is_at_limit(sample_tick, "600519.SH", "BUY") is False
    # 卖出
    assert is_at_limit(sample_tick, "600519.SH", "SELL") is False
    # 空 dict 也兼容
    assert is_at_limit(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices={}, limit_down_prices={},
    ) is False
    # code 不在 limit_prices 中也兼容
    assert is_at_limit(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices={"OTHER.SH": 9999.0},
    ) is False


# ============================================================
# 补充用例: _get_current_price 适配 Tick/Bar
# ============================================================

def test_get_current_price_from_tick(sample_tick: TickData) -> None:
    """TickData 用 .price 字段。"""
    assert _get_current_price(sample_tick) == 1800.0


def test_get_current_price_from_bar(sample_bar: BarData) -> None:
    """BarData 用 .close 字段。"""
    assert _get_current_price(sample_bar) == 1820.0


# ============================================================
# 补充用例: check_tradable 综合检查
# ============================================================

def test_check_tradable_normal(sample_tick: TickData) -> None:
    """正常标的可交易。"""
    tradable, reason = check_tradable(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices={"600519.SH": 1900.0},
    )
    assert tradable is True
    assert reason == "OK"


def test_check_tradable_suspended() -> None:
    """停牌标的不可交易。"""
    suspended_bar = BarData(
        code="600519.SH", exchange="SSE", period="1d",
        open=0, high=0, low=0, close=1800.0,
        volume=0, amount=0,
        date=20231114, time=0,
    )
    tradable, reason = check_tradable(suspended_bar, "600519.SH", "BUY")
    assert tradable is False
    assert reason == "SUSPENDED"


def test_check_tradable_limit_up(sample_tick: TickData) -> None:
    """涨停时买入不可交易。"""
    tradable, reason = check_tradable(
        sample_tick, "600519.SH", "BUY",
        limit_up_prices={"600519.SH": 1800.0},  # 恰好涨停
    )
    assert tradable is False
    assert reason == "PRICE_LIMIT_UP"


def test_check_tradable_limit_down(sample_tick: TickData) -> None:
    """跌停时卖出不可交易。"""
    tradable, reason = check_tradable(
        sample_tick, "600519.SH", "SELL",
        limit_down_prices={"600519.SH": 1800.0},  # 恰好跌停
    )
    assert tradable is False
    assert reason == "PRICE_LIMIT_DOWN"


def test_check_tradable_limit_up_allows_sell(sample_tick: TickData) -> None:
    """涨停时仍可卖出(只是不可买)。"""
    tradable, reason = check_tradable(
        sample_tick, "600519.SH", "SELL",
        limit_up_prices={"600519.SH": 1800.0},  # 涨停价
    )
    assert tradable is True
    assert reason == "OK"
