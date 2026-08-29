"""
G7 Coverage Boost: utils/strategy/arbitrage/pairs_trading.py (369 lines, 0% -> target ~80%)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.strategy.arbitrage.pairs_trading import (
    WalkForwardPairsValidator,
    WFValidationReport,
    WFWindowResult,
)


class TestWFWindowResult:
    def test_defaults(self):
        result = WFWindowResult(
            window_id=0,
            train_start="20260101",
            train_end="20260331",
            test_start="20260401",
            test_end="20260630",
            n_pairs_trained=2,
            n_pairs_tested=2,
        )
        assert result.oos_return == 0.0
        assert result.oos_sharpe == 0.0
        assert result.n_trades == 0

    def test_custom_values(self):
        result = WFWindowResult(
            window_id=1,
            train_start="20260101",
            train_end="20260331",
            test_start="20260401",
            test_end="20260630",
            n_pairs_trained=3,
            n_pairs_tested=2,
            oos_return=0.05,
            oos_sharpe=1.2,
            n_trades=10,
        )
        assert result.oos_return == 0.05
        assert result.oos_sharpe == 1.2
        assert result.n_trades == 10


class TestWFValidationReport:
    def test_defaults(self):
        report = WFValidationReport()
        assert report.windows == []
        assert report.avg_oos_sharpe == 0.0
        assert report.passed is False
        assert report.target_sharpe == 1.0
        assert report.baseline_sharpe == 1.499

    def test_summary_passed(self):
        report = WFValidationReport(
            windows=[
                WFWindowResult(
                    window_id=0,
                    train_start="20260101",
                    train_end="20260331",
                    test_start="20260401",
                    test_end="20260630",
                    n_pairs_trained=1,
                    n_pairs_tested=1,
                    oos_return=0.1,
                    oos_sharpe=1.5,
                    n_trades=5,
                )
            ],
            avg_oos_sharpe=1.5,
            avg_oos_return=0.1,
            total_trades=5,
            oos_sharpe_std=0.1,
            passed=True,
        )
        text = report.summary()
        assert "✅ PASS" in text
        assert "1.5000" in text
        assert "窗口数: 1" in text

    def test_summary_failed(self):
        report = WFValidationReport(avg_oos_sharpe=0.5, passed=False)
        text = report.summary()
        assert "❌ FAIL" in text
        assert "0.5000" in text


class TestWalkForwardPairsValidatorInit:
    def test_defaults(self):
        validator = WalkForwardPairsValidator()
        assert validator.train_window == 120
        assert validator.test_window == 60
        assert validator.step == 60
        assert validator.target_sharpe == 1.0

    def test_custom_params(self):
        validator = WalkForwardPairsValidator(
            train_window=100,
            test_window=50,
            step=25,
            significance=0.01,
            zscore_window=10,
            entry_z=1.5,
            exit_z=0.3,
            target_sharpe=1.5,
        )
        assert validator.train_window == 100
        assert validator.test_window == 50
        assert validator.step == 25
        assert validator.target_sharpe == 1.5

    def test_step_minimum_one(self):
        validator = WalkForwardPairsValidator(step=0)
        assert validator.step == 1


class TestExtractCloses:
    def test_valid_dataframes(self):
        validator = WalkForwardPairsValidator()
        closes = pd.DataFrame(
            {
                "000001.SZ": [10.0, 10.5, 11.0],
                "000002.SZ": [20.0, 19.5, 19.0],
            },
            index=pd.date_range("20260101", periods=3),
        )
        price_data = {"000001.SZ": closes, "000002.SZ": closes}
        result = validator._extract_closes(price_data)
        assert list(result.columns) == ["000001.SZ", "000002.SZ"]
        assert len(result) == 3

    def test_first_column_fallback(self):
        validator = WalkForwardPairsValidator()
        df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
        price_data = {"s1": df}
        result = validator._extract_closes(price_data)
        assert list(result.columns) == ["s1"]
        assert list(result["s1"]) == [1.0, 2.0]

    def test_empty_price_data(self):
        validator = WalkForwardPairsValidator()
        assert validator._extract_closes({}).empty

    def test_non_dataframe_skipped(self):
        validator = WalkForwardPairsValidator()
        price_data = {"s1": [1.0, 2.0]}
        assert validator._extract_closes(price_data).empty


class TestValidate:
    def test_empty_price_data(self):
        validator = WalkForwardPairsValidator()
        report = validator.validate({})
        assert report.windows == []
        assert report.passed is False

    def test_insufficient_data(self):
        validator = WalkForwardPairsValidator(train_window=120, test_window=60)
        index = pd.date_range("20260101", periods=100)
        closes = pd.DataFrame({"000001.SZ": np.linspace(10, 20, 100)}, index=index)
        report = validator.validate({"000001.SZ": closes})
        assert report.windows == []
        assert report.passed is False

    def test_successful_validation(self):
        validator = WalkForwardPairsValidator(
            train_window=60,
            test_window=30,
            step=30,
        )
        np.random.seed(0)
        n = 150
        index = pd.date_range("20260101", periods=n)
        base = np.linspace(10, 12, n)
        closes = pd.DataFrame(
            {
                "000001.SZ": base + np.random.normal(0, 0.05, n),
                "000002.SZ": base + np.random.normal(0, 0.05, n),
            },
            index=index,
        )
        report = validator.validate({"000001.SZ": closes, "000002.SZ": closes})
        assert isinstance(report, WFValidationReport)
        assert len(report.windows) > 0
        assert report.total_trades >= 0
        assert report.avg_oos_sharpe >= -10.0

    def test_passed_threshold(self):
        validator = WalkForwardPairsValidator(
            train_window=40, test_window=20, step=20, target_sharpe=0.0
        )
        n = 100
        index = pd.date_range("20260101", periods=n)
        closes = pd.DataFrame(
            {
                "000001.SZ": np.linspace(10, 15, n),
                "000002.SZ": np.linspace(20, 25, n),
            },
            index=index,
        )
        report = validator.validate({"000001.SZ": closes, "000002.SZ": closes})
        assert report.target_sharpe == 0.0
        assert report.passed is True


class TestRunSingleWindow:
    def test_no_pairs_trained(self):
        validator = WalkForwardPairsValidator()
        index = pd.date_range("20260101", periods=10)
        train_data = pd.DataFrame({"000001.SZ": np.linspace(10, 20, 10)}, index=index)
        test_data = pd.DataFrame({"000001.SZ": np.linspace(20, 30, 10)}, index=index)
        closes = pd.concat([train_data, test_data])
        result = validator._run_single_window(
            window_id=0,
            train_data=train_data,
            test_data=test_data,
            train_start_idx=0,
            train_end_idx=10,
            test_start_idx=10,
            test_end_idx=20,
            closes=closes,
        )
        assert isinstance(result, WFWindowResult)
        assert result.n_pairs_trained == 0
        assert result.n_pairs_tested == 0
        assert result.n_trades == 0

    def test_insufficient_test_data(self):
        validator = WalkForwardPairsValidator(zscore_window=20)
        index = pd.date_range("20260101", periods=30)
        closes = pd.DataFrame({"000001.SZ": np.linspace(10, 20, 30)}, index=index)
        train_data = closes.iloc[:10]
        test_data = closes.iloc[10:20]
        result = validator._run_single_window(
            window_id=0,
            train_data=train_data,
            test_data=test_data,
            train_start_idx=0,
            train_end_idx=10,
            test_start_idx=10,
            test_end_idx=20,
            closes=closes,
        )
        assert result.n_trades == 0
        assert result.oos_sharpe == 0.0


class TestModuleExports:
    def test_all_exports(self):
        from utils.strategy.arbitrage import pairs_trading as module

        assert hasattr(module, "WFValidationReport")
        assert hasattr(module, "WFWindowResult")
        assert hasattr(module, "WalkForwardPairsValidator")
        assert hasattr(module, "generate_rotation_signals") is False
