# -*- coding: utf-8 -*-
"""P2.2 v6a 实验：验证 MARGIN_EXP winsorize 是否真的有效

v5 决策依据：
    "winsorize 对小量级变化因子（ROE_DELTA/MARGIN_EXP）有效：极端值是噪声，
     裁剪后 IC_IR 提升"
    MARGIN_EXP v1 IC_IR=0.1270 → v5 winsorize IC_IR=0.2742 (+116%)

但 v5 IC_IR 都是 legacy 单期伪估算，结论不可信！v6 修复后用真实日频 IC_IR 重新验证：

实验设计：
    对比 MARGIN_EXP 两个版本的真实日频 IC_IR：
    - v6a_no_winsorize: gm[q] - gm[q-4]（不做 winsorize）
    - v6a_winsorize:    winsorize(gm[q] - gm[q-4], p5/p95)（保留 winsorize）

如果两者 IC_IR 接近 → winsorize 无效，可以去掉
如果 winsorize 版本 IC_IR 显著更高 → v5 决策正确（虽然基于伪 IC_IR 但结论成立）
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from typing import Dict, Any, List

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols, load_price_data, load_fundamentals,
    load_benchmark_returns, compute_equal_weight_benchmark,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter, CandidateFactor,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history, compute_rolling_ic_series, compute_ic_ir,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
)

logger = logging.getLogger("test_v6a")
logging.basicConfig(level=logging.WARNING)


def _compute_margin_exp_values(
    fundamentals_history: Dict[str, Dict[str, Any]],
    use_winsorize: bool,
) -> Dict[str, float]:
    """计算 MARGIN_EXP 因子值

    Args:
        fundamentals_history: 历史季度财务数据
        use_winsorize: True=winsorize P5/P95, False=不处理

    Returns:
        {symbol: factor_value}
    """
    deltas: List[float] = []
    sym_list: List[str] = []
    for sym, hist in fundamentals_history.items():
        if not isinstance(hist, dict):
            continue
        quarters = hist.get("quarters", [])
        if len(quarters) < 5:
            continue
        cur_gm = float(quarters[0].get("gross_margin", 0))
        prev_gm = float(quarters[4].get("gross_margin", 0))
        if cur_gm != 0 and prev_gm != 0:
            deltas.append(cur_gm - prev_gm)
            sym_list.append(sym)

    values: Dict[str, float] = {}
    if len(deltas) < 5:
        return values
    if use_winsorize:
        arr = np.array(deltas, dtype=float)
        p_lo, p_hi = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
        for sym, delta in zip(sym_list, deltas):
            values[sym] = float(max(p_lo, min(p_hi, delta)))
    else:
        for sym, delta in zip(sym_list, deltas):
            values[sym] = float(delta)
    return values


def _build_factor_history_static(
    factor_values: Dict[str, float],
    valid_dates: List[int],
    price_data: Dict[str, Dict[str, List[float]]],
    forward_window: int = 5,
) -> List[Dict[str, float]]:
    """基于静态 factor_values 构建日频因子历史

    QualityTrend 因子值在历史窗口内是固定的（fundamentals_history 不变），
    所以这里直接复制 factor_values 到每一天。
    """
    factor_history: List[Dict[str, float]] = []
    for t in valid_dates:
        # 每天的因子值都是相同的（基于静态 fundamentals_history）
        factor_history.append(dict(factor_values))
    return factor_history


def _build_forward_returns(
    price_data: Dict[str, Dict[str, List[float]]],
    valid_dates: List[int],
    forward_window: int = 5,
) -> List[Dict[str, float]]:
    """构建日频 forward returns"""
    fwd_returns: List[Dict[str, float]] = []
    for t in valid_dates:
        fwd_dict: Dict[str, float] = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if t + forward_window < len(closes) and closes[t] > 0:
                fwd_ret = float(closes[t + forward_window] / closes[t] - 1)
                if math.isfinite(fwd_ret):
                    fwd_dict[sym] = fwd_ret
        fwd_returns.append(fwd_dict)
    return fwd_returns


def main() -> int:
    print("=" * 70)
    print("P2.2 v6a 实验：MARGIN_EXP winsorize 真实有效性验证")
    print("=" * 70)

    # 1. 加载数据
    print("\n[1/3] 加载数据")
    symbols = list_available_symbols()
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns() or compute_equal_weight_benchmark(price_data)
    orchestrator = PipelineOrchestrator(config={})
    fundamentals_history = orchestrator._load_fundamentals_history(
        list(price_data.keys()) if price_data else []
    )
    print(f"  price_data: {len(price_data)} symbols")
    print(f"  fundamentals_history: {len(fundamentals_history)} symbols")

    # 2. 用 build_factor_history 获取 valid_dates 和 forward_returns
    print("\n[2/3] 构建日频历史（复用 build_factor_history）")
    adapter = VibeTradingFactorAdapter()
    # 这里调用主要是为了获取 valid_dates 和 forward_returns
    # 因为我们修正后的 build_factor_history 会自动计算所有因子
    factor_history_full, fwd_returns_hist, valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,
    )
    print(f"  valid_dates: {len(valid_dates)} days")
    print(f"  fwd_returns_hist: {len(fwd_returns_hist)} days")

    # 3. 计算两个版本的 MARGIN_EXP 因子值
    print("\n[3/3] 对比 MARGIN_EXP winsorize vs no_winsorize 真实日频 IC_IR")
    values_winsorize = _compute_margin_exp_values(fundamentals_history, use_winsorize=True)
    values_no_winsorize = _compute_margin_exp_values(fundamentals_history, use_winsorize=False)
    print(f"  winsorize 版本: {len(values_winsorize)} symbols")
    print(f"  no_winsorize 版本: {len(values_no_winsorize)} symbols")

    # 用相同的 valid_dates 构建 factor_history（静态复制）
    hist_winsorize = _build_factor_history_static(
        values_winsorize, valid_dates, price_data
    )
    hist_no_winsorize = _build_factor_history_static(
        values_no_winsorize, valid_dates, price_data
    )

    # 计算真实日频 IC_IR
    ic_series_win = compute_rolling_ic_series(hist_winsorize, fwd_returns_hist)
    ic_ir_win, ic_mean_win, ic_std_win = compute_ic_ir(ic_series_win, min_periods=20)

    ic_series_no = compute_rolling_ic_series(hist_no_winsorize, fwd_returns_hist)
    ic_ir_no, ic_mean_no, ic_std_no = compute_ic_ir(ic_series_no, min_periods=20)

    # 同时报告 pipeline 内置的 MARGIN_EXP IC_IR（v5 winsorize 版本）
    pipeline_ic_ir = 0.0
    pipeline_ic_mean = 0.0
    if "VT_QUALTREND_MARGIN_EXP" in factor_history_full:
        hist_pipeline = factor_history_full["VT_QUALTREND_MARGIN_EXP"]
        ic_series_pl = compute_rolling_ic_series(hist_pipeline, fwd_returns_hist)
        pipeline_ic_ir, pipeline_ic_mean, _ = compute_ic_ir(ic_series_pl, min_periods=20)

    # 输出对比结果
    print()
    print("=" * 70)
    print("MARGIN_EXP winsorize vs no_winsorize 真实日频 IC_IR 对比")
    print("=" * 70)
    print(f"  {'版本':25s} | IC_mean  | IC_std  | IC_IR   | vs 0.3")
    print(f"  {'-'*25}-+-{'-'*8}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}")
    print(f"  {'pipeline内置(winsorize)':25s} | {pipeline_ic_mean:+.4f}  | "
          f"{(abs(pipeline_ic_mean)/max(pipeline_ic_ir,0.001)):.4f}  | "
          f"{pipeline_ic_ir:+.4f} | {'✅' if abs(pipeline_ic_ir) >= 0.3 else '❌'}")
    print(f"  {'独立实现(winsorize)':25s} | {ic_mean_win:+.4f}  | {ic_std_win:.4f}  | "
          f"{ic_ir_win:+.4f} | {'✅' if abs(ic_ir_win) >= 0.3 else '❌'}")
    print(f"  {'独立实现(no_winsorize)':25s} | {ic_mean_no:+.4f}  | {ic_std_no:.4f}  | "
          f"{ic_ir_no:+.4f} | {'✅' if abs(ic_ir_no) >= 0.3 else '❌'}")
    print()
    diff = ic_ir_win - ic_ir_no
    print(f"  winsorize vs no_winsorize IC_IR 差异: {diff:+.4f}")
    if abs(diff) < 0.02:
        print("  → 两者 IC_IR 接近，winsorize 对 MARGIN_EXP 无显著影响")
        print("  → v5 关于 winsorize 提升 IC_IR +116% 的结论是伪 IC_IR 导致的假象")
        print("  → 可以简化公式，去掉 winsorize")
    elif diff > 0:
        print(f"  → winsorize 版本 IC_IR 更高 (+{diff:.4f})，winsorize 有效")
        print("  → v5 决策虽然基于伪 IC_IR，但结论方向正确")
    else:
        print(f"  → no_winsorize 版本 IC_IR 更高 ({diff:+.4f})，winsorize 有害")
        print("  → v5 决策方向错误，应该去掉 winsorize")

    return 0


if __name__ == "__main__":
    sys.exit(main())
