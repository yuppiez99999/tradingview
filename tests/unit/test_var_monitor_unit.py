"""
单元测试: utils/var_monitor.py
覆盖 VaRMonitor.calculate_var / calculate_var_from_positions / execute_breach_response / get_event_history
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from utils.var_monitor import VaRMonitor


class TestVaRMonitorInit:
    def test_default_lookback(self):
        vm = VaRMonitor()
        assert vm.lookback_days == 252

    def test_custom_lookback(self):
        vm = VaRMonitor(lookback_days=100)
        assert vm.lookback_days == 100


class TestCalculateVar:
    def test_empty_returns(self):
        vm = VaRMonitor()
        result = vm.calculate_var([], 5_000_000)
        assert result["any_breach"] is False
        assert result.get("error") == "insufficient_data"

    def test_normal_returns(self):
        vm = VaRMonitor()
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 252).tolist()
        result = vm.calculate_var(returns, 5_000_000)
        assert "var_95_pct" in result
        assert "var_99_pct" in result
        assert "var_95_amount" in result
        assert "var_99_amount" in result
        assert result["method"] == "historical_simulation"
        assert result["portfolio_value"] == 5_000_000

    def test_breach_detection(self):
        vm = VaRMonitor()
        returns = [-0.06e0] * 50 + [0.001] * 50
        result = vm.calculate_var(returns, 5_000_000)
        assert result["any_breach"] is True
        assert len(result["actions"]) > 0

    def test_no_breach(self):
        vm = VaRMonitor()
        returns = [0.001] * 100
        result = vm.calculate_var(returns, 5_000_000)
        assert result["any_breach"] is False
        assert result["actions"] == []

    def test_lookback_truncation(self):
        vm = VaRMonitor(lookback_days=50)
        returns = [0.001] * 200
        result = vm.calculate_var(returns, 5_000_000)
        assert result["lookback_days"] == 50

    def test_custom_confidence_levels(self):
        vm = VaRMonitor()
        returns = np.random.normal(0.001, 0.01, 100).tolist()
        result = vm.calculate_var(returns, 5_000_000, confidence_levels=[0.90])
        assert "var_90_pct" in result
        assert "var_95_pct" not in result

    def test_amount_calculation(self):
        vm = VaRMonitor()
        returns = [-0.02] * 100
        result = vm.calculate_var(returns, 1_000_000)
        assert result["var_95_amount"] == pytest.approx(
            result["var_95_pct"] * 1_000_000
        )


class TestCalculateVarFromPositions:
    def test_basic(self):
        vm = VaRMonitor()
        positions = [
            {"code": "600519", "weight": 0.5},
            {"code": "000001", "weight": 0.5},
        ]
        returns_matrix = {
            "600519": [0.01, -0.02, 0.005] * 20,
            "000001": [-0.01, 0.02, -0.005] * 20,
        }
        result = vm.calculate_var_from_positions(positions, returns_matrix, 5_000_000)
        assert "var_95_pct" in result
        assert result["portfolio_value"] == 5_000_000

    def test_empty_positions(self):
        vm = VaRMonitor()
        result = vm.calculate_var_from_positions([], {}, 5_000_000)
        assert result.get("error") == "insufficient_data"


class TestExecuteBreachResponse:
    def test_var_95(self):
        vm = VaRMonitor()
        result = vm.execute_breach_response("var_95")
        assert result["executed"] is True
        assert result["var_type"] == "var_95"
        assert len(result["actions"]) == 1
        assert result["actions"][0]["action"] == "reduce_position"
        assert result["actions"][0]["pct"] == 0.10

    def test_var_99(self):
        vm = VaRMonitor()
        result = vm.execute_breach_response("var_99")
        assert result["executed"] is True
        assert len(result["actions"]) == 2
        assert result["actions"][0]["action"] == "reduce_position"
        assert result["actions"][0]["pct"] == 0.20
        assert result["actions"][1]["action"] == "increase_hedge"

    def test_unknown_type(self):
        vm = VaRMonitor()
        result = vm.execute_breach_response("unknown")
        assert result["executed"] is True
        assert result["actions"] == []


class TestGetEventHistory:
    def test_no_log_file(self, tmp_path):
        vm = VaRMonitor()
        with patch("utils.var_monitor.LOG_FILE", tmp_path / "nonexistent.jsonl"):
            assert vm.get_event_history() == []

    def test_with_events(self, tmp_path):
        import json
        from datetime import datetime

        log_file = tmp_path / "var_events.jsonl"
        event = {"timestamp": now_bj().isoformat(), "event": "test"}
        log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        vm = VaRMonitor()
        with patch("utils.var_monitor.LOG_FILE", log_file):
            history = vm.get_event_history(days=30)
        assert len(history) == 1
        assert history[0]["event"] == "test"
