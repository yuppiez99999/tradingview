# -*- coding: utf-8 -*-
"""greek_exposure_dashboard 单元测试 — Greeks 暴露监控面板"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from utils.greek_exposure_dashboard import (
    GreekSnapshot,
    GreekDashboard,
    load_positions,
    _signal_level,
    _build_recommendations,
    compute_dashboard,
    dashboard_to_dict,
)


class TestGreekSnapshot:
    def test_defaults(self):
        s = GreekSnapshot()
        assert s.delta == 0.0
        assert s.gamma == 0.0
        assert s.theta == 0.0
        assert s.vega == 0.0
        assert s.rho == 0.0

    def test_custom(self):
        s = GreekSnapshot(delta=100, gamma=-5, theta=-200, vega=1000, rho=50)
        assert s.delta == 100
        assert s.gamma == -5
        assert s.theta == -200
        assert s.vega == 1000
        assert s.rho == 50


class TestGreekDashboard:
    def test_defaults(self):
        d = GreekDashboard()
        assert d.snapshot == GreekSnapshot()
        assert d.targets == {}
        assert d.rebalance_signals == {}
        assert d.signal_levels == {}
        assert d.recommendations == []
        assert d.per_position == []


class TestLoadPositions:
    def test_nonexistent_path(self):
        positions, prices = load_positions("/nonexistent/path.json")
        assert positions == {}
        assert prices == {}

    def test_valid_file(self, tmp_path):
        data = {
            "positions": {
                "A": {"code": "600519", "name": "贵州茅台", "phase1_shares": 100,
                      "est_price": 1800.0, "beta": 1.2, "delta": 1.0},
                "B": {"code": "000001", "name": "平安银行", "total_shares": 200,
                      "est_price": 10.0},
            }
        }
        f = tmp_path / "positions.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions, prices = load_positions(f)
        assert "600519" in positions
        assert positions["600519"]["shares"] == 100
        assert positions["600519"]["est_price"] == 1800.0
        assert positions["600519"]["name"] == "贵州茅台"
        assert positions["600519"]["beta"] == 1.2
        assert prices["600519"] == 1800.0
        assert "000001" in positions
        assert positions["000001"]["shares"] == 200

    def test_shares_fallback(self, tmp_path):
        data = {"positions": {"A": {"code": "001", "shares": 50, "est_price": 5.0}}}
        f = tmp_path / "p.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions, _ = load_positions(f)
        assert positions["001"]["shares"] == 50

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid json", encoding="utf-8")
        positions, prices = load_positions(f)
        assert positions == {}
        assert prices == {}

    def test_no_code_skipped(self, tmp_path):
        data = {"positions": {"A": {"shares": 100, "est_price": 10.0}}}
        f = tmp_path / "p.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions, _ = load_positions(f)
        assert positions == {}

    def test_zero_qty_skipped(self, tmp_path):
        data = {"positions": {"A": {"code": "001", "phase1_shares": 0, "est_price": 10.0}}}
        f = tmp_path / "p.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions, _ = load_positions(f)
        assert positions == {}

    def test_default_beta_delta(self, tmp_path):
        data = {"positions": {"A": {"code": "001", "shares": 10, "est_price": 5.0}}}
        f = tmp_path / "p.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions, _ = load_positions(f)
        assert positions["001"]["beta"] == 1.0
        assert positions["001"]["delta"] == 1.0
        assert positions["001"]["gamma"] == 0.0


class TestSignalLevel:
    def test_ok_exact_match(self):
        assert _signal_level(100, 100) == "OK"

    def test_ok_within_tolerance(self):
        assert _signal_level(104, 100, tolerance=0.05) == "OK"

    def test_warn(self):
        assert _signal_level(108, 100, tolerance=0.05) == "WARN"

    def test_critical(self):
        assert _signal_level(200, 100, tolerance=0.05) == "CRITICAL"

    def test_zero_target(self):
        assert _signal_level(0, 0) == "OK"

    def test_negative_value(self):
        assert _signal_level(-100, -100) == "OK"

    def test_small_values(self):
        assert _signal_level(0.01, 0.0) == "OK"


class TestBuildRecommendations:
    def test_no_rebalance_needed(self):
        exposure = GreekSnapshot(delta=0, gamma=0, theta=0, vega=0)
        signals = {"delta_rebalance": False, "gamma_rebalance": False,
                   "vega_rebalance": False, "theta_rebalance": False}
        levels = {"delta": "OK", "gamma": "OK", "vega": "OK", "theta": "OK"}
        recs = _build_recommendations(exposure, signals, levels)
        assert len(recs) == 1
        assert "无需再平衡" in recs[0]

    def test_delta_rebalance(self):
        exposure = GreekSnapshot(delta=50000)
        signals = {"delta_rebalance": True}
        levels = {"delta": "WARN"}
        recs = _build_recommendations(exposure, signals, levels)
        assert any("Delta" in r for r in recs)

    def test_gamma_rebalance(self):
        exposure = GreekSnapshot(gamma=1000)
        signals = {"gamma_rebalance": True}
        levels = {"gamma": "CRITICAL"}
        recs = _build_recommendations(exposure, signals, levels)
        assert any("Gamma" in r for r in recs)

    def test_vega_rebalance(self):
        exposure = GreekSnapshot(vega=80000)
        signals = {"vega_rebalance": True}
        levels = {"vega": "WARN"}
        recs = _build_recommendations(exposure, signals, levels)
        assert any("Vega" in r for r in recs)

    def test_theta_rebalance(self):
        exposure = GreekSnapshot(theta=-10000)
        signals = {"theta_rebalance": True}
        levels = {"theta": "CRITICAL"}
        recs = _build_recommendations(exposure, signals, levels)
        assert any("Theta" in r for r in recs)

    def test_negative_delta_direction(self):
        exposure = GreekSnapshot(delta=-50000)
        signals = {"delta_rebalance": True}
        levels = {"delta": "WARN"}
        recs = _build_recommendations(exposure, signals, levels)
        assert any("买入" in r for r in recs)


class TestComputeDashboard:
    def test_no_positions_file(self):
        dashboard = compute_dashboard("/nonexistent/path.json")
        assert "无持仓数据" in dashboard.recommendations[0]

    def test_import_error(self):
        with patch("utils.greek_hedge_manager.GreekHedgeManager", create=True) as mock:
            with patch("builtins.__import__", side_effect=ImportError):
                dashboard = compute_dashboard()
        assert any("不可用" in r for r in dashboard.recommendations)


class TestDashboardToDict:
    def test_conversion(self):
        d = GreekDashboard()
        d.snapshot = GreekSnapshot(delta=100.123, gamma=-5.456)
        d.targets = {"target_delta": 0}
        d.recommendations = ["test rec"]
        result = dashboard_to_dict(d)
        assert result["snapshot"]["delta"] == 100.12
        assert result["snapshot"]["gamma"] == -5.46
        assert result["targets"] == {"target_delta": 0}
        assert result["recommendations"] == ["test rec"]

    def test_empty_dashboard(self):
        d = GreekDashboard()
        result = dashboard_to_dict(d)
        assert result["snapshot"]["delta"] == 0.0
        assert result["recommendations"] == []
        assert result["per_position_top10"] == []