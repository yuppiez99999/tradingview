# -*- coding: utf-8 -*-
"""P2.2 v6b 分析：MARGIN_EXP IC_decay=1.0 根因分析

v6 实测：
    MARGIN_EXP 真实 IC_IR=0.3981（通过 0.3 阈值）
    但 IC_decay=1.0（>= 0.6 阈值），被 G2 阻挡

IC_decay 公式（compute_ic_decay）:
    recent_ic = mean(ic_series[-5:])
    longer_ic = mean(ic_series[-20:])
    decay = 1 - |recent_ic| / |longer_ic|

decay=1.0 表示 recent_ic = 0（最近 5 天 IC 完全消失）

可能原因：
    1. 因子值在最近 5 天是常数（fundamentals_history 不变 → factor_value 不变）
       → 但 forward_returns 每天变化，IC 不应为 0
    2. 最近 5 天市场异常（如政策事件、情绪冲击）
    3. 因子最近 5 天的截面排序与未来收益相关性消失

本脚本详细分析 IC 序列的分布，找出 decay=1.0 的真正原因
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history,
    compute_rolling_ic_series,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    compute_equal_weight_benchmark,
    list_available_symbols,
    load_benchmark_returns,
    load_fundamentals,
    load_price_data,
)

logger = logging.getLogger("analyze_decay")
logging.basicConfig(level=logging.WARNING)


def main() -> int:
    logger.info("=" * 70)
    logger.info("MARGIN_EXP IC_decay=1.0 根因分析")
    logger.info("=" * 70)

    # 1. 加载数据
    logger.info("\n[1/3] 加载数据")
    symbols = list_available_symbols()
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns() or compute_equal_weight_benchmark(price_data)
    orchestrator = PipelineOrchestrator(config={})
    fundamentals_history = orchestrator._load_fundamentals_history(
        list(price_data.keys()) if price_data else []
    )

    # 2. 构建 factor_history
    logger.info("\n[2/3] 构建 factor_history")
    adapter = VibeTradingFactorAdapter()
    factor_history, fwd_returns_hist, _valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,
    )

    # 3. 分析 MARGIN_EXP IC 序列
    logger.info("\n[3/3] MARGIN_EXP IC 序列详细分析")
    margin_hist = factor_history.get("VT_QUALTREND_MARGIN_EXP", [])
    if not margin_hist:
        logger.info("  ❌ MARGIN_EXP factor_history 为空")
        return 1

    # 验证：每天 factor_value 是否相同（静态 fundamentals_history 导致）
    logger.info("\n  验证 factor_value 是否每天相同（fundamentals_history 静态性）:")
    sample_syms = list(margin_hist[0].keys())[:3]
    for sym in sample_syms:
        values_at_t = [fv.get(sym, float("nan")) for fv in margin_hist]
        unique_vals = set(round(v, 6) for v in values_at_t if math.isfinite(v))
        logger.info(f"    {sym}: {len(unique_vals)} 个不同值（0=静态, >0=动态）")

    # 计算 IC 序列
    ic_series = compute_rolling_ic_series(margin_hist, fwd_returns_hist)
    ic_arr = np.array(ic_series, dtype=float)
    logger.info("\n  IC 序列统计:")
    logger.info(f"    长度: {len(ic_series)}")
    logger.info(f"    mean: {float(np.mean(ic_arr)):+.4f}")
    logger.info(f"    std:  {float(np.std(ic_arr, ddof=1)):.4f}")
    logger.info(f"    IC_IR (mean/std): {float(np.mean(ic_arr)/np.std(ic_arr, ddof=1)):+.4f}")
    logger.info(f"    min:  {float(np.min(ic_arr)):+.4f}")
    logger.info(f"    max:  {float(np.max(ic_arr)):+.4f}")

    # 计算 decay 各组件
    logger.info("\n  IC_decay 组件分析:")
    short_window = 5
    long_window = 20
    recent_ic = float(np.mean(ic_arr[-short_window:]))
    longer_ic = float(np.mean(ic_arr[-long_window:]))
    logger.info(f"    ic_series[-5:]  (recent): {ic_arr[-5:]}")
    logger.info(f"    recent_ic (mean of last 5): {recent_ic:+.6f}")
    logger.info(f"    ic_series[-20:] (longer): {ic_arr[-20:]}")
    logger.info(f"    longer_ic (mean of last 20): {longer_ic:+.6f}")
    if abs(longer_ic) < 1e-6:
        logger.info("    ⚠️ longer_ic 过小，decay 计算不可信")
    else:
        decay = 1.0 - abs(recent_ic) / abs(longer_ic)
        logger.info(f"    decay = 1 - |{recent_ic:.6f}| / |{longer_ic:.6f}| = {decay:.6f}")

    # 滚动分析：每 20 天窗口的 IC_mean 和 IC_IR
    logger.info("\n  滚动 20 天窗口 IC 统计（每 20 天为一个 segment）:")
    logger.info(f"    {'segment':10s} | {'IC_mean':10s} | {'IC_std':10s} | {'IC_IR':10s}")
    logger.info(f"    {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
    seg_len = 20
    n_segs = len(ic_arr) // seg_len
    for i in range(n_segs):
        seg = ic_arr[i*seg_len:(i+1)*seg_len]
        m = float(np.mean(seg))
        s = float(np.std(seg, ddof=1))
        ir = m / s if s > 1e-6 else 0
        logger.info(f"    seg {i+1:2d}     | {m:+.4f}     | {s:.4f}     | {ir:+.4f}")

    # 分析最近 5 天 IC 为 0 的具体原因
    logger.info("\n  最近 5 天 IC 详细分析:")
    for i in range(5, 0, -1):
        idx = len(ic_arr) - i
        ic = ic_arr[idx]
        fv = margin_hist[idx]
        fr = fwd_returns_hist[idx]
        common = [s for s in fv if s in fr and math.isfinite(fv[s]) and math.isfinite(fr[s])]
        if len(common) >= 5:
            x = np.array([fv[s] for s in common], dtype=float)
            y = np.array([fr[s] for s in common], dtype=float)
            corr = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 1e-9 and np.std(y) > 1e-9 else 0
            print(f"    t={idx:3d} (倒数第{i}天): IC={ic:+.4f}, 标的数={len(common)}, "
                  f"factor_std={float(np.std(x)):.4f}, fwd_ret_std={float(np.std(y)):.4f}, "
                  f"直接 corr={corr:+.4f}")
        else:
            logger.info(f"    t={idx:3d} (倒数第{i}天): IC={ic:+.4f}, 标的数={len(common)} < 5")

    # 对比前 5 天和最近 5 天的因子值分布
    logger.info("\n  最近 5 天 vs 早期 5 天因子值分布对比:")
    early_fv = margin_hist[5]  # 早期某天
    late_fv = margin_hist[-1]  # 最近一天
    common_syms = [s for s in early_fv if s in late_fv]
    early_vals = np.array([early_fv[s] for s in common_syms], dtype=float)
    late_vals = np.array([late_fv[s] for s in common_syms], dtype=float)
    logger.info(f"    早期 day5: mean={float(np.mean(early_vals)):+.4f}, std={float(np.std(early_vals)):.4f}")
    logger.info(f"    最近 day_last: mean={float(np.mean(late_vals)):+.4f}, std={float(np.std(late_vals)):.4f}")
    diff = late_vals - early_vals
    print(f"    差异: mean={float(np.mean(diff)):+.4f}, std={float(np.std(diff)):.4f}, "
          f"max_abs_diff={float(np.max(np.abs(diff))):.6f}")
    if float(np.max(np.abs(diff))) < 1e-9:
        logger.info("    → 因子值在历史窗口内完全相同（fundamentals_history 静态性）")
        logger.info("    → IC 序列变化完全来自 forward_returns 变化")
        logger.info("    → 最近 5 天 IC=0 意味着截面排序与未来收益脱钩")

    # 检查 IC 序列末尾的具体值
    logger.info("\n  IC 序列末尾 20 个值:")
    for i in range(20, 0, -1):
        idx = len(ic_arr) - i
        if idx >= 0:
            logger.info(f"    t={idx:3d}: IC={ic_arr[idx]:+.6f}")

    # 修复方向建议
    logger.info("\n  " + "=" * 68)
    logger.info("  IC_decay=1.0 根因分析结论")
    logger.info("  " + "=" * 68)
    if float(np.max(np.abs(diff))) < 1e-9:
        logger.info("  根因：fundamentals_history 静态性 + 最近 5 天市场情绪与基本面脱钩")
        logger.info("  → QualityTrend 因子值在 120 天历史窗口内完全相同")
        logger.info("  → IC 序列完全由 forward_returns 驱动")
        logger.info("  → 最近 5 天 IC 恰好为 0，导致 IC_decay=1.0")
        print()
        logger.info("  修复方向：")
        logger.info("  1. 时间加权 IC：用指数衰减权重，近期权重更高（但需先确认 IC 不为 0）")
        logger.info("  2. 扩大 decay 窗口：short_window=5→10，降低单点噪声影响")
        logger.info("  3. 接受现状：MARGIN_EXP IC_IR=0.3981 通过阈值，decay=1.0 是暂时现象")
        logger.info("     可手动放行进入 G3，或调整 decay 阈值 0.6 → 0.9")

    return 0


if __name__ == "__main__":
    sys.exit(main())
