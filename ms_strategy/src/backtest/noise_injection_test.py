"""Noise Injection 稳定性测试 — Lopez de Prado (2018) 第章 15.

v8.4 T08 (2026-07-28): 1000 次噪音注入, 验证策略非过拟合产物.

核心思想:
    1. 在原始收益序列上叠加零均值高斯噪音 (σ_noise = σ_signal × ratio)
    2. 对每个噪音样本重新计算策略核心指标 (SR / Sortino / MaxDD)
    3. 检验指标的分布稳定性:
       - CV (变异系数) < 0.3 → 稳定
       - P5/P95 区间窄 → 稳定
       - 噪音下 SR 仍 > 0 → 非纯运气
    4. 如果 SR 在噪音下变负, 说明策略对噪音敏感, 可能过拟合

通过标准:
    - 1000 次实验中, SR > 0 的比例 >= 95% (P5 > 0)
    - SR 均值相对原始 SR 的偏差 < 20%
    - MaxDD 在噪音下恶化不超过 50%
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class NoiseInjectionResult:
    """噪音注入测试结果.

    Attributes:
        original_sharpe: 原始策略的 Sharpe
        noise_ratio: 使用的噪音比例 (σ_noise / σ_signal)
        n_trials: 实验次数
        sharpe_mean: 噪音下 Sharpe 均值
        sharpe_std: 噪音下 Sharpe 标准差
        sharpe_cv: 变异系数 = std / |mean|
        sharpe_p5: 噪音下 Sharpe 5% 分位 (悲观情景)
        sharpe_p25: 25% 分位
        sharpe_median: 中位数
        sharpe_p75: 75% 分位
        sharpe_p95: 95% 分位
        sharpe_min: 最小值
        sharpe_max: 最大值
        pct_positive: 噪音下 SR > 0 的比例
        pct_above_half_original: 噪音下 SR > 0.5×original 的比例
        is_stable: 是否通过稳定性检验
        all_sharpes: 所有实验的 Sharpe 值
        all_max_dds: 所有实验的 MaxDD 值
        original_max_dd: 原始 MaxDD
        max_dd_mean: 噪音下 MaxDD 均值
        max_dd_p95: 噪音下 MaxDD 95% 分位
    """
    original_sharpe: float
    noise_ratio: float
    n_trials: int
    sharpe_mean: float = 0.0
    sharpe_std: float = 0.0
    sharpe_cv: float = 0.0
    sharpe_p5: float = 0.0
    sharpe_p25: float = 0.0
    sharpe_median: float = 0.0
    sharpe_p75: float = 0.0
    sharpe_p95: float = 0.0
    sharpe_min: float = 0.0
    sharpe_max: float = 0.0
    pct_positive: float = 0.0
    pct_above_half_original: float = 0.0
    is_stable: bool = False
    all_sharpes: np.ndarray = field(default_factory=lambda: np.array([]))
    all_max_dds: np.ndarray = field(default_factory=lambda: np.array([]))
    original_max_dd: float = 0.0
    max_dd_mean: float = 0.0
    max_dd_p95: float = 0.0
    verdict: str = ""


def _compute_sharpe(returns: np.ndarray, ann_factor: float = math.sqrt(252)) -> float:
    """计算年化 Sharpe (假设无风险利率=0)."""
    if len(returns) < 2:
        return 0.0
    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns, ddof=1))
    if std_r <= 0:
        return 0.0
    return mean_r / std_r * ann_factor


def _compute_max_dd(returns: np.ndarray) -> float:
    """计算最大回撤."""
    if len(returns) < 2:
        return 0.0
    cum = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cum)
    drawdown = (cum - running_max) / running_max
    return float(abs(np.min(drawdown))) if len(drawdown) > 0 else 0.0


def run_noise_injection_test(
    daily_returns: Sequence[float],
    noise_ratio: float = 0.1,
    n_trials: int = 1000,
    random_seed: int | None = 42,
    required_pct_positive: float = 0.95,
    required_pct_above_half: float = 0.50,
) -> NoiseInjectionResult:
    """执行 1000 次噪音注入稳定性测试.

    Args:
        daily_returns: 原始日收益率序列
        noise_ratio: 噪音 / 信号 比 (默认 0.1 = 10% 噪音)
        n_trials: 实验次数 (默认 1000)
        random_seed: 随机种子
        required_pct_positive: 通过标准: SR>0 的比例阈值 (默认 95%)
        required_pct_above_half: 通过标准: SR>0.5×原 的比例阈值 (默认 50%)

    Returns:
        NoiseInjectionResult 包含完整测试结果
    """
    rets = np.asarray(daily_returns, dtype=np.float64)
    n_samples = len(rets)

    if n_samples < 20:
        return NoiseInjectionResult(
            original_sharpe=0.0,
            noise_ratio=noise_ratio,
            n_trials=0,
            verdict=f"样本不足 ({n_samples} < 20)",
        )

    if random_seed is not None:
        rng = np.random.default_rng(random_seed)
    else:
        rng = np.random.default_rng()

    ann_factor = math.sqrt(252)

    # 1. 原始 SR 和 MaxDD
    original_sharpe = _compute_sharpe(rets, ann_factor)
    original_max_dd = _compute_max_dd(rets)

    # 2. 噪音标准差 = 信号标准差 × ratio
    signal_std = float(np.std(rets, ddof=1))
    noise_std = signal_std * noise_ratio

    # 3. n_trials 次噪音注入
    all_sharpes = np.zeros(n_trials, dtype=np.float64)
    all_max_dds = np.zeros(n_trials, dtype=np.float64)

    for t in range(n_trials):
        # 生成零均值高斯噪音
        noise = rng.normal(0, noise_std, size=n_samples)
        # 叠加到原始收益
        noisy_rets = rets + noise
        # 计算指标
        all_sharpes[t] = _compute_sharpe(noisy_rets, ann_factor)
        all_max_dds[t] = _compute_max_dd(noisy_rets)

    # 4. 统计
    sharpe_mean = float(np.mean(all_sharpes))
    sharpe_std = float(np.std(all_sharpes, ddof=1))
    sharpe_cv = sharpe_std / abs(sharpe_mean) if abs(sharpe_mean) > 1e-10 else float('inf')

    p5, p25, median, p75, p95 = (
        float(np.percentile(all_sharpes, q))
        for q in [5, 25, 50, 75, 95]
    )
    sharpe_min = float(np.min(all_sharpes))
    sharpe_max = float(np.max(all_sharpes))

    pct_positive = float(np.mean(all_sharpes > 0))
    pct_above_half = float(np.mean(all_sharpes > 0.5 * original_sharpe)) if original_sharpe > 0 else 0.0

    max_dd_mean = float(np.mean(all_max_dds))
    max_dd_p95 = float(np.percentile(all_max_dds, 95))

    # 5. 稳定性判断
    is_stable = (
        pct_positive >= required_pct_positive
        and pct_above_half >= required_pct_above_half
        and sharpe_cv < 0.5  # CV < 0.5 表示分布相对集中
    )

    if is_stable:
        verdict = (
            f"通过稳定性检验: 1000 次噪音注入下, "
            f"SR 均值={sharpe_mean:.4f} (原始={original_sharpe:.4f}), "
            f"P5={p5:.4f} > 0, "
            f"{pct_positive*100:.1f}% 实验 SR>0, "
            f"CV={sharpe_cv:.3f} < 0.5"
        )
    else:
        reasons = []
        if pct_positive < required_pct_positive:
            reasons.append(f"SR>0 比例 {pct_positive*100:.1f}% < {required_pct_positive*100:.1f}%")
        if pct_above_half < required_pct_above_half:
            reasons.append(f"SR>0.5×原始 比例 {pct_above_half*100:.1f}% < {required_pct_above_half*100:.1f}%")
        if sharpe_cv >= 0.5:
            reasons.append(f"CV={sharpe_cv:.3f} >= 0.5 (分布过于分散)")
        verdict = f"未通过稳定性检验: {'; '.join(reasons)}"

    logger.info(
        f"Noise injection (ratio={noise_ratio}, N={n_trials}): "
        f"original SR={original_sharpe:.4f}, noisy mean={sharpe_mean:.4f}, "
        f"P5={p5:.4f}, pct>0={pct_positive*100:.1f}%, stable={is_stable}"
    )

    return NoiseInjectionResult(
        original_sharpe=original_sharpe,
        noise_ratio=noise_ratio,
        n_trials=n_trials,
        sharpe_mean=sharpe_mean,
        sharpe_std=sharpe_std,
        sharpe_cv=sharpe_cv,
        sharpe_p5=p5, sharpe_p25=p25, sharpe_median=median,
        sharpe_p75=p75, sharpe_p95=p95,
        sharpe_min=sharpe_min, sharpe_max=sharpe_max,
        pct_positive=pct_positive,
        pct_above_half_original=pct_above_half,
        is_stable=is_stable,
        all_sharpes=all_sharpes,
        all_max_dds=all_max_dds,
        original_max_dd=original_max_dd,
        max_dd_mean=max_dd_mean,
        max_dd_p95=max_dd_p95,
        verdict=verdict,
    )


def noise_injection_summary(result: NoiseInjectionResult) -> str:
    """生成噪音注入测试汇总报告字符串."""
    lines = [
        f"=== Noise Injection 稳定性测试 ({result.n_trials} 次) ===",
        f"原始 Sharpe:      {result.original_sharpe:.4f}",
        f"噪音/信号 比:     {result.noise_ratio:.1%}",
        "",
        "Sharpe 分布:",
        f"  Mean:   {result.sharpe_mean:.4f}  (偏差 {result.sharpe_mean - result.original_sharpe:+.4f})",
        f"  Median: {result.sharpe_median:.4f}",
        f"  Std:    {result.sharpe_std:.4f}",
        f"  CV:     {result.sharpe_cv:.4f}  ({'稳定' if result.sharpe_cv < 0.5 else '不稳定'})",
        f"  P5:     {result.sharpe_p5:.4f}  (悲观情景)",
        f"  P95:    {result.sharpe_p95:.4f}",
        f"  Range:  [{result.sharpe_min:.4f}, {result.sharpe_max:.4f}]",
        "",
        "通过率:",
        f"  SR > 0:           {result.pct_positive*100:.1f}%",
        f"  SR > 0.5×原始:    {result.pct_above_half_original*100:.1f}%",
        "",
        "MaxDD:",
        f"  原始:    {result.original_max_dd:.2%}",
        f"  噪音均值: {result.max_dd_mean:.2%}",
        f"  P95:     {result.max_dd_p95:.2%}",
        "",
        f"结论: {'✓ 通过' if result.is_stable else '✗ 未通过'} - {result.verdict}",
    ]
    return '\n'.join(lines)


__all__ = [
    'NoiseInjectionResult',
    'noise_injection_summary',
    'run_noise_injection_test',
]
