"""test_etf_option_hedge_rebalancer_unit.py — ETF期权对冲再平衡子模型单元测试

覆盖:
  - 初始化与配置加载
  - 目标权重 (14 ETF, 总权重=1.0)
  - 风险评估
  - 回撤熔断 L0/L1/L2/L3
  - 期权对冲 (认沽保护订单生成)
  - 阈值再平衡
  - 日度再平衡完整流程
  - 压力测试 (6场景, 含对冲后净回撤)
  - 回撤熔断触发时再平衡跳过
"""
from __future__ import annotations

import pytest

from etf_option_hedge_rebalancer import (
    DailyPlan,
    ETFOptionHedgeRebalancer,
    RiskState,
)


@pytest.fixture(scope="module")
def rebalancer():
    r = ETFOptionHedgeRebalancer()
    r.config["etf_flow_adjustment"]["enabled"] = False
    r.config["alpha_enhancement"]["enabled"] = False
    r.portfolio_optimizer = None
    return r


@pytest.fixture(scope="module")
def target_weights(rebalancer):
    return rebalancer.get_target_weights()


@pytest.fixture(scope="module")
def prices():
    return {
        "510300.SH": 3.85, "510500.SH": 5.62, "510050.SH": 2.95, "512100.SH": 2.65,
        "588000.SH": 1.32, "159915.SZ": 1.98, "512480.SH": 1.45, "512010.SH": 0.55,
        "512660.SH": 1.15, "515170.SH": 1.85, "159939.SZ": 0.95, "518880.SH": 5.42,
        "511260.SH": 1.18, "510310.SH": 2.85,
    }


@pytest.fixture
def positions(rebalancer, target_weights, prices):
    pos = {}
    for code, w in target_weights.items():
        px = prices.get(code, 4.0)
        shares = int(w * rebalancer.portfolio_value / px / 100) * 100
        pos[code] = {"shares": shares, "name": code, "category": "宽基", "daily_return": 0.001}
    return pos


class TestInitialization:
    @pytest.mark.unit
    def test_portfolio_value(self, rebalancer):
        assert rebalancer.portfolio_value == 2_000_000

    @pytest.mark.unit
    def test_target_annual_return(self, rebalancer):
        assert rebalancer.target_annual_return == 0.08

    @pytest.mark.unit
    def test_target_max_drawdown(self, rebalancer):
        assert rebalancer.target_max_drawdown == 0.15

    @pytest.mark.unit
    def test_drawdown_breaker_loaded(self, rebalancer):
        assert rebalancer.drawdown_breaker is not None

    @pytest.mark.unit
    def test_put_engine_loaded(self, rebalancer):
        assert rebalancer.put_engine is not None


class TestTargetWeights:
    @pytest.mark.unit
    def test_etf_count(self, target_weights):
        assert len(target_weights) == 14

    @pytest.mark.unit
    def test_total_weight(self, target_weights):
        assert abs(sum(target_weights.values()) - 1.0) < 0.01

    @pytest.mark.unit
    def test_broad_based_weights(self, target_weights):
        broad = ["510300.SH", "510500.SH", "510050.SH", "512100.SH", "588000.SH", "159915.SZ"]
        total_broad = sum(target_weights[c] for c in broad)
        assert abs(total_broad - 0.60) < 0.01

    @pytest.mark.unit
    def test_defense_weights(self, target_weights):
        defense = ["518880.SH", "511260.SH", "510310.SH"]
        total_def = sum(target_weights[c] for c in defense)
        assert abs(total_def - 0.15) < 0.01

    @pytest.mark.unit
    def test_all_positive(self, target_weights):
        for code, w in target_weights.items():
            assert w > 0, f"{code} weight should be positive"


class TestRiskAssessment:
    @pytest.mark.unit
    def test_risk_state(self, rebalancer, positions, prices):
        risk = rebalancer.assess_risk(positions, prices, current_drawdown=-0.03)
        assert isinstance(risk, RiskState)
        assert risk.portfolio_value > 0
        assert risk.current_drawdown == -0.03

    @pytest.mark.unit
    def test_max_single_weight(self, rebalancer, positions, prices):
        risk = rebalancer.assess_risk(positions, prices)
        assert 0 < risk.max_single_weight < 0.25

    @pytest.mark.unit
    def test_empty_positions(self, rebalancer):
        risk = rebalancer.assess_risk({}, {}, current_drawdown=0.0)
        assert risk.portfolio_value == 0.0


class TestDrawdownCircuitBreaker:
    @pytest.mark.unit
    def test_normal(self, rebalancer):
        dec = rebalancer.check_drawdown_circuit(-0.03)
        assert dec is not None
        assert dec.allow_new_buy is True

    @pytest.mark.unit
    def test_reduce_level(self, rebalancer):
        dec = rebalancer.check_drawdown_circuit(-0.10)
        assert dec is not None
        assert dec.allow_new_buy is False

    @pytest.mark.unit
    def test_force_hedge_level(self, rebalancer):
        dec = rebalancer.check_drawdown_circuit(-0.13)
        assert dec is not None
        assert dec.allow_new_buy is False

    @pytest.mark.unit
    def test_halt_level(self, rebalancer):
        dec = rebalancer.check_drawdown_circuit(-0.16)
        assert dec is not None
        assert dec.allow_new_buy is False

    @pytest.mark.unit
    def test_zero_drawdown(self, rebalancer):
        dec = rebalancer.check_drawdown_circuit(0.0)
        assert dec is not None
        assert dec.allow_new_buy is True


class TestOptionHedge:
    @pytest.mark.unit
    def test_generate_orders(self, rebalancer):
        result = rebalancer.decide_option_hedge(drawdown_level=0)
        assert result["enabled"] is True
        assert "put_orders" in result

    @pytest.mark.unit
    def test_drawdown_level_amplifies(self, rebalancer):
        normal = rebalancer.decide_option_hedge(drawdown_level=0)
        escalated = rebalancer.decide_option_hedge(drawdown_level=3)
        normal_premium = normal.get("total_premium_est", 0)
        escalated_premium = escalated.get("total_premium_est", 0)
        if normal_premium > 0 and escalated_premium > 0:
            assert escalated_premium >= normal_premium


class TestRebalance:
    @pytest.mark.unit
    def test_no_rebalance_when_aligned(self, rebalancer, positions, target_weights, prices):
        orders = rebalancer.check_rebalance(positions, target_weights, prices)
        assert len(orders) == 0

    @pytest.mark.unit
    def test_rebalance_triggered(self, rebalancer, positions, target_weights, prices):
        deviated = dict(positions)
        code = "510300.SH"
        deviated[code] = dict(positions[code])
        deviated[code]["shares"] = int(positions[code]["shares"] * 1.60)
        orders = rebalancer.check_rebalance(deviated, target_weights, prices)
        assert len(orders) > 0
        assert any(o["code"] == code for o in orders)

    @pytest.mark.unit
    def test_empty_positions(self, rebalancer, target_weights, prices):
        orders = rebalancer.check_rebalance({}, target_weights, prices)
        assert orders == []


class TestDailyRebalance:
    @pytest.mark.unit
    def test_normal_flow(self, rebalancer, positions, prices):
        plan = rebalancer.run_daily_rebalance(positions, prices, "2026-08-20", current_drawdown=-0.03)
        assert isinstance(plan, DailyPlan)
        assert plan.trade_date == "2026-08-20"
        assert plan.risk_state is not None
        assert plan.drawdown_decision is not None

    @pytest.mark.unit
    def test_halt_skips_rebalance(self, rebalancer, positions, prices):
        plan = rebalancer.run_daily_rebalance(positions, prices, "2026-08-20", current_drawdown=-0.16)
        assert len(plan.rebalance_orders) == 0
        assert "HALT" in plan.execution_summary or "熔断" in plan.execution_summary
        assert len(plan.warning_flags) > 0

    @pytest.mark.unit
    def test_option_hedge_always_present(self, rebalancer, positions, prices):
        plan = rebalancer.run_daily_rebalance(positions, prices, "2026-08-20", current_drawdown=0.0)
        assert plan.option_hedge.get("enabled") is True


class TestStressTests:
    @pytest.mark.unit
    def test_six_scenarios(self, rebalancer, positions, prices):
        result = rebalancer.run_stress_tests(positions, prices)
        assert result["total_scenarios"] == 6

    @pytest.mark.unit
    def test_raw_breach_count(self, rebalancer, positions, prices):
        result = rebalancer.run_stress_tests(positions, prices)
        assert result["breach_count"] == 6

    @pytest.mark.unit
    def test_hedged_reduces_breach(self, rebalancer, positions, prices):
        result = rebalancer.run_stress_tests(positions, prices)
        assert result["breach_count_hedged"] < result["breach_count"]

    @pytest.mark.unit
    def test_hedge_coverage_positive(self, rebalancer, positions, prices):
        result = rebalancer.run_stress_tests(positions, prices)
        assert result["hedge_coverage"] > 0

    @pytest.mark.unit
    def test_scenario_has_hedged_drawdown(self, rebalancer, positions, prices):
        result = rebalancer.run_stress_tests(positions, prices)
        for name, scenario in result["scenarios"].items():
            assert "drawdown_hedged_pct" in scenario
            assert scenario["drawdown_hedged_pct"] >= 0