# -*- coding: utf-8 -*-
"""S3 第十六批次：GROWTH_ACCEL 重新设计验证（P2.2 v6.4 改用扣非净利润）

设计背景：
    v5 GROWTH_ACCEL 用净利润绝对值计算 YoY 增长率加速，v6 真实 IC_IR=0.0159（失效）。
    v3 尝试用 yoy_pni（扣非净利同比%）的 QoQ 变化，IC_IR=0.0066（更差）。

v6.4 重新设计思路：
    1. yoy_pni 是 baostock 提供的扣非净利同比 (%)，直接使用其水平值作为因子
       - 经济含义：扣非净利增长率高的公司 → 主业增长强 → 看涨
       - 风险：可能与现有 Growth 类因子共线

    2. yoy_pni 的 YoY 变化（而非 QoQ 变化）
       - 公式：yoy_pni[q] - yoy_pni[q-4]
       - 经济含义：扣非净利增长率加速 → 二阶导为正 → 看涨
       - 优势：YoY 变化消除季节性（vs v3 的 QoQ 变化受季节性影响）

    3. yoy_ni 的 YoY 变化（对比扣非净利）
       - 公式：yoy_ni[q] - yoy_ni[q-4]
       - 经济含义：净利润增长率加速
       - 用途：对比扣非净利 vs 净利润的 Alpha 信号强弱

    4. v5 复现（baseline）：净利润绝对值 YoY 增长率加速
       - 公式：(np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1)
       - 已知 IC_IR=0.0159（v6 真实值）

    5. v3 复现（baseline）：yoy_pni 的 QoQ 变化
       - 公式：yoy_pni[q] - yoy_pni[q-1]
       - 已知 IC_IR=0.0066

验证方法：
    用 build_factor_history 获取日频 forward returns（120 天），
    从 fundamentals_history 构建静态因子值，计算 IC_IR。
    注：此方法假设 fundamentals 在 120 天窗口内不变（与 build_factor_history 简化一致）。

v6.4 验证目标：
    - 找到 IC_IR >= 0.3 的 GROWTH_ACCEL 方案
    - 若找到，将其作为 v6.4 候选方案，进入完整 8 级流水线验证
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols,
    load_price_data, load_fundamentals, load_benchmark_returns,
    compute_equal_weight_benchmark,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history, compute_ic_ir,
)

logger = logging.getLogger("run_sixteenth_batch_growth_accel")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def compute_ic_series_for_static_factor(
    factor_values: Dict[str, float],
    forward_returns_history: List[Dict[str, float]],
    min_samples: int = 5,
) -> List[float]:
    """计算静态因子值与每日 forward returns 的 IC 序列

    Args:
        factor_values: 静态因子值 {symbol: value}
        forward_returns_history: 日频 forward returns
        min_samples: 计算单日 IC 最少所需标的数

    Returns:
        ic_series: List[float]，每日的 Pearson IC
    """
    ic_series: List[float] = []
    for fr in forward_returns_history:
        common = [
            s for s in factor_values
            if s in fr and math.isfinite(factor_values[s]) and math.isfinite(fr[s])
        ]
        if len(common) < min_samples:
            ic_series.append(0.0)
            continue
        x = np.array([factor_values[s] for s in common], dtype=float)
        y = np.array([fr[s] for s in common], dtype=float)
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            ic_series.append(0.0)
            continue
        ic = float(np.corrcoef(x, y)[0, 1])
        ic_series.append(ic)
    return ic_series


def winsorize_values(values: Dict[str, float], p_lo: float = 5, p_hi: float = 95) -> Dict[str, float]:
    """Winsorize 处理极端值（裁剪到 [P5, P95]）"""
    valid = {s: v for s, v in values.items() if np.isfinite(v)}
    if len(valid) < 5:
        return values
    arr = np.array(list(valid.values()), dtype=float)
    lo, hi = float(np.percentile(arr, p_lo)), float(np.percentile(arr, p_hi))
    return {s: float(max(lo, min(hi, v))) for s, v in values.items()}


def compute_growth_accel_v5(quarters: List[Dict]) -> float:
    """v5 baseline: 净利润绝对值 YoY 增长率加速

    公式: (np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1)
    """
    if len(quarters) < 6:
        return float("nan")
    np_cur = float(quarters[0].get("net_profit", 0))
    np_prev_q = float(quarters[1].get("net_profit", 0))
    np_yoy_cur = float(quarters[4].get("net_profit", 0))
    np_yoy_prev = float(quarters[5].get("net_profit", 0))
    if np_cur == 0 or np_yoy_cur == 0 or np_prev_q == 0 or np_yoy_prev == 0:
        return float("nan")
    growth_cur = (np_cur - np_yoy_cur) / abs(np_yoy_cur)
    growth_prev = (np_prev_q - np_yoy_prev) / abs(np_yoy_prev)
    return growth_cur - growth_prev


def compute_growth_accel_v3(quarters: List[Dict]) -> float:
    """v3 baseline: yoy_pni 的 QoQ 变化

    公式: yoy_pni[q] - yoy_pni[q-1]
    """
    if len(quarters) < 2:
        return float("nan")
    cur = float(quarters[0].get("yoy_pni", 0))
    prev = float(quarters[1].get("yoy_pni", 0))
    if cur == 0 or prev == 0:
        return float("nan")
    return cur - prev


def compute_growth_accel_v6a(quarters: List[Dict]) -> float:
    """v6.1: yoy_pni 水平值（直接用扣非净利同比 %）

    公式: yoy_pni[q]
    经济含义: 扣非净利增长率高的公司 → 主业增长强 → 看涨
    """
    if len(quarters) < 1:
        return float("nan")
    return float(quarters[0].get("yoy_pni", 0))


def compute_growth_accel_v6b(quarters: List[Dict]) -> float:
    """v6.2: yoy_pni 的 YoY 变化（消除季节性）

    公式: yoy_pni[q] - yoy_pni[q-4]
    经济含义: 扣非净利增长率加速 → 二阶导为正 → 看涨
    """
    if len(quarters) < 5:
        return float("nan")
    cur = float(quarters[0].get("yoy_pni", 0))
    yoy_prev = float(quarters[4].get("yoy_pni", 0))
    if cur == 0 or yoy_prev == 0:
        return float("nan")
    return cur - yoy_prev


def compute_growth_accel_v6c(quarters: List[Dict]) -> float:
    """v6.3: yoy_ni 的 YoY 变化（对比扣非净利）

    公式: yoy_ni[q] - yoy_ni[q-4]
    经济含义: 净利润增长率加速 → 二阶导为正 → 看涨
    """
    if len(quarters) < 5:
        return float("nan")
    cur = float(quarters[0].get("yoy_ni", 0))
    yoy_prev = float(quarters[4].get("yoy_ni", 0))
    if cur == 0 or yoy_prev == 0:
        return float("nan")
    return cur - yoy_prev


def build_factor_values(
    fundamentals_history: Dict[str, Any],
    compute_fn,
) -> Dict[str, float]:
    """从 fundamentals_history 构建因子值

    Args:
        fundamentals_history: {symbol: {"quarters": [...], ...}}
        compute_fn: 计算函数 (quarters) -> float

    Returns:
        {symbol: factor_value}
    """
    values: Dict[str, float] = {}
    for sym, hist in fundamentals_history.items():
        if not isinstance(hist, dict):
            continue
        quarters = hist.get("quarters", [])
        if not quarters:
            continue
        try:
            val = compute_fn(quarters)
            if math.isfinite(val):
                values[sym] = val
        except Exception:
            continue
    return values


def evaluate_scheme(
    name: str,
    factor_values: Dict[str, float],
    forward_returns_history: List[Dict[str, float]],
    apply_winsorize: bool = False,
) -> Dict[str, Any]:
    """评估单个方案的 IC 指标

    Args:
        name: 方案名称
        factor_values: 静态因子值
        forward_returns_history: 日频 forward returns
        apply_winsorize: 是否 winsorize 处理

    Returns:
        dict 包含 ic_ir, ic_mean, ic_std, n_symbols
    """
    if apply_winsorize:
        factor_values = winsorize_values(factor_values)
    ic_series = compute_ic_series_for_static_factor(factor_values, forward_returns_history)
    ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series)
    print(f"  {name}:")
    print(f"    IC_IR={ic_ir:+.4f}  IC_mean={ic_mean:+.4f}  IC_std={ic_std:.4f}  "
          f"n_symbols={len(factor_values)}")
    return {
        "ic_ir": float(ic_ir),
        "ic_mean": float(ic_mean),
        "ic_std": float(ic_std),
        "n_symbols": len(factor_values),
        "apply_winsorize": apply_winsorize,
    }


def main() -> int:
    """主入口：GROWTH_ACCEL 重新设计验证"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    print("=" * 70)
    print("S3 第十六批次：GROWTH_ACCEL 重新设计验证（P2.2 v6.4 改用扣非净利润）")
    print("=" * 70)
    print("v6.4 验证方案:")
    print("  - v5 baseline: 净利润绝对值 YoY 增长率加速（已知 IC_IR=0.0159）")
    print("  - v3 baseline: yoy_pni QoQ 变化（已知 IC_IR=0.0066）")
    print("  - v6.1: yoy_pni 水平值（直接用扣非净利同比 %）")
    print("  - v6.2: yoy_pni YoY 变化（消除季节性）")
    print("  - v6.3: yoy_ni YoY 变化（对比扣非净利）")
    print("  - 每个方案测试 winsorize 前后效果")

    # ============ Step 1: 加载数据 ============
    print("\n[1/4] 加载数据")
    symbols = list_available_symbols()
    print(f"  可用标的数: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    print(f"  price_data: {len(price_data)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 加载 fundamentals_history ============
    print("\n[2/4] 加载历史季度财务数据")
    cache_dir = _PROJECT_ROOT / "cache" / "fundamentals"
    fundamentals_history: Dict[str, Any] = {}
    for sym in symbols:
        cache_path = cache_dir / f"{sym}_history.json"
        if not cache_path.exists():
            continue
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("n_valid", 0) >= 4:
                fundamentals_history[sym] = data
        except Exception as e:
            logger.debug("[GrowthAccelLoader] 加载 %s 失败: %s", sym, e)
    print(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建日频因子历史（获取 forward_returns） ============
    print("\n[3/4] 构建日频因子历史（获取 forward_returns，120 天）")
    adapter = VibeTradingFactorAdapter()
    factor_history, fwd_returns_hist, valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,
    )
    print(f"  valid_dates: {len(valid_dates)} 天")
    print(f"  forward_returns: {len(fwd_returns_hist)} 天")

    # ============ Step 4: 评估各方案 IC_IR ============
    print("\n[4/4] 评估各方案 IC_IR")
    print("-" * 70)

    results: Dict[str, Any] = {}

    # v5 baseline: 净利润绝对值 YoY 增长率加速
    v5_values = build_factor_values(fundamentals_history, compute_growth_accel_v5)
    results["v5_baseline"] = evaluate_scheme("v5 baseline (净利润 YoY 加速)", v5_values, fwd_returns_hist)

    # v3 baseline: yoy_pni QoQ 变化
    v3_values = build_factor_values(fundamentals_history, compute_growth_accel_v3)
    results["v3_baseline"] = evaluate_scheme("v3 baseline (yoy_pni QoQ 变化)", v3_values, fwd_returns_hist)

    # v6.1: yoy_pni 水平值
    v6a_values = build_factor_values(fundamentals_history, compute_growth_accel_v6a)
    results["v6_1_yoy_pni_level"] = evaluate_scheme("v6.1 (yoy_pni 水平值)", v6a_values, fwd_returns_hist)
    results["v6_1_yoy_pni_level_winsorize"] = evaluate_scheme(
        "v6.1+winsorize (yoy_pni 水平值)", v6a_values, fwd_returns_hist, apply_winsorize=True
    )

    # v6.2: yoy_pni YoY 变化
    v6b_values = build_factor_values(fundamentals_history, compute_growth_accel_v6b)
    results["v6_2_yoy_pni_yoy_delta"] = evaluate_scheme("v6.2 (yoy_pni YoY 变化)", v6b_values, fwd_returns_hist)
    results["v6_2_yoy_pni_yoy_delta_winsorize"] = evaluate_scheme(
        "v6.2+winsorize (yoy_pni YoY 变化)", v6b_values, fwd_returns_hist, apply_winsorize=True
    )

    # v6.3: yoy_ni YoY 变化
    v6c_values = build_factor_values(fundamentals_history, compute_growth_accel_v6c)
    results["v6_3_yoy_ni_yoy_delta"] = evaluate_scheme("v6.3 (yoy_ni YoY 变化)", v6c_values, fwd_returns_hist)
    results["v6_3_yoy_ni_yoy_delta_winsorize"] = evaluate_scheme(
        "v6.3+winsorize (yoy_ni YoY 变化)", v6c_values, fwd_returns_hist, apply_winsorize=True
    )

    # ============ 汇总对比 ============
    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"\n{'方案':<45s} {'IC_IR':>8s} {'IC_mean':>10s} {'IC_std':>8s} {'n_symbols':>10s}")
    print("-" * 85)
    for name, metrics in results.items():
        winsorize_tag = " (winsorize)" if metrics["apply_winsorize"] else ""
        print(f"{name + winsorize_tag:<45s} {metrics['ic_ir']:>+8.4f} "
              f"{metrics['ic_mean']:>+10.4f} {metrics['ic_std']:>8.4f} "
              f"{metrics['n_symbols']:>10d}")

    # ============ 找出最优方案 ============
    print("\n[最优方案评估]")
    best_name = max(results.keys(), key=lambda k: abs(results[k]["ic_ir"]))
    best_ic_ir = results[best_name]["ic_ir"]
    threshold = 0.3

    print(f"  最优方案: {best_name}")
    print(f"  IC_IR: {best_ic_ir:+.4f}")
    print(f"  0.3 阈值: {'✅ 达标' if abs(best_ic_ir) >= threshold else '❌ 未达标'}")

    if abs(best_ic_ir) >= threshold:
        print(f"\n  🎉 推荐方案 {best_name} 进入完整 8 级流水线验证")
        if best_ic_ir < 0:
            print(f"  ⚠️ IC_IR 为负值，考虑反向使用（如 VT_*_INV 模式）")
    else:
        print(f"\n  ⚠️ 所有方案 IC_IR < 0.3，GROWTH_ACCEL 类因子 Alpha 信号不足")
        print(f"  建议：")
        print(f"    1. 重新审视 GROWTH_ACCEL 的因子设计（可能需要新数据源）")
        print(f"    2. 考虑其他质量变化维度（如分析师预期变化、研报情感）")
        print(f"    3. 将 GROWTH_ACCEL 标记为 defer，优先推进其他因子")

    # ============ 保存结果 ============
    batch_id = f"sixteenth_batch_growth_accel_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "background": "GROWTH_ACCEL v5 真实 IC_IR=0.0159（v6 修复后），远低于 0.3 阈值",
        "schemes": results,
        "best_scheme": best_name,
        "best_ic_ir": best_ic_ir,
        "threshold": threshold,
        "recommendation": (
            f"推荐方案 {best_name} 进入完整 8 级流水线验证"
            if abs(best_ic_ir) >= threshold
            else "所有方案未达标，GROWTH_ACCEL Alpha 信号不足"
        ),
    }

    output_path = output_dir / "growth_accel_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n结果已保存至: {output_path}")
    print(f"批次 ID: {batch_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
