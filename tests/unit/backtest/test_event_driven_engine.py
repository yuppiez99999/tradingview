"""event_driven_engine.py 单元测试 — 主引擎与事件循环。

覆盖矩阵:
    PendingOrder / Position: 数据类基础行为
    EventDrivenEngine 初始化: 默认组件、初始资金
    submit_order: 入队、延迟计算、非法参数
    process_event 延迟语义: FixedLatency(0) 次事件成交、FixedLatency(2) 多事件延迟
    process_event 撮合: BUY/SELL OPEN/CLOSE → 持仓与现金更新
    process_event 权益跟踪: equity_curve 增长、mark-to-market
    run(): 完整回测 + EngineSummary
    get_state(): 快照不可变性
    集成测试: 策略 on_bar 回调提交订单
"""

from __future__ import annotations

import pytest

from utils.backtest.event_driven_engine import (
    EngineSnapshot,
    EngineSummary,
    EventClockMode,  # W6.3.2 新增
    EventDrivenEngine,
    NonMonotonicTimestampError,  # W6.3.2 新增
    PendingOrder,
    Position,
)
from utils.backtest.latency_model import FixedLatency, QueueLatency
from utils.backtest.matching_engine import MatchingEngine
from utils.wt_hedge_strategy import HedgeStrategy
from utils.wt_structs import BarData, OrderData

# ============================================================
# 测试辅助
# ============================================================


def make_bar(
    code: str = "600519.SH",
    close: float = 100.0,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    volume: float = 10000.0,
) -> BarData:
    """构造测试用 BarData。"""
    return BarData(
        code=code,
        exchange="SSE",
        period="1d",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=close * volume,
        date=20231114,
        time=0,
    )


def make_buy_order(
    code: str = "600519.SH",
    price: float = 100.0,
    volume: float = 100.0,
    order_id: str = "test-buy-001",
) -> OrderData:
    """构造测试用买入订单。"""
    return OrderData(
        order_id=order_id,
        code=code,
        exchange="SSE",
        direction="BUY",
        offset="OPEN",
        order_type="LIMIT",
        price=price,
        volume=volume,
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
    )


def make_sell_order(
    code: str = "600519.SH",
    price: float = 100.0,
    volume: float = 100.0,
    order_id: str = "test-sell-001",
) -> OrderData:
    """构造测试用卖出订单。"""
    return OrderData(
        order_id=order_id,
        code=code,
        exchange="SSE",
        direction="SELL",
        offset="OPEN",
        order_type="LIMIT",
        price=price,
        volume=volume,
        timestamp=1700000000.0,
        datetime_str="2023-11-14 10:00:00",
    )


class _NoopStrategy(HedgeStrategy):
    """空策略 — 不在回调中提交任何订单。"""

    def __init__(self) -> None:
        super().__init__(name="noop")

    def on_rebalance(self, ctx) -> None:  # type: ignore[override]
        pass

    def on_tick(self, ctx, tick) -> None:  # type: ignore[override]
        pass

    def on_bar(self, ctx, bar) -> None:  # type: ignore[override]
        pass


class _BuyOnFirstBarStrategy(HedgeStrategy):
    """首个 bar 提交买入订单的策略 (用于集成测试)。"""

    def __init__(self) -> None:
        super().__init__(name="test_buy_first_bar")
        self.submitted = False

    def on_rebalance(self, ctx) -> None:  # type: ignore[override]
        pass

    def on_tick(self, ctx, tick) -> None:  # type: ignore[override]
        pass

    def on_bar(self, ctx, bar: BarData) -> None:  # type: ignore[override]
        # 通过 ctx 暴露的 submit 机制由引擎注入
        if not self.submitted:
            ctx.current_prices[bar.code] = bar.close
            self.submitted = True


# ============================================================
# PendingOrder / Position 数据类测试
# ============================================================


def test_pending_order_dataclass() -> None:
    """PendingOrder 数据类基础行为。"""
    order = make_buy_order()
    po = PendingOrder(order=order, remaining_latency=3)
    assert po.order is order
    assert po.remaining_latency == 3


def test_position_apply_fill_new() -> None:
    """Position.apply_fill 新建仓。"""
    pos = Position(code="600519.SH", direction="LONG")
    pos.apply_fill(fill_price=100.0, fill_volume=100.0)
    assert pos.volume == 100.0
    assert pos.avg_price == 100.0


def test_position_apply_fill_add() -> None:
    """Position.apply_fill 加仓 — 重算均价。"""
    pos = Position(code="600519.SH", direction="LONG", volume=100.0, avg_price=100.0)
    pos.apply_fill(fill_price=110.0, fill_volume=100.0)
    assert pos.volume == 200.0
    assert pos.avg_price == 105.0  # (100*100 + 110*100) / 200


def test_position_apply_fill_close_to_zero() -> None:
    """Position.apply_fill 平仓完毕 — 重置。"""
    pos = Position(code="600519.SH", direction="LONG", volume=100.0, avg_price=100.0)
    pos.apply_fill(fill_price=105.0, fill_volume=-100.0)
    assert pos.volume == 0.0
    assert pos.avg_price == 0.0


def test_position_apply_fill_zero_volume_noop() -> None:
    """Position.apply_fill volume=0 不操作。"""
    pos = Position(code="600519.SH", direction="LONG", volume=100.0, avg_price=100.0)
    pos.apply_fill(fill_price=200.0, fill_volume=0.0)
    assert pos.volume == 100.0
    assert pos.avg_price == 100.0


# ============================================================
# EventDrivenEngine 初始化测试
# ============================================================


def test_engine_init_defaults() -> None:
    """默认参数初始化: BAR 撮合 + FixedLatency(0) + 1M 资金。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    assert engine._initial_capital == 1_000_000.0
    assert engine._cash == 1_000_000.0
    assert engine._equity == 1_000_000.0
    assert isinstance(engine._matching_engine, MatchingEngine)
    assert isinstance(engine._latency_model, FixedLatency)
    assert engine._latency_model.latency_ticks == 0


def test_engine_init_custom_params() -> None:
    """自定义参数初始化。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        matching_engine=MatchingEngine(mode="BAR"),
        latency_model=FixedLatency(latency_ticks=2),
        initial_capital=500_000.0,
        commission_rate=0.0005,
    )
    assert engine._initial_capital == 500_000.0
    assert engine._latency_model.latency_ticks == 2
    assert engine._commission_rate == 0.0005


def test_engine_init_equity_curve_starts_with_capital() -> None:
    """权益曲线初始点 = 初始资金。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy(), initial_capital=1_000_000.0)
    assert engine._equity_curve == [1_000_000.0]


# ============================================================
# submit_order 测试
# ============================================================


def test_submit_order_enters_latency_queue() -> None:
    """submit_order 将订单放入延迟队列。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy(), latency_model=FixedLatency(1))
    order = make_buy_order()

    result = engine.submit_order(order)

    assert result is True
    assert len(engine._latency_queue) == 1
    po = engine._latency_queue[0]
    assert po.order is order
    assert po.remaining_latency == 1
    assert engine._n_submitted == 1


def test_submit_order_rejects_zero_volume() -> None:
    """submit_order 拒绝 volume <= 0。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    order = make_buy_order(volume=0.0)

    result = engine.submit_order(order)

    assert result is False
    assert len(engine._latency_queue) == 0
    assert engine._n_submitted == 0


def test_submit_order_rejects_negative_volume() -> None:
    """submit_order 拒绝 volume < 0。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    order = make_buy_order(volume=-10.0)

    result = engine.submit_order(order)

    assert result is False
    assert engine._n_submitted == 0


def test_submit_order_latency_zero() -> None:
    """FixedLatency(0) → remaining_latency=0 (次事件可撮合)。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy(), latency_model=FixedLatency(0))
    order = make_buy_order()

    engine.submit_order(order)

    assert engine._latency_queue[0].remaining_latency == 0


def test_submit_order_uses_pending_count_for_queue_latency() -> None:
    """QueueLatency 使用 pending_count 计算延迟。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=QueueLatency(
            base_ticks=1, per_pending_order_ticks=1.0, max_ticks=10
        ),
    )

    # 第一个订单: pending=0 → 1 + 1*0 = 1
    engine.submit_order(make_buy_order(order_id="o1"))
    assert engine._latency_queue[0].remaining_latency == 1

    # 第二个订单: pending=1 → 1 + 1*1 = 2
    engine.submit_order(make_buy_order(order_id="o2"))
    assert engine._latency_queue[1].remaining_latency == 2


# ============================================================
# process_event 延迟语义测试
# ============================================================


def test_process_event_no_orders_no_change() -> None:
    """无订单时 process_event 仅增长 equity_curve。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy(), initial_capital=1_000_000.0)

    engine.process_event(make_bar(close=100.0))

    assert engine._n_events == 1
    assert len(engine._equity_curve) == 2  # 初始 + 1 事件
    assert engine._cash == 1_000_000.0  # 无交易
    assert engine._equity == 1_000_000.0


def test_latency_zero_order_matches_on_next_event() -> None:
    """FixedLatency(0): 订单在提交后的下一个事件撮合。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,  # 简化: 不收手续费
    )
    order = make_buy_order(price=100.0, volume=100.0)

    # 事件 1: 提交订单 → 进入延迟队列 (remaining=0)
    engine.process_event(make_bar(close=100.0, low=95.0, open=98.0))
    engine.submit_order(order)

    # 此时订单不应撮合
    assert engine._n_filled == 0
    assert len(engine._long_positions) == 0

    # 事件 2: 延迟递减 (0→-1) → 入撮合队列 → 撮合
    engine.process_event(make_bar(close=102.0, low=98.0, open=100.0))

    # 应已成交
    assert engine._n_filled == 1
    assert "600519.SH" in engine._long_positions
    pos = engine._long_positions["600519.SH"]
    assert pos.volume == 100.0
    # BUY LIMIT @ 100, bar.open=100 → fill at max(100, 100) = 100
    assert pos.avg_price == 100.0
    # 现金减少
    assert engine._cash == 1_000_000.0 - 100.0 * 100.0


def test_latency_two_order_matches_on_third_event() -> None:
    """FixedLatency(2): 订单在第 3 个事件撮合 (T 提交 → T+1 递减到 1 → T+2 递减到 0 入撮合)。

    语义: latency=N 表示提交后第 N 个事件撮合 (T+N)。
    """
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(2),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )
    order = make_buy_order(price=100.0, volume=100.0)

    # 事件 1: 提交 (remaining=2)
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(order)
    assert engine._n_filled == 0

    # 事件 2: remaining 2→1, 仍在延迟队列
    engine.process_event(make_bar(close=100.0))
    assert engine._n_filled == 0

    # 事件 3: remaining 1→0, 入撮合队列 → 撮合
    engine.process_event(make_bar(close=100.0, low=95.0, open=100.0))
    assert engine._n_filled == 1
    assert "600519.SH" in engine._long_positions


def test_order_not_matched_on_different_code_event() -> None:
    """订单 code 与事件 code 不同时不撮合,保留在撮合队列。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )
    # 提交 600519.SH 订单
    engine.process_event(make_bar(code="600519.SH", close=100.0))
    engine.submit_order(make_buy_order(code="600519.SH", price=100.0))

    # 事件 2: 不同 code 的 bar — 订单不应撮合
    engine.process_event(make_bar(code="000001.SZ", close=10.0))
    assert engine._n_filled == 0

    # 事件 3: 同 code 的 bar — 订单应撮合
    engine.process_event(make_bar(code="600519.SH", close=100.0, low=95.0, open=100.0))
    assert engine._n_filled == 1


# ============================================================
# process_event 撮合与持仓测试
# ============================================================


def test_buy_open_creates_long_position() -> None:
    """BUY OPEN 创建多头持仓,现金减少。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    engine.process_event(make_bar(close=100.0, low=95.0, open=100.0))

    pos = engine._long_positions["600519.SH"]
    assert pos.direction == "LONG"
    assert pos.volume == 100.0
    assert engine._cash == 1_000_000.0 - 100.0 * 100.0


def test_sell_open_creates_short_position() -> None:
    """SELL OPEN 创建空头持仓,现金增加。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_sell_order(price=100.0, volume=100.0))

    engine.process_event(make_bar(close=100.0, high=105.0, open=100.0))

    pos = engine._short_positions["600519.SH"]
    assert pos.direction == "SHORT"
    assert pos.volume == 100.0
    assert engine._cash == 1_000_000.0 + 100.0 * 100.0


def test_commission_deducted_on_buy() -> None:
    """BUY 时扣除手续费 (双边收费)。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    engine.process_event(make_bar(close=100.0, low=95.0, open=100.0))

    fill_value = 100.0 * 100.0
    expected_commission = fill_value * 0.0003
    assert engine._cash == pytest.approx(1_000_000.0 - fill_value - expected_commission)


def test_commission_deducted_on_sell() -> None:
    """SELL 时扣除手续费。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_sell_order(price=100.0, volume=100.0))

    engine.process_event(make_bar(close=100.0, high=105.0, open=100.0))

    fill_value = 100.0 * 100.0
    expected_commission = fill_value * 0.0003
    assert engine._cash == pytest.approx(1_000_000.0 + fill_value - expected_commission)


# ============================================================
# process_event 权益跟踪测试
# ============================================================


def test_equity_curve_grows_one_point_per_event() -> None:
    """每个事件增加一个权益点。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    initial_len = len(engine._equity_curve)

    engine.process_event(make_bar(close=100.0))
    engine.process_event(make_bar(close=101.0))
    engine.process_event(make_bar(close=102.0))

    assert len(engine._equity_curve) == initial_len + 3


def test_equity_reflects_mark_to_market() -> None:
    """权益 = cash + 多头市值 - 空头市值 (mark-to-market)。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )
    # 提交买入
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # 事件 2: 成交 @ 100,close=110 → 多头市值 = 100*110 = 11000
    engine.process_event(make_bar(close=110.0, low=95.0, open=100.0))

    expected_cash = 1_000_000.0 - 100.0 * 100.0  # 990000
    expected_long_value = 100.0 * 110.0  # 11000
    expected_equity = expected_cash + expected_long_value
    assert engine._equity == pytest.approx(expected_equity)


def test_market_prices_updated() -> None:
    """process_event 更新 market_prices 字典。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())

    engine.process_event(make_bar(code="600519.SH", close=100.0))
    assert engine._market_prices["600519.SH"] == 100.0

    engine.process_event(make_bar(code="600519.SH", close=105.0))
    assert engine._market_prices["600519.SH"] == 105.0


def test_position_last_price_marked() -> None:
    """持仓的 last_price 被当前事件价格标记。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # 事件 2: 成交 + 标记
    engine.process_event(make_bar(close=110.0, low=95.0, open=100.0))
    assert engine._long_positions["600519.SH"].last_price == 110.0

    # 事件 3: 仅标记
    engine.process_event(make_bar(close=120.0))
    assert engine._long_positions["600519.SH"].last_price == 120.0


# ============================================================
# run() 完整回测测试
# ============================================================


def test_run_processes_all_events() -> None:
    """run() 处理所有事件并返回 EngineSummary。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    events = [make_bar(close=100.0 + i) for i in range(5)]

    summary = engine.run(events)

    assert isinstance(summary, EngineSummary)
    assert summary.n_events == 5
    assert summary.initial_capital == 1_000_000.0
    assert len(summary.equity_curve) == 6  # 初始 + 5 事件


def test_run_empty_events() -> None:
    """run() 空事件列表 — 返回初始状态。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())

    summary = engine.run([])

    assert summary.n_events == 0
    assert summary.final_equity == 1_000_000.0
    assert summary.total_return == 0.0


def test_run_with_orders_summary_stats() -> None:
    """run() 含订单 — summary 统计正确。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )
    events = [make_bar(close=100.0, low=95.0, open=100.0) for _ in range(3)]

    # 在第一个事件后手动提交订单 (通过 engine.submit_order)
    # 由于 run() 是一次性消费,我们需要在事件处理中插入订单
    # 这里直接提交,然后 run 处理后续事件
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))
    # 注意: submit_order 不触发事件,订单在延迟队列中等待第一个事件

    summary = engine.run(events)

    assert summary.n_orders_submitted == 1
    # 第一个事件后订单应已撮合 (latency=0 → 第一个事件递减后撮合)
    assert summary.n_orders_filled == 1
    assert summary.n_orders_rejected == 0


def test_summary_total_return_calculation() -> None:
    """EngineSummary.total_return = (final - initial) / initial。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        initial_capital=1_000_000.0,
        commission_rate=0.0,
    )

    # 无交易: total_return = 0
    summary = engine.run([make_bar(close=100.0)])
    assert summary.total_return == 0.0


# ============================================================
# get_state() 快照测试
# ============================================================


def test_get_state_returns_snapshot() -> None:
    """get_state 返回 EngineSnapshot 快照。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    engine.process_event(make_bar(close=100.0))

    state = engine.get_state()

    assert isinstance(state, EngineSnapshot)
    assert state.cash == 1_000_000.0
    assert state.equity == 1_000_000.0
    assert state.n_events_processed == 1
    assert state.n_orders_submitted == 0


def test_get_state_snapshot_is_copy() -> None:
    """快照字段是副本,外部修改不影响引擎。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())
    engine.process_event(make_bar(close=100.0))

    state = engine.get_state()
    state.equity_curve.clear()
    state.market_prices.clear()

    # 引擎内部状态不受影响
    assert len(engine._equity_curve) == 2
    assert len(engine._market_prices) == 1


def test_get_state_reflects_positions() -> None:
    """快照反映当前持仓。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))
    engine.process_event(make_bar(close=110.0, low=95.0, open=100.0))

    state = engine.get_state()

    assert "600519.SH" in state.long_positions
    assert state.long_positions["600519.SH"]["volume"] == 100.0
    assert state.pending_latency_count == 0
    assert state.n_orders_filled == 1


# ============================================================
# 集成测试: 策略回调
# ============================================================


def test_strategy_on_bar_dispatched() -> None:
    """策略的 on_bar 被正确分发。"""
    strategy = _BuyOnFirstBarStrategy()
    engine = EventDrivenEngine(strategy=strategy, latency_model=FixedLatency(0))

    engine.process_event(make_bar(close=100.0))

    assert strategy.submitted is True


def test_strategy_can_submit_via_engine() -> None:
    """策略通过 engine.submit_order 提交订单的集成流程。"""
    strategy = _NoopStrategy()
    engine = EventDrivenEngine(
        strategy=strategy,
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )

    # 模拟策略在第一个事件后提交订单
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # 第二个事件: 订单应撮合
    engine.process_event(make_bar(close=105.0, low=95.0, open=100.0))

    state = engine.get_state()
    assert state.n_orders_filled == 1
    assert "600519.SH" in state.long_positions


def test_multiple_orders_batch_matching() -> None:
    """多个订单同 code 同事件撮合 (next-event 语义: T 提交 → T+1 撮合)。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
    )
    engine.process_event(make_bar(close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=50.0, order_id="o1"))
    engine.submit_order(make_buy_order(price=100.0, volume=50.0, order_id="o2"))

    # T+1 事件: 两笔订单延迟到期, 同事件批量撮合
    engine.process_event(make_bar(close=100.0, low=95.0, open=100.0))

    assert engine._n_filled == 2
    pos = engine._long_positions["600519.SH"]
    assert pos.volume == 100.0  # 50 + 50


# ============================================================
# W6.3.2 新增: 确定性事件时钟 WALL_CLOCK_NS 模式测试
# ============================================================
# 时间戳基准: 1 日 = 86_400_000_000_000 纳秒
DAY_NS = 86_400_000_000_000
# 初始日期: 2023-11-14 00:00:00 UTC+8 (随便选一个基准, 只要单调递增即可)
TS_D0 = 1_700_000_000_000_000_000  # day 0 纳秒
TS_D1 = TS_D0 + DAY_NS  # day 1 纳秒
TS_D2 = TS_D1 + DAY_NS  # day 2 纳秒
TS_D3 = TS_D2 + DAY_NS  # day 3 纳秒


def make_bar_ts(
    ts_event: int,
    code: str = "600519.SH",
    close: float = 100.0,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    volume: float = 10000.0,
) -> BarData:
    """W6.3.2 辅助: 构造带 ts_event/ts_init 的 BarData (WALL_CLOCK_NS 模式用)。"""
    return BarData(
        code=code,
        exchange="SSE",
        period="1d",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=close * volume,
        date=20231114,
        time=0,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def test_event_clock_mode_invalid_raises() -> None:
    """无效事件时钟模式 → ValueError。"""
    with pytest.raises(ValueError, match="event_clock_mode 必须是"):
        EventDrivenEngine(
            strategy=_NoopStrategy(),
            event_clock_mode="BAD_MODE",
        )


def test_wall_clock_requires_positive_ts_event() -> None:
    """WALL_CLOCK_NS 模式下, ts_event<=0 → ValueError (防未设置字段)。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    # make_bar() 默认 ts_event=0
    with pytest.raises(ValueError, match="ts_event 为正"):
        engine.process_event(make_bar(close=100.0))


def test_wall_clock_non_monotonic_raises() -> None:
    """WALL_CLOCK_NS 模式下, ts_event 乱序 → NonMonotonicTimestampError (前视偏差硬门禁)。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))

    # 尝试喂一个更早的事件 (模拟前视偏差)
    with pytest.raises(NonMonotonicTimestampError) as exc_info:
        engine.process_event(make_bar_ts(ts_event=TS_D0, close=99.0))

    err = exc_info.value
    assert err.last_ts == TS_D1
    assert err.curr_ts == TS_D0
    assert "600519.SH" in str(err)


def test_wall_clock_equal_timestamp_raises() -> None:
    """严格大于: ts_event 相等也视为乱序 (同一事件重复) → 抛异常。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))

    with pytest.raises(NonMonotonicTimestampError):
        engine.process_event(make_bar_ts(ts_event=TS_D1, close=101.0))


def test_wall_clock_monotonic_forward_ok() -> None:
    """3 个事件严格递增 → 正常推进, last_ts_event 单调。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    for ts in (TS_D1, TS_D2, TS_D3):
        engine.process_event(make_bar_ts(ts_event=ts, close=100.0))

    state = engine.get_state()
    assert state.event_clock_mode == EventClockMode.WALL_CLOCK_NS
    assert state.last_ts_event == TS_D3
    assert state.n_events_processed == 3


def test_wall_clock_order_submit_ready_ts() -> None:
    """WALL_CLOCK_NS: submit_order 设置 PendingOrder.ready_ts = 当前 ts + 延迟纳秒。

    FixedLatency(0) → days_latency=max(1,0+1)=1 日 → ready_ts = submit_ts + 1 day
    """
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    # Day 1 事件提交订单
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # 延迟队列里只有 1 个, ready_ts = TS_D1 + 1 day = TS_D2
    assert len(engine._latency_queue) == 1
    po = engine._latency_queue[0]
    assert po.ready_ts == TS_D1 + DAY_NS
    assert po.ready_ts == TS_D2


def test_wall_clock_fill_on_ready_ts_event() -> None:
    """WALL_CLOCK_NS: 订单在 ready_ts ≤ 当前 ts 的事件上成交 (next-event 语义)。

    TS_D1 提交, ready_ts=TS_D2, 应在 TS_D2 事件撮合。
    """
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # TS_D1 后, ready_ts = TS_D2
    # TS_D2 事件到达: ready_ts ≤ TS_D2 → 撮合
    engine.process_event(make_bar_ts(ts_event=TS_D2, close=100.0, low=95.0, open=100.0))

    state = engine.get_state()
    assert state.n_orders_filled == 1
    assert "600519.SH" in state.long_positions
    assert state.long_positions["600519.SH"]["volume"] == 100.0


def test_wall_clock_not_filled_before_ready_ts() -> None:
    """WALL_CLOCK_NS: 在 ready_ts 之前的事件不撮合 (即使 lat=0 也跨 1 日)。

    FixedLatency(0) → 下一日成交 → TS_D1 提交, TS_D2 才能成交。
    在 TS_D1+1ns (仍为 day1 日内) 不会成交。
    """
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))
    engine.submit_order(make_buy_order(price=100.0, volume=100.0))

    # TS_D1 + 1ns: still < ready_ts=TS_D2, 不撮合
    midday_ns = TS_D1 + 1_000_000_000  # +1秒 = 10^9 ns
    engine.process_event(
        make_bar_ts(ts_event=midday_ns, close=100.0, low=95.0, open=100.0)
    )

    state = engine.get_state()
    assert state.n_orders_filled == 0
    assert state.pending_latency_count == 1  # 仍在延迟队列


def test_wall_clock_equity_curve_has_timestamps() -> None:
    """WALL_CLOCK_NS: EngineSummary.equity_curve 与 equity_timestamps_ns 对齐。

    - equity_curve 长度 = n_events + 1 (初始点 + 每事件一个点)
    - equity_timestamps_ns[0] = 0 (初始点无事件时间)
    - 后续时间戳 = 对应事件的 ts_event
    """
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    events = [
        make_bar_ts(ts_event=TS_D1, close=100.0),
        make_bar_ts(ts_event=TS_D2, close=110.0),
        make_bar_ts(ts_event=TS_D3, close=120.0),
    ]
    summary = engine.run(events)

    assert summary.event_clock_mode == EventClockMode.WALL_CLOCK_NS
    assert summary.n_events == 3
    assert len(summary.equity_curve) == 4  # init + 3 events
    assert len(summary.equity_timestamps_ns) == 4
    # 初始点
    assert summary.equity_timestamps_ns[0] == 0
    assert summary.equity_curve[0] == 1_000_000.0
    # 后续点对应事件时间戳
    assert summary.equity_timestamps_ns[1:] == [TS_D1, TS_D2, TS_D3]


def test_wall_clock_mono_mode_still_uses_indices() -> None:
    """默认 MONOTONIC_INDEX 下: EngineSummary.equity_timestamps_ns=[] (向后兼容)。"""
    engine = EventDrivenEngine(strategy=_NoopStrategy())  # 默认
    engine.process_event(make_bar(close=100.0))
    engine.process_event(make_bar(close=101.0))
    summary = engine.get_summary()

    assert summary.event_clock_mode == EventClockMode.MONOTONIC_INDEX
    assert summary.equity_timestamps_ns == []
    assert len(summary.equity_curve) == 3  # init + 2 events
    # 每一项都是标量
    for v in summary.equity_curve:
        assert isinstance(v, (int, float))
        assert not isinstance(v, tuple)


def test_wall_clock_trade_records_have_ts_event_ns() -> None:
    """WALL_CLOCK_NS: 成交记录的 ts_event_ns 字段 = 撮合事件的 ts_event。"""
    engine = EventDrivenEngine(
        strategy=_NoopStrategy(),
        latency_model=FixedLatency(0),
        commission_rate=0.0,
        event_clock_mode=EventClockMode.WALL_CLOCK_NS,
    )
    engine.process_event(make_bar_ts(ts_event=TS_D1, close=100.0))
    engine.submit_order(
        make_buy_order(price=100.0, volume=100.0, order_id="fill-ts-test")
    )
    engine.process_event(make_bar_ts(ts_event=TS_D2, close=100.0, low=95.0, open=100.0))

    summary = engine.get_summary()
    fills = [t for t in summary.trade_records if t.get("status") == "ALL_TRADED"]
    assert len(fills) == 1
    assert fills[0]["ts_event_ns"] == TS_D2  # 撮合发生在 TS_D2
