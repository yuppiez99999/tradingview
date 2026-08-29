"""
二叉树期权定价 (Binomial Tree / CRR 模型)

Cox-Ross-Rubinstein (CRR) 二叉树模型, 支持欧式和美式期权。

CRR 参数化:
    u = exp(σ·√Δt)      — 上行因子
    d = 1/u              — 下行因子
    p = (e^(r·Δt) - d) / (u - d)  — 风险中性上行概率

参考:
    Cox, J., Ross, S., & Rubinstein, M. (1979).
    "Option Pricing: A Simplified Approach"
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ExerciseStyle(Enum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


@dataclass
class BinomialResult:
    """二叉树定价结果"""

    price: float  # 期权理论价格
    delta: float  # Delta (有限差分近似)
    gamma: float  # Gamma (有限差分近似)
    theta: float  # Theta (年化, 有限差分近似)
    n_steps: int  # 使用的步数


class BinomialTree:
    """CRR 二叉树期权定价引擎

    支持欧式和美式期权, 通过增加步数可逼近 BS 解析解。

    Usage:
        tree = BinomialTree(n_steps=200)
        result = tree.price(S=100, K=100, T=1.0, r=0.05, sigma=0.20, is_call=True)
    """

    def __init__(self, n_steps: int = 200):
        """初始化二叉树引擎

        Args:
            n_steps: 时间步数。越多越精确但越慢。
                    200 步对大多数场景足够 (与 BS 误差 < 0.1%)
                    1000 步可获得极高精度
        """
        if n_steps < 1:
            raise ValueError(f"n_steps must be >= 1, got {n_steps}")
        self.n_steps = n_steps

    # -----------------------------------------------------------
    # 核心定价
    # -----------------------------------------------------------

    def price(
        self,
        S: float,
        K: float,
        T: float,
        r: float = 0.02,
        sigma: float = 0.20,
        is_call: bool = True,
        exercise: ExerciseStyle = ExerciseStyle.AMERICAN,
    ) -> BinomialResult:
        """二叉树期权定价

        Args:
            S: 标的现价
            K: 行权价
            T: 剩余期限 (年)
            r: 无风险利率
            sigma: 年化波动率
            is_call: True=看涨, False=看跌
            exercise: 欧式/美式

        Returns:
            BinomialResult 包含价格和 Greeks 近似值
        """
        if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
            # 边界: 返回行权收益
            payoff = max(S - K, 0.0) if is_call else max(K - S, 0.0)
            return BinomialResult(
                price=payoff, delta=0.0, gamma=0.0, theta=0.0, n_steps=self.n_steps
            )

        option_price = self._tree_price(S, K, T, r, sigma, is_call, exercise)

        # 有限差分 Greeks (通过小幅扰动 S 和 T 重定价)
        ds = S * 0.001
        dt_shift = 1.0 / 365.0  # 1 天

        price_up = self._tree_price(S + ds, K, T, r, sigma, is_call, exercise)
        price_down = self._tree_price(S - ds, K, T, r, sigma, is_call, exercise)
        delta = (price_up - price_down) / (2.0 * ds) if ds > 0 else 0.0

        gamma = (
            (price_up - 2.0 * option_price + price_down) / (ds * ds) if ds > 0 else 0.0
        )

        price_later = (
            self._tree_price(S, K, max(T - dt_shift, 0), r, sigma, is_call, exercise)
            if dt_shift < T
            else option_price
        )
        theta = (price_later - option_price) / dt_shift if dt_shift > 0 else 0.0

        return BinomialResult(
            price=option_price,
            delta=delta,
            gamma=gamma,
            theta=theta,
            n_steps=self.n_steps,
        )

    def _tree_price(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        is_call: bool,
        exercise: ExerciseStyle,
    ) -> float:
        """二叉树核心定价 (无 Greeks, 用于自重入和 Greeks 有限差分)"""
        if T <= 0:
            return max(S - K, 0.0) if is_call else max(K - S, 0.0)
        if sigma <= 0:
            discount = math.exp(-r * T)
            fwd = S - K * discount
            if is_call:
                return max(fwd, 0.0)
            return max(-fwd, 0.0)

        n = self.n_steps
        dt = T / n
        u = math.exp(sigma * math.sqrt(dt))
        d = 1.0 / u
        p = (math.exp(r * dt) - d) / (u - d)
        discount = math.exp(-r * dt)

        # 构建到期收益树 (第 n 层)
        values = [0.0] * (n + 1)
        for j in range(n + 1):
            spot_j = S * (u ** (n - j)) * (d**j)
            values[j] = max(spot_j - K, 0.0) if is_call else max(K - spot_j, 0.0)

        # 向后递推
        for i in range(n - 1, -1, -1):
            for j in range(i + 1):
                hold = discount * (p * values[j] + (1.0 - p) * values[j + 1])
                if exercise == ExerciseStyle.AMERICAN:
                    spot_j = S * (u ** (i - j)) * (d**j)
                    early = max(spot_j - K, 0.0) if is_call else max(K - spot_j, 0.0)
                    values[j] = max(hold, early)
                else:
                    values[j] = hold

        return values[0]


# ============================================================
# 便捷函数
# ============================================================


def binomial_price(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
    n_steps: int = 200,
    american: bool = True,
) -> float:
    """二叉树期权定价便捷函数

    Args:
        american: True=美式, False=欧式
        n_steps: 时间步数

    Returns:
        期权理论价格
    """
    exercise = ExerciseStyle.AMERICAN if american else ExerciseStyle.EUROPEAN
    tree = BinomialTree(n_steps=n_steps)
    return tree.price(S, K, T, r, sigma, is_call, exercise).price
