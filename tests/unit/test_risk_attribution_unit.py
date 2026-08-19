"""risk_attribution 单元测试 — 风险归因面板全覆盖.

被测模块: utils/risk_attribution.py
覆盖目标: >=90%
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_attribution import (  # noqa: E402
    _aggregate,
    _calc_concentration,
    _calc_hedge_residual,
    _to_pct_map,
    attribution_to_dict,
    compute_attribution,
    load_hedge_positions,
    load_positions,
)

# ============================================================
# _calc_concentration
# ============================================================

class TestConcentration:
    def test_empty(self):
        r = _calc_concentration([])
        assert r["hhi"] == 0.0

    def test_equal_weights(self):
        r = _calc_concentration([100, 100, 100])
        assert r["hhi"] == pytest.approx(1/3, abs=0.01)
        assert r["top1"] == pytest.approx(1/3, abs=0.01)

    def test_concentrated(self):
        r = _calc_concentration([90, 5, 5])
        assert r["top1"] > 0.8
        assert r["hhi"] > 0.7

    def test_zero_total(self):
        r = _calc_concentration([0, 0, 0])
        assert r["hhi"] == 0.0

    def test_effective_n(self):
        r = _calc_concentration([100, 100, 100, 100])
        assert r["effective_n"] == pytest.approx(4.0, abs=0.1)


# ============================================================
# _aggregate
# ============================================================

class TestAggregate:
    def test_basic(self):
        r = _aggregate([("科技", 100), ("金融", 200), ("科技", 50)])
        assert r["科技"] == 150
        assert r["金融"] == 200

    def test_none_key(self):
        r = _aggregate([(None, 100)])
        assert r["其他"] == 100

    def test_empty(self):
        assert _aggregate([]) == {}


# ============================================================
# _to_pct_map
# ============================================================

class TestToPctMap:
    def test_basic(self):
        r = _to_pct_map({"A": 100, "B": 300}, 400)
        assert r["A"] == 0.25
        assert r["B"] == 0.75

    def test_zero_total(self):
        r = _to_pct_map({"A": 100}, 0)
        assert r["A"] == 0.0


# ============================================================
# _calc_hedge_residual
# ============================================================

class TestHedgeResidual:
    def test_no_hedge(self):
        positions = [{"amount": 100, "beta": 1.0}, {"amount": 200, "beta": 0.8}]
        r = _calc_hedge_residual(positions, {})
        assert r["portfolio_beta"] > 0
        assert r["futures_contracts"] == 0

    def test_with_futures(self):
        positions = [{"amount": 1000, "beta": 1.0}]
        hedge = {"IC": {"target_contracts": 2, "target_beta_reduction": 0.5}}
        r = _calc_hedge_residual(positions, hedge)
        assert r["futures_contracts"] == 2
        assert r["residual_beta"] < r["portfolio_beta"]

    def test_with_options(self):
        positions = [{"amount": 1000, "beta": 1.0}]
        hedge = {"PUT": {"is_option": True, "target_contracts": 1, "premium_budget": 5000, "estimated_notional": 100000}}
        r = _calc_hedge_residual(positions, hedge)
        assert r["options_contracts"] == 1
        assert r["tail_risk_coverage_pct"] > 0


# ============================================================
# load_positions / load_hedge_positions
# ============================================================

class TestLoadPositions:
    def test_missing_file(self, tmp_path):
        assert load_positions(tmp_path / "nonexistent.json") == []

    def test_valid_file(self, tmp_path):
        data = {"positions": {"A": {"code": "A", "amount": 100, "est_price": 10, "beta": 1.2}}}
        f = tmp_path / "positions.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        positions = load_positions(f)
        assert len(positions) == 1
        assert positions[0]["code"] == "A"

    def test_hedge_missing(self, tmp_path):
        assert load_hedge_positions(tmp_path / "nonexistent.json") == {}

    def test_hedge_valid(self, tmp_path):
        data = {"hedge_positions": {"IC": {"target_contracts": 2}}}
        f = tmp_path / "positions.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        hedge = load_hedge_positions(f)
        assert "IC" in hedge


# ============================================================
# compute_attribution
# ============================================================

class TestComputeAttribution:
    def _make_positions_file(self, tmp_path):
        data = {
            "positions": {
                "A": {"code": "A", "amount": 500, "est_price": 10, "sector": "科技", "style": "成长", "beta": 1.2},
                "B": {"code": "B", "amount": 300, "est_price": 20, "sector": "金融", "style": "价值", "beta": 0.8},
                "C": {"code": "C", "amount": 200, "est_price": 30, "sector": "科技", "style": "成长", "beta": 1.0},
            },
            "hedge_positions": {"IC": {"target_contracts": 1, "target_beta_reduction": 0.3}},
        }
        f = tmp_path / "positions.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def test_basic(self, tmp_path):
        f = self._make_positions_file(tmp_path)
        attr = compute_attribution(f)
        assert attr.total_value == 1000
        assert "科技" in attr.by_sector
        assert "金融" in attr.by_sector

    def test_concentration(self, tmp_path):
        f = self._make_positions_file(tmp_path)
        attr = compute_attribution(f)
        assert "hhi" in attr.concentration
        assert "top1" in attr.concentration

    def test_hedge_residual(self, tmp_path):
        f = self._make_positions_file(tmp_path)
        attr = compute_attribution(f)
        assert "portfolio_beta" in attr.hedge_residual
        assert "residual_beta" in attr.hedge_residual

    def test_no_positions(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text(json.dumps({"positions": {}}), encoding="utf-8")
        attr = compute_attribution(f)
        assert "无持仓数据" in attr.warnings

    def test_missing_file(self, tmp_path):
        attr = compute_attribution(tmp_path / "nonexistent.json")
        assert "无持仓数据" in attr.warnings


# ============================================================
# attribution_to_dict
# ============================================================

class TestAttributionToDict:
    def test_basic(self, tmp_path):
        data = {"positions": {"A": {"amount": 100, "sector": "科技", "beta": 1.0}}}
        f = tmp_path / "positions.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        attr = compute_attribution(f)
        d = attribution_to_dict(attr)
        assert "total_value" in d
        assert "by_sector" in d
        assert "by_sector_pct" in d
        assert "concentration" in d
        assert "hedge_residual" in d
