"""GammaEngine 单元测试.

被测模块: utils/gamma_engine.py
覆盖目标: >=85%
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import utils.gamma_engine as gamma_engine_mod  # noqa: E402
from utils.gamma_engine import GammaEngine  # noqa: E402


def _make_config(put_options=None):
    if put_options is None:
        put_options = [
            {"instrument": "ETF_put", "premium_budget": 300000, "strike": "OTM_5%"},
            {"instrument": "510050_put", "premium_budget": 120000, "strike": "OTM_5%"},
        ]
    return {"hedge": {"gamma_vega_engine": {"put_options": put_options}}}


class GammaEngineTest:
    """GammaEngine 单元测试."""

    # ------ _load_config 显式路径 ------
    def test_load_config_explicit_path(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        assert "put_options" in engine.config
        assert len(engine.config["put_options"]) == 2

    def test_load_config_missing_file(self, tmp_path):
        engine = GammaEngine(config_path=tmp_path / "nonexistent.yaml")
        assert engine.config == {}

    def test_load_config_invalid_yaml(self, tmp_path):
        cfg_path = tmp_path / "bad.yaml"
        cfg_path.write_text("- list\n- not dict", encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        assert engine.config == {}

    def test_load_config_no_hedge_section(self, tmp_path):
        cfg_path = tmp_path / "empty.yaml"
        cfg_path.write_text(yaml.dump({"other": {}}), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        assert engine.config == {}

    # ------ _load_config ConfigManager 回退（默认路径）------
    def test_load_config_default_path_via_config_manager(self):
        # 默认路径走 ConfigManager，应能加载到 put_options
        engine = GammaEngine()
        assert "put_options" in engine.config
        assert len(engine.config["put_options"]) >= 1

    # ------ execute_tail_hedge ------
    def test_execute_tail_hedge_budget_invalid(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("ma60_breakdown", 0)
        assert result["executed"] is False
        assert result["reason"] == "budget_invalid"

    def test_execute_tail_hedge_negative_budget(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("iv_low_percentile", -100)
        assert result["executed"] is False

    def test_execute_tail_hedge_normal(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("ma60_breakdown", 50000)
        assert result["executed"] is True
        assert result["budget"] == 50000
        assert result["trigger_type"] == "ma60_breakdown"
        assert len(result["orders"]) >= 1
        assert all(o["direction"] == "BUY" for o in result["orders"])

    def test_execute_tail_hedge_budget_exhaustion_break(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("ma60_breakdown", 100)
        assert result["executed"] is True
        # 预算很小，订单预算之和 <= 100
        total = sum(o["budget"] for o in result["orders"])
        assert total <= 100

    def test_execute_tail_hedge_empty_put_options(self, tmp_path):
        cfg = _make_config(put_options=[])
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("ma60_breakdown", 50000)
        assert result["executed"] is True
        assert result["orders"] == []

    def test_execute_tail_hedge_zero_premium_budget_break(self, tmp_path):
        # total_budget_cfg <= 0 → break
        cfg = _make_config(put_options=[{"instrument": "X", "premium_budget": 0}])
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        result = engine.execute_tail_hedge("ma60_breakdown", 50000)
        assert result["executed"] is True
        assert result["orders"] == []

    # ------ _log_trigger + get_trigger_history ------
    def test_log_trigger_writes(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        info = {"timestamp": datetime.now().isoformat(), "trigger_type": "test", "budget": 100}
        engine._log_trigger(info)
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["trigger_type"] == "test"

    def test_get_trigger_history_file_not_exists(self, tmp_path, monkeypatch):
        log_path = tmp_path / "nonexistent.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        assert engine.get_trigger_history() == []

    def test_get_trigger_history_normal(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        now = datetime.now()
        records = [
            {"timestamp": now.isoformat(), "trigger_type": "ma60", "budget": 50000},
            {"timestamp": (now - timedelta(days=40)).isoformat(), "trigger_type": "iv_low", "budget": 100000},
        ]
        log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        # days=30 → 只返回最近30天
        history = engine.get_trigger_history(days=30)
        assert len(history) == 1
        assert history[0]["trigger_type"] == "ma60"
        # days=60 → 返回全部
        history_all = engine.get_trigger_history(days=60)
        assert len(history_all) == 2

    def test_get_trigger_history_bad_lines_skipped(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        now = datetime.now()
        content = (
            json.dumps({"timestamp": now.isoformat(), "trigger_type": "good"}) + "\n"
            + "not a json line\n"
            + json.dumps({"no_timestamp": True}) + "\n"
        )
        log_path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        history = engine.get_trigger_history(days=30)
        assert len(history) == 1
        assert history[0]["trigger_type"] == "good"

    # ------ monitor ------
    def test_monitor_no_trigger(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        prices = np.linspace(4000, 4200, 70)  # 收盘价递增，current > ma60
        df = pd.DataFrame({"close": prices})
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.5})
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["triggered"] is False
        assert result["ma60_broken"] is False
        assert result["iv_low"] is False
        assert result["budget"] == 0

    def test_monitor_ma60_breakdown_trigger(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        prices = np.linspace(4000, 4200, 69).tolist() + [3900]  # 最后一价 3900 < ma60
        df = pd.DataFrame({"close": prices})
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.5})
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["triggered"] is True
        assert result["ma60_broken"] is True
        assert result["trigger_type"] == "ma60_breakdown"
        assert result["budget"] == int(5000000 * 0.01)
        assert log_path.exists()  # 触发日志已写

    def test_monitor_iv_low_trigger(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        prices = np.linspace(4000, 4200, 70)  # 价格上行，不破 ma60
        df = pd.DataFrame({"close": prices})
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.05})  # iv < 0.10
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["triggered"] is True
        assert result["iv_low"] is True
        assert result["trigger_type"] == "iv_low_percentile"
        assert result["budget"] == int(5000000 * 0.02)

    def test_monitor_double_trigger_takes_larger_budget(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        prices = np.linspace(4000, 4200, 69).tolist() + [3900]  # 跌破 ma60
        df = pd.DataFrame({"close": prices})
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.05})  # iv 也低
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["triggered"] is True
        assert result["ma60_broken"] is True
        assert result["iv_low"] is True
        # 双触发取较大预算 = 2% = 100000
        assert result["budget"] == max(int(5000000 * 0.01), int(5000000 * 0.02))

    def test_monitor_wind_exception_no_trigger(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        mock_mod.wind_get_index_data = MagicMock(side_effect=RuntimeError("fail"))
        mock_mod.wind_get_option_iv = MagicMock(return_value=None)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["triggered"] is False
        assert result["ma60_value"] is None

    def test_monitor_short_data_no_ma60_trigger(self, tmp_path, monkeypatch):
        log_path = tmp_path / "triggers.jsonl"
        monkeypatch.setattr(gamma_engine_mod, "TRIGGER_LOG", log_path)
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)

        mock_mod = MagicMock()
        df = pd.DataFrame({"close": np.linspace(4000, 4200, 50)})  # len=50 < 60
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.5})
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            result = engine.monitor()
        assert result["ma60_broken"] is False
        assert result["triggered"] is False

    # ------ _get_market_ma60 / _get_market_iv_percentile ------
    def test_get_market_ma60_via_wind(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        df = pd.DataFrame({"close": np.linspace(4000, 4200, 70)})
        mock_mod.wind_get_index_data = MagicMock(return_value=df)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            ma60 = engine._get_market_ma60()
        assert ma60 is not None
        assert 4000 < ma60 < 4200

    def test_get_market_ma60_all_fail_returns_none(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        mock_wind = MagicMock()
        mock_wind.wind_get_index_data = MagicMock(side_effect=ConnectionError("fail"))
        mock_requests = MagicMock()
        mock_requests.get = MagicMock(side_effect=ConnectionError("http fail"))
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_wind, "requests": mock_requests}):
            ma60 = engine._get_market_ma60()
        assert ma60 is None

    def test_get_market_iv_percentile_via_wind(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.wind_get_option_iv = MagicMock(return_value={"iv_percentile": 0.15})
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            iv = engine._get_market_iv_percentile()
        assert iv == 0.15

    def test_get_market_iv_percentile_none(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.wind_get_option_iv = MagicMock(return_value=None)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            iv = engine._get_market_iv_percentile()
        assert iv is None

    def test_get_market_iv_percentile_missing_key(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = GammaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.wind_get_option_iv = MagicMock(return_value={"other_key": 0.2})  # 无 iv_percentile
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            iv = engine._get_market_iv_percentile()
        assert iv is None