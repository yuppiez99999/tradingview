"""协整检验模块 — Engle-Granger + Johansen (纯 numpy 实现)

零第三方依赖, 永不降级。

Engle-Granger 两步法:
    1. OLS 回归: y_t = α + β * x_t + ε_t
    2. ADF 检验残差 ε_t: 若平稳 (单位根被拒绝) → 协整

Johansen 检验 (简化版):
    1. 构造 VAR 一阶差分模型
    2. 简化特征值估计 (避免完整 SVD)
    3. 返回协整秩 r 与特征值

参考:
- Engle & Granger (1987)
- Johansen (1988)
- MacKinnon (1996) 临界值表
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ============================================================
# OLS 回归 (零依赖)
# ============================================================


def ols_regression(
    y: np.ndarray | list[float], x: np.ndarray | list[float]
) -> tuple[float, float, float]:
    """普通最小二乘回归 y = alpha + beta * x + epsilon

    Args:
        y: 因变量
        x: 自变量

    Returns:
        (beta, alpha, r_squared); 数据不足返回 (0, 0, 0)
    """
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    n = len(y_arr)
    if n != len(x_arr) or n < 3:
        return 0.0, 0.0, 0.0

    x_mean = float(np.mean(x_arr))
    y_mean = float(np.mean(y_arr))
    x_centered = x_arr - x_mean
    y_centered = y_arr - y_mean

    sxx = float(np.sum(x_centered**2))
    if sxx < 1e-12:
        return 0.0, y_mean, 0.0
    sxy = float(np.sum(x_centered * y_centered))
    beta = sxy / sxx
    alpha = y_mean - beta * x_mean

    # R²
    y_hat = alpha + beta * x_arr
    ss_res = float(np.sum((y_arr - y_hat) ** 2))
    ss_tot = float(np.sum(y_centered**2))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return float(beta), float(alpha), float(r_sq)


# ============================================================
# ADF 检验 (简化版, 纯 numpy)
# ============================================================


def _adf_statistic(residuals: np.ndarray) -> float:
    """ADF 检验统计量 (简化版, 无常数无趋势)

    回归: Δε_t = ρ * ε_{t-1} + u_t
    ADF 统计量 = ρ / SE(ρ)
    若 ADF < 临界值 → 拒绝单位根 → 平稳

    Args:
        residuals: 残差序列

    Returns:
        ADF t-统计量; 数据不足返回 0 (无法拒绝单位根)
    """
    n = len(residuals)
    if n < 10:
        return 0.0
    # Δε_t = ε_t - ε_{t-1}
    lag = residuals[:-1]
    diff = np.diff(residuals)
    # OLS: diff = rho * lag + u
    sxx = float(np.sum(lag**2))
    if sxx < 1e-12:
        return 0.0
    sxy = float(np.sum(lag * diff))
    rho = sxy / sxx
    # 残差标准误
    u = diff - rho * lag
    n_eff = len(u)
    sigma_u2 = float(np.sum(u**2)) / (n_eff - 1)
    se_rho = float(np.sqrt(sigma_u2 / sxx))
    if se_rho < 1e-12:
        return 0.0
    return float(rho / se_rho)


# MacKinnon (1996) EG 协整检验临界值 (无常数无趋势, n=∞ 近似)
# 显著性 1% / 5% / 10%
_EG_CRITICAL = {-3.90: 0.01, -3.34: 0.05, -3.04: 0.10}


def _adf_pvalue_approx(adf_stat: float) -> float:
    """ADF p-value 近似 (基于 MacKinnon 临界值线性插值)

    Args:
        adf_stat: ADF t-统计量

    Returns:
        p-value ∈ [0, 1]
    """
    if adf_stat <= -3.90:
        return 0.01
    if adf_stat <= -3.34:
        return 0.01 + (adf_stat + 3.90) / (3.90 - 3.34) * (0.05 - 0.01)
    if adf_stat <= -3.04:
        return 0.05 + (adf_stat + 3.34) / (3.34 - 3.04) * (0.10 - 0.05)
    return 0.10 + (adf_stat + 3.04) / 3.04 * 0.90 if adf_stat > -3.04 else 0.10


# ============================================================
# Engle-Granger 协整检验
# ============================================================


@dataclass
class CointegrationResult:
    """协整检验结果"""

    is_cointegrated: bool
    hedge_ratio: float  # β
    intercept: float  # α
    adf_statistic: float
    pvalue: float
    half_life: float | None  # 均值回复半衰期 (天)


def engle_granger_test(
    y: np.ndarray | list[float],
    x: np.ndarray | list[float],
    significance: float = 0.05,
) -> CointegrationResult:
    """Engle-Granger 协整检验

    步骤:
    1. OLS: y = α + β*x + ε
    2. ADF 检验 ε 是否平稳
    3. 若 p-value < significance → 协整

    Args:
        y: 因变量价格序列
        x: 自变量价格序列
        significance: 显著性水平

    Returns:
        CointegrationResult
    """
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    n = len(y_arr)
    if n != len(x_arr) or n < 30:
        return CointegrationResult(
            is_cointegrated=False,
            hedge_ratio=0.0,
            intercept=0.0,
            adf_statistic=0.0,
            pvalue=1.0,
            half_life=None,
        )

    # 1. OLS 回归
    beta, alpha, _ = ols_regression(y_arr, x_arr)

    # 2. 残差 ADF 检验
    residuals = y_arr - (alpha + beta * x_arr)
    adf_stat = _adf_statistic(residuals)
    pvalue = _adf_pvalue_approx(adf_stat)

    # 3. 半衰期估计 (OU 过程)
    half_life = _estimate_half_life(residuals)

    return CointegrationResult(
        is_cointegrated=pvalue < significance,
        hedge_ratio=beta,
        intercept=alpha,
        adf_statistic=adf_stat,
        pvalue=pvalue,
        half_life=half_life,
    )


def _estimate_half_life(spread: np.ndarray) -> float | None:
    """估计均值回复半衰期 (Ornstein-Uhlenbeck)

    Δs_t = -κ * s_{t-1} + u_t
    half_life = ln(2) / κ

    Args:
        spread: 价差序列

    Returns:
        半衰期 (天); κ ≤ 0 返回 None
    """
    n = len(spread)
    if n < 10:
        return None
    lag = spread[:-1]
    diff = np.diff(spread)
    sxx = float(np.sum(lag**2))
    if sxx < 1e-12:
        return None
    sxy = float(np.sum(lag * diff))
    kappa = -sxy / sxx
    if kappa <= 1e-6:
        return None
    return float(np.log(2) / kappa)


# ============================================================
# Johansen 检验 (简化版)
# ============================================================


def johansen_test(
    series: np.ndarray | list[list[float]],
    significance: float = 0.05,
) -> dict:
    """Johansen 协整检验 (简化版)

    对多变量序列检验协整秩 r。
    简化: 用 VAR(1) 一阶差分 + 特征值估计, 不做完整 SVD 分解。

    Args:
        series: 二维数组 (T × N), T 时间点, N 变量
        significance: 显著性水平

    Returns:
        {
            "rank": 协整秩,
            "eigenvalues": 特征值列表,
            "is_cointegrated": bool,
            "n_variables": 变量数,
        }
    """
    arr = np.asarray(series, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 20 or arr.shape[1] < 2:
        return {
            "rank": 0,
            "eigenvalues": [],
            "is_cointegrated": False,
            "n_variables": 0,
        }

    n_obs, n_vars = arr.shape
    # 一阶差分
    diff_arr = np.diff(arr, axis=0)
    # 滞后项
    lag_arr = arr[:-1]

    # 简化: 计算差分与滞后的协方差矩阵
    # ΔY_t = Π * Y_{t-1} + u_t
    # Π = Σ_01 * Σ_11^{-1}
    # 协整秩 = Π 的非零特征值数 (实际为负特征值对应)
    try:
        sigma_11 = np.cov(lag_arr.T)
        sigma_01 = np.cov(diff_arr.T, lag_arr.T)[:n_vars, n_vars:]
        if np.linalg.matrix_rank(sigma_11) < n_vars:
            return {
                "rank": 0,
                "eigenvalues": [],
                "is_cointegrated": False,
                "n_variables": n_vars,
            }
        pi_mat = sigma_01 @ np.linalg.inv(sigma_11)
        eigenvalues = np.linalg.eigvals(pi_mat)
        # 协整秩 = 显著负特征值数 (简化: |λ| > 0.1 视为非零)
        rank = int(np.sum(np.abs(eigenvalues) > 0.1))
        return {
            "rank": rank,
            "eigenvalues": [
                float(ev) for ev in sorted(eigenvalues, key=lambda x: -abs(x))
            ],
            "is_cointegrated": rank > 0,
            "n_variables": n_vars,
        }
    except (np.linalg.LinAlgError, ValueError, FloatingPointError):
        return {
            "rank": 0,
            "eigenvalues": [],
            "is_cointegrated": False,
            "n_variables": n_vars,
        }
