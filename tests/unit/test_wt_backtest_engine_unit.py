"""wt_backtest_engine 单元测试 — WonderTrader 风格回测引擎

覆盖:
- BacktestEngine: 初始化/重置/手续费/滑点/买入/卖出/权益/回测/报告
- ETFSignalStrategy: 信号生成
- BacktestDataLoader: 合成数据/历史加载
- run_etf_signal_backtest / compare_strategies 便捷函数
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from utils.wt_backtest_engine import (
    BacktestDataLoader,
    BacktestEngine,
    ETFSignalStrategy,
    compare_strategies,
    run_etf_signal_backtest,
)

# ============================================================
# BacktestEngine
# ============================================================


class TestBacktestEngine:
    """BacktestEngine 回测引擎测试"""

    def test_init_defaults(self):
        e = BacktestEngine()
        assert e.initial_capital == 1_000_000.0
        assert e.commission_rate == 0.0003
        assert e.slippage_rate == 0.001
        assert e.min_commission == 5.0
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.trades == []

    def test_init_custom(self):
        e = BacktestEngine(
            initial_capital=500_000, commission_rate=0.0005, slippage_rate=0.002
        )
        assert e.initial_capital == 500_000
        assert e.commission_rate == 0.0005
        assert e.slippage_rate == 0.002

    def test_reset(self):
        e = BacktestEngine()
        e.cash = 100
        e.positions = {"x": {}}
        e.trades = [{}]
        e.reset()
        assert e.cash == 1_000_000.0
        assert e.positions == {}
        assert e.trades == []

    def test_calculate_commission(self):
        e = BacktestEngine(commission_rate=0.0003, min_commission=5.0)
        assert e.calculate_commission(100_000) == pytest.approx(30.0)
        assert e.calculate_commission(100) == 5.0  # min_commission

    def test_calculate_slippage_buy(self):
        e = BacktestEngine(slippage_rate=0.001)
        assert e.calculate_slippage(100, 10, "BUY") == 100.1

    def test_calculate_slippage_sell(self):
        e = BacktestEngine(slippage_rate=0.001)
        assert e.calculate_slippage(100, 10, "SELL") == 99.9

    def test_buy_success(self):
        e = BacktestEngine()
        assert e.buy("600519", 100, 100) is True
        assert "600519" in e.positions
        assert e.positions["600519"]["qty"] == 100
        assert e.cash < 1_000_000
        assert len(e.trades) == 1

    def test_buy_insufficient_cash(self):
        e = BacktestEngine(initial_capital=100)
        assert e.buy("600519", 100, 100) is False
        assert "600519" not in e.positions

    def test_buy_updates_avg_cost(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.buy("A", 110, 100)
        pos = e.positions["A"]
        assert pos["qty"] == 200
        assert 100 < pos["avg_cost"] < 110

    def test_sell_success(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        assert e.sell("A", 110, 50) is True
        assert e.positions["A"]["qty"] == 50

    def test_sell_all_removes_position(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        assert e.sell("A", 110, 100) is True
        assert "A" not in e.positions

    def test_sell_no_position(self):
        e = BacktestEngine()
        assert e.sell("A", 100, 10) is False

    def test_sell_insufficient_qty(self):
        e = BacktestEngine()
        e.buy("A", 100, 50)
        assert e.sell("A", 100, 100) is False

    def test_update_prices(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.update_prices({"A": 110})
        assert e.positions["A"]["current_price"] == 110

    def test_get_total_equity_empty(self):
        e = BacktestEngine()
        assert e.get_total_equity() == 1_000_000.0

    def test_get_total_equity_with_positions(self):
        e = BacktestEngine()
        e.buy("A", 100, 100)
        e.update_prices({"A": 110})
        # cash + 100*110
        assert e.get_total_equity() == e.cash + 100 * 110

    def test_record_daily_pnl_first(self):
        e = BacktestEngine()
        e.record_daily_pnl("2026-08-01")
        assert len(e.equity_curve) == 1
        assert len(e.daily_pnl) == 1
        assert e.daily_pnl[0]["daily_return"] == 0.0

    def test_record_daily_pnl_subsequent(self):
        e = BacktestEngine()
        e.record_daily_pnl("2026-08-01")
        e.buy("A", 100, 100)
        e.record_daily_pnl("2026-08-02")
        assert len(e.daily_pnl) == 2

    def test_record_daily_pnl_none_date(self):
        e = BacktestEngine()
        e.record_daily_pnl(None)
        assert e.daily_pnl[0]["date"] == ""

    def test_is_suspended_explicit(self):
        e = BacktestEngine()
        day_data = {"suspended": {"A": True}}
        assert e._is_suspended(day_data, "A") is True

    def test_is_suspended_not_suspended(self):
        e = BacktestEngine()
        day_data = {"suspended": {"A": False}, "prices": {"A": 100}}
        assert e._is_suspended(day_data, "A") is False

    def test_is_suspended_zero_price(self):
        e = BacktestEngine()
        day_data = {"prices": {"A": 0}}
        assert e._is_suspended(day_data, "A") is True

    def test_is_suspended_no_data(self):
        e = BacktestEngine()
        assert e._is_suspended({}, "A") is True

    def test_run_empty_data(self):
        e = BacktestEngine()
        result = e.run([], lambda d, p: [])
        assert result["status"] == "error"

    def test_run_simple(self):
        e = BacktestEngine()

        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {"date": "2026-08-02", "prices": {"A": 110}},
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01" and "A" not in positions:
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        result = e.run(data, strategy)
        assert result["status"] == "success"
        assert result["backtest_days"] == 2

    def test_run_with_limit_up(self):
        """涨停不可买"""
        e = BacktestEngine()
        data = [
            {
                "date": "2026-08-01",
                "prices": {"A": 110},
                "limit_up_prices": {"A": 110},
            }
        ]

        def strategy(day_data, positions):
            return [{"code": "A", "action": "BUY", "qty": 100, "price": 110}]

        result = e.run(data, strategy)
        assert result["total_trades"] == 0

    def test_run_with_limit_down(self):
        """跌停不可卖"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {
                "date": "2026-08-02",
                "prices": {"A": 90},
                "limit_down_prices": {"A": 90},
            },
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            elif day_data["date"] == "2026-08-02":
                return [{"code": "A", "action": "SELL", "qty": 100, "price": 90}]
            return []

        result = e.run(data, strategy)
        assert result["buy_trades"] == 1
        assert result["sell_trades"] == 0

    def test_run_with_suspended(self):
        """停牌不可交易"""
        e = BacktestEngine()
        data = [
            {"date": "2026-08-01", "prices": {"A": 100}},
            {
                "date": "2026-08-02",
                "prices": {"A": 110},
                "suspended": {"A": True},
            },
        ]

        def strategy(day_data, positions):
            if day_data["date"] == "2026-08-01":
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 100}]
            return []

        result = e.run(data, strategy)
        assert result["total_trades"] == 1

    def test_generate_report_no_data(self):
        e = BacktestEngine()
        assert e.generate_report()["status"] == "error"

    def test_generate_report_with_data(self):
        e = BacktestEngine()
        data = [{"date": "2026-08-01", "prices": {"A": 100}}]
        e.run(data, lambda d, p: [])
        report = e.generate_report()
        assert report["status"] == "success"
        for field in [
            "initial_capital",
            "final_equity",
            "total_return",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
        ]:
            assert field in report


# ============================================================
# ETFSignalStrategy
# ============================================================


class TestETFSignalStrategy:
    """ETFSignalStrategy 策略测试"""

    def test_init_defaults(self):
        s = ETFSignalStrategy()
        assert s.signal_thresholds["strong_buy"] == "强加仓"
        assert s.signal_thresholds["strong_sell"] == "强减仓"
        assert s.max_position_pct == 0.3

    def test_init_custom(self):
        s = ETFSignalStrategy(max_position_pct=0.5)
        assert s.max_position_pct == 0.5

    def test_generate_signals_empty(self):
        s = ETFSignalStrategy()
        day_data = {"etf_signals": {}, "prices": {}, "equity": 1_000_000}
        assert s.generate_signals(day_data, {}) == []

    def test_generate_signals_strong_buy(self):
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强加仓", "inflow": 100}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        signals = s.generate_signals(day_data, {})
        assert len(signals) >= 1
        assert signals[0]["action"] == "BUY"
        assert signals[0]["code"] == "A"

    def test_generate_signals_strong_sell(self):
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强减仓", "inflow": -100}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        positions = {"A": {"qty": 1000, "avg_cost": 10, "current_price": 10}}
        signals = s.generate_signals(day_data, positions)
        assert len(signals) >= 1
        assert signals[0]["action"] == "SELL"

    def test_generate_signals_zero_price(self):
        """价格为 0 → 跳过"""
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "强加仓", "inflow": 100}},
            "prices": {"A": 0},
            "equity": 1_000_000,
        }
        assert s.generate_signals(day_data, {}) == []

    def test_generate_signals_no_signal(self):
        """无信号 → 不交易"""
        s = ETFSignalStrategy()
        day_data = {
            "etf_signals": {"A": {"signal": "中性", "inflow": 0}},
            "prices": {"A": 10},
            "equity": 1_000_000,
        }
        assert s.generate_signals(day_data, {}) == []


# ============================================================
# BacktestDataLoader
# ============================================================


class TestBacktestDataLoader:
    """BacktestDataLoader 数据加载器测试"""

    def test_generate_synthetic_data(self):
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-03", "2026-08-07", ["A", "B"]
        )
        assert len(data) >= 3  # 8/3-8/7 有 5 个工作日
        for day in data:
            assert "date" in day
            assert "prices" in day
            assert "etf_signals" in day
            assert "A" in day["prices"]

    def test_generate_synthetic_data_weekend_skip(self):
        """周末跳过"""
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-01", "2026-08-02", ["A"]
        )
        # 8/1=周六, 8/2=周日 → 0 个工作日
        assert len(data) == 0

    def test_load_from_positions_history_empty(self):
        with tempfile.TemporaryDirectory() as d:
            data = BacktestDataLoader.load_from_positions_history(d)
        assert data == []

    def test_load_from_positions_history_with_file(self):
        with tempfile.TemporaryDirectory() as d:
            pos_file = os.path.join(d, "positions_2026-08-01.json")
            with open(pos_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "positions": {
                            "A": {
                                "etf_flow_signal": "强加仓",
                                "etf_inflow": 100,
                                "avg_cost": 10,
                            }
                        }
                    },
                    f,
                )
            data = BacktestDataLoader.load_from_positions_history(d)
        assert len(data) == 1
        assert data[0]["date"] == "2026-08-01"
        assert "A" in data[0]["prices"]

    def test_load_from_positions_history_with_tickers_filter(self):
        with tempfile.TemporaryDirectory() as d:
            pos_file = os.path.join(d, "positions_2026-08-01.json")
            with open(pos_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "positions": {
                            "A": {"avg_cost": 10},
                            "B": {"avg_cost": 20},
                        }
                    },
                    f,
                )
            data = BacktestDataLoader.load_from_positions_history(d, tickers=["A"])
        assert len(data) == 1
        assert "A" in data[0]["prices"]
        assert "B" not in data[0]["prices"]


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """run_etf_signal_backtest / compare_strategies 测试"""

    def test_run_etf_signal_backtest(self):
        data = BacktestDataLoader.generate_synthetic_data(
            "2026-08-03", "2026-08-07", ["A"]
        )
        result = run_etf_signal_backtest(data, initial_capital=500_000)
        assert result["status"] == "success"
        assert result["initial_capital"] == 500_000

    def test_run_etf_signal_backtest_empty(self):
        result = run_etf_signal_backtest([])
        assert result["status"] == "error"

    def test_compare_strategies(self):
        data = [{"date": "2026-08-03", "prices": {"A": 10}, "etf_signals": {}}]

        def buy_strategy(day_data, positions):
            if "A" not in positions:
                return [{"code": "A", "action": "BUY", "qty": 100, "price": 10}]
            return []

        def hold_strategy(day_data, positions):
            return []

        results = compare_strategies(data, {"buy": buy_strategy, "hold": hold_strategy})
        assert "buy" in results
        assert "hold" in results
        assert results["buy"]["status"] == "success"
        assert results["hold"]["status"] == "success"
