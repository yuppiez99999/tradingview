"""wt_spread_strategy 单元测试 — WonderTrader 价差策略框架"""

import pytest

from utils.wt_spread_strategy import (
    ETF_PAIR_SPREADS,
    SpreadBacktester,
    SpreadCalculator,
    SpreadContext,
    SpreadDefinition,
    SpreadStrategy,
    _leg_code,
    _leg_direction,
    _leg_ratio,
)
from utils.wt_structs import BarData, TickData


class TestLegAccessors:
    def test_leg_code(self):
        assert _leg_code({"code": "510300.SH"}) == "510300.SH"

    def test_leg_ratio_default(self):
        assert _leg_ratio({"code": "A"}) == 1.0

    def test_leg_ratio_custom(self):
        assert _leg_ratio({"code": "A", "ratio": 0.5}) == 0.5

    def test_leg_direction_default(self):
        assert _leg_direction({"code": "A"}) == "BUY"

    def test_leg_direction_sell(self):
        assert _leg_direction({"code": "A", "direction": "SELL"}) == "SELL"


class TestSpreadDefinition:
    def test_defaults(self):
        sd = SpreadDefinition(name="TEST", legs=[{"code": "A"}])
        assert sd.name == "TEST"
        assert sd.spread_type == "ratio"
        assert sd.description == ""

    def test_custom(self):
        sd = SpreadDefinition(
            name="SPD",
            legs=[{"code": "A"}, {"code": "B"}],
            spread_type="diff",
            description="test spread",
        )
        assert sd.spread_type == "diff"
        assert sd.description == "test spread"
        assert len(sd.legs) == 2


class TestSpreadCalculator:
    def test_calc_price_buy_sell(self):
        spread = SpreadDefinition(
            name="SPD",
            legs=[
                {"code": "A", "ratio": 1.0, "direction": "BUY"},
                {"code": "B", "ratio": 1.0, "direction": "SELL"},
            ],
        )
        prices = {"A": 10.0, "B": 8.0}
        assert SpreadCalculator.calc_spread_price(spread, prices) == 2.0

    def test_calc_price_ratio(self):
        spread = SpreadDefinition(
            name="SPD",
            legs=[
                {"code": "A", "ratio": 2.0, "direction": "BUY"},
                {"code": "B", "ratio": 1.0, "direction": "SELL"},
            ],
        )
        prices = {"A": 5.0, "B": 3.0}
        assert SpreadCalculator.calc_spread_price(spread, prices) == 7.0

    def test_calc_price_missing_code(self):
        spread = SpreadDefinition(
            name="SPD",
            legs=[{"code": "A", "ratio": 1.0, "direction": "BUY"}],
        )
        prices = {"B": 10.0}
        assert SpreadCalculator.calc_spread_price(spread, prices) == 0.0

    def test_calc_price_empty_legs(self):
        spread = SpreadDefinition(name="SPD", legs=[])
        assert SpreadCalculator.calc_spread_price(spread, {"A": 10.0}) == 0.0

    def test_calc_spread_bars(self):
        spread = SpreadDefinition(
            name="SPD",
            legs=[
                {"code": "A", "ratio": 1.0, "direction": "BUY"},
                {"code": "B", "ratio": 1.0, "direction": "SELL"},
            ],
        )
        bar_a = BarData(
            code="A.SH",
            exchange="SSE",
            period="1d",
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
        )
        bar_b = BarData(
            code="B.SH",
            exchange="SSE",
            period="1d",
            open=8,
            high=8.5,
            low=7.5,
            close=8.2,
            volume=2000,
        )
        bars = {"A": bar_a, "B": bar_b}
        o, h, lo, c = SpreadCalculator.calc_spread_bars(spread, bars)
        assert o == pytest.approx(2.0)
        assert h == pytest.approx(2.5)
        assert lo == pytest.approx(1.5)
        assert c == pytest.approx(2.3)


class TestSpreadContext:
    def _make_spread(self):
        return SpreadDefinition(
            name="SPD.TEST",
            legs=[
                {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
                {"code": "510050.SH", "ratio": 1.0, "direction": "SELL"},
            ],
            spread_type="diff",
        )

    def _make_strategy(self, spread):
        class TestStrategy(SpreadStrategy):
            def on_spread_tick(self, ctx, spread_price, leg_prices):
                pass

            def on_spread_bar(self, ctx, spread_bar, leg_bars):
                pass

            def on_trade(self, ctx, trade):
                pass

            def on_position(self, ctx, position):
                pass

        return TestStrategy("test", spread)

    def test_init(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        assert ctx.leg_positions["510300.SH"] == 0.0
        assert ctx.leg_positions["510050.SH"] == 0.0
        assert ctx.cash == 1_000_000.0

    def test_enter_long_spread(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        result = ctx.enter_long_spread(100, prices)
        assert result is True
        assert ctx.leg_positions["510300.SH"] == 100
        assert ctx.leg_positions["510050.SH"] == -100
        assert len(ctx.trades) == 2

    def test_enter_long_zero_price(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 0.0, "510050.SH": 3.0}
        result = ctx.enter_long_spread(100, prices)
        assert result is False

    def test_enter_short_spread(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        result = ctx.enter_short_spread(100, prices)
        assert result is True
        assert ctx.leg_positions["510300.SH"] == -100
        assert ctx.leg_positions["510050.SH"] == 100

    def test_exit_long_spread(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        ctx.enter_long_spread(100, prices)
        ctx.exit_long_spread(100, prices)
        assert ctx.leg_positions["510300.SH"] == 0.0
        assert ctx.leg_positions["510050.SH"] == 0.0

    def test_get_spread_position(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        ctx.enter_long_spread(100, prices)
        assert ctx.get_spread_position() == 100

    def test_get_leg_position(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        ctx.enter_long_spread(100, prices)
        assert ctx.get_leg_position("510300.SH") == 100
        assert ctx.get_leg_position("510050.SH") == -100
        assert ctx.get_leg_position("UNKNOWN") == 0.0

    def test_get_total_equity(self):
        spread = self._make_spread()
        strategy = self._make_strategy(spread)
        ctx = SpreadContext(strategy, spread)
        prices = {"510300.SH": 4.0, "510050.SH": 3.0}
        ctx.enter_long_spread(100, prices)
        equity = ctx.get_total_equity(prices)
        assert equity != 1_000_000.0


class TestSpreadBacktester:
    def test_run_on_ticks(self):
        spread = SpreadDefinition(
            name="SPD.TEST",
            legs=[
                {"code": "A.SH", "ratio": 1.0, "direction": "BUY"},
                {"code": "B.SH", "ratio": 1.0, "direction": "SELL"},
            ],
        )

        class MyStrategy(SpreadStrategy):
            def __init__(self, name, spread):
                super().__init__(name, spread)
                self.entered = False

            def on_spread_tick(self, ctx, spread_price, leg_prices):
                if spread_price > 1.0 and not self.entered:
                    ctx.enter_long_spread(10, leg_prices)
                    self.entered = True

            def on_spread_bar(self, ctx, spread_bar, leg_bars):
                pass

            def on_trade(self, ctx, trade):
                pass

            def on_position(self, ctx, position):
                pass

        strategy = MyStrategy("test", spread)
        bt = SpreadBacktester(strategy, initial_capital=1_000_000)

        ticks = []
        for i in range(5):
            p_a = 5.0 + i * 0.1
            p_b = 3.0 + i * 0.05
            tick_dict = {
                "A.SH": TickData(
                    code="A.SH",
                    exchange="SSE",
                    price=p_a,
                    open=p_a,
                    high=p_a,
                    low=p_a,
                    pre_close=p_a,
                    volume=100,
                    amount=p_a * 100,
                ),
                "B.SH": TickData(
                    code="B.SH",
                    exchange="SSE",
                    price=p_b,
                    open=p_b,
                    high=p_b,
                    low=p_b,
                    pre_close=p_b,
                    volume=200,
                    amount=p_b * 200,
                ),
            }
            ticks.append(tick_dict)

        report = bt.run_on_ticks(ticks)
        assert report["strategy"] == "test"
        assert report["n_ticks"] == 5
        assert "total_return" in report
        assert "max_drawdown" in report

    def test_empty_ticks(self):
        spread = SpreadDefinition(
            name="SPD", legs=[{"code": "A.SH", "direction": "BUY"}]
        )

        class EmptyStrategy(SpreadStrategy):
            def on_spread_tick(self, ctx, spread_price, leg_prices):
                pass

            def on_spread_bar(self, ctx, spread_bar, leg_bars):
                pass

            def on_trade(self, ctx, trade):
                pass

            def on_position(self, ctx, position):
                pass

        strategy = EmptyStrategy("test", spread)
        bt = SpreadBacktester(strategy)
        report = bt.run_on_ticks([])
        assert report == {}


class TestETFPairSpreads:
    def test_spd_300_50(self):
        spd = ETF_PAIR_SPREADS["SPD.300-50"]
        assert spd.name == "SPD.300-50"
        assert len(spd.legs) == 2
        assert _leg_code(spd.legs[0]) == "510300.SH"
        assert _leg_direction(spd.legs[0]) == "BUY"
        assert _leg_code(spd.legs[1]) == "510050.SH"
        assert _leg_direction(spd.legs[1]) == "SELL"

    def test_spd_500_1000(self):
        spd = ETF_PAIR_SPREADS["SPD.500-1000"]
        assert spd.name == "SPD.500-1000"
        assert len(spd.legs) == 2

    def test_spd_kechuang(self):
        spd = ETF_PAIR_SPREADS["SPD.KECHUANG"]
        assert spd.name == "SPD.KECHUANG"
        assert len(spd.legs) == 2

    def test_spd_300_if(self):
        spd = ETF_PAIR_SPREADS["SPD.300-IF"]
        assert spd.name == "SPD.300-IF"
        assert spd.spread_type == "weighted"
        assert _leg_code(spd.legs[1]) == "IF.CFFEX"

    def test_all_spreads_have_two_legs(self):
        for name, spd in ETF_PAIR_SPREADS.items():
            assert len(spd.legs) == 2, f"{name} should have 2 legs"
