"""wt_tick_engine 单元测试 — Tick 级事件驱动回测引擎

覆盖:
- TickMatcher: 限价/市价/滑点/手续费/印花税/代码不匹配/Tick量限制
- TickBacktestEngine: 初始化/重置/策略/数据加载/下单/撮合/持仓/权益/回测/报告
- ticks_from_csv / bars_from_csv / run_tick_backtest 便捷函数
"""

from __future__ import annotations

import csv
import os
import tempfile

from utils.wt_structs import BarData, OrderData, PositionData, TickData, TradeData
from utils.wt_tick_engine import (
    TickBacktestEngine,
    TickMatcher,
    bars_from_csv,
    run_tick_backtest,
    ticks_from_csv,
)

# ============================================================
# 测试辅助
# ============================================================


def make_tick(
    code: str = "600519.SH",
    exchange: str = "SSE",
    price: float = 100.0,
    volume: float = 1000.0,
    timestamp: float = 1000.0,
) -> TickData:
    return TickData(
        code=code,
        exchange=exchange,
        price=price,
        open=price,
        high=price,
        low=price,
        pre_close=price,
        volume=volume,
        amount=price * volume,
        timestamp=timestamp,
        datetime_str="2026-08-18 10:00:00",
        date=20260818,
        time=100000,
    )


def make_order(
    code: str = "600519.SH",
    exchange: str = "SSE",
    direction: str = "BUY",
    price: float = 100.0,
    volume: float = 100.0,
    order_type: str = "LIMIT",
    offset: str = "OPEN",
    order_id: str = "O_1",
) -> OrderData:
    return OrderData(
        order_id=order_id,
        code=code,
        exchange=exchange,
        direction=direction,
        offset=offset,
        order_type=order_type,
        price=price,
        volume=volume,
        status="NOT_REPORTED",
        timestamp=1000.0,
    )


# ============================================================
# TickMatcher
# ============================================================


class TestTickMatcher:
    """TickMatcher 撮合器测试"""

    def test_init_defaults(self):
        m = TickMatcher()
        assert m.slippage_rate == 0.001
        assert m.commission_rate == 0.0003
        assert m.min_commission == 5.0
        assert m.stamp_duty == 0.001

    def test_init_custom(self):
        m = TickMatcher(slippage_rate=0.002, commission_rate=0.0005, min_commission=10.0, stamp_duty=0.002)
        assert m.slippage_rate == 0.002
        assert m.commission_rate == 0.0005
        assert m.min_commission == 10.0
        assert m.stamp_duty == 0.002

    def test_match_code_mismatch_returns_none(self):
        m = TickMatcher()
        order = make_order(code="000001.SZ", exchange="SZSE")
        tick = make_tick(code="600519.SH", exchange="SSE")
        assert m.match_order(order, tick) is None

    def test_match_limit_buy_price_too_low(self):
        """限价买单, 市价高于限价 → 不成交"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=99.0)
        tick = make_tick(price=100.0)
        assert m.match_order(order, tick) is None

    def test_match_limit_sell_price_too_high(self):
        """限价卖单, 市价低于限价 → 不成交"""
        m = TickMatcher()
        order = make_order(direction="SELL", price=101.0)
        tick = make_tick(price=100.0)
        assert m.match_order(order, tick) is None

    def test_match_limit_buy_success(self):
        """限价买单, 市价 <= 限价 → 成交"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=101.0, volume=100.0)
        tick = make_tick(price=100.0, volume=10000.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.direction == "BUY"
        assert trade.code == "600519.SH"
        assert trade.volume > 0
        # 买入滑点向上
        assert trade.price > 100.0

    def test_match_limit_sell_success(self):
        """限价卖单, 市价 >= 限价 → 成交"""
        m = TickMatcher()
        order = make_order(direction="SELL", price=99.0, volume=100.0)
        tick = make_tick(price=100.0, volume=10000.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.direction == "SELL"
        # 卖出滑点向下
        assert trade.price < 100.0

    def test_match_market_order(self):
        """市价单直接成交"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=0.0, volume=100.0, order_type="MARKET")
        tick = make_tick(price=100.0, volume=10000.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.volume > 0

    def test_match_buy_slippage(self):
        """买单成交价 = tick_price * (1 + slippage)"""
        m = TickMatcher(slippage_rate=0.01)
        order = make_order(direction="BUY", price=200.0, volume=100.0)
        tick = make_tick(price=100.0, volume=10000.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        # 100 * 1.01 = 101, 量化到 tick_size=0.01
        assert abs(trade.price - 101.0) < 0.1

    def test_match_sell_slippage(self):
        """卖单成交价 = tick_price * (1 - slippage)"""
        m = TickMatcher(slippage_rate=0.01)
        order = make_order(direction="SELL", price=50.0, volume=100.0)
        tick = make_tick(price=100.0, volume=10000.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert abs(trade.price - 99.0) < 0.1

    def test_match_volume_capped_by_tick(self):
        """成交量不超过 Tick 成交量的 10%"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=200.0, volume=10000.0)
        tick = make_tick(price=100.0, volume=1000.0)  # 10% = 100
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.volume <= 100.0

    def test_match_zero_tick_volume(self):
        """Tick 成交量为 0 → 用订单剩余量"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=200.0, volume=100.0)
        tick = make_tick(price=100.0, volume=0.0)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.volume == 100.0

    def test_match_fully_traded_order(self):
        """订单已全部成交 → 返回 None"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=200.0, volume=100.0)
        order.traded_volume = 100.0  # 已全部成交
        tick = make_tick(price=100.0, volume=10000.0)
        assert m.match_order(order, tick) is None

    def test_match_trade_data_fields(self):
        """成交回报字段完整性"""
        m = TickMatcher()
        order = make_order(direction="BUY", price=200.0, volume=100.0, order_id="O_TEST")
        tick = make_tick(price=100.0, volume=10000.0, timestamp=1234.5)
        trade = m.match_order(order, tick)
        assert trade is not None
        assert trade.order_id == "O_TEST"
        assert trade.exchange == "SSE"
        assert trade.offset == "OPEN"
        assert trade.timestamp == 1234.5
        assert trade.trade_id.startswith("T_")


# ============================================================
# TickBacktestEngine
# ============================================================


class TestTickBacktestEngine:
    """TickBacktestEngine 回测引擎测试"""

    def test_init_defaults(self):
        e = TickBacktestEngine()
        assert e.initial_capital == 1_000_000.0
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.pending_orders == []
        assert e.trades == []
        assert e.equity_curve == []
        assert e.strategy is None
        assert e.tick_data == {}

    def test_init_custom(self):
        e = TickBacktestEngine(initial_capital=500_000, slippage_rate=0.002)
        assert e.initial_capital == 500_000
        assert e.cash == 500_000
        assert e.matcher.slippage_rate == 0.002

    def test_reset(self):
        e = TickBacktestEngine()
        e.cash = 100
        e.positions = {"x": None}
        e.pending_orders = [None]
        e.trades = [None]
        e.equity_curve = [None]
        e.reset()
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.pending_orders == []
        assert e.trades == []
        assert e.equity_curve == []

    def test_set_strategy(self):
        e = TickBacktestEngine()

        class DummyStrategy:
            def on_tick(self, ctx, tick):
                pass

        s = DummyStrategy()
        e.set_strategy(s)
        assert e.strategy is s

    def test_load_tick_data(self):
        e = TickBacktestEngine()
        ticks = {"600519.SH": [make_tick()]}
        e.load_tick_data(ticks)
        assert e.tick_data == ticks

    def test_load_bar_data(self):
        e = TickBacktestEngine()
        bars = {"600519.SH": [BarData(code="600519.SH", exchange="SSE", period="1d", open=1, high=2, low=0.5, close=1.5, volume=100)]}
        e.load_bar_data(bars)
        assert e.bar_data == bars

    def test_send_order(self):
        e = TickBacktestEngine()
        oid = e.send_order("600519.SH", "BUY", 100, 100.0)
        assert oid == "O_1"
        assert len(e.pending_orders) == 1
        order = e.pending_orders[0]
        assert order.code == "600519.SH"
        assert order.direction == "BUY"
        assert order.volume == 100

    def test_send_order_increments_id(self):
        e = TickBacktestEngine()
        oid1 = e.send_order("600519.SH", "BUY", 100)
        oid2 = e.send_order("600519.SH", "BUY", 100)
        assert oid1 == "O_1"
        assert oid2 == "O_2"

    def test_buy(self):
        e = TickBacktestEngine()
        oid = e.buy("600519.SH", 100, 100.0)
        assert oid == "O_1"
        assert e.pending_orders[0].direction == "BUY"
        assert e.pending_orders[0].offset == "OPEN"

    def test_sell(self):
        e = TickBacktestEngine()
        oid = e.sell("600519.SH", 100, 100.0)
        assert oid == "O_1"
        assert e.pending_orders[0].direction == "SELL"
        assert e.pending_orders[0].offset == "CLOSE"

    def test_get_position_none(self):
        e = TickBacktestEngine()
        assert e.get_position("600519.SH") is None

    def test_get_position_existing(self):
        e = TickBacktestEngine()
        pos = PositionData(code="600519.SH", exchange="SSE", volume=100, avg_price=50)
        e.positions["600519.SH"] = pos
        assert e.get_position("600519.SH") is pos

    def test_get_position_profit_empty(self):
        e = TickBacktestEngine()
        assert e.get_position_profit() == 0

    def test_get_position_profit_with_positions(self):
        e = TickBacktestEngine()
        e.positions["A"] = PositionData(code="A", exchange="SSE", volume=100, avg_price=50, last_price=60)
        e.positions["B"] = PositionData(code="B", exchange="SSE", volume=200, avg_price=30, last_price=25)
        # A: (60-50)*100 = 1000, B: (25-30)*200 = -1000
        assert e.get_position_profit() == 0.0

    def test_get_total_equity_empty(self):
        e = TickBacktestEngine()
        assert e.get_total_equity() == 1_000_000.0

    def test_get_total_equity_with_positions(self):
        e = TickBacktestEngine()
        e.cash = 500_000
        e.positions["A"] = PositionData(code="A", exchange="SSE", volume=100, avg_price=50, last_price=60)
        # 500000 + 100*60 = 506000
        assert e.get_total_equity() == 506_000.0

    def test_run_empty_returns_empty_dict(self):
        e = TickBacktestEngine()
        result = e.run()
        assert result == {}

    def test_run_with_ticks_no_strategy(self):
        """无策略回测 — 仅记录权益曲线"""
        e = TickBacktestEngine()
        ticks = {
            "600519.SH": [
                make_tick(price=100, timestamp=1.0, volume=10000),
                make_tick(price=101, timestamp=2.0, volume=10000),
            ]
        }
        e.load_tick_data(ticks)
        result = e.run()
        assert result["engine"] == "TickBacktestEngine"
        assert result["n_ticks"] == 2
        assert result["initial_capital"] == 1_000_000.0

    def test_run_with_strategy_on_tick(self):
        """带策略回测 — on_tick 回调被调用"""
        e = TickBacktestEngine()

        call_count = [0]

        class MyStrategy:
            def on_tick(self, ctx, tick):
                call_count[0] += 1
                if call_count[0] == 1:
                    ctx.buy(tick.code, 100, tick.price + 10)

        e.set_strategy(MyStrategy())
        ticks = {"600519.SH": [make_tick(price=100, timestamp=1.0, volume=10000)]}
        e.load_tick_data(ticks)
        result = e.run()
        assert call_count[0] == 1
        assert result["n_ticks"] == 1

    def test_run_generates_report_fields(self):
        e = TickBacktestEngine()
        ticks = {"600519.SH": [make_tick(price=100, timestamp=1.0, volume=10000)]}
        e.load_tick_data(ticks)
        result = e.run()
        for field in [
            "engine",
            "initial_capital",
            "final_equity",
            "total_return",
            "max_drawdown",
            "sharpe_ratio",
            "win_rate",
            "n_ticks",
        ]:
            assert field in result

    def test_match_pending_orders_fills(self):
        e = TickBacktestEngine()
        e.buy("600519.SH", 100, 200.0)  # 限价 200, 市价 100 → 成交
        tick = make_tick(price=100, volume=10000)
        e._match_pending_orders(tick)
        assert len(e.trades) == 1
        assert len(e.pending_orders) == 0

    def test_process_trade_buy_updates_position(self):
        e = TickBacktestEngine()
        trade = TradeData(
            trade_id="T1", order_id="O1", code="600519.SH", exchange="SSE",
            direction="BUY", offset="OPEN", price=100, volume=50, amount=5000, timestamp=1,
        )
        e._process_trade(trade)
        pos = e.positions["600519.SH"]
        assert pos.volume == 50
        assert pos.avg_price == 100
        assert e.cash < 1_000_000  # 扣减

    def test_process_trade_sell_reduces_position(self):
        e = TickBacktestEngine()
        # 先建仓
        e.positions["600519.SH"] = PositionData(code="600519.SH", exchange="SSE", volume=100, avg_price=50)
        trade = TradeData(
            trade_id="T1", order_id="O1", code="600519.SH", exchange="SSE",
            direction="SELL", offset="CLOSE", price=60, volume=30, amount=1800, timestamp=1,
        )
        e._process_trade(trade)
        pos = e.positions["600519.SH"]
        assert pos.volume == 70
        assert e.cash > 1_000_000  # 增加

    def test_update_position_prices(self):
        e = TickBacktestEngine()
        e.positions["A"] = PositionData(code="A", exchange="SSE", volume=100, last_price=50)
        e._update_position_prices({"A": 55})
        assert e.positions["A"].last_price == 55


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """ticks_from_csv / bars_from_csv / run_tick_backtest 测试"""

    def test_ticks_from_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "price", "open", "high", "low", "pre_close", "volume", "amount"])
            writer.writeheader()
            writer.writerow({"timestamp": "1000", "price": "100", "open": "99", "high": "101", "low": "98", "pre_close": "99", "volume": "1000", "amount": "100000"})
            path = f.name
        try:
            ticks = ticks_from_csv(path, "600519.SH", "SSE")
            assert len(ticks) == 1
            assert ticks[0].code == "600519.SH"
            assert ticks[0].price == 100.0
        finally:
            os.unlink(path)

    def test_bars_from_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerow({"date": "2026-08-18", "open": "100", "high": "105", "low": "99", "close": "103", "volume": "10000"})
            path = f.name
        try:
            bars = bars_from_csv(path, "600519.SH", "SSE", "1d")
            assert len(bars) == 1
            assert bars[0].code == "600519.SH"
            assert bars[0].close == 103.0
            assert bars[0].date == 20260818
        finally:
            os.unlink(path)

    def test_run_tick_backtest_empty(self):
        result = run_tick_backtest(strategy=None, tick_data={})
        assert result == {}

    def test_run_tick_backtest_with_ticks(self):
        ticks = {"600519.SH": [make_tick(price=100, timestamp=1.0, volume=10000)]}
        result = run_tick_backtest(strategy=None, tick_data=ticks, initial_capital=500_000)
        assert result["engine"] == "TickBacktestEngine"
        assert result["initial_capital"] == 500_000
