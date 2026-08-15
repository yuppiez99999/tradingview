"""
卡尔曼滤波时变 Beta 估计 (Kalman Filter Time-Varying Beta)

只读对照模块 — 与现有滚动 OLS Beta 做对冲效率回测对比。
仅当 Kalman 对冲后方差下降 ≥5% 且通过 Walk-Forward 验证，才申请接入 beta_hedger。

状态空间模型:
    观测方程:  r_portfolio,t = β_t · r_index,t + ε_t,   ε_t ~ N(0, R)
    状态方程:  β_t = β_{t-1} + η_t,                    η_t ~ N(0, Q)

    其中:
    - β_t: 时变对冲比率 (局部水平模型, Random Walk)
    - Q: 状态噪声方差 (控制 β 时变速度快慢)
    - R: 观测噪声方差 (对冲误差)

卡尔曼滤波递推 (标量状态):
    预测:
        β_{t|t-1}  = β_{t-1|t-1}
        P_{t|t-1}  = P_{t-1|t-1} + Q
    更新:
        K_t        = P_{t|t-1} · x_t / (x²_t · P_{t|t-1} + R)
        β_{t|t}    = β_{t|t-1} + K_t · (y_t - x_t · β_{t|t-1})
        P_{t|t}    = (1 - K_t · x_t) · P_{t|t-1}

设计原则:
    1. 零外部依赖 — 仅 math 标准库
    2. fail-closed — 滤波发散时回退 OLS Beta
    3. 纯函数 — 确定性输入输出

参考:
    Kalman, R. E. (1960). "A New Approach to Linear Filtering and Prediction Problems"
    Kim, C. J. & Nelson, C. R. (1999). "State-Space Models with Regime Switching"

Usage:
    from utils.fineng.kalman_beta import fit_kalman_beta
    result = fit_kalman_beta(portfolio_returns, index_returns)
    logger.info("最新时变 Beta: %.4f", result.filtered_beta[-1])
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ============================================================
# 结果数据结构
# ============================================================


@dataclass
class KalmanBetaResult:
    """卡尔曼滤波时变 Beta 拟合结果"""

    filtered_beta: list[float]            # 滤波后 Beta 序列 β_{t|t}
    predicted_beta: list[float]           # 预测 Beta 序列 β_{t|t-1}
    filtered_variance: list[float]        # 滤波误差方差 P_{t|t}
    predicted_variance: list[float]       # 预测误差方差 P_{t|t-1}
    kalman_gain: list[float]              # 卡尔曼增益 K_t

    # 参数
    Q: float                              # 状态噪声方差
    R: float                              # 观测噪声方差

    # 摘要统计
    mean_beta: float                      # 平均 Beta
    std_beta: float                       # Beta 标准差 (时变程度)
    min_beta: float
    max_beta: float
    latest_beta: float                    # 最新 Beta (用于当前对冲)

    # 诊断
    converged: bool                       # 滤波是否收敛
    effective_days: int                   # 有效估计天数
    error_message: str = ""


@dataclass
class BetaHedgeComparison:
    """Kalman vs Rolling OLS 对冲效率回测对比"""

    date: str
    kalman_beta: float
    ols_beta: float
    kalman_hedged_variance: float         # Kalman 对冲后组合方差
    ols_hedged_variance: float            # OLS 对冲后组合方差
    variance_reduction_pct: float         # 方差下降百分比 (正值=Kalman 更优)
    kalman_beta_std: float                # Beta 时变标准差
    meets_threshold: bool                 # 方差下降 ≥ 5%
    walk_forward_result: str = "pending"  # Walk-Forward 闸门状态


# ============================================================
# OLS 基准
# ============================================================


def rolling_ols_beta(
    y: list[float],
    x: list[float],
    window: int = 120,
) -> list[float]:
    """滚动窗口 OLS Beta 估计

    β = Cov(x, y) / Var(x)  (单因子模型, 截距=0 近似)

    Args:
        y: 组合收益率序列
        x: 指数收益率序列
        window: 滚动窗口大小

    Returns:
        滚动 Beta 序列, 前 window-1 个值为 NaN
    """
    n = min(len(y), len(x))
    if n < window:
        return [float("nan")] * n

    betas: list[float] = [float("nan")] * (window - 1)

    for t in range(window - 1, n):
        start = t - window + 1
        end = t + 1
        x_win = x[start:end]
        y_win = y[start:end]

        # Cov(x, y) / Var(x)
        mx = sum(x_win) / window
        my = sum(y_win) / window

        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x_win, y_win)) / (window - 1)
        var_x = sum((xi - mx) ** 2 for xi in x_win) / (window - 1)

        if var_x > 1e-12:
            betas.append(cov / var_x)
        else:
            betas.append(float("nan"))

    return betas


# ============================================================
# 卡尔曼滤波递推 (标量状态)
# ============================================================


def _kalman_filter_scalar(
    y: list[float],
    x: list[float],
    Q: float,
    R: float,
    beta0: float = 1.0,
    P0: float = 1.0,
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """标量状态卡尔曼滤波递推

    局部水平模型: β_t = β_{t-1} + η_t,  y_t = x_t·β_t + ε_t

    Returns:
        (filtered_beta, predicted_beta, filtered_P, predicted_P, K)
    """
    n = min(len(y), len(x))

    beta_filtered = beta0
    P_filtered = P0

    filtered_beta: list[float] = []
    predicted_beta: list[float] = []
    filtered_P: list[float] = []
    predicted_P: list[float] = []
    gains: list[float] = []

    for t in range(n):
        # ---- 预测 ----
        beta_pred = beta_filtered
        P_pred = P_filtered + Q

        # ---- 更新 ----
        x_t = x[t] if abs(x[t]) > 1e-10 else 1e-10
        # 卡尔曼增益 (守护: 防止 R=0 且 P_pred=0 时分母为零)
        denom = x_t * x_t * P_pred + R
        if abs(denom) < 1e-12:
            K = 0.0
        else:
            K = P_pred * x_t / denom
        # 新息 (Innovation)
        innovation = y[t] - x_t * beta_pred
        # 更新状态
        beta_filtered = beta_pred + K * innovation
        P_filtered = (1.0 - K * x_t) * P_pred

        # 收集
        filtered_beta.append(beta_filtered)
        predicted_beta.append(beta_pred)
        filtered_P.append(P_filtered)
        predicted_P.append(P_pred)
        gains.append(K)

        # 发散检测: P_filtered 不应爆炸
        if P_filtered > P0 * 100:
            # 重置滤波 (软重置)
            P_filtered = P0

    return filtered_beta, predicted_beta, filtered_P, predicted_P, gains


def _estimate_q_r(
    y: list[float],
    x: list[float],
) -> tuple[float, float]:
    """启发式估计 Q 和 R

    Q (状态噪声): 控制 Beta 时变速度
        - Q 大 → Beta 可快速变化, 但噪声大
        - Q 小 → Beta 平滑, 但适应慢
        启发式: Q = Var(Δβ_ols) * 0.1 (假设 Beta 变化缓慢)

    R (观测噪声): 对冲误差方差
        - 直接用 OLS 残差方差估计
    """
    n = min(len(y), len(x))
    if n < 120:
        return 0.001, 0.001

    # OLS 估计整体 Beta
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    var_x = sum((xi - mx) ** 2 for xi in x) / n

    if var_x < 1e-12:
        return 0.001, 0.001

    ols_beta = cov / var_x

    # R = 残差方差
    residuals = [yi - ols_beta * xi for yi, xi in zip(y, x)]
    R = sum(r * r for r in residuals) / max(n - 1, 1)
    R = max(R, 1e-8)

    # Q: 粗略估计 — 使用滚动 OLS Beta 的差分方差
    window = min(60, n // 3)
    rolling_betas = rolling_ols_beta(y, x, window=window)
    valid_betas = [b for b in rolling_betas if not math.isnan(b)]
    if len(valid_betas) > 1:
        db = [valid_betas[i] - valid_betas[i - 1] for i in range(1, len(valid_betas))]
        var_db = sum(d * d for d in db) / max(len(db) - 1, 1)
        Q = var_db * 0.05  # 保守平滑
    else:
        Q = 0.0001

    Q = max(Q, 1e-8)
    return Q, R


# ============================================================
# 公共 API
# ============================================================


def fit_kalman_beta(
    portfolio_returns: list[float],
    index_returns: list[float],
    Q: float | None = None,
    R: float | None = None,
    min_history: int = 120,
) -> KalmanBetaResult:
    """拟合卡尔曼滤波时变 Beta

    Args:
        portfolio_returns: 组合日收益率序列
        index_returns: 指数日收益率序列 (如 IF 期货或沪深300)
        Q: 状态噪声方差, None 则自动估计
        R: 观测噪声方差, None 则自动估计
        min_history: 最小样本量, 默认 120 日

    Returns:
        KalmanBetaResult 含完整滤波序列和摘要统计

    Fail-Closed 行为:
        - 样本量不足 → converged=False, 返回 OLS Beta 作为 latest_beta
        - 滤波发散 (P 爆炸) → 内置软重置机制
        - 无有效指数方差 → converged=False
    """
    n = min(len(portfolio_returns), len(index_returns))

    # ---- 样本量检查 ----
    if n < min_history:
        ols_beta_val = 1.0
        if n > 1:
            mx = sum(index_returns) / n
            my = sum(portfolio_returns) / n
            var_x = sum((xi - mx) ** 2 for xi in index_returns) / n
            if var_x > 1e-12:
                cov = sum(
                    (xi - mx) * (yi - my)
                    for xi, yi in zip(index_returns, portfolio_returns)
                ) / n
                ols_beta_val = cov / var_x

        return KalmanBetaResult(
            filtered_beta=[],
            predicted_beta=[],
            filtered_variance=[],
            predicted_variance=[],
            kalman_gain=[],
            Q=0.0,
            R=0.0,
            mean_beta=ols_beta_val,
            std_beta=0.0,
            min_beta=ols_beta_val,
            max_beta=ols_beta_val,
            latest_beta=ols_beta_val,
            converged=False,
            effective_days=0,
            error_message=f"样本量不足: {n} < {min_history}",
        )

    # ---- 参数估计 ----
    if Q is None or R is None:
        q_est, r_est = _estimate_q_r(portfolio_returns, index_returns)
        Q = Q if Q is not None else q_est
        R = R if R is not None else r_est

    # ---- 初始化 ----
    # 用前 30 日 OLS 结果做初始 Beta
    if n >= 30:
        init_x = index_returns[:30]
        init_y = portfolio_returns[:30]
        mx = sum(init_x) / 30
        my = sum(init_y) / 30
        var_x = sum((xi - mx) ** 2 for xi in init_x) / 29
        if var_x > 1e-12:
            cov = sum(
                (xi - mx) * (yi - my) for xi, yi in zip(init_x, init_y)
            ) / 29
            beta0 = cov / var_x
        else:
            beta0 = 1.0
    else:
        beta0 = 1.0

    # ---- 卡尔曼滤波 ----
    (filtered_beta, predicted_beta,
     filtered_P, predicted_P, gains) = _kalman_filter_scalar(
        y=portfolio_returns,
        x=index_returns,
        Q=Q,
        R=R,
        beta0=beta0,
        P0=1.0,
    )

    # ---- 收敛检查 ----
    # 检查最终 P_filtered 是否合理 (应远小于初始 P0)
    converged = bool(filtered_P and filtered_P[-1] < 0.5)

    # ---- 摘要统计 ----
    valid_betas = [b for b in filtered_beta if not math.isnan(b)]
    if not valid_betas:
        return KalmanBetaResult(
            filtered_beta=filtered_beta,
            predicted_beta=predicted_beta,
            filtered_variance=filtered_P,
            predicted_variance=predicted_P,
            kalman_gain=gains,
            Q=Q,
            R=R,
            mean_beta=beta0,
            std_beta=0.0,
            min_beta=beta0,
            max_beta=beta0,
            latest_beta=beta0,
            converged=False,
            effective_days=0,
            error_message="滤波后无有效 Beta 值",
        )

    mean_beta = sum(valid_betas) / len(valid_betas)
    # 使用最后 60 日的标准差 (更稳定)
    tail = valid_betas[-min(60, len(valid_betas)):]
    std_beta = math.sqrt(
        sum((b - mean_beta) ** 2 for b in tail) / max(len(tail) - 1, 1)
    ) if len(tail) > 1 else 0.0

    return KalmanBetaResult(
        filtered_beta=filtered_beta,
        predicted_beta=predicted_beta,
        filtered_variance=filtered_P,
        predicted_variance=predicted_P,
        kalman_gain=gains,
        Q=Q,
        R=R,
        mean_beta=mean_beta,
        std_beta=std_beta,
        min_beta=min(valid_betas),
        max_beta=max(valid_betas),
        latest_beta=valid_betas[-1] if valid_betas else 1.0,
        converged=converged,
        effective_days=n,
    )


def backtest_hedge_comparison(
    portfolio_returns: list[float],
    index_returns: list[float],
    ols_window: int = 120,
    kalman_Q: float | None = None,
    kalman_R: float | None = None,
    date_str: str = "",
) -> BetaHedgeComparison:
    """回测 Kalman vs Rolling OLS 对冲效率对比

    构建对冲后组合: r_hedged = r_portfolio - β_t * r_index
    比较两方法的对冲后方差。

    Args:
        portfolio_returns: 组合收益率
        index_returns: 指数收益率
        ols_window: OLS 滚动窗口
        kalman_Q, kalman_R: 卡尔曼参数, None 则自动估计
        date_str: 报告日期

    Returns:
        BetaHedgeComparison 含双侧方差及改善百分比
    """
    kalman = fit_kalman_beta(portfolio_returns, index_returns, Q=kalman_Q, R=kalman_R)
    ols_betas = rolling_ols_beta(portfolio_returns, index_returns, window=ols_window)

    n = min(len(portfolio_returns), len(index_returns))

    # 计算对冲后组合收益
    kalman_hedged: list[float] = []
    ols_hedged: list[float] = []

    for t in range(n):
        if t >= len(kalman.predicted_beta):
            break

        k_beta = kalman.predicted_beta[t]
        # 限制异常值
        k_beta = max(min(k_beta, 3.0), -1.0)
        kalman_hedged.append(portfolio_returns[t] - k_beta * index_returns[t])

    for t in range(n):
        if t >= len(ols_betas):
            break
        o_beta = ols_betas[t]
        if math.isnan(o_beta):
            continue
        o_beta = max(min(o_beta, 3.0), -1.0)
        ols_hedged.append(portfolio_returns[t] - o_beta * index_returns[t])

    # 方差
    def calc_var(series: list[float]) -> float:
        if len(series) < 2:
            return float("inf")
        m = sum(series) / len(series)
        return sum((x - m) ** 2 for x in series) / (len(series) - 1)

    kalman_var = calc_var(kalman_hedged)
    ols_var = calc_var(ols_hedged)

    if ols_var > 1e-12:
        reduction_pct = (ols_var - kalman_var) / ols_var * 100.0
    else:
        reduction_pct = 0.0

    meets = reduction_pct >= 5.0 and kalman.converged

    return BetaHedgeComparison(
        date=date_str,
        kalman_beta=kalman.latest_beta,
        ols_beta=ols_betas[-1] if ols_betas and not math.isnan(ols_betas[-1]) else 1.0,
        kalman_hedged_variance=kalman_var,
        ols_hedged_variance=ols_var,
        variance_reduction_pct=reduction_pct,
        kalman_beta_std=kalman.std_beta,
        meets_threshold=meets,
    )
