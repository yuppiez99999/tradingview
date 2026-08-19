"""momentum_reversal_engine 单元测试 — 动量反转引擎全覆盖.

被测模块: utils/momentum_reversal_engine.py
覆盖目标: >=90%
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.momentum_reversal_engine import (  # noqa: E402
    MomentumReversalEngine,
)


def _make_prices(n: int = 260, trend: float = 0.0, vol: float = 0.02):
    rng = np.random.default_rng(42)
    prices = [10.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + trend + rng.normal(0, vol)))
    return prices


# ============================================================
# generate_signals
# ============================================================

class TestGenerateSignals:
    def test_empty(self):
        engine = MomentumReversalEngine()
        result = engine.generate_signals({})
        assert len(result.signals) == 0

    def test_none_input(self):
        engine = MomentumReversalEngine()
        result = engine.generate_signals(None)
        assert len(result.signals) == 0

    def test_single_symbol(self):
        engine = MomentumReversalEngine()
        closes = _make_prices(260, trend=0.001)
        result = engine.generate_signals({"600519": {"closes": closes}})
        assert "600519" in result.signals
        sig = result.signals["600519"]
        assert -1 <= sig.signal_strength <= 1
        assert 0 <= sig.confidence <= 1

    def test_multiple_symbols(self):
        engine = MomentumReversalEngine()
        data = {
            "A": {"closes": _make_prices(260, trend=0.002)},
            "B": {"closes": _make_prices(260, trend=-0.001)},
        }
        result = engine.generate_signals(data)
        assert len(result.signals) == 2

    def test_result_diagnostics(self):
        engine = MomentumReversalEngine()
        data = {
            "A": {"closes": _make_prices(260, trend=0.002)},
            "B": {"closes": _make_prices(260, trend=-0.002)},
        }
        result = engine.generate_signals(data)
        assert result.bullish_count + result.bearish_count + result.neutral_count == 2
        assert result.strategy_state in ("TRENDING_UP", "TRENDING_DOWN", "REVERSING", "NEUTRAL")

    def test_with_volumes(self):
        engine = MomentumReversalEngine()
        closes = _make_prices(260)
        volumes = [1000.0] * 260
        result = engine.generate_signals({"A": {"closes": closes, "volumes": volumes}})
        assert "A" in result.signals

    def test_short_data(self):
        engine = MomentumReversalEngine()
        result = engine.generate_signals({"A": {"closes": [10, 11, 12]}})
        assert "A" in result.signals
        assert result.signals["A"].combined_signal == 0.0


# ============================================================
# signal properties
# ============================================================

class TestSignalProperties:
    def test_uptrend_positive_tsmom(self):
        engine = MomentumReversalEngine()
        closes = _make_prices(260, trend=0.003, vol=0.01)
        result = engine.generate_signals({"A": {"closes": closes}})
        sig = result.signals["A"]
        assert sig.tsmom_60d > 0 or sig.tsmom_120d > 0

    def test_downtrend_negative_tsmom(self):
        engine = MomentumReversalEngine()
        closes = _make_prices(260, trend=-0.003, vol=0.01)
        result = engine.generate_signals({"A": {"closes": closes}})
        sig = result.signals["A"]
        assert sig.tsmom_60d < 0 or sig.tsmom_120d < 0

    def test_target_weight_bounded(self):
        engine = MomentumReversalEngine(max_position=0.10)
        data = {"A": {"closes": _make_prices(260, trend=0.003)}}
        result = engine.generate_signals(data)
        assert abs(result.signals["A"].target_weight) <= 0.10

    def test_target_weight_below_threshold(self):
        engine = MomentumReversalEngine(signal_threshold=0.99)
        data = {"A": {"closes": _make_prices(260)}}
        result = engine.generate_signals(data)
        assert result.signals["A"].target_weight == 0.0


# ============================================================
# filter_low_confidence
# ============================================================

class TestFilterLowConfidence:
    def test_filters(self):
        engine = MomentumReversalEngine()
        data = {
            "A": {"closes": _make_prices(260, trend=0.003)},
            "B": {"closes": _make_prices(260, trend=0.0, vol=0.001)},
        }
        result = engine.generate_signals(data)
        filtered = engine.filter_low_confidence(result, min_confidence=0.01)
        assert len(filtered.signals) <= len(result.signals)

    def test_high_threshold_filters_all(self):
        engine = MomentumReversalEngine()
        data = {"A": {"closes": _make_prices(260)}}
        result = engine.generate_signals(data)
        filtered = engine.filter_low_confidence(result, min_confidence=1.5)
        assert len(filtered.signals) == 0


# ============================================================
# get_position_adjustment
# ============================================================

class TestPositionAdjustment:
    def test_basic(self):
        engine = MomentumReversalEngine()
        data = {"A": {"closes": _make_prices(260, trend=0.003)}}
        result = engine.generate_signals(data)
        adjustments = engine.get_position_adjustment(result, {"A": 0.0})
        assert isinstance(adjustments, dict)

    def test_no_adjustment_needed(self):
        engine = MomentumReversalEngine()
        data = {"A": {"closes": _make_prices(260)}}
        result = engine.generate_signals(data)
        target = result.signals["A"].target_weight
        adjustments = engine.get_position_adjustment(result, {"A": target})
        assert "A" not in adjustments

    def test_small_delta_ignored(self):
        engine = MomentumReversalEngine()
        data = {"A": {"closes": _make_prices(260)}}
        result = engine.generate_signals(data)
        target = result.signals["A"].target_weight
        adjustments = engine.get_position_adjustment(result, {"A": target + 0.0001})
        assert "A" not in adjustments


# ============================================================
# custom weights
# ============================================================

class TestCustomWeights:
    def test_custom_tsmom_weights(self):
        engine = MomentumReversalEngine(tsmom_weights={60: 1.0})
        data = {"A": {"closes": _make_prices(260, trend=0.003)}}
        result = engine.generate_signals(data)
        assert "A" in result.signals

    def test_custom_category_weights(self):
        engine = MomentumReversalEngine(
            tsmom_category_weight=0.6,
            xsmom_category_weight=0.2,
            reversal_category_weight=0.2,
        )
        data = {"A": {"closes": _make_prices(260)}}
        result = engine.generate_signals(data)
        assert "A" in result.signals
