"""
隐含波动率求解器 (Implied Volatility)

给定期权市场价格, 反推 BS 模型中隐含的波动率参数。

算法:
    1. Newton-Raphson (首选) — 利用 Vega 加速收敛, 通常 3-5 次迭代
    2. Bisection (回退)   — 当 NR 不收敛时使用, 保证收敛但较慢

参考:
    Manaster, S. & Koehler, G. (1982). "The Calculation of Implied Variances"
    Corrado, C.J. & Miller, T.W. (1996). "A Note on a Simple Formula for Implied Volatilities"
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from utils.fineng.pricing.black_scholes import (
    bs_price,
    bs_vega,
)

# ============================================================
# 数据结构
# ============================================================


@dataclass
class ImpliedVolResult:
    """隐含波动率求解结果"""

    iv: float               # 隐含波动率 (年化)
    n_iterations: int       # 迭代次数
    converged: bool         # 是否收敛
    price_error: float      # 最终定价误差 (市场价 - 模型价)
    method: str             # 使用的算法: "newton_raphson" / "bisection"


# ============================================================
# Newton-Raphson 法
# ============================================================


def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    is_call: bool = True,
    initial_guess: float = 0.30,
    tolerance: float = 1e-8,
    max_iterations: int = 100,
) -> ImpliedVolResult:
    """Newton-Raphson 隐含波动率求解

    利用 Vega (期权价格对 σ 的偏导数) 加速迭代:
        σ_{n+1} = σ_n - [BS_price(σ_n) - market_price] / vega(σ_n)

    Args:
        market_price: 期权市场价
        S: 标的现价
        K: 行权价
        T: 剩余期限 (年)
        r: 无风险利率
        is_call: True=看涨, False=看跌
        initial_guess: 初始波动率猜测 (默认 30%)
        tolerance: 收敛容差 (价格误差)
        max_iterations: 最大迭代次数

    Returns:
        ImpliedVolResult

    Raises:
        ValueError: 若 market_price 低于理论最低价 (无套利下界)
    """
    # 无套利下界检查
    min_price = _no_arbitrage_lower_bound(S, K, T, r, is_call)
    if market_price < min_price - 1e-10:
        # 不抛异常, 返回零波动 (实践中可能是数据错误)
        return ImpliedVolResult(
            iv=0.0, n_iterations=0, converged=False,
            price_error=market_price - min_price, method="newton_raphson",
        )

    sigma = initial_guess
    for i in range(max_iterations):
        model_price = bs_price(S, K, T, r, sigma, is_call)
        vega_raw = bs_vega(S, K, T, r, sigma) * 100.0  # 还原为 per-unit σ 的 Vega

        price_diff = model_price - market_price

        if abs(price_diff) < tolerance:
            return ImpliedVolResult(
                iv=sigma, n_iterations=i + 1, converged=True,
                price_error=price_diff, method="newton_raphson",
            )

        if vega_raw < 1e-12:
            # Vega 接近零 (深度虚值/到期), 切换到 Bisection
            return implied_vol_bisection(
                market_price, S, K, T, r, is_call, tolerance, max_iterations,
            )

        # Newton-Raphson 步
        sigma = sigma - price_diff / vega_raw

        # 边界钳制
        if sigma <= 0.0:
            sigma = 0.001
        if sigma > 5.0:  # 500% vol 上限
            sigma = 5.0

    # 未收敛, 尝试 Bisection
    return implied_vol_bisection(
        market_price, S, K, T, r, is_call, tolerance, max_iterations,
    )


# ============================================================
# Bisection 法 (回退)
# ============================================================


def implied_vol_bisection(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    is_call: bool = True,
    tolerance: float = 1e-8,
    max_iterations: int = 200,
    sigma_low: float = 0.001,
    sigma_high: float = 5.0,
) -> ImpliedVolResult:
    """Bisection 法隐含波动率求解 (保证收敛)

    当 Newton-Raphson 不收敛时作为回退方案。
    优点: 绝对收敛 (只要解在区间内)
    缺点: 收敛速度较慢 (线性收敛 vs NR 的二次收敛)

    Args:
        sigma_low: 下界 (默认 0.1%)
        sigma_high: 上界 (默认 500%)
        (其他参数同 implied_vol)

    Returns:
        ImpliedVolResult
    """
    price_low = bs_price(S, K, T, r, sigma_low, is_call)
    price_high = bs_price(S, K, T, r, sigma_high, is_call)

    # 检查根是否在区间内
    if price_low > market_price or price_high < market_price:
        # 扩大边界重试
        sigma_low = 1e-6
        sigma_high = 10.0
        price_low = bs_price(S, K, T, r, sigma_low, is_call)
        price_high = bs_price(S, K, T, r, sigma_high, is_call)
        if price_low > market_price or price_high < market_price:
            return ImpliedVolResult(
                iv=0.0, n_iterations=0, converged=False,
                price_error=float("inf"), method="bisection",
            )

    for i in range(max_iterations):
        sigma_mid = (sigma_low + sigma_high) / 2.0
        price_mid = bs_price(S, K, T, r, sigma_mid, is_call)

        if abs(price_mid - market_price) < tolerance:
            return ImpliedVolResult(
                iv=sigma_mid, n_iterations=i + 1, converged=True,
                price_error=price_mid - market_price, method="bisection",
            )

        if price_mid < market_price:
            sigma_low = sigma_mid
        else:
            sigma_high = sigma_mid

    sigma_final = (sigma_low + sigma_high) / 2.0
    price_final = bs_price(S, K, T, r, sigma_final, is_call)
    return ImpliedVolResult(
        iv=sigma_final, n_iterations=max_iterations, converged=False,
        price_error=price_final - market_price, method="bisection",
    )


# ============================================================
# 辅助函数
# ============================================================


def _no_arbitrage_lower_bound(
    S: float,
    K: float,
    T: float,
    r: float,
    is_call: bool,
) -> float:
    """计算期权无套利价格下界

    Call: max(0, S - K·e^(-rT))
    Put:  max(0, K·e^(-rT) - S)
    """
    discount = math.exp(-r * T)
    if is_call:
        return max(0.0, S - K * discount)
    else:
        return max(0.0, K * discount - S)
