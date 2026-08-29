"""test_v10_config_loader_unit.py — v10.0 投资计划配置加载器单元测试

覆盖要点:
    - load (文件不存在/正常/解析失败/缓存)
    - get_allocation / get_total_capital / get_max_leverage
    - get_current_phase (各年度阶段)
    - get_stock_positions / get_etf_positions
    - get_risk_automation / get_rebalance_config
    - summary
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from utils.v10_config_loader import V10ConfigLoader


def _write_config(tmp_path: Path) -> Path:
    cfg = {
        "meta": {
            "total_capital": 5_000_000,
            "max_leverage": 1.5,
            "target_annual_return": 0.085,
            "target_max_drawdown": 0.15,
            "allocation": {"stock": 2_000_000, "etf": 1_000_000},
        },
        "stock_long_account": {"positions": [{"code": "600519", "weight": 0.1}]},
        "etf_account": {"positions": [{"code": "510300", "weight": 0.2}]},
        "macro_hedge_account": {"positions": []},
        "quant_neutral_account": {},
        "options_account": {},
        "cash_management": {},
        "execution_plan": {
            "phase_2026": {
                "name": "建仓期",
                "period": "2026-01-01 to 2026-12-31",
                "target_return": 0.08,
                "max_drawdown": 0.08,
                "daily_build_limit": 200_000,
            },
            "phase_2027": {"name": "成长期", "daily_build_limit": 300_000},
        },
        "daily_schedule": {"07:00": {"stage": "pre", "action": "check"}},
        "risk_automation": {
            "drawdown_control": {"max_dd": 0.15},
            "var_monitoring": {"confidence": 0.99},
            "concentration_limits": {"max_single": 0.1},
        },
        "dynamic_rebalance": {"threshold": 0.05},
    }
    p = tmp_path / "v10.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


# ============================================================
# load
# ============================================================


class TestLoad:
    @pytest.mark.unit
    def test_file_not_exists(self, tmp_path):
        loader = V10ConfigLoader(config_path=tmp_path / "nope.json")
        assert loader.load() == {}

    @pytest.mark.unit
    def test_normal_load(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        cfg = loader.load()
        assert "meta" in cfg
        assert cfg["meta"]["total_capital"] == 5_000_000

    @pytest.mark.unit
    def test_cache(self, tmp_path):
        """第二次 load 返回缓存 (同一对象)"""
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        cfg1 = loader.load()
        cfg2 = loader.load()
        assert cfg1 is cfg2  # 同一对象

    @pytest.mark.unit
    def test_malformed_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        loader = V10ConfigLoader(config_path=p)
        assert loader.load() == {}


# ============================================================
# getter
# ============================================================


class TestGetters:
    @pytest.mark.unit
    def test_allocation(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        alloc = loader.get_allocation()
        assert alloc["stock"] == 2_000_000
        assert alloc["etf"] == 1_000_000

    @pytest.mark.unit
    def test_total_capital(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        assert loader.get_total_capital() == 5_000_000

    @pytest.mark.unit
    def test_max_leverage(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        assert loader.get_max_leverage() == 1.5

    @pytest.mark.unit
    def test_target_annual_return(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        assert loader.get_target_annual_return() == 0.085

    @pytest.mark.unit
    def test_target_max_drawdown(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        assert loader.get_target_max_drawdown() == 0.15

    @pytest.mark.unit
    def test_stock_positions(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        positions = loader.get_stock_positions()
        assert len(positions) == 1
        assert positions[0]["code"] == "600519"

    @pytest.mark.unit
    def test_etf_positions(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        positions = loader.get_etf_positions()
        assert len(positions) == 1

    @pytest.mark.unit
    def test_empty_config_defaults(self, tmp_path):
        """空配置 → 默认值"""
        loader = V10ConfigLoader(config_path=tmp_path / "nope.json")
        assert loader.get_total_capital() == 5_000_000
        assert loader.get_max_leverage() == 1.5
        assert loader.get_target_annual_return() == 0.085
        assert loader.get_target_max_drawdown() == 0.15
        assert loader.get_allocation() == {}
        assert loader.get_stock_positions() == []


# ============================================================
# get_current_phase
# ============================================================


class TestGetCurrentPhase:
    @pytest.mark.unit
    def test_phase_2026(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        phase = loader.get_current_phase(date(2026, 7, 15))
        assert phase["phase_key"] == "phase_2026"
        assert phase["name"] == "建仓期"

    @pytest.mark.unit
    def test_phase_2027(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        phase = loader.get_current_phase(date(2027, 6, 1))
        assert phase["phase_key"] == "phase_2027"
        assert phase["name"] == "成长期"

    @pytest.mark.unit
    def test_out_of_range_defaults_2026(self, tmp_path):
        """2025 年 → 默认返回 phase_2026"""
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        phase = loader.get_current_phase(date(2025, 1, 1))
        assert phase["phase_key"] == "phase_2026"

    @pytest.mark.unit
    def test_daily_build_limit(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        assert loader.get_daily_build_limit() == 200_000


# ============================================================
# risk / rebalance
# ============================================================


class TestRiskConfig:
    @pytest.mark.unit
    def test_risk_automation(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        risk = loader.get_risk_automation()
        assert "drawdown_control" in risk

    @pytest.mark.unit
    def test_drawdown_config(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        dd = loader.get_drawdown_config()
        assert dd["max_dd"] == 0.15

    @pytest.mark.unit
    def test_var_config(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        var = loader.get_var_config()
        assert var["confidence"] == 0.99

    @pytest.mark.unit
    def test_rebalance_config(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        reb = loader.get_rebalance_config()
        assert reb["threshold"] == 0.05


# ============================================================
# summary
# ============================================================


class TestSummary:
    @pytest.mark.unit
    def test_summary_string(self, tmp_path):
        p = _write_config(tmp_path)
        loader = V10ConfigLoader(config_path=p)
        s = loader.summary()
        assert "v10.0" in s
        assert "5,000,000" in s
        assert "建仓期" in s
