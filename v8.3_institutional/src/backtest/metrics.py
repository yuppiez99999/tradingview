"""
v7.5 PerformanceMetrics — Sortino / Calmar / DSR 等绩效指标
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §4.1, §4.5
"""

import numpy as np
import pandas as pd
from typing import Optional
from scipy.stats import norm


class PerformanceMetrics:
    """
    组合绩效指标计算

    目标函数: J = Sortino + 0.5 * Calmar - λ * ||w||²
    """

    ANNUAL_FACTOR = 252

    def __init__(self, returns: pd.Series, rf: float = 0.02, l2_lambda: float = 0.1):
        """
        Args:
            returns: 日收益序列
            rf: 无风险利率
            l2_lambda: L2 正则化系数
        """
        self.returns = returns.dropna()
        self.rf = rf
        self.rf_daily = rf / self.ANNUAL_FACTOR
        self.l2_lambda = l2_lambda

    # ---------- 基础指标 ----------
    def annual_return(self) -> float:
        if len(self.returns) < 2:
            return 0.0
        return float(self.returns.mean() * self.ANNUAL_FACTOR)

    def annual_vol(self) -> float:
        if len(self.returns) < 2:
            return 0.0
        return float(self.returns.std() * np.sqrt(self.ANNUAL_FACTOR))

    def max_drawdown(self) -> float:
        if len(self.returns) == 0:
            return 0.0
        cumulative = (1 + self.returns).cumprod()
        if cumulative.iloc[-1] <= 0 or not np.isfinite(cumulative).all():
            return 0.0
        rolling_max = cumulative.expanding().max()
        denom = rolling_max.replace(0, np.nan)
        dd = (cumulative - rolling_max) / denom
        dd = dd.replace([np.inf, -np.inf], np.nan).fillna(0)
        min_dd = float(dd.min())
        return 0.0 if not np.isfinite(min_dd) else min_dd

    def sharpe_ratio(self) -> float:
        excess = self.annual_return() - self.rf
        vol = self.annual_vol()
        return excess / vol if vol > 0 else 0.0

    # ---------- 目标函数指标 ----------
    def sortino_ratio(self, target_return: float = 0.0) -> float:
        """Sortino Ratio = (E[Rp - rf]) / σ_down"""
        excess = self.returns - self.rf_daily
        downside = excess[excess < target_return]
        if len(downside) == 0:
            return float("inf") if self.annual_return() > self.rf else 0.0
        downside_std = downside.std() * np.sqrt(self.ANNUAL_FACTOR)
        ann_excess = self.annual_return() - self.rf
        return ann_excess / downside_std if downside_std > 0 else 0.0

    def calmar_ratio(self) -> float:
        """Calmar Ratio = Annual Return / Max DD"""
        ann_ret = self.annual_return()
        dd = abs(self.max_drawdown())
        return ann_ret / dd if dd > 0 else 0.0

    def objective(self, weights: Optional[np.ndarray] = None) -> float:
        """
        目标函数: J = Sortino + 0.5 * Calmar - λ * ||w||²
        """
        sortino = self.sortino_ratio()
        calmar = self.calmar_ratio()
        penalty = 0.0
        if weights is not None:
            penalty = self.l2_lambda * np.sum(weights**2)
        return sortino + 0.5 * calmar - penalty

    # ---------- 进阶指标 ----------
    def value_at_risk(self, confidence: float = 0.95) -> float:
        """VaR"""
        return float(self.returns.quantile(1 - confidence))

    def conditional_var(self, confidence: float = 0.95) -> float:
        """CVaR / Expected Shortfall"""
        var = self.value_at_risk(confidence)
        tail = self.returns[self.returns <= var]
        return float(tail.mean()) if len(tail) > 0 else var

    def win_rate(self) -> float:
        return float((self.returns > 0).mean())

    def profit_factor(self) -> float:
        gains = self.returns[self.returns > 0].sum()
        losses = abs(self.returns[self.returns < 0].sum())
        return gains / losses if losses > 0 else float("inf")

    def omega_ratio(self, threshold: float = 0.0) -> float:
        gains = self.returns[self.returns > threshold].sum()
        losses = abs(self.returns[self.returns < threshold].sum())
        return gains / losses if losses > 0 else float("inf")

    # ---------- 汇总 ----------
    def summary(self) -> dict:
        return {
            "annual_return": self.annual_return(),
            "annual_vol": self.annual_vol(),
            "max_drawdown": self.max_drawdown(),
            "sharpe": self.sharpe_ratio(),
            "sortino": self.sortino_ratio(),
            "calmar": self.calmar_ratio(),
            "objective": self.objective(),
            "var_95": self.value_at_risk(0.95),
            "cvar_95": self.conditional_var(0.95),
            "win_rate": self.win_rate(),
            "profit_factor": self.profit_factor(),
            "omega": self.omega_ratio(),
            "n_days": len(self.returns),
        }

    def summary_str(self) -> str:
        s = self.summary()
        return (
            f"Annual Return: {s['annual_return']:.2%}\n"
            f"Annual Vol:    {s['annual_vol']:.2%}\n"
            f"Max DD:        {s['max_drawdown']:.2%}\n"
            f"Sharpe:        {s['sharpe']:.3f}\n"
            f"Sortino:       {s['sortino']:.3f}\n"
            f"Calmar:        {s['calmar']:.3f}\n"
            f"Objective:     {s['objective']:.3f}\n"
            f"VaR 95%:       {s['var_95']:.4f}\n"
            f"CVaR 95%:      {s['cvar_95']:.4f}\n"
            f"Win Rate:      {s['win_rate']:.2%}\n"
            f"Profit Factor: {s['profit_factor']:.3f}\n"
            f"N Days:        {s['n_days']}"
        )


class DeflatedSharpeRatio:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado)

    DSR = Φ((SR_hat - E[SR_max]) * sqrt(T-1))

    若 DSR < 0.95，则不能拒绝"策略 Sharpe 系随机取得"的原假设。
    """

    def __init__(
        self, sharpe_ratio: float, n_trials: int, n_observations: int, skewness: float = 0.0, kurtosis: float = 3.0
    ):
        """
        Args:
            sharpe_ratio: 策略的经验 Sharpe Ratio
            n_trials: 尝试的策略数量（若含数据窥探）
            n_observations: 样本量
            skewness: 收益偏度
            kurtosis: 收益峰度（正态=3）
        """
        self.sr = sharpe_ratio
        self.n_trials = n_trials
        self.T = n_observations
        self.skew = skewness
        self.kurt = kurtosis

    def expected_max_sr(self) -> float:
        """
        E[SR_max] — 在 n_trials 次随机尝试中期望的最大 SR
        使用极值理论近似
        """
        # 底层标准正态的 order statistic 期望
        if self.n_trials <= 1:
            return 0.0
        # 近似：E[Z_{(n)}] ≈ sqrt(2 * log(n))  for large n
        Z_max = np.sqrt(2 * np.log(max(self.n_trials, 2)))
        # 考虑偏度/峰度修正
        correction = 1 + (self.skew / 6) * (Z_max**2 - 1) + ((self.kurt - 3) / 24) * (Z_max**3 - 3 * Z_max)
        return Z_max * correction / np.sqrt(max(self.T, 1))

    def compute(self) -> float:
        """计算 DSR"""
        e_max = self.expected_max_sr()
        z_score = (self.sr - e_max) * np.sqrt(max(self.T - 1, 1))
        dsr = norm.cdf(z_score)
        return float(dsr)

    def is_significant(self, threshold: float = 0.95) -> bool:
        return self.compute() >= threshold

    def summary(self) -> dict:
        dsr = self.compute()
        return {
            "sharpe_ratio": self.sr,
            "expected_max_sr": self.expected_max_sr(),
            "dsr": dsr,
            "significant": self.is_significant(),
            "n_trials": self.n_trials,
            "n_observations": self.T,
        }


# ============================================================
# 模块级便捷函数 (兼容测试 API)
# ============================================================


def compute_sharpe(returns: pd.Series, rf: float = 0.02) -> float:
    """计算 Sharpe Ratio"""
    return PerformanceMetrics(returns, rf=rf).sharpe_ratio()


def compute_sortino(returns: pd.Series, rf: float = 0.02, target: float = 0.0) -> float:
    """计算 Sortino Ratio"""
    return PerformanceMetrics(returns, rf=rf).sortino_ratio(target)


def compute_calmar(returns: pd.Series, rf: float = 0.02) -> float:
    """计算 Calmar Ratio"""
    return PerformanceMetrics(returns, rf=rf).calmar_ratio()


def compute_max_drawdown(equity: pd.Series) -> tuple:
    """
    计算最大回撤及其位置

    Args:
        equity: 净值序列 (非累积收益)

    Returns:
        (max_dd, peak_idx, trough_idx)
        max_dd: 最大回撤 (正值表示回撤幅度, 0 表示无回撤)
        peak_idx: 峰值索引
        trough_idx: 谷值索引
    """
    if equity is None or len(equity) == 0:
        return 0.0, 0, 0

    s = pd.Series(equity).astype(float)
    if len(s) < 2:
        return 0.0, 0, 0

    running_max = s.expanding().max()
    dd = (s - running_max) / running_max.replace(0, np.nan)
    dd = dd.fillna(0.0)

    trough_idx = int(dd.idxmin())
    max_dd = float(dd.iloc[trough_idx])
    # 峰值在谷值之前
    peak_idx = int(s.iloc[: trough_idx + 1].idxmax()) if trough_idx > 0 else 0

    # 返回绝对值 (回撤幅度)
    return abs(max_dd), peak_idx, trough_idx


def compute_dsr(observed_sr: float, n_trials: int, t_obs: int, skewness: float = 0.0, kurtosis: float = 3.0) -> float:
    """
    计算 Deflated Sharpe Ratio

    Args:
        observed_sr: 观察到的 Sharpe Ratio
        n_trials: 尝试的策略数
        t_obs: 样本量 (观测数)
        skewness: 偏度
        kurtosis: 峰度

    Returns:
        DSR 值 ∈ [0, 1]
    """
    dsr_obj = DeflatedSharpeRatio(
        sharpe_ratio=observed_sr,
        n_trials=n_trials,
        n_observations=t_obs,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    return dsr_obj.compute()


def compute_all_metrics(returns: pd.Series, rf: float = 0.02) -> dict:
    """
    一次性计算全部绩效指标

    Returns:
        {annual_return, annual_vol, sharpe, sortino, calmar,
         max_drawdown, var_95, cvar_95, win_rate, ...}
    """
    pm = PerformanceMetrics(returns, rf=rf)
    base = pm.summary()
    # 确保 max_drawdown 是正数 (回撤幅度)
    base["max_drawdown"] = abs(base.get("max_drawdown", 0.0))
    return base
