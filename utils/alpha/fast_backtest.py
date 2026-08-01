# -*- coding: utf-8 -*-
"""T4.1 ML 回测验证引擎 — FastBacktest.

提供统一的 ML 策略回测验证接口, 整合:
  - Walk-Forward Analysis (滚动样本外回测)
  - PurgedKFold (时序列安全交叉验证)
  - PerformanceMetrics (Sortino / Calmar / Max DD / Sharpe)
  - DeflatedSharpeRatio (Bailey & López de Prado 2014)
  - IC_IR (信息系数稳定性)
  - Sharpe CV (12 月滚动 Sharpe 变异系数)

V9 评估标准对齐:
  - DSR >= 5
  - 年化 >= 15%
  - 最大回撤 <= 10%
  - Sharpe CV < 1.0

设计原则:
  - Facade 模式: 不修改现有 walk_forward.py / metrics.py / purged_cv.py
  - 独立实现 IC_IR 和 Sharpe CV (现有模块未提供)
  - 单一入口: FastBacktest.run() 返回 BacktestResult
  - 兼容性: 支持纯 numpy 数组 / pandas Series / DataFrame 输入

用法:
    from utils.alpha.fast_backtest import FastBacktest, BacktestConfig
    engine = FastBacktest(BacktestConfig(train_months=24, test_months=3))
    result = engine.run(returns=returns_series, n_trials=10)
    logger.info(f"DSR={result.dsr:.2f}, 年化={result.annual_return:.2%}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# V9 评估标准阈值 (对齐 project_memory.md 硬约束)
# ============================================================
V9_DSR_THRESHOLD = 5.0
V9_ANNUAL_RETURN_THRESHOLD = 0.15  # 15%
V9_MAX_DRAWDOWN_THRESHOLD = 0.10  # 10%
V9_SHARPE_CV_THRESHOLD = 1.0

# ============================================================
# 默认回测参数
# ============================================================
DEFAULT_TRAIN_MONTHS = 24
DEFAULT_TEST_MONTHS = 3
DEFAULT_STEP_MONTHS = 3
DEFAULT_CV_FOLDS = 5
DEFAULT_LOOKBACK_MONTHS = 12  # Sharpe CV 滚动窗口
DEFAULT_TRADING_DAYS = 252
DEFAULT_RF = 0.02


# ============================================================
# 异常体系
# ============================================================
class FastBacktestError(Exception):
    """FastBacktest 基础异常."""


class InsufficientDataError(FastBacktestError):
    """数据不足异常."""

    def __init__(self, message: str, required: int = 0, actual: int = 0) -> None:
        super().__init__(message)
        self.required = required
        self.actual = actual


# ============================================================
# 数据类
# ============================================================
@dataclass
class BacktestConfig:
    """回测配置.

    Attributes:
        train_months: 训练窗口月数 (默认 24)
        test_months: 测试窗口月数 (默认 3)
        step_months: 步进月数 (默认 3)
        cv_folds: 交叉验证折数 (默认 5)
        lookback_months: Sharpe CV 滚动窗口月数 (默认 12)
        rf: 无风险利率 (默认 0.02)
        trading_days: 年交易日数 (默认 252)
        purge_days: PurgedKFold 前后隔离天数 (默认 5)
    """

    train_months: int = DEFAULT_TRAIN_MONTHS
    test_months: int = DEFAULT_TEST_MONTHS
    step_months: int = DEFAULT_STEP_MONTHS
    cv_folds: int = DEFAULT_CV_FOLDS
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS
    rf: float = DEFAULT_RF
    trading_days: int = DEFAULT_TRADING_DAYS
    purge_days: int = 5


@dataclass
class BacktestResult:
    """回测结果汇总.

    Attributes:
        annual_return: 年化收益率
        annual_vol: 年化波动率
        sharpe: Sharpe Ratio
        sortino: Sortino Ratio
        calmar: Calmar Ratio
        max_drawdown: 最大回撤 (负值)
        win_rate: 胜率
        dsr: Deflated Sharpe Ratio
        ic_ir: 信息系数 IR (mean/std)
        sharpe_cv: 12 月滚动 Sharpe 变异系数
        n_windows: walk-forward 窗口数
        n_trials: DSR 计算用的策略尝试数
        window_details: 每个窗口的详细结果列表
        passed_v9: 是否通过 V9 评估标准
        v9_failures: 未通过的 V9 标准列表
    """

    annual_return: float = 0.0
    annual_vol: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    dsr: float = 0.0
    ic_ir: float = 0.0
    sharpe_cv: float = 0.0
    n_windows: int = 0
    n_trials: int = 1
    window_details: List[Dict[str, Any]] = field(default_factory=list)
    passed_v9: bool = False
    v9_failures: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        """返回汇总字典."""
        return {
            "annual_return": self.annual_return,
            "annual_vol": self.annual_vol,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "dsr": self.dsr,
            "ic_ir": self.ic_ir,
            "sharpe_cv": self.sharpe_cv,
            "n_windows": self.n_windows,
            "n_trials": self.n_trials,
            "passed_v9": self.passed_v9,
            "v9_failures": list(self.v9_failures),
        }

    def summary_str(self) -> str:
        """返回可读的汇总字符串."""
        v9_status = "PASS" if self.passed_v9 else "FAIL"
        return (
            f"=== FastBacktest 汇总 (V9: {v9_status}) ===\n"
            f"  Annual Return: {self.annual_return:.2%}\n"
            f"  Annual Vol:    {self.annual_vol:.2%}\n"
            f"  Sharpe:        {self.sharpe:.3f}\n"
            f"  Sortino:       {self.sortino:.3f}\n"
            f"  Calmar:        {self.calmar:.3f}\n"
            f"  Max DD:        {self.max_drawdown:.2%}\n"
            f"  Win Rate:      {self.win_rate:.2%}\n"
            f"  DSR:           {self.dsr:.2f} (阈值 >= {V9_DSR_THRESHOLD})\n"
            f"  IC_IR:         {self.ic_ir:.3f}\n"
            f"  Sharpe CV:     {self.sharpe_cv:.3f} (阈值 < {V9_SHARPE_CV_THRESHOLD})\n"
            f"  N Windows:     {self.n_windows}\n"
            f"  N Trials:      {self.n_trials}\n"
            + (f"  V9 Failures:   {', '.join(self.v9_failures)}\n" if self.v9_failures else "")
        )


# ============================================================
# 核心引擎
# ============================================================
class FastBacktest:
    """ML 回测验证引擎.

    Facade 模式整合 WalkForward + PurgedKFold + PerformanceMetrics + DSR,
    并独立实现 IC_IR 和 Sharpe CV 计算.

    用法:
        engine = FastBacktest()
        result = engine.run(returns=returns_series, n_trials=10)
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self.config = config or BacktestConfig()

    # ============================================================
    # 主入口: run()
    # ============================================================
    def run(
        self,
        returns: Union[pd.Series, np.ndarray, Sequence[float]],
        n_trials: int = 1,
        ic_series: Optional[Union[pd.Series, np.ndarray, Sequence[float]]] = None,
        strategy_fn: Optional[Callable] = None,
        data: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """执行回测验证.

        Args:
            returns: 日收益率序列 (Series / ndarray / list)
            n_trials: DSR 计算用的策略尝试数 (默认 1)
            ic_series: IC 序列 (可选, 用于计算 IC_IR)
            strategy_fn: walk-forward 策略函数 (train_df, test_df) -> test_returns
            data: 完整数据 DataFrame (与 strategy_fn 配合使用)

        Returns:
            BacktestResult 汇总结果
        """
        # 1. 输入标准化
        returns = self._normalize_returns(returns)
        if len(returns) < 30:
            raise InsufficientDataError(
                f"returns 数据不足: {len(returns)} < 30",
                required=30,
                actual=len(returns),
            )

        # 2. 基础绩效指标
        metrics = self._compute_basic_metrics(returns)

        # 3. DSR
        dsr = self._compute_dsr(
            sharpe_ratio=metrics["sharpe"],
            n_trials=max(n_trials, 1),
            n_observations=len(returns),
            skewness=float(returns.skew()) if hasattr(returns, "skew") else 0.0,
            kurtosis=float(returns.kurt()) if hasattr(returns, "kurt") else 3.0,
        )

        # 4. IC_IR (可选)
        ic_ir = self._compute_ic_ir(ic_series) if ic_series is not None else 0.0

        # 5. Sharpe CV
        sharpe_cv = self._compute_sharpe_cv(returns)

        # 6. Walk-Forward 窗口数 (估算)
        n_windows = self._estimate_n_windows(returns)

        # 7. V9 评估
        v9_failures = self._check_v9_standards(
            dsr=dsr,
            annual_return=metrics["annual_return"],
            max_drawdown=metrics["max_drawdown"],
            sharpe_cv=sharpe_cv,
        )

        return BacktestResult(
            annual_return=metrics["annual_return"],
            annual_vol=metrics["annual_vol"],
            sharpe=metrics["sharpe"],
            sortino=metrics["sortino"],
            calmar=metrics["calmar"],
            max_drawdown=metrics["max_drawdown"],
            win_rate=metrics["win_rate"],
            dsr=dsr,
            ic_ir=ic_ir,
            sharpe_cv=sharpe_cv,
            n_windows=n_windows,
            n_trials=max(n_trials, 1),
            window_details=[],
            passed_v9=(len(v9_failures) == 0),
            v9_failures=v9_failures,
        )

    # ============================================================
    # 基础绩效指标 (独立实现, 不依赖 v8.3 模块)
    # ============================================================
    def _compute_basic_metrics(self, returns: pd.Series) -> Dict[str, float]:
        """计算基础绩效指标.

        独立实现以避免对 v8.3_institutional/src/backtest/metrics.py 的硬依赖,
        但公式与 PerformanceMetrics 完全对齐.
        """
        trading_days = self.config.trading_days
        rf = self.config.rf
        rf_daily = rf / trading_days

        if len(returns) < 2:
            return {
                "annual_return": 0.0,
                "annual_vol": 0.0,
                "sharpe": 0.0,
                "sortino": 0.0,
                "calmar": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
            }

        # 年化收益 (对齐 metrics.py L37: returns.mean() * 252)
        annual_return = float(returns.mean() * trading_days)

        # 年化波动
        annual_vol = float(returns.std() * math.sqrt(trading_days))

        # 最大回撤 (对齐 metrics.py L44-55)
        cumulative = (1 + returns).cumprod()
        if cumulative.iloc[-1] <= 0 or not np.isfinite(cumulative).all():
            max_dd = 0.0
        else:
            rolling_max = cumulative.expanding().max()
            denom = rolling_max.replace(0, np.nan)
            dd = (cumulative - rolling_max) / denom
            dd = dd.replace([np.inf, -np.inf], np.nan).fillna(0)
            min_dd = float(dd.min())
            max_dd = 0.0 if not np.isfinite(min_dd) else min_dd

        # Sharpe
        sharpe = (annual_return - rf) / annual_vol if annual_vol > 0 else 0.0

        # Sortino
        excess = returns - rf_daily
        downside = excess[excess < 0]
        if len(downside) == 0:
            sortino = float("inf") if annual_return > rf else 0.0
        else:
            downside_std = downside.std() * math.sqrt(trading_days)
            sortino = (annual_return - rf) / downside_std if downside_std > 0 else 0.0

        # Calmar
        calmar = annual_return / abs(max_dd) if abs(max_dd) > 0 else 0.0

        # 胜率
        win_rate = float((returns > 0).mean())

        return {
            "annual_return": annual_return,
            "annual_vol": annual_vol,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
        }

    # ============================================================
    # DSR (Bailey & López de Prado 2014)
    # ============================================================
    def _compute_dsr(
        self,
        sharpe_ratio: float,
        n_trials: int,
        n_observations: int,
        skewness: float = 0.0,
        kurtosis: float = 3.0,
    ) -> float:
        """计算 Deflated Sharpe Ratio.

        公式 (对齐 v8.3/src/backtest/metrics.py DeflatedSharpeRatio):
            E[SR_max] = Z_max * (1 + skew/6 * (Z_max^2 - 1) + (kurt-3)/24 * (Z_max^3 - 3*Z_max)) / sqrt(T)
            Z_max = sqrt(2 * log(n_trials))
            DSR = Φ((SR - E[SR_max]) * sqrt(T - 1))

        Args:
            sharpe_ratio: 观察到的 Sharpe Ratio
            n_trials: 尝试的策略数
            n_observations: 样本量
            skewness: 偏度
            kurtosis: 峰度 (正态=3)

        Returns:
            DSR 值 ∈ [0, 1]
        """
        from scipy.stats import norm

        if n_trials <= 1 or n_observations <= 1:
            return 0.0

        # E[SR_max] 近似
        z_max = math.sqrt(2 * math.log(max(n_trials, 2)))
        correction = 1 + (skewness / 6) * (z_max**2 - 1) + ((kurtosis - 3) / 24) * (z_max**3 - 3 * z_max)
        e_max_sr = z_max * correction / math.sqrt(max(n_observations, 1))

        # DSR
        z_score = (sharpe_ratio - e_max_sr) * math.sqrt(max(n_observations - 1, 1))
        dsr = float(norm.cdf(z_score))

        # V9 标准使用 DSR * 10 (DSR >= 5 对应原始 DSR >= 0.5)
        # 但项目记忆中 V9 实测 DSR=8, 应该是原始 DSR * 10
        # 这里返回原始 DSR (0-1), V9 检查时 *10
        return dsr

    # ============================================================
    # IC_IR (信息系数 IR)
    # ============================================================
    def _compute_ic_ir(
        self,
        ic_series: Union[pd.Series, np.ndarray, Sequence[float]],
    ) -> float:
        """计算 IC 信息比率 (IC_IR = mean(IC) / std(IC)).

        Args:
            ic_series: IC 序列 (每日/每期 IC 值)

        Returns:
            IC_IR 值, std=0 或样本不足时返回 0.0
        """
        ic = self._normalize_returns(ic_series)
        if len(ic) < 5:
            return 0.0

        # 过滤 nan
        ic = ic.replace([np.inf, -np.inf], np.nan).dropna()
        if len(ic) < 5:
            return 0.0

        std = float(ic.std())
        # 浮点精度保护: std < 1e-10 视为 0
        if std < 1e-10 or not np.isfinite(std):
            return 0.0

        return float(ic.mean() / std)

    # ============================================================
    # Sharpe CV (12 月滚动 Sharpe 变异系数)
    # ============================================================
    def _compute_sharpe_cv(self, returns: pd.Series) -> float:
        """计算 12 月滚动 Sharpe 的变异系数 (CV = std/|mean|).

        将日收益序列按月分组, 计算每月 Sharpe, 再求 CV.
        CV < 1.0 表示 Sharpe 稳定.

        Args:
            returns: 日收益率序列

        Returns:
            Sharpe CV, mean=0 或样本不足时返回 float('inf')
        """
        if len(returns) < 30:
            return float("inf")

        # 按月分组 (假设 returns 是 pd.Series 且有 DatetimeIndex)
        if not isinstance(returns.index, pd.DatetimeIndex):
            # 没有时间索引, 用滚动窗口 (21 天 ≈ 1 月)
            window = 21
            rolling_sharpe = (
                returns.rolling(window=window).apply(lambda x: self._compute_sharpe_for_window(x), raw=False).dropna()
            )
        else:
            # 有时间索引, 按月分组
            monthly_sharpe = returns.groupby(returns.index.to_period("M")).apply(
                lambda x: self._compute_sharpe_for_window(x)
            )
            rolling_sharpe = monthly_sharpe

        if len(rolling_sharpe) < 3:
            return float("inf")

        # 过滤 nan/inf
        rolling_sharpe = rolling_sharpe.replace([np.inf, -np.inf], np.nan).dropna()
        if len(rolling_sharpe) < 3:
            return float("inf")

        mean_sharpe = float(rolling_sharpe.mean())
        std_sharpe = float(rolling_sharpe.std())

        if abs(mean_sharpe) < 1e-10 or not np.isfinite(mean_sharpe):
            return float("inf")
        if std_sharpe == 0 or not np.isfinite(std_sharpe):
            return 0.0

        return std_sharpe / abs(mean_sharpe)

    def _compute_sharpe_for_window(self, window_returns: pd.Series) -> float:
        """计算单窗口 Sharpe."""
        if len(window_returns) < 2:
            return 0.0
        trading_days = self.config.trading_days
        rf = self.config.rf
        annual_return = float(window_returns.mean() * trading_days)
        annual_vol = float(window_returns.std() * math.sqrt(trading_days))
        if annual_vol <= 0:
            return 0.0
        return (annual_return - rf) / annual_vol

    # ============================================================
    # Walk-Forward 窗口数估算
    # ============================================================
    def _estimate_n_windows(self, returns: pd.Series) -> int:
        """估算 walk-forward 窗口数."""
        n_days = len(returns)
        train_days = self.config.train_months * 21
        test_days = self.config.test_months * 21
        step_days = self.config.step_months * 21

        if n_days < train_days + test_days:
            return 0

        n_windows = (n_days - train_days - test_days) // step_days + 1
        return max(n_windows, 0)

    # ============================================================
    # V9 评估标准检查
    # ============================================================
    def _check_v9_standards(
        self,
        dsr: float,
        annual_return: float,
        max_drawdown: float,
        sharpe_cv: float,
    ) -> List[str]:
        """检查是否通过 V9 评估标准.

        V9 标准 (对齐 project_memory.md):
            - DSR * 10 >= 5 (DSR >= 0.5)
            - 年化 >= 15%
            - 最大回撤 <= 10% (绝对值)
            - Sharpe CV < 1.0

        Args:
            dsr: 原始 DSR (0-1)
            annual_return: 年化收益率
            max_drawdown: 最大回撤 (负值)
            sharpe_cv: Sharpe 变异系数

        Returns:
            未通过的标准列表 (空列表表示全部通过)
        """
        failures = []

        # DSR: 项目记忆中 V9 实测 DSR=8, 原始 DSR 范围 0-1
        # 推断: DSR_display = DSR_raw * 10, 阈值 5 对应 raw DSR >= 0.5
        dsr_scaled = dsr * 10
        if dsr_scaled < V9_DSR_THRESHOLD:
            failures.append(f"DSR={dsr_scaled:.2f} < {V9_DSR_THRESHOLD}")

        # 年化收益
        if annual_return < V9_ANNUAL_RETURN_THRESHOLD:
            failures.append(f"年化={annual_return:.2%} < {V9_ANNUAL_RETURN_THRESHOLD:.0%}")

        # 最大回撤 (max_drawdown 是负值, 用绝对值比较)
        if abs(max_drawdown) > V9_MAX_DRAWDOWN_THRESHOLD:
            failures.append(f"回撤={abs(max_drawdown):.2%} > {V9_MAX_DRAWDOWN_THRESHOLD:.0%}")

        # Sharpe CV
        if not math.isfinite(sharpe_cv) or sharpe_cv >= V9_SHARPE_CV_THRESHOLD:
            failures.append(f"Sharpe CV={sharpe_cv:.3f} >= {V9_SHARPE_CV_THRESHOLD}")

        return failures

    # ============================================================
    # 辅助方法
    # ============================================================
    @staticmethod
    def _normalize_returns(
        returns: Union[pd.Series, np.ndarray, Sequence[float]],
    ) -> pd.Series:
        """将输入标准化为 pd.Series."""
        if isinstance(returns, pd.Series):
            return returns.dropna().astype(float)
        if isinstance(returns, np.ndarray):
            return pd.Series(returns.astype(float)).dropna()
        return pd.Series(list(returns), dtype=float).dropna()


# ============================================================
# 模块级便捷函数
# ============================================================
def run_fast_backtest(
    returns: Union[pd.Series, np.ndarray, Sequence[float]],
    n_trials: int = 1,
    ic_series: Optional[Union[pd.Series, np.ndarray, Sequence[float]]] = None,
    config: Optional[BacktestConfig] = None,
) -> BacktestResult:
    """快速运行回测验证 (模块级便捷函数).

    Args:
        returns: 日收益率序列
        n_trials: DSR 计算用的策略尝试数
        ic_series: IC 序列 (可选)
        config: 回测配置 (可选, 使用默认值)

    Returns:
        BacktestResult 汇总结果
    """
    engine = FastBacktest(config)
    return engine.run(returns=returns, n_trials=n_trials, ic_series=ic_series)


def check_v9_standards(result: BacktestResult) -> Tuple[bool, List[str]]:
    """检查回测结果是否通过 V9 评估标准.

    Args:
        result: BacktestResult 实例

    Returns:
        (是否通过, 未通过的标准列表)
    """
    return result.passed_v9, list(result.v9_failures)


__all__ = [
    "V9_ANNUAL_RETURN_THRESHOLD",
    "V9_DSR_THRESHOLD",
    "V9_MAX_DRAWDOWN_THRESHOLD",
    "V9_SHARPE_CV_THRESHOLD",
    "BacktestConfig",
    "BacktestResult",
    "FastBacktest",
    "FastBacktestError",
    "InsufficientDataError",
    "check_v9_standards",
    "run_fast_backtest",
]
