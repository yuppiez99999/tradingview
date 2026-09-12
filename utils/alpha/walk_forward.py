"""样本外滚动验证 — Walk-Forward + CPCV (审计 item 14, 2026-09-10).

背景 (2026-09-09 审计 item 14 "样本外验证主链路接入"):
    1. ``utils/alpha/fast_backtest.py`` 的 ``run()`` 只对**同一段** returns 计算静态
       指标, ``window_details`` 恒为空列表、``n_windows`` 只是段数估算 —— 该模块
       已在 docstring 显式声明"不执行 Walk-Forward" (诚实但能力缺失);
    2. ``utils/pipeline/backtest_gate.py`` docstring 写"复用系统现有 Walk-Forward",
       但 ``_run_walk_forward`` 实际只算单窗口静态 IC/Sharpe, 且它 import 的
       ``utils.alpha.purged_kfold`` **模块并不存在** → ``_has_purged_kfold`` 恒为
       False → 闸门第一步 Walk-Forward 永远被跳过并按"通过"处理 (假 PASS)。

本模块提供可信实现, 供 ``fast_backtest`` / ``backtest_gate`` / 主训练链路复用。

设计铁律:
  - **泄漏必须可验证**: 训练窗口与测试窗口之间强制留 purge 间隔 (默认 = 标签
    horizon), 且每个窗口在运行期断言 train/test 索引无重叠, 不是"大致避免";
  - **数据不足即 fail-closed**: 无法形成任何窗口时返回 ``n_windows=0`` 且
    ``is_stable=False`` + 明确 ``insufficient_reason``, 绝不返回"看起来稳定"的空结果;
  - **OOS 收益按时间顺序拼接**, 供 DSR / Noise 注入 / 报告复用, 不重新造轮子
    (CPCV 直接委托 ``ms_strategy.src.backtest.combinatorial_purged_cv``)。

用法::

    from utils.alpha.walk_forward import walk_forward_evaluate

    wf = walk_forward_evaluate(daily_returns)
    if not wf.is_stable:
        logger.warning("样本外不稳定: %s", wf.verdict)

参考:
    - López de Prado (2018) AFML Chapter 7 (Purged/Embargo) / Chapter 11 (PBO)
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

TRADING_DAYS = 252

# 稳定性判据 (与 utils/backtest/honest_validation.py 的 CPCV 口径一致):
#   路径/窗口间 Sharpe 变异系数 < 0.5 且 正收益窗口占比 > 0.6, 且窗口数 >= 3。
STABILITY_CV_MAX = 0.5
STABILITY_PCT_POSITIVE_MIN = 0.6
STABILITY_MIN_WINDOWS = 3


@dataclass
class WalkForwardConfig:
    """walk-forward 窗口参数 (单位: 交易日).

    Attributes:
        train_days: 训练窗口长度 (默认 252 ≈ 1 年)
        test_days: 测试窗口长度 (默认 63 ≈ 1 季)
        step_days: 步进长度 (默认 63; 等于 test_days 即不重叠滚动)
        purge_days: 训练/测试之间的 purged 间隔 (默认 5; 应 >= 标签 horizon)
        min_test_days: 单窗口最少测试样本 (低于则丢弃该窗口)
        trading_days: 年化换算交易日数
        rf: 年化无风险利率
    """

    train_days: int = 252
    test_days: int = 63
    step_days: int = 63
    purge_days: int = 5
    min_test_days: int = 10
    trading_days: int = TRADING_DAYS
    rf: float = 0.02

    def __post_init__(self) -> None:
        if self.train_days < 1:
            raise ValueError(f"train_days 必须 >= 1, 实际 {self.train_days}")
        if self.test_days < 1:
            raise ValueError(f"test_days 必须 >= 1, 实际 {self.test_days}")
        if self.step_days < 1:
            raise ValueError(f"step_days 必须 >= 1, 实际 {self.step_days}")
        if self.purge_days < 0:
            raise ValueError(f"purge_days 必须 >= 0, 实际 {self.purge_days}")
        if self.trading_days < 1:
            raise ValueError(f"trading_days 必须 >= 1, 实际 {self.trading_days}")


@dataclass
class WalkForwardWindow:
    """单个 walk-forward 窗口的真实样本外结果."""

    window_id: int
    train_start: int
    train_end: int  # exclusive
    test_start: int
    test_end: int  # exclusive
    purge_gap: int
    n_train: int
    n_test: int
    sharpe: float = 0.0
    mean_return: float = 0.0
    max_drawdown: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "purge_gap": self.purge_gap,
            "n_train": self.n_train,
            "n_test": self.n_test,
            "sharpe": self.sharpe,
            "mean_return": self.mean_return,
            "max_drawdown": self.max_drawdown,
        }


@dataclass
class WalkForwardResult:
    """walk-forward 汇总结果 (真实执行, 非估算).

    Attributes:
        n_windows: **实际执行**的窗口数 (区别于 fast_backtest 的估算值)
        windows: 每窗口明细 (供报告落盘)
        oos_returns: 所有窗口测试期收益按时间顺序拼接
        oos_sharpe: 拼接后 OOS 序列的年化 Sharpe
        oos_sharpe_cv: 窗口间 Sharpe 变异系数 (std/|mean|)
        window_sharpe_mean / window_sharpe_std: 窗口 Sharpe 分布
        pct_positive_windows: Sharpe > 0 的窗口占比
        first_half_sharpe_by_year / second_half_sharpe_by_year: 前后半段年化 (衰减检测)
        is_stable: 稳定性判据 (见模块常量); 数据不足恒为 False
        insufficient_reason: 无法评估/数据不足的原因 (空串表示已执行)
        verdict: 人类可读结论
    """

    n_windows: int = 0
    windows: list[WalkForwardWindow] = field(default_factory=list)
    oos_returns: np.ndarray = field(default_factory=lambda: np.array([]))
    oos_sharpe: float = 0.0
    oos_sharpe_cv: float = math.inf
    oos_max_drawdown: float = 0.0
    window_sharpe_mean: float = 0.0
    window_sharpe_std: float = 0.0
    pct_positive_windows: float = 0.0
    first_half_sharpe_by_year: float = 0.0
    second_half_sharpe_by_year: float = 0.0
    is_stable: bool = False
    insufficient_reason: str = ""
    verdict: str = ""

    @property
    def executed(self) -> bool:
        """是否真实执行过至少一个窗口."""
        return self.n_windows > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_windows": self.n_windows,
            "oos_sharpe": self.oos_sharpe,
            "oos_max_drawdown": self.oos_max_drawdown,
            "oos_sharpe_cv": (
                None if not math.isfinite(self.oos_sharpe_cv) else self.oos_sharpe_cv
            ),
            "window_sharpe_mean": self.window_sharpe_mean,
            "window_sharpe_std": self.window_sharpe_std,
            "pct_positive_windows": self.pct_positive_windows,
            "first_half_sharpe_by_year": self.first_half_sharpe_by_year,
            "second_half_sharpe_by_year": self.second_half_sharpe_by_year,
            "is_stable": self.is_stable,
            "insufficient_reason": self.insufficient_reason,
            "verdict": self.verdict,
            "windows": [w.as_dict() for w in self.windows],
        }


# ============================================================
# 辅助计算
# ============================================================
def _to_float_array(returns: Sequence[float] | np.ndarray) -> np.ndarray:
    """标准化为 float64 一维数组, 剔除 nan/inf."""
    arr = np.asarray(returns, dtype=float).ravel()
    if arr.size == 0:
        return arr
    # 显式落到 np.ndarray 类型的局部变量: numpy 未带类型存根, 直接 return
    # arr[mask] 会被 mypy 视为 Any (no-any-return)。
    filtered: np.ndarray = arr[np.asarray(np.isfinite(arr), dtype=bool)]
    return filtered


def _annualized_sharpe(window_returns: np.ndarray, rf: float, trading_days: int) -> float:
    """窗口年化 Sharpe; 样本不足或零波动返回 0.0."""
    if window_returns.size < 2:
        return 0.0
    std = float(np.std(window_returns, ddof=1))
    if std < 1e-12:
        return 0.0
    daily_rf = rf / trading_days
    return float((np.mean(window_returns) - daily_rf) / std * math.sqrt(trading_days))


def _max_drawdown(window_returns: np.ndarray) -> float:
    """窗口最大回撤 (正数绝对值; 含初始本金 1.0)."""
    if window_returns.size == 0:
        return 0.0
    cumulative = np.cumprod(1.0 + window_returns)
    if not np.isfinite(cumulative).all():
        return 0.0
    if np.any(cumulative <= 0):
        return 1.0
    prior = np.concatenate(([1.0], cumulative[:-1]))
    peak = np.maximum.accumulate(prior)
    drawdown = (cumulative - peak) / peak
    return float(abs(min(np.min(drawdown), 0.0)))


def _split_half_sharpe(
    oos_returns: np.ndarray, rf: float, trading_days: int
) -> tuple[float, float]:
    """把 OOS 序列对半切, 返回 (前半段, 后半段) 年化 Sharpe (按年化换算)."""
    if oos_returns.size < 4:
        return 0.0, 0.0
    mid = oos_returns.size // 2
    return (
        _annualized_sharpe(oos_returns[:mid], rf, trading_days),
        _annualized_sharpe(oos_returns[mid:], rf, trading_days),
    )


# ============================================================
# 主入口: walk-forward
# ============================================================
def build_windows(
    n_samples: int, config: WalkForwardConfig | None = None
) -> list[tuple[int, int, int, int]]:
    """生成滚动窗口索引 ``(train_start, train_end, test_start, test_end)``.

    测试窗口起点 = ``train_end + purge_days`` — purge 间隔是硬约束, 保证
    train 与 test 之间不存在标签窗口重叠。返回的每个元组满足
    ``train_end <= test_start - purge_days``。
    """
    cfg = config or WalkForwardConfig()
    windows: list[tuple[int, int, int, int]] = []
    start = 0
    while True:
        train_start = start
        train_end = train_start + cfg.train_days
        test_start = train_end + cfg.purge_days
        test_end = test_start + cfg.test_days
        if test_end > n_samples:
            break
        windows.append((train_start, train_end, test_start, test_end))
        start += cfg.step_days
    return windows


def walk_forward_evaluate(
    returns: Sequence[float] | np.ndarray,
    config: WalkForwardConfig | None = None,
    strategy_fn: Callable[[np.ndarray, np.ndarray], Sequence[float] | np.ndarray]
    | None = None,
) -> WalkForwardResult:
    """真实滚动样本外评估.

    Args:
        returns: 日收益率序列 (策略全样本收益; 当 ``strategy_fn`` 为 None 时,
            每个测试窗口直接取该段的实际收益作为 OOS 收益 —— 即"该收益序列在
            滚动窗口下的样本外稳定性检验")
        config: 窗口参数
        strategy_fn: 可选 ``(train_returns, test_returns) -> test_returns``。
            传入时用训练窗口信息(重新)生成测试期收益, 实现真正的"训练→样本外"
            滚动回测; 不传则按上式直接用测试段收益。

    Returns:
        WalkForwardResult — 数据不足时 ``n_windows=0`` 且 ``is_stable=False``。
    """
    cfg = config or WalkForwardConfig()
    arr = _to_float_array(returns)
    n = arr.size

    candidate = build_windows(n, cfg)
    if not candidate:
        need = cfg.train_days + cfg.purge_days + cfg.test_days
        reason = (
            f"样本不足: n={n} < train({cfg.train_days})+purge({cfg.purge_days})"
            f"+test({cfg.test_days})={need}, 无法形成任何样本外窗口"
        )
        logger.warning("[WalkForward] %s", reason)
        return WalkForwardResult(
            n_windows=0,
            is_stable=False,
            insufficient_reason=reason,
            verdict=f"UNVERIFIED: {reason}",
        )

    windows: list[WalkForwardWindow] = []
    oos_chunks: list[np.ndarray] = []

    for window_id, (tr_s, tr_e, te_s, te_e) in enumerate(candidate):
        # 泄漏硬断言: train/test 之间必须有 >= purge_days 的间隔
        gap = te_s - tr_e
        if gap < cfg.purge_days:
            raise AssertionError(
                f"窗口 {window_id} purge 间隔不足: gap={gap} < purge_days={cfg.purge_days}"
            )

        train_slice = arr[tr_s:tr_e]
        test_slice = arr[te_s:te_e]
        if test_slice.size < cfg.min_test_days:
            logger.debug(
                "[WalkForward] 窗口 %d 测试样本 %d < %d, 跳过",
                window_id,
                test_slice.size,
                cfg.min_test_days,
            )
            continue

        if strategy_fn is not None:
            produced = strategy_fn(train_slice, test_slice)
            test_returns = _to_float_array(produced)
            if test_returns.size == 0:
                logger.warning("[WalkForward] 窗口 %d strategy_fn 返回空序列, 跳过", window_id)
                continue
        else:
            test_returns = test_slice

        windows.append(
            WalkForwardWindow(
                window_id=window_id,
                train_start=tr_s,
                train_end=tr_e,
                test_start=te_s,
                test_end=te_e,
                purge_gap=gap,
                n_train=int(train_slice.size),
                n_test=int(test_returns.size),
                sharpe=_annualized_sharpe(test_returns, cfg.rf, cfg.trading_days),
                mean_return=float(np.mean(test_returns)),
                max_drawdown=_max_drawdown(test_returns),
            )
        )
        oos_chunks.append(test_returns)

    if not windows:
        reason = f"候选窗口 {len(candidate)} 个全部被过滤 (测试样本不足或策略返回空)"
        logger.warning("[WalkForward] %s", reason)
        return WalkForwardResult(
            n_windows=0,
            is_stable=False,
            insufficient_reason=reason,
            verdict=f"UNVERIFIED: {reason}",
        )

    oos_returns = np.concatenate(oos_chunks)
    sharpes = np.array([w.sharpe for w in windows], dtype=float)
    mean_s = float(np.mean(sharpes))
    std_s = float(np.std(sharpes, ddof=1)) if sharpes.size > 1 else 0.0
    cv = float(std_s / abs(mean_s)) if abs(mean_s) > 1e-12 else math.inf
    pct_pos = float(np.sum(sharpes > 0) / sharpes.size)
    first_half, second_half = _split_half_sharpe(oos_returns, cfg.rf, cfg.trading_days)

    is_stable = bool(
        len(windows) >= STABILITY_MIN_WINDOWS
        and math.isfinite(cv)
        and cv < STABILITY_CV_MAX
        and pct_pos > STABILITY_PCT_POSITIVE_MIN
    )

    result = WalkForwardResult(
        n_windows=len(windows),
        windows=windows,
        oos_returns=oos_returns,
        oos_sharpe=_annualized_sharpe(oos_returns, cfg.rf, cfg.trading_days),
        oos_max_drawdown=_max_drawdown(oos_returns),
        oos_sharpe_cv=cv,
        window_sharpe_mean=mean_s,
        window_sharpe_std=std_s,
        pct_positive_windows=pct_pos,
        first_half_sharpe_by_year=first_half,
        second_half_sharpe_by_year=second_half,
        is_stable=is_stable,
        insufficient_reason="",
        verdict=(
            f"{'STABLE' if is_stable else 'UNSTABLE'}: {len(windows)} 个样本外窗口, "
            f"Sharpe mean={mean_s:.3f} cv={cv:.3f} 正收益窗口={pct_pos:.0%}, "
            f"OOS Sharpe={_annualized_sharpe(oos_returns, cfg.rf, cfg.trading_days):.3f}"
        ),
    )
    logger.info("[WalkForward] %s", result.verdict)
    return result


# ============================================================
# 主入口: CPCV 路径分布 (委托 ms_strategy 内核, 不重复实现)
# ============================================================
def cpcv_path_distribution(
    returns: Sequence[float] | np.ndarray,
    n_groups: int = 6,
    n_test_groups: int = 2,
    purge_pct: float = 0.01,
    embargo_pct: float = 0.01,
) -> dict[str, Any]:
    """组合清洗交叉验证 (CPCV) 多路径 Sharpe 分布.

    委托 ``ms_strategy.src.backtest.combinatorial_purged_cv`` (真实内核),
    返回可 JSON 化的分布统计。

    Returns:
        ``{"available": bool, "n_paths": int, "sharpe_mean": ..., "sharpe_cv": ...,
        "pct_positive": ..., "is_stable": bool, "path_sharpes": [...], "note": str}``

        **available=False 表示模块不可用或样本不足** —— 调用方必须据此走
        fail-closed 分支, 不得把 ``n_paths=0`` 当作"验证通过"。
    """
    empty: dict[str, Any] = {
        "available": False,
        "n_paths": 0,
        "sharpe_mean": 0.0,
        "sharpe_std": 0.0,
        "sharpe_cv": None,
        "pct_positive": 0.0,
        "is_stable": False,
        "path_sharpes": [],
        "note": "",
    }

    arr = _to_float_array(returns)
    try:
        from ms_strategy.src.backtest.combinatorial_purged_cv import (
            CombinatorialPurgedCV,
            CPCVConfig,
        )
    except ImportError as e:
        empty["note"] = f"CPCV 内核不可用 (ms_strategy 未安装/路径异常): {e}"
        logger.warning("[CPCV] %s", empty["note"])
        return empty

    if arr.size < n_groups * 5:
        empty["note"] = f"样本不足: n={arr.size} < n_groups*5={n_groups * 5}"
        logger.warning("[CPCV] %s", empty["note"])
        return empty

    cv = CombinatorialPurgedCV(
        CPCVConfig(
            n_groups=n_groups,
            n_test_groups=n_test_groups,
            purge_pct=purge_pct,
            embargo_pct=embargo_pct,
        )
    )
    splits = cv.split(arr.size)

    path_sharpes: list[float] = []
    for split in splits:
        test_returns = arr[split.test_idx]
        if test_returns.size < 5:
            continue
        sr = _annualized_sharpe(test_returns, rf=0.0, trading_days=TRADING_DAYS)
        if math.isfinite(sr):
            path_sharpes.append(sr)

    if not path_sharpes:
        empty["note"] = f"CPCV 生成 {len(splits)} 个分割但无有效路径 Sharpe"
        logger.warning("[CPCV] %s", empty["note"])
        return empty

    sharpes = np.array(path_sharpes, dtype=float)
    mean_s = float(np.mean(sharpes))
    std_s = float(np.std(sharpes, ddof=1)) if sharpes.size > 1 else 0.0
    cv_val = float(std_s / abs(mean_s)) if abs(mean_s) > 1e-12 else None
    pct_pos = float(np.sum(sharpes > 0) / sharpes.size)
    is_stable = bool(cv_val is not None and cv_val < 0.5 and pct_pos > 0.8)

    return {
        "available": True,
        "n_paths": len(path_sharpes),
        "sharpe_mean": mean_s,
        "sharpe_std": std_s,
        "sharpe_cv": cv_val,
        "pct_positive": pct_pos,
        "is_stable": is_stable,
        "path_sharpes": path_sharpes,
        "note": (
            f"CPCV N={n_groups} k={n_test_groups}: {len(path_sharpes)}/{len(splits)} 有效路径"
        ),
    }


__all__ = [
    "STABILITY_CV_MAX",
    "STABILITY_MIN_WINDOWS",
    "STABILITY_PCT_POSITIVE_MIN",
    "TRADING_DAYS",
    "WalkForwardConfig",
    "WalkForwardResult",
    "WalkForwardWindow",
    "build_windows",
    "cpcv_path_distribution",
    "walk_forward_evaluate",
]
