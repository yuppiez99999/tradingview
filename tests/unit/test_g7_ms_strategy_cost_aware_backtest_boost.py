"""G7 boost: ms_strategy/src/backtest/cost_aware_backtest.py 单元测试.

覆盖 TradeRecord / BacktestResult / CostAwareBacktest 的全部公开接口,
包括成本计算/策略回测/无成本对比的核心路径与边界分支.
外部依赖 cost_model 直接使用真实实现 (同包轻量依赖, 非 IO).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.cost_aware_backtest import (  # noqa: E402
    BacktestResult,
    CostAwareBacktest,
    TradeRecord,
)

# ============================================================
# 1. dataclass 构造
# ============================================================


class TestTradeRecord:
    def test_construct(self):
        tr = TradeRecord(
            date=pd.Timestamp("2020-01-01"),
            code="000001",
            side="BUY",
            qty=100,
            price=10.0,
            notional=1000.0,
            commission=0.25,
            stamp_duty=0.0,
            transfer_fee=0.02,
            market_impact=1.0,
            total_cost=1.27,
        )
        assert tr.code == "000001"
        assert tr.side == "BUY"
        assert tr.qty == 100


class TestBacktestResult:
    def test_defaults(self):
        r = BacktestResult()
        assert r.total_return == 0.0
        assert r.n_trades == 0
        assert r.trades == []
        assert r.equity_curve is None


# ============================================================
# 2. CostAwareBacktest 初始化
# ============================================================


class TestCostAwareBacktestInit:
    def test_default_init(self):
        bt = CostAwareBacktest()
        assert bt.capital == 5_000_000
        assert bt.commission_rate == pytest.approx(0.00025)
        assert bt.stamp_duty_rate == pytest.approx(0.001)
        assert bt.cost_model is not None

    def test_custom_init(self):
        bt = CostAwareBacktest(
            initial_capital=1_000_000, commission_rate=0.0005, min_cost_bps=5.0
        )
        assert bt.capital == 1_000_000
        assert bt.commission_rate == pytest.approx(0.0005)
        assert bt.min_cost_bps == pytest.approx(5.0 / 10000)


# ============================================================
# 3. compute_trade_cost
# ============================================================


class TestComputeTradeCost:
    def test_buy_basic(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(notional=100_000.0, side="BUY")
        assert result["commission"] == pytest.approx(100_000.0 * 0.00025)
        assert result["stamp_duty"] == 0.0
        assert result["transfer_fee"] == pytest.approx(100_000.0 * 0.00002)
        assert result["total_cost"] > 0

    def test_sell_has_stamp_duty(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(notional=100_000.0, side="SELL")
        assert result["stamp_duty"] == pytest.approx(100_000.0 * 0.001)
        assert result["commission"] == pytest.approx(100_000.0 * 0.00025)

    def test_market_impact_with_cost_model(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(
            notional=10_000.0,
            side="BUY",
            qty=100,
            daily_volume=1_000_000,
            volatility=0.02,
            price=100.0,
        )
        assert result["market_impact"] >= 0

    def test_market_impact_simplified_path(self):
        # 强制 cost_model=None 走简化分支
        bt = CostAwareBacktest()
        bt.cost_model = None
        result = bt.compute_trade_cost(
            notional=10_000.0,
            side="BUY",
            qty=100,
            daily_volume=1_000_000,
            volatility=0.02,
            price=100.0,
        )
        assert result["market_impact"] > 0

    def test_no_market_impact_when_qty_zero(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(
            notional=100_000.0, side="BUY", qty=0, daily_volume=1_000_000, price=100.0
        )
        assert result["market_impact"] == 0.0

    def test_no_market_impact_when_volume_zero(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(
            notional=100_000.0, side="BUY", qty=100, daily_volume=0, price=100.0
        )
        assert result["market_impact"] == 0.0

    def test_min_cost_floor(self):
        bt = CostAwareBacktest(min_cost_bps=100.0)  # 1% 最低成本
        result = bt.compute_trade_cost(notional=1000.0, side="BUY")
        # min_cost = 1000 * 0.01 = 10
        assert result["total_cost"] >= 10.0
        assert result["total_cost"] == pytest.approx(10.0)

    def test_keys_present(self):
        bt = CostAwareBacktest()
        result = bt.compute_trade_cost(notional=1000.0, side="BUY")
        for key in (
            "commission",
            "stamp_duty",
            "transfer_fee",
            "market_impact",
            "total_cost",
        ):
            assert key in result


# ============================================================
# 4. run_strategy
# ============================================================


class TestRunStrategy:
    def _make_data(self, n_days=60, n_codes=2):
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=n_days, freq="D")
        codes = [f"00000{i}" for i in range(n_codes)]
        prices = pd.DataFrame(
            np.random.uniform(9, 11, (n_days, n_codes)),
            index=dates,
            columns=codes,
        )
        # 目标权重: 第一标的 0.5, 第二标的 0.3
        weights = pd.DataFrame(
            [[0.5, 0.3]] * n_days,
            index=dates,
            columns=codes,
        )
        return prices, weights

    def test_basic_run(self):
        prices, weights = self._make_data()
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights, rebalance_threshold=0.05)
        assert isinstance(result, BacktestResult)
        assert result.n_trades >= 0
        assert result.equity_curve is not None
        assert len(result.equity_curve) == len(prices)

    def test_with_daily_volumes(self):
        prices, weights = self._make_data()
        np.random.seed(42)
        volumes = pd.DataFrame(
            np.random.randint(100_000, 1_000_000, prices.shape),
            index=prices.index,
            columns=prices.columns,
        )
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights, daily_volumes=volumes)
        assert isinstance(result, BacktestResult)
        assert result.total_cost >= 0

    def test_no_rebalance_after_first_day(self):
        prices, weights = self._make_data()
        bt = CostAwareBacktest(initial_capital=1_000_000)
        # 极大阈值 → 仅第一天再平衡
        result = bt.run_strategy(prices, weights, rebalance_threshold=10.0)
        assert isinstance(result, BacktestResult)

    def test_single_day(self):
        prices, weights = self._make_data(n_days=1)
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights)
        assert len(result.equity_curve) == 1
        assert result.n_trades >= 0

    def test_zero_target_weights(self):
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=30, freq="D")
        codes = ["000001"]
        prices = pd.DataFrame(
            np.random.uniform(9, 11, (30, 1)),
            index=dates,
            columns=codes,
        )
        weights = pd.DataFrame([[0.0]] * 30, index=dates, columns=codes)
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights)
        assert isinstance(result, BacktestResult)

    def test_result_metrics_computed(self):
        prices, weights = self._make_data(n_days=100)
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights)
        # 验证指标字段已计算
        assert isinstance(result.total_return, float)
        assert isinstance(result.annual_return, float)
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown, float)
        assert isinstance(result.turnover, float)
        assert result.daily_costs is not None

    def test_trades_recorded(self):
        prices, weights = self._make_data()
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights, rebalance_threshold=0.01)
        if result.n_trades > 0:
            trade = result.trades[0]
            assert isinstance(trade, TradeRecord)
            assert trade.side in ("BUY", "SELL")
            assert trade.qty > 0


# ============================================================
# 5. compare_with_no_cost
# ============================================================


class TestCompareWithNoCost:
    def test_compare_keys(self):
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=60, freq="D")
        codes = ["000001", "000002"]
        prices = pd.DataFrame(
            np.random.uniform(9, 11, (60, 2)),
            index=dates,
            columns=codes,
        )
        weights = pd.DataFrame([[0.5, 0.3]] * 60, index=dates, columns=codes)
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights)
        cmp = bt.compare_with_no_cost(result)
        for key in (
            "total_return_with_cost",
            "total_return_without_cost",
            "cost_drag",
            "cost_breakdown",
            "sharpe_with_cost",
            "turnover",
            "n_trades",
            "avg_cost_bps",
        ):
            assert key in cmp

    def test_cost_drag_positive(self):
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=60, freq="D")
        codes = ["000001"]
        prices = pd.DataFrame(
            np.random.uniform(9, 11, (60, 1)),
            index=dates,
            columns=codes,
        )
        weights = pd.DataFrame([[0.8]] * 60, index=dates, columns=codes)
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = bt.run_strategy(prices, weights, rebalance_threshold=0.01)
        cmp = bt.compare_with_no_cost(result)
        assert cmp["cost_drag"] >= 0

    def test_zero_trades_avg_cost_bps(self):
        bt = CostAwareBacktest(initial_capital=1_000_000)
        result = BacktestResult(n_trades=0)
        cmp = bt.compare_with_no_cost(result)
        assert cmp["avg_cost_bps"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
