"""chip_distribution 单元测试 — CYQ 筹码分布因子全覆盖.

被测模块: utils/alpha_factor/chip_distribution.py
覆盖目标: >=90%
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha_factor.chip_distribution import (  # noqa: E402
    ChipDistributionEngine,
    ChipSnapshot,
    compute_chip_factors,
)

# ============================================================
# 辅助: 合成 OHLCV 数据
# ============================================================

def _make_ohlcv(n: int = 160, base: float = 10.0, vol: float = 1000.0):
    rng = np.random.default_rng(42)
    closes = [base + rng.normal(0, 0.2) for _ in range(n)]
    highs = [c + abs(rng.normal(0, 0.1)) for c in closes]
    lows = [c - abs(rng.normal(0, 0.1)) for c in closes]
    volumes = [vol + rng.normal(0, 100) for _ in range(n)]
    return closes, highs, lows, volumes


# ============================================================
# ChipDistributionEngine — update
# ============================================================

class TestEngineUpdate:
    def test_insufficient_window_returns_none(self):
        engine = ChipDistributionEngine(window=150)
        closes = [10.0] * 100
        highs = [10.1] * 100
        lows = [9.9] * 100
        volumes = [1000.0] * 100
        snap = engine.update("600519", closes, highs, lows, volumes)
        assert snap is None

    def test_valid_window_returns_snapshot(self):
        engine = ChipDistributionEngine(window=150)
        closes, highs, lows, volumes = _make_ohlcv(160)
        snap = engine.update("600519", closes, highs, lows, volumes)
        assert snap is not None
        assert isinstance(snap, ChipSnapshot)
        assert snap.symbol == "600519"

    def test_snapshot_fields(self):
        engine = ChipDistributionEngine(window=150)
        closes, highs, lows, volumes = _make_ohlcv(160)
        snap = engine.update("600519", closes, highs, lows, volumes)
        assert 0 <= snap.profit_ratio <= 1.0
        assert 0 <= snap.concentration <= 1.0
        assert snap.avg_cost > 0
        assert snap.current_price > 0
        assert snap.peak_price > 0

    def test_distribution_normalized(self):
        engine = ChipDistributionEngine(window=150)
        closes, highs, lows, volumes = _make_ohlcv(160)
        snap = engine.update("600519", closes, highs, lows, volumes)
        assert snap.distribution.sum() == pytest.approx(1.0, abs=1e-6)

    def test_free_float_shares(self):
        engine = ChipDistributionEngine(window=150)
        closes, highs, lows, volumes = _make_ohlcv(160)
        snap = engine.update("600519", closes, highs, lows, volumes, free_float_shares=1e8)
        assert snap is not None

    def test_multiple_updates_same_symbol(self):
        engine = ChipDistributionEngine(window=150)
        closes, highs, lows, volumes = _make_ohlcv(160)
        snap1 = engine.update("600519", closes[:155], highs[:155], lows[:155], volumes[:155])
        snap2 = engine.update("600519", closes[:160], highs[:160], lows[:160], volumes[:160])
        assert snap1 is not None
        assert snap2 is not None

    def test_multiple_symbols(self):
        engine = ChipDistributionEngine(window=150)
        c1, h1, l1, v1 = _make_ohlcv(160, base=10.0)
        c2, h2, l2, v2 = _make_ohlcv(160, base=50.0)
        snap1 = engine.update("600519", c1, h1, l1, v1)
        snap2 = engine.update("000858", c2, h2, l2, v2)
        assert snap1 is not None
        assert snap2 is not None
        assert snap1.symbol != snap2.symbol


# ============================================================
# 静态方法
# ============================================================

class TestStaticMethods:
    def test_bin_edges_fixed(self):
        edges = ChipDistributionEngine._bin_edges_fixed(10.0, 20.0, 150)
        assert len(edges) == 151
        assert edges[0] < 10.0
        assert edges[-1] > 20.0

    def test_bin_edges_fixed_lo_equal_hi(self):
        edges = ChipDistributionEngine._bin_edges_fixed(10.0, 10.0, 50)
        assert len(edges) == 51

    def test_extend_edges(self):
        old = np.linspace(10, 20, 11)
        new = ChipDistributionEngine._extend_edges(old, 5, 25)
        assert new[0] <= 5
        assert new[-1] >= 25

    def test_align_distribution_same_edges(self):
        edges = np.linspace(10, 20, 11)
        dist = np.array([0.1] * 10)
        result = ChipDistributionEngine._align_distribution(edges, dist, edges)
        np.testing.assert_array_almost_equal(result, dist)

    def test_align_distribution_different_edges(self):
        old = np.linspace(10, 20, 11)
        new = np.linspace(8, 22, 15)
        dist = np.array([0.1] * 10)
        result = ChipDistributionEngine._align_distribution(old, dist, new)
        assert result.sum() == pytest.approx(1.0, abs=1e-6)

    def test_triangle_weights(self):
        centers = np.linspace(10, 20, 11)
        w = ChipDistributionEngine._triangle_weights(centers, 15.0, 2.0)
        assert w.sum() == pytest.approx(1.0)
        assert w[5] > w[0]

    def test_triangle_weights_zero_spread(self):
        centers = np.linspace(10, 20, 11)
        w = ChipDistributionEngine._triangle_weights(centers, 15.0, 0)
        assert w.sum() == pytest.approx(1.0)


# ============================================================
# compute_chip_factors
# ============================================================

class TestComputeChipFactors:
    def test_basic(self):
        c, h, lows, v = _make_ohlcv(160)
        price_data = {"600519": {"closes": c, "highs": h, "lows": lows, "volumes": v}}
        factors = compute_chip_factors(price_data, window=150)
        assert "CYQ_PROFIT_RATIO" in factors
        assert "CYQ_CONCENTRATION" in factors
        assert "CYQ_COST_DEVIATION" in factors
        assert "CYQ_PEAK_POSITION" in factors
        assert "600519" in factors["CYQ_PROFIT_RATIO"].values

    def test_skip_short_window(self):
        c, h, lows, v = _make_ohlcv(100)
        price_data = {"600519": {"closes": c, "highs": h, "lows": lows, "volumes": v}}
        factors = compute_chip_factors(price_data, window=150)
        assert len(factors["CYQ_PROFIT_RATIO"].values) == 0

    def test_multiple_symbols(self):
        c1, h1, l1, v1 = _make_ohlcv(160, base=10.0)
        c2, h2, l2, v2 = _make_ohlcv(160, base=50.0)
        price_data = {
            "600519": {"closes": c1, "highs": h1, "lows": l1, "volumes": v1},
            "000858": {"closes": c2, "highs": h2, "lows": l2, "volumes": v2},
        }
        factors = compute_chip_factors(price_data, window=150)
        assert len(factors["CYQ_PROFIT_RATIO"].values) == 2

    def test_missing_ohlcv_fields(self):
        c, _, _, _ = _make_ohlcv(160)
        price_data = {"600519": {"closes": c}}
        factors = compute_chip_factors(price_data, window=150)
        assert "600519" in factors["CYQ_PROFIT_RATIO"].values

    def test_free_float_shares(self):
        c, h, lows, v = _make_ohlcv(160)
        price_data = {"600519": {"closes": c, "highs": h, "lows": lows, "volumes": v}}
        ffs = {"600519": 1e8}
        factors = compute_chip_factors(price_data, window=150, free_float_shares=ffs)
        assert "600519" in factors["CYQ_PROFIT_RATIO"].values
