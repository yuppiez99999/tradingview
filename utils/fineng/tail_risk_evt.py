"""
POT-GPD 尾部风险估计 (Peaks Over Threshold / Generalized Pareto)

只读监控模块 — 仅输出 EVT 尾部 ES 估计作为辅助观察指标，严禁接触发器。

方法论:
    1. POT (Peaks Over Threshold): 选定阈值 u, 提取超越样本 Y = {X_i - u | X_i > u}
    2. GPD 拟合: 超越量 Y 服从广义帕累托分布
       CDF:  F(y) = 1 - [1 + ξ·y/σ]^{-1/ξ}  (ξ ≠ 0)
                    = 1 - exp(-y/σ)           (ξ = 0, 指数分布)
       PDF:  f(y) = (1/σ) · [1 + ξ·y/σ]^{-(1/ξ + 1)}
    3. 尾部风险估计:
       VaR_α = u + (σ/ξ) · [(n/N_u · (1-α))^{-ξ} - 1]   (ξ ≠ 0)
             = u + σ · ln(n/N_u · (1-α))                 (ξ = 0)
       ES_α  = VaR_α/(1-ξ) + (σ - ξ·u)/(1-ξ)            (ξ < 1)

参数估计: 网格搜索 MLE (纯 Python, 零外部依赖)

fail-closed 约束:
    - 超越样本 < 40 → 拒绝拟合, 输出 NaN + 告警
    - ξ ≥ 0.5 → 估计极不稳定, 输出宽置信区间 + 告警
    - GPD log-lik 评估中数值异常 → fail-closed

参考:
    McNeil, A. J., Frey, R., & Embrechts, P. (2015). "Quantitative Risk Management"
    Embrechts, P., Kluppelberg, C., & Mikosch, T. (1997). "Modelling Extremal Events"

Usage:
    from utils.fineng.tail_risk_evt import fit_evt, evt_var_es
    result = fit_evt(daily_returns, threshold_percentile=0.95)
    if result.converged:
        logger.info(f"EVT 99% ES: {result.es_99:.4f}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ============================================================
# 结果数据结构
# ============================================================


@dataclass
class EVTResult:
    """POT-GPD 尾部风险估计结果"""

    # 阈值
    threshold_u: float  # 阈值 (负值, 如 -0.02 = -2%)
    threshold_percentile: float  # 阈值分位数 (如 0.95)

    # 超越样本
    n_total: int  # 总样本量
    n_excess: int  # 超越样本数

    # GPD 参数
    sigma: float  # GPD 尺度参数 σ > 0
    xi: float  # GPD 形状参数 ξ
    log_likelihood: float  # 最大对数似然

    # 尾部风险
    var_975: float  # VaR 97.5% (负值)
    var_99: float  # VaR 99% (负值)
    es_975: float  # ES 97.5% (负值, 更极端)
    es_99: float  # ES 99% (负值)

    # 置信区间 (通过 Profile Likelihood 近似)
    xi_lower: float  # ξ 95% CI 下界
    xi_upper: float  # ξ 95% CI 上界
    sigma_lower: float
    sigma_upper: float

    # 经验分位数 (对照)
    empirical_var_975: float
    empirical_var_99: float
    empirical_es_975: float
    empirical_es_99: float
    evt_vs_empirical_ratio: float  # EVT ES(99%) / 经验 ES(99%), 偏离 >30% 应告警

    # 状态
    converged: bool
    warning: str = ""
    error_message: str = ""


# ============================================================
# GPD 对数似然 & 网格搜索
# ============================================================


def _gpd_loglik(excesses: list[float], sigma: float, xi: float) -> float:
    """GPD 对数似然

    LL = -n·ln(σ) - (1/ξ + 1)·Σ ln(1 + ξ·y_i/σ)

    约束: σ > 0,  1 + ξ·y_i/σ > 0 (对所有 i)
    """
    if sigma <= 0:
        return -float("inf")

    n = len(excesses)
    log_lik = -n * math.log(sigma)

    for y in excesses:
        arg = 1.0 + xi * y / sigma
        if arg <= 0:
            return -float("inf")
        log_lik -= (1.0 / xi + 1.0) * math.log(arg) if abs(xi) > 1e-10 else y / sigma

    return log_lik


def _grid_search_gpd(
    excesses: list[float],
    xi_grid: list[float] | None = None,
    sigma_grid: list[float] | None = None,
) -> tuple[float, float, float]:
    """网格搜索 GPD 参数 MLE

    ξ 范围: [-0.5, 1.0], 重点关注 [0, 0.5] (金融收益尾部通常在 0~0.4)
    σ 范围: [0.1·σ_sample, 5·σ_sample]

    Returns:
        (sigma, xi, log_likelihood)
    """
    n = len(excesses)
    if n == 0:
        return 0.0, 0.0, -float("inf")

    # σ 范围: 围绕样本标准差
    mean_y = sum(excesses) / n
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in excesses) / max(n - 1, 1))
    sigma_lo = max(std_y * 0.1, 1e-6)
    sigma_hi = std_y * 5.0

    if xi_grid is None:
        xi_grid = [-0.4, -0.2, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]

    if sigma_grid is None:
        n_sigma = 15
        step = (sigma_hi - sigma_lo) / max(n_sigma - 1, 1)
        sigma_grid = [sigma_lo + step * i for i in range(n_sigma)]

    best_sigma, best_xi = 0.0, 0.0
    best_ll = -float("inf")

    for xi in xi_grid:
        for sigma in sigma_grid:
            ll = _gpd_loglik(excesses, sigma, xi)
            if ll > best_ll:
                best_ll = ll
                best_sigma = sigma
                best_xi = xi

    # 细网格
    if best_sigma > 0:
        delta_s = best_sigma * 0.1
        delta_x = 0.05
        fine_sigmas = [
            best_sigma - delta_s,
            best_sigma - delta_s * 0.5,
            best_sigma,
            best_sigma + delta_s * 0.5,
            best_sigma + delta_s,
        ]
        fine_xis = [
            best_xi - delta_x,
            best_xi - delta_x * 0.5,
            best_xi,
            best_xi + delta_x * 0.5,
            best_xi + delta_x,
        ]
        for xi in fine_xis:
            for sigma in fine_sigmas:
                if sigma <= 0:
                    continue
                ll = _gpd_loglik(excesses, sigma, xi)
                if ll > best_ll:
                    best_ll = ll
                    best_sigma = sigma
                    best_xi = xi

    return best_sigma, best_xi, best_ll


def _profile_likelihood_ci(
    excesses: list[float], sigma_hat: float, xi_hat: float
) -> tuple[float, float, float, float]:
    """Profile Likelihood 近似 95% 置信区间

    卡方近似: 2·(LL_max - LL(θ)) ~ χ²(1)
    95% 临界值 ≈ 1.92 (单参数)
    """
    ll_max = _gpd_loglik(excesses, sigma_hat, xi_hat)
    threshold = ll_max - 1.92  # χ²(1) 95% 临界值的一半

    # ξ 的 Profile CI
    xi_lo, xi_hi = xi_hat - 0.3, xi_hat + 0.3
    for xi_test in _linspace(xi_hat - 0.5, xi_hat + 0.5, 40):
        # 对每个 ξ, 优化 σ (简化: 在 sigma_hat 附近搜索)
        best_ll = -float("inf")
        for s in _linspace(sigma_hat * 0.3, sigma_hat * 3.0, 20):
            ll = _gpd_loglik(excesses, s, xi_test)
            if ll > best_ll:
                best_ll = ll
        if best_ll >= threshold and xi_test < xi_lo:
            xi_lo = min(xi_test, xi_lo)
        if best_ll >= threshold and xi_test > xi_hi:
            xi_hi = max(xi_test, xi_hi)

    sigma_lo, sigma_hi = sigma_hat * 0.3, sigma_hat * 3.0
    for s_test in _linspace(sigma_hat * 0.1, sigma_hat * 5.0, 40):
        best_ll = -float("inf")
        for xi_test in _linspace(xi_hat - 0.3, xi_hat + 0.3, 15):
            ll = _gpd_loglik(excesses, s_test, xi_test)
            if ll > best_ll:
                best_ll = ll
        if best_ll >= threshold:
            sigma_lo = min(s_test, sigma_lo)
            sigma_hi = max(s_test, sigma_hi)

    return xi_lo, xi_hi, sigma_lo, sigma_hi


def _linspace(start: float, end: float, n: int) -> list[float]:
    """均匀采样 (替代 numpy.linspace)"""
    if n <= 1:
        return [start]
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


# ============================================================
# 公共 API
# ============================================================


def fit_evt(
    daily_returns: list[float],
    threshold_percentile: float = 0.95,
    min_history: int = 120,
) -> EVTResult:
    """POT-GPD 尾部风险估计

    Args:
        daily_returns: 日收益率序列 (正值=盈利, 负值=亏损)
        threshold_percentile: POT 阈值分位数, 默认 0.95
            - 0.95 = 尾部 5% 极端负收益
            - 0.975 = 更严格, 仅尾部 2.5%
        min_history: 最小样本量, 默认 120

    Returns:
        EVTResult 含 VaR/ES 估计及置信区间

    Fail-Closed:
        - 样本量 < min_history → converged=False
        - 超越样本 < 40 → converged=False, 拒绝拟合
        - ξ > 0.5 → 告警 (估计极不稳定)
    """
    n = len(daily_returns)

    # ---- 样本量检查 ----
    if n < min_history:
        return EVTResult(
            threshold_u=0.0,
            threshold_percentile=threshold_percentile,
            n_total=n,
            n_excess=0,
            sigma=0.0,
            xi=0.0,
            log_likelihood=-float("inf"),
            var_975=float("nan"),
            var_99=float("nan"),
            es_975=float("nan"),
            es_99=float("nan"),
            xi_lower=0.0,
            xi_upper=0.0,
            sigma_lower=0.0,
            sigma_upper=0.0,
            empirical_var_975=float("nan"),
            empirical_var_99=float("nan"),
            empirical_es_975=float("nan"),
            empirical_es_99=float("nan"),
            evt_vs_empirical_ratio=float("nan"),
            converged=False,
            error_message=f"样本量不足: {n} < {min_history}",
        )

    # ---- 计算阈值 ----
    sorted_returns = sorted(daily_returns)
    idx_u = int(n * threshold_percentile)
    threshold_u = sorted_returns[idx_u]

    # 提取超越样本 (负收益侧, 所以取 ≤ threshold)
    excesses = [threshold_u - r for r in daily_returns if r <= threshold_u]
    n_excess = len(excesses)

    # ---- 超越样本不足 ----
    if n_excess < 40:
        return EVTResult(
            threshold_u=threshold_u,
            threshold_percentile=threshold_percentile,
            n_total=n,
            n_excess=n_excess,
            sigma=0.0,
            xi=0.0,
            log_likelihood=-float("inf"),
            var_975=float("nan"),
            var_99=float("nan"),
            es_975=float("nan"),
            es_99=float("nan"),
            xi_lower=0.0,
            xi_upper=0.0,
            sigma_lower=0.0,
            sigma_upper=0.0,
            empirical_var_975=float("nan"),
            empirical_var_99=float("nan"),
            empirical_es_975=float("nan"),
            empirical_es_99=float("nan"),
            evt_vs_empirical_ratio=float("nan"),
            converged=False,
            error_message=f"超越样本不足: {n_excess} < 40 (阈值={threshold_percentile}, 需要更宽松的阈值或更多历史数据)",
        )

    # ---- GPD 拟合 ----
    sigma_hat, xi_hat, best_ll = _grid_search_gpd(excesses)

    if xi_hat <= -0.5 or sigma_hat <= 0:
        return EVTResult(
            threshold_u=threshold_u,
            threshold_percentile=threshold_percentile,
            n_total=n,
            n_excess=n_excess,
            sigma=0.0,
            xi=0.0,
            log_likelihood=-float("inf"),
            var_975=float("nan"),
            var_99=float("nan"),
            es_975=float("nan"),
            es_99=float("nan"),
            xi_lower=0.0,
            xi_upper=0.0,
            sigma_lower=0.0,
            sigma_upper=0.0,
            empirical_var_975=float("nan"),
            empirical_var_99=float("nan"),
            empirical_es_975=float("nan"),
            empirical_es_99=float("nan"),
            evt_vs_empirical_ratio=float("nan"),
            converged=False,
            warning="GPD 参数异常 (ξ≤-0.5 或 σ≤0), 无法计算尾部风险",
        )

    # ---- 尾部风险计算 ----
    p_975 = 0.025  # 左侧 2.5%
    p_99 = 0.01  # 左侧 1%

    def evt_var(alpha: float) -> float:
        """EVT VaR: 注意这里 α 是左侧尾部概率 (0.01, 0.025)"""
        if abs(xi_hat) < 1e-8:
            return threshold_u + sigma_hat * math.log(n / n_excess * alpha)
        tail_prob = n_excess / n
        ratio = tail_prob / alpha
        if ratio <= 0:
            return threshold_u
        return threshold_u + (sigma_hat / xi_hat) * (ratio ** (-xi_hat) - 1.0)

    def evt_es(alpha: float) -> float:
        """EVT ES (Expected Shortfall)"""
        var_a = evt_var(alpha)
        if abs(xi_hat) < 1e-8:
            return var_a + sigma_hat
        denom = 1.0 - xi_hat
        if denom <= 0:
            return float("nan")  # ξ ≥ 1, 均值不存在
        return (var_a + sigma_hat - xi_hat * threshold_u) / denom

    var_975 = evt_var(p_975)
    var_99 = evt_var(p_99)
    es_975 = evt_es(p_975)
    es_99 = evt_es(p_99)

    # ---- 置信区间 ----
    xi_lo, xi_hi, sigma_lo, sigma_hi = _profile_likelihood_ci(
        excesses, sigma_hat, xi_hat
    )

    # ---- 经验分位数对照 ----
    def empirical_quantile(data: list[float], p: float) -> float:
        s = sorted(data)
        k = int(len(s) * p)
        return s[max(0, min(k, len(s) - 1))]

    def empirical_es(data: list[float], p: float) -> float:
        s = sorted(data)
        k = int(len(s) * p)
        tail = s[:k]
        return sum(tail) / max(len(tail), 1)

    emp_var_975 = empirical_quantile(daily_returns, p_975)
    emp_var_99 = empirical_quantile(daily_returns, p_99)
    emp_es_975 = empirical_es(daily_returns, p_975)
    emp_es_99 = empirical_es(daily_returns, p_99)

    # EVT vs 经验对比
    if abs(emp_es_99) > 1e-10:
        ratio = abs(es_99 / emp_es_99)
    else:
        ratio = float("nan")

    # ---- 告警生成 ----
    warning = ""
    if xi_hat > 0.5:
        warning = (
            f"ξ={xi_hat:.3f} > 0.5, GPD 估计极不稳定，宽置信区间: "
            f"[{xi_lo:.3f}, {xi_hi:.3f}]，样本少时尾部估计不可靠"
        )
    if ratio > 1.30 and not math.isnan(ratio):
        if warning:
            warning += "; "
        warning += (
            f"EVT ES(99%) / 经验 ES(99%) = {ratio:.2f} > 1.30, 差异过大应手工复核"
        )

    return EVTResult(
        threshold_u=threshold_u,
        threshold_percentile=threshold_percentile,
        n_total=n,
        n_excess=n_excess,
        sigma=sigma_hat,
        xi=xi_hat,
        log_likelihood=best_ll,
        var_975=var_975,
        var_99=var_99,
        es_975=es_975,
        es_99=es_99,
        xi_lower=xi_lo,
        xi_upper=xi_hi,
        sigma_lower=sigma_lo,
        sigma_upper=sigma_hi,
        empirical_var_975=emp_var_975,
        empirical_var_99=emp_var_99,
        empirical_es_975=emp_es_975,
        empirical_es_99=emp_es_99,
        evt_vs_empirical_ratio=ratio,
        converged=True,
        warning=warning,
    )


def evt_var_es(
    daily_returns: list[float],
    confidence: float = 0.99,
    threshold_percentile: float = 0.95,
) -> dict[str, float]:
    """便捷接口: 直接返回 EVT VaR/ES 值

    Args:
        daily_returns: 日收益率序列
        confidence: VaR/ES 置信水平 (0.99 = 99%)
        threshold_percentile: POT 阈值分位数

    Returns:
        dict 含 var, es, n_excess, converged 等

    Fail-closed: 拟合失败时返回 NaN
    """
    result = fit_evt(daily_returns, threshold_percentile=threshold_percentile)

    if not result.converged:
        return {
            "var": float("nan"),
            "es": float("nan"),
            "n_excess": result.n_excess,
            "converged": False,
        }

    if confidence == 0.975:
        var_val = result.var_975
        es_val = result.es_975
    else:
        var_val = result.var_99
        es_val = result.es_99

    return {
        "var": var_val,
        "es": es_val,
        "n_excess": result.n_excess,
        "xi": result.xi,
        "converged": result.converged,
        "warning": result.warning,
    }
