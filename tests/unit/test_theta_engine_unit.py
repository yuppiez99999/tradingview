"""Theta Engine 单元测试.

被测模块: utils/theta_engine.py
覆盖目标: >=85%
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.theta_engine import ThetaEngine  # noqa: E402


def _make_config(enabled=True, with_etfs=True):
    cfg = {
        "hedge": {
            "theta_engine": {
                "enabled": enabled,
                "underlying_collateral": 3000000,
                "risk_free_rate": 0.02,
                "rolling": {"dte_range": [20, 40], "strike_otm_pct": [0.05, 0.08]},
                "expected_enhancement": {
                    "monthly_theta_target": [0.005, 0.008],
                    "annual_cashflow_boost": [0.06, 0.09],
                },
            }
        }
    }
    if with_etfs:
        cfg["hedge"]["theta_engine"]["target_etfs"] = [
            {"code": "588080", "weight_in_pool": 0.5},
            {"code": "510300", "weight_in_pool": 0.5},
        ]
    return cfg


class TestThetaEngineInit:
    def test_load_config_explicit_path(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        assert engine.config.get("enabled") is True

    def test_load_config_disabled(self, tmp_path):
        cfg = _make_config(enabled=False)
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        assert engine.config.get("enabled") is False

    def test_load_config_missing_file(self, tmp_path):
        engine = ThetaEngine(config_path=tmp_path / "nonexistent.yaml")
        assert engine.config == {}

    def test_load_config_invalid_yaml(self, tmp_path):
        cfg_path = tmp_path / "bad.yaml"
        cfg_path.write_text("- list\n- not dict", encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        assert engine.config == {}

    def test_load_config_no_theta_section(self, tmp_path):
        cfg_path = tmp_path / "empty.yaml"
        cfg_path.write_text(yaml.dump({"hedge": {}}), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        assert engine.config == {}


class TestGetEtfSpots:
    def test_no_target_etfs(self, tmp_path):
        cfg = _make_config(with_etfs=False)
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": MagicMock()}):
            spots = engine._get_etf_spots()
        assert spots == {}

    def test_with_mocked_wind(self, tmp_path):
        """SC-31: Wind 真实入口为 fetch_realtime_price (原 mock 的是幽灵 API)."""
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.fetch_realtime_price = MagicMock(return_value=1.05)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            spots = engine._get_etf_spots()
        assert len(spots) == 2

    def test_wind_exception_fallback(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.fetch_realtime_price = MagicMock(side_effect=RuntimeError("fail"))
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            spots = engine._get_etf_spots()
        assert isinstance(spots, dict)

    def test_import_error_fallback(self, tmp_path):
        """SC-31 核心: 依赖缺失 (ImportError) 也必须降级为 dict, 不得穿透.

        旧码异常元组不含 ImportError, 且目标为幽灵 API `wind_get_etf_quote`,
        ImportError 会直接炸穿备兑看涨现价链。
        """
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        mock_mod = MagicMock()
        mock_mod.fetch_realtime_price = MagicMock(
            side_effect=ImportError("cannot import name 'wind_get_etf_quote'")
        )
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_mod}):
            spots = engine._get_etf_spots()
        assert isinstance(spots, dict)


class TestGenerateMonthlyPlan:
    def test_disabled(self, tmp_path):
        cfg = _make_config(enabled=False)
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        result = engine.generate_monthly_plan()
        assert result == {"status": "disabled"}

    def test_no_spots(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch.object(engine, "_get_etf_spots", return_value={}):
            result = engine.generate_monthly_plan()
        assert result["status"] == "error"

    def test_normal_plan(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch.object(
            engine, "_get_etf_spots", return_value={"588080": 1.05, "510300": 4.20}
        ):
            with patch("utils.theta_engine.PLAN_DIR", tmp_path):
                result = engine.generate_monthly_plan()
        assert "positions" in result
        assert len(result["positions"]) == 2
        assert result["total_collateral"] == 3000000

    def test_partial_spots(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch.object(engine, "_get_etf_spots", return_value={"588080": 1.05}):
            with patch("utils.theta_engine.PLAN_DIR", tmp_path):
                result = engine.generate_monthly_plan()
        assert len(result["positions"]) == 1


class TestCheckRollover:
    def test_no_plans(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            result = engine.check_rollover()
        assert result == []

    def test_not_near_expiry(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        plan = {
            "expiry_date": (now_bj() + timedelta(days=30)).strftime("%Y-%m-%d"),
            "positions": [{"code": "588080", "strike": 1.08}],
        }
        (tmp_path / "theta_plan_20260814.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            result = engine.check_rollover()
        assert result == []

    def test_near_expiry(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        plan = {
            "expiry_date": (now_bj() + timedelta(days=3)).strftime("%Y-%m-%d"),
            "positions": [
                {"code": "588080", "strike": 1.08},
                {"code": "510300", "strike": 4.50},
            ],
        }
        (tmp_path / "theta_plan_20260814.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            result = engine.check_rollover()
        assert len(result) == 2
        assert result[0]["action"] == "close_and_open_new"


class TestGetThetaStatistics:
    def test_no_plans(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            stats = engine.get_theta_statistics()
        assert stats["total_plans"] == 0
        assert stats["target_met"] is False

    def test_with_plans(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        plan1 = {"total_est_premium": 50000, "portfolio_yield_monthly": 0.007}
        plan2 = {"total_est_premium": 60000, "portfolio_yield_monthly": 0.008}
        (tmp_path / "theta_plan_20260801.json").write_text(
            json.dumps(plan1), encoding="utf-8"
        )
        (tmp_path / "theta_plan_20260814.json").write_text(
            json.dumps(plan2), encoding="utf-8"
        )
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            stats = engine.get_theta_statistics()
        assert stats["total_plans"] == 2
        assert stats["total_premium_collected"] == 110000

    def test_with_corrupted_plan(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        (tmp_path / "theta_plan_20260801.json").write_text(
            "invalid json", encoding="utf-8"
        )
        (tmp_path / "theta_plan_20260814.json").write_text(
            json.dumps({"total_est_premium": 50000, "portfolio_yield_monthly": 0.007}),
            encoding="utf-8",
        )
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            stats = engine.get_theta_statistics()
        assert stats["total_plans"] == 2

    def test_target_met(self, tmp_path):
        cfg = _make_config()
        cfg_path = tmp_path / "portfolio.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        plan = {"total_est_premium": 100000, "portfolio_yield_monthly": 0.0075}
        (tmp_path / "theta_plan_20260814.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        engine = ThetaEngine(config_path=cfg_path)
        with patch("utils.theta_engine.PLAN_DIR", tmp_path):
            stats = engine.get_theta_statistics()
        assert isinstance(stats["target_met"], bool)
