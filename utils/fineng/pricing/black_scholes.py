"""
Black-Scholes 期权定价内核 — 单一真相源 (Single Source of Truth)

本模块是整个量化系统中 Black-Scholes 公式的**唯一定义点**。
所有其他模块 (对冲管理、保护性认沽、Theta 引擎等) 均从此导入。

数学约定 (与业界标准一致):
    S       — 标的现价
    K       — 行权价
    T       — 剩余期限 (年, ACT/365)
    r       — 无风险利率 (连续复利, 如 0.02 = 2%)
    sigma   — 年化波动率 (如 0.20 = 20%)

    d1 = [ln(S/K) + (r + 0.5·σ²)·T] / (σ·√T)
    d2 = d1 - σ·√T

    Call  = S·N(d1) - K·e^(-rT)·N(d2)
    Put   = K·e^(-rT)·N(-d2) - S·N(-d1)

Greeks 单位约定:
    Delta   — 标的每变动 1 单位, 期权价格变动量 (无单位)
    Gamma   — 标的每变动 1 单位, Delta 变动量 (1/单位)
    Theta   — 每过 1 年, 期权价格变动量 (年化)
    Vega    — 波动率每变动 1 个百分点 (0.01), 期权价格变动量
    Rho     — 利率每变动 1 个百分点 (0.01), 期权价格变动量

依赖: 仅 math 标准库, 零外部依赖。

参考:
    Black, F. & Scholes, M. (1973). "The Pricing of Options and Corporate Liabilities"
    Hull, J. (2022). "Options, Futures, and Other Derivatives" (11th ed.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ============================================================
# 统计辅助函数
# ============================================================


def norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数 Φ(x)

    使用 math.erf 实现, 精度 ~1e-15, 零外部依赖。
    """
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def norm_pdf(x: float) -> float:
    """标准正态分布概率密度函数 φ(x)"""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ============================================================
# 核心 d1/d2 计算
# ============================================================


def bs_d1_d2(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> tuple[float, float]:
    """计算 Black-Scholes 的 d1 和 d2 参数

    边界处理:
        T ≤ 0 或 sigma ≤ 0 或 S ≤ 0 → 返回 (nan, nan)
        调用方应在定价函数中处理这些边界。

    Returns:
        (d1, d2) 元组
    """
    if T <= 0.0 or sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        return math.nan, math.nan

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


# ============================================================
# 期权定价
# ============================================================


def bs_call_price(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
) -> float:
    """Black-Scholes 欧式看涨期权定价

    Args:
        S: 标的现价
        K: 行权价
        T: 剩余期限 (年)
        r: 无风险利率 (连续复利, 默认 2%)
        sigma: 年化波动率 (默认 20%)

    Returns:
        期权理论价格。边界情况:
        - T ≤ 0: 返回行权收益 max(S-K, 0)
        - sigma ≤ 0: 返回行权收益的现值 max(S-K·e^(-rT), 0)
        - S ≤ 0 或 K ≤ 0: 返回 0.0

    Examples:
        >>> bs_call_price(100, 100, 1.0, 0.05, 0.20)
        10.45...  # ATM call with 1yr, 5% rate, 20% vol
        >>> bs_call_price(100, 120, 0.5, 0.02, 0.30)
        ...
    """
    if S <= 0.0 or K <= 0.0:
        return 0.0

    # 到期: 直接返回行权收益
    if T <= 0.0:
        return max(S - K, 0.0)

    # 零波动: 确定性收益的现值
    if sigma <= 0.0:
        return max(S - K * math.exp(-r * T), 0.0)

    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)
    return S * norm_cdf(d1) - K * discount * norm_cdf(d2)


def bs_put_price(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
) -> float:
    """Black-Scholes 欧式看跌期权定价

    边界处理与 call 一致, 到期返回 max(K-S, 0)。
    """
    if S <= 0.0 or K <= 0.0:
        return 0.0

    if T <= 0.0:
        return max(K - S, 0.0)

    if sigma <= 0.0:
        return max(K * math.exp(-r * T) - S, 0.0)

    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)
    return K * discount * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
) -> float:
    """Black-Scholes 欧式期权定价 (调度函数)

    Args:
        is_call: True=看涨, False=看跌

    Returns:
        期权理论价格
    """
    if is_call:
        return bs_call_price(S, K, T, r, sigma)
    else:
        return bs_put_price(S, K, T, r, sigma)


# ============================================================
# Greeks — Delta / Gamma / Theta / Vega / Rho
# ============================================================


def bs_delta(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
) -> float:
    """Black-Scholes Delta

    Call Delta = N(d1)
    Put  Delta = N(d1) - 1

    边界:
        T ≤ 0: Call: 1 if S≥K else 0;  Put: -1 if S≤K else 0
        sigma ≤ 0: Call: 1 if S≥K·e^(-rT) else 0; Put 同理
    """
    if S <= 0.0 or K <= 0.0:
        return 0.0

    if T <= 0.0:
        if is_call:
            return 1.0 if S >= K else 0.0
        else:
            return -1.0 if S <= K else 0.0

    if sigma <= 0.0:
        fwd = K * math.exp(-r * T)
        if is_call:
            return 1.0 if S >= fwd else 0.0
        else:
            return -1.0 if S <= fwd else 0.0

    d1, _d2 = bs_d1_d2(S, K, T, r, sigma)
    if is_call:
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0


def bs_gamma(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
) -> float:
    """Black-Scholes Gamma (Call 和 Put 相同)

    Gamma = N'(d1) / (S·σ·√T)

    边界: T≤0 或 sigma≤0 或 S≤0 时返回 0.0
    """
    if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
        return 0.0

    d1, _d2 = bs_d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    return norm_pdf(d1) / (S * sigma * sqrt_T)


def bs_theta(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
) -> float:
    """Black-Scholes Theta (年化)

    Call Theta = -[S·N'(d1)·σ] / (2√T) - r·K·e^(-rT)·N(d2)
    Put  Theta = -[S·N'(d1)·σ] / (2√T) + r·K·e^(-rT)·N(-d2)

    注意: Theta 通常为负值 (时间流逝对买方不利)。

    边界: T≤0 或 sigma≤0 或 S≤0 时返回 0.0
    """
    if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
        return 0.0

    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    discount = math.exp(-r * T)

    term1 = -S * norm_pdf(d1) * sigma / (2.0 * sqrt_T)
    if is_call:
        term2 = -r * K * discount * norm_cdf(d2)
    else:
        term2 = r * K * discount * norm_cdf(-d2)

    return term1 + term2


def bs_vega(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
) -> float:
    """Black-Scholes Vega (每 1 个百分点波动率变化)

    Vega = S·N'(d1)·√T / 100

    除以 100 将 Vega 标准化为"波动率每变动 1% (0.01) 的期权价格变动"。
    若需要原始 Vega (per unit σ), 请乘以 100。

    边界: T≤0 或 S≤0 时返回 0.0
    """
    if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
        return 0.0

    d1, _d2 = bs_d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    return S * norm_pdf(d1) * sqrt_T / 100.0


def bs_rho(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
) -> float:
    """Black-Scholes Rho (每 1 个百分点利率变化)

    Call Rho = K·T·e^(-rT)·N(d2) / 100
    Put  Rho = -K·T·e^(-rT)·N(-d2) / 100

    除以 100 将 Rho 标准化为"利率每变动 1% (0.01) 的期权价格变动"。
    若需要原始 Rho (per unit r), 请乘以 100。

    边界: T≤0 或 S≤0 时返回 0.0
    """
    if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
        return 0.0

    _d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)
    raw_rho = K * T * discount * norm_cdf(d2) if is_call else -K * T * discount * norm_cdf(-d2)
    return raw_rho / 100.0


# ============================================================
# 批量 Greeks 计算
# ============================================================


@dataclass
class GreeksResult:
    """Black-Scholes 完整 Greeks 结果

    所有值遵循上述单位约定。
    """

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0

    # 输入参数 (用于调试/审计追踪)
    price: float = 0.0
    S: float = 0.0
    K: float = 0.0
    T: float = 0.0
    r: float = 0.0
    sigma: float = 0.0
    is_call: bool = True


def bs_all_greeks(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    is_call: bool = True,
) -> GreeksResult:
    """一次性计算全部 Greeks + 期权价格

    相比分别调用各 Greek 函数, 本函数复用 d1/d2 计算,
    减少重复计算, 适用于需要多重 Greeks 的场景。

    Returns:
        GreeksResult 包含 delta/gamma/theta/vega/rho/price 及输入参数
    """
    price = bs_price(S, K, T, r, sigma, is_call)

    if S <= 0.0 or K <= 0.0 or T <= 0.0 or sigma <= 0.0:
        # 边界情况: 使用各自函数的边界逻辑
        return GreeksResult(
            delta=bs_delta(S, K, T, r, sigma, is_call),
            gamma=bs_gamma(S, K, T, r, sigma),
            theta=bs_theta(S, K, T, r, sigma, is_call),
            vega=bs_vega(S, K, T, r, sigma),
            rho=bs_rho(S, K, T, r, sigma, is_call),
            price=price,
            S=S, K=K, T=T, r=r, sigma=sigma, is_call=is_call,
        )

    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    discount = math.exp(-r * T)
    npdf_d1 = norm_pdf(d1)

    # Delta
    delta = norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0
    # Gamma
    gamma = npdf_d1 / (S * sigma * sqrt_T)
    # Theta
    term1 = -S * npdf_d1 * sigma / (2.0 * sqrt_T)
    if is_call:
        term2 = -r * K * discount * norm_cdf(d2)
    else:
        term2 = r * K * discount * norm_cdf(-d2)
    theta = term1 + term2
    # Vega (per 1%)
    vega = S * npdf_d1 * sqrt_T / 100.0
    # Rho (per 1%)
    rho = K * T * discount * norm_cdf(d2) / 100.0
    if not is_call:
        rho = -K * T * discount * norm_cdf(-d2) / 100.0

    return GreeksResult(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        price=price,
        S=S, K=K, T=T, r=r, sigma=sigma, is_call=is_call,
    )


# ============================================================
# Put-Call Parity 验证工具 (辅助函数)
# ============================================================


def check_put_call_parity(
    S: float,
    K: float,
    T: float,
    r: float = 0.02,
    sigma: float = 0.20,
    tolerance: float = 1e-6,
) -> bool:
    """验证 Put-Call Parity: C - P = S - K·e^(-rT)

    可用于:
        - 验证 BS 实现正确性
        - 发现隐含利率/股息率异常
        - 套利机会检测

    Returns:
        True 表示等式在容差范围内成立
    """
    C = bs_call_price(S, K, T, r, sigma)
    P = bs_put_price(S, K, T, r, sigma)
    lhs = C - P
    rhs = S - K * math.exp(-r * T)
    return abs(lhs - rhs) < tolerance
