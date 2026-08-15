"""
GARCH(1,1) 条件波动率预测 (Volatility Forecasting)

只读对照模块 — 与现有 EWMA(λ=0.94) 并行输出，不替换任何波动率链路。

数学模型:
    ε_t = r_t - μ                    (去均值对数收益率)
    σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}   (GARCH(1,1) 条件方差)

    约束: ω > 0,  α ≥ 0,  β ≥ 0,  α + β < 1
    无条件方差:  σ²_long = ω / (1 - α - β)

参数估计: 网格搜索最大似然 (Grid Search MLE)
    对数似然:  LL = -½ Σ [ln(σ²_t) + ε²_t / σ²_t]
    网格覆盖 α ∈ [0.01, 0.30], β ∈ [0.50, 0.95], 步长自适应

设计原则 (与 fineng 包一致):
    1. 零外部依赖 — 仅 math 标准库
    2. 纯函数 — 输入确定则输出确定
    3. fail-closed — 拟合失败回退 NaN + 告警, 调用方应 fallback 到 EWMA

参考:
    Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroskedasticity"
    Hull, J. (2022). "Options, Futures, and Other Derivatives" (11th ed.) Ch.23

Usage:
    from utils.fineng.vol_forecast import fit_garch, forecast_vol
    result = fit_garch(daily_returns)
    if result.converged:
        vol_t1 = result.forecast_vol
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ============================================================
# 结果数据结构
# ============================================================


@dataclass
class GARCHResult:
    """GARCH(1,1) 拟合结果"""

    omega: float
    alpha: float
    beta: float
    persistence: float                 # α + β (越接近 1 则波动率持续性越强)
    long_run_vol: float                # 无条件波动率 (年化)
    conditional_vol: list[float]       # 条件波动率序列 (每日)
    forecast_vol: float                # 向前 1 步条件波动率预测 (年化)
    forecast_variance: float           # 向前 1 步条件方差
    log_likelihood: float              # 最大对数似然值
    converged: bool                    # 是否成功拟合
    fitted_days: int                   # 有效拟合天数
    error_message: str = ""            # 收敛失败原因

    @property
    def half_life_days(self) -> float:
        """波动率冲击半衰期 (天)  HL = ln(0.5) / ln(α+β)"""
        if self.persistence <= 0 or self.persistence >= 1:
            return float("inf")
        return math.log(0.5) / math.log(self.persistence)


@dataclass
class VolComparisonReport:
    """GARCH vs EWMA 对照报告"""

    date: str
    garch_vol: float
    ewma_vol: float                   # EWMA(λ=0.94) 波动率
    garch_vs_ewma_ratio: float        # GARCH/EWMA 比率
    garch_persistence: float
    garch_converged: bool
    ewma_lambda: float = 0.94


# ============================================================
# 核心算法
# ============================================================


def _grid_search_garch(
    eps2: list[float],
    alpha_grid: list[float] | None = None,
    beta_grid: list[float] | None = None,
) -> tuple[float, float, float, float]:
    """网格搜索 GARCH(1,1) 参数

    搜索策略:
        粗网格 (coarse): α ∈ 8 点, β ∈ 8 点 → 64 组合
        细网格 (refine): 粗网格最优 ±10% 范围内 6×6 细化 → 36 组合
        总共最多评估 100 个参数组合

    Args:
        eps2: 残差平方序列 (已去均值)
        alpha_grid: 自定义 α 搜索格点, 默认粗网格
        beta_grid: 自定义 β 搜索格点

    Returns:
        (omega, alpha, beta, log_likelihood) 最优参数
    """
    n = len(eps2)
    unconditional_var = sum(eps2) / max(n, 1)

    # ---- 粗网格 ----
    if alpha_grid is None:
        alpha_grid = [0.01, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25]
    if beta_grid is None:
        beta_grid = [0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]

    best_omega, best_alpha, best_beta = 0.0, 0.0, 0.0
    best_ll = -float("inf")

    for alpha in alpha_grid:
        for beta in beta_grid:
            if alpha + beta >= 0.995 or alpha + beta <= 0.01:
                continue
            omega = unconditional_var * (1.0 - alpha - beta)
            if omega <= 1e-12:
                omega = 1e-10
            ll = _garch_loglik(eps2, omega, alpha, beta)
            if ll > best_ll:
                best_ll = ll
                best_omega = omega
                best_alpha = alpha
                best_beta = beta

    # ---- 细网格 ----
    if best_alpha > 0 and best_beta > 0:
        fine_alphas = _fine_grid(best_alpha, len(eps2))
        fine_betas = _fine_grid(best_beta, len(eps2))
        for alpha in fine_alphas:
            for beta in fine_betas:
                if alpha + beta >= 0.995 or alpha + beta <= 0.01:
                    continue
                omega = unconditional_var * (1.0 - alpha - beta)
                if omega <= 1e-12:
                    omega = 1e-10
                ll = _garch_loglik(eps2, omega, alpha, beta)
                if ll > best_ll:
                    best_ll = ll
                    best_omega = omega
                    best_alpha = alpha
                    best_beta = beta

    return best_omega, best_alpha, best_beta, best_ll


def _fine_grid(center: float, n_samples: int) -> list[float]:
    """围绕中心值生成细网格 (±15% 范围, 6 点)"""
    delta = center * 0.15
    lo = max(center - delta, 0.001)
    hi = min(center + delta, 0.98)
    step = (hi - lo) / 5.0
    if step <= 0:
        return [center]
    return [lo + step * i for i in range(6)]


def _garch_loglik(
    eps2: list[float], omega: float, alpha: float, beta: float
) -> float:
    """计算 GARCH(1,1) 对数似然

    LL = -½ Σ [ln(σ²_t) + ε²_t / σ²_t]
    其中 σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}
    """
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
        return -float("inf")

    # 初始化 σ²_1 = ω / (1 - α - β) (无条件方差)
    unconditional_var = omega / (1.0 - alpha - beta)
    sigma2 = unconditional_var

    log_lik = 0.0
    n = len(eps2)

    for t in range(1, n):
        sigma2 = omega + alpha * eps2[t - 1] + beta * sigma2
        if sigma2 <= 0:
            return -float("inf")
        log_lik += -0.5 * (math.log(sigma2) + eps2[t] / sigma2)

    return log_lik


def _compute_cond_var(
    eps2: list[float], omega: float, alpha: float, beta: float
) -> list[float]:
    """计算条件方差序列"""
    n = len(eps2)
    unconditional_var = omega / max(1.0 - alpha - beta, 1e-10)
    sigma2_series = [unconditional_var] * n

    sigma2 = unconditional_var
    for t in range(1, n):
        sigma2 = omega + alpha * eps2[t - 1] + beta * sigma2
        sigma2_series[t] = max(sigma2, 1e-12)

    return sigma2_series


# ============================================================
# 公共 API
# ============================================================


def fit_garch(
    daily_returns: list[float],
    min_history: int = 250,
    annualize: bool = True,
    trading_days: int = 252,
) -> GARCHResult:
    """拟合 GARCH(1,1) 模型

    Args:
        daily_returns: 日对数收益率序列 (r_t = ln(P_t/P_{t-1}))
        min_history: 最小样本量要求, 默认 250 (约 1 年)
        annualize: 是否年化波动率输出, 默认 True
        trading_days: 年化用的交易日数, 默认 252

    Returns:
        GARCHResult 包含拟合参数、条件波动率序列、向前 1 步预测

    Fail-Closed 行为:
        - 样本量不足 min_history → converged=False, error_message 说明
        - 网格搜索无有效参数 → converged=False
        - 条件方差序列含 NaN → converged=False
    """
    n = len(daily_returns)

    # ---- 样本量检查 ----
    if n < min_history:
        return GARCHResult(
            omega=0.0,
            alpha=0.0,
            beta=0.0,
            persistence=0.0,
            long_run_vol=0.0,
            conditional_vol=[],
            forecast_vol=0.0,
            forecast_variance=0.0,
            log_likelihood=float("-inf"),
            converged=False,
            fitted_days=0,
            error_message=f"样本量不足: {n} < {min_history} (需要至少 {min_history} 个日收益)",
        )

    # ---- 去均值 ----
    mu = sum(daily_returns) / n
    eps = [r - mu for r in daily_returns]
    eps2 = [e * e for e in eps]

    # ---- 网格搜索 MLE ----
    omega, alpha, beta, best_ll = _grid_search_garch(eps2)

    if alpha <= 0 or beta <= 0:
        return GARCHResult(
            omega=0.0,
            alpha=0.0,
            beta=0.0,
            persistence=0.0,
            long_run_vol=0.0,
            conditional_vol=[],
            forecast_vol=0.0,
            forecast_variance=0.0,
            log_likelihood=float("-inf"),
            converged=False,
            fitted_days=0,
            error_message="网格搜索未找到有效参数 (α≤0 或 β≤0), 可能收益序列波动率极低或过于平稳",
        )

    # ---- 计算条件方差序列 ----
    cond_var = _compute_cond_var(eps2, omega, alpha, beta)

    # ---- 向前 1 步预测 ----
    forecast_var = omega + alpha * eps2[-1] + beta * cond_var[-1]
    if forecast_var <= 0:
        forecast_var = omega / max(1.0 - alpha - beta, 1e-10)

    # ---- 年化 ----
    scale = math.sqrt(trading_days) if annualize else 1.0
    long_run_vol_raw = math.sqrt(omega / max(1.0 - alpha - beta, 1e-10))
    forecast_vol_raw = math.sqrt(max(forecast_var, 0.0))

    cond_vol = [math.sqrt(max(v, 0.0)) * scale for v in cond_var]

    return GARCHResult(
        omega=omega,
        alpha=alpha,
        beta=beta,
        persistence=alpha + beta,
        long_run_vol=long_run_vol_raw * scale,
        conditional_vol=cond_vol,
        forecast_vol=forecast_vol_raw * scale,
        forecast_variance=forecast_var * (trading_days if annualize else 1.0),
        log_likelihood=best_ll,
        converged=True,
        fitted_days=n,
    )


def forecast_vol(
    previous_eps2: float,
    previous_sigma2: float,
    omega: float,
    alpha: float,
    beta: float,
    steps: int = 1,
    annualize: bool = True,
    trading_days: int = 252,
) -> float:
    """向前多步波动率预测 (仅使用已拟合参数)

    多步预测公式:
        E[σ²_{t+k} | F_t] = σ²_long + (α+β)^{k-1} · (σ²_{t+1} - σ²_long)

    Args:
        previous_eps2: 最近一期残差平方 ε²_t
        previous_sigma2: 最近一期条件方差 σ²_t
        omega, alpha, beta: GARCH 参数
        steps: 预测步数
        annualize: 是否年化
        trading_days: 年化交易日数

    Returns:
        年化波动率预测值
    """
    persistence = alpha + beta
    long_run_var = omega / max(1.0 - persistence, 1e-10)

    # 1 步预测
    sigma2_t1 = omega + alpha * previous_eps2 + beta * previous_sigma2

    if steps == 1:
        vol = math.sqrt(max(sigma2_t1, 0.0))
    else:
        # 多步预测: 向无条件方差均值回复
        sigma2_k = long_run_var + (persistence ** (steps - 1)) * (
            sigma2_t1 - long_run_var
        )
        vol = math.sqrt(max(sigma2_k, 0.0))

    return vol * math.sqrt(trading_days) if annualize else vol


def ewma_vol(
    daily_returns: list[float],
    lambda_: float = 0.94,
    annualize: bool = True,
    trading_days: int = 252,
) -> tuple[float, list[float]]:
    """EWMA 波动率 (基准对照组)

    σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}
    初始值: σ²_0 = 无条件方差

    返回 (最新年化波动率, 波动率序列)
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0, [0.0]

    # 初始方差 = 样本方差
    var_init = sum(r * r for r in daily_returns) / n
    var_series = [var_init]

    sigma2 = var_init
    for t in range(1, n):
        sigma2 = lambda_ * sigma2 + (1.0 - lambda_) * daily_returns[t - 1] ** 2
        var_series.append(max(sigma2, 1e-12))

    latest_vol = math.sqrt(var_series[-1])
    scale = math.sqrt(trading_days) if annualize else 1.0

    return latest_vol * scale, [math.sqrt(v) * scale for v in var_series]


def generate_comparison(
    daily_returns: list[float],
    date_str: str = "",
    ewma_lambda: float = 0.94,
) -> VolComparisonReport:
    """生成 GARCH vs EWMA 对照报告 (只读模式入口)

    Args:
        daily_returns: 日对数收益率序列
        date_str: 报告日期标识 (如 "2026-08-02")
        ewma_lambda: EWMA 衰减因子

    Returns:
        VolComparisonReport 包含双侧波动率及比率
    """
    garch = fit_garch(daily_returns)
    ewma_vol_val, _ = ewma_vol(daily_returns, lambda_=ewma_lambda)

    if garch.converged and ewma_vol_val > 0:
        ratio = garch.forecast_vol / ewma_vol_val
    else:
        ratio = float("nan")

    return VolComparisonReport(
        date=date_str,
        garch_vol=garch.forecast_vol,
        ewma_vol=ewma_vol_val,
        garch_vs_ewma_ratio=ratio,
        garch_persistence=garch.persistence,
        garch_converged=garch.converged,
        ewma_lambda=ewma_lambda,
    )
