# -*- coding: utf-8 -*-
"""P2.2 v6 修复验证脚本（build_factor_history fundamentals_history 传递 bug）

问题描述：
    v5 报告中所有 QualityTrend 因子的 IC_IR 都用 legacy_single_period 方法估算，
    即 ic_ir = abs(ic) / (1 - abs(ic))，并非真实日频 IC_IR。
    根因：build_factor_history 调用 adapter.compute_candidate_factors 时
    未传递 fundamentals_history 参数，导致 QualityTrend 因子日频历史为空，
    触发 _gate2_ic_stability 降级到 legacy 实现。

验证目标：
    1. 调用 build_factor_history 时传入 fundamentals_history
    2. 检查 QualityTrend 因子的 factor_history 不为空
    3. 检查 IC_IR 用 compute_ic_ir 真实计算（非 legacy）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols, load_price_data, load_fundamentals,
    load_benchmark_returns, compute_equal_weight_benchmark,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history, compute_rolling_ic_series, compute_ic_ir,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
)

logger = logging.getLogger("verify_v6_fix")
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
)


def main() -> int:
    logger.info("=" * 70)
    logger.info("P2.2 v6 修复验证：build_factor_history fundamentals_history 传递")
    logger.info("=" * 70)

    # 1. 加载数据
    logger.info("\n[1/4] 加载数据")
    symbols = list_available_symbols()
    logger.info(f"  symbols: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns() or compute_equal_weight_benchmark(price_data)
    logger.info(f"  price_data: {len(price_data)} symbols")

    # 2. 加载 fundamentals_history（与 PipelineOrchestrator 一致）
    logger.info("\n[2/4] 加载 fundamentals_history")
    orchestrator = PipelineOrchestrator(config={})
    fundamentals_history = orchestrator._load_fundamentals_history(
        list(price_data.keys()) if price_data else []
    )
    logger.info(f"  fundamentals_history: {len(fundamentals_history)} symbols")

    # 3. 调用 build_factor_history（v6 修复后）
    logger.info("\n[3/4] 调用 build_factor_history (v6 修复：传 fundamentals_history)")
    adapter = VibeTradingFactorAdapter()
    factor_history, fwd_returns_hist, valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,  # v6 修复
    )
    logger.info(f"  factor_history: {len(factor_history)} factors")
    logger.info(f"  fwd_returns_hist: {len(fwd_returns_hist)} days")
    logger.info(f"  valid_dates: {len(valid_dates)} days")

    # 4. 检查 QualityTrend 因子的 factor_history
    logger.info("\n[4/4] 检查 QualityTrend 因子 factor_history")
    qt_factors = [
        "VT_QUALTREND_ROE_DELTA",
        "VT_QUALTREND_MARGIN_EXP",
        "VT_QUALTREND_DEBT_RED",
        "VT_QUALTREND_GROWTH_ACCEL",
    ]
    logger.info(f"  {'因子':32s} | history_len | 非空天数 | IC_mean | IC_std | 真实 IC_IR")
    logger.info(f"  {'-'*32}-+-{'-'*11}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}")
    all_pass = True
    for fname in qt_factors:
        hist = factor_history.get(fname, [])
        if not hist:
            logger.info(f"  {fname:32s} | ❌ 空 | - | - | - | -")
            all_pass = False
            continue
        # 计算真实 IC_IR
        ic_series = compute_rolling_ic_series(hist, fwd_returns_hist)
        ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series, min_periods=20)
        # 统计非空天数
        non_empty = sum(1 for fv in hist if fv)
        print(f"  {fname:32s} | {len(hist):11d} | {non_empty:8d} | "
              f"{ic_mean:+.4f} | {ic_std:.4f} | {ic_ir:+.4f}")

    print()
    if all_pass:
        logger.info("✅ 修复生效：所有 QualityTrend 因子 factor_history 不为空")
        logger.info("✅ 现在可以使用真实日频 IC_IR 重新评估 v5 决策")
    else:
        logger.info("❌ 修复未生效，部分因子 factor_history 仍为空")

    # 对比 v5 legacy IC_IR vs v6 真实 IC_IR
    logger.info("\n" + "=" * 70)
    logger.info("v5 legacy 伪 IC_IR vs v6 真实日频 IC_IR 对比")
    logger.info("=" * 70)
    v5_legacy = {
        "VT_QUALTREND_ROE_DELTA": 0.1811,
        "VT_QUALTREND_MARGIN_EXP": 0.2742,
        "VT_QUALTREND_DEBT_RED": 0.0839,
        "VT_QUALTREND_GROWTH_ACCEL": 0.2676,
    }
    logger.info(f"  {'因子':32s} | v5_legacy | v6_real | 差异")
    logger.info(f"  {'-'*32}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}")
    for fname in qt_factors:
        hist = factor_history.get(fname, [])
        if not hist:
            continue
        ic_series = compute_rolling_ic_series(hist, fwd_returns_hist)
        v6_ic_ir, _, _ = compute_ic_ir(ic_series, min_periods=20)
        v5_ic_ir = v5_legacy[fname]
        diff = v6_ic_ir - v5_ic_ir
        sign = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
        logger.info(f"  {fname:32s} | {v5_ic_ir:+.4f}   | {v6_ic_ir:+.4f}  | {diff:+.4f} {sign}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
