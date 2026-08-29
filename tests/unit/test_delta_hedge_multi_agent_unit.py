"""
DeltaHedge 多智能体期权优化 — 单元测试
======================================

测试覆盖:
- GreekType / OptionType 枚举
- OptionInstrument 期权工具
- GreeksCalculator BS 希腊字母
- PortfolioGreeks 组合暴露
- HedgingAgent 单智能体对冲
- RLWeightOptimizer RL 权重优化
- MultiAgentCoordinator 多智能体协调
- DeltaHedgeEngine 集成引擎
- 端到端对冲效果 + Beta 对比

文献: #37 PACIS 2025
"""

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.delta_hedge_multi_agent import (
    DeltaHedgeEngine,
    GreeksCalculator,
    GreekType,
    HedgingAgent,
    MultiAgentCoordinator,
    OptionInstrument,
    OptionType,
    PortfolioGreeks,
    RLWeightOptimizer,
)

# ============================================================
# 枚举测试
# ============================================================


class TestEnums:
    """枚举测试。"""

    def test_greek_types(self):
        assert len(GreekType) == 4
        assert GreekType.DELTA.value == "delta"

    def test_option_types(self):
        assert len(OptionType) == 2
        assert OptionType.CALL.value == "call"
        assert OptionType.PUT.value == "put"


# ============================================================
# 期权工具测试
# ============================================================


class TestOptionInstrument:
    """期权工具测试。"""

    def test_create(self):
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        assert opt.option_type == OptionType.CALL
        assert opt.strike == 100

    def test_defaults(self):
        opt = OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2)
        assert opt.underlying == 100.0
        assert opt.price == 0.0


# ============================================================
# 希腊字母计算测试
# ============================================================


class TestGreeksCalculator:
    """BS 希腊字母计算测试。"""

    def test_call_delta_atm(self):
        """ATM call delta ≈ 0.5。"""
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        delta = GreeksCalculator.delta(opt)
        assert 0.4 < delta < 0.6

    def test_put_delta_atm(self):
        """ATM put delta ≈ -0.5。"""
        opt = OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        delta = GreeksCalculator.delta(opt)
        assert -0.6 < delta < -0.4

    def test_call_delta_itm(self):
        """ITM call delta > 0.5。"""
        opt = OptionInstrument(OptionType.CALL, 80, 30 / 365, 0.2, underlying=100)
        delta = GreeksCalculator.delta(opt)
        assert delta > 0.7

    def test_call_delta_otm(self):
        """OTM call delta < 0.5。"""
        opt = OptionInstrument(OptionType.CALL, 120, 30 / 365, 0.2, underlying=100)
        delta = GreeksCalculator.delta(opt)
        assert delta < 0.3

    def test_gamma_positive(self):
        """gamma > 0。"""
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        gamma = GreeksCalculator.gamma(opt)
        assert gamma > 0

    def test_vega_positive(self):
        """vega > 0。"""
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        vega = GreeksCalculator.vega(opt)
        assert vega > 0

    def test_theta_negative(self):
        """call theta 通常 < 0 (时间衰减)。"""
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        theta = GreeksCalculator.theta(opt)
        assert theta < 0

    def test_all_greeks(self):
        """所有希腊字母。"""
        opt = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        greeks = GreeksCalculator.all_greeks(opt)
        assert GreekType.DELTA in greeks
        assert GreekType.GAMMA in greeks
        assert GreekType.VEGA in greeks
        assert GreekType.THETA in greeks

    def test_maturity_zero(self):
        """到期时 delta 为二元。"""
        opt = OptionInstrument(OptionType.CALL, 100, 0, 0.2, underlying=110)
        delta = GreeksCalculator.delta(opt)
        assert delta == 1.0

    def test_put_call_delta_parity(self):
        """put-call parity: delta_call - delta_put = 1。"""
        call = OptionInstrument(OptionType.CALL, 100, 30 / 365, 0.2, underlying=100)
        put = OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        d_call = GreeksCalculator.delta(call)
        d_put = GreeksCalculator.delta(put)
        assert abs(d_call - d_put - 1.0) < 1e-10


# ============================================================
# 组合希腊字母测试
# ============================================================


class TestPortfolioGreeks:
    """组合希腊字母测试。"""

    def test_defaults(self):
        pg = PortfolioGreeks()
        assert pg.delta == 0.0
        assert pg.total_exposure() == 0.0

    def test_get(self):
        pg = PortfolioGreeks(delta=100, gamma=50)
        assert pg.get(GreekType.DELTA) == 100
        assert pg.get(GreekType.GAMMA) == 50

    def test_total_exposure(self):
        pg = PortfolioGreeks(delta=3, gamma=4)
        assert pg.total_exposure() == 5.0  # 3-4-5

    def test_to_dict(self):
        pg = PortfolioGreeks(delta=1, gamma=2, vega=3, theta=4)
        d = pg.to_dict()
        assert d["delta"] == 1
        assert d["theta"] == 4


# ============================================================
# 对冲智能体测试
# ============================================================


class TestHedgingAgent:
    """对冲智能体测试。"""

    def test_no_exposure(self):
        """无暴露时不对冲。"""
        agent = HedgingAgent(GreekType.DELTA)
        action = agent.compute_hedge(0.0, [])
        assert action.quantity == 0.0

    def test_no_instruments(self):
        """无工具时不对冲。"""
        agent = HedgingAgent(GreekType.DELTA)
        action = agent.compute_hedge(100.0, [])
        assert action.instrument is None

    def test_hedge_delta(self):
        """delta 对冲。"""
        agent = HedgingAgent(GreekType.DELTA)
        opt = OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        action = agent.compute_hedge(100.0, [opt])
        assert action.quantity != 0.0
        assert action.exposure_reduced > 0

    def test_action_recorded(self):
        """动作被记录。"""
        agent = HedgingAgent(GreekType.DELTA)
        opt = OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        agent.compute_hedge(100.0, [opt])
        assert len(agent.actions) == 1


# ============================================================
# RL 权重优化器测试
# ============================================================


class TestRLWeightOptimizer:
    """RL 权重优化器测试。"""

    def test_init(self):
        opt = RLWeightOptimizer(n_agents=3)
        assert opt.n_agents == 3
        assert len(opt.weights) == 3
        assert abs(opt.weights.sum() - 1.0) < 1e-10

    def test_update(self):
        """权重更新。"""
        opt = RLWeightOptimizer(n_agents=3)
        residuals = np.array([10, 5, 2])
        opt.update(residuals)
        assert abs(opt.weights.sum() - 1.0) < 1e-10

    def test_update_zero_residuals(self):
        """零残差不变。"""
        opt = RLWeightOptimizer(n_agents=3)
        original = opt.weights.copy()
        opt.update(np.zeros(3))
        np.testing.assert_array_almost_equal(opt.weights, original)

    def test_perturb(self):
        """权重扰动。"""
        opt = RLWeightOptimizer(n_agents=3)

        opt.perturb(noise=0.1)
        assert abs(opt.weights.sum() - 1.0) < 1e-10

    def test_get_weights(self):
        opt = RLWeightOptimizer(n_agents=3)
        w = opt.get_weights()
        assert w.shape == (3,)


# ============================================================
# 多智能体协调器测试
# ============================================================


class TestMultiAgentCoordinator:
    """多智能体协调器测试。"""

    def test_coordinate(self):
        """协调对冲。"""
        coord = MultiAgentCoordinator(use_rl_weights=False)
        pg = PortfolioGreeks(delta=100, gamma=50, vega=20)
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
            OptionInstrument(OptionType.CALL, 105, 30 / 365, 0.2, underlying=100),
        ]
        result = coord.coordinate(pg, instruments)
        assert len(result.actions) == 3  # delta + gamma + vega

    def test_zero_exposure(self):
        """零暴露时无动作。"""
        coord = MultiAgentCoordinator(use_rl_weights=False)
        result = coord.coordinate(PortfolioGreeks(), [])
        assert result.total_reduction == 0.0

    def test_rl_weights(self):
        """RL 权重模式。"""
        coord = MultiAgentCoordinator(use_rl_weights=True)
        pg = PortfolioGreeks(delta=100, gamma=50, vega=20)
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
        ]
        result = coord.coordinate(pg, instruments)
        assert len(result.weights) > 0


# ============================================================
# 引擎测试
# ============================================================


class TestDeltaHedgeEngine:
    """集成引擎测试。"""

    def test_hedge(self):
        """执行对冲。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        pg = PortfolioGreeks(delta=100, gamma=50, vega=20)
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
        ]
        result = engine.hedge(pg, instruments)
        assert result.total_reduction >= 0

    def test_hedge_batch(self):
        """批量对冲。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        exposures = [PortfolioGreeks(delta=100), PortfolioGreeks(gamma=50)]
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        ]
        results = engine.hedge_batch(exposures, instruments)
        assert len(results) == 2

    def test_stats(self):
        """统计信息。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        pg = PortfolioGreeks(delta=100)
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100)
        ]
        engine.hedge(pg, instruments)
        stats = engine.get_stats()
        assert stats["total"] == 1

    def test_compare_beta(self):
        """Beta 对比。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        pg = PortfolioGreeks(delta=100, gamma=50)
        comparison = engine.compare_with_beta_hedge(pg, beta=1.0, index_price=100)
        assert "beta_hedge_quantity" in comparison
        assert "beta_residual_exposure" in comparison


# ============================================================
# 端到端集成测试
# ============================================================


class TestEndToEnd:
    """端到端集成测试。"""

    def test_hedge_reduces_exposure(self):
        """对冲减少暴露。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        pg = PortfolioGreeks(delta=1000, gamma=500, vega=200)
        instruments = [
            OptionInstrument(OptionType.PUT, 95, 30 / 365, 0.2, underlying=100),
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
            OptionInstrument(OptionType.CALL, 105, 30 / 365, 0.2, underlying=100),
        ]
        result = engine.hedge(pg, instruments)
        assert result.total_reduction > 0
        assert result.residual_greeks.total_exposure() < pg.total_exposure()

    def test_multi_agent_better_than_delta_only(self):
        """多智能体优于仅 delta 对冲。"""
        engine = DeltaHedgeEngine(use_rl_weights=False)
        pg = PortfolioGreeks(delta=1000, gamma=500, vega=200)
        instruments = [
            OptionInstrument(OptionType.PUT, 95, 30 / 365, 0.2, underlying=100),
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
            OptionInstrument(OptionType.CALL, 105, 30 / 365, 0.2, underlying=100),
        ]
        result = engine.hedge(pg, instruments)
        # 多智能体应同时减少 gamma/vega 暴露
        assert abs(result.residual_greeks.gamma) < abs(pg.gamma)
        assert abs(result.residual_greeks.vega) < abs(pg.vega)

    def test_rl_weight_evolution(self):
        """RL 权重演化。"""
        engine = DeltaHedgeEngine(use_rl_weights=True)
        pg = PortfolioGreeks(delta=1000, gamma=500, vega=200)
        instruments = [
            OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
        ]
        for _ in range(5):
            engine.hedge(pg, instruments)
        stats = engine.get_stats()
        assert stats["total"] == 5
        assert stats["rl_weights"] is not None
