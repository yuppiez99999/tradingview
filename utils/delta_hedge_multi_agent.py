"""
DeltaHedge 多智能体期权优化
==========================

文献依据: #37 PACIS 2025 — Multi-Agent Delta Hedging
任务: LIT-3.2 DeltaHedge 多智能体期权优化

核心范式
--------
传统对冲: 纯 Beta 加权 (期货/指数对冲, 仅 delta)
多智能体对冲: 多个智能体分别对冲不同希腊字母
  - DeltaAgent: 中和 delta 风险 (价格变动)
  - GammaAgent: 中和 gamma 风险 (凸性)
  - VegaAgent: 中和 vega 风险 (波动率变动)
  - 多智能体协调避免对冲冲突
  - RL 优化各智能体权重分配

优势
----
1. 超越纯 Beta 加权 (同时对冲多个希腊字母)
2. 期权作为对冲工具 (不仅仅是期货/指数)
3. 多智能体协调避免过度对冲
4. RL 自适应权重 (市场制度感知)

使用示例
--------
    from utils.delta_hedge_multi_agent import DeltaHedgeEngine

    engine = DeltaHedgeEngine()
    result = engine.hedge(portfolio_greeks, option_chain)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("delta_hedge_multi_agent")


# ============================================================
# 枚举
# ============================================================

class GreekType(str, Enum):
    """希腊字母类型。"""
    DELTA = "delta"
    GAMMA = "gamma"
    VEGA = "vega"
    THETA = "theta"


class OptionType(str, Enum):
    """期权类型。"""
    CALL = "call"
    PUT = "put"


# ============================================================
# 期权工具
# ============================================================

@dataclass
class OptionInstrument:
    """期权工具描述。

    Attributes:
        option_type: 期权类型 (call/put)
        strike: 行权价
        maturity: 到期时间 (年)
        iv: 隐含波动率
        price: 期权价格
        underlying: 标的价格
    """
    option_type: OptionType
    strike: float
    maturity: float
    iv: float
    price: float = 0.0
    underlying: float = 100.0


# ============================================================
# 希腊字母计算 (Black-Scholes)
# ============================================================

class GreeksCalculator:
    """Black-Scholes 希腊字母计算器。"""

    @staticmethod
    def _d1(spot: float, strike: float, maturity: float,
            vol: float, rate: float = 0.03) -> float:
        if maturity <= 0 or vol <= 0:
            return 0.0
        return (
            np.log(spot / strike) + (rate + 0.5 * vol ** 2) * maturity
        ) / (vol * np.sqrt(maturity))

    @staticmethod
    def _d2(d1: float, vol: float, maturity: float) -> float:
        return d1 - vol * np.sqrt(maturity)

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def _norm_pdf(x: float) -> float:
        return np.exp(-0.5 * x ** 2) / np.sqrt(2 * np.pi)

    @classmethod
    def delta(cls, option: OptionInstrument, rate: float = 0.03) -> float:
        """计算 delta。"""
        if option.maturity <= 0:
            if option.option_type == OptionType.CALL:
                return 1.0 if option.underlying > option.strike else 0.0
            return 0.0 if option.underlying > option.strike else -1.0

        d1 = cls._d1(option.underlying, option.strike, option.maturity,
                     option.iv, rate)
        if option.option_type == OptionType.CALL:
            return cls._norm_cdf(d1)
        return cls._norm_cdf(d1) - 1.0

    @classmethod
    def gamma(cls, option: OptionInstrument, rate: float = 0.03) -> float:
        """计算 gamma。"""
        if option.maturity <= 0 or option.iv <= 0:
            return 0.0
        d1 = cls._d1(option.underlying, option.strike, option.maturity,
                     option.iv, rate)
        return cls._norm_pdf(d1) / (
            option.underlying * option.iv * np.sqrt(option.maturity)
        )

    @classmethod
    def vega(cls, option: OptionInstrument, rate: float = 0.03) -> float:
        """计算 vega。"""
        if option.maturity <= 0 or option.iv <= 0:
            return 0.0
        d1 = cls._d1(option.underlying, option.strike, option.maturity,
                     option.iv, rate)
        return option.underlying * cls._norm_pdf(d1) * np.sqrt(option.maturity)

    @classmethod
    def theta(cls, option: OptionInstrument, rate: float = 0.03) -> float:
        """计算 theta。"""
        if option.maturity <= 0 or option.iv <= 0:
            return 0.0
        d1 = cls._d1(option.underlying, option.strike, option.maturity,
                     option.iv, rate)
        d2 = cls._d2(d1, option.iv, option.maturity)

        first = -(
            option.underlying * cls._norm_pdf(d1) * option.iv
        ) / (2 * np.sqrt(option.maturity))

        if option.option_type == OptionType.CALL:
            second = -rate * option.strike * np.exp(-rate * option.maturity) * cls._norm_cdf(d2)
        else:
            second = rate * option.strike * np.exp(-rate * option.maturity) * cls._norm_cdf(-d2)

        return first + second

    @classmethod
    def all_greeks(cls, option: OptionInstrument, rate: float = 0.03) -> dict[GreekType, float]:
        """计算所有希腊字母。"""
        return {
            GreekType.DELTA: cls.delta(option, rate),
            GreekType.GAMMA: cls.gamma(option, rate),
            GreekType.VEGA: cls.vega(option, rate),
            GreekType.THETA: cls.theta(option, rate),
        }


# ============================================================
# 组合希腊字母
# ============================================================

@dataclass
class PortfolioGreeks:
    """组合希腊字母暴露。

    Attributes:
        delta: 组合 delta 暴露
        gamma: 组合 gamma 暴露
        vega: 组合 vega 暴露
        theta: 组合 theta 暴露
    """
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0

    def get(self, greek: GreekType) -> float:
        return getattr(self, greek.value)

    def total_exposure(self) -> float:
        """总暴露 (L2 范数)。"""
        return np.sqrt(self.delta ** 2 + self.gamma ** 2 + self.vega ** 2)

    def to_dict(self) -> dict[str, float]:
        return {
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
        }


# ============================================================
# 对冲智能体
# ============================================================

@dataclass
class HedgeAction:
    """对冲动作。

    Attributes:
        instrument: 对冲工具
        quantity: 对冲数量
        greek_target: 目标希腊字母
        exposure_reduced: 减少的暴露
    """
    instrument: OptionInstrument | None
    quantity: float
    greek_target: GreekType
    exposure_reduced: float = 0.0


class HedgingAgent:
    """单个希腊字母对冲智能体。

    负责中和一种希腊字母的暴露。
    """

    def __init__(self, greek_type: GreekType, weight: float = 1.0) -> None:
        self.greek_type = greek_type
        self.weight = weight
        self.actions: list[HedgeAction] = []

    def compute_hedge(
        self,
        portfolio_exposure: float,
        hedge_instruments: list[OptionInstrument],
    ) -> HedgeAction:
        """计算对冲动作。

        选择能最有效中和目标暴露的对冲工具。
        """
        if abs(portfolio_exposure) < 1e-8 or not hedge_instruments:
            return HedgeAction(None, 0.0, self.greek_type, 0.0)

        best_instrument = None
        best_quantity = 0.0
        best_reduction = 0.0

        for inst in hedge_instruments:
            greeks = GreeksCalculator.all_greeks(inst)
            inst_exposure = greeks.get(self.greek_type)

            if abs(inst_exposure) < 1e-8:
                continue

            quantity = -portfolio_exposure / inst_exposure
            reduction = abs(portfolio_exposure) - abs(portfolio_exposure + quantity * inst_exposure)

            if reduction > best_reduction:
                best_instrument = inst
                best_quantity = quantity
                best_reduction = reduction

        action = HedgeAction(best_instrument, best_quantity, self.greek_type, best_reduction)
        self.actions.append(action)
        return action


# ============================================================
# RL 权重优化器
# ============================================================

class RLWeightOptimizer:
    """RL 权重优化器 (进化策略).

    优化各智能体的权重分配, 最小化剩余暴露。
    """

    def __init__(self, n_agents: int = 3, lr: float = 0.01) -> None:
        self.n_agents = n_agents
        self.lr = lr
        self.weights = np.ones(n_agents) / n_agents
        self.rng = np.random.default_rng(42)
        self.history: list[float] = []

    def update(self, residuals: np.ndarray) -> None:
        """根据残差更新权重。

        Args:
            residuals: 各智能体的对冲残差 (n_agents,)
        """
        abs_residuals = np.abs(residuals)
        total = abs_residuals.sum()
        if total < 1e-8:
            return

        target_weights = (total - abs_residuals) / (total * (self.n_agents - 1) + 1e-8)
        target_weights = np.clip(target_weights, 0.05, 1.0)
        target_weights /= target_weights.sum()

        self.weights = (1 - self.lr) * self.weights + self.lr * target_weights
        self.history.append(float(np.mean(abs_residuals)))

    def get_weights(self) -> np.ndarray:
        return self.weights.copy()

    def perturb(self, noise: float = 0.01) -> None:
        """权重扰动 (探索)。"""
        perturbation = self.rng.standard_normal(self.n_agents) * noise
        new_weights = self.weights + perturbation
        new_weights = np.clip(new_weights, 0.05, 1.0)
        self.weights = new_weights / new_weights.sum()


# ============================================================
# 多智能体协调器
# ============================================================

@dataclass
class HedgingResult:
    """对冲结果。

    Attributes:
        actions: 各智能体的对冲动作
        residual_greeks: 对冲后剩余希腊字母
        total_reduction: 总暴露减少
        weights: 各智能体权重
    """
    actions: list[HedgeAction] = field(default_factory=list)
    residual_greeks: PortfolioGreeks = field(default_factory=PortfolioGreeks)
    total_reduction: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_actions": len(self.actions),
            "residual_greeks": self.residual_greeks.to_dict(),
            "total_reduction": self.total_reduction,
            "weights": dict(self.weights),
        }


class MultiAgentCoordinator:
    """多智能体协调器.

    协调多个 HedgingAgent, 避免对冲冲突:
    1. 按优先级排序 (delta > gamma > vega)
    2. 依次执行各智能体的对冲
    3. 更新组合暴露 (考虑前序对冲的影响)
    4. RL 优化权重
    """

    def __init__(self, use_rl_weights: bool = True) -> None:
        self.agents: dict[GreekType, HedgingAgent] = {
            GreekType.DELTA: HedgingAgent(GreekType.DELTA),
            GreekType.GAMMA: HedgingAgent(GreekType.GAMMA),
            GreekType.VEGA: HedgingAgent(GreekType.VEGA),
        }
        self.rl_optimizer = RLWeightOptimizer(n_agents=3) if use_rl_weights else None
        self.use_rl_weights = use_rl_weights

    def coordinate(
        self,
        portfolio_greeks: PortfolioGreeks,
        hedge_instruments: list[OptionInstrument],
    ) -> HedgingResult:
        """协调多智能体对冲。"""
        result = HedgingResult()
        current_greeks = PortfolioGreeks(
            delta=portfolio_greeks.delta,
            gamma=portfolio_greeks.gamma,
            vega=portfolio_greeks.vega,
            theta=portfolio_greeks.theta,
        )

        initial_exposure = portfolio_greeks.total_exposure()

        priority = [GreekType.DELTA, GreekType.GAMMA, GreekType.VEGA]
        residuals = np.zeros(3)

        for i, greek in enumerate(priority):
            agent = self.agents[greek]
            if self.use_rl_weights and self.rl_optimizer is not None:
                agent.weight = float(self.rl_optimizer.weights[i])

            exposure = current_greeks.get(greek)
            action = agent.compute_hedge(exposure, hedge_instruments)
            result.actions.append(action)

            if action.instrument is not None:
                inst_greeks = GreeksCalculator.all_greeks(action.instrument)
                current_greeks.delta += action.quantity * inst_greeks[GreekType.DELTA]
                current_greeks.gamma += action.quantity * inst_greeks[GreekType.GAMMA]
                current_greeks.vega += action.quantity * inst_greeks[GreekType.VEGA]
                current_greeks.theta += action.quantity * inst_greeks[GreekType.THETA]

            residuals[i] = current_greeks.get(greek)

        if self.rl_optimizer is not None:
            self.rl_optimizer.update(residuals)

        result.residual_greeks = current_greeks
        result.total_reduction = initial_exposure - current_greeks.total_exposure()

        if self.rl_optimizer is not None:
            for greek, agent in self.agents.items():
                result.weights[greek.value] = agent.weight

        return result


# ============================================================
# DeltaHedge 引擎
# ============================================================

class DeltaHedgeEngine:
    """DeltaHedge 多智能体期权对冲引擎.

    使用示例:
        engine = DeltaHedgeEngine()
        result = engine.hedge(portfolio_greeks, option_chain)
    """

    def __init__(self, use_rl_weights: bool = True) -> None:
        self.coordinator = MultiAgentCoordinator(use_rl_weights=use_rl_weights)
        self._stats: dict[str, int] = {"total": 0, "hedged": 0}

    def hedge(
        self,
        portfolio_greeks: PortfolioGreeks,
        hedge_instruments: list[OptionInstrument],
    ) -> HedgingResult:
        """执行多智能体对冲。"""
        self._stats["total"] += 1
        result = self.coordinator.coordinate(portfolio_greeks, hedge_instruments)

        if result.total_reduction > 0:
            self._stats["hedged"] += 1

        logger.debug(
            "对冲完成: reduction=%.4f, residual=%.4f",
            result.total_reduction, result.residual_greeks.total_exposure()
        )

        return result

    def hedge_batch(
        self,
        exposures: list[PortfolioGreeks],
        instruments: list[OptionInstrument],
    ) -> list[HedgingResult]:
        """批量对冲。"""
        return [self.hedge(g, instruments) for g in exposures]

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        total = self._stats["total"]
        return {
            "total": total,
            "hedged": self._stats["hedged"],
            "hedge_rate": self._stats["hedged"] / max(total, 1),
            "rl_weights": (
                self.coordinator.rl_optimizer.get_weights().tolist()
                if self.coordinator.rl_optimizer is not None
                else None
            ),
        }

    def compare_with_beta_hedge(
        self,
        portfolio_greeks: PortfolioGreeks,
        beta: float,
        index_price: float,
    ) -> dict[str, float]:
        """与纯 Beta 加权对冲对比。

        Args:
            portfolio_greeks: 组合希腊字母
            beta: 组合 Beta
            index_price: 指数价格
        Returns:
            对比结果
        """
        beta_hedge_qty = -portfolio_greeks.delta / max(index_price, 1e-8) * beta
        beta_residual = portfolio_greeks.total_exposure() - abs(beta_hedge_qty * index_price)

        return {
            "beta_hedge_quantity": beta_hedge_qty,
            "beta_residual_exposure": beta_residual,
            "multi_agent_initial_exposure": portfolio_greeks.total_exposure(),
        }


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示 DeltaHedge 多智能体期权优化。"""
    print("=" * 60)
    print("DeltaHedge 多智能体期权优化")
    print("文献: #37 PACIS 2025")
    print("=" * 60)

    engine = DeltaHedgeEngine(use_rl_weights=True)

    portfolio = PortfolioGreeks(delta=1000, gamma=500, vega=200, theta=-50)
    print("\n--- 组合暴露 ---")
    print(f"  Delta: {portfolio.delta}")
    print(f"  Gamma: {portfolio.gamma}")
    print(f"  Vega:  {portfolio.vega}")
    print(f"  Theta: {portfolio.theta}")
    print(f"  总暴露: {portfolio.total_exposure():.2f}")

    hedge_instruments = [
        OptionInstrument(OptionType.PUT, 95, 30 / 365, 0.2, underlying=100),
        OptionInstrument(OptionType.PUT, 100, 30 / 365, 0.2, underlying=100),
        OptionInstrument(OptionType.CALL, 105, 30 / 365, 0.2, underlying=100),
        OptionInstrument(OptionType.CALL, 110, 60 / 365, 0.2, underlying=100),
    ]

    print(f"\n--- 对冲工具 ({len(hedge_instruments)} 个期权) ---")
    for inst in hedge_instruments:
        greeks = GreeksCalculator.all_greeks(inst)
        print(f"  {inst.option_type.value} K={inst.strike} T={inst.maturity:.3f}: "
              f"d={greeks[GreekType.DELTA]:.3f} g={greeks[GreekType.GAMMA]:.4f} "
              f"v={greeks[GreekType.VEGA]:.3f}")

    result = engine.hedge(portfolio, hedge_instruments)

    print("\n--- 对冲结果 ---")
    print(f"  动作数: {len(result.actions)}")
    print(f"  总减少: {result.total_reduction:.2f}")
    print(f"  剩余暴露: {result.residual_greeks.total_exposure():.2f}")
    print(f"  剩余 Delta: {result.residual_greeks.delta:.2f}")
    print(f"  剩余 Gamma: {result.residual_greeks.gamma:.2f}")
    print(f"  剩余 Vega:  {result.residual_greeks.vega:.2f}")
    print(f"  RL 权重: {result.weights}")

    comparison = engine.compare_with_beta_hedge(portfolio, beta=1.0, index_price=100)
    print("\n--- 纯 Beta 对冲对比 ---")
    print(f"  Beta 对冲量: {comparison['beta_hedge_quantity']:.2f}")
    print(f"  Beta 剩余暴露: {comparison['beta_residual_exposure']:.2f}")

    stats = engine.get_stats()
    print("\n--- 统计 ---")
    print(f"  总计: {stats['total']}, 已对冲: {stats['hedged']}")
    print(f"  RL 权重: {stats['rl_weights']}")


if __name__ == "__main__":
    main()
