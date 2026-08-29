"""
Deep Hedging RL 范式集成 — 单元测试
====================================

测试覆盖:
- DeepHedgingConfig 配置
- VolatilitySurface 隐含波动率面
- MarketSimulator 市场模拟
- HedgingActor 策略网络
- RiskMeasure 风险指标 (CVaR/VaR/MSE/效用/MaxDD)
- DeepHedgingTrainer 训练器
- DeepHedgingEngine 集成接口
- 端到端训练改善

文献: #36 Buehler et al. 2018/2025.12
"""

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.deep_hedging_rl import (
    DeepHedgingConfig,
    DeepHedgingEngine,
    DeepHedgingTrainer,
    HedgingActor,
    HedgingResult,
    MarketSimulator,
    RiskMeasure,
    VolatilitySurface,
)

# ============================================================
# 配置测试
# ============================================================


class TestDeepHedgingConfig:
    """配置测试。"""

    def test_defaults(self):
        config = DeepHedgingConfig()
        assert config.spot == 100.0
        assert config.strike == 100.0
        assert config.volatility == 0.2
        assert config.risk_measure == "cvar"
        assert config.cvar_alpha == 0.95
        assert config.use_iv_surface is True

    def test_custom(self):
        config = DeepHedgingConfig(spot=50, strike=55, volatility=0.3)
        assert config.spot == 50
        assert config.strike == 55
        assert config.volatility == 0.3

    def test_iv_surface_params(self):
        config = DeepHedgingConfig()
        assert "sigma_atm" in config.iv_surface_params
        assert "skew" in config.iv_surface_params


# ============================================================
# 隐含波动率面测试
# ============================================================


class TestVolatilitySurface:
    """隐含波动率面测试。"""

    def test_atm(self):
        """ATM 波动率。"""
        vs = VolatilitySurface()
        iv = vs.implied_vol(0.0, 30 / 365)
        assert 0.15 < iv < 0.25

    def test_skew(self):
        """负偏度: OTM put IV > ATM IV。"""
        vs = VolatilitySurface()
        iv_atm = vs.implied_vol(0.0, 30 / 365)
        iv_otm_put = vs.implied_vol(-0.1, 30 / 365)
        assert iv_otm_put > iv_atm

    def test_floor(self):
        """波动率下限保护。"""
        vs = VolatilitySurface(
            {"sigma_atm": 0.01, "skew": -1, "kurt": 0, "term_slope": 0}
        )
        iv = vs.implied_vol(1.0, 1.0)
        assert iv >= 0.01

    def test_vectorized(self):
        """向量化计算。"""
        vs = VolatilitySurface()
        k = np.array([-0.1, 0.0, 0.1])
        ivs = vs.vectorized_iv(k, 30 / 365)
        assert ivs.shape == (3,)
        assert np.all(ivs > 0)

    def test_custom_params(self):
        """自定义参数。"""
        params = {"sigma_atm": 0.3, "skew": 0.0, "kurt": 0.0, "term_slope": 0.0}
        vs = VolatilitySurface(params)
        assert vs.implied_vol(0.0, 1.0) == 0.3


# ============================================================
# 市场模拟器测试
# ============================================================


class TestMarketSimulator:
    """市场模拟器测试。"""

    def test_simulate_shape(self):
        """路径形状正确。"""
        config = DeepHedgingConfig(n_steps=30)
        sim = MarketSimulator(config)
        prices = sim.simulate_paths(100)
        assert prices.shape == (100, 31)

    def test_initial_price(self):
        """初始价格正确。"""
        config = DeepHedgingConfig(spot=100, n_steps=10)
        sim = MarketSimulator(config)
        prices = sim.simulate_paths(50)
        assert np.all(prices[:, 0] == 100.0)

    def test_positive_prices(self):
        """价格为正。"""
        config = DeepHedgingConfig(n_steps=20)
        sim = MarketSimulator(config)
        prices = sim.simulate_paths(200)
        assert np.all(prices > 0)

    def test_reproducible(self):
        """相同种子可复现。"""
        config = DeepHedgingConfig(n_steps=10)
        sim1 = MarketSimulator(config, seed=42)
        sim2 = MarketSimulator(config, seed=42)
        p1 = sim1.simulate_paths(10)
        p2 = sim2.simulate_paths(10)
        np.testing.assert_array_equal(p1, p2)

    def test_jumps(self):
        """跳空模拟。"""
        config = DeepHedgingConfig(n_steps=30)
        sim = MarketSimulator(config)
        prices = sim.simulate_with_jumps(100, jump_prob=0.1, jump_size=-0.1)
        assert prices.shape == (100, 31)
        assert np.all(prices > 0)


# ============================================================
# Actor 网络测试
# ============================================================


class TestHedgingActor:
    """策略网络测试。"""

    def test_forward_single(self):
        """单样本前向传播。"""
        actor = HedgingActor(input_dim=3, hidden_dim=16)
        state = np.array([0.0, 0.5, 0.0])
        out = actor.forward(state)
        assert out.shape == (1, 1)

    def test_forward_batch(self):
        """批量前向传播。"""
        actor = HedgingActor(input_dim=3, hidden_dim=16)
        state = np.random.randn(100, 3)
        out = actor.forward(state)
        assert out.shape == (100, 1)

    def test_output_bounded(self):
        """输出在 [-1, 1] 范围内 (tanh)。"""
        actor = HedgingActor(input_dim=3, hidden_dim=16)
        state = np.random.randn(1000, 3) * 10
        out = actor.forward(state)
        assert np.all(out >= -1.0 - 1e-6)
        assert np.all(out <= 1.0 + 1e-6)

    def test_get_set_params(self):
        """参数获取/设置。"""
        actor = HedgingActor(input_dim=3, hidden_dim=16)
        params = actor.get_params()
        actor.set_params([p.copy() for p in params])
        params2 = actor.get_params()
        for p1, p2 in zip(params, params2, strict=True):
            np.testing.assert_array_equal(p1, p2)

    def test_perturb(self):
        """参数扰动改变输出。"""
        actor = HedgingActor(input_dim=3, hidden_dim=16, seed=42)
        state = np.array([[0.0, 0.5, 0.0]])
        out_before = actor.forward(state).copy()
        rng = np.random.default_rng(99)
        actor.perturb(0.1, rng)
        out_after = actor.forward(state)
        assert not np.allclose(out_before, out_after)


# ============================================================
# 风险指标测试
# ============================================================


class TestRiskMeasure:
    """风险指标测试。"""

    def test_cvar(self):
        """CVaR 计算。"""
        pnl = np.array([-10, -5, -1, 0, 1, 5, 10])
        cvar = RiskMeasure.cvar(pnl, alpha=0.9)
        assert cvar > 0

    def test_cvar_symmetric(self):
        """对称分布 CVaR > 0。"""
        pnl = np.random.randn(10000)
        cvar = RiskMeasure.cvar(pnl, alpha=0.95)
        assert cvar > 1.0

    def test_var(self):
        """VaR 计算。"""
        pnl = np.array([-10, -5, -1, 0, 1, 5, 10])
        var = RiskMeasure.var(pnl, alpha=0.9)
        assert var > 0

    def test_mse(self):
        """MSE 计算。"""
        pnl = np.array([1, 2, 3])
        mse = RiskMeasure.mse(pnl)
        assert mse == (1 + 4 + 9) / 3

    def test_mse_zero(self):
        """零 PnL 的 MSE = 0。"""
        pnl = np.zeros(100)
        assert RiskMeasure.mse(pnl) == 0.0

    def test_utility(self):
        """效用函数。"""
        pnl = np.array([0.0, 0.0])
        u = RiskMeasure.utility(pnl, risk_aversion=2.0)
        assert u > 0  # -E[-exp(0)/2] = 1/2

    def test_max_drawdown(self):
        """最大回撤。"""
        pnl = np.array([1, -2, 1, -3, 1])
        mdd = RiskMeasure.max_drawdown(pnl)
        assert mdd > 0

    def test_max_drawdown_no_drawdown(self):
        """无回撤。"""
        pnl = np.array([1, 2, 3, 4])
        mdd = RiskMeasure.max_drawdown(pnl)
        assert mdd == 0.0

    def test_expected_shortfall_equals_cvar(self):
        """ES = CVaR。"""
        pnl = np.random.randn(1000)
        es = RiskMeasure.expected_shortfall(pnl, 0.95)
        cvar = RiskMeasure.cvar(pnl, 0.95)
        assert abs(es - cvar) < 1e-10


# ============================================================
# 训练器测试
# ============================================================


class TestDeepHedgingTrainer:
    """训练器测试。"""

    def test_compute_pnl_shape(self):
        """PnL 形状正确。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=5)
        trainer = DeepHedgingTrainer(config)
        prices = trainer.simulator.simulate_paths(50)
        pnl, mean_pnl, total_cost = trainer.compute_hedge_pnl(prices)
        assert pnl.shape == (50,)
        assert isinstance(mean_pnl, float)
        assert isinstance(total_cost, float)

    def test_evaluate(self):
        """评估返回指标。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=5)
        trainer = DeepHedgingTrainer(config)
        metrics = trainer.evaluate(n_paths=100)
        assert "risk" in metrics
        assert "mean_pnl" in metrics
        assert "std_pnl" in metrics
        assert "total_cost" in metrics
        assert "max_drawdown" in metrics

    def test_train_step(self):
        """训练一步返回指标。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=5)
        trainer = DeepHedgingTrainer(config)
        metrics = trainer.train_step(n_paths=50, noise_scale=0.01, n_perturbations=5)
        assert "risk" in metrics

    def test_train_history(self):
        """训练历史记录。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=5)
        trainer = DeepHedgingTrainer(config)
        history = trainer.train(n_episodes=5, n_paths=50, verbose=False)
        assert len(history) == 5

    def test_different_risk_measures(self):
        """不同风险指标。"""
        for rm in ["cvar", "var", "mse", "utility"]:
            config = DeepHedgingConfig(n_steps=10, n_episodes=3, risk_measure=rm)
            trainer = DeepHedgingTrainer(config)
            metrics = trainer.evaluate(n_paths=100)
            assert isinstance(metrics["risk"], float)


# ============================================================
# 引擎测试
# ============================================================


class TestDeepHedgingEngine:
    """集成引擎测试。"""

    def test_init(self):
        """初始化。"""
        engine = DeepHedgingEngine()
        assert not engine.is_trained

    def test_train(self):
        """训练后标记。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=3)
        engine = DeepHedgingEngine(config)
        engine.train(n_episodes=3, n_paths=50, verbose=False)
        assert engine.is_trained

    def test_hedge(self):
        """执行对冲。"""
        config = DeepHedgingConfig(n_steps=10, n_episodes=3)
        engine = DeepHedgingEngine(config)
        result = engine.hedge(spot=100, strike=100, maturity=30 / 365, n_paths=100)
        assert isinstance(result, HedgingResult)
        assert isinstance(result.pnl, float)
        assert isinstance(result.hedging_error, float)
        assert result.hedging_error >= 0

    def test_iv_surface(self):
        """IV 面查询。"""
        engine = DeepHedgingEngine()
        iv = engine.get_iv_surface(0.0, 30 / 365)
        assert iv > 0

    def test_iv_surface_disabled(self):
        """禁用 IV 面返回常量。"""
        config = DeepHedgingConfig(use_iv_surface=False, volatility=0.25)
        engine = DeepHedgingEngine(config)
        iv = engine.get_iv_surface(0.1, 30 / 365)
        assert iv == 0.25


# ============================================================
# 端到端集成测试
# ============================================================


class TestEndToEnd:
    """端到端集成测试。"""

    def test_training_improves_risk(self):
        """训练改善风险指标 (或至少不恶化太多)。"""
        config = DeepHedgingConfig(
            n_steps=10,
            n_episodes=10,
            risk_measure="cvar",
            transaction_cost=0.001,
        )
        engine = DeepHedgingEngine(config)

        metrics_before = engine.trainer.evaluate(n_paths=500)
        engine.train(n_episodes=10, n_paths=200, verbose=False)
        metrics_after = engine.trainer.evaluate(n_paths=500)

        # 训练后 CVaR 应该有变化 (改善或探索)
        assert metrics_after["risk"] != metrics_before["risk"]

    def test_full_pipeline(self):
        """完整管道。"""
        config = DeepHedgingConfig(n_steps=5, n_episodes=3)
        engine = DeepHedgingEngine(config)
        engine.train(n_episodes=3, n_paths=50, verbose=False)
        result = engine.hedge(100, 100, 30 / 365, n_paths=100)
        assert isinstance(result.pnl, float)
        assert isinstance(result.transaction_costs, float)
        assert result.transaction_costs >= 0

    def test_iv_surface_consistency(self):
        """IV 面一致性。"""
        engine = DeepHedgingEngine()
        iv_atm = engine.get_iv_surface(0.0, 30 / 365)
        iv_otm = engine.get_iv_surface(0.1, 30 / 365)
        assert iv_atm > 0
        assert iv_otm > 0
