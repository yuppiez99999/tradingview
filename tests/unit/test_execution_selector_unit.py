"""
单元测试: utils/execution_selector.py
覆盖 _estimate_depth_ratio / _compute_adaptive_weights / _make_result / choose_execution_algorithm
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.execution_selector import (
    _compute_adaptive_weights,
    _estimate_depth_ratio,
    _make_result,
    choose_execution_algorithm,
)


class TestEstimateDepthRatio:
    def test_basic(self):
        assert _estimate_depth_ratio(100000, 10.0, 10000) == 1.0

    def test_zero_volume(self):
        assert _estimate_depth_ratio(100000, 10.0, 0) == 0.0

    def test_negative_volume(self):
        assert _estimate_depth_ratio(100000, 10.0, -1) == 0.0

    def test_zero_price(self):
        assert _estimate_depth_ratio(100000, 0, 10000) == 0.0

    def test_negative_price(self):
        assert _estimate_depth_ratio(100000, -1, 10000) == 0.0

    def test_small_ratio(self):
        result = _estimate_depth_ratio(100, 10.0, 100000)
        assert 0 < result < 0.001


class TestComputeAdaptiveWeights:
    def test_normal_market(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.02, 0.01, 100000, 0.7, 0.3
        )
        assert ac == 0.7
        assert at == 0.3
        assert shallow is False
        assert high_vol is False

    def test_shallow_market(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.06, 0.01, 100000, 0.7, 0.3
        )
        assert ac == 0.5
        assert at == 0.5
        assert shallow is True

    def test_high_volatility(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.02, 0.06, 100000, 0.7, 0.3
        )
        assert ac == 0.6
        assert at == 0.4
        assert high_vol is True

    def test_shallow_and_high_vol(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.06, 0.06, 100000, 0.7, 0.3
        )
        assert ac == 0.4
        assert at == 0.6
        assert shallow is True
        assert high_vol is True

    def test_deep_market_low_ratio(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.005, 0.01, 100000, 0.7, 0.3
        )
        assert ac == 0.8
        assert at == 0.2

    def test_zero_volume_shallow(self):
        ac, at, reason, shallow, high_vol = _compute_adaptive_weights(
            0.02, 0.01, 0, 0.7, 0.3
        )
        assert shallow is True

    def test_reason_string(self):
        ac, at, reason, _, _ = _compute_adaptive_weights(0.02, 0.01, 100000, 0.7, 0.3)
        assert "depth_ratio" in reason
        assert "vol" in reason


class TestMakeResult:
    def test_basic(self):
        result = _make_result("twap", "test reason")
        assert result["algorithm"] == "twap"
        assert result["reason"] == "test reason"
        assert result["orders"] == []
        assert result["comparison"] == {}

    def test_with_all_params(self):
        result = _make_result(
            "vwap", "full", comparison={"a": 1}, orders=[1, 2],
            estimated_cost=100.0, cost_bps=5.0, execution_time_minutes=30.0,
            adaptive_params={"key": "val"},
        )
        assert result["comparison"] == {"a": 1}
        assert result["orders"] == [1, 2]
        assert result["estimated_cost"] == 100.0
        assert result["cost_bps"] == 5.0
        assert result["execution_time_minutes"] == 30.0
        assert result["adaptive_params"] == {"key": "val"}

    def test_no_adaptive_params(self):
        result = _make_result("immediate", "simple")
        assert "adaptive_params" not in result


class TestChooseExecutionAlgorithm:
    def test_import_failure_returns_immediate(self):
        with patch.dict("sys.modules", {"utils.wt_execution_algo": None}):
            result = choose_execution_algorithm(100000, 10.0, 10000)
        assert result["algorithm"] == "immediate"
        assert "回退" in result["reason"]

    def test_empty_comparison(self):
        mock_mod = MagicMock()
        mock_mod.compare_execution = MagicMock(return_value={})
        with patch.dict("sys.modules", {"utils.wt_execution_algo": mock_mod}):
            result = choose_execution_algorithm(100000, 10.0, 10000)
        assert result["algorithm"] == "immediate"

    def test_selects_best_algorithm(self):
        comparison = {
            "twap": {"estimated_cost": 50, "cost_bps": 5, "execution_time_minutes": 30, "orders": []},
            "vwap": {"estimated_cost": 40, "cost_bps": 4, "execution_time_minutes": 20, "orders": []},
            "immediate": {"estimated_cost": 100, "cost_bps": 10, "execution_time_minutes": 5, "orders": []},
        }
        mock_mod = MagicMock()
        mock_mod.compare_execution = MagicMock(return_value=comparison)
        with patch.dict("sys.modules", {"utils.wt_execution_algo": mock_mod}):
            result = choose_execution_algorithm(100000, 10.0, 10000)
        assert result["algorithm"] in ("twap", "vwap", "immediate")
        assert "adaptive_params" in result
        assert "depth_ratio" in result["adaptive_params"]

    def test_max_execution_time_filter(self):
        comparison = {
            "twap": {"estimated_cost": 50, "cost_bps": 5, "execution_time_minutes": 120, "orders": []},
            "immediate": {"estimated_cost": 100, "cost_bps": 10, "execution_time_minutes": 5, "orders": []},
        }
        mock_mod = MagicMock()
        mock_mod.compare_execution = MagicMock(return_value=comparison)
        with patch.dict("sys.modules", {"utils.wt_execution_algo": mock_mod}):
            result = choose_execution_algorithm(100000, 10.0, 10000, max_execution_minutes=60)
        assert result["algorithm"] == "immediate"

    def test_all_exceed_time(self):
        comparison = {
            "twap": {"estimated_cost": 50, "cost_bps": 5, "execution_time_minutes": 120, "orders": []},
        }
        mock_mod = MagicMock()
        mock_mod.compare_execution = MagicMock(return_value=comparison)
        with patch.dict("sys.modules", {"utils.wt_execution_algo": mock_mod}):
            result = choose_execution_algorithm(100000, 10.0, 10000, max_execution_minutes=30)
        assert result["algorithm"] == "immediate"
        assert "超时" in result["reason"]