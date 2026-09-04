"""05_05a — ComboBacktest 回测验证测试.

逐日驱动/BS重构/绩效统计/对冲效率.
"""

from __future__ import annotations

import pytest

from utils.etf_option_combo.combo_backtest import ComboBacktest
from utils.etf_option_combo.combo_base import StrategyType


pytestmark = pytest.mark.integration


@pytest.fixture
def backtest():
    return ComboBacktest(config={"total_capital": 2_000_000})


class TestBacktestRun:
    def test_backtest_run_basic(self, backtest):
        """基础回测运行返回完整结构."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-31",
            strategies=[StrategyType.COVERED_CALL],
            underlying="510050.SH",
        )
        assert "equity_curve" in result
        assert "metrics" in result
        assert "hedge_efficiency" in result
        assert "trades" in result
        assert "trade_count" in result
        assert len(result["equity_curve"]) > 0

    def test_backtest_run_all_strategies(self, backtest):
        """全 5 策略回测."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-15",
        )
        assert result["trade_count"] > 0
        # 每日 5 策略
        assert len(result["trades"]) == result["trade_count"]

    def test_backtest_empty_date_range(self, backtest):
        """空日期范围返回空结果."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-01",
            strategies=[StrategyType.COVERED_CALL],
        )
        # 至少 1 天 (start == end)
        assert "equity_curve" in result


class TestBacktestMetrics:
    def test_backtest_metrics_fields(self, backtest):
        """绩效指标字段完整."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-31",
            strategies=[StrategyType.COVERED_CALL],
        )
        m = result["metrics"]
        assert "total_return" in m
        assert "annual_return" in m
        assert "max_drawdown" in m
        assert "sharpe" in m
        assert "final_equity" in m
        assert "n_trading_days" in m

    def test_backtest_max_drawdown_non_negative(self, backtest):
        """最大回撤非负."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-02-28",
        )
        assert result["metrics"]["max_drawdown"] >= 0.0
        assert result["metrics"]["max_drawdown"] <= 1.0

    def test_backtest_final_equity(self, backtest):
        """终值 = metrics.final_equity."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-31",
            strategies=[StrategyType.COVERED_CALL],
        )
        assert result["metrics"]["final_equity"] == result["equity_curve"][-1]["equity"]


class TestBacktestEquityCurve:
    def test_backtest_equity_curve_structure(self, backtest):
        """净值曲线每点含 date/equity/drawdown."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-15",
        )
        for point in result["equity_curve"]:
            assert "date" in point
            assert "equity" in point
            assert "drawdown" in point
            assert point["drawdown"] >= 0.0

    def test_backtest_equity_curve_monotonic_dates(self, backtest):
        """净值曲线日期递增."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-15",
        )
        dates = [p["date"] for p in result["equity_curve"]]
        assert dates == sorted(dates)


class TestBacktestBSReconstruct:
    def test_backtest_bs_reconstruct(self, backtest):
        """use_bs_reconstruct=True 时 BS 重构期权价格."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-15",
            strategies=[StrategyType.COVERED_CALL],
            use_bs_reconstruct=True,
            initial_iv=0.20,
        )
        assert result["trade_count"] > 0
        # COVERED_CALL 权利金为正
        for trade in result["trades"]:
            if trade["strategy"] == "covered_call":
                assert trade["pnl"] != 0.0

    def test_backtest_custom_prices(self, backtest):
        """自定义价格序列回测."""
        prices = [
            {"date": "2026-01-01", "close": 3.00},
            {"date": "2026-01-02", "close": 3.02},
            {"date": "2026-01-03", "close": 2.98},
        ]
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-03",
            strategies=[StrategyType.COVERED_CALL],
            etf_prices=prices,
        )
        assert len(result["equity_curve"]) == 3


class TestBacktestHedgeEfficiency:
    def test_backtest_hedge_efficiency_range(self, backtest):
        """对冲效率 ∈ [0, 1]."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-02-28",
        )
        assert 0.0 <= result["hedge_efficiency"] <= 1.0

    def test_backtest_hedge_vs_spot_classification(self, backtest):
        """交易按 is_hedge 分类."""
        result = backtest.run_backtest(
            start_date="2026-01-01",
            end_date="2026-01-15",
        )
        for trade in result["trades"]:
            assert "is_hedge" in trade
            assert isinstance(trade["is_hedge"], bool)