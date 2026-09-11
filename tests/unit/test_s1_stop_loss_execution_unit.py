"""test_s1_stop_loss_execution_unit.py — 止损执行归属与阻断性告警 (Issue #13: S-1)

巡检事实:
    daily_trade_executor 调 _run_stop_loss_check 后**只打一条 WARNING**, 不再使用
    返回值; 叠加 StopLossManager 触发即锁死状态机 → "错过一条 WARNING = 该标的止损
    保护永久消失", 既不自动平仓也不阻断执行。

本测试锁定 S-1 修复后的语义:
    1. 阈值来自 config/risk_thresholds.yaml (不再硬编码 8%/15%);
    2. 触发 → 执行被阻断 (block_on_trigger=true), 并回传告警明细;
    3. 阻断结果可被下游识别 (status/blocked_reason/stop_loss_alerts);
    4. 未触发时不阻断。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.risk_thresholds import get_stop_loss_config  # noqa: E402
from utils.wt_risk_control import StopLossManager  # noqa: E402


class TestStopLossThresholdsFromSingleSource:
    @pytest.mark.unit
    def test_thresholds_aligned_with_config(self):
        cfg = get_stop_loss_config()
        assert cfg["stop_loss_pct"] == pytest.approx(0.08)
        assert cfg["take_profit_pct"] == pytest.approx(0.15)
        assert cfg["block_on_trigger"] is True


class TestRunStopLossCheck:
    @pytest.mark.unit
    def test_manager_unavailable_is_surfaced(self):
        """止损管理器缺失 → 返回显式降级标记 (不静默返回空列表)。"""
        from daily_trade_executor import _run_stop_loss_check

        triggered = _run_stop_loss_check({}, {"600519": {"avg_cost": 100, "shares": 100}})
        assert len(triggered) == 1
        assert triggered[0]["code"] == "__manager_unavailable"

    @pytest.mark.unit
    def test_no_trigger_when_price_safe(self):
        from daily_trade_executor import _run_stop_loss_check

        manager = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
        positions = {
            "600519": {
                "avg_cost": 100.0,
                "total_shares": 100,
                "est_price": 100.0,
                "name": "贵州茅台",
            }
        }
        assert _run_stop_loss_check({"stop_loss_manager": manager}, positions) == []

    @pytest.mark.unit
    def test_trigger_returns_alert_details(self):
        from daily_trade_executor import _run_stop_loss_check

        manager = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
        positions = {
            "600519": {
                "avg_cost": 100.0,
                "total_shares": 100,
                "est_price": 90.0,  # -10% → 触发 8% 止损
                "name": "贵州茅台",
            }
        }
        triggered = _run_stop_loss_check({"stop_loss_manager": manager}, positions)
        assert len(triggered) == 1
        assert triggered[0]["action"] == "stop_loss"
        assert triggered[0]["pnl_pct"] == pytest.approx(-0.10, abs=1e-6)


class TestBlockingAlertWiring:
    """S-1: 触发后必须阻断执行, 而非仅记日志。"""

    @pytest.mark.unit
    def test_trigger_blocks_execution(self, monkeypatch, tmp_path):
        """端到端 (execute_instructions): 止损触发 → status=blocked + 告警明细。"""
        import daily_trade_executor as dte

        manager = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
        instruction_file = tmp_path / "2026-09-11_instructions.json"
        instruction_file.write_text(
            json.dumps({"instructions": [{"confirm": True}], "risk_checks": {}}),
            encoding="utf-8",
        )

        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        monkeypatch.setattr(dte, "init_wt_modules", lambda *a, **k: {"stop_loss_manager": manager})
        monkeypatch.setattr(dte, "_run_wt_risk_block_check", lambda *a, **k: None)
        monkeypatch.setattr(dte, "load_build_progress", lambda *a, **k: {"daily_records": []})
        monkeypatch.setattr(dte, "POSITIONS_FILE", tmp_path / "positions.json")
        monkeypatch.setattr(
            dte,
            "load_positions",
            lambda *a, **k: {
                "positions": {
                    "600519": {
                        "avg_cost": 100.0,
                        "total_shares": 100,
                        "est_price": 85.0,  # -15% → 触发 8% 止损
                        "name": "贵州茅台",
                    }
                }
            },
        )
        (tmp_path / "positions.json").write_text(
            json.dumps({"positions": {}}), encoding="utf-8"
        )

        result = dte.execute_instructions("2026-09-11")

        assert result["status"] == "blocked"
        assert result["blocked_reason"] == "stop_loss_triggered (S-1)"
        assert len(result["stop_loss_alerts"]) == 1
        assert result["stop_loss_alerts"][0]["action"] == "stop_loss"

    @pytest.mark.unit
    def test_manager_unavailable_does_not_block(self, monkeypatch, tmp_path):
        """DTE-3 降级标记不是止损触发 → 不得阻断主链 (风控降级不误伤执行)。"""
        import daily_trade_executor as dte

        instruction_file = tmp_path / "2026-09-11_instructions.json"
        instruction_file.write_text(
            json.dumps({"instructions": [{"confirm": False}], "risk_checks": {}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        monkeypatch.setattr(dte, "init_wt_modules", lambda *a, **k: {})  # 无 stop_loss_manager
        monkeypatch.setattr(dte, "_run_wt_risk_block_check", lambda *a, **k: None)
        monkeypatch.setattr(dte, "load_build_progress", lambda *a, **k: {"daily_records": []})
        monkeypatch.setattr(dte, "POSITIONS_FILE", tmp_path / "positions.json")
        monkeypatch.setattr(dte, "load_positions", lambda *a, **k: {"positions": {}})
        (tmp_path / "positions.json").write_text(
            json.dumps({"positions": {}}), encoding="utf-8"
        )

        result = dte.execute_instructions("2026-09-11")
        assert result.get("blocked_reason") != "stop_loss_triggered (S-1)"

    @pytest.mark.unit
    def test_no_trigger_does_not_block(self, monkeypatch, tmp_path):
        """未触发止损 → 不应出现 S-1 阻断标记。"""
        import daily_trade_executor as dte

        manager = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
        instruction_file = tmp_path / "2026-09-11_instructions.json"
        instruction_file.write_text(
            json.dumps({"instructions": [{"confirm": False}], "risk_checks": {}}),
            encoding="utf-8",
        )

        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        monkeypatch.setattr(dte, "init_wt_modules", lambda *a, **k: {"stop_loss_manager": manager})
        monkeypatch.setattr(dte, "_run_wt_risk_block_check", lambda *a, **k: None)
        monkeypatch.setattr(dte, "load_build_progress", lambda *a, **k: {"daily_records": []})
        monkeypatch.setattr(dte, "POSITIONS_FILE", tmp_path / "positions.json")
        monkeypatch.setattr(
            dte,
            "load_positions",
            lambda *a, **k: {
                "positions": {
                    "600519": {
                        "avg_cost": 100.0,
                        "total_shares": 100,
                        "est_price": 100.0,  # 安全区间
                        "name": "贵州茅台",
                    }
                }
            },
        )
        (tmp_path / "positions.json").write_text(
            json.dumps({"positions": {}}), encoding="utf-8"
        )

        result = dte.execute_instructions("2026-09-11")
        assert result.get("blocked_reason") != "stop_loss_triggered (S-1)"
