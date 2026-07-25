# -*- coding: utf-8 -*-
"""factor_history_builder 自检脚本"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history, compute_rolling_ic_series, compute_ic_ir, compute_ic_decay,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    load_all_for_pipeline, list_available_symbols,
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s | %(message)s")
    print("=" * 70)
    print("FactorHistoryBuilder 自检")
    print("=" * 70)

    # 加载真实数据
    symbols = list_available_symbols()
    price_data, fundamentals, benchmark_returns = load_all_for_pipeline(symbols=symbols)
    print(f"数据加载: {len(price_data)} 标的, {len(benchmark_returns)} 天基准")

    # 构建日频因子历史
    adapter = VibeTradingFactorAdapter()
    print("\n[1] 构建日频因子历史（120d + 5d forward）...")
    factor_history, fwd_returns, valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
    )
    print(f"  因子数: {len(factor_history)}")
    print(f"  序列长度: {len(fwd_returns)}")
    print(f"  有效天数: {len(valid_dates)}")

    if not factor_history:
        print("[ERROR] 未构建出任何因子历史")
        return 1

    # 验证每个因子的 IC_IR
    print("\n[2] 计算每个因子的 IC_IR (120d 真实滚动)")
    print("-" * 80)
    print(f"  {'Factor':35s} | {'IC_mean':>9s} | {'IC_std':>8s} | {'IC_IR':>8s} | {'decay':>8s}")
    print(f"  {'-'*35}-+-{'-'*9}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

    results = []
    for fname, hist in factor_history.items():
        if len(hist) < 20:
            print(f"  {fname:35s} | 样本不足: {len(hist)}")
            continue
        ic_series = compute_rolling_ic_series(hist, fwd_returns)
        ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series, min_periods=20)
        decay = compute_ic_decay(hist, fwd_returns)
        print(f"  {fname:35s} | {ic_mean:+.4f} | {ic_std:.4f} | {ic_ir:+.4f} | {decay:+.4f}")
        results.append({
            "factor": fname,
            "ic_ir": ic_ir,
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "decay": decay,
            "n_days": len(hist),
        })

    # 排序展示
    print(f"\n[3] 按 IC_IR 排序（找出最值得通过的因子）")
    print("-" * 80)
    results_sorted = sorted(results, key=lambda r: r["ic_ir"], reverse=True)
    for i, r in enumerate(results_sorted[:10], 1):
        gate2_status = "✓ 通过" if abs(r["ic_ir"]) >= 0.3 else "✗ 未达"
        print(f"  {i:2d}. {r['factor']:35s} | IC_IR={r['ic_ir']:+.4f} | decay={r['decay']:+.4f} | {gate2_status}")

    print("\n" + "=" * 70)
    print("FactorHistoryBuilder 自检完成")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
