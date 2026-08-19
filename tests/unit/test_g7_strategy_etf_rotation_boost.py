"""
G7 Coverage Boost: utils/strategy/etf_rotation/engine.py (372 lines, 0% -> target ~80%)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from utils.strategy.etf_rotation.engine import (
    BTResult,
    ThreeTierETFRotationValidator,
    ThreeTierReport,
    VECResult,
    WFOResult,
    generate_rotation_signals,
)


class TestWFOResult:
    def test_defaults(self):
        result = WFOResult(
            window_id=0,
            train_start="20260101",
            train_end="20260331",
            test_start="20260401",
            test_end="20260630",
            best_lookback=20,
            best_holdings=3,
            train_sharpe=1.0,
            oos_sharpe=0.8,
            oos_return=0.05,
        )
        assert result.oos_sharpe == 0.8
        assert result.oos_return == 0.05

    def test_summary_values(self):
        result = WFOResult(
            window_id=1,
            train_start="20260101",
            train_end="20260331",
            test_start="20260401",
            test_end="20260630",
            best_lookback=10,
            best_holdings=2,
            train_sharpe=1.2,
            oos_sharpe=0.9,
            oos_return=0.03,
        )
        assert result.best_lookback == 10
        assert result.best_holdings == 2


class TestVECResult:
    def test_defaults(self):
        result = VECResult(
            n_folds=5,
            avg_sharpe=0.8,
            sharpe_std=0.2,
            robust_lookback=20,
            robust_holdings=3,
        )
        assert result.n_folds == 5
        assert result.avg_sharpe == 0.8
        assert result.sharpe_std == 0.2
        assert result.robust_lookback == 20
        assert result.robust_holdings == 3


class TestBTResult:
    def test_default_equity_curve(self):
        result = BTResult(
            final_equity=1_050_000.0,
            total_return=0.05,
            sharpe_ratio=1.1,
            max_drawdown=0.02,
            n_rebalances=5,
        )
        assert result.final_equity == 1_050_000.0
        assert result.total_return == 0.05
        assert result.sharpe_ratio == 1.1
        assert result.max_drawdown == 0.02
        assert result.n_rebalances == 5
        assert result.equity_curve == []

    def test_custom_equity_curve(self):
        result = BTResult(
            final_equity=1_000_000.0,
            total_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            n_rebalances=0,
            equity_curve=[1_000_000.0, 1_010_000.0],
        )
        assert len(result.equity_curve) == 2


class TestThreeTierReport:
    def test_defaults(self):
        report = ThreeTierReport()
        assert report.wfo_results == []
        assert report.vec_result is None
        assert report.bt_result is None
        assert report.final_lookback == 20
        assert report.final_holdings == 3
        assert report.passed is False
        assert report.target_sharpe == 1.0

    def test_summary_failed(self):
        report = ThreeTierReport(bt_result=BTResult(1_000_000.0, 0.0, 0.0, 0.0, 0))
        text = report.summary()
        assert "❌ FAIL" in text
        assert "WFO: 0 窗口" in text

    def test_summary_passed(self):
        wfo = WFOResult(
            window_id=0,
            train_start="20260101",
            train_end="20260331",
            test_start="20260401",
            test_end="20260630",
            best_lookback=20,
            best_holdings=3,
            train_sharpe=1.0,
            oos_sharpe=1.0,
            oos_return=0.05,
        )
        vec = VECResult(n_folds=1, avg_sharpe=1.0, sharpe_std=0.1, robust_lookback=20, robust_holdings=3)
        bt = BTResult(1_050_000.0, 0.05, 1.0, 0.02, 5, [1_000_000.0, 1_050_000.0])
        report = ThreeTierReport(wfo_results=[wfo], vec_result=vec, bt_result=bt, passed=True)
        text = report.summary()
        assert "✅ PASS" in text
        assert "VEC: 1 折" in text
        assert "BT:" in text


class TestGenerateRotationSignals:
    def test_basic_momentum(self):
        index = pd.date_range("20260101", periods=30)
        closes = pd.DataFrame(
            {
                "etf_a": np.linspace(1.0, 1.3, 30),
                "etf_b": np.linspace(1.0, 1.1, 30),
                "etf_c": np.linspace(1.0, 0.9, 30),
            },
            index=index,
        )
        signals = generate_rotation_signals(closes, lookback=10, holdings=2)
        assert signals.shape == closes.shape
        assert signals.sum(axis=1).max() <= 1.0 + 1e-9

    def test_not_enough_etfs(self):
        index = pd.date_range("20260101", periods=30)
        closes = pd.DataFrame({"etf_a": np.linspace(1.0, 1.2, 30)}, index=index)
        signals = generate_rotation_signals(closes, lookback=10, holdings=2)
        assert (signals == 0.0).all().all()

    def test_lookback_skip(self):
        index = pd.date_range("20260101", periods=25)
        closes = pd.DataFrame(
            {
                "etf_a": np.linspace(1.0, 1.2, 25),
                "etf_b": np.linspace(1.0, 1.1, 25),
            },
            index=index,
        )
        signals = generate_rotation_signals(closes, lookback=10, holdings=1)
        assert signals.iloc[:10].sum().sum() == 0.0

    def test_equal_weight_top_k(self):
        index = pd.date_range("20260101", periods=40)
        closes = pd.DataFrame(
            {
                "etf_a": np.linspace(1.0, 1.5, 40),
                "etf_b": np.linspace(1.0, 1.4, 40),
                "etf_c": np.linspace(1.0, 1.3, 40),
            },
            index=index,
        )
        signals = generate_rotation_signals(closes, lookback=10, holdings=2)
        row_sums = signals.sum(axis=1).replace(0, np.nan).dropna()
        assert np.allclose(row_sums.values, 1.0)


class TestRunBacktest:
    def test_positive_returns(self):
        closes = pd.DataFrame(
            {
                "etf_a": [1.0, 1.01, 1.02, 1.03],
                "etf_b": [1.0, 1.00, 0.99, 0.98],
            }
        )
        signals = pd.DataFrame(
            {
                "etf_a": [0.0, 1.0, 1.0, 1.0],
                "etf_b": [0.0, 0.0, 0.0, 0.0],
            }
        )
        bt = _run_backtest_from_signals(closes, signals)
        assert bt.final_equity > 1_000_000.0
        assert bt.total_return > 0.0

    def test_commission_cost(self):
        index = pd.date_range("20260101", periods=10)
        closes = pd.DataFrame(
            {
                "etf_a": np.linspace(1.0, 1.1, 10),
                "etf_b": np.linspace(1.0, 1.05, 10),
            },
            index=index,
        )
        signals = pd.DataFrame(
            {
                "etf_a": [0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "etf_b": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            },
            index=index,
        )
        bt = _run_backtest_from_signals(closes, signals)
        assert bt.max_drawdown >= 0.0
        assert bt.n_rebalances >= 0

    def test_empty_inputs(self):
        closes = pd.DataFrame()
        signals = pd.DataFrame()
        bt = _run_backtest_from_signals(closes, signals)
        assert bt.final_equity == 1_000_000.0
        assert bt.total_return == 0.0
        assert bt.equity_curve == []


class TestThreeTierETFRotationValidator:
    def test_defaults(self):
        validator = ThreeTierETFRotationValidator()
        assert validator.lookback_grid == [10, 20, 60]
        assert validator.holdings_grid == [2, 3, 5]
        assert validator.train_window == 120
        assert validator.test_window == 60
        assert validator.n_folds == 5
        assert validator.target_sharpe == 1.0

    def test_validate_empty(self):
        validator = ThreeTierETFRotationValidator()
        report = validator.validate(pd.DataFrame())
        assert report.wfo_results == []
        assert report.passed is False

    def test_validate_insufficient_data(self):
        validator = ThreeTierETFRotationValidator(train_window=120, test_window=60)
        index = pd.date_range("20260101", periods=50)
        closes = pd.DataFrame({"etf_a": np.linspace(1.0, 1.1, 50)}, index=index)
        report = validator.validate(closes)
        assert report.wfo_results == []
        assert report.bt_result is not None


def _run_backtest_from_signals(closes: pd.DataFrame, signals: pd.DataFrame) -> BTResult:
    from utils.strategy.etf_rotation.engine import _run_backtest

    return _run_backtest(closes, signals)
