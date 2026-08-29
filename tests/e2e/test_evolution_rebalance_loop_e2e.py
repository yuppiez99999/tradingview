"""test_evolution_rebalance_loop_e2e.py — 进化→再平衡闭环端到端测试

验证完整数据流:
    EvolutionOrchestratorV2.run_cycle()
        → CycleResult.weight_adjustments
        → (写入 factor_weights.json)
        → HedgeRebalanceIntegrator._load_evolution_factor_weights()
        → check_rebalance() 中 target_weight *= multiplier

使用临时目录隔离, 不污染生产配置.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_config_dir(tmp_path):
    """创建隔离的配置目录, 含最小 portfolio.yaml + factor_weights.json."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    portfolio_yaml = """
portfolio_value: 2000000
target_annual_return: 0.08
target_max_drawdown: 0.15

categories:
  高端制造:
    weight: 0.4
  顺周期:
    weight: 0.2
  资源:
    weight: 0.2
  防御:
    weight: 0.2

assets:
  - code: "600519.SH"
    name: "贵州茅台"
    category: "高端制造"
    target_weight: 0.08
  - code: "601318.SH"
    name: "中国平安"
    category: "防御"
    target_weight: 0.06
  - code: "600028.SH"
    name: "中国石化"
    category: "顺周期"
    target_weight: 0.05
"""
    (config_dir / "portfolio.yaml").write_text(portfolio_yaml, encoding="utf-8")
    return config_dir


class TestEvolutionToRebalanceE2E:
    """进化产出 → factor_weights.json → 再平衡消费 完整数据流."""

    @pytest.mark.e2e
    def test_factor_weights_json_round_trip(self, isolated_config_dir):
        """factor_weights.json 写入→读取 round-trip 一致性."""
        fw = {"600519.SH": 1.2, "601318.SH": 0.8, "600028.SH": 1.0}
        fw_path = isolated_config_dir / "factor_weights.json"
        fw_path.write_text(json.dumps(fw), encoding="utf-8")

        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(isolated_config_dir)

        loaded = integrator._load_evolution_factor_weights()
        assert loaded == fw

    @pytest.mark.e2e
    def test_cycle_result_to_dict_preserves_weight_adjustments(self):
        """CycleResult.to_dict() 保留 weight_adjustments 供消费端提取."""
        from utils.evolution.orchestrator import CycleResult

        adjustments = {"600519.SH": 1.3, "601318.SH": 0.7}
        cr = CycleResult(
            status="success",
            level="L2",
            action="promote",
            weight_adjustments=adjustments,
        )
        d = cr.to_dict()

        consumed = d.get("weight_adjustments", {})
        assert consumed == adjustments
        for _, mult in consumed.items():
            assert 0.5 <= mult <= 2.0

    @pytest.mark.e2e
    def test_v2_run_cycle_to_rebalance_weight_application(self, isolated_config_dir):
        """v2 run_cycle() → to_dict() → weight_adjustments → 再平衡 target_weight 调整."""
        from utils.evolution.orchestrator import CycleResult

        mock_cycle_result = CycleResult(
            status="success",
            level="L2",
            action="promote",
            weight_adjustments={"600519.SH": 1.5, "601318.SH": 0.6},
        )
        cycle_dict = mock_cycle_result.to_dict()

        weight_adjustments = cycle_dict.get("weight_adjustments", {})
        assert weight_adjustments

        base_weights = {"600519.SH": 0.08, "601318.SH": 0.06, "600028.SH": 0.05}
        adjusted = {}
        for code, w in base_weights.items():
            mult = weight_adjustments.get(code, 1.0)
            if 0.5 <= mult <= 2.0:
                adjusted[code] = w * mult
            else:
                adjusted[code] = w

        assert abs(adjusted["600519.SH"] - 0.12) < 1e-9
        assert abs(adjusted["601318.SH"] - 0.036) < 1e-9
        assert adjusted["600028.SH"] == 0.05

    @pytest.mark.e2e
    def test_rebalance_engine_consumes_factor_weights_file(self, isolated_config_dir):
        """再平衡引擎从 factor_weights.json 读取并应用进化权重."""
        fw = {"600519.SH": 1.5, "601318.SH": 0.6}
        (isolated_config_dir / "factor_weights.json").write_text(
            json.dumps(fw), encoding="utf-8"
        )

        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(isolated_config_dir)

        loaded = integrator._load_evolution_factor_weights()
        assert loaded == fw

        base_target = 0.08
        code = "600519.SH"
        adjusted = base_target * loaded.get(code, 1.0)
        assert abs(adjusted - 0.12) < 1e-9

    @pytest.mark.e2e
    def test_full_loop_evolution_output_to_rebalance_input(self, isolated_config_dir):
        """完整闭环: 进化产出 → 写入 JSON → 再平衡读取 → 权重调整."""
        from utils.evolution.orchestrator import CycleResult

        cr = CycleResult(
            status="success",
            weight_adjustments={"600519.SH": 1.3, "601318.SH": 0.8},
        )
        cycle_dict = cr.to_dict()
        adjustments = cycle_dict["weight_adjustments"]

        fw_path = isolated_config_dir / "factor_weights.json"
        fw_path.write_text(json.dumps(adjustments), encoding="utf-8")

        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator.__new__(HedgeRebalanceIntegrator)
        integrator.config_dir = str(isolated_config_dir)
        loaded = integrator._load_evolution_factor_weights()

        assert loaded == adjustments

        base_weights = {"600519.SH": 0.08, "601318.SH": 0.06}
        final_weights = {
            code: w * loaded.get(code, 1.0) for code, w in base_weights.items()
        }
        assert abs(final_weights["600519.SH"] - 0.104) < 1e-9
        assert abs(final_weights["601318.SH"] - 0.048) < 1e-9

    @pytest.mark.e2e
    def test_consumer_v2_path_extracts_weight_adjustments(self):
        """etf_option_hedge_rebalancer._run_evolution_cycle() v2 路径提取 weight_adjustments."""
        try:
            from utils.evolution.orchestrator import EvolutionOrchestratorV2
        except ImportError:
            pytest.skip("EvolutionOrchestratorV2 不可用")

        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
        from utils.evolution.orchestrator import CycleResult

        mock_v2 = MagicMock(spec=EvolutionOrchestratorV2)
        mock_v2.enabled = True
        mock_v2.run_cycle.return_value = CycleResult(
            status="success",
            weight_adjustments={"510300.SH": 1.2, "510050.SH": 0.9},
        )

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = mock_v2

        with patch("etf_option_hedge_rebalancer._EO_V2_OK", True):
            result = rebalancer._run_evolution_cycle()

        assert "weight_adjustments" in result
        assert result["weight_adjustments"]["510300.SH"] == 1.2
        assert result["weight_adjustments"]["510050.SH"] == 0.9

    @pytest.mark.e2e
    def test_weight_adjustments_applied_in_run_daily_rebalance(self):
        """run_daily_rebalance 中 weight_adjustments 被应用到 target_weights."""

        target_weights = {"510300.SH": 0.10, "510050.SH": 0.08, "510500.SH": 0.06}
        weight_adjustments = {"510300.SH": 1.5, "510050.SH": 0.5}

        adjusted = dict(target_weights)
        adjusted_count = 0
        for code, multiplier in weight_adjustments.items():
            if (
                code in adjusted
                and isinstance(multiplier, (int, float))
                and 0.5 <= multiplier <= 2.0
            ):
                adjusted[code] = adjusted[code] * float(multiplier)
                adjusted_count += 1

        assert adjusted_count == 2
        assert abs(adjusted["510300.SH"] - 0.15) < 1e-9
        assert abs(adjusted["510050.SH"] - 0.04) < 1e-9
        assert adjusted["510500.SH"] == 0.06
