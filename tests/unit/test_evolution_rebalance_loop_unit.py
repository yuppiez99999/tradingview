"""test_evolution_rebalance_loop_unit.py — 进化→再平衡闭环4处断裂修复单元测试

覆盖:
  - 断裂1: hedge_rebalance_integrator._load_evolution_factor_weights()
  - 断裂2: orchestrator.CycleResult.weight_adjustments 字段 + to_dict()
  - 断裂3: orchestrator._derive_weight_adjustments() 方法
  - 断裂3: orchestrator._route_by_recommendation promote/rollback 分支
  - 断裂4: etf_option_hedge_rebalancer._run_evolution_cycle() v2 对接
  - 断裂4: drift_monitor rebalance_callback 触发
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 断裂1: _load_evolution_factor_weights
# ============================================================


class TestLoadEvolutionFactorWeights:
    """hedge_rebalance_integrator._load_evolution_factor_weights() 读取 factor_weights.json."""

    @pytest.mark.unit
    def test_file_not_exist_returns_empty(self, tmp_path):
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(tmp_path)
        result = integrator._load_evolution_factor_weights()
        assert result == {}

    @pytest.mark.unit
    def test_valid_multipliers_loaded(self, tmp_path):
        fw = {"600519": 1.2, "000858": 0.8, "601318": 1.5}
        (tmp_path / "factor_weights.json").write_text(json.dumps(fw), encoding="utf-8")
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(tmp_path)
        result = integrator._load_evolution_factor_weights()
        assert result == {"600519": 1.2, "000858": 0.8, "601318": 1.5}

    @pytest.mark.unit
    def test_out_of_range_filtered(self, tmp_path):
        fw = {"600519": 0.3, "000858": 2.5, "601318": 1.0}
        (tmp_path / "factor_weights.json").write_text(json.dumps(fw), encoding="utf-8")
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(tmp_path)
        result = integrator._load_evolution_factor_weights()
        assert result == {"601318": 1.0}

    @pytest.mark.unit
    def test_invalid_json_returns_empty(self, tmp_path):
        (tmp_path / "factor_weights.json").write_text("not json", encoding="utf-8")
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(tmp_path)
        result = integrator._load_evolution_factor_weights()
        assert result == {}


# ============================================================
# 断裂2: CycleResult.weight_adjustments 字段
# ============================================================


class TestCycleResultWeightAdjustments:
    """CycleResult dataclass 新增 weight_adjustments 字段."""

    @pytest.mark.unit
    def test_default_empty_dict(self):
        from utils.evolution.orchestrator import CycleResult

        cr = CycleResult()
        assert cr.weight_adjustments == {}

    @pytest.mark.unit
    def test_to_dict_contains_weight_adjustments(self):
        from utils.evolution.orchestrator import CycleResult

        cr = CycleResult(weight_adjustments={"600519": 1.2})
        d = cr.to_dict()
        assert "weight_adjustments" in d
        assert d["weight_adjustments"] == {"600519": 1.2}

    @pytest.mark.unit
    def test_to_dict_round_trip(self):
        from utils.evolution.orchestrator import CycleResult

        cr = CycleResult(
            status="success",
            weight_adjustments={"600519": 1.2, "000858": 0.8},
        )
        d = cr.to_dict()
        assert d["weight_adjustments"] == {"600519": 1.2, "000858": 0.8}


# ============================================================
# 断裂3: _derive_weight_adjustments 方法
# ============================================================


class TestDeriveWeightAdjustments:
    """orchestrator._derive_weight_adjustments() 从评估报告推导权重乘子."""

    @pytest.mark.unit
    def test_empty_report_returns_empty(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        result = EvolutionOrchestratorV2._derive_weight_adjustments({})
        assert result == {}

    @pytest.mark.unit
    def test_none_returns_empty(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        result = EvolutionOrchestratorV2._derive_weight_adjustments(None)
        assert result == {}

    @pytest.mark.unit
    def test_explicit_weight_adjustments_extracted(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {"weight_adjustments": {"600519": 1.2, "000858": 0.8}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result == {"600519": 1.2, "000858": 0.8}

    @pytest.mark.unit
    def test_explicit_out_of_range_filtered(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {"weight_adjustments": {"600519": 0.3, "000858": 2.5, "601318": 1.0}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result == {"601318": 1.0}

    @pytest.mark.unit
    def test_factor_scores_converted(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {"factor_scores": {"600519": 0.8, "000858": 0.2}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert len(result) == 2
        assert 0.5 <= result["600519"] <= 2.0
        assert 0.5 <= result["000858"] <= 2.0
        assert result["600519"] > result["000858"]

    @pytest.mark.unit
    def test_factor_scores_clamped(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {"factor_scores": {"A": -1.0, "B": 2.0}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result["A"] == 0.5
        assert result["B"] == 2.0

    @pytest.mark.unit
    def test_no_per_code_info_returns_empty(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {
            "public_score": 0.7,
            "private_score": 0.6,
            "recommendation": "continue",
        }
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result == {}

    @pytest.mark.unit
    def test_explicit_takes_priority_over_factor_scores(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        report = {
            "weight_adjustments": {"600519": 1.5},
            "factor_scores": {"000858": 0.9},
        }
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result == {"600519": 1.5}


# ============================================================
# 断裂3: _route_by_recommendation promote/rollback 填充
# ============================================================


class TestRouteByRecommendationWeightAdjustments:
    """_route_by_recommendation 在所有分支填充 weight_adjustments."""

    @pytest.mark.unit
    def test_continue_branch_has_weight_adjustments(self):
        from utils.evolution.orchestrator import (
            EvolutionOrchestratorV2,
        )

        mock_report = MagicMock()
        mock_report.recommendation = "continue"
        mock_report.public_score = 0.5
        mock_report.private_score = 0.5
        mock_report.reward_hacking_risk = 0.1
        mock_report.to_dict.return_value = {
            "weight_adjustments": {"600519": 1.2},
            "recommendation": "continue",
        }
        mock_metrics = MagicMock()
        mock_metrics.to_dict.return_value = {}

        orch = EvolutionOrchestratorV2.__new__(EvolutionOrchestratorV2)
        orch._enabled = True
        orch._memory = MagicMock()
        orch._memory.record.return_value = "pid_test"

        result = orch._route_by_recommendation(
            mock_report, mock_metrics, "2026-01-01T00:00:00Z"
        )
        assert result.weight_adjustments == {"600519": 1.2}

    @pytest.mark.unit
    def test_continue_branch_empty_when_no_per_code(self):
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        mock_report = MagicMock()
        mock_report.recommendation = "continue"
        mock_report.public_score = 0.5
        mock_report.private_score = 0.5
        mock_report.reward_hacking_risk = 0.1
        mock_report.to_dict.return_value = {"recommendation": "continue"}
        mock_metrics = MagicMock()
        mock_metrics.to_dict.return_value = {}

        orch = EvolutionOrchestratorV2.__new__(EvolutionOrchestratorV2)
        orch._enabled = True
        orch._memory = MagicMock()
        orch._memory.record.return_value = "pid_test"

        result = orch._route_by_recommendation(
            mock_report, mock_metrics, "2026-01-01T00:00:00Z"
        )
        assert result.weight_adjustments == {}


# ============================================================
# 断裂4: etf_option_hedge_rebalancer._run_evolution_cycle v2 对接
# ============================================================


class TestRunEvolutionCycleV2:
    """_run_evolution_cycle() 优先使用 v2 run_cycle().to_dict()."""

    @pytest.mark.unit
    def test_disabled_orchestrator_returns_empty(self):
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = MagicMock()
        rebalancer.evolution_orchestrator.enabled = False
        assert rebalancer._run_evolution_cycle() == {}

    @pytest.mark.unit
    def test_none_orchestrator_returns_empty(self):
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = None
        assert rebalancer._run_evolution_cycle() == {}

    @pytest.mark.unit
    def test_v2_orchestrator_calls_run_cycle_to_dict(self):
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

        try:
            from utils.evolution.orchestrator import EvolutionOrchestratorV2
        except ImportError:
            pytest.skip("EvolutionOrchestratorV2 不可用")

        mock_v2 = MagicMock(spec=EvolutionOrchestratorV2)
        mock_v2.enabled = True
        mock_cycle_result = MagicMock()
        mock_cycle_result.to_dict.return_value = {
            "status": "success",
            "weight_adjustments": {"600519": 1.2},
        }
        mock_v2.run_cycle.return_value = mock_cycle_result

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = mock_v2

        with patch("etf_option_hedge_rebalancer._EO_V2_OK", True):
            result = rebalancer._run_evolution_cycle()

        assert result["weight_adjustments"] == {"600519": 1.2}
        mock_v2.run_cycle.assert_called_once()

    @pytest.mark.unit
    def test_v1_orchestrator_calls_run_observation_cycle(self):
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

        mock_v1 = MagicMock()
        mock_v1.enabled = True
        mock_v1.run_observation_cycle.return_value = {
            "status": "ok",
            "recommendation": "continue",
        }

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = mock_v1

        with patch("etf_option_hedge_rebalancer._EO_V2_OK", False):
            result = rebalancer._run_evolution_cycle()

        assert result["recommendation"] == "continue"
        mock_v1.run_observation_cycle.assert_called_once()


# ============================================================
# 断裂4: drift_monitor rebalance_callback
# ============================================================


class TestDriftMonitorRebalanceCallback:
    """drift_monitor.DriftMonitor 新增 rebalance_callback 参数."""

    @pytest.mark.unit
    def test_rebalance_callback_default_none(self):
        from utils.alpha.drift_monitor import DriftMonitor

        dm = DriftMonitor()
        assert getattr(dm, "rebalance_callback", None) is None

    @pytest.mark.unit
    def test_rebalance_callback_set(self):
        from utils.alpha.drift_monitor import DriftMonitor

        cb = MagicMock()
        dm = DriftMonitor(rebalance_callback=cb)
        assert dm.rebalance_callback is cb
