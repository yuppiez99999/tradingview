"""MatchingEngine 单元测试 — 12+ 用例覆盖 TICK/BAR/HYBRID 三模式 + 涨跌停 + FOK/FAK。

覆盖矩阵:
    BAR 模式:  限价买/卖、市价买、部分成交(participation_rate)、不允许部分成交拒单
    TICK 模式: 限价买消费五档、限价买低于卖一不成交、部分成交、市价买按 ask[0]
    约束:      停牌拒单、涨停买入拒单、跌停卖出拒单
    订单类型:  FOK 部分成时拒单
    HYBRID:    自动按 event 类型选择
"""

from __future__ import annotations

import pytest

from utils.backtest.matching_engine import (
    FillReason,
    MatchingEngine,
    RejectReason,
)
from utils.wt_structs import BarData, OrderData, TickData

# ============================================================
# 测试用回调收集器 — 记录成交/拒单事件
# ============================================================


class _CallbackRecorder:
    """收集 on_fill/on_partial_fill/on_reject 回调,便于断言。"""

    def __init__(self) -> None:
        self.fills: list[tuple] = []  # (order, price, volume)
        self.partial_fills: list[tuple] = []  # (order, price, volume)
        self.rejects: list[tuple] = []  # (order, reason)

    def on_fill(self, order: OrderData, price: float, volume: float) -> None:
        self.fills.append((order, price, volume))

    def on_partial_fill(self, order: OrderData, price: float, volume: float) -> None:
        self.partial_fills.append((order, price, volume))

    def on_reject(self, order: OrderData, reason: str) -> None:
        self.rejects.append((order, reason))


@pytest.fixture
def recorder() -> _CallbackRecorder:
    return _CallbackRecorder()


# ============================================================
# BAR 模式测试 (6 用例)
# ============================================================


def test_bar_limit_buy_full_match(
    recorder: _CallbackRecorder, sample_bar: BarData
) -> None:
    """BAR 限价买: order.price >= bar.low → 全成于 max(price, open)。"""
    # Arrange: sample_bar.open=1780, low=1770, high=1860, close=1820, volume=50000
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=True, max_participation_rate=1.0
    )
    order = OrderData(
        order_id="buy-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        offset="OPEN",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    # Act
    events = engine.match(
        [order],
        sample_bar,
        on_fill=recorder.on_fill,
        on_partial_fill=recorder.on_partial_fill,
        on_reject=recorder.on_reject,
    )

    # Assert: 全成于 max(1800, 1780) = 1800
    assert len(events) == 1
    assert events[0].reason == FillReason.FULL_MATCH
    assert events[0].fill_price == 1800.0
    assert events[0].fill_volume == 100.0
    assert not events[0].is_partial
    assert len(recorder.fills) == 1
    assert recorder.fills[0][1] == 1800.0  # price


def test_bar_limit_buy_below_low_no_match(
    recorder: _CallbackRecorder, sample_bar: BarData
) -> None:
    """BAR 限价买: order.price < bar.low → 不成交(挂起)。"""
    engine = MatchingEngine(mode="BAR")
    order = OrderData(
        order_id="buy-002",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1700.0,
        volume=100.0,  # 低于 low=1770
    )

    events = engine.match(
        [order], sample_bar, on_fill=recorder.on_fill, on_reject=recorder.on_reject
    )

    # 限价单挂起: 无事件,无回调
    assert len(events) == 0
    assert len(recorder.fills) == 0
    assert len(recorder.rejects) == 0


def test_bar_market_buy_at_open(
    recorder: _CallbackRecorder, sample_bar: BarData
) -> None:
    """BAR 市价买: 按 bar.open 成交。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="buy-003",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], sample_bar, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1780.0  # bar.open
    assert events[0].fill_volume == 100.0


def test_bar_partial_fill_with_participation_rate(recorder: _CallbackRecorder) -> None:
    """BAR 部分成交: fill_volume = min(order.volume, bar.volume × rate)。"""
    # Arrange: bar.volume=1000, rate=0.10 → max_volume=100
    bar = BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=4.0,
        high=4.1,
        low=3.9,
        close=4.05,
        volume=1000.0,
        amount=4000.0,
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=True, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="buy-004",
        code="510300.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=500.0,  # 需要 500,但 max_volume=100
    )

    events = engine.match(
        [order], bar, on_fill=recorder.on_fill, on_partial_fill=recorder.on_partial_fill
    )

    # Assert: 部分成交 100 股
    assert len(events) == 1
    assert events[0].is_partial is True
    assert events[0].fill_volume == 100.0
    assert events[0].reason == FillReason.PARTIAL_MATCH
    assert len(recorder.partial_fills) == 1


def test_bar_no_partial_fill_when_disabled(recorder: _CallbackRecorder) -> None:
    """BAR 不允许部分成交时: 部分成 → 拒单。"""
    bar = BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=4.0,
        high=4.1,
        low=3.9,
        close=4.05,
        volume=1000.0,
        amount=4000.0,
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=False, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="buy-005",
        code="510300.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=500.0,
    )

    events = engine.match(
        [order], bar, on_fill=recorder.on_fill, on_reject=recorder.on_reject
    )

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_FULL_LIQUIDITY
    assert len(recorder.rejects) == 1
    assert len(recorder.fills) == 0


def test_bar_limit_sell_full_match(
    recorder: _CallbackRecorder, sample_bar: BarData
) -> None:
    """BAR 限价卖: order.price <= bar.high → 全成于 min(price, open)。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="sell-001",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        offset="CLOSE",
        order_type="LIMIT",
        price=1850.0,
        volume=100.0,
    )

    events = engine.match([order], sample_bar, on_fill=recorder.on_fill)

    # 全成于 min(1850, 1780) = 1780 (open 价更优)
    assert len(events) == 1
    assert events[0].fill_price == 1780.0
    assert events[0].fill_volume == 100.0


# ============================================================
# TICK 模式测试 (4 用例)
# ============================================================


def test_tick_limit_buy_consumes_ask_levels(
    recorder: _CallbackRecorder, sample_tick: TickData
) -> None:
    """TICK 限价买: order.price >= ask[0] → 全成于 ask[0]。"""
    # sample_tick.ask_prices=[1800.5, 1800.6, ...], ask_volumes=[180, 120, ...]
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-buy-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1801.0,
        volume=100.0,  # 高于 ask[0]=1800.5
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1800.5  # ask[0]
    assert events[0].fill_volume == 100.0
    assert events[0].reason == FillReason.FULL_MATCH


def test_tick_limit_buy_below_ask_no_match(
    recorder: _CallbackRecorder, sample_tick: TickData
) -> None:
    """TICK 限价买: order.price < ask[0] → 不成交(挂起)。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-buy-002",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1799.0,
        volume=100.0,  # 低于 ask[0]=1800.5
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 0  # 限价单挂起


def test_tick_partial_fill_consumes_five_levels(recorder: _CallbackRecorder) -> None:
    """TICK 部分成交: 消费五档 ask_volumes。"""
    # 五档 ask: 总量 180+120+90+60+30 = 480
    tick = TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[1799.5, 1799.4, 1799.3, 1799.2, 1799.1],
        ask_prices=[1800.5, 1800.6, 1800.7, 1800.8, 1800.9],
        bid_volumes=[200, 150, 100, 80, 50],
        ask_volumes=[180, 120, 90, 60, 30],
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )
    engine = MatchingEngine(mode="TICK", allow_partial_fill=True)
    order = OrderData(
        order_id="tick-buy-003",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1900.0,
        volume=1000.0,  # 需要 1000,但五档仅 480
    )

    events = engine.match(
        [order],
        tick,
        on_fill=recorder.on_fill,
        on_partial_fill=recorder.on_partial_fill,
    )

    # 部分成交 480 股,加权均价 = (180*1800.5 + 120*1800.6 + 90*1800.7 + 60*1800.8 + 30*1800.9) / 480
    expected_value = (
        180 * 1800.5 + 120 * 1800.6 + 90 * 1800.7 + 60 * 1800.8 + 30 * 1800.9
    )
    expected_avg = expected_value / 480.0
    assert len(events) == 1
    assert events[0].is_partial is True
    assert events[0].fill_volume == 480.0
    assert abs(events[0].fill_price - expected_avg) < 0.001


def test_tick_market_buy_at_ask(
    recorder: _CallbackRecorder, sample_tick: TickData
) -> None:
    """TICK 市价买: 按 ask[0] 成交。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-buy-004",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1800.5  # ask[0]
    assert events[0].fill_volume == 100.0


# ============================================================
# 约束测试 (3 用例)
# ============================================================


def test_reject_on_suspended(recorder: _CallbackRecorder) -> None:
    """停牌标的: 任何方向都被拒。"""
    suspended_bar = BarData(
        code="600519.SH",
        exchange="SSE",
        period="1d",
        open=0,
        high=0,
        low=0,
        close=1800.0,
        volume=0,
        amount=0,  # 停牌
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(mode="BAR", enforce_price_limit=True)
    order = OrderData(
        order_id="buy-suspended",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], suspended_bar, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.SUSPENDED
    assert len(recorder.rejects) == 1


def test_reject_on_limit_up(recorder: _CallbackRecorder, sample_bar: BarData) -> None:
    """涨停时买入被拒: price >= limit_up。"""
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=True, max_participation_rate=1.0
    )
    order = OrderData(
        order_id="buy-limit-up",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1820.0,
        volume=100.0,  # 价格 = close = 1820
    )
    limit_up = {"600519.SH": 1820.0}  # 恰好涨停

    events = engine.match(
        [order], sample_bar, on_reject=recorder.on_reject, limit_up_prices=limit_up
    )

    assert len(events) == 1
    assert events[0].reason == RejectReason.LIMIT_UP


def test_reject_on_limit_down(recorder: _CallbackRecorder, sample_bar: BarData) -> None:
    """跌停时卖出被拒: price <= limit_down。"""
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=True, max_participation_rate=1.0
    )
    order = OrderData(
        order_id="sell-limit-down",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="LIMIT",
        price=1820.0,
        volume=100.0,  # 价格 = close = 1820
    )
    limit_down = {"600519.SH": 1820.0}  # 恰好跌停

    events = engine.match(
        [order], sample_bar, on_reject=recorder.on_reject, limit_down_prices=limit_down
    )

    assert len(events) == 1
    assert events[0].reason == RejectReason.LIMIT_DOWN


# ============================================================
# 订单类型测试 (1 用例)
# ============================================================


def test_fok_rejects_partial_fill(recorder: _CallbackRecorder) -> None:
    """FOK 订单: 无法全部成交时拒单(而非部分成交)。"""
    bar = BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=4.0,
        high=4.1,
        low=3.9,
        close=4.05,
        volume=1000.0,
        amount=4000.0,
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(
        mode="BAR",
        allow_partial_fill=True,  # 即使允许部分成交
        max_participation_rate=0.10,  # max_volume=100
    )
    order = OrderData(
        order_id="fok-001",
        code="510300.SH",
        exchange="SSE",
        direction="BUY",
        order_type="FOK",  # Fill Or Kill
        price=0.0,
        volume=500.0,
    )

    events = engine.match(
        [order],
        bar,
        on_fill=recorder.on_fill,
        on_partial_fill=recorder.on_partial_fill,
        on_reject=recorder.on_reject,
    )

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_FULL_LIQUIDITY
    assert len(recorder.rejects) == 1
    assert len(recorder.fills) == 0
    assert len(recorder.partial_fills) == 0


# ============================================================
# HYBRID 模式测试 (1 用例)
# ============================================================


def test_hybrid_mode_auto_selects(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
    sample_bar: BarData,
) -> None:
    """HYBRID 模式: TickData→TICK 撮合, BarData→BAR 撮合。"""
    engine = MatchingEngine(mode="HYBRID", max_participation_rate=1.0)

    # TickData → TICK 模式
    tick_order = OrderData(
        order_id="hybrid-tick",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1801.0,
        volume=100.0,
    )
    tick_events = engine.match([tick_order], sample_tick, on_fill=recorder.on_fill)
    assert len(tick_events) == 1
    assert tick_events[0].fill_price == 1800.5  # ask[0],证明走 TICK 路径

    # BarData → BAR 模式
    bar_order = OrderData(
        order_id="hybrid-bar",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )
    bar_events = engine.match([bar_order], sample_bar, on_fill=recorder.on_fill)
    assert len(bar_events) == 1
    assert bar_events[0].fill_price == 1800.0  # max(1800, open=1780),证明走 BAR 路径


# ============================================================
# 不可变性测试 (1 用例)
# ============================================================


def test_immutability_order_not_modified(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """撮合后入参 OrderData 对象不被修改。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="immut-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
        traded_volume=0.0,
        status="NOT_REPORTED",
    )
    orig_status = order.status
    orig_traded = order.traded_volume

    engine.match([order], sample_bar, on_fill=recorder.on_fill)

    # 入参 order 字段不变
    assert order.status == orig_status
    assert order.traded_volume == orig_traded


# ============================================================
# 补充测试 — 覆盖 TICK SELL / 边界 / 回调=None / 模式不匹配
# 目标: 将 matching_engine.py 覆盖率从 73.6% 提升至 ≥ 80%
# ============================================================

# ---- TICK SELL 路径 (lines 210-212, 254-278) ----


def test_tick_limit_sell_consumes_bid_levels(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """TICK 限价卖: order.price <= bid[0] → 全成于 bid[0]。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-sell-001",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        offset="CLOSE",
        order_type="LIMIT",
        price=1799.0,
        volume=100.0,  # 低于 bid[0]=1799.5
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1799.5  # bid[0]
    assert events[0].fill_volume == 100.0
    assert events[0].reason == FillReason.FULL_MATCH


def test_tick_limit_sell_above_bid_no_match(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """TICK 限价卖: order.price > bid[0] → 不成交(挂起)。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-sell-002",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,  # 高于 bid[0]=1799.5
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 0  # 限价单挂起


def test_tick_market_sell_at_bid(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """TICK 市价卖: 按 bid[0] 成交。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-sell-003",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], sample_tick, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1799.5  # bid[0]
    assert events[0].fill_volume == 100.0


def test_tick_sell_partial_fill_consumes_bid_levels(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """TICK 卖出部分成交: 消费五档 bid_volumes。"""
    # sample_tick bid 总量 = 200+150+100+80+50 = 580
    engine = MatchingEngine(mode="TICK", allow_partial_fill=True)
    order = OrderData(
        order_id="tick-sell-004",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="LIMIT",
        price=1799.0,
        volume=1000.0,  # 需要 1000,但五档仅 580
    )

    events = engine.match(
        [order], sample_tick, on_partial_fill=recorder.on_partial_fill
    )

    expected_value = (
        200 * 1799.5 + 150 * 1799.4 + 100 * 1799.3 + 80 * 1799.2 + 50 * 1799.1
    )
    expected_avg = expected_value / 580.0
    assert len(events) == 1
    assert events[0].is_partial is True
    assert events[0].fill_volume == 580.0
    assert abs(events[0].fill_price - expected_avg) < 0.01


def test_tick_sell_no_liquidity_rejects(
    recorder: _CallbackRecorder,
) -> None:
    """TICK 卖出无 bid 流动性 → 拒单 NO_LIQUIDITY。"""
    tick = TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[],
        bid_volumes=[],
        ask_prices=[1800.5],
        ask_volumes=[100],
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-sell-005",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_LIQUIDITY


# ---- TICK BUY 边界 (lines 220, 241) ----


def test_tick_buy_no_ask_liquidity_rejects(
    recorder: _CallbackRecorder,
) -> None:
    """TICK 买入无 ask 流动性 → 拒单 NO_LIQUIDITY。"""
    tick = TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[1799.5],
        bid_volumes=[100],
        ask_prices=[],
        ask_volumes=[],
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-buy-005",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_LIQUIDITY


def test_tick_market_buy_zero_ask_volume_rejects(
    recorder: _CallbackRecorder,
) -> None:
    """TICK 市价买: ask 存在但 ask_volumes 全为 0 → 拒单 NO_LIQUIDITY。"""
    tick = TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[1799.5],
        bid_volumes=[100],
        ask_prices=[1800.5],
        ask_volumes=[0],  # 价位有,量为 0
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="tick-buy-006",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_LIQUIDITY


# ---- BAR SELL 边界 (lines 334, 337, 345) ----


def test_bar_limit_sell_above_high_no_match(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """BAR 限价卖: order.price > bar.high → 不成交(挂起)。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="sell-002",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="LIMIT",
        price=1900.0,
        volume=100.0,  # 高于 high=1860
    )

    events = engine.match([order], sample_bar, on_fill=recorder.on_fill)

    assert len(events) == 0  # 限价单挂起


def test_bar_market_sell_at_open(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """BAR 市价卖: 按 bar.open 成交。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="sell-003",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        order_type="MARKET",
        price=0.0,
        volume=100.0,
    )

    events = engine.match([order], sample_bar, on_fill=recorder.on_fill)

    assert len(events) == 1
    assert events[0].fill_price == 1780.0  # bar.open
    assert events[0].fill_volume == 100.0


def test_bar_sell_no_volume_rejects(recorder: _CallbackRecorder) -> None:
    """BAR 卖出: bar.volume=0 → 拒单 NO_LIQUIDITY。"""
    bar = BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=4.0,
        high=4.1,
        low=3.9,
        close=4.05,
        volume=0.0,
        amount=0.0,
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=False, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="sell-004",
        code="510300.SH",
        exchange="SSE",
        direction="SELL",
        order_type="LIMIT",
        price=4.0,
        volume=100.0,
    )

    events = engine.match([order], bar, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_LIQUIDITY


# ---- BAR BUY 边界 (line 321) ----


def test_bar_buy_no_volume_rejects(recorder: _CallbackRecorder) -> None:
    """BAR 买入: bar.volume=0 → 拒单 NO_LIQUIDITY。"""
    bar = BarData(
        code="510300.SH",
        exchange="SSE",
        period="1d",
        open=4.0,
        high=4.1,
        low=3.9,
        close=4.05,
        volume=0.0,
        amount=0.0,
        date=20231114,
        time=0,
    )
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=False, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="buy-006",
        code="510300.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=4.0,
        volume=100.0,
    )

    events = engine.match([order], bar, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_LIQUIDITY


# ---- 模式不匹配 (lines 184, 187, 191) ----


def test_tick_mode_with_bar_event_rejects(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """TICK 模式收到 BarData → 拒单 UNKNOWN_EVENT。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="mode-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], sample_bar, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.UNKNOWN_EVENT


def test_bar_mode_with_tick_event_rejects(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """BAR 模式收到 TickData → 拒单 UNKNOWN_EVENT。"""
    engine = MatchingEngine(mode="BAR")
    order = OrderData(
        order_id="mode-002",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], sample_tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.UNKNOWN_EVENT


def test_hybrid_tick_without_ask_rejects(recorder: _CallbackRecorder) -> None:
    """HYBRID 模式: TickData 但无 ask_prices → 拒单 UNKNOWN_EVENT。"""
    tick = TickData(
        code="600519.SH",
        exchange="SSE",
        price=1800.0,
        open=1780.0,
        high=1860.0,
        low=1770.0,
        pre_close=1780.0,
        volume=10000.0,
        amount=18000000.0,
        bid_prices=[],
        bid_volumes=[],
        ask_prices=[],
        ask_volumes=[],
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
        date=20231114,
        time=100000,
    )
    engine = MatchingEngine(mode="HYBRID")
    order = OrderData(
        order_id="mode-003",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.UNKNOWN_EVENT


# ---- 未知方向 (lines 212, 299) ----


def test_tick_unknown_direction_rejects(
    recorder: _CallbackRecorder,
    sample_tick: TickData,
) -> None:
    """TICK 模式: direction 非 BUY/SELL → 拒单 UNKNOWN_EVENT。"""
    engine = MatchingEngine(mode="TICK")
    order = OrderData(
        order_id="dir-001",
        code="600519.SH",
        exchange="SSE",
        direction="HOLD",
        order_type="LIMIT",  # 非法方向
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], sample_tick, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.UNKNOWN_EVENT


def test_bar_unknown_direction_rejects(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """BAR 模式: direction 非 BUY/SELL → 拒单 UNKNOWN_EVENT。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="dir-002",
        code="600519.SH",
        exchange="SSE",
        direction="HOLD",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    events = engine.match([order], sample_bar, on_reject=recorder.on_reject)

    assert len(events) == 1
    assert events[0].reason == RejectReason.UNKNOWN_EVENT


# ---- 回调=None 分支 (lines 377->379, 381->383, 396->398) ----


def test_full_fill_without_fill_callback(
    sample_bar: BarData,
) -> None:
    """全成但 on_fill=None: 仍返回 FillEvent,不报错。"""
    engine = MatchingEngine(mode="BAR", max_participation_rate=1.0)
    order = OrderData(
        order_id="cb-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1800.0,
        volume=100.0,
    )

    # 不传任何回调
    events = engine.match([order], sample_bar)

    assert len(events) == 1
    assert events[0].reason == FillReason.FULL_MATCH
    assert events[0].fill_volume == 100.0


def test_partial_fill_without_partial_callback(
    sample_bar: BarData,
) -> None:
    """部分成但 on_partial_fill=None: 仍返回 FillEvent,不报错。"""
    # sample_bar.volume=50000, rate=0.10 → max_volume=5000
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=True, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="cb-002",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=10000.0,  # 需要 10000,但 max_volume=5000
    )

    events = engine.match([order], sample_bar)  # 无回调

    assert len(events) == 1
    assert events[0].is_partial is True
    assert events[0].fill_volume == 5000.0


def test_reject_without_reject_callback(
    sample_bar: BarData,
) -> None:
    """拒单但 on_reject=None: 仍返回 FillEvent,不报错。"""
    # sample_bar.volume=50000, rate=0.10 → max_volume=5000, 但不允许部分成交
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=False, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="cb-003",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="MARKET",
        price=0.0,
        volume=10000.0,
    )

    events = engine.match([order], sample_bar)  # 无回调

    assert len(events) == 1
    assert events[0].reason == RejectReason.NO_FULL_LIQUIDITY


# ---- enforce_price_limit=False (line 165->177) ----


def test_enforce_price_limit_false_skips_check(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """enforce_price_limit=False: 涨停价仍允许买入(跳过约束检查)。"""
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=False, max_participation_rate=1.0
    )
    order = OrderData(
        order_id="nolimit-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1820.0,
        volume=100.0,
    )
    limit_up = {"600519.SH": 1820.0}  # 恰好涨停

    events = engine.match(
        [order], sample_bar, on_fill=recorder.on_fill, limit_up_prices=limit_up
    )

    # 不约束 → 正常成交,而非拒单
    assert len(events) == 1
    assert events[0].reason == FillReason.FULL_MATCH
    assert len(recorder.fills) == 1


def test_constraint_reject_without_reject_callback(
    sample_bar: BarData,
) -> None:
    """约束拒单但 on_reject=None: 仍返回 FillEvent,不报错 (line 172->174)。"""
    engine = MatchingEngine(
        mode="BAR", enforce_price_limit=True, max_participation_rate=1.0
    )
    order = OrderData(
        order_id="nolimit-002",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="LIMIT",
        price=1820.0,
        volume=100.0,
    )
    limit_up = {"600519.SH": 1820.0}

    # 不传 on_reject
    events = engine.match([order], sample_bar, limit_up_prices=limit_up)

    assert len(events) == 1
    assert events[0].reason == RejectReason.LIMIT_UP


# ---- FAK 订单类型补充 ----


def test_fak_partial_fill_then_cancel(
    recorder: _CallbackRecorder,
    sample_bar: BarData,
) -> None:
    """FAK 订单: 部分成后剩余撤单(允许部分成交时)。"""
    # sample_bar.volume=50000, rate=0.10 → max_volume=5000
    engine = MatchingEngine(
        mode="BAR", allow_partial_fill=True, max_participation_rate=0.10
    )
    order = OrderData(
        order_id="fak-001",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        order_type="FAK",
        price=1800.0,
        volume=10000.0,  # 需要 10000,但 max_volume=5000
    )

    events = engine.match([order], sample_bar, on_partial_fill=recorder.on_partial_fill)

    # FAK 允许部分成交,剩余自动撤单
    assert len(events) == 1
    assert events[0].is_partial is True
    assert events[0].fill_volume == 5000.0
    assert events[0].reason == FillReason.PARTIAL_MATCH
