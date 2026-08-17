# -*- coding: utf-8 -*-
"""factor_model 单元测试 — 五维因子选股模型全覆盖.

被测模块: utils/factor_model.py
覆盖目标: >=90%
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.factor_model import FactorModel, FactorResult  # noqa: E402


def _make_klines(n: int = 252, base: float = 10.0):
    rng = np.random.default_rng(42)
    closes = [base * (1 + rng.normal(0, 0.02)) for _ in range(n)]
    return pd.DataFrame({"close": closes, "amount": [1e6] * n})


# ============================================================
# value_factor
# ============================================================

class TestValueFactor:
    def test_low_pe_high_score(self):
        m = FactorModel()
        score = m.value_factor(pd.DataFrame(), pe=8.0, pb=1.0, dividend_yield=0.03)
        assert score > 0

    def test_high_pe_low_score(self):
        m = FactorModel()
        score = m.value_factor(pd.DataFrame(), pe=60.0, pb=5.0, dividend_yield=0.0)
        assert score < 0

    def test_no_fundamentals(self):
        m = FactorModel()
        assert m.value_factor(pd.DataFrame()) == 0.0

    def test_score_range(self):
        m = FactorModel()
        score = m.value_factor(pd.DataFrame(), pe=100, pb=10, dividend_yield=0.1)
        assert -1 <= score <= 1


# ============================================================
# quality_factor
# ============================================================

class TestQualityFactor:
    def test_high_quality(self):
        m = FactorModel()
        score = m.quality_factor(pd.DataFrame(), roe=0.20, debt_ratio=0.2, profit_margin=0.15)
        assert score > 0

    def test_low_quality(self):
        m = FactorModel()
        score = m.quality_factor(pd.DataFrame(), roe=0.02, debt_ratio=0.8, profit_margin=0.01)
        assert score < 0

    def test_no_data(self):
        m = FactorModel()
        assert m.quality_factor(pd.DataFrame()) == 0.0


# ============================================================
# momentum_factor
# ============================================================

class TestMomentumFactor:
    def test_uptrend(self):
        m = FactorModel()
        df = pd.DataFrame({"close": np.linspace(10, 15, 100)})
        assert m.momentum_factor(df) > 0

    def test_downtrend(self):
        m = FactorModel()
        df = pd.DataFrame({"close": np.linspace(15, 10, 100)})
        assert m.momentum_factor(df) < 0

    def test_empty(self):
        m = FactorModel()
        assert m.momentum_factor(pd.DataFrame()) == 0.0

    def test_short_data(self):
        m = FactorModel()
        df = pd.DataFrame({"close": [10, 11, 12]})
        assert m.momentum_factor(df) == 0.0

    def test_no_close_column(self):
        m = FactorModel()
        df = pd.DataFrame({"open": [10, 11]})
        assert m.momentum_factor(df) == 0.0


# ============================================================
# growth_factor
# ============================================================

class TestGrowthFactor:
    def test_high_growth(self):
        m = FactorModel()
        assert m.growth_factor(revenue_growth=0.3, earnings_growth=0.4) > 0

    def test_negative_growth(self):
        m = FactorModel()
        assert m.growth_factor(revenue_growth=-0.1, earnings_growth=-0.2) < 0

    def test_no_data(self):
        m = FactorModel()
        assert m.growth_factor() == 0.0


# ============================================================
# safety_factor
# ============================================================

class TestSafetyFactor:
    def test_low_vol_safe(self):
        m = FactorModel()
        rng = np.random.default_rng(42)
        df = pd.DataFrame({"close": 10 + rng.normal(0, 0.01, 100)})
        assert m.safety_factor(df) > 0

    def test_high_vol_unsafe(self):
        m = FactorModel()
        rng = np.random.default_rng(42)
        df = pd.DataFrame({"close": 10 + rng.normal(0, 0.5, 100)})
        score = m.safety_factor(df)
        assert -1 <= score <= 1

    def test_empty(self):
        m = FactorModel()
        assert m.safety_factor(pd.DataFrame()) == 0.0

    def test_short_data(self):
        m = FactorModel()
        df = pd.DataFrame({"close": [10, 11]})
        assert m.safety_factor(df) == 0.0


# ============================================================
# evaluate
# ============================================================

class TestEvaluate:
    def test_basic(self):
        m = FactorModel()
        klines = {"600519": _make_klines(), "000858": _make_klines(base=20)}
        results = m.evaluate(klines)
        assert len(results) == 2
        assert all(isinstance(r, FactorResult) for r in results.values())

    def test_with_fundamentals(self):
        m = FactorModel()
        klines = {"600519": _make_klines()}
        fund = {"600519": {"pe": 12, "pb": 1.5, "roe": 0.15, "debt_ratio": 0.3}}
        results = m.evaluate(klines, fundamentals=fund)
        assert "600519" in results
        assert "value" in results["600519"].factors

    def test_with_events(self):
        m = FactorModel()
        klines = {"600519": _make_klines()}
        events = {"600519": {"sentiment": 0.5, "event_impact": -0.2}}
        results = m.evaluate(klines, event_factors=events)
        assert results["600519"].factors.get("sentiment") == 0.5

    def test_ranking(self):
        m = FactorModel()
        klines = {"A": _make_klines(), "B": _make_klines(base=20), "C": _make_klines(base=30)}
        results = m.evaluate(klines)
        ranks = [r.rank for r in results.values()]
        assert sorted(ranks) == [1, 2, 3]

    def test_skip_short(self):
        m = FactorModel()
        klines = {"A": pd.DataFrame({"close": [10, 11, 12]})}
        results = m.evaluate(klines)
        assert len(results) == 0

    def test_signal_in_result(self):
        m = FactorModel()
        klines = {"600519": _make_klines()}
        results = m.evaluate(klines)
        assert results["600519"].signal in ("strong_buy", "buy", "hold", "sell", "strong_sell")


# ============================================================
# generate_signal
# ============================================================

class TestGenerateSignal:
    def test_empty(self):
        m = FactorModel()
        sig = m.generate_signal({})
        assert sig["signal"] == "hold"

    def test_basic(self):
        m = FactorModel()
        klines = {"A": _make_klines(), "B": _make_klines(base=20)}
        results = m.evaluate(klines)
        sig = m.generate_signal(results)
        assert "avg_composite" in sig
        assert "top_3" in sig
        assert "bottom_3" in sig
        assert "distribution" in sig


# ============================================================
# _to_signal
# ============================================================

class TestToSignal:
    def test_strong_buy(self):
        m = FactorModel()
        assert m._to_signal(0.5) == "strong_buy"

    def test_buy(self):
        m = FactorModel()
        assert m._to_signal(0.15) == "buy"

    def test_hold(self):
        m = FactorModel()
        assert m._to_signal(0.0) == "hold"

    def test_sell(self):
        m = FactorModel()
        assert m._to_signal(-0.15) == "sell"

    def test_strong_sell(self):
        m = FactorModel()
        assert m._to_signal(-0.5) == "strong_sell"


# ============================================================
# compute_factor_correlation
# ============================================================

class TestCorrelation:
    def test_basic(self):
        m = FactorModel()
        klines = {"A": _make_klines(), "B": _make_klines(base=20), "C": _make_klines(base=30)}
        results = m.evaluate(klines)
        corr = m.compute_factor_correlation(results)
        assert isinstance(corr, pd.DataFrame)