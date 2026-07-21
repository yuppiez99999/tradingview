# -*- coding: utf-8 -*-
"""
Deflated Sharpe Ratio v1.0 — Bailey & Lopez de Prado 方法

参考论文:
    Bailey, D. H., & Lopez de Prado, M. (2014).
    "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting,
    and Non-Normality." Journal of Portfolio Management.

核心思想:
    标准夏普比率在多重测试场景下存在严重的选取偏差 —
    试过 N 种策略后, 最好的那一个"纯属运气好"的概率极高。
    DSR (Deflated Sharpe Ratio) 通过估计零均值策略的最高预期夏普,
    将观测到的夏普与"纯噪音下期望最大值"进行比较,
    给出一个经多重测试修正后的概率 — P(策略优于噪音)。

算法概要:
    1. 计算标准夏普比率 SR*
    2. 估计 Sharpe Ratio 的分布参数 (偏度/峰度修正)
    3. 估计 E[max{SR}] 在 N 个独立试验下 (使用极值理论)
    4. 计算 DSR = P(SR > E[max{SR}] | SR ~ 非中心t分布)
    5. P(DSR) 即为去膨胀后的显著性水平

用法:
    from utils.deflated_sharpe import deflated_sharpe_ratio, dsr_threshold_check

    dsr, p_value, is_pass = deflated_sharpe_ratio(
        daily_returns, n_trials=100, required_dsr=0.95
    )
"""

import math
from typing import Tuple, Optional
from dataclasses import dataclass


@dataclass
class DeflatedSharpeResult:
    """DSR 计算结果"""
    sharpe_ratio: float           # 观测到的年化夏普比率
    deflated_sharpe_ratio: float   # 去膨胀后的夏普比率 (DSR)
    p_value: float                # DSR 对应的 p-value
    e_max_sr: float               # 纯噪音下 N 次试验的期望最大夏普
    n_trials: int                 # 校正试验次数
    skewness: float               # 收益率偏度
    kurtosis: float               # 收益率超额峰度
    is_pass: bool                 # 是否通过阈值检验
    required_dsr: float           # 要求的 DSR 阈值
    verdict: str                  # 判断结论


def _compute_moments(returns: list) -> Tuple[float, float, float, float, int]:
    """
    计算收益率的前四阶矩: 均值, 标准差, 偏度, 超额峰度

    Returns:
        (mean, std, skewness, excess_kurtosis, n)
    """
    n = len(returns)
    if n < 4:
        return 0.0, 0.0, 0.0, 0.0, n

    mean = sum(returns) / n
    # 样本标准差 (ddof=1)
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var) if var > 0 else 1e-10

    if std < 1e-15:
        return mean, std, 0.0, 0.0, n

    # 偏度
    m3 = sum((r - mean) ** 3 for r in returns) / n
    skewness = m3 / (std ** 3)

    # 超额峰度 (excess kurtosis)
    m4 = sum((r - mean) ** 4 for r in returns) / n
    kurtosis = m4 / (std ** 4) - 3.0

    return mean, std, skewness, kurtosis, n


def _annualize(mean_daily: float, std_daily: float) -> Tuple[float, float]:
    """日度转年化 (252 交易日)"""
    ann_factor = math.sqrt(252)
    return mean_daily * 252, std_daily * ann_factor


def _estimate_e_max_sr(
    n_trials: int,
    sample_length: int,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
) -> float:
    """
    估计 N 次独立试验下纯噪音策略的期望最大夏普比率。

    理论依据 (Bailey & Lopez de Prado 2014, Eq. 4-5):
        E[max{SR}] ≈ sqrt(Var[SR]) * ((1-gamma)*Z^{-1}(1-1/N) + gamma*Z^{-1}(1-1/(N*e)))

    其中 gamma ≈ 0.5772156649 (欧拉常数),
    Var[SR] 来自 Edgeworth 展开对方差/偏度/峰度的修正。

    Args:
        n_trials: 独立试验次数 (试过多少种策略变体)
        sample_length: 样本长度 (日历天数/交易日数, 用于年化转换)
        skewness: 日收益率偏度
        kurtosis: 日收益率超额峰度

    Returns:
        E[max{SR}] 年化
    """
    if n_trials <= 1:
        return 0.0

    gamma_euler = 0.5772156649015329

    # Var[SR] 的理论方差 (Lo 2002; Opdyke 2007)
    # 简化近似: Var[SR] ≈ (1 + 0.5*SR^2 - skew*SR + 0.25*(kurt-1)*SR^2) / T
    # 对于零均值策略 (SR≈0):
    #   Var[SR] ≈ 1/T
    # 考虑非正态调整:
    #   Var[SR] ≈ (1 + skew^2/4 - 2*skew*SR + (kurt/4)*SR^2) / T
    # 当 SR=0 时: Var[SR] ≈ (1 + skew^2/4) / T
    T = max(sample_length, 20)  # 交易日数
    var_sr = (1.0 + skewness ** 2 / 4.0) / T

    std_sr = math.sqrt(max(var_sr, 1e-10))

    # 标准正态分位数近似
    # Z^{-1}(1 - 1/N) — 使用 Blom 近似
    p_n = 1.0 - 1.0 / n_trials
    z_n = _norm_ppf_approx(p_n)

    # Z^{-1}(1 - 1/(N*e))
    p_ne = 1.0 - 1.0 / (n_trials * math.e)
    z_ne = _norm_ppf_approx(p_ne)

    # E[max{SR}] 年化
    e_max = std_sr * ((1.0 - gamma_euler) * z_n + gamma_euler * z_ne)

    # 年化调整: E[max{SR_ann}] = E[max{SR_daily}] * sqrt(252)
    e_max_ann = e_max * math.sqrt(252)

    return e_max_ann


def _norm_ppf_approx(p: float) -> float:
    """
    标准正态分布分位数的多项式近似 (Abramowitz & Stegun 26.2.23)

    精度: |error| < 4.5e-4 for 0 < p < 1
    """
    if p <= 0.0:
        return -10.0
    if p >= 1.0:
        return 10.0

    # 针对上尾: 用 1-p 计算
    q = min(p, 1.0 - p)
    if q < 1e-16:
        return 10.0 if p > 0.5 else -10.0

    t = math.sqrt(-2.0 * math.log(q))

    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308

    z = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)

    if p < 0.5:
        z = -z

    return z


def deflated_sharpe_ratio(
    daily_returns: list,
    n_trials: int = 100,
    required_dsr: float = 0.95,
    risk_free_rate: float = 0.03,
) -> DeflatedSharpeResult:
    """
    计算 Deflated Sharpe Ratio

    Args:
        daily_returns: 日收益率序列 (策略净值日变动率)
        n_trials: 独立策略试验次数 (试过的策略变体总数)
        required_dsr: DSR 阈值 (默认 0.95, 即要求 95% 不是过拟合产物)
        risk_free_rate: 年化无风险利率 (默认 3%)

    Returns:
        DeflatedSharpeResult 包含所有计算细节
    """
    n = len(daily_returns)
    if n < 20:
        return DeflatedSharpeResult(
            sharpe_ratio=0.0, deflated_sharpe_ratio=0.0,
            p_value=1.0, e_max_sr=0.0, n_trials=n_trials,
            skewness=0.0, kurtosis=0.0, is_pass=False,
            required_dsr=required_dsr,
            verdict=f"数据不足: 仅{n}个观测, 需要至少20个",
        )

    # 1. 计算收益率矩
    mean_daily, std_daily, skewness, kurtosis, n = _compute_moments(daily_returns)

    if std_daily < 1e-15:
        return DeflatedSharpeResult(
            sharpe_ratio=0.0, deflated_sharpe_ratio=0.0,
            p_value=1.0, e_max_sr=0.0, n_trials=n_trials,
            skewness=skewness, kurtosis=kurtosis, is_pass=False,
            required_dsr=required_dsr,
            verdict="零波动率策略, 无法评估",
        )

    # 2. 年化夏普
    excess_daily = mean_daily - risk_free_rate / 252
    ann_excess = excess_daily * 252
    ann_vol = std_daily * math.sqrt(252)
    sr = ann_excess / ann_vol if ann_vol > 0 else 0.0

    # 3. 估计 E[max{SR}] — 纯噪音下 N 次试验的期望最优夏普
    e_max_sr = _estimate_e_max_sr(
        n_trials=n_trials,
        sample_length=n,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    # 4. 计算 DSR
    # DSR = P(SR > E[max{SR}])
    # 使用非中心 t 分布的 Edgeworth 展开近似
    # SR 的渐进分布: sqrt(T) * (sr_hat - sr_true) / sqrt(1 + 0.5*sr_true^2) → N(0,1)

    # 简化: 使用正态近似计算 P(SR > e_max)
    # Var[SR_hat] ≈ (1 + 0.5 * sr^2) / T
    T = n / 252.0  # 年数
    if T < 0.01:
        T = 0.01

    se_sr = math.sqrt((1.0 + 0.5 * sr ** 2 - skewness * sr * math.sqrt(1/252)
                       + (kurtosis / 4.0) * sr ** 2)
                      / (T * 252))

    if se_sr < 1e-15:
        z_score = 10.0 if sr > e_max_sr else -10.0
    else:
        z_score = (sr - e_max_sr) / se_sr

    # 正态 CDF 近似
    dsr_p_value = _norm_cdf_approx(-z_score)  # P(SR > e_max) = 1 - cdf(z)
    dsr = 1.0 - dsr_p_value

    # 5. 判断
    is_pass = dsr >= required_dsr

    if is_pass:
        verdict = (
            f"DSR={dsr:.4f} >= {required_dsr}, 通过 — "
            f"观测夏普{sr:.2f}显著优于{n_trials}次试验的噪音上限{e_max_sr:.2f}"
        )
    else:
        shortfall = required_dsr - dsr
        verdict = (
            f"DSR={dsr:.4f} < {required_dsr}, 未通过 — "
            f"观测夏普{sr:.2f}可能为{n_trials}次试验中的噪音极值(上限{e_max_sr:.2f}), "
            f"差距{shortfall:.4f}"
        )

    return DeflatedSharpeResult(
        sharpe_ratio=sr,
        deflated_sharpe_ratio=dsr,
        p_value=dsr_p_value,
        e_max_sr=e_max_sr,
        n_trials=n_trials,
        skewness=skewness,
        kurtosis=kurtosis,
        is_pass=is_pass,
        required_dsr=required_dsr,
        verdict=verdict,
    )


def _norm_cdf_approx(z: float) -> float:
    """
    标准正态 CDF 的数值近似 (Hart 1968, 误差 < 1e-15)
    """
    if z < -8.0:
        return 0.0
    if z > 8.0:
        return 1.0

    # 使用 math.erf
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def dsr_threshold_check(
    daily_returns: list,
    n_trials: int = 100,
    required_dsr: float = 0.95,
) -> Tuple[bool, str, DeflatedSharpeResult]:
    """
    便捷函数: DSR 阈值检查

    Returns:
        (是否通过, 结论描述, 详细结果)
    """
    result = deflated_sharpe_ratio(daily_returns, n_trials, required_dsr)
    return result.is_pass, result.verdict, result


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import random

    random.seed(42)

    # 生成正夏普的模拟收益率 (SR ≈ 1.0)
    good_rets = [random.gauss(0.001, 0.015) for _ in range(1260)]  # 5年
    result_good = deflated_sharpe_ratio(good_rets, n_trials=100, required_dsr=0.95)
    print(f"正夏普策略: SR={result_good.sharpe_ratio:.3f}, "
          f"DSR={result_good.deflated_sharpe_ratio:.4f}, "
          f"E[max_SR]={result_good.e_max_sr:.3f}, "
          f"通过={result_good.is_pass}")
    print(f"  {result_good.verdict}")

    # 生成零夏普的模拟收益率 (纯噪音)
    noise_rets = [random.gauss(0.0, 0.02) for _ in range(1260)]
    result_noise = deflated_sharpe_ratio(noise_rets, n_trials=200, required_dsr=0.95)
    print(f"\n噪音策略: SR={result_noise.sharpe_ratio:.3f}, "
          f"DSR={result_noise.deflated_sharpe_ratio:.4f}, "
          f"E[max_SR]={result_noise.e_max_sr:.3f}, "
          f"通过={result_noise.is_pass}")
    print(f"  {result_noise.verdict}")
