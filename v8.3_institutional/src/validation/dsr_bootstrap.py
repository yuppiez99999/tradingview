# -*- coding: utf-8 -*-
"""DSR Bootstrap 估计器 — 替代解析近似的 E[SR_max] 估计.

v8.4 T07 (2026-07-28): 用 bootstrap 替代极值理论解析近似, 提高小样本精度.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BootstrapEMaxResult:
    """Bootstrap E[SR_max] 估计结果."""

    e_max_sr: float
    std: float
    p5: float
    p25: float
    median: float
    p75: float
    p95: float
    n_bootstrap: int
    n_trials: int
    sample_length: int
    all_e_max: np.ndarray = field(default_factory=lambda: np.array([]))


def estimate_e_max_sr_bootstrap(
    daily_returns: Sequence[float],
    n_trials: int = 100,
    n_bootstrap: int = 1000,
    random_seed: Optional[int] = None,
) -> BootstrapEMaxResult:
    """用 bootstrap 估计 N 次独立试验下的 E[SR_max].

    算法:
        1. 将原始收益序列减去均值 (变为零均值噪音)
        2. 对零均值噪音做 B 次 bootstrap 重采样
        3. 每次重采样模拟 N 个独立"噪音策略"的 SR, 取最大值
        4. E[SR_max] = mean(B 次实验的最大值)

    Notes:
        - 关键: 必须先去均值, 否则 bootstrap 重采样保留了原始均值,
          导致噪音策略 SR 偏高, DSR 永远为 0
    """
    if random_seed is not None:
        rng = np.random.default_rng(random_seed)
    else:
        rng = np.random.default_rng()

    rets = np.asarray(daily_returns, dtype=np.float64)
    n_samples = len(rets)

    if n_samples < 20:
        logger.warning(f"样本数 {n_samples} < 20, bootstrap 结果不可靠")
        return BootstrapEMaxResult(
            e_max_sr=0.0,
            std=0.0,
            p5=0.0,
            p25=0.0,
            median=0.0,
            p75=0.0,
            p95=0.0,
            n_bootstrap=0,
            n_trials=n_trials,
            sample_length=n_samples,
            all_e_max=np.array([]),
        )

    # 关键: 去均值, 使其变为零均值噪音
    noise_rets = rets - np.mean(rets)

    ann_factor = math.sqrt(252)
    all_e_max = np.zeros(n_bootstrap, dtype=np.float64)

    for b in range(n_bootstrap):
        # 模拟 N 个独立噪音策略
        noise_srs = np.zeros(n_trials, dtype=np.float64)
        for t in range(n_trials):
            # 每个噪音策略独立 bootstrap 重采样
            trial_idx = rng.integers(0, n_samples, size=n_samples)
            trial_rets = noise_rets[trial_idx]
            mean_t = float(np.mean(trial_rets))
            std_t = float(np.std(trial_rets, ddof=1))
            if std_t > 0:
                noise_srs[t] = mean_t / std_t * ann_factor
            else:
                noise_srs[t] = 0.0

        all_e_max[b] = float(np.max(noise_srs))

    e_max_mean = float(np.mean(all_e_max))
    e_max_std = float(np.std(all_e_max, ddof=1))
    p5, p25, median, p75, p95 = (float(np.percentile(all_e_max, q)) for q in [5, 25, 50, 75, 95])

    logger.info(
        f"Bootstrap E[SR_max] (N={n_trials}, B={n_bootstrap}, T={n_samples}): "
        f"mean={e_max_mean:.4f}, median={median:.4f}, "
        f"P5={p5:.4f}, P95={p95:.4f}, std={e_max_std:.4f}"
    )

    return BootstrapEMaxResult(
        e_max_sr=e_max_mean,
        std=e_max_std,
        p5=p5,
        p25=p25,
        median=median,
        p75=p75,
        p95=p95,
        n_bootstrap=n_bootstrap,
        n_trials=n_trials,
        sample_length=n_samples,
        all_e_max=all_e_max,
    )


def _norm_cdf(z: float) -> float:
    """标准正态 CDF."""
    if z < -8.0:
        return 0.0
    if z > 8.0:
        return 1.0
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def deflated_sharpe_ratio_bootstrap(
    daily_returns: Sequence[float],
    n_trials: int = 100,
    n_bootstrap: int = 1000,
    required_dsr: float = 0.95,
    risk_free_rate: float = 0.03,
    random_seed: Optional[int] = 42,
) -> Tuple[float, float, bool, "BootstrapEMaxResult"]:
    """用 bootstrap 估计 E[SR_max] 后计算 DSR.

    Returns:
        (dsr, p_value, is_pass, bootstrap_result)
    """
    rets = np.asarray(daily_returns, dtype=np.float64)
    n_samples = len(rets)

    if n_samples < 20:
        return 0.0, 1.0, False, estimate_e_max_sr_bootstrap(daily_returns, n_trials, 0, random_seed)

    mean_daily = float(np.mean(rets))
    std_daily = float(np.std(rets, ddof=1))

    if std_daily < 1e-15:
        return 0.0, 1.0, False, estimate_e_max_sr_bootstrap(daily_returns, n_trials, 0, random_seed)

    excess_daily = mean_daily - risk_free_rate / 252
    sr = excess_daily / std_daily * math.sqrt(252)

    boot_result = estimate_e_max_sr_bootstrap(daily_returns, n_trials, n_bootstrap, random_seed)

    # SR 的标准误 (Lo 2002)
    T = n_samples
    se_sr = math.sqrt(max((1.0 + 0.5 * sr**2) / T, 1e-10))

    if se_sr < 1e-15:
        z_score = 10.0 if sr > boot_result.e_max_sr else -10.0
    else:
        z_score = (sr - boot_result.e_max_sr) / se_sr

    dsr = _norm_cdf(z_score)
    p_value = 1.0 - dsr
    is_pass = dsr >= required_dsr

    logger.info(f"DSR (bootstrap): SR={sr:.4f}, E[max]={boot_result.e_max_sr:.4f}, DSR={dsr:.4f}, pass={is_pass}")

    return dsr, p_value, is_pass, boot_result


__all__ = [
    "BootstrapEMaxResult",
    "deflated_sharpe_ratio_bootstrap",
    "estimate_e_max_sr_bootstrap",
]
