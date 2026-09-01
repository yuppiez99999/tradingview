"""
期限结构模型 (Term Structure)

利率期限结构和波动率期限结构的参数化建模。

支持的模型:
    - Nelson-Siegel: 利率期限结构
    - 线性/多项式: 简单期限插值

参考:
    Nelson, C.R. & Siegel, A.F. (1987).
    "Parsimonious Modeling of Yield Curves"
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RatePoint:
    """利率数据点"""
    tenor: float     # 期限 (年)
    rate: float      # 利率 (连续复利)


class TermStructure:
    """通用期限结构基类"""

    def rate(self, T: float) -> float:
        """获取特定期限的利率"""
        raise NotImplementedError

    def discount_factor(self, T: float) -> float:
        """获取特定期限的贴现因子 e^(-rT)"""
        r = self.rate(T)
        return math.exp(-r * T)

    def forward_rate(self, T1: float, T2: float) -> float:
        """计算远期利率 (T1 到 T2)

        f(T1, T2) = [r2*T2 - r1*T1] / (T2 - T1)
        """
        if abs(T2 - T1) < 1e-10:
            return self.rate(T1)
        r1 = self.rate(T1)
        r2 = self.rate(T2)
        return (r2 * T2 - r1 * T1) / (T2 - T1)


class FlatTermStructure(TermStructure):
    """平坦期限结构 (所有期限相同利率)"""

    def __init__(self, rate: float = 0.02):
        self._rate = rate

    def rate(self, T: float) -> float:
        return self._rate


class LinearTermStructure(TermStructure):
    """线性插值期限结构

    给定离散利率点, 线段间线性插值。
    """

    def __init__(self, points: list[RatePoint]):
        if not points:
            raise ValueError("至少需要一个利率点")
        self.points = sorted(points, key=lambda p: p.tenor)

    def rate(self, T: float) -> float:
        """线性插值获取利率"""
        if T <= self.points[0].tenor:
            return self.points[0].rate
        if T >= self.points[-1].tenor:
            return self.points[-1].rate

        for i in range(len(self.points) - 1):
            if self.points[i].tenor <= T <= self.points[i + 1].tenor:
                t = (T - self.points[i].tenor) / (self.points[i + 1].tenor - self.points[i].tenor)
                return self.points[i].rate + t * (self.points[i + 1].rate - self.points[i].rate)

        return self.points[-1].rate


class NelsonSiegelModel(TermStructure):
    """Nelson-Siegel 利率期限结构模型

    r(T) = β₀ + β₁·[(1 - e^(-T/τ))/(T/τ)] + β₂·[(1 - e^(-T/τ))/(T/τ) - e^(-T/τ)]

    参数经济含义:
        β₀: 长期利率水平
        β₁: 短期利率偏离 (负值=向上倾斜)
        β₂: 中期驼峰 (正值=中期凸起)
        τ:  衰减因子 (控制驼峰位置)

    Reference:
        Nelson & Siegel (1987), Journal of Business
    """

    def __init__(self, beta0: float, beta1: float, beta2: float, tau: float):
        if tau <= 0:
            raise ValueError("tau must be positive")
        self.beta0 = beta0
        self.beta1 = beta1
        self.beta2 = beta2
        self.tau = tau

    def rate(self, T: float) -> float:
        """Nelson-Siegel 利率"""
        if T <= 0:
            # 极限: T→0 时, r(0) = β₀ + β₁
            return self.beta0 + self.beta1

        x = T / self.tau
        exp_neg_x = math.exp(-x)
        factor = (1.0 - exp_neg_x) / x

        return self.beta0 + self.beta1 * factor + self.beta2 * (factor - exp_neg_x)

    @classmethod
    def from_points(
        cls,
        points: list[RatePoint],
        beta0: float | None = None,
        beta1: float | None = None,
        beta2: float | None = None,
        tau: float = 2.0,
    ) -> NelsonSiegelModel:
        """从数据点快速构造 NS 模型 (使用启发式参数)

        若未提供参数, 则从数据点估算:
            beta0 = 最长期限利率
            beta1 = 最短期限利率 - beta0
            beta2 = 0 (无驼峰, 保守假设)

        Args:
            points: 利率数据点
            beta0~beta2: NS 参数 (可选, 不提供则自动估算)
            tau: 衰减因子 (默认 2.0)

        Returns:
            NelsonSiegelModel 实例
        """
        sorted_pts = sorted(points, key=lambda p: p.tenor)
        if beta0 is None:
            beta0 = sorted_pts[-1].rate  # 长期利率
        if beta1 is None:
            beta1 = sorted_pts[0].rate - beta0  # 短期偏离
        if beta2 is None:
            beta2 = 0.0

        return cls(beta0, beta1, beta2, tau)
