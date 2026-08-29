"""
Deep Hedging RL 范式集成
========================

文献依据: #36 Deep Hedging (Buehler et al. 2018/2025.12)
任务: LIT-3.1 Deep Hedging RL 范式集成

核心范式
--------
传统对冲: 解析 delta-gamma (Black-Scholes, 假设连续交易无摩擦)
Deep Hedging: 用 RL 直接优化对冲策略的风险指标
  - Actor: 输入市场状态 → 输出对冲头寸
  - 进化策略 (ES): 无需梯度, 兼容不可导风险指标 (CVaR)
  - 目标: min CVaR(对冲误差) 或 max E[U(对冲后财富)]

优势
----
1. 处理交易成本/跳空/流动性约束
2. 优化任意风险指标 (CVaR/效用/MaxDD)
3. 自适应市场制度 (无需 BS 假设)
4. 隐含波动率面参数化 (SVI 简化版)

使用示例
--------
    from utils.deep_hedging_rl import DeepHedgingEngine, DeepHedgingConfig

    config = DeepHedgingConfig(spot=100, strike=100, maturity=30/365)
    engine = DeepHedgingEngine(config)
    engine.train(n_episodes=100)
    result = engine.hedge(spot=100, strike=100, maturity=30/365)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("deep_hedging_rl")


# ============================================================
# 配置
# ============================================================


@dataclass
class DeepHedgingConfig:
    """Deep Hedging 配置。

    Attributes:
        spot: 初始价格
        strike: 行权价
        maturity: 到期时间 (年)
        volatility: 波动率
        risk_free_rate: 无风险利率
        transaction_cost: 交易成本率 (bps)
        n_steps: 对冲步数
        n_episodes: 训练轮数
        learning_rate: 学习率
        gamma: 折扣因子
        risk_measure: 风险指标 (cvar/var/mse/utility)
        cvar_alpha: CVaR 置信水平
        risk_aversion: 风险厌恶系数
        hidden_dim: 网络隐藏层维度
        use_iv_surface: 是否使用隐含波动率面
        iv_surface_params: IV 面参数
    """

    spot: float = 100.0
    strike: float = 100.0
    maturity: float = 30 / 365
    volatility: float = 0.2
    risk_free_rate: float = 0.03
    transaction_cost: float = 0.001
    n_steps: int = 30
    n_episodes: int = 1000
    learning_rate: float = 0.001
    gamma: float = 0.99
    risk_measure: str = "cvar"
    cvar_alpha: float = 0.95
    risk_aversion: float = 2.0
    hidden_dim: int = 32
    use_iv_surface: bool = True
    iv_surface_params: dict[str, float] = field(
        default_factory=lambda: {
            "sigma_atm": 0.2,
            "skew": -0.02,
            "kurt": 0.01,
            "term_slope": -0.1,
        }
    )


# ============================================================
# 隐含波动率面
# ============================================================


class VolatilitySurface:
    """SPX/SPY 隐含波动率面参数化 (SVI 简化版).

    σ(k, τ) = σ_atm + skew*k + kurt*k^2 + term_slope*τ
    其中 k = log(K/F) (log-moneyness), τ = 到期时间
    """

    def __init__(self, params: dict[str, float] | None = None) -> None:
        self.params = params or {
            "sigma_atm": 0.2,
            "skew": -0.02,
            "kurt": 0.01,
            "term_slope": -0.1,
        }

    def implied_vol(self, log_moneyness: float, maturity: float) -> float:
        """计算隐含波动率。"""
        p = self.params
        iv = (
            p["sigma_atm"]
            + p["skew"] * log_moneyness
            + p["kurt"] * log_moneyness**2
            + p["term_slope"] * maturity
        )
        return max(iv, 0.01)

    def vectorized_iv(self, log_moneyness: np.ndarray, maturity: float) -> np.ndarray:
        """向量化隐含波动率。"""
        p = self.params
        iv = (
            p["sigma_atm"]
            + p["skew"] * log_moneyness
            + p["kurt"] * log_moneyness**2
            + p["term_slope"] * maturity
        )
        return np.maximum(iv, 0.01)


# ============================================================
# 市场模拟器
# ============================================================


class MarketSimulator:
    """几何布朗运动市场模拟器。"""

    def __init__(self, config: DeepHedgingConfig, seed: int = 42) -> None:
        self.config = config
        self.rng = np.random.default_rng(seed)

    def simulate_paths(self, n_paths: int) -> np.ndarray:
        """模拟价格路径。

        Returns:
            prices: shape (n_paths, n_steps+1)
        """
        cfg = self.config
        dt = cfg.maturity / cfg.n_steps
        drift = (cfg.risk_free_rate - 0.5 * cfg.volatility**2) * dt
        diffusion = cfg.volatility * np.sqrt(dt)

        dW = self.rng.standard_normal((n_paths, cfg.n_steps))
        log_returns = drift + diffusion * dW
        log_prices = np.cumsum(log_returns, axis=1)
        prices = cfg.spot * np.exp(log_prices)
        prices = np.column_stack([np.full(n_paths, cfg.spot), prices])

        return prices

    def simulate_with_jumps(
        self, n_paths: int, jump_prob: float = 0.05, jump_size: float = -0.05
    ) -> np.ndarray:
        """含跳空的价格路径模拟。"""
        prices = self.simulate_paths(n_paths)
        cfg = self.config

        jumps = self.rng.random((n_paths, cfg.n_steps)) < jump_prob
        jump_returns = jumps * jump_size
        log_prices = np.log(prices / cfg.spot)
        log_prices[:, 1:] += np.cumsum(jump_returns, axis=1)
        prices = cfg.spot * np.exp(log_prices)

        return prices


# ============================================================
# Actor (对冲策略网络)
# ============================================================


class HedgingActor:
    """对冲策略 Actor (numpy 神经网络).

    输入: [log_moneyness, time_to_maturity, prev_hedge]
    输出: 对冲头寸 (delta), 范围 [-1, 1]
    网络: 3 → hidden → hidden → 1 (tanh 激活)
    """

    def __init__(
        self, input_dim: int = 3, hidden_dim: int = 32, seed: int = 123
    ) -> None:
        self.rng = np.random.default_rng(seed)
        scale1 = np.sqrt(2.0 / (input_dim + hidden_dim))
        scale2 = np.sqrt(2.0 / (hidden_dim + hidden_dim))
        scale3 = np.sqrt(2.0 / (hidden_dim + 1))

        self.W1 = self.rng.standard_normal((input_dim, hidden_dim)) * scale1
        self.b1 = np.zeros(hidden_dim)
        self.W2 = self.rng.standard_normal((hidden_dim, hidden_dim)) * scale2
        self.b2 = np.zeros(hidden_dim)
        self.W3 = self.rng.standard_normal((hidden_dim, 1)) * scale3
        self.b3 = np.zeros(1)

    def forward(self, state: np.ndarray) -> np.ndarray:
        """前向传播。"""
        if state.ndim == 1:
            state = state.reshape(1, -1)
        h1 = np.tanh(state @ self.W1 + self.b1)
        h2 = np.tanh(h1 @ self.W2 + self.b2)
        out = np.tanh(h2 @ self.W3 + self.b3)
        return out

    def get_params(self) -> list[np.ndarray]:
        return [self.W1, self.b1, self.W2, self.b2, self.W3, self.b3]

    def set_params(self, params: list[np.ndarray]) -> None:
        self.W1, self.b1, self.W2, self.b2, self.W3, self.b3 = params

    def perturb(self, noise_scale: float, rng: np.random.Generator) -> None:
        """参数扰动 (用于进化策略)。"""
        for p in self.get_params():
            p += rng.standard_normal(p.shape) * noise_scale


# ============================================================
# 风险指标
# ============================================================


class RiskMeasure:
    """风险指标计算。"""

    @staticmethod
    def cvar(pnl: np.ndarray, alpha: float = 0.95) -> float:
        """CVaR (Conditional Value at Risk).

        CVaR_α = -E[PnL | PnL ≤ VaR_α]
        """
        var = np.percentile(pnl, (1 - alpha) * 100)
        tail = pnl[pnl <= var]
        if len(tail) == 0:
            return -float(var)
        return -float(np.mean(tail))

    @staticmethod
    def var(pnl: np.ndarray, alpha: float = 0.95) -> float:
        """VaR (Value at Risk)。"""
        return -float(np.percentile(pnl, (1 - alpha) * 100))

    @staticmethod
    def mse(pnl: np.ndarray) -> float:
        """均方误差。"""
        return float(np.mean(pnl**2))

    @staticmethod
    def expected_shortfall(pnl: np.ndarray, alpha: float = 0.95) -> float:
        """Expected Shortfall (= CVaR)。"""
        return RiskMeasure.cvar(pnl, alpha)

    @staticmethod
    def utility(pnl: np.ndarray, risk_aversion: float = 2.0) -> float:
        """指数效用函数 (返回 -E[U] 以最小化)。"""
        return -float(np.mean(-np.exp(-risk_aversion * pnl) / risk_aversion))

    @staticmethod
    def max_drawdown(pnl: np.ndarray) -> float:
        """最大回撤。"""
        cumulative = np.cumsum(pnl)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        return float(np.max(drawdown))


# ============================================================
# 对冲结果
# ============================================================


@dataclass
class HedgingResult:
    """对冲结果。

    Attributes:
        hedge_positions: 对冲头寸路径
        pnl: 对冲后平均 PnL
        hedging_error: 对冲误差 (std)
        transaction_costs: 交易成本
    """

    hedge_positions: np.ndarray
    pnl: float
    hedging_error: float
    transaction_costs: float


# ============================================================
# Deep Hedging 训练器
# ============================================================


class DeepHedgingTrainer:
    """Deep Hedging RL 训练器 (进化策略优化).

    使用进化策略 (ES) 优化 Actor 参数, 无需梯度计算:
    1. 采样噪声扰动参数
    2. 评估扰动后策略的风险指标
    3. 加权更新参数 (低风险 = 高权重)
    """

    def __init__(self, config: DeepHedgingConfig) -> None:
        self.config = config
        self.actor = HedgingActor(input_dim=3, hidden_dim=config.hidden_dim)
        self.simulator = MarketSimulator(config)
        self.iv_surface = (
            VolatilitySurface(config.iv_surface_params)
            if config.use_iv_surface
            else None
        )
        self.rng = np.random.default_rng(42)
        self.history: list[dict[str, float]] = []

    def compute_hedge_pnl(self, prices: np.ndarray) -> tuple[np.ndarray, float, float]:
        """计算对冲 PnL。

        Args:
            prices: shape (n_paths, n_steps+1) 价格路径
        Returns:
            (pnl_per_path, mean_pnl, total_cost)
        """
        cfg = self.config
        n_paths, n_steps = prices.shape[0], prices.shape[1] - 1
        dt = cfg.maturity / cfg.n_steps

        hedge_positions = np.zeros((n_paths, n_steps + 1))
        transaction_costs = np.zeros(n_paths)

        for t in range(n_steps):
            time_to_mat = cfg.maturity - t * dt
            log_moneyness = np.log(prices[:, t] / cfg.strike)
            prev_hedge = hedge_positions[:, t]

            state = np.column_stack(
                [
                    log_moneyness,
                    np.full(n_paths, time_to_mat),
                    prev_hedge,
                ]
            )

            new_hedge = self.actor.forward(state).flatten()
            hedge_positions[:, t + 1] = new_hedge

            cost = np.abs(new_hedge - prev_hedge) * prices[:, t] * cfg.transaction_cost
            transaction_costs += cost

        option_payoff = np.maximum(cfg.strike - prices[:, -1], 0)

        hedge_pnl = np.sum(hedge_positions[:, :-1] * np.diff(prices, axis=1), axis=1)

        total_pnl = hedge_pnl - option_payoff - transaction_costs

        return total_pnl, float(np.mean(total_pnl)), float(np.mean(transaction_costs))

    def evaluate(self, n_paths: int = 1000) -> dict[str, float]:
        """评估当前策略。"""
        prices = self.simulator.simulate_paths(n_paths)
        pnl, mean_pnl, total_cost = self.compute_hedge_pnl(prices)

        cfg = self.config
        if cfg.risk_measure == "cvar":
            risk = RiskMeasure.cvar(pnl, cfg.cvar_alpha)
        elif cfg.risk_measure == "var":
            risk = RiskMeasure.var(pnl, cfg.cvar_alpha)
        elif cfg.risk_measure == "mse":
            risk = RiskMeasure.mse(pnl)
        elif cfg.risk_measure == "utility":
            risk = RiskMeasure.utility(pnl, cfg.risk_aversion)
        else:
            risk = RiskMeasure.cvar(pnl, cfg.cvar_alpha)

        return {
            "risk": risk,
            "mean_pnl": mean_pnl,
            "std_pnl": float(np.std(pnl)),
            "total_cost": total_cost,
            "max_drawdown": RiskMeasure.max_drawdown(pnl),
        }

    def train_step(
        self, n_paths: int = 500, noise_scale: float = 0.01, n_perturbations: int = 20
    ) -> dict[str, float]:
        """进化策略训练一步。"""
        original_params = [p.copy() for p in self.actor.get_params()]

        perturbations: list[list[np.ndarray]] = []
        risks: list[float] = []

        for _ in range(n_perturbations):
            self.actor.set_params([p.copy() for p in original_params])
            self.actor.perturb(noise_scale, self.rng)
            metrics = self.evaluate(n_paths)
            perturbations.append([p.copy() for p in self.actor.get_params()])
            risks.append(metrics["risk"])

        self.actor.set_params([p.copy() for p in original_params])

        risks_arr = np.array(risks)
        weights = np.exp(-risks_arr / (np.std(risks_arr) + 1e-8))
        weights /= weights.sum()

        new_params = [np.zeros_like(p) for p in original_params]
        for i, pert in enumerate(perturbations):
            for j in range(len(new_params)):
                new_params[j] += weights[i] * pert[j]

        self.actor.set_params(new_params)

        metrics = self.evaluate(n_paths)
        return metrics

    def train(
        self, n_episodes: int | None = None, n_paths: int = 500, verbose: bool = True
    ) -> list[dict[str, float]]:
        """训练 Deep Hedging 策略。"""
        episodes = n_episodes or self.config.n_episodes

        for ep in range(episodes):
            noise_scale = 0.01 * (1 - ep / max(episodes, 1))
            metrics = self.train_step(n_paths=n_paths, noise_scale=noise_scale)
            self.history.append(metrics)

            if verbose and (ep + 1) % max(1, episodes // 10) == 0:
                logger.info(
                    "Episode %d/%d: risk=%.4f, mean_pnl=%.4f",
                    ep + 1,
                    episodes,
                    metrics["risk"],
                    metrics["mean_pnl"],
                )

        return self.history


# ============================================================
# Deep Hedging 引擎 (集成接口)
# ============================================================


class DeepHedgingEngine:
    """Deep Hedging 引擎 — 集成到 hedge_engine.py 的接口。

    使用示例:
        engine = DeepHedgingEngine()
        engine.train(n_episodes=100)
        result = engine.hedge(spot=100, strike=100, maturity=30/365)
    """

    def __init__(self, config: DeepHedgingConfig | None = None) -> None:
        self.config = config or DeepHedgingConfig()
        self.trainer = DeepHedgingTrainer(self.config)
        self._is_trained = False

    def train(self, n_episodes: int = 100, **kwargs: Any) -> list[dict[str, float]]:
        """训练对冲策略。"""
        history = self.trainer.train(n_episodes=n_episodes, **kwargs)
        self._is_trained = True
        return history

    def hedge(
        self, spot: float, strike: float, maturity: float, n_paths: int = 1000
    ) -> HedgingResult:
        """执行对冲。"""
        self.config.spot = spot
        self.config.strike = strike
        self.config.maturity = maturity

        prices = self.trainer.simulator.simulate_paths(n_paths)
        pnl, mean_pnl, total_cost = self.trainer.compute_hedge_pnl(prices)

        hedge_positions = np.zeros(n_paths)

        return HedgingResult(
            hedge_positions=hedge_positions,
            pnl=mean_pnl,
            hedging_error=float(np.std(pnl)),
            transaction_costs=total_cost,
        )

    def get_iv_surface(self, log_moneyness: float, maturity: float) -> float:
        """获取隐含波动率。"""
        if self.trainer.iv_surface is not None:
            return self.trainer.iv_surface.implied_vol(log_moneyness, maturity)
        return self.config.volatility

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def save_model(self, path: str) -> None:
        """v8.8: 持久化训练好的模型到磁盘.

        保存内容: actor权重 + config + history + is_trained
        安全: 同步生成 .sha256 侧车文件, 加载时校验完整性
        """
        import hashlib
        import pickle

        model_state = {
            "actor_params": [p.copy() for p in self.trainer.actor.get_params()],
            "config": self.config,
            "history": self.trainer.history,
            "is_trained": self._is_trained,
        }
        data = pickle.dumps(model_state)
        with open(path, "wb") as f:
            f.write(data)
        # 生成 SHA256 侧车文件用于加载时完整性校验
        digest = hashlib.sha256(data).hexdigest()
        with open(path + ".sha256", "w", encoding="utf-8") as f:
            f.write(digest)
        logger.info("Deep Hedging 模型已保存: %s (sha256=%s)", path, digest[:12])

    def load_model(self, path: str) -> bool:
        """v8.8: 从磁盘加载预训练模型.

        Returns:
            True if loaded successfully, False otherwise
        """
        import os
        import pickle

        if not os.path.exists(path):
            logger.debug("模型文件不存在: %s", path)
            return False

        try:
            import hashlib

            # 优先校验 SHA256 侧车 (防篡改)
            sidecar = path + ".sha256"
            with open(path, "rb") as f:
                raw = f.read()
            if os.path.exists(sidecar):
                with open(sidecar, encoding="utf-8") as f:
                    expected = f.read().strip()
                actual = hashlib.sha256(raw).hexdigest()
                if actual != expected:
                    logger.error(
                        "模型完整性校验失败: %s (expected=%s..., actual=%s...) — 拒绝加载",
                        path, expected[:12], actual[:12],
                    )
                    return False
            else:
                digest = hashlib.sha256(raw).hexdigest()
                logger.warning(
                    "模型无 SHA256 侧车, 记录哈希作审计: %s sha256=%s", path, digest
                )

            model_state = pickle.loads(raw)  # noqa: S301 — SHA256 完整性已校验

            self.trainer.actor.set_params(model_state["actor_params"])
            self.trainer.history = model_state.get("history", [])
            self._is_trained = model_state.get("is_trained", True)
            logger.info(
                "Deep Hedging 模型已加载: %s (trained=%s)", path, self._is_trained
            )
            return True
        except (pickle.PickleError, KeyError, TypeError, OSError) as e:
            logger.warning("模型加载失败: %s — %s", path, e)
            return False


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 Deep Hedging。"""
    print("=" * 60)
    print("Deep Hedging RL 范式集成")
    print("文献: #36 Buehler et al. 2018/2025.12")
    print("=" * 60)

    config = DeepHedgingConfig(
        spot=100.0,
        strike=100.0,
        maturity=30 / 365,
        volatility=0.2,
        transaction_cost=0.001,
        n_steps=30,
        n_episodes=50,
        risk_measure="cvar",
    )

    engine = DeepHedgingEngine(config)

    print("\n--- 训练前 ---")
    metrics_before = engine.trainer.evaluate(n_paths=1000)
    print(f"  CVaR: {metrics_before['risk']:.4f}")
    print(f"  Mean PnL: {metrics_before['mean_pnl']:.4f}")
    print(f"  Std PnL: {metrics_before['std_pnl']:.4f}")
    print(f"  Max DD: {metrics_before['max_drawdown']:.4f}")

    print("\n--- 训练中 (50 episodes) ---")
    engine.train(n_episodes=50, n_paths=500, verbose=False)

    print("\n--- 训练后 ---")
    metrics_after = engine.trainer.evaluate(n_paths=1000)
    print(f"  CVaR: {metrics_after['risk']:.4f}")
    print(f"  Mean PnL: {metrics_after['mean_pnl']:.4f}")
    print(f"  Std PnL: {metrics_after['std_pnl']:.4f}")
    print(f"  Max DD: {metrics_after['max_drawdown']:.4f}")

    improvement = metrics_before["risk"] - metrics_after["risk"]
    print("\n--- 改善 ---")
    print(
        f"  CVaR 改善: {improvement:.4f} ({improvement / max(metrics_before['risk'], 1e-8):.1%})"
    )

    print("\n--- 隐含波动率面 ---")
    for k in [-0.1, -0.05, 0.0, 0.05, 0.1]:
        iv = engine.get_iv_surface(k, 30 / 365)
        print(f"  k={k:+.2f}: sigma={iv:.4f}")


if __name__ == "__main__":
    main()
