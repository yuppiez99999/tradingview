# -*- coding: utf-8 -*-
"""vol_target_controller 单元测试 — 波动率目标控制器全覆盖.

被测模块: utils/vol_target_controller.py
覆盖目标: >=95%
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.vol_target_controller import VolTargetController  # noqa: E402


# ============================================================
# calc_realized_vol
# ============================================================

class TestCalcRealizedVol:
    def test_with_provided_returns(self):
        vtc = VolTargetController()
        returns = [0.01, -0.02, 0.005, -0.008, 0.012, 0.003, -0.001, 0.006]
        vol = vtc.calc_realized_vol(returns)
        assert vol > 0
        assert vol < 1.0

    def test_insufficient_returns(self):
        vtc = VolTargetController()
        vol = vtc.calc_realized_vol([0.01, 0.02])
        assert vol == 0.20

    def test_empty_returns(self):
        vtc = VolTargetController()
        vol = vtc.calc_realized_vol([])
        assert vol == 0.20

    def test_none_returns(self, monkeypatch):
        vtc = VolTargetController()
        monkeypatch.setattr(vtc, "_load_portfolio_returns", lambda: [])
        vol = vtc.calc_realized_vol(None)
        assert vol == 0.20

    def test_constant_returns(self):
        vtc = VolTargetController()
        vol = vtc.calc_realized_vol([0.01] * 20)
        assert vol >= 0

    def test_lookback_window(self):
        vtc = VolTargetController()
        long_returns = [0.01] * 50 + [0.05] * 5
        vol = vtc.calc_realized_vol(long_returns)
        assert vol > 0


# ============================================================
# calc_vol_scale
# ============================================================

class TestCalcVolScale:
    def test_normal_vol(self):
        vtc = VolTargetController()
        scale = vtc.calc_vol_scale(0.12)
        assert scale == pytest.approx(1.0)

    def test_high_vol_scales_down(self):
        vtc = VolTargetController()
        scale = vtc.calc_vol_scale(0.30)
        assert scale < 1.0
        assert scale >= vtc.VOL_SCALE_FLOOR

    def test_low_vol_capped(self):
        vtc = VolTargetController()
        scale = vtc.calc_vol_scale(0.05)
        assert scale == vtc.VOL_SCALE_CAP

    def test_zero_vol(self):
        vtc = VolTargetController()
        scale = vtc.calc_vol_scale(0.0)
        assert scale == vtc.VOL_SCALE_CAP

    def test_floor_enforced(self):
        vtc = VolTargetController()
        scale = vtc.calc_vol_scale(1.0)
        assert scale == vtc.VOL_SCALE_FLOOR

    def test_none_realized_vol(self, monkeypatch):
        vtc = VolTargetController()
        monkeypatch.setattr(vtc, "calc_realized_vol", lambda: 0.24)
        scale = vtc.calc_vol_scale(None)
        assert scale == pytest.approx(0.5)


# ============================================================
# adjust_daily_budget
# ============================================================

class TestAdjustDailyBudget:
    def test_force_scale(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.5)
        assert r["vol_scale"] == pytest.approx(0.5)
        assert r["threshold_active"] is True
        assert r["adjusted_budget"] == pytest.approx(75_000)

    def test_normal_no_reduction(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.9)
        assert r["threshold_active"] is False
        assert r["adjusted_budget"] == 150_000

    def test_high_vol_reduction(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, realized_vol=0.30)
        assert r["threshold_active"] is True
        assert r["adjusted_budget"] < 150_000
        assert r["reduction_pct"] > 0

    def test_zero_budget(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(0, force_scale=0.5)
        assert r["adjusted_budget"] == 0

    def test_result_fields(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.5)
        assert "original_budget" in r
        assert "vol_scale" in r
        assert "threshold_active" in r
        assert "adjusted_budget" in r
        assert "reduction_pct" in r
        assert "realized_vol" in r
        assert "target_vol" in r
        assert "recommendation" in r
        assert "timestamp" in r

    def test_recommendation_normal(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.9)
        assert "正常" in r["recommendation"]

    def test_recommendation_moderate(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.6)
        assert "适度" in r["recommendation"]

    def test_recommendation_severe(self):
        vtc = VolTargetController()
        r = vtc.adjust_daily_budget(150_000, force_scale=0.35)
        assert "大幅" in r["recommendation"]


# ============================================================
# load_latest_scale
# ============================================================

class TestLoadLatestScale:
    def test_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.vol_target_controller.CACHE_DIR", tmp_path)
        assert VolTargetController.load_latest_scale() is None

    def test_with_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.vol_target_controller.CACHE_DIR", tmp_path)
        vtc = VolTargetController()
        vtc.adjust_daily_budget(150_000, force_scale=0.5)
        scale = VolTargetController.load_latest_scale()
        assert scale is not None
        assert scale == pytest.approx(0.5)

    def test_corrupt_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.vol_target_controller.CACHE_DIR", tmp_path)
        (tmp_path / "vol_target_latest.json").write_text("invalid json")
        assert VolTargetController.load_latest_scale() is None