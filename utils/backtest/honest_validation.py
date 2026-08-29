"""诚实回测三件套编排器 — CPCV + DSR + Noise 残差注入

将分散在 ms_strategy 的三件套统一编排为单一入口:
1. CPCV (T06): 组合清洗交叉验证 → 多路径 Sharpe 分布
2. DSR (T07): Deflated Sharpe Ratio → 多重检验修正
3. Noise (T08): 噪音注入稳定性测试 → 过拟合检验

三件套联合判定:
    PASS = DSR.is_pass AND Noise.is_stable AND CPCV 路径间 SR CV < 0.5

参考:
    - Lopez de Prado (2018) AFML Chapter 7/8/15
    - ms_strategy/src/backtest/combinatorial_purged_cv.py (CPCV 内核)
    - utils/backtest/deflated_sharpe.py (DSR 顶层模块, W6.6.2 修复)
    - ms_strategy/src/backtest/noise_injection_test.py (Noise 内核)
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from utils.backtest.deflated_sharpe import DSRResult, deflated_sharpe_ratio

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


# ============================================================
# 结果数据类
# ============================================================


@dataclass
class CPCVSummary:
    """CPCV 路径汇总"""

    n_paths: int = 0
    sharpe_mean: float = 0.0
    sharpe_std: float = 0.0
    sharpe_cv: float = 0.0  # 变异系数 = std / |mean|
    sharpe_min: float = 0.0
    sharpe_max: float = 0.0
    pct_positive: float = 0.0
    is_stable: bool = False  # CV < 0.5 且 pct_positive > 0.8
    path_sharpes: list[float] = field(default_factory=list)


@dataclass
class HonestValidationResult:
    """诚实回测三件套综合结果"""

    # 三件套各自结果
    cpcv: CPCVSummary = field(default_factory=CPCVSummary)
    dsr: DSRResult | None = None
    noise: Any | None = None  # NoiseInjectionResult (避免硬依赖)

    # 综合判定
    is_honest: bool = False
    verdict: str = ""

    # 诊断
    n_observations: int = 0
    original_sharpe: float = 0.0


# ============================================================
# CPCV 路径 Sharpe 计算 (薄包装)
# ============================================================


def _compute_cpcv_sharpe_paths(
    daily_returns: np.ndarray,
    n_groups: int = 6,
    n_test_groups: int = 2,
    purge_pct: float = 0.01,
    embargo_pct: float = 0.01,
) -> CPCVSummary:
    """运行 CPCV 分割 + 每路径 Sharpe 计算"""
    try:
        from ms_strategy.src.backtest.combinatorial_purged_cv import (
            CombinatorialPurgedCV,
            CPCVConfig,
        )
    except ImportError:
        logger.warning("CPCV 模块不可用 (ms_strategy), 跳过 CPCV 验证")
        return CPCVSummary()

    n = len(daily_returns)
    if n < n_groups * 5:
        logger.warning("样本不足 %d < %d, 跳过 CPCV", n, n_groups * 5)
        return CPCVSummary()

    cfg = CPCVConfig(
        n_groups=n_groups,
        n_test_groups=n_test_groups,
        purge_pct=purge_pct,
        embargo_pct=embargo_pct,
    )
    cv = CombinatorialPurgedCV(cfg)
    splits = cv.split(n)

    path_sharpes: list[float] = []
    ann_factor = math.sqrt(TRADING_DAYS)

    for split in splits:
        test_returns = daily_returns[split.test_idx]
        if len(test_returns) < 5:
            continue
        mean_r = np.mean(test_returns)
        std_r = np.std(test_returns, ddof=1)
        if std_r > 1e-12:
            sr = float(mean_r / std_r * ann_factor)
            path_sharpes.append(sr)

    if not path_sharpes:
        return CPCVSummary()

    arr = np.array(path_sharpes)
    mean_s = float(arr.mean())
    std_s = float(arr.std(ddof=1))
    cv = float(std_s / abs(mean_s)) if abs(mean_s) > 1e-12 else float("inf")
    pct_pos = float(np.sum(arr > 0) / len(arr))

    is_stable = cv < 0.5 and pct_pos > 0.8

    return CPCVSummary(
        n_paths=len(path_sharpes),
        sharpe_mean=mean_s,
        sharpe_std=std_s,
        sharpe_cv=cv,
        sharpe_min=float(arr.min()),
        sharpe_max=float(arr.max()),
        pct_positive=pct_pos,
        is_stable=is_stable,
        path_sharpes=path_sharpes,
    )


# ============================================================
# Noise 注入 (薄包装)
# ============================================================


def _run_noise_test(
    daily_returns: np.ndarray,
    noise_ratio: float = 0.1,
    n_trials: int = 1000,
    random_seed: int = 42,
) -> Any:
    """运行噪音注入稳定性测试"""
    try:
        from ms_strategy.src.backtest.noise_injection_test import (
            run_noise_injection_test,
        )
    except ImportError:
        logger.warning("Noise injection 模块不可用 (ms_strategy), 跳过 Noise 验证")
        return None
    return run_noise_injection_test(
        daily_returns=daily_returns.tolist(),
        noise_ratio=noise_ratio,
        n_trials=n_trials,
        random_seed=random_seed,
    )


# ============================================================
# 主入口
# ============================================================


def run_honest_validation(
    daily_returns: Sequence[float] | np.ndarray,
    n_trials_dsr: int = 1,
    required_dsr: float = 0.95,
    risk_free_rate: float = 0.02,
    cpcv_n_groups: int = 6,
    cpcv_n_test_groups: int = 2,
    noise_ratio: float = 0.1,
    noise_n_trials: int = 1000,
    random_seed: int = 42,
) -> HonestValidationResult:
    """诚实回测三件套联合验证 (CPCV + DSR + Noise)

    Args:
        daily_returns: 日收益率序列
        n_trials_dsr: DSR 多重检验修正的策略数 (数据窥探修正)
        required_dsr: DSR 通过阈值 (默认 0.95)
        risk_free_rate: 年化无风险利率
        cpcv_n_groups: CPCV 分组数
        cpcv_n_test_groups: CPCV 测试组数
        noise_ratio: 噪音/信号 比
        noise_n_trials: 噪音注入实验次数
        random_seed: 随机种子

    Returns:
        HonestValidationResult — 三件套综合结果
    """
    returns = np.asarray(daily_returns, dtype=float)
    n = len(returns)

    result = HonestValidationResult(n_observations=n)

    if n < 20:
        result.verdict = f"样本不足 (n={n} < 20), 无法验证"
        return result

    # 原始 Sharpe
    mean_r = np.mean(returns)
    std_r = np.std(returns, ddof=1)
    if std_r > 1e-12:
        result.original_sharpe = float(mean_r / std_r * math.sqrt(TRADING_DAYS))

    # 1. CPCV
    result.cpcv = _compute_cpcv_sharpe_paths(
        returns,
        n_groups=cpcv_n_groups,
        n_test_groups=cpcv_n_test_groups,
    )

    # 2. DSR (n_trials 取 max(n_trials_dsr, CPCV 路径数) 修正多重检验)
    effective_n_trials = max(n_trials_dsr, result.cpcv.n_paths)
    result.dsr = deflated_sharpe_ratio(
        daily_returns=returns.tolist(),
        n_trials=effective_n_trials,
        required_dsr=required_dsr,
        risk_free_rate=risk_free_rate,
    )

    # 3. Noise
    result.noise = _run_noise_test(
        returns,
        noise_ratio=noise_ratio,
        n_trials=noise_n_trials,
        random_seed=random_seed,
    )

    # 综合判定
    dsr_pass = result.dsr.is_pass if result.dsr else False
    noise_stable = getattr(result.noise, "is_stable", False) if result.noise else False
    cpcv_stable = result.cpcv.is_stable

    result.is_honest = dsr_pass and noise_stable and cpcv_stable

    if result.is_honest:
        result.verdict = (
            f"HONEST: DSR={result.dsr.deflated_sharpe_ratio:.4f}≥{required_dsr}, "
            f"Noise stable, CPCV CV={result.cpcv.sharpe_cv:.3f}"
        )
    else:
        reasons = []
        if not dsr_pass:
            reasons.append(
                f"DSR FAIL ({result.dsr.deflated_sharpe_ratio:.4f}<{required_dsr})"
            )
        if not noise_stable:
            reasons.append("Noise unstable")
        if not cpcv_stable:
            reasons.append(f"CPCV unstable (CV={result.cpcv.sharpe_cv:.3f})")
        result.verdict = f"NOT HONEST: {', '.join(reasons)}"

    logger.info(
        "诚实回测三件套: Sharpe=%.3f, CPCV paths=%d (CV=%.3f), DSR=%.4f, Noise=%s → %s",
        result.original_sharpe,
        result.cpcv.n_paths,
        result.cpcv.sharpe_cv,
        result.dsr.deflated_sharpe_ratio if result.dsr else 0.0,
        "stable" if noise_stable else "unstable",
        "HONEST" if result.is_honest else "NOT HONEST",
    )

    return result
