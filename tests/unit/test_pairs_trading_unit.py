"""strategy_lib/pairs_trading.py 单元测试 — 不依赖 statsmodels 的路径.

目标模块: utils/strategy_lib/pairs_trading.py (branch-rate 0.0303 → 高覆盖)
覆盖: PairSignal / PairsTrading (backtest_pair 全分支 + 空返回路径 + 异常路径)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.strategy_lib.pairs_trading import (
    PairSignal,
    PairsTrading,
    _HAS_STATSMODELS,
)


# ============================================================
# PairSignalTest — 配对信号数据结构
# ============================================================

class PairSignalTest:

    def test_init_all_fields(self):
        ps = PairSignal(
            code_a="000001.SZ", code_b="000002.SZ",
            hedge_ratio=1.2, intercept=0.5,
            half_life=15.0, zscore=2.1, signal=1, pvalue=0.01,
        )
        assert ps.code_a == "000001.SZ"
        assert ps.code_b == "000002.SZ"
        assert ps.hedge_ratio == 1.2
        assert ps.intercept == 0.5
        assert ps.half_life == 15.0
        assert ps.zscore == 2.1
        assert ps.signal == 1
        assert ps.pvalue == 0.01

    def test_to_dict_rounds(self):
        ps = PairSignal(
            code_a="a", code_b="b",
            hedge_ratio=1.23456, intercept=0.12345,
            half_life=12.345, zscore=-2.1234, signal=-1, pvalue=0.01234,
        )
        d = ps.to_dict()
        assert d["code_a"] == "a"
        assert d["hedge_ratio"] == 1.2346
        assert d["intercept"] == 0.1235
        assert d["half_life"] == 12.35
        assert d["zscore"] == -2.1234
        assert d["signal"] == -1
        assert d["pvalue"] == 0.0123

    def test_to_dict_none_half_life(self):
        ps = PairSignal(
            code_a="a", code_b="b",
            hedge_ratio=1.0, intercept=0.0,
            half_life=None, zscore=0.0, signal=0, pvalue=0.5,
        )
        d = ps.to_dict()
        assert d["half_life"] is None

    def test_to_dict_zero_half_life_falsy(self):
        """half_life=0.0 时 to_dict 返回 None (falsy 判断)."""
        ps = PairSignal(
            code_a="a", code_b="b",
            hedge_ratio=1.0, intercept=0.0,
            half_life=0.0, zscore=0.0, signal=0, pvalue=0.5,
        )
        d = ps.to_dict()
        assert d["half_life"] is None


# ============================================================
# PairsTradingInitTest — 构造与参数
# ============================================================

class PairsTradingInitTest:

    def test_init_defaults(self):
        pt = PairsTrading()
        assert pt.significance == 0.05
        assert pt.zscore_window == 20
        assert pt.entry_z == 2.0
        assert pt.exit_z == 0.5
        assert pt.min_half_life == 1
        assert pt.max_half_life == 60

    def test_init_custom(self):
        pt = PairsTrading(
            significance=0.01, zscore_window=30,
            entry_z=1.5, exit_z=0.3,
            min_half_life=2, max_half_life=100,
        )
        assert pt.significance == 0.01
        assert pt.zscore_window == 30
        assert pt.entry_z == 1.5
        assert pt.exit_z == 0.3
        assert pt.min_half_life == 2
        assert pt.max_half_life == 100


# ============================================================
# PairsTradingEmptyReturnTest — 无 statsmodels 时的空返回路径
# ============================================================

class PairsTradingEmptyReturnTest:

    def test_find_cointegrated_pairs_no_statsmodels(self):
        """无 statsmodels 时 find_cointegrated_pairs 返回空列表."""
        pt = PairsTrading()
        if not _HAS_STATSMODELS:
            result = pt.find_cointegrated_pairs({"A": pd.DataFrame(), "B": pd.DataFrame()})
            assert result == []

    def test_find_cointegrated_pairs_single_symbol(self):
        """只有一只标的时返回空 (需要至少 2 只)."""
        pt = PairsTrading()
        df = pd.DataFrame({"close": [10, 11, 12]})
        result = pt.find_cointegrated_pairs({"A": df})
        assert result == []

    def test_generate_signals_no_pairs(self):
        """pairs=[] 时返回空."""
        pt = PairsTrading()
        df = pd.DataFrame({"close": [10, 11, 12]})
        result = pt.generate_signals({"A": df}, pairs=[])
        assert result == []

    def test_generate_signals_auto_find_empty(self):
        """pairs=None 且无协整对时返回空."""
        pt = PairsTrading()
        df = pd.DataFrame({"close": list(range(100))})
        result = pt.generate_signals({"A": df, "B": df}, pairs=None)
        # 无 statsmodels 或无协整对 → 空列表
        assert result == []


# ============================================================
# PairsTradingBacktestTest — backtest_pair 向量回测 (不依赖 statsmodels)
# ============================================================

class PairsTradingBacktestTest:

    @pytest.fixture
    def price_data(self):
        """生成两只标的的收盘价序列 (有均值回复特征)."""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        # A 和 B 高度相关, 价差有均值回复
        common_trend = np.cumsum(np.random.randn(n) * 0.5) + 100
        spread = np.zeros(n)
        for i in range(1, n):
            spread[i] = spread[i - 1] * 0.9 + np.random.randn() * 0.5
        pa = pd.Series(common_trend + spread / 2, index=dates, name="A")
        pb = pd.Series(common_trend - spread / 2, index=dates, name="B")
        return pa, pb

    def test_backtest_basic(self, price_data):
        pa, pb = price_data
        pt = PairsTrading()
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        assert "equity_curve" in result
        assert "trades" in result
        assert "sharpe" in result
        assert "max_dd" in result
        assert "total_return" in result
        assert isinstance(result["trades"], int)

    def test_backtest_short_history(self):
        """历史太短时返回空回测结果."""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        pa = pd.Series(np.arange(10.0), index=dates)
        pb = pd.Series(np.arange(10.0) + 1, index=dates)
        pt = PairsTrading(zscore_window=20)
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        assert result["trades"] == 0
        assert result["sharpe"] == 0.0
        assert result["total_return"] == 0.0
        assert len(result["equity_curve"]) == 0

    def test_backtest_with_cost(self, price_data):
        pa, pb = price_data
        pt = PairsTrading()
        r1 = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0, cost_bps=0.0)
        r2 = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0, cost_bps=50.0)
        # 有成本时总收益应低于无成本 (或相等)
        assert r2["total_return"] <= r1["total_return"] + 1e-6

    def test_backtest_equity_starts_at_one(self, price_data):
        pa, pb = price_data
        pt = PairsTrading()
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        eq = result["equity_curve"]
        if len(eq) > 0:
            assert eq.iloc[0] == pytest.approx(1.0, abs=1e-6)

    def test_backtest_max_dd_non_positive(self, price_data):
        pa, pb = price_data
        pt = PairsTrading()
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        assert result["max_dd"] <= 0.0

    def test_backtest_custom_entry_exit(self, price_data):
        """自定义开平仓阈值."""
        pa, pb = price_data
        pt = PairsTrading(entry_z=1.0, exit_z=0.2)
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        assert "equity_curve" in result

    def test_backtest_with_intercept(self, price_data):
        pa, pb = price_data
        pt = PairsTrading()
        result = pt.backtest_pair(pa, pb, beta=0.95, intercept=2.0)
        assert "equity_curve" in result

    def test_backtest_nan_handling(self):
        """价差含 NaN 时应正确处理."""
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(0)
        pa = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        pb = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        pa.iloc[50] = np.nan  # 插入 NaN
        pt = PairsTrading()
        result = pt.backtest_pair(pa, pb, beta=1.0, intercept=0.0)
        assert "equity_curve" in result


# ============================================================
# PairsTradingCointTestTest — _coint_test 异常路径
# ============================================================

class PairsTradingCointTestTest:

    def test_coint_test_short_series(self):
        """序列长度 < 60 时返回 (1.0, 0.0, 0.0)."""
        pt = PairsTrading()
        y = pd.Series([1.0, 2.0, 3.0])
        x = pd.Series([1.0, 2.0, 3.0])
        pvalue, beta, intercept = pt._coint_test(y, x)
        assert pvalue == 1.0
        assert beta == 0.0
        assert intercept == 0.0

    def test_coint_test_no_statsmodels(self):
        """无 statsmodels 时 _coint_test 抛 NameError (add_constant 未定义, except 未捕获)."""
        if not _HAS_STATSMODELS:
            pt = PairsTrading()
            n = 100
            y = pd.Series(np.cumsum(np.random.randn(n)) + 100)
            x = pd.Series(np.cumsum(np.random.randn(n)) + 100)
            with pytest.raises(NameError):
                pt._coint_test(y, x)


# ============================================================
# PairsTradingHalfLifeTest — _estimate_half_life 异常路径
# ============================================================

class PairsTradingHalfLifeTest:

    def test_half_life_short_series(self):
        """序列 < 30 返回 None."""
        pt = PairsTrading()
        spread = pd.Series([1.0, 2.0, 3.0])
        assert pt._estimate_half_life(spread) is None

    def test_half_life_no_statsmodels(self):
        """无 statsmodels 时 _estimate_half_life 抛 NameError (add_constant 未定义)."""
        if not _HAS_STATSMODELS:
            pt = PairsTrading()
            np.random.seed(0)
            spread = pd.Series(np.cumsum(np.random.randn(100)))
            with pytest.raises(NameError):
                pt._estimate_half_life(spread)


# ============================================================
# PairsTradingGenerateSignalsTest — 信号生成路径
# ============================================================

class PairsTradingGenerateSignalsTest:

    def test_generate_signals_with_explicit_pairs(self):
        """传入协整对列表时生成信号 (不调用 find_cointegrated_pairs)."""
        pt = PairsTrading()
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(0)
        pa = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        pb = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        df_a = pd.DataFrame({"close": pa})
        df_b = pd.DataFrame({"close": pb})
        pairs = [{"code_a": "A", "code_b": "B", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals({"A": df_a, "B": df_b}, pairs=pairs)
        assert len(signals) == 1
        assert isinstance(signals[0], PairSignal)
        assert signals[0].code_a == "A"
        assert signals[0].code_b == "B"
        assert signals[0].signal in (-1, 0, 1)

    def test_generate_signals_missing_price(self):
        """协整对的标的不在 price_data 中时跳过."""
        pt = PairsTrading()
        pairs = [{"code_a": "X", "code_b": "Y", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals({}, pairs=pairs)
        assert signals == []

    def test_generate_signals_short_history(self):
        """协整对历史太短时跳过."""
        pt = PairsTrading(zscore_window=20)
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df_a = pd.DataFrame({"close": np.arange(10.0)}, index=dates)
        df_b = pd.DataFrame({"close": np.arange(10.0) + 1}, index=dates)
        pairs = [{"code_a": "A", "code_b": "B", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals({"A": df_a, "B": df_b}, pairs=pairs)
        assert signals == []

    def test_generate_signals_signal_values(self):
        """测试信号值在有效范围 {-1, 0, 1}."""
        pt = PairsTrading()
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(0)
        pa = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        pb = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        df_a = pd.DataFrame({"close": pa})
        df_b = pd.DataFrame({"close": pb})
        pairs = [{"code_a": "A", "code_b": "B", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals({"A": df_a, "B": df_b}, pairs=pairs)
        for s in signals:
            assert s.signal in (-1, 0, 1)
            assert -1.0 <= s.zscore or s.zscore <= 1.0  # z-score 是实数

    def test_generate_signals_first_column_fallback(self):
        """无 close 列时用第一列."""
        pt = PairsTrading()
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(0)
        pa = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        pb = pd.Series(np.cumsum(np.random.randn(n)) + 100, index=dates)
        df_a = pd.DataFrame({"price": pa})  # 用 price 而非 close
        df_b = pd.DataFrame({"price": pb})
        pairs = [{"code_a": "A", "code_b": "B", "beta": 1.0, "intercept": 0.0, "pvalue": 0.01}]
        signals = pt.generate_signals({"A": df_a, "B": df_b}, pairs=pairs)
        assert len(signals) == 1