# -*- coding: utf-8 -*-
"""FactorHistoryBuilder - 日频因子值历史构建器（P0 改进核心模块）

为 Gate2 IC_IR / Gate3 DSR / Shadow / Regime 提供真实日频因子值序列，
替代首批次的占位实现 `[candidate.values] * 90`。

设计思路：
    对每个历史时间点 t，截断 price_data 到 [0:t+1]，调用 adapter 重新计算因子值，
    收集形成日频序列。这是「时间序列回放」的标准做法。

性能：
    - 120 个时间点 × 16 因子 × 23 标的 = 44160 次计算
    - 实测 < 10 秒（单线程）

输入：
    price_data: {symbol: {closes, volumes, highs, lows, opens}}（完整历史，>= 120 天）
    fundamentals: {symbol: {pe, pb, roe, ...}}（当前快照，回看时假设不变）
    benchmark_returns: List[float]（完整基准收益率序列）

输出：
    factor_history: {factor_name: List[Dict[str, float]]}
        每个元素是一天的 {symbol: factor_value}
    forward_returns_history: List[Dict[str, float]]
        每个元素是一天的 {symbol: forward_return}
    valid_dates: List[int]（有效的 t 索引列表）
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("factor_history_builder")


def build_factor_history(
    adapter: Any,
    price_data: Dict[str, Dict[str, List[float]]],
    fundamentals: Optional[Dict[str, Dict[str, float]]] = None,
    benchmark_returns: Optional[List[float]] = None,
    history_days: int = 120,
    forward_window: int = 5,
    fundamentals_history: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, List[Dict[str, float]]], List[Dict[str, float]], List[int]]:
    """构建日频因子值历史序列

    Args:
        adapter: VibeTradingFactorAdapter 实例（提供 compute_candidate_factors）
        price_data: 完整价格数据（closes/volumes 等）
        fundamentals: 财务数据（当前快照，回看时假设不变 - 简化）
        benchmark_returns: 基准收益率序列
        history_days: 历史窗口天数（默认 120）
        forward_window: forward return 窗口（默认 5 日）
        fundamentals_history: P2.2 QualityTrend 类因子所需的历史季度财务数据
            {symbol: {"quarters": [...], "n_valid": int, ...}}
            必须传入，否则 QualityTrend 因子日频历史为空，IC_IR 会降级到 legacy 单期估算
            （P2.2 v6 修复：之前未传递，导致 v5 报告中所有 QualityTrend IC_IR 都是伪 IC_IR）

    Returns:
        factor_history: {factor_name: List[{symbol: factor_value}]}
            每个因子在 history_days 天内的日频值序列
        forward_returns_history: List[{symbol: forward_return}]
            每日的 forward return（用于 IC 计算）
        valid_dates: List[int]
            有效的 t 索引列表（从 len-HISTORY到 len-1-forward_window）
    """
    fundamentals = fundamentals or {}
    benchmark_returns = benchmark_returns or []

    # P1.1 改进：过滤掉数据过短的标的（如 ETF / 新股数据不足）
    # 用分位数阈值（默认 25%）自动剔除异常短数据标的，避免 1-2 只短数据标的拖累整体
    all_lens = sorted([len(data.get("closes", [])) for data in price_data.values()])
    if all_lens:
        median_len = all_lens[len(all_lens) // 2]
        # 任何标的长度 < 中位数的 70% 视为异常短，剔除
        # 例如：5 ETF 只有 134 天，其他股票都有 484 天，median=484，阈值 = 338
        length_threshold = int(median_len * 0.7)
        filtered_price = {
            sym: data for sym, data in price_data.items()
            if len(data.get("closes", [])) >= length_threshold
        }
        excluded = set(price_data.keys()) - set(filtered_price.keys())
        if excluded:
            logger.info(
                "[FactorHistory] 过滤短数据标的 | 阈值=%d 天 | 剔除 %d 个: %s",
                length_threshold, len(excluded), sorted(excluded)[:10],
            )
        price_data = filtered_price

    # 找最小公共长度
    min_len = min(
        (len(data.get("closes", [])) for data in price_data.values()),
        default=0,
    )
    if min_len < history_days + forward_window:
        logger.warning(
            "[FactorHistory] 数据长度不足 | min_len=%d < history_days+forward=%d",
            min_len, history_days + forward_window,
        )
        history_days = max(20, min_len - forward_window - 10)

    # 有效的 t 索引：从 (min_len - history_days - forward_window) 到 (min_len - forward_window - 1)
    # 对每个 t，截断 price_data 到 [0:t+1]，然后用 [t+1, t+1+forward_window] 算 forward return
    start_t = min_len - history_days - forward_window
    end_t = min_len - forward_window
    if start_t < 60:  # 至少需要 60 天历史才能算因子
        start_t = 60
    valid_dates = list(range(start_t, end_t))

    logger.info(
        "[FactorHistory] 构建日频因子历史 | 标的=%d min_len=%d history_days=%d forward_window=%d | "
        "valid_dates: [%d, %d) 共 %d 天",
        len(price_data), min_len, history_days, forward_window, start_t, end_t, len(valid_dates),
    )

    # 构建每日 factor_values
    factor_history: Dict[str, List[Dict[str, float]]] = {}
    forward_returns_history: List[Dict[str, float]] = []

    for t in valid_dates:
        # 截断 price_data 到 [0:t+1]
        truncated_price: Dict[str, Dict[str, List[float]]] = {}
        for sym, data in price_data.items():
            truncated_price[sym] = {
                k: list(v[: t + 1]) for k, v in data.items() if isinstance(v, list)
            }

        # 计算该时间点的候选因子
        try:
            pool = adapter.compute_candidate_factors(
                price_data=truncated_price,
                fundamentals=fundamentals,
                benchmark_returns=benchmark_returns[: t + 1] if benchmark_returns else None,
                fundamentals_history=fundamentals_history,
            )
        except Exception as e:
            logger.warning("[FactorHistory] t=%d 因子计算失败: %s", t, e)
            continue

        # 收集每个因子的当日值
        for fname, cf in pool.factors.items():
            if fname not in factor_history:
                factor_history[fname] = []
            # 确保长度对齐（如果某天某因子没算出，则补 None）
            while len(factor_history[fname]) < len(forward_returns_history):
                factor_history[fname].append({})
            factor_history[fname].append(dict(cf.values))

        # 计算 forward returns: 从 t 到 t+forward_window 的收益率
        fwd_dict: Dict[str, float] = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if t + forward_window < len(closes) and closes[t] > 0:
                fwd_ret = float(closes[t + forward_window] / closes[t] - 1)
                if math.isfinite(fwd_ret):
                    fwd_dict[sym] = fwd_ret
        forward_returns_history.append(fwd_dict)

    # 长度对齐（某些因子可能在某些日子没算出）
    for fname in factor_history:
        while len(factor_history[fname]) < len(forward_returns_history):
            factor_history[fname].append({})

    logger.info(
        "[FactorHistory] 构建完成 | 因子数=%d 序列长度=%d",
        len(factor_history), len(forward_returns_history),
    )
    return factor_history, forward_returns_history, valid_dates


def compute_rolling_ic_series(
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    min_samples: int = 5,
) -> List[float]:
    """计算 IC 序列（每日一个 cross-sectional IC）

    Args:
        factor_history: 单个因子的日频值序列
        forward_returns_history: 日频 forward returns
        min_samples: 计算单日 IC 最少所需标的数

    Returns:
        ic_series: List[float]，每日的 Pearson IC
    """
    ic_series: List[float] = []
    n = min(len(factor_history), len(forward_returns_history))
    for i in range(n):
        fv = factor_history[i]
        fr = forward_returns_history[i]
        common = [s for s in fv if s in fr and math.isfinite(fv[s]) and math.isfinite(fr[s])]
        if len(common) < min_samples:
            ic_series.append(0.0)
            continue
        x = np.array([fv[s] for s in common], dtype=float)
        y = np.array([fr[s] for s in common], dtype=float)
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            ic_series.append(0.0)
            continue
        ic = float(np.corrcoef(x, y)[0, 1])
        if not math.isfinite(ic):
            ic = 0.0
        ic_series.append(ic)
    return ic_series


def compute_ic_ir(
    ic_series: List[float],
    min_periods: int = 20,
) -> Tuple[float, float, float]:
    """计算 IC_IR = mean(IC) / std(IC)

    Args:
        ic_series: 日频 IC 序列
        min_periods: 最少所需 IC 样本数

    Returns:
        (ic_ir, ic_mean, ic_std)
    """
    if len(ic_series) < min_periods:
        return 0.0, 0.0, 0.0
    arr = np.array(ic_series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < min_periods:
        return 0.0, 0.0, 0.0
    ic_mean = float(np.mean(arr))
    ic_std = float(np.std(arr, ddof=1))
    if ic_std < 1e-12:
        return 0.0, ic_mean, 0.0
    ic_ir = ic_mean / ic_std
    if not math.isfinite(ic_ir):
        return 0.0, ic_mean, ic_std
    return float(ic_ir), ic_mean, ic_std


def compute_ic_decay(
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    short_window: int = 5,
    long_window: int = 20,
) -> float:
    """计算 IC 衰减率 = 1 - IC_recent / IC_long

    含义：
        - decay > 0  : IC 衰减（近期 IC < 长期 IC，因子效力减弱）
        - decay = 0  : IC 稳定（近期 ≈ 长期）
        - decay < 0  : IC 增强（近期 IC > 长期 IC）
        - 返回值域严格限制在 [-1, 1]，避免 longer_ic 极小导致比值爆炸

    边界处理：
        - 样本不足 -> 返回 0.0（中性，无明确衰减信号）
        - |longer_ic| < 0.01 -> 返回 0.0（长期 IC 过小，比值不可信）
        - IC 反号（recent 与 longer 异号）-> 返回 1.0（IC 完全反转，视作最大衰减）
        - 比值越界 -> 截断到 [-1, 1]
    """
    n = min(len(factor_history), len(forward_returns_history))
    if n < long_window + 5:
        return 0.0  # 样本不足，返回中性

    # 用 rolling IC 序列，取近 short 日 vs 近 long 日
    ic_series = compute_rolling_ic_series(factor_history, forward_returns_history)
    if len(ic_series) < long_window:
        return 0.0

    recent_ic = float(np.mean(ic_series[-short_window:])) if short_window > 0 else 0.0
    longer_ic = float(np.mean(ic_series[-long_window:])) if long_window > 0 else 0.0

    # 长期 IC 过小 -> 比值不可信，返回中性
    if abs(longer_ic) < 0.01:
        return 0.0

    # v6 修复：异号判断前加噪声阈值检查
    # 之前 bug：recent_ic=-0.0027（噪声级）与 longer_ic=+0.033 异号即触发 return 1.0
    # 实际上 |recent_ic| 接近 0 表示 IC 弱化（接近 0），不应视为反转
    # 噪声阈值 0.005：低于此值视为噪声，按比值计算 decay 而非触发反转
    RECENT_IC_NOISE_THRESHOLD = 0.005
    if abs(recent_ic) >= RECENT_IC_NOISE_THRESHOLD and recent_ic * longer_ic < 0:
        # 真实反转（|recent| 显著且异号）-> IC 完全反转，视作最大衰减
        return 1.0

    # IC 弱化或同号：decay = 1 - |recent| / |longer|
    # - recent=longer → decay=0 (无衰减)
    # - recent=0 → decay=1 (完全弱化)
    # - recent=-longer 但 |recent|<噪声阈值 → decay≈1 (近 0 视为弱化)
    decay = 1.0 - abs(recent_ic) / abs(longer_ic) if abs(longer_ic) > 1e-9 else 0.0
    if not math.isfinite(decay):
        return 0.0

    # 截断到 [-1, 1] 域（防止极端比值越界）
    decay = max(-1.0, min(1.0, decay))
    return float(decay)
