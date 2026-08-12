"""adapters.py 单元测试 — 覆盖 _EngineBackedHedgeContext 和 StrategyAdapter。

覆盖矩阵:
    _EngineBackedHedgeContext:
        - open_hedge: 正确创建 SELL 订单并提交
        - close_hedge: 正确创建 BUY 订单并提交
        - 参数校验 (hands <= 0, price <= 0)
        - 订单 ID 格式、交易所提取
        - order_submitter 返回 False 的处理
        - get_submitted_orders 返回列表

    StrategyAdapter:
        - dispatch_tick: 更新 prices + 调用 on_tick
        - dispatch_bar: 更新 prices + 调用 on_bar
        - dispatch_rebalance: 调用 on_rebalance
        - update_prices: 批量更新
        - 与 mock 策略集成测试
"""
from __future__ import annotations

import pytest

from utils.backtest.adapters import (
    OrderSubmitter,
    StrategyAdapter,
    _EngineBackedHedgeContext,
)
from utils.wt_hedge_strategy import HedgeStrategy
from utils.wt_structs import BarData, OrderData, TickData

# ============================================================
# Mock 策略 (最小实现,用于测试)
# ============================================================

class _MockHedgeStrategy(HedgeStrategy):
    """测试用的最小对冲策略,记录回调调用次数。"""

    def __init__(self) -> None:
        super().__init__(name="mock_strategy")
        self.tick_calls: list[tuple] = []
        self.bar_calls: list[tuple] = []
        self.rebalance_calls: int = 0

    def on_rebalance(self, ctx) -> None:  # type: ignore[override]
        self.rebalance_calls += 1

    def on_tick(self, ctx, tick: TickData) -> None:  # type: ignore[override]
        self.tick_calls.append((tick.code, tick.price))

    def on_bar(self, ctx, bar: BarData) -> None:  # type: ignore[override]
        self.bar_calls.append((bar.code, bar.close))


@pytest.fixture
def mock_strategy() -> _MockHedgeStrategy:
    return _MockHedgeStrategy()


@pytest.fixture
def capture_submitter() -> tuple[OrderSubmitter, list[OrderData]]:
    """创建捕获订单的 submitter。"""
    orders: list[OrderData] = []

    def submitter(order: OrderData) -> bool:
        orders.append(order)
        return True

    return submitter, orders


@pytest.fixture
def failing_submitter() -> OrderSubmitter:
    """总是返回 False 的 submitter。"""
    def submitter(order: OrderData) -> bool:
        return False

    return submitter


# ============================================================
# _EngineBackedHedgeContext 测试
# ============================================================

def test_open_hedge_creates_sell_order(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 创建 SELL OPEN 订单并通过 submitter 提交。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.open_hedge("IF.CFFEX", 5.0)

    assert result is True
    assert len(orders) == 1
    order = orders[0]
    assert order.direction == "SELL"
    assert order.offset == "OPEN"
    assert order.code == "IF.CFFEX"
    assert order.volume == 5.0
    assert order.price == 4000.0


def test_open_hedge_order_id_format(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 生成的订单 ID 以 hedge_ 开头。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 1.0)

    order = orders[0]
    assert order.order_id.startswith("hedge_")


def test_open_hedge_exchange_extraction(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 正确提取交易所后缀。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 1.0)

    order = orders[0]
    assert order.exchange == "CFFEX"


def test_open_hedge_exchange_no_dot(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """无点号的代码使用 UNKNOWN 交易所。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["UNKNOWN_CODE"] = 100.0

    ctx.open_hedge("UNKNOWN_CODE", 1.0)

    order = orders[0]
    assert order.exchange == "UNKNOWN"


def test_open_hedge_custom_price(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 使用自定义价格而非当前市价。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 5.0, price=3950.0)

    order = orders[0]
    assert order.price == 3950.0


def test_open_hedge_rejects_negative_hands(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 拒绝 hands <= 0。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.open_hedge("IF.CFFEX", -1.0)
    assert result is False
    assert len(orders) == 0


def test_open_hedge_rejects_zero_hands(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 拒绝 hands = 0。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.open_hedge("IF.CFFEX", 0.0)
    assert result is False
    assert len(orders) == 0


def test_open_hedge_rejects_zero_price(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 拒绝 price <= 0 (无当前价格且无自定义价格)。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    # 不设置 current_prices

    result = ctx.open_hedge("IF.CFFEX", 5.0)
    assert result is False
    assert len(orders) == 0


def test_open_hedge_submitter_failure(
    mock_strategy: _MockHedgeStrategy,
    failing_submitter: OrderSubmitter,
) -> None:
    """open_hedge 在 submitter 返回 False 时不记录订单。"""
    ctx = _EngineBackedHedgeContext(mock_strategy, failing_submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.open_hedge("IF.CFFEX", 5.0)
    assert result is False
    assert len(ctx.get_submitted_orders()) == 0


def test_close_hedge_creates_buy_order(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """close_hedge 创建 BUY CLOSE 订单。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4100.0

    result = ctx.close_hedge("IF.CFFEX", 3.0)

    assert result is True
    assert len(orders) == 1
    order = orders[0]
    assert order.direction == "BUY"
    assert order.offset == "CLOSE"
    assert order.code == "IF.CFFEX"
    assert order.volume == 3.0
    assert order.price == 4100.0


def test_close_hedge_rejects_negative_hands(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """close_hedge 拒绝 hands <= 0。"""
    submitter, orders = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.close_hedge("IF.CFFEX", -1.0)
    assert result is False
    assert len(orders) == 0


def test_close_hedge_submitter_failure(
    mock_strategy: _MockHedgeStrategy,
    failing_submitter: OrderSubmitter,
) -> None:
    """close_hedge 在 submitter 返回 False 时不记录订单。"""
    ctx = _EngineBackedHedgeContext(mock_strategy, failing_submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    result = ctx.close_hedge("IF.CFFEX", 3.0)
    assert result is False
    assert len(ctx.get_submitted_orders()) == 0


def test_get_submitted_orders_returns_copy(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """get_submitted_orders 返回列表副本,外部修改不影响内部状态。"""
    submitter, _ = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 1.0)
    orders = ctx.get_submitted_orders()
    orders.clear()  # 修改返回的列表

    assert len(ctx.get_submitted_orders()) == 1  # 内部状态不受影响


def test_open_hedge_does_not_modify_short_positions(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge 不修改 strategy.short_positions (事件驱动原则)。"""
    submitter, _ = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 5.0)

    # short_positions 应为空 (订单在途,尚未成交)
    assert len(mock_strategy.short_positions) == 0


def test_hedge_orders_logged(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """open_hedge/close_hedge 记录 hedge_orders 日志。"""
    submitter, _ = capture_submitter
    ctx = _EngineBackedHedgeContext(mock_strategy, submitter)
    ctx.current_prices["IF.CFFEX"] = 4000.0

    ctx.open_hedge("IF.CFFEX", 5.0)
    ctx.close_hedge("IF.CFFEX", 2.0)

    assert len(ctx.hedge_orders) == 2
    assert ctx.hedge_orders[0]["action"] == "OPEN_SHORT"
    assert ctx.hedge_orders[1]["action"] == "CLOSE_SHORT"


# ============================================================
# StrategyAdapter 测试
# ============================================================

def test_dispatch_tick_updates_prices_and_calls_on_tick(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
    sample_tick: TickData,
) -> None:
    """dispatch_tick 更新 prices 并调用 strategy.on_tick。"""
    submitter, _ = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    adapter.dispatch_tick(sample_tick)

    # prices 已更新
    assert adapter.context.current_prices["600519.SH"] == sample_tick.price
    # on_tick 被调用
    assert len(mock_strategy.tick_calls) == 1
    assert mock_strategy.tick_calls[0] == ("600519.SH", sample_tick.price)


def test_dispatch_bar_updates_prices_and_calls_on_bar(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
    sample_bar: BarData,
) -> None:
    """dispatch_bar 更新 prices 并调用 strategy.on_bar。"""
    submitter, _ = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    adapter.dispatch_bar(sample_bar)

    # prices 已更新 (使用 bar.close)
    assert adapter.context.current_prices["600519.SH"] == sample_bar.close
    # on_bar 被调用
    assert len(mock_strategy.bar_calls) == 1
    assert mock_strategy.bar_calls[0] == ("600519.SH", sample_bar.close)


def test_dispatch_rebalance_calls_on_rebalance(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """dispatch_rebalance 调用 strategy.on_rebalance。"""
    submitter, _ = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    assert mock_strategy.rebalance_calls == 0
    adapter.dispatch_rebalance()
    assert mock_strategy.rebalance_calls == 1

    # 多次调用
    adapter.dispatch_rebalance()
    assert mock_strategy.rebalance_calls == 2


def test_update_prices_batch(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """update_prices 批量更新价格。"""
    submitter, _ = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    prices = {"600519.SH": 1800.0, "IF.CFFEX": 4000.0}
    adapter.update_prices(prices)

    for code, price in prices.items():
        assert adapter.context.current_prices[code] == price


def test_multiple_dispatches_accumulate(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
    sample_tick: TickData,
    sample_bar: BarData,
) -> None:
    """多次 dispatch 累积调用。"""
    submitter, _ = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    adapter.dispatch_tick(sample_tick)
    adapter.dispatch_bar(sample_bar)
    adapter.dispatch_tick(sample_tick)

    assert len(mock_strategy.tick_calls) == 2
    assert len(mock_strategy.bar_calls) == 1


def test_strategy_adapter_integration_with_order_submission(
    mock_strategy: _MockHedgeStrategy,
    capture_submitter: tuple[OrderSubmitter, list[OrderData]],
) -> None:
    """完整集成: 策略回调中提交订单。"""
    submitter, orders = capture_submitter
    adapter = StrategyAdapter(mock_strategy, submitter)

    # 在 context 中设置价格 (模拟行情已到达)
    adapter.update_prices({"IF.CFFEX": 4000.0})

    # 直接通过 context 提交对冲订单
    adapter.context.open_hedge("IF.CFFEX", 5.0)
    adapter.context.close_hedge("IF.CFFEX", 2.0)

    assert len(orders) == 2
    assert orders[0].direction == "SELL"
    assert orders[0].offset == "OPEN"
    assert orders[1].direction == "BUY"
    assert orders[1].offset == "CLOSE"


# ============================================================
# _EngineBackedHedgeContext._create_hedge_order 静态方法测试
# ============================================================

def test_create_hedge_order_static() -> None:
    """_create_hedge_order 是纯静态方法,可独立测试。"""
    order = _EngineBackedHedgeContext._create_hedge_order(
        code="IF.CFFEX",
        direction="SELL",
        offset="OPEN",
        volume=10.0,
        price=4000.0,
    )

    assert isinstance(order, OrderData)
    assert order.order_id.startswith("hedge_")
    assert order.code == "IF.CFFEX"
    assert order.exchange == "CFFEX"
    assert order.direction == "SELL"
    assert order.offset == "OPEN"
    assert order.order_type == "LIMIT"
    assert order.price == 4000.0
    assert order.volume == 10.0
    assert order.status == "NOT_REPORTED"
    assert order.timestamp > 0


def test_create_hedge_order_unique_ids() -> None:
    """每次调用生成唯一 order_id。"""
    ids = {
        _EngineBackedHedgeContext._create_hedge_order(
            code="IF.CFFEX",
            direction="SELL",
            offset="OPEN",
            volume=1.0,
            price=4000.0,
        ).order_id
        for _ in range(100)
    }
    assert len(ids) == 100  # 所有 ID 唯一
