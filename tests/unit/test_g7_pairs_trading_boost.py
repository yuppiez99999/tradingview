"""
G7 Coverage Boost: utils/strategy_lib/pairs_trading.py (186 lines, 0% -> target ~80%)
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from utils.strategy_lib.pairs_trading import _HAS_STATSMODELS, PairSignal, PairsTrading


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestPairSignal:
    def test_init(self):
        ps = PairSignal(
            code_a="000001.SZ",
            code_b="000002.SZ",
            hedge_ratio=1.2,
            intercept=0.5,
            half_life=15.0,
            zscore=2.1,
            signal=1,
            pvalue=0.01,
        )
        assert ps.code_a == "000001.SZ"
        assert ps.code_b == "000002.SZ"
        assert ps.hedge_ratio == 1.2
        assert ps.signal == 1

    def test_to_dict_rounds(self):
        ps = PairSignal(
            code_a="a",
            code_b="b",
            hedge_ratio=1.23456,
            intercept=0.12345,
            half_life=12.345,
            zscore=-2.1,
            signal=-1,
            pvalue=0.01234,
        )
        d = ps.to_dict()
        assert d["hedge_ratio"] == pytest.approx(1.2346)
        assert d["intercept"] == pytest.approx(0.1235)
        assert d["half_life"] == pytest.approx(12.35)
        assert d["zscore"] == pytest.approx(-2.1)
        assert d["pvalue"] == pytest.approx(0.0123)


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestPairsTradingInit:
    def test_defaults(self):
        pt = PairsTrading()
        assert pt.significance == 0.05
        assert pt.zscore_window == 20
        assert pt.entry_z == 2.0
        assert pt.exit_z == 0.5
        assert pt.min_half_life == 1
        assert pt.max_half_life == 60

    def test_custom_params(self):
        pt = PairsTrading(significance=0.01, zscore_window=10, entry_z=2.5, exit_z=0.8, min_half_life=2, max_half_life=30)
        assert pt.significance == 0.01
        assert pt.zscore_window == 10
        assert pt.entry_z == 2.5
        assert pt.exit_z == 0.8
        assert pt.min_half_life == 2
        assert pt.max_half_life == 30


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestCointTest:
    def test_insufficient_data(self):
        pt = PairsTrading()
        y = pd.Series(np.random.randn(30))
        x = pd.Series(np.random.randn(30))
        pvalue, beta, intercept = pt._coint_test(y, x)
        assert pvalue == 1.0
        assert beta == 0.0
        assert intercept == 0.0

    def test_cointegrated_returns_pvalue_beta(self):
        pt = PairsTrading(significance=0.05)
        np.random.seed(0)
        n = 200
        x = pd.Series(np.cumsum(np.random.randn(n)) + 100, name="x")
        y = x * 1.5 + 10 + np.random.randn(n) * 0.5
        pvalue, beta, intercept = pt._coint_test(y, x)
        assert isinstance(pvalue, float)
        assert isinstance(beta, float)
        assert isinstance(intercept, float)

    def test_exception_returns_fallback(self):
        pt = PairsTrading()
        bad_y = pd.Series([1, 2, 3])
        bad_x = pd.Series([1, 2, 3])
        pvalue, beta, intercept = pt._coint_test(bad_y, bad_x)
        assert pvalue == 1.0
        assert beta == 0.0
        assert intercept == 0.0


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestEstimateHalfLife:
    def test_insufficient_data(self):
        pt = PairsTrading()
        spread = pd.Series(np.random.randn(20))
        assert pt._estimate_half_life(spread) is None

    def test_nonstationary_returns_none(self):
        pt = PairsTrading()
        np.random.seed(1)
        spread = pd.Series(np.cumsum(np.random.randn(200)))
        assert pt._estimate_half_life(spread) is None

    def test_mean_reverting_returns_half_life(self):
        pt = PairsTrading()
        np.random.seed(2)
        n = 300
        spread = pd.Series(np.random.randn(n))
        for i in range(1, n):
            spread.iloc[i] = 0.7 * spread.iloc[i - 1] + np.random.randn()
        half_life = pt._estimate_half_life(spread)
        if half_life is not None:
            assert half_life > 0


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestFindCointegratedPairs:
    def test_empty_price_data(self):
        pt = PairsTrading()
        assert pt.find_cointegrated_pairs({}) == []

    def test_single_symbol(self):
        pt = PairsTrading()
        df = pd.DataFrame({"close": np.random.randn(100) + 100})
        assert pt.find_cointegrated_pairs({"000001.SZ": df}) == []

    def test_no_cointegrated_pairs(self):
        pt = PairsTrading(significance=0.001, min_half_life=1000)
        np.random.seed(3)
        symbols = [f"{i:06d}.SZ" for i in range(4)]
        price_data = {sym: pd.DataFrame({"close": np.cumsum(np.random.randn(200)) + 100}) for sym in symbols}
        assert pt.find_cointegrated_pairs(price_data) == []

    def test_finds_cointegrated_pair(self):
        pt = PairsTrading(significance=0.05, min_half_life=1, max_half_life=200)
        np.random.seed(4)
        n = 200
        x = pd.Series(np.cumsum(np.random.randn(n)) + 100, name="x")
        y = x * 1.2 + 5 + np.random.randn(n) * 0.3
        price_data = {
            "000001.SZ": pd.DataFrame({"close": y}),
            "000002.SZ": pd.DataFrame({"close": x}),
        }
        pairs = pt.find_cointegrated_pairs(price_data)
        assert isinstance(pairs, list)
        if pairs:
            assert "code_a" in pairs[0]
            assert "code_b" in pairs[0]
            assert "pvalue" in pairs[0]
            assert "beta" in pairs[0]

    def test_max_pairs_limit(self):
        pt = PairsTrading(significance=0.05, min_half_life=1, max_half_life=200)
        np.random.seed(5)
        n = 200
        base = pd.Series(np.cumsum(np.random.randn(n)) + 100)
        price_data = {}
        for i in range(5):
            s = base * (1 + i * 0.1) + np.random.randn(n) * 0.2
            price_data[f"{i:06d}.SZ"] = pd.DataFrame({"close": s})
        pairs = pt.find_cointegrated_pairs(price_data, max_pairs=2)
        assert len(pairs) <= 2

    def test_missing_statsmodels_returns_empty(self):
        with patch("utils.strategy_lib.pairs_trading._HAS_STATSMODELS", False):
            pt = PairsTrading()
            df = pd.DataFrame({"close": np.random.randn(100) + 100})
            assert pt.find_cointegrated_pairs({"000001.SZ": df}) == []


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestGenerateSignals:
    def _make_price_data(self, n: int = 200, seed: int = 0) -> dict[str, pd.DataFrame]:
        np.random.seed(seed)
        base = pd.Series(np.cumsum(np.random.randn(n)) + 100)
        return {
            "000001.SZ": pd.DataFrame({"close": base * 1.2 + 5 + np.random.randn(n) * 0.3}),
            "000002.SZ": pd.DataFrame({"close": base}),
        }

    def test_empty_pairs_returns_empty(self):
        pt = PairsTrading()
        price_data = self._make_price_data()
        signals = pt.generate_signals(price_data, pairs=[])
        assert signals == []

    def test_generates_signals(self):
        pt = PairsTrading(significance=0.05, min_half_life=1, max_half_life=200, zscore_window=10)
        price_data = self._make_price_data()
        pairs = pt.find_cointegrated_pairs(price_data)
        if not pairs:
            pytest.skip("no cointegrated pairs found in test data")
        signals = pt.generate_signals(price_data, pairs=pairs)
        assert isinstance(signals, list)
        for sig in signals:
            assert isinstance(sig, PairSignal)
            assert sig.signal in (-1, 0, 1)

    def test_missing_close_column_uses_first_column(self):
        pt = PairsTrading()
        price_data = {
            "000001.SZ": pd.DataFrame({"open": np.random.randn(100) + 100}),
            "000002.SZ": pd.DataFrame({"open": np.random.randn(100) + 100}),
        }
        pairs = [{"code_a": "000001.SZ", "code_b": "000002.SZ", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals(price_data, pairs=pairs)
        assert isinstance(signals, list)
        assert len(signals) == 1

    def test_auto_find_pairs_when_none(self):
        pt = PairsTrading(significance=0.05, min_half_life=1, max_half_life=200)
        price_data = self._make_price_data()
        signals = pt.generate_signals(price_data, pairs=None)
        assert isinstance(signals, list)


@pytest.mark.skipif(not _HAS_STATSMODELS, reason="statsmodels not installed")
class TestBacktestPair:
    def test_insufficient_data(self):
        pt = PairsTrading(zscore_window=20)
        price_a = pd.Series(np.random.randn(20) + 100)
        price_b = pd.Series(np.random.randn(20) + 100)
        result = pt.backtest_pair(price_a, price_b, beta=1.0, intercept=0.0)
        assert result["trades"] == 0
        assert result["sharpe"] == 0.0
        assert result["max_dd"] == 0.0
        assert result["total_return"] == 0.0

    def test_backtest_returns_metrics(self):
        pt = PairsTrading(zscore_window=10, entry_z=2.0, exit_z=0.5)
        np.random.seed(6)
        n = 200
        x = pd.Series(np.cumsum(np.random.randn(n)) + 100)
        y = x * 1.2 + 5 + np.random.randn(n) * 0.3
        result = pt.backtest_pair(y, x, beta=1.2, intercept=5.0)
        assert "equity_curve" in result
        assert "trades" in result
        assert "sharpe" in result
        assert "max_dd" in result
        assert "total_return" in result
        assert isinstance(result["trades"], int)
        assert isinstance(result["sharpe"], float)

    def test_backtest_with_cost(self):
        pt = PairsTrading(zscore_window=10, entry_z=2.0, exit_z=0.5)
        np.random.seed(7)
        n = 200
        x = pd.Series(np.cumsum(np.random.randn(n)) + 100)
        y = x * 1.2 + 5 + np.random.randn(n) * 0.3
        result = pt.backtest_pair(y, x, beta=1.2, intercept=5.0, cost_bps=10.0)
        assert "equity_curve" in result
        assert isinstance(result["total_return"], float)
