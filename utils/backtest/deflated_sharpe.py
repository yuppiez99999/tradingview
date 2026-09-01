"""Deflated Sharpe Ratio (DSR) — Bailey & López de Prado (2014)

修复 T07: 原有算法类存在于 ms_strategy/src/backtest/metrics.py 的 DeflatedSharpeRatio,
但顶层模块 `deflated_sharpe` 缺失, 导致 strategy_evaluator / shadow_account_adapter
的 import 全部静默失败. 本模块补齐顶层模块并统一接口.

公式:
    DSR = Φ((SR_hat - E[SR_max]) * sqrt(T-1))

    E[SR_max] ≈ sqrt(2 * log(n_trials)) / sqrt(T) * correction(skew, kurt)

    若 DSR < required_dsr (默认 0.95), 则不能拒绝"策略 Sharpe 系随机取得"的原假设.

接口:
    from deflated_sharpe import deflated_sharpe_ratio

    result = deflated_sharpe_ratio(
        daily_returns=[...],
        n_trials=10,
        required_dsr=0.95,
        risk_free_rate=0.02,
    )
    # result.sharpe_ratio / .deflated_sharpe_ratio / .p_value / .is_pass / .verdict
    # float(result) → deflated_sharpe_ratio 值 (兼容 cast(float, ...))

参考:
    - Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"
    - López de Prado (2018) AFML Chapter 8/15
    - ms_strategy/src/backtest/metrics.py DeflatedSharpeRatio 类 (算法复用内核)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)

# 最小样本数 (252 交易日 ≈ 1 年)
MIN_SAMPLES = 20
TRADING_DAYS = 252


@dataclass
class DSRResult:
    """DSR 计算结果

    兼容两种调用方:
    - strategy_evaluator: 取 .sharpe_ratio / .deflated_sharpe_ratio / .p_value / .is_pass / .verdict
    - shadow_account_adapter: cast(float, result) → __float__ 返回 DSR 值
    """

    sharpe_ratio: float
    deflated_sharpe_ratio: float
    p_value: float
    is_pass: bool
    verdict: str
    expected_max_sr: float = 0.0
    n_trials: int = 1
    n_observations: int = 0
    skewness: float = 0.0
    kurtosis: float = 3.0

    def __float__(self) -> float:
        """float(result) → 返回 DSR 值 (兼容 cast(float, ...))"""
        return self.deflated_sharpe_ratio

    def as_dict(self) -> dict[str, object]:
        """转换为字典 (兼容 strategy_evaluator 的 dict 访问模式)"""
        return {
            "sharpe_ratio": self.sharpe_ratio,
            "deflated_sharpe_ratio": self.deflated_sharpe_ratio,
            "p_value": self.p_value,
            "is_pass": self.is_pass,
            "verdict": self.verdict,
            "expected_max_sr": self.expected_max_sr,
            "n_trials": self.n_trials,
            "n_observations": self.n_observations,
        }


def _compute_sharpe(
    daily_returns: np.ndarray,
    risk_free_rate: float = 0.02,
) -> float:
    """计算年化 Sharpe Ratio"""
    if len(daily_returns) < 2:
        return 0.0
    mean_ret = np.mean(daily_returns)
    std_ret = np.std(daily_returns, ddof=1)
    if std_ret < 1e-12:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS
    sr = (mean_ret - daily_rf) / std_ret
    return float(sr * np.sqrt(TRADING_DAYS))


def _expected_max_sr(
    n_trials: int,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """E[SR_max] — 在 n_trials 次随机尝试中期望的最大 SR (极值理论近似)"""
    if n_trials <= 1:
        return 0.0
    z_max = np.sqrt(2 * np.log(max(n_trials, 2)))
    # 偏度/峰度修正 (Bailey & López de Prado)
    correction = (
        1
        + (skewness / 6) * (z_max**2 - 1)
        + ((kurtosis - 3) / 24) * (z_max**3 - 3 * z_max)
    )
    return float(max(z_max * correction / np.sqrt(max(n_observations, 1)), 0.0))


def deflated_sharpe_ratio(
    daily_returns: Sequence[float] | np.ndarray,
    n_trials: int = 1,
    required_dsr: float = 0.95,
    risk_free_rate: float = 0.02,
) -> DSRResult:
    """计算 Deflated Sharpe Ratio

    Args:
        daily_returns: 日收益率序列
        n_trials: 尝试的策略数量 (数据窥探修正)
        required_dsr: DSR 通过阈值 (默认 0.95)
        risk_free_rate: 年化无风险利率 (默认 0.02)

    Returns:
        DSRResult (float(result) → DSR 值, 兼容 cast(float, ...))
    """
    returns = np.asarray(daily_returns, dtype=float)
    n = len(returns)

    if n < MIN_SAMPLES:
        return DSRResult(
            sharpe_ratio=0.0,
            deflated_sharpe_ratio=0.0,
            p_value=1.0,
            is_pass=False,
            verdict=f"样本不足 (n={n} < {MIN_SAMPLES})",
            n_trials=n_trials,
            n_observations=n,
        )

    # Sharpe Ratio (年化, 仅用于展示/verdict)
    sr = _compute_sharpe(returns, risk_free_rate)

    # 偏度和峰度
    skew = float(0.0 if n < 3 else _safe_skew(returns))
    kurt = float(3.0 if n < 4 else _safe_kurt(returns))

    # E[SR_max] (日频口径)
    e_max = _expected_max_sr(n_trials, n, skew, kurt)

    # 日频未年化 SR — PSR/DSR 公式口径, 必须与 sqrt(T-1) 配套
    # (修复: 原实现误用年化 SR 参与 z_score, z 被放大 sqrt(252) 倍, DSR 系统性高估 → 恒 PASS)
    daily_rf = risk_free_rate / TRADING_DAYS
    std_daily = float(np.std(returns, ddof=1))
    sr_daily = (float(np.mean(returns)) - daily_rf) / std_daily if std_daily >= 1e-12 else 0.0

    # PSR 分母: 偏度/峰度对 SR 方差的修正 (Bailey & López de Prado 2014)
    denom = np.sqrt(max(1.0 - skew * sr_daily + (kurt - 1.0) / 4.0 * sr_daily**2, 1e-12))

    # DSR = Φ((SR_daily - E[SR_max]) * sqrt(T-1) / denom)
    z_score = (sr_daily - e_max) * np.sqrt(max(n - 1, 1)) / denom
    dsr = float(norm.cdf(z_score))

    # p_value = 1 - DSR (单尾检验)
    p_value = 1.0 - dsr

    is_pass = dsr >= required_dsr
    if is_pass:
        verdict = f"PASS: DSR={dsr:.4f} ≥ {required_dsr} (Sharpe={sr:.3f}, n_trials={n_trials})"
    else:
        verdict = f"FAIL: DSR={dsr:.4f} < {required_dsr} (Sharpe={sr:.3f}, n_trials={n_trials})"

    logger.info(
        "DSR: Sharpe=%.3f, E[SR_max]=%.4f, DSR=%.4f, p=%.4f, n_trials=%d, n_obs=%d → %s",
        sr,
        e_max,
        dsr,
        p_value,
        n_trials,
        n,
        "PASS" if is_pass else "FAIL",
    )

    return DSRResult(
        sharpe_ratio=sr,
        deflated_sharpe_ratio=dsr,
        p_value=p_value,
        is_pass=is_pass,
        verdict=verdict,
        expected_max_sr=e_max,
        n_trials=n_trials,
        n_observations=n,
        skewness=skew,
        kurtosis=kurt,
    )


def _safe_skew(x: np.ndarray) -> float:
    """安全偏度 (防溢出)"""
    len(x)
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(((x - mean) / std) ** 3))


def _safe_kurt(x: np.ndarray) -> float:
    """安全峰度 (正态=3)"""
    len(x)
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std < 1e-12:
        return 3.0
    return float(np.mean(((x - mean) / std) ** 4))
