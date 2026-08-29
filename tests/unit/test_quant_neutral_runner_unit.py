"""Quant Neutral Runner 单元测试.

被测模块: utils/quant_neutral_runner.py
覆盖目标: >=80%
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.quant_neutral_runner import (  # noqa: E402
    QuantNeutralResult,
    QuantNeutralRunner,
    StockFactorScore,
)


class TestMapFactorToColumn:
    def test_all_factors(self):
        runner = QuantNeutralRunner()
        assert runner._map_factor_to_column("momentum") == "returns_20d"
        assert runner._map_factor_to_column("reversal") == "returns_5d"
        assert runner._map_factor_to_column("volatility") == "volatility_60d"
        assert runner._map_factor_to_column("liquidity") == "avg_turnover_amount"
        assert runner._map_factor_to_column("earnings_quality") == "roe"
        assert runner._map_factor_to_column("growth") == "revenue_growth"
        assert runner._map_factor_to_column("valuation") == "pe_percentile"

    def test_unknown_factor(self):
        runner = QuantNeutralRunner()
        assert runner._map_factor_to_column("custom") == "custom"


class TestExtractFactorValue:
    def test_normal_value(self):
        runner = QuantNeutralRunner()
        stock = {"returns_20d": 0.05}
        assert runner._extract_factor_value(stock, "momentum") == 0.05

    def test_missing_value(self):
        runner = QuantNeutralRunner()
        assert runner._extract_factor_value({}, "momentum") == 0.0

    def test_none_value(self):
        runner = QuantNeutralRunner()
        stock = {"returns_20d": None}
        assert runner._extract_factor_value(stock, "momentum") == 0.0

    def test_invalid_value(self):
        runner = QuantNeutralRunner()
        stock = {"returns_20d": "invalid"}
        assert runner._extract_factor_value(stock, "momentum") == 0.0


class TestCalculatePortfolioBeta:
    def test_empty_positions(self):
        runner = QuantNeutralRunner()
        assert runner.calculate_portfolio_beta([]) == 0.0

    def test_zero_weight(self):
        runner = QuantNeutralRunner()
        positions = [{"weight": 0, "beta": 1.2}]
        assert runner.calculate_portfolio_beta(positions) == 1.0

    def test_normal_beta(self):
        runner = QuantNeutralRunner()
        positions = [
            {"weight": 0.5, "beta": 1.2},
            {"weight": 0.5, "beta": 0.8},
        ]
        beta = runner.calculate_portfolio_beta(positions)
        assert abs(beta - 1.0) < 1e-6

    def test_default_beta(self):
        runner = QuantNeutralRunner()
        positions = [{"weight": 1.0}]
        beta = runner.calculate_portfolio_beta(positions)
        assert beta == 1.0


class TestBuildLongPositions:
    def test_empty(self):
        runner = QuantNeutralRunner()
        assert runner._build_long_positions([], 1_000_000) == []

    def test_single_position(self):
        runner = QuantNeutralRunner()
        selected = [StockFactorScore(code="600519", composite=0.5, rank=1)]
        positions = runner._build_long_positions(selected, 1_000_000)
        assert len(positions) == 1
        assert positions[0]["code"] == "600519"
        assert abs(positions[0]["weight"] - 1.0) < 1e-6
        assert positions[0]["amount"] == 1_000_000

    def test_multiple_positions(self):
        runner = QuantNeutralRunner()
        selected = [
            StockFactorScore(code="A", composite=0.8, rank=1),
            StockFactorScore(code="B", composite=0.4, rank=2),
        ]
        positions = runner._build_long_positions(selected, 1_000_000)
        assert len(positions) == 2
        total_weight = sum(p["weight"] for p in positions)
        assert abs(total_weight - 1.0) < 1e-6
        assert positions[0]["weight"] > positions[1]["weight"]

    def test_negative_scores(self):
        runner = QuantNeutralRunner()
        selected = [
            StockFactorScore(code="A", composite=-0.5, rank=1),
            StockFactorScore(code="B", composite=-0.8, rank=2),
        ]
        positions = runner._build_long_positions(selected, 1_000_000)
        assert len(positions) == 2
        assert all(p["weight"] > 0 for p in positions)


class TestBuildRebalanceOrders:
    def test_all_new_buys(self):
        runner = QuantNeutralRunner()
        target = [{"code": "A", "amount": 50000, "name": "StockA"}]
        orders = runner._build_rebalance_orders(
            [], target, 0, {"contracts": 2}, date(2026, 8, 14)
        )
        assert len(orders["buy_orders"]) == 1
        assert len(orders["sell_orders"]) == 0
        assert orders["ic_action"]["action"] == "add_short"

    def test_all_sells(self):
        runner = QuantNeutralRunner()
        current = [{"code": "A", "amount": 50000, "name": "StockA"}]
        orders = runner._build_rebalance_orders(
            current, [], 2, {"contracts": 0}, date(2026, 8, 14)
        )
        assert len(orders["sell_orders"]) == 1
        assert orders["ic_action"]["action"] == "reduce_short"

    def test_adjust_existing(self):
        runner = QuantNeutralRunner()
        current = [{"code": "A", "amount": 40000, "name": "StockA"}]
        target = [{"code": "A", "amount": 50000, "name": "StockA"}]
        orders = runner._build_rebalance_orders(
            current, target, 1, {"contracts": 1}, date(2026, 8, 14)
        )
        assert len(orders["adjust_orders"]) == 1
        assert orders["adjust_orders"][0]["action"] == "buy"
        assert orders["ic_action"]["action"] == "hold"

    def test_small_delta_ignored(self):
        runner = QuantNeutralRunner()
        current = [{"code": "A", "amount": 50000, "name": "StockA"}]
        target = [{"code": "A", "amount": 50500, "name": "StockA"}]
        orders = runner._build_rebalance_orders(
            current, target, 0, {"contracts": 0}, date(2026, 8, 14)
        )
        assert len(orders["adjust_orders"]) == 0

    def test_turnover_calculation(self):
        runner = QuantNeutralRunner()
        current = [{"code": "A", "amount": 50000, "name": "StockA"}]
        target = [{"code": "B", "amount": 50000, "name": "StockB"}]
        orders = runner._build_rebalance_orders(
            current, target, 0, {"contracts": 0}, date(2026, 8, 14)
        )
        assert orders["turnover"] > 0


class TestGeneratePauseOrder:
    def test_pause_order(self):
        runner = QuantNeutralRunner()
        result = QuantNeutralResult()
        current = [{"code": "A", "amount": 50000, "name": "StockA"}]
        result = runner._generate_pause_order(
            result, current, 2, 5500.0, date(2026, 8, 14)
        )
        assert len(result.long_positions) == 1
        assert result.long_positions[0]["action"] == "sell_all"
        assert result.ic_hedge["action"] == "close_all_short"
        assert result.ic_hedge["current_contracts"] == 2

    def test_empty_holdings(self):
        runner = QuantNeutralRunner()
        result = QuantNeutralResult()
        result = runner._generate_pause_order(result, [], 0, 5500.0, date(2026, 8, 14))
        assert result.long_positions == []
        assert result.ic_hedge["target_contracts"] == 0


class TestSummary:
    def test_basic_summary(self):
        runner = QuantNeutralRunner()
        result = QuantNeutralResult(
            trade_date="2026-08-14",
            action="rebalance",
            long_count=25,
            long_market_value=1_400_000,
            portfolio_beta=0.05,
        )
        summary = runner.summary(result)
        assert "量化市场中性策略月度调仓报告" in summary
        assert "2026-08-14" in summary

    def test_with_basis_warning(self):
        runner = QuantNeutralRunner()
        result = QuantNeutralResult(
            trade_date="2026-08-14",
            action="rebalance",
            basis_warning=True,
        )
        summary = runner.summary(result)
        assert isinstance(summary, str)


class TestScoreFactors:
    def test_empty_universe(self):
        runner = QuantNeutralRunner()
        scores = runner.score_factors([])
        assert scores == []

    def test_single_stock(self):
        runner = QuantNeutralRunner()
        universe = [{"code": "600519", "name": "贵州茅台", "returns_20d": 0.05}]
        scores = runner.score_factors(universe)
        assert len(scores) == 1
        assert scores[0].code == "600519"

    def test_multiple_stocks(self):
        runner = QuantNeutralRunner()
        universe = [
            {"code": "A", "name": "StockA", "returns_20d": 0.08, "roe": 0.15},
            {"code": "B", "name": "StockB", "returns_20d": 0.02, "roe": 0.10},
            {"code": "C", "name": "StockC", "returns_20d": 0.05, "roe": 0.20},
        ]
        scores = runner.score_factors(universe)
        assert len(scores) == 3
        assert scores[0].composite >= scores[1].composite
