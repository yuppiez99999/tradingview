"""T4.1 ML 回测验证引擎 — FastBacktest.

对单段收益率序列计算静态绩效指标与统计显著性:
  - PerformanceMetrics 类指标 (Sharpe / Sortino / Calmar / Max DD / 年化)
  - DeflatedSharpeRatio (Bailey & López de Prado 2014, 须显式声明试验次数)
  - IC_IR (信息系数稳定性)
  - Sharpe CV (12 月滚动 Sharpe 变异系数)

诚实边界 (2026-09-04 审计 P0-5 立; 2026-09-10 审计 item 14 更新):
  **run() 仍只对同一全样本计算静态指标**, 不做任何滚动/样本外切分
  (DSR/IC_IR/Sharpe CV 亦基于该全样本)。这条边界不因下述新增能力而改变。
  早期文档曾声称"整合 Walk-Forward Analysis 与 PurgedKFold"却无实现, 属包装
  误导, 该虚假声明已删除; 现以**显式入口**提供真实能力:
    - ``run_walk_forward()``: 真实滚动样本外评估 (train/test 之间强制 purge 间隔
      + 逐窗口明细 + OOS 拼接 Sharpe 与稳定性判定), 内核见 utils.alpha.walk_forward;
    - ``run_cpcv()``: 组合清洗交叉验证 (CPCV) 多路径 Sharpe 分布, 委托
      ms_strategy.src.backtest.combinatorial_purged_cv 真实内核;
    - ``run()`` 的 ``strategy_fn`` / ``data`` 仍 fail-fast (NotImplementedError) ——
      这是**刻意保留**的防误用: 要滚动验证必须显式调用 run_walk_forward(),
      避免调用方以为"把这两个参数透传给 run() 就获得了样本外结果";
    - ``BacktestResult.n_windows`` / ``window_details`` 在 run() 中仍只是
      "潜在段数估算 / 预留字段", **不代表已执行滚动回测**; 真实窗口明细见
      ``WalkForwardResult.windows``。
  另可选用 utils.backtest.honest_validation (CPCV + DSR + Noise) 或
  utils.wt_backtest_engine (事件驱动, 含次日成交/T+1/费用/涨跌停约束)。

V9 内部评估门槛 (对齐 project_memory, 属项目宽松筛选阈值而非学术显著性):
  - DSR Score >= 5.0  (DSR Score = raw DSR × 10, 即 raw DSR >= 0.5)
  - 年化 >= 15%
  - 最大回撤 <= 10%
  - Sharpe CV < 1.0
  注: 统计显著性结论 (可否决"结果系随机取得"原假设) 要求 raw DSR >= 0.95,
  见 utils.backtest.deflated_sharpe 与 utils.alpha.strategy_evaluator。

设计原则:
  - 兼容性: 支持纯 numpy 数组 / pandas Series / list 输入

用法:
    from utils.alpha.fast_backtest import FastBacktest, BacktestConfig
    engine = FastBacktest(BacktestConfig(rf=0.02))
    result = engine.run(returns=returns_series, n_trials=10)
    logger.info(f"DSR={result.dsr:.2f}, 年化={result.annual_return:.2%}")
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from utils.alpha.walk_forward import (
    WalkForwardConfig,
    WalkForwardResult,
    cpcv_path_distribution,
    walk_forward_evaluate,
)

logger = logging.getLogger(__name__)

# ============================================================
# V9 评估标准阈值 (对齐 project_memory.md 硬约束)
#
# 口径说明 (2026-09-04 审计 P0-6, 诚实化):
#   - DSR raw ∈ [0,1] 是 Bailey & López de Prado 的原始概率 (本模块内部语义);
#   - V9 判定/展示统一使用 DSR Score = raw × 10 ∈ [0,10], 阈值 5.0
#     对应 raw DSR >= 0.5 (优于"随机最好策略"的概率不低于 50%)。
#   - 5.0 是项目内部宽松筛选阈值, 非学术显著性门槛 (学术须 raw >= 0.95)。
# ============================================================
V9_DSR_THRESHOLD = 5.0
V9_DSR_RAW_MIN = V9_DSR_THRESHOLD / 10.0  # raw DSR 对应下限 0.5
V9_ANNUAL_RETURN_THRESHOLD = 0.15  # 15%
V9_MAX_DRAWDOWN_THRESHOLD = 0.10  # 10%
V9_SHARPE_CV_THRESHOLD = 1.0

# Sortino 下行缺失哨兵: 无负超额且为正收益时, 真实 Sortino 无上界
# (数学上 ≈ +inf)。为保持 JSON 序列化与下游比较安全, 用大而有限值表示
# "下行风险近零"。见 _compute_sortino。
SORTINO_FINITE_SENTINEL = 99.0

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
        train_months: 训练窗口月数 (默认 24) — 仅用于估算潜在滚动段数
        test_months: 测试窗口月数 (默认 3) — 同上
        step_months: 步进月数 (默认 3) — 同上
        cv_folds: 交叉验证折数 (默认 5) — 预留, 本模块不执行 CV
        lookback_months: Sharpe CV 滚动窗口月数 (默认 12) — 预留
        rf: 无风险利率 (默认 0.02)
        trading_days: 年交易日数 (默认 252)
        purge_days: PurgedKFold 前后隔离天数 (默认 5) — 预留, 本模块不执行 CV

    诚实边界: walk-forward / purged CV 参数 (train/test/step/cv/purge) 仅用于
    `_estimate_n_windows()` 对潜在窗口段数的粗估, 本模块不执行真实滚动回测。
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
    """回测结果汇总 (单段静态绩效, 非样本外滚动验证结果).

    Attributes:
        annual_return: 年化收益率
        annual_vol: 年化波动率
        sharpe: Sharpe Ratio
        sortino: Sortino Ratio (无下行时返回有限哨兵 99.0)
        calmar: Calmar Ratio
        max_drawdown: 最大回撤 (负值; 爆仓/清零时为 -1.0)
        win_rate: 胜率
        dsr: Deflated Sharpe Ratio 原始概率 (raw, ∈ [0,1])
        dsr_score: DSR Score = raw × 10 (V9 判定/展示统一口径)
        ic_ir: 信息系数 IR (mean/std)
        sharpe_cv: 12 月滚动 Sharpe 变异系数
        n_windows: 潜在 walk-forward 段数**估算值** (未实际执行滚动回测)
        n_trials: 数据窥探修正的策略尝试数 (>= 1)
        window_details: 预留字段, 未实现真实 WF, 恒为空列表
        passed_v9: 是否通过 V9 内部评估标准
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
    dsr_score: float = 0.0
    ic_ir: float = 0.0
    sharpe_cv: float = 0.0
    n_windows: int = 0
    n_trials: int = 1
    window_details: list[dict[str, Any]] = field(default_factory=list)
    passed_v9: bool = False
    v9_failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """返回汇总字典.

        dsr 为 raw 概率 [0,1]; dsr_score = dsr × 10 与 V9 判定口径一致,
        JSON/展示层应优先使用 dsr_score。
        """
        return {
            "annual_return": self.annual_return,
            "annual_vol": self.annual_vol,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "dsr": self.dsr,
            "dsr_score": self.dsr_score,
            "ic_ir": self.ic_ir,
            "sharpe_cv": self.sharpe_cv,
            "n_windows": self.n_windows,
            "n_trials": self.n_trials,
            "passed_v9": self.passed_v9,
            "v9_failures": list(self.v9_failures),
        }

    def summary_str(self) -> str:
        """返回可读的汇总字符串 (DSR 用 Score 口径, 与判定依据一致)."""
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
            f"  DSR Score:     {self.dsr_score:.2f} (raw {self.dsr:.3f}; 阈值 >= {V9_DSR_THRESHOLD})\n"
            f"  IC_IR:         {self.ic_ir:.3f}\n"
            f"  Sharpe CV:     {self.sharpe_cv:.3f} (阈值 < {V9_SHARPE_CV_THRESHOLD})\n"
            f"  N Windows(est):{self.n_windows}\n"
            f"  N Trials:      {self.n_trials}\n"
            + (f"  V9 Failures:   {', '.join(self.v9_failures)}\n" if self.v9_failures else "")
        )


# ============================================================
# 核心引擎
# ============================================================
class FastBacktest:
    """单段静态绩效评估引擎 (非 walk-forward / purged CV 执行器).

    计算与 PerformanceMetrics / DeflatedSharpeRatio / honest_validation 等价的
    静态指标, 并独立实现 IC_IR 与 Sharpe CV。本类**不**执行滚动样本外回测:
    run() 的全部指标 (含 DSR/IC_IR/Sharpe CV) 都基于同一段 returns 全样本。

    用法:
        engine = FastBacktest()
        result = engine.run(returns=returns_series, n_trials=10)
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    # ============================================================
    # 主入口: run()
    # ============================================================
    def run(
        self,
        returns: pd.Series | np.ndarray | Sequence[float],
        n_trials: int | None = None,
        ic_series: pd.Series | np.ndarray | Sequence[float] | None = None,
        strategy_fn: Callable | None = None,
        data: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """对单段收益率序列计算静态绩效指标 (不含样本外滚动验证).

        Args:
            returns: 日收益率序列 (Series / ndarray / list)
            n_trials: 数据窥探修正的策略尝试数。DSR 需按"从多少套独立尝试中
                挑出本策略"声明, 用于惩罚多重比较。None = 未声明, 按 1 处理
                (DSR 退化为 PSR, **无数据窥探惩罚**) 并记录显式告警。
                若本结果确经多试验筛选而来, 必须传实际试验次数。
            ic_series: IC 序列 (可选, 用于计算 IC_IR)
            strategy_fn: 占位参数 (walk-forward 未实现)。传入即抛
                NotImplementedError, 不再静默忽略。真实样本外验证请用
                utils.backtest.honest_validation。
            data: 占位参数 (与 strategy_fn 配套)。传入即抛 NotImplementedError。

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

        # 1b. 占位参数 fail-fast (诚实边界 P0-5: 不再静默接收而全程零引用)
        if strategy_fn is not None or data is not None:
            raise NotImplementedError(
                "FastBacktest 未实现 walk-forward / 样本外滚动回测, 不接受 "
                "strategy_fn/data 参数。请改用 utils.backtest.honest_validation "
                "(CPCV+DSR+Noise) 或 utils.wt_backtest_engine 做真实验证。"
            )

        # 1c. n_trials 归一: None → 1, 但须显式告知调用方"无多重比较惩罚"
        if n_trials is None:
            n_trials = 1
            logger.warning(
                "FastBacktest.run: n_trials 未声明, 按 1 处理 (DSR=PSR, 无数据"
                "窥探惩罚)。若该结果系从多次策略试验中选出, 请传实际试验次数, "
                "否则 DSR 会被高估。"
            )
        trials = max(int(n_trials), 1)

        # 2. 基础绩效指标
        metrics = self._compute_basic_metrics(returns)

        # 3. DSR (raw ∈ [0,1]) 与 DSR Score (raw × 10, V9 判定口径)
        # PSR/DSR 公式要求日频未年化 SR (与 sqrt(T-1) 配套)。
        # 日频口径推导: metrics["sharpe"]=(mean×252−rf)/(std×√252),
        #   sharpe/√252 = (mean − rf/252)/std, 恰为日频无风险调整后 SR。
        sr_daily = metrics["sharpe"] / math.sqrt(self.config.trading_days) if metrics["sharpe"] != 0.0 else 0.0
        dsr = self._compute_dsr(
            sharpe_ratio=sr_daily,
            n_trials=trials,
            n_observations=len(returns),
            skewness=float(returns.skew()) if hasattr(returns, "skew") else 0.0,
            kurtosis=(float(returns.kurt()) + 3.0) if hasattr(returns, "kurt") else 3.0,
        )
        dsr_score = round(dsr * 10.0, 4)

        # 4. IC_IR (可选)
        ic_ir = self._compute_ic_ir(ic_series) if ic_series is not None else 0.0

        # 5. Sharpe CV
        sharpe_cv = self._compute_sharpe_cv(returns)

        # 6. 潜在 walk-forward 段数**估算值** (非实际执行滚动数)
        n_windows = self._estimate_n_windows(returns)

        # 7. V9 评估 (DSR 判定用 dsr_score, 与展示一致)
        v9_failures = self._check_v9_standards(
            dsr_score=dsr_score,
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
            dsr_score=dsr_score,
            ic_ir=ic_ir,
            sharpe_cv=sharpe_cv,
            n_windows=n_windows,
            n_trials=trials,
            window_details=[],
            passed_v9=(len(v9_failures) == 0),
            v9_failures=v9_failures,
        )

    # ============================================================
    # 真实样本外验证 (审计 item 14, 2026-09-10)
    # ============================================================
    def run_walk_forward(
        self,
        returns: pd.Series | np.ndarray | Sequence[float],
        strategy_fn: Callable[[np.ndarray, np.ndarray], pd.Series | np.ndarray]
        | None = None,
        config: WalkForwardConfig | None = None,
    ) -> WalkForwardResult:
        """真实滚动样本外评估 (walk-forward), 填充逐窗口明细.

        与 run() 的区别: run() 对全样本算静态指标; 本方法按
        ``config.train_days/test_days/step_days`` 切出滚动窗口, **train 与 test
        之间强制留 purge_days 间隔**, 逐窗口计算 Sharpe/回撤 并拼接 OOS 序列。

        Args:
            returns: 日收益率序列
            strategy_fn: 可选 ``(train_returns, test_returns) -> test_returns``。
                传入时用训练窗口信息生成测试期收益 (真正的"训练→样本外"滚动);
                不传则直接以各测试段实际收益作为 OOS 收益 (滚动稳定性检验)。
            config: 窗口参数 (默认 WalkForwardConfig: 252/63/63 + purge 5)

        Returns:
            WalkForwardResult — 数据不足时 ``n_windows=0`` 且 ``is_stable=False``
            (fail-closed, 绝不返回"看起来稳定"的空结果)。
        """
        arr = self._normalize_returns(returns)
        return walk_forward_evaluate(
            arr.to_numpy(), config=config, strategy_fn=strategy_fn
        )

    def run_cpcv(
        self,
        returns: pd.Series | np.ndarray | Sequence[float],
        n_groups: int = 6,
        n_test_groups: int = 2,
        purge_pct: float = 0.01,
        embargo_pct: float = 0.01,
    ) -> dict[str, Any]:
        """组合清洗交叉验证 (CPCV) 多路径 Sharpe 分布.

        委托 ms_strategy 的 CombinatorialPurgedCV 真实内核; 结果中
        ``available=False`` 表示内核不可用或样本不足 —— **调用方必须据此走
        fail-closed 分支**, 不得把 ``n_paths=0`` 当作"验证通过"。
        """
        arr = self._normalize_returns(returns)
        return cpcv_path_distribution(
            arr.to_numpy(),
            n_groups=n_groups,
            n_test_groups=n_test_groups,
            purge_pct=purge_pct,
            embargo_pct=embargo_pct,
        )

    # ============================================================
    # 基础绩效指标 (独立实现, 不依赖 v8.3 模块)
    # ============================================================
    @staticmethod
    def _compute_max_drawdown(returns: pd.Series) -> float:
        """最大回撤 (负值, 峰值起点含初始本金 1.0).

        对齐 ms_strategy metrics.py 的 expanding-peak 定义, 但修复两处:
          - P1-7 (2026-09-04): 原实现期末净值 <= 0 时返回 0.0, 爆仓策略被
            报告为"零回撤"(V9 反而 PASS)。现任一点累计净值 <= 0 (爆仓/清零)
            → 返回 -1.0 (满回撤)。
          - 原实现滚动峰值不含初始本金 1.0, 首日亏损从未被计入回撤。
            现以 shift(1, fill_value=1.0) 前插初始净值, 首日亏损正确入账。
        """
        cumulative = (1.0 + returns).cumprod()
        if not np.isfinite(cumulative).all():
            return 0.0
        if float((cumulative <= 0).any()):
            return -1.0
        prior = cumulative.shift(1, fill_value=1.0)
        peak = prior.cummax()
        dd = (cumulative - peak) / peak
        min_dd = float(dd.min())
        # 纯上行序列首日为正收益时 dd.min() > 0, 但"回撤"不应为正:
        # clamp 到 0.0 (无回撤), 保证 max_drawdown 语义恒 <= 0。
        if not np.isfinite(min_dd):
            return 0.0
        return min(min_dd, 0.0)

    def _compute_sortino(self, returns: pd.Series, annual_return: float) -> float:
        """Sortino Ratio = (年化收益 − rf) / 下行标准差 (对齐 metrics.py).

        P2 修复 (2026-09-04): 原实现无负超额时返回 float('inf') (metrics.py
        同款缺陷), 污染 JSON 序列化与下游排序/比较。现以有限哨兵
        SORTINO_FINITE_SENTINEL (99.0) 表示"下行风险近零"; 文档注明该值非
        精确比率, 仅作序列化安全的近似。
        """
        rf = self.config.rf
        rf_daily = rf / self.config.trading_days
        excess = returns - rf_daily
        downside = excess[excess < 0]
        if len(downside) == 0:
            return SORTINO_FINITE_SENTINEL if annual_return > rf else 0.0
        downside_std = float(downside.std() * math.sqrt(self.config.trading_days))
        if downside_std <= 0:
            return SORTINO_FINITE_SENTINEL if annual_return > rf else 0.0
        return (annual_return - rf) / downside_std

    def _compute_basic_metrics(self, returns: pd.Series) -> dict[str, float]:
        """计算基础绩效指标.

        独立实现以避免对 v8.3_institutional/src/backtest/metrics.py 的硬依赖,
        但公式与 PerformanceMetrics 完全对齐 (除 max_dd/sortino 的上述修复).
        """
        trading_days = self.config.trading_days
        rf = self.config.rf

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

        # 最大回撤
        max_dd = self._compute_max_drawdown(returns)

        # Sharpe
        sharpe = (annual_return - rf) / annual_vol if annual_vol > 0 else 0.0

        # Sortino
        sortino = self._compute_sortino(returns, annual_return)

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
            DSR = Φ((SR - E[SR_max]) * sqrt(T - 1) / sqrt(1 - skew·SR + (kurt-1)/4·SR²))

        注意: sharpe_ratio 必须为**日频未年化**口径 (与 sqrt(T-1) 配套)。
        年化 SR 参与 z_score 会被放大 sqrt(252) 倍导致 DSR 系统性高估。

        Args:
            sharpe_ratio: 观察到的 Sharpe Ratio (日频未年化口径)
            n_trials: 尝试的策略数
            n_observations: 样本量
            skewness: 偏度
            kurtosis: 峰度 (正态=3)

        Returns:
            DSR 值 ∈ [0, 1]
        """
        from scipy.stats import norm

        if n_observations <= 1:
            return 0.0

        # E[SR_max] 近似。n_trials <= 1 = 单次尝试, 无多重比较可修正,
        # E[max SR] = 0, DSR 退化为 PSR (Bailey & López de Prado 2014;
        # 对齐 ms_strategy metrics.py expected_max_sr / utils.backtest.deflated_sharpe)。
        # 修复 (2026-09-04): 原实现对 n_trials <= 1 直接 return 0.0, 把"未做
        # 多重试验的诚实单策略"一律判为 DSR=0, 与 PSR 定义冲突。
        if n_trials <= 1:
            e_max_sr = 0.0
        else:
            z_max = math.sqrt(2 * math.log(max(n_trials, 2)))
            correction = 1 + (skewness / 6) * (z_max**2 - 1) + ((kurtosis - 3) / 24) * (z_max**3 - 3 * z_max)
            e_max_sr = max(z_max * correction / math.sqrt(max(n_observations, 1)), 0.0)

        # PSR 分母: 偏度/峰度对 SR 方差的修正
        denom = math.sqrt(
            max(
                1.0 - skewness * sharpe_ratio + (kurtosis - 1.0) / 4.0 * sharpe_ratio**2,
                1e-12,
            )
        )

        # DSR
        z_score = (sharpe_ratio - e_max_sr) * math.sqrt(max(n_observations - 1, 1)) / denom
        dsr = float(norm.cdf(z_score))

        # 返回 raw DSR ∈ [0,1] (Bailey 原义概率)。V9 判定/展示统一在 run()
        # 中换算为 dsr_score = raw × 10 (阈值 5.0 ↔ raw 0.5), 见 V9 常量区
        # 口径说明, 保证用户看到的数字与判定依据一致。
        return dsr

    # ============================================================
    # IC_IR (信息系数 IR)
    # ============================================================
    def _compute_ic_ir(
        self,
        ic_series: pd.Series | np.ndarray | Sequence[float],
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
    # 潜在滚动段数估算 (仅为估算, 不执行滚动回测)
    # ============================================================
    def _estimate_n_windows(self, returns: pd.Series) -> int:
        """估算"若能执行 WF 时的潜在窗口段数" (非实际执行数).

        诚实边界 (P0-5): 本方法仅按 train/test/step 配置粗估段数上限,
        不代表 FastBacktest 真正执行过 walk-forward。run() 不产生任何
        滚动样本外结果, n_windows 仅作参考字段保留。
        """
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
        dsr_score: float,
        annual_return: float,
        max_drawdown: float,
        sharpe_cv: float,
    ) -> list[str]:
        """检查是否通过 V9 内部评估标准.

        V9 标准 (对齐 project_memory.md, 展示与判定同一标度):
            - DSR Score = raw DSR × 10 >= 5.0 (↔ raw DSR >= 0.5)
            - 年化 >= 15%
            - 最大回撤 <= 10% (绝对值)
            - Sharpe CV < 1.0

        Args:
            dsr_score: DSR Score = raw DSR × 10 (V9 判定口径)
            annual_return: 年化收益率
            max_drawdown: 最大回撤 (负值)
            sharpe_cv: Sharpe 变异系数

        Returns:
            未通过的标准列表 (空列表表示全部通过)
        """
        failures = []

        if dsr_score < V9_DSR_THRESHOLD:
            failures.append(
                f"DSR Score={dsr_score:.2f} < {V9_DSR_THRESHOLD} (raw DSR {dsr_score / 10.0:.3f} < {V9_DSR_RAW_MIN})"
            )

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
        returns: pd.Series | np.ndarray | Sequence[float],
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
    returns: pd.Series | np.ndarray | Sequence[float],
    n_trials: int | None = None,
    ic_series: pd.Series | np.ndarray | Sequence[float] | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """快速运行静态绩效评估 (模块级便捷函数).

    Args:
        returns: 日收益率序列
        n_trials: 数据窥探修正的策略尝试数 (None = 未声明 → 视为 1,
            即 DSR=PSR 无多重比较惩罚, 详见 FastBacktest.run)
        ic_series: IC 序列 (可选)
        config: 回测配置 (可选, 使用默认值)

    Returns:
        BacktestResult 汇总结果
    """
    engine = FastBacktest(config)
    return engine.run(returns=returns, n_trials=n_trials, ic_series=ic_series)


def check_v9_standards(result: BacktestResult) -> tuple[bool, list[str]]:
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
    "V9_DSR_RAW_MIN",
    "V9_MAX_DRAWDOWN_THRESHOLD",
    "V9_SHARPE_CV_THRESHOLD",
    "SORTINO_FINITE_SENTINEL",
    "BacktestConfig",
    "BacktestResult",
    "FastBacktest",
    "FastBacktestError",
    "InsufficientDataError",
    "WalkForwardConfig",
    "WalkForwardResult",
    "check_v9_standards",
    "run_fast_backtest",
]

