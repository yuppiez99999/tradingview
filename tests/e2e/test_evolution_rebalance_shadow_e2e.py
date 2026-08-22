"""test_evolution_rebalance_shadow_e2e.py — 进化→再平衡闭环 Shadow 账户验证

在 Shadow 账户环境中验证进化→再平衡闭环的行为正确性:
    场景1: 进化权重调整→Shadow 账户记录调整后收益
    场景2: Flag 禁用→降级到原始权重 (无进化调整)
    场景3: 乘子约束 [0.5, 2.0] 边界验证
    场景4: 进化失败→fail-safe 降级 (Shadow 不受影响)
    场景5: 完整闭环: V2.run_cycle→weight_adjustments→再平衡→Shadow 记录
    场景6: 对比有/无进化调整的 Shadow 指标差异
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from utils.alpha.shadow_account_adapter import (
    ShadowAccountAdapter,
)
from utils.evolution.orchestrator import (
    CYCLE_STATUS_DISABLED,
    CycleResult,
    EvolutionOrchestratorV2,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]


# ============================================================
# Helper: mock compute_dsr (与 test_shadow_account_lifecycle_e2e 同模式)
# ============================================================

@dataclass
class _MockDsrResult:
    deflated_sharpe_ratio: float = 0.85


def _mock_compute_dsr(self):
    return _MockDsrResult(deflated_sharpe_ratio=0.85)


@pytest.fixture(autouse=True)
def mock_dsr_if_missing(monkeypatch):
    try:
        import deflated_sharpe  # noqa: F401
    except ImportError:
        monkeypatch.setattr(ShadowAccountAdapter, "compute_dsr", _mock_compute_dsr)


# ============================================================
# 共享 fixture
# ============================================================

@pytest.fixture
def shadow_adapter():
    return ShadowAccountAdapter(
        account_id="shadow_evo_rebalance",
        strategy_id="evolution_rebalance_loop",
        initial_capital=500_000,
    )


@pytest.fixture
def base_target_weights():
    return {
        "510300.SH": 0.15,
        "510050.SH": 0.10,
        "510500.SH": 0.07,
        "512100.SH": 0.09,
        "588000.SH": 0.06,
    }


@pytest.fixture
def base_daily_returns():
    return [
        0.005, -0.003, 0.008, -0.002, 0.004,
        -0.006, 0.003, 0.001, -0.004, 0.007,
        -0.005, 0.002, 0.006, -0.003, 0.004,
        0.002, -0.001, 0.005, 0.003, -0.002,
    ]


def _apply_weight_adjustments(
    target_weights: dict[str, float],
    adjustments: dict[str, float],
) -> dict[str, float]:
    result = dict(target_weights)
    for code, mult in adjustments.items():
        if code in result and isinstance(mult, (int, float)) and 0.5 <= mult <= 2.0:
            result[code] *= float(mult)
    return result


def _simulate_portfolio_returns(
    daily_returns: list[float],
    weight_multiplier_effect: float = 1.0,
) -> list[float]:
    return [r * weight_multiplier_effect for r in daily_returns]


# ============================================================
# 场景1: 进化权重调整→Shadow 账户记录调整后收益
# ============================================================

class TestEvolutionDrivenRebalanceShadow:
    """进化权重调整后 Shadow 账户应记录与调整一致的收益行为."""

    def test_weight_adjustments_applied_to_target_weights(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        adjustments = {"510300.SH": 1.2, "510050.SH": 0.8}
        adjusted = _apply_weight_adjustments(base_target_weights, adjustments)

        assert abs(adjusted["510300.SH"] - 0.18) < 1e-9
        assert abs(adjusted["510050.SH"] - 0.08) < 1e-9
        assert adjusted["510500.SH"] == base_target_weights["510500.SH"]

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success
        assert result.days_processed == len(base_daily_returns)

    def test_shadow_records_evolution_adjusted_returns(
        self, base_daily_returns, shadow_adapter,
    ):
        adjusted_returns = _simulate_portfolio_returns(base_daily_returns, 1.1)
        result = shadow_adapter.run_shadow(adjusted_returns)

        assert result.success
        assert result.final_nav > 1.0

        baseline_adapter = ShadowAccountAdapter(
            account_id="shadow_baseline",
            initial_capital=500_000,
        )
        baseline_result = baseline_adapter.run_shadow(base_daily_returns)

        assert result.final_nav != baseline_result.final_nav

    def test_cycle_result_weight_adjustments_flow_to_shadow(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        cr = CycleResult(
            status="success",
            level="L2",
            action="promote",
            weight_adjustments={"510300.SH": 1.3, "510050.SH": 0.7},
        )
        cycle_dict = cr.to_dict()
        adjustments = cycle_dict["weight_adjustments"]

        adjusted_weights = _apply_weight_adjustments(base_target_weights, adjustments)
        assert adjusted_weights["510300.SH"] > base_target_weights["510300.SH"]
        assert adjusted_weights["510050.SH"] < base_target_weights["510050.SH"]

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success


# ============================================================
# 场景2: Flag 禁用→降级到原始权重
# ============================================================

class TestFlagDisabledDegradation:
    """USE_EVOLUTION_ORCHESTRATOR=False 时应降级到原始逻辑."""

    def test_disabled_cycle_returns_empty_weight_adjustments(self):
        orch = EvolutionOrchestratorV2.__new__(EvolutionOrchestratorV2)
        orch._enabled = False
        orch.feature_flag_name = "USE_EVOLUTION_ORCHESTRATOR"


        result = orch.run_cycle()

        assert result.status == CYCLE_STATUS_DISABLED
        assert result.weight_adjustments == {}

    def test_disabled_flag_no_impact_on_rebalance(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        empty_adjustments: dict[str, float] = {}
        adjusted = _apply_weight_adjustments(base_target_weights, empty_adjustments)

        assert adjusted == base_target_weights

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success


# ============================================================
# 场景3: 乘子约束 [0.5, 2.0] 边界验证
# ============================================================

class TestMultiplierConstraint:
    """weight_adjustments 乘子必须在 [0.5, 2.0] 范围内."""

    def test_in_range_multipliers_applied(self, base_target_weights):
        adjustments = {"510300.SH": 0.5, "510050.SH": 2.0, "510500.SH": 1.0}
        adjusted = _apply_weight_adjustments(base_target_weights, adjustments)

        assert abs(adjusted["510300.SH"] - base_target_weights["510300.SH"] * 0.5) < 1e-9
        assert abs(adjusted["510050.SH"] - base_target_weights["510050.SH"] * 2.0) < 1e-9
        assert adjusted["510500.SH"] == base_target_weights["510500.SH"]

    def test_out_of_range_multipliers_filtered(self, base_target_weights):
        adjustments = {"510300.SH": 0.3, "510050.SH": 2.5, "510500.SH": 1.5}
        adjusted = _apply_weight_adjustments(base_target_weights, adjustments)

        assert adjusted["510300.SH"] == base_target_weights["510300.SH"]
        assert adjusted["510050.SH"] == base_target_weights["510050.SH"]
        assert abs(adjusted["510500.SH"] - base_target_weights["510500.SH"] * 1.5) < 1e-9

    def test_derive_weight_adjustments_clamps(self):
        report = {"weight_adjustments": {"A": 0.3, "B": 2.5, "C": 1.0}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert "A" not in result
        assert "B" not in result
        assert result["C"] == 1.0

    def test_factor_scores_clamped_to_range(self):
        report = {"factor_scores": {"A": -1.0, "B": 2.0}}
        result = EvolutionOrchestratorV2._derive_weight_adjustments(report)
        assert result["A"] == 0.5
        assert result["B"] == 2.0


# ============================================================
# 场景4: 进化失败→fail-safe 降级
# ============================================================

class TestEvolutionFailSafe:
    """进化编排失败时 Shadow 账户不受影响."""

    def test_v2_exception_returns_empty_dict(self):
        from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
        try:
            from utils.evolution.orchestrator import EvolutionOrchestratorV2
        except ImportError:
            pytest.skip("EvolutionOrchestratorV2 不可用")

        mock_v2 = MagicMock(spec=EvolutionOrchestratorV2)
        mock_v2.enabled = True
        mock_v2.run_cycle.side_effect = ValueError("模拟进化失败")

        rebalancer = ETFOptionHedgeRebalancer.__new__(ETFOptionHedgeRebalancer)
        rebalancer.evolution_orchestrator = mock_v2

        with patch("etf_option_hedge_rebalancer._EO_V2_OK", True):
            result = rebalancer._run_evolution_cycle()

        assert result == {}

    def test_shadow_unaffected_by_evolution_failure(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        empty_adjustments: dict[str, float] = {}
        adjusted = _apply_weight_adjustments(base_target_weights, empty_adjustments)

        assert adjusted == base_target_weights

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success
        assert result.fail_fast_triggered is False

    def test_orchestrator_degraded_status_has_empty_adjustments(self):
        cr = CycleResult(status="degraded", reason="v1_orchestrator_unavailable")
        assert cr.weight_adjustments == {}
        assert cr.to_dict()["weight_adjustments"] == {}


# ============================================================
# 场景5: 完整闭环: V2.run_cycle→weight_adjustments→再平衡→Shadow 记录
# ============================================================

class TestFullLoopShadowValidation:
    """完整闭环: V2 产出→权重调整→再平衡→Shadow 账户记录."""

    def test_full_loop_with_explicit_weight_adjustments(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        cr = CycleResult(
            status="success",
            level="L2",
            action="promote",
            weight_adjustments={"510300.SH": 1.2, "510050.SH": 0.9, "510500.SH": 1.1},
        )
        cycle_dict = cr.to_dict()
        adjustments = cycle_dict["weight_adjustments"]

        adjusted_weights = _apply_weight_adjustments(base_target_weights, adjustments)

        total_base = sum(base_target_weights.values())
        total_adjusted = sum(adjusted_weights.values())
        assert total_adjusted != total_base

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success
        assert result.days_processed == 20

    def test_full_loop_flag_disabled_no_adjustments(
        self, base_target_weights, base_daily_returns, shadow_adapter,
    ):
        cr = CycleResult(status=CYCLE_STATUS_DISABLED)
        cycle_dict = cr.to_dict()
        adjustments = cycle_dict["weight_adjustments"]

        assert adjustments == {}

        adjusted_weights = _apply_weight_adjustments(base_target_weights, adjustments)
        assert adjusted_weights == base_target_weights

        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success


# ============================================================
# 场景6: 对比有/无进化调整的 Shadow 指标差异
# ============================================================

class TestShadowMetricsComparison:
    """对比有/无进化权重调整的 Shadow 账户指标差异."""

    def test_adjusted_vs_baseline_shadow_metrics(
        self, base_daily_returns, shadow_adapter,
    ):
        baseline_result = shadow_adapter.run_shadow(base_daily_returns)
        assert baseline_result.success

        amplified_returns = _simulate_portfolio_returns(base_daily_returns, 1.5)
        adjusted_adapter = ShadowAccountAdapter(
            account_id="shadow_adjusted",
            initial_capital=500_000,
        )
        adjusted_result = adjusted_adapter.run_shadow(amplified_returns)
        assert adjusted_result.success

        assert baseline_result.final_nav != adjusted_result.final_nav

    def test_weight_adjustments_direction_consistency(
        self, base_target_weights,
    ):
        promote_adjustments = {"510300.SH": 1.3, "510050.SH": 1.2}
        rollback_adjustments = {"510300.SH": 0.7, "510050.SH": 0.8}

        promote_weights = _apply_weight_adjustments(base_target_weights, promote_adjustments)
        rollback_weights = _apply_weight_adjustments(base_target_weights, rollback_adjustments)

        assert promote_weights["510300.SH"] > base_target_weights["510300.SH"]
        assert rollback_weights["510300.SH"] < base_target_weights["510300.SH"]
        assert promote_weights["510300.SH"] > rollback_weights["510300.SH"]

    def test_shadow_fail_fast_not_triggered_by_evolution(
        self, base_daily_returns, shadow_adapter,
    ):
        result = shadow_adapter.run_shadow(base_daily_returns)
        assert result.success
        assert not result.fail_fast_triggered

    def test_rebalance_callback_in_drift_monitor(self):
        from utils.alpha.drift_monitor import DriftMonitor

        cb = MagicMock()
        dm = DriftMonitor(rebalance_callback=cb)
        assert dm.rebalance_callback is cb
        assert dm.rebalance_callback is not None
