# -*- coding: utf-8 -*-
"""portfolio_optimizer 单元测试 — 组合优化器全分支覆盖.

被测模块: utils/portfolio_optimizer.py
覆盖目标: >=90%
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.portfolio_optimizer import PortfolioOptimizer  # noqa: E402


# ============================================================
# __init__
# ============================================================

class TestInit:
    def test_default_signals_dir(self):
        opt = PortfolioOptimizer()
        assert opt.signals_dir.exists()

    def test_custom_signals_dir(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path / "signals"))
        assert opt.signals_dir == tmp_path / "signals"

    def test_default_alpha(self):
        assert PortfolioOptimizer.DEFAULT_ALPHA == 0.05

    def test_max_exposure(self):
        assert PortfolioOptimizer.MAX_TOTAL_EXPOSURE == 1.5


# ============================================================
# load_factor_signals
# ============================================================

class TestLoadFactorSignals:
    def test_empty_trade_date(self):
        opt = PortfolioOptimizer()
        assert opt.load_factor_signals("") == {}

    def test_file_not_found(self):
        opt = PortfolioOptimizer()
        assert opt.load_factor_signals("1999-01-01") == {}

    def test_valid_file(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {
            "trade_date": "2026-08-17",
            "signals": {
                "600519": {"signal": 0.8},
                "000001": {"signal": -0.3},
            },
        }
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        signals = opt.load_factor_signals("2026-08-17")
        assert signals["600519"] == 0.8
        assert signals["000001"] == -0.3

    def test_stale_date(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {"trade_date": "2026-08-10", "signals": {"X": {"signal": 1.0}}}
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert opt.load_factor_signals("2026-08-17") == {}

    def test_empty_signals(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {"trade_date": "2026-08-17", "signals": {}}
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert opt.load_factor_signals("2026-08-17") == {}

    def test_non_dict_info(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {"trade_date": "2026-08-17", "signals": {"X": 0.5, "Y": {"signal": 0.3}}}
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        signals = opt.load_factor_signals("2026-08-17")
        assert "X" not in signals
        assert signals["Y"] == 0.3

    def test_none_signal(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {"trade_date": "2026-08-17", "signals": {"X": {"signal": None}, "Y": {"signal": 0.5}}}
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        signals = opt.load_factor_signals("2026-08-17")
        assert "X" not in signals
        assert signals["Y"] == 0.5

    def test_non_finite_signal(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        data = {"trade_date": "2026-08-17", "signals": {"X": {"signal": float("nan")}, "Y": {"signal": 0.5}}}
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        signals = opt.load_factor_signals("2026-08-17")
        assert "X" not in signals

    def test_corrupt_file(self, tmp_path):
        opt = PortfolioOptimizer(signals_dir=str(tmp_path))
        path = tmp_path / "pipeline_factor_signals_2026-08-17.json"
        path.write_text("not json", encoding="utf-8")
        assert opt.load_factor_signals("2026-08-17") == {}


# ============================================================
# adjust_target_weights
# ============================================================

class TestAdjustTargetWeights:
    def test_basic_adjustment(self):
        opt = PortfolioOptimizer()
        base = {"A": 0.10, "B": 0.05, "C": 0.08}
        signals = {"A": 0.5, "B": -0.3, "C": 0.2}
        adjusted = opt.adjust_target_weights(base, signals, alpha=0.05)
        assert len(adjusted) == 3
        total_base = sum(abs(w) for w in base.values())
        total_adj = sum(abs(w) for w in adjusted.values())
        assert total_adj == pytest.approx(total_base, rel=1e-6)

    def test_empty_base(self):
        opt = PortfolioOptimizer()
        assert opt.adjust_target_weights({}, {"A": 0.5}) == {}

    def test_empty_signals(self):
        opt = PortfolioOptimizer()
        base = {"A": 0.1, "B": 0.2}
        result = opt.adjust_target_weights(base, {})
        assert result == base

    def test_zero_alpha(self):
        opt = PortfolioOptimizer()
        base = {"A": 0.1, "B": 0.2}
        result = opt.adjust_target_weights(base, {"A": 0.5}, alpha=0.0)
        assert result == base

    def test_default_alpha(self):
        opt = PortfolioOptimizer()
        base = {"A": 0.1, "B": 0.2}
        adjusted = opt.adjust_target_weights(base, {"A": 1.0, "B": -1.0})
        assert abs(adjusted["A"] - base["A"]) > 1e-6 or abs(adjusted["B"] - base["B"]) > 1e-6

    def test_missing_signal_symbol(self):
        opt = PortfolioOptimizer()
        base = {"A": 0.1, "B": 0.2}
        adjusted = opt.adjust_target_weights(base, {"A": 0.5}, alpha=0.05)
        assert "B" in adjusted


# ============================================================
# apply_risk_management
# ============================================================

class TestApplyRiskManagement:
    def test_empty_weights(self):
        opt = PortfolioOptimizer()
        weights, stats = opt.apply_risk_management({}, [0.01, 0.02])
        assert weights == {}
        assert "error" in stats

    def test_insufficient_pnl(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.1}
        result, stats = opt.apply_risk_management(weights, [0.01])
        assert result == weights
        assert stats["note"] == "insufficient_pnl_history"

    def test_empty_pnl(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.1}
        result, stats = opt.apply_risk_management(weights, [])
        assert result == weights

    def test_vol_scaling(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.5, "B": 0.5}
        pnl = [0.01] * 20 + [-0.01] * 20
        result, stats = opt.apply_risk_management(weights, pnl)
        assert stats["vol_scaler"] > 0
        assert stats["combined_scaler"] > 0

    def test_drawdown_derisk(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.5, "B": 0.5}
        pnl = [0.01] * 10 + [-0.03] * 10
        result, stats = opt.apply_risk_management(weights, pnl)
        assert stats["current_dd"] > 0

    def test_exposure_cap(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.5, "B": 0.5}
        pnl = [0.001, -0.0005, 0.0008, -0.0003, 0.0006] * 6
        result, stats = opt.apply_risk_management(weights, pnl)
        assert stats["scaled_total_exposure"] <= 1.5 + 1e-9

    def test_custom_config(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.5, "B": 0.5}
        pnl = [0.01] * 20 + [-0.01] * 20
        result, stats = opt.apply_risk_management(
            weights, pnl, config={"target_vol": 0.10, "vol_lookback": 10}
        )
        assert stats["target_vol"] == 0.10
        assert stats["vol_lookback"] == 10

    def test_lookahead_bias_fixed(self):
        opt = PortfolioOptimizer()
        weights = {"A": 1.0}
        pnl = [0.01] * 5 + [-0.05]
        _, stats = opt.apply_risk_management(weights, pnl)
        assert stats["lookahead_bias_fixed"] is True
        assert stats["dd_pnl_used"] == "yesterday_only"

    def test_zero_volatility(self):
        opt = PortfolioOptimizer()
        weights = {"A": 0.5, "B": 0.5}
        pnl = [0.0] * 30
        result, stats = opt.apply_risk_management(weights, pnl)
        assert stats["vol_scaler"] == 1.0

    def test_derisk_triggered(self):
        opt = PortfolioOptimizer()
        weights = {"A": 1.0}
        pnl = [0.02] * 10 + [-0.05] * 5
        result, stats = opt.apply_risk_management(weights, pnl)
        if stats["current_dd"] > 0.05:
            assert stats["derisk_triggered"] is True
            assert stats["dd_scaler"] == 0.5