# -*- coding: utf-8 -*-
"""wt_hedge_strategy 单元测试 — WonderTrader 对冲策略框架"""
import pytest

from utils.wt_hedge_strategy import (
    HedgePosition,
    PortfolioMetrics,
    HedgeStrategy,
    HedgeContext,
    BetaHedgeStrategy,
    TailRiskHedgeStrategy,
    DynamicHedgeStrategy,
)


def make_strategy(config=None):
    class TestHedgeStrategy(HedgeStrategy):
        def on_rebalance(self, ctx):
            pass
    return TestHedgeStrategy("test", config=config)


class TestHedgePosition:
    def test_defaults(self):
        pos = HedgePosition(code="A")
        assert pos.direction == "LONG"
        assert pos.volume == 0.0
        assert pos.beta == 1.0
        assert pos.delta == 1.0

    def test_custom(self):
        pos = HedgePosition(code="A", direction="SHORT", volume=100, beta=-1.0)
        assert pos.direction == "SHORT"
        assert pos.volume == 100


class TestPortfolioMetrics:
    def test_defaults(self):
        m = PortfolioMetrics()
        assert m.total_value == 0.0
        assert m.net_exposure == 0.0
        assert m.hedge_ratio == 0.0


class TestHedgeStrategyInit:
    def test_defaults(self):
        s = make_strategy()
        assert s.name == "test"
        assert s.target_hedge_ratio == 0.3
        assert s.max_hedge_ratio == 0.5
        assert s.long_positions == {}
        assert s.short_positions == {}

    def test_custom_config(self):
        s = make_strategy(config={"target_hedge_ratio": 0.4, "max_hedge_ratio": 0.6})
        assert s.target_hedge_ratio == 0.4
        assert s.max_hedge_ratio == 0.6


class TestAddLongPosition:
    def test_new(self):
        s = make_strategy()
        s.add_long_position("600519.SH", 100, 1800.0, beta=1.2)
        assert "600519.SH" in s.long_positions
        assert s.long_positions["600519.SH"].volume == 100
        assert s.long_positions["600519.SH"].avg_price == 1800.0

    def test_add_existing(self):
        s = make_strategy()
        s.add_long_position("A", 100, 10.0)
        s.add_long_position("A", 100, 20.0)
        assert s.long_positions["A"].volume == 200
        assert s.long_positions["A"].avg_price == 15.0


class TestAddShortPosition:
    def test_new(self):
        s = make_strategy()
        s.add_short_position("IF.CFFEX", 10, 4000.0)
        assert "IF.CFFEX" in s.short_positions
        assert s.short_positions["IF.CFFEX"].volume == 10
        assert s.short_positions["IF.CFFEX"].direction == "SHORT"

    def test_default_delta(self):
        s = make_strategy()
        s.add_short_position("IF.CFFEX", 10, 4000.0)
        assert s.short_positions["IF.CFFEX"].delta == -1.0


class TestCalcPortfolioMetrics:
    def test_empty(self):
        s = make_strategy()
        m = s.calc_portfolio_metrics({})
        assert m.total_value == 0.0

    def test_long_only(self):
        s = make_strategy()
        s.add_long_position("A", 100, 10.0)
        m = s.calc_portfolio_metrics({"A": 12.0})
        assert m.total_value == 1200.0
        assert m.hedge_ratio == 0.0

    def test_with_short(self):
        s = make_strategy()
        s.add_long_position("A", 100, 10.0)
        s.add_short_position("IF.CFFEX", 1, 4000.0)
        m = s.calc_portfolio_metrics({"A": 10.0, "IF.CFFEX": 4000.0})
        assert m.hedge_ratio > 0


class TestHedgeContext:
    def test_init(self):
        s = make_strategy()
        ctx = HedgeContext(s)
        assert ctx.hedge_orders == []
        assert ctx.current_prices == {}

    def test_get_metrics(self):
        s = make_strategy()
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        m = ctx.get_portfolio_metrics({"A": 12.0})
        assert m.total_value == 1200.0

    def test_open_hedge(self):
        s = make_strategy()
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        ctx.current_prices = {"A": 10.0, "IF.CFFEX": 4000.0}
        result = ctx.open_hedge("IF.CFFEX", 1, price=4000.0)
        assert result is True
        assert len(ctx.hedge_orders) == 1

    def test_open_hedge_zero_hands(self):
        s = make_strategy()
        ctx = HedgeContext(s)
        assert ctx.open_hedge("IF.CFFEX", 0) is False

    def test_close_hedge(self):
        s = make_strategy()
        s.add_short_position("IF.CFFEX", 10, 4000.0)
        ctx = HedgeContext(s)
        result = ctx.close_hedge("IF.CFFEX", 5, price=4000.0)
        assert result is True
        assert s.short_positions["IF.CFFEX"].volume == 5

    def test_close_hedge_not_exists(self):
        s = make_strategy()
        ctx = HedgeContext(s)
        assert ctx.close_hedge("UNKNOWN", 1) is False

    def test_adjust_hedge_skip(self):
        s = make_strategy()
        ctx = HedgeContext(s)
        ctx.current_prices = {"IF.CFFEX": 4000.0}
        result = ctx.adjust_hedge(0.3, "IF.CFFEX")
        assert result["action"] == "SKIP"


class TestBetaHedgeStrategy:
    def test_init(self):
        s = BetaHedgeStrategy()
        assert s.name == "beta_hedge"
        assert s.hedge_code == "IF.CFFEX"

    def test_on_rebalance(self):
        s = BetaHedgeStrategy()
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        ctx.current_prices = {"A": 10.0, "IF.CFFEX": 4000.0}
        s.on_rebalance(ctx)


class TestTailRiskHedgeStrategy:
    def test_init(self):
        s = TailRiskHedgeStrategy()
        assert s.name == "tail_risk_hedge"
        assert s.vol_threshold == 0.25

    def test_on_rebalance(self):
        s = TailRiskHedgeStrategy()
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        ctx.current_prices = {"A": 10.0, "IF.CFFEX": 4000.0}
        s.on_rebalance(ctx)


class TestDynamicHedgeStrategy:
    def test_init(self):
        s = DynamicHedgeStrategy()
        assert s.name == "dynamic_hedge"
        assert s.market_state == "neutral"

    def test_set_market_state(self):
        s = DynamicHedgeStrategy()
        s.set_market_state("bull")
        assert s.market_state == "bull"

    def test_on_rebalance_bull(self):
        s = DynamicHedgeStrategy()
        s.set_market_state("bull")
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        ctx.current_prices = {"A": 10.0, "IF.CFFEX": 4000.0}
        s.on_rebalance(ctx)

    def test_on_rebalance_bear(self):
        s = DynamicHedgeStrategy()
        s.set_market_state("bear")
        s.add_long_position("A", 100, 10.0)
        ctx = HedgeContext(s)
        ctx.current_prices = {"A": 10.0, "IF.CFFEX": 4000.0}
        s.on_rebalance(ctx)