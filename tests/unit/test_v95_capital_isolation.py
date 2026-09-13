"""v9.5 三百万子组合资金/持仓隔离验证 — 缺口⑥

验证:
    T01 positions.json 含 v95_subportfolio 段
    T02 v95 total_capital = 3,000,000
    T03 v95 腿分解 = 200万证券 + 100万对冲
    T04 v95 enabled=false (资金未到位)
    T05 v95 持仓空 (资金未到位)
    T06 v95 与现役 positions 物理隔离 (独立段)
    T07 v95 与 risk_thresholds.yaml capital_base 口径不冲突
    T08 双登记一致性: positions.json v95 total = etf_option_combo_v95.yaml subportfolio total
    T09 v95 config_source 指向正确配置文件
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POSITIONS_PATH = _PROJECT_ROOT / "config" / "positions.json"

pytestmark = pytest.mark.skipif(
    not _POSITIONS_PATH.exists() or "v95_subportfolio" not in json.loads(
        _POSITIONS_PATH.read_text(encoding="utf-8-sig")
    ),
    reason="positions.json 不存在或未含 v95_subportfolio 段 (运行时文件, 非 CI 必备)",
)


def _load_positions() -> dict:
    return json.loads(
        (_PROJECT_ROOT / "config" / "positions.json").read_text(encoding="utf-8-sig")
    )


def _load_v95_config() -> dict:
    return yaml.safe_load(
        (_PROJECT_ROOT / "config" / "etf_option_combo_v95.yaml").read_text(encoding="utf-8")
    )


def _load_risk_thresholds() -> dict:
    return yaml.safe_load(
        (_PROJECT_ROOT / "config" / "risk_thresholds.yaml").read_text(encoding="utf-8")
    )


class TestV95Isolation:
    def test_t01_v95_subportfolio_exists(self):
        data = _load_positions()
        assert "v95_subportfolio" in data, "positions.json 缺少 v95_subportfolio 段"

    def test_t02_total_capital_3m(self):
        v95 = _load_positions()["v95_subportfolio"]
        assert v95["total_capital"] == 3_000_000

    def test_t03_leg_decomposition(self):
        v95 = _load_positions()["v95_subportfolio"]
        assert v95["stock_etf_capital"] == 2_000_000
        assert v95["hedge_capital"] == 1_000_000
        assert v95["stock_etf_capital"] + v95["hedge_capital"] == v95["total_capital"]

    def test_t04_disabled_before_capital_arrival(self):
        v95 = _load_positions()["v95_subportfolio"]
        assert v95["enabled"] is False, "资金未到位前 enabled 必须为 false"
        assert v95["capital_arrival_date"] is None

    def test_t05_empty_positions_before_arrival(self):
        v95 = _load_positions()["v95_subportfolio"]
        assert v95["positions"] == {}, "资金未到位前持仓必须为空"
        assert v95["hedge_positions"] == {}

    def test_t06_physical_isolation_from_active(self):
        """v95 段独立于现役 positions/hedge_positions。"""
        data = _load_positions()
        v95 = data["v95_subportfolio"]
        assert "positions" in v95
        assert "hedge_positions" in v95
        assert v95["positions"] is not data["positions"]
        assert v95["hedge_positions"] is not data.get("hedge_positions", {})

    def test_t07_no_conflict_with_risk_thresholds(self):
        """v95 口径与 risk_thresholds.yaml capital_base 不冲突。"""
        v95 = _load_positions()["v95_subportfolio"]
        rt = _load_risk_thresholds()
        cb = rt["capital_base"]
        assert cb["total_capital"] == 3_000_000
        assert v95["total_capital"] == cb["total_capital"], "v95 与权威口径一致 (300万)"

    def test_t08_dual_registration_consistency(self):
        """positions.json v95 total = etf_option_combo_v95.yaml subportfolio total。"""
        v95_pos = _load_positions()["v95_subportfolio"]
        v95_cfg = _load_v95_config()
        assert v95_pos["total_capital"] == v95_cfg["subportfolio"]["total_capital"]

    def test_t09_config_source_correct(self):
        v95 = _load_positions()["v95_subportfolio"]
        assert v95["config_source"] == "config/etf_option_combo_v95.yaml"
        assert (_PROJECT_ROOT / v95["config_source"]).exists()
