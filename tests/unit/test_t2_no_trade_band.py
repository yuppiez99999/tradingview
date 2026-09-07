"""T2 单测: 波动率调制 no-trade band.

覆盖:
    1. 纯函数 band_width / should_rebalance: 正常调制 / 边界 / sigma 无效退化 /
       目标权重无效 / 当前权重无效 fail-close;
    2. generate_rebalance_orders 集成: 传 volatility 时带内偏离被过滤、带外仍触发、
       不传 volatility 行为向后兼容 (固定 abs_tol);
    3. _load_style_volatility: 文件缺失 → None, 合法文件 → dict, 坏 JSON → None.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.execution import rebalance_execution_orders as reo  # noqa: E402
from utils.risk.no_trade_band import (  # noqa: E402
    DEFAULT_ABS_TOL,
    band_width,
    should_rebalance,
)

# ───────────────────────── 1. 纯函数 ─────────────────────────


class TestBandWidth:
    def test_volatility_modulated(self):
        # band = max(0.02, 0.5 * 0.15 * 0.25) = max(0.02, 0.01875) = 0.02
        band, reason = band_width(target_weight=0.15, sigma=0.25)
        assert reason == "volatility_modulated"
        assert band == pytest.approx(max(DEFAULT_ABS_TOL, 0.5 * 0.15 * 0.25))

    def test_high_weight_volatility_dominates(self):
        # 0.5 * 0.22 * 0.25 = 0.0275 > 0.02 → 调制带宽生效
        band, reason = band_width(target_weight=0.22, sigma=0.25)
        assert reason == "volatility_modulated"
        assert band == pytest.approx(0.0275)

    def test_low_volatility_asset_collapses_to_abs_tol(self):
        # 国债 sigma=0.05: 0.5*0.22*0.05 = 0.0055 < 0.02 → abs_tol 生效
        band, reason = band_width(target_weight=0.22, sigma=0.05)
        assert reason == "volatility_modulated"
        assert band == pytest.approx(DEFAULT_ABS_TOL)

    @pytest.mark.parametrize("bad_sigma", [None, 0.0, -0.2, float("nan"), float("inf")])
    def test_invalid_sigma_fails_open(self, bad_sigma):
        band, reason = band_width(target_weight=0.15, sigma=bad_sigma)
        assert reason == "sigma_unavailable_abs_tol"
        assert band == pytest.approx(DEFAULT_ABS_TOL)

    def test_invalid_target_weight_fails_open(self):
        band, reason = band_width(target_weight=float("nan"), sigma=0.25)
        assert reason == "invalid_target_weight_abs_tol"
        assert band == pytest.approx(DEFAULT_ABS_TOL)

    def test_negative_target_weight_invalid(self):
        band, reason = band_width(target_weight=-0.1, sigma=0.25)
        assert reason == "invalid_target_weight_abs_tol"


class TestShouldRebalance:
    def test_inside_band_no_trigger(self):
        # band=0.02, dev=0.01 → 不触发
        d = should_rebalance(current_weight=0.16, target_weight=0.15, sigma=0.25)
        assert d.triggered is False
        assert d.deviation == pytest.approx(0.01)

    def test_outside_band_triggers(self):
        # band=0.02, dev=0.05 → 触发
        d = should_rebalance(current_weight=0.20, target_weight=0.15, sigma=0.25)
        assert d.triggered is True

    def test_just_below_band_no_trigger(self):
        # dev=0.019 略小于 band=0.02 → 不触发 (浮点边界语义: abs(dev) > band 才触发)
        d = should_rebalance(current_weight=0.169, target_weight=0.15, sigma=0.25)
        assert d.triggered is False

    def test_sigma_none_degrades_to_abs_tol(self):
        d = should_rebalance(current_weight=0.10, target_weight=0.15, sigma=None)
        assert d.triggered is True  # dev=0.05 > 0.02
        assert d.reason == "sigma_unavailable_abs_tol"

    def test_invalid_current_weight_fail_close(self):
        d = should_rebalance(current_weight=float("nan"), target_weight=0.15, sigma=0.25)
        assert d.triggered is True  # 数据坏了保守触发, 不静默躺平
        assert d.reason == "invalid_current_weight_fail_close"
        assert math.isnan(d.deviation)


# ───────────────────── 2. generate_rebalance_orders 集成 ─────────────────────


def _make_style_allocation(weights: dict[str, float]) -> dict:
    """按权重构造 style_allocation (amount 与权重自洽)."""
    total = 5_000_000.0
    return {style: {"amount": total * w, "weight": w, "codes": [f"{style}001"]} for style, w in weights.items()}


# 与 reo.TARGET_ALLOCATION 同键的简单目标
_TARGET = {"宽基": 0.15, "国债": 0.22}
_POSITIONS = {"宽基001": 100000, "国债001": 100000}
_PRICES = {"宽基001": 5.0, "国债001": 5.0}


class TestGenerateRebalanceOrdersBand:
    def test_small_deviation_filtered_with_volatility(self):
        alloc = _make_style_allocation({"宽基": 0.16, "国债": 0.22})
        vol = {"宽基": 0.05}  # 0.5*0.15*0.05 < 0.02 → band=0.02, dev=0.01 带内
        orders = reo.generate_rebalance_orders(alloc, _TARGET, _POSITIONS, _PRICES, volatility=vol)
        assert all(o["style"] != "宽基" for o in orders)

    def test_large_deviation_still_triggers_with_volatility(self):
        alloc = _make_style_allocation({"宽基": 0.25, "国债": 0.22})
        vol = {"宽基": 0.25}  # dev=0.10 > band=0.02 → 触发
        orders = reo.generate_rebalance_orders(alloc, _TARGET, _POSITIONS, _PRICES, volatility=vol)
        assert any(o["style"] == "宽基" for o in orders)

    def test_missing_style_sigma_uses_abs_tol(self):
        alloc = _make_style_allocation({"宽基": 0.16, "国债": 0.22})
        vol = {"国债": 0.05}  # 宽基无 sigma → abs_tol=0.02, dev=0.01 带内 → 过滤
        orders = reo.generate_rebalance_orders(alloc, _TARGET, _POSITIONS, _PRICES, volatility=vol)
        assert all(o["style"] != "宽基" for o in orders)

    def test_none_volatility_backward_compatible(self):
        """不传 volatility 时行为与旧版一致 (固定 2% 带宽语义)."""
        alloc_small = _make_style_allocation({"宽基": 0.16, "国债": 0.22})
        alloc_large = _make_style_allocation({"宽基": 0.25, "国债": 0.22})
        # dev=0.01 < 0.02 → 过滤
        assert reo.generate_rebalance_orders(alloc_small, _TARGET, _POSITIONS, _PRICES) == []
        # dev=0.10 > 0.02 → 触发 (宽基001: price=5, qty=100000, 可生成 SELL 单)
        orders = reo.generate_rebalance_orders(alloc_large, _TARGET, _POSITIONS, _PRICES)
        assert any(o["style"] == "宽基" for o in orders)


# ───────────────────── 3. _load_style_volatility ─────────────────────


class TestLoadStyleVolatility:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reo, "_PROJECT_ROOT", tmp_path)
        assert reo._load_style_volatility() is None

    def test_valid_file_returns_dict(self, tmp_path, monkeypatch):
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "style_volatility.json").write_text(json.dumps({"宽基": 0.25, "国债": 0.05}), encoding="utf-8")
        monkeypatch.setattr(reo, "_PROJECT_ROOT", tmp_path)
        vol = reo._load_style_volatility()
        assert vol == {"宽基": 0.25, "国债": 0.05}

    def test_broken_json_returns_none(self, tmp_path, monkeypatch):
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "style_volatility.json").write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(reo, "_PROJECT_ROOT", tmp_path)
        assert reo._load_style_volatility() is None

    def test_non_numeric_values_filtered(self, tmp_path, monkeypatch):
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "style_volatility.json").write_text(json.dumps({"宽基": 0.25, "国债": "high"}), encoding="utf-8")
        monkeypatch.setattr(reo, "_PROJECT_ROOT", tmp_path)
        vol = reo._load_style_volatility()
        assert vol == {"宽基": 0.25}
