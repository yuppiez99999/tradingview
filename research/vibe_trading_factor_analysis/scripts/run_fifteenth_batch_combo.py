# -*- coding: utf-8 -*-
"""S3 第十五批次：MARGIN_EXP + VT_MICRO_VOL_SKEW_INV 组合验证（P2.2 v6.3）

设计背景：
    截至第十四批次，已有两个 approved 因子：
        1. VT_MICRO_VOL_SKEW_INV（微观结构类，第八批次 approved）
           - IC_IR=0.418, DSR=1.87, Shadow max_dd=10.57% (Config_A)
        2. VT_QUALTREND_MARGIN_EXP（质量变化类，第十四批次 approved）
           - IC_IR=0.3981, DSR=0.5304, Shadow max_dd=7.59% (Config_E override)

    两个因子分属不同维度（微观结构 vs 基本面质量变化），理论上应具有低相关性，
    组合后预期能：
        - 提升 IC_IR（信号叠加增强）
        - 降低 max_dd（不同维度的回撤期不完全重叠）
        - 提升 Shadow live_dsr（更稳健的 Alpha）

v6.3 组合验证方案：
    1. 因子组合方法：cross-sectional rank 标准化 + 等权相加
       - rank 标准化解决量纲差异（VT_MICRO_VOL_SKEW_INV 是偏度，MARGIN_EXP 是毛利率变化）
       - 等权相加作为基线方案，未来可扩展为 IC 加权或 IR 加权
    2. 对比方案：单因子 A vs 单因子 B vs 组合 (A+B)
    3. 风险管理：使用 Config_A 基线（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5）
       - 因为组合信号可能比单因子更稳定，先用基线参数测试
       - 若组合 max_dd > 12%，再尝试 Config_E 超激进参数

v6.3 验证目标：
    - 组合 IC_IR 是否优于任一单因子（IC_IR > max(0.418, 0.3981)）
    - 组合 Shadow max_dd 是否低于任一单因子（max_dd < min(10.57%, 7.59%)）
    - 组合 live_dsr 是否优于任一单因子
    - 验证"不同维度因子组合可降低回撤"假设
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

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
    build_factor_history, compute_rolling_ic_series, compute_ic_ir, compute_ic_decay,
)
from research.vibe_trading_factor_analysis.shadow.shadow_account import ShadowAccount

logger = logging.getLogger("run_fifteenth_batch_combo")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 验证的因子
FACTOR_A = "VT_MICRO_VOL_SKEW_INV"          # 微观结构类（第八批次 approved）
FACTOR_B = "VT_QUALTREND_MARGIN_EXP"        # 质量变化类（第十四批次 approved）


def cross_sectional_rank(values: Dict[str, float]) -> Dict[str, float]:
    """cross-sectional rank 标准化到 [0, 1]

    将原始因子值按大小排序，转换为 [0, 1] 区间的 rank。
    解决两个因子量纲差异问题（偏度 vs 毛利率变化），保留单调性。

    Args:
        values: {symbol: factor_value}

    Returns:
        {symbol: rank_normalized} rank_normalized ∈ [0, 1]
    """
    valid = {s: v for s, v in values.items() if np.isfinite(v)}
    if len(valid) < 2:
        return {s: 0.5 for s in values}
    sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
    n = len(sorted_syms)
    # rank 标准化到 [0, 1]：最小值→0，最大值→1
    ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
    return {s: ranks.get(s, 0.5) for s in values}


def combine_factors_equal_weight(
    factor_history_a: List[Dict[str, float]],
    factor_history_b: List[Dict[str, float]],
    weight_a: float = 0.5,
) -> List[Dict[str, float]]:
    """等权组合两个因子（先 rank 标准化再加权相加）

    组合方法：
        combined[symbol] = weight_a * rank(f_A[symbol]) + (1-weight_a) * rank(f_B[symbol])

    Args:
        factor_history_a: 因子 A 的日频值序列
        factor_history_b: 因子 B 的日频值序列
        weight_a: 因子 A 的权重（默认 0.5 等权）

    Returns:
        combined_history: 组合信号的日频值序列
    """
    weight_b = 1.0 - weight_a
    n = min(len(factor_history_a), len(factor_history_b))
    combined = []
    for i in range(n):
        rank_a = cross_sectional_rank(factor_history_a[i])
        rank_b = cross_sectional_rank(factor_history_b[i])
        # 取两个 rank 序列的公共 symbols
        common_syms = set(rank_a.keys()) & set(rank_b.keys())
        combined_day = {
            s: weight_a * rank_a[s] + weight_b * rank_b[s]
            for s in common_syms
        }
        combined.append(combined_day)
    return combined


def compute_all_ic_metrics(
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    name: str,
) -> Dict[str, Any]:
    """计算单因子的完整 IC 指标：IC_IR, IC_mean, IC_std, IC_decay

    Args:
        factor_history: 日频因子值序列
        forward_returns_history: 日频 forward return
        name: 因子名称

    Returns:
        dict 包含 ic_ir, ic_mean, ic_std, ic_decay
    """
    ic_series = compute_rolling_ic_series(factor_history, forward_returns_history)
    ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series)
    decay = compute_ic_decay(factor_history, forward_returns_history)
    print(f"  {name}:")
    print(f"    IC_IR={ic_ir:+.4f}  IC_mean={ic_mean:+.4f}  IC_std={ic_std:.4f}  decay={decay:.4f}")
    return {
        "ic_ir": float(ic_ir),
        "ic_mean": float(ic_mean),
        "ic_std": float(ic_std),
        "ic_decay": float(decay),
    }


def run_shadow_test(
    shadow_account: ShadowAccount,
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    n_trials: int,
    name: str,
) -> Dict[str, Any]:
    """执行 Shadow 测试并返回关键字段

    Args:
        shadow_account: ShadowAccount 实例
        factor_history: 日频因子值序列
        forward_returns_history: 日频 forward return
        n_trials: 多重检验数
        name: 因子名称

    Returns:
        dict 包含 pass_shadow, live_dsr, max_drawdown, total_return, sr_observed, realized_vol
    """
    result = shadow_account.run_shadow(
        factor_values_history=factor_history,
        forward_returns_history=forward_returns_history,
        n_trials=n_trials,
        factor_name=name,
    )
    print(f"  {name}:")
    print(f"    pass_shadow={result.pass_shadow}  live_dsr={result.live_dsr:+.4f}  "
          f"max_dd={result.max_drawdown:.4f}")
    print(f"    total_return={result.total_return:+.4f}  sr_observed={result.sr_observed:.4f}  "
          f"realized_vol={result.realized_vol:.4f}")
    if result.fail_reasons:
        print(f"    fail_reasons: {result.fail_reasons}")
    return {
        "pass_shadow": bool(result.pass_shadow),
        "live_dsr": float(result.live_dsr),
        "max_drawdown": float(result.max_drawdown),
        "total_return": float(result.total_return),
        "sr_observed": float(result.sr_observed),
        "realized_vol": float(result.realized_vol),
        "mc_p95_dd": float(result.monte_carlo_p95_dd),
        "avg_scaler": float(result.avg_scaler),
        "derisk_triggered_days": int(result.derisk_triggered_days),
        "fail_reasons": list(result.fail_reasons),
    }


def load_fundamentals_history(symbols: List[str]) -> Dict[str, Any]:
    """从 cache/fundamentals/ 加载历史季度财务数据

    Args:
        symbols: 标的列表

    Returns:
        {symbol: {"quarters": [...], "n_valid": int, ...}}
    """
    cache_dir = _PROJECT_ROOT / "cache" / "fundamentals"
    history: Dict[str, Any] = {}
    for sym in symbols:
        cache_path = cache_dir / f"{sym}_history.json"
        if not cache_path.exists():
            continue
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("n_valid", 0) >= 4:
                history[sym] = data
        except Exception as e:
            logger.debug("[ComboLoader] 加载 %s 历史季度数据失败: %s", sym, e)
    return history


def main() -> int:
    """主入口：跑第十五批次组合验证"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    print("=" * 70)
    print("S3 第十五批次：MARGIN_EXP + VT_MICRO_VOL_SKEW_INV 组合验证（P2.2 v6.3）")
    print("=" * 70)
    print("v6.3 验证目标:")
    print(f"  - Factor A: {FACTOR_A}（微观结构类，第八批次 approved）")
    print(f"  - Factor B: {FACTOR_B}（质量变化类，第十四批次 approved）")
    print("  - 组合方法：cross-sectional rank 标准化 + 等权相加")
    print("  - 验证组合 IC_IR 和 Shadow 表现是否优于单因子")

    # ============ Step 1: 加载数据 ============
    print("\n[1/6] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    print(f"  可用标的数: {len(symbols)}")

    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    print(f"  price_data: {len(price_data)} | benchmark_returns: {len(benchmark_returns)} 天")

    if not price_data:
        print("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 加载 fundamentals_history ============
    print("\n[2/6] 加载历史季度财务数据（QualityTrend 类所需）")
    fundamentals_history = load_fundamentals_history(symbols)
    print(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建日频因子历史 ============
    print("\n[3/6] 构建日频因子历史（history_days=120, forward_window=5）")
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
    print(f"  factor_history 包含 {len(factor_history)} 个因子")
    print(f"  valid_dates: {len(valid_dates)} 天")

    # ============ Step 4: 提取单因子历史 + 构建组合信号 ============
    print("\n[4/6] 提取单因子历史并构建组合信号")
    if FACTOR_A not in factor_history:
        print(f"[ERROR] 因子 {FACTOR_A} 不在 factor_history 中")
        return 1
    if FACTOR_B not in factor_history:
        print(f"[ERROR] 因子 {FACTOR_B} 不在 factor_history 中")
        return 1

    hist_a = factor_history[FACTOR_A]
    hist_b = factor_history[FACTOR_B]

    # 对齐长度（取三者最小长度）
    n = min(len(hist_a), len(hist_b), len(fwd_returns_hist))
    hist_a = hist_a[:n]
    hist_b = hist_b[:n]
    fwd_returns_hist = fwd_returns_hist[:n]
    print(f"  Factor A ({FACTOR_A}): {len(hist_a)} 天")
    print(f"  Factor B ({FACTOR_B}): {len(hist_b)} 天")
    print(f"  forward_returns: {len(fwd_returns_hist)} 天")

    # 构建组合信号（等权）
    combined_hist = combine_factors_equal_weight(hist_a, hist_b, weight_a=0.5)
    print(f"  Combined (equal weight): {len(combined_hist)} 天")

    # ============ Step 5: 计算单因子和组合的 IC 指标 ============
    print("\n[5/6] IC 指标对比")
    metrics_a = compute_all_ic_metrics(hist_a, fwd_returns_hist, FACTOR_A)
    metrics_b = compute_all_ic_metrics(hist_b, fwd_returns_hist, FACTOR_B)
    metrics_combined = compute_all_ic_metrics(combined_hist, fwd_returns_hist, "COMBINED (A+B equal weight)")

    # ============ Step 6: Shadow 测试 ============
    print("\n[6/6] Shadow 测试（Config_A 基线参数）")
    shadow_config = {
        "risk_managed": True,
        "target_vol": 0.15,
        "vol_lookback": 20,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "scaler_cap": 2.0,
    }
    print(f"  Shadow 配置: {shadow_config}")
    sa = ShadowAccount(shadow_config)
    n_trials = max(len(symbols), 13)
    print(f"  n_trials: {n_trials}")
    print()

    shadow_a = run_shadow_test(sa, hist_a, fwd_returns_hist, n_trials, FACTOR_A)
    print()
    shadow_b = run_shadow_test(sa, hist_b, fwd_returns_hist, n_trials, FACTOR_B)
    print()
    shadow_combined = run_shadow_test(sa, combined_hist, fwd_returns_hist, n_trials, "COMBINED")

    # ============ 汇总对比 ============
    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)

    print("\n[IC 指标]")
    print(f"{'因子':<35s} {'IC_IR':>8s} {'IC_mean':>10s} {'IC_std':>8s} {'decay':>8s}")
    print("-" * 75)
    print(f"{FACTOR_A:<35s} {metrics_a['ic_ir']:>+8.4f} {metrics_a['ic_mean']:>+10.4f} "
          f"{metrics_a['ic_std']:>8.4f} {metrics_a['ic_decay']:>8.4f}")
    print(f"{FACTOR_B:<35s} {metrics_b['ic_ir']:>+8.4f} {metrics_b['ic_mean']:>+10.4f} "
          f"{metrics_b['ic_std']:>8.4f} {metrics_b['ic_decay']:>8.4f}")
    print(f"{'COMBINED (equal weight)':<35s} {metrics_combined['ic_ir']:>+8.4f} "
          f"{metrics_combined['ic_mean']:>+10.4f} {metrics_combined['ic_std']:>8.4f} "
          f"{metrics_combined['ic_decay']:>8.4f}")

    print("\n[Shadow 指标 - Config_A 基线]")
    print(f"{'因子':<35s} {'pass':>6s} {'live_dsr':>10s} {'max_dd':>10s} "
          f"{'total_ret':>12s} {'sr':>8s} {'realized_vol':>14s}")
    print("-" * 100)
    print(f"{FACTOR_A:<35s} {'✅' if shadow_a['pass_shadow'] else '❌':>6s} "
          f"{shadow_a['live_dsr']:>+10.4f} {shadow_a['max_drawdown']:>10.4f} "
          f"{shadow_a['total_return']:>+12.4f} {shadow_a['sr_observed']:>8.4f} "
          f"{shadow_a['realized_vol']:>14.4f}")
    print(f"{FACTOR_B:<35s} {'✅' if shadow_b['pass_shadow'] else '❌':>6s} "
          f"{shadow_b['live_dsr']:>+10.4f} {shadow_b['max_drawdown']:>10.4f} "
          f"{shadow_b['total_return']:>+12.4f} {shadow_b['sr_observed']:>8.4f} "
          f"{shadow_b['realized_vol']:>14.4f}")
    print(f"{'COMBINED (equal weight)':<35s} "
          f"{'✅' if shadow_combined['pass_shadow'] else '❌':>6s} "
          f"{shadow_combined['live_dsr']:>+10.4f} {shadow_combined['max_drawdown']:>10.4f} "
          f"{shadow_combined['total_return']:>+12.4f} {shadow_combined['sr_observed']:>8.4f} "
          f"{shadow_combined['realized_vol']:>14.4f}")

    # ============ 结论判断 ============
    print("\n[组合效果评估]")
    best_single_ic_ir = max(metrics_a["ic_ir"], metrics_b["ic_ir"])
    best_single_max_dd = min(shadow_a["max_drawdown"], shadow_b["max_drawdown"])
    best_single_live_dsr = max(shadow_a["live_dsr"], shadow_b["live_dsr"])

    ic_ir_improved = metrics_combined["ic_ir"] > best_single_ic_ir
    max_dd_improved = shadow_combined["max_drawdown"] < best_single_max_dd
    live_dsr_improved = shadow_combined["live_dsr"] > best_single_live_dsr

    print(f"  组合 IC_IR vs 单因子最优: {metrics_combined['ic_ir']:+.4f} vs {best_single_ic_ir:+.4f}  "
          f"{'✅ 提升' if ic_ir_improved else '❌ 未提升'}")
    print(f"  组合 max_dd vs 单因子最优: {shadow_combined['max_drawdown']:.4f} vs {best_single_max_dd:.4f}  "
          f"{'✅ 降低' if max_dd_improved else '❌ 未降低'}")
    print(f"  组合 live_dsr vs 单因子最优: {shadow_combined['live_dsr']:+.4f} vs {best_single_live_dsr:+.4f}  "
          f"{'✅ 提升' if live_dsr_improved else '❌ 未提升'}")

    overall_success = ic_ir_improved and max_dd_improved
    print(f"\n  组合验证结论: {'✅ 成功' if overall_success else '⚠️ 部分有效'}")
    if not overall_success:
        if not ic_ir_improved:
            print("    - IC_IR 未提升，可能两因子相关性较高")
        if not max_dd_improved:
            print("    - max_dd 未降低，可能两因子回撤期重叠")

    # ============ 保存结果 ============
    batch_id = f"fifteenth_batch_combo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "factor_a": FACTOR_A,
        "factor_b": FACTOR_B,
        "combination_method": "equal_weight_rank_normalized",
        "combination_weight_a": 0.5,
        "ic_metrics": {
            FACTOR_A: metrics_a,
            FACTOR_B: metrics_b,
            "COMBINED": metrics_combined,
        },
        "shadow_config": shadow_config,
        "shadow_results": {
            FACTOR_A: shadow_a,
            FACTOR_B: shadow_b,
            "COMBINED": shadow_combined,
        },
        "combination_assessment": {
            "best_single_ic_ir": best_single_ic_ir,
            "combined_ic_ir": metrics_combined["ic_ir"],
            "ic_ir_improved": ic_ir_improved,
            "best_single_max_dd": best_single_max_dd,
            "combined_max_dd": shadow_combined["max_drawdown"],
            "max_dd_improved": max_dd_improved,
            "best_single_live_dsr": best_single_live_dsr,
            "combined_live_dsr": shadow_combined["live_dsr"],
            "live_dsr_improved": live_dsr_improved,
            "overall_success": overall_success,
        },
    }

    output_path = output_dir / "combo_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n结果已保存至: {output_path}")
    print(f"批次 ID: {batch_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
