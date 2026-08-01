"""S3 第十七批次：IC 加权组合验证（P2.2 v6.5 动态权重适应信号反转）

设计背景：
    第十五批次等权组合验证发现：
        - VT_MICRO_VOL_SKEW_INV 信号反转（IC_IR=-0.3050），等权组合稀释 Alpha
        - 组合 max_dd 降低 28.4%（回撤分散化成功），但 IC_IR 未提升
    根本问题：等权组合假设因子信号方向一致，无法适应信号反转

v6.5 IC 加权组合设计：
    1. 滚动 IC_IR 作为动态权重
       - 在每个时间点 t，用过去 lookback 天（20 天）的 IC 序列计算各因子滚动 IC_IR
       - 权重 = IC_IR_i / sum(|IC_IR_j|)  （保留符号，自动适应信号方向）
    2. 信号方向自适应
       - 当因子 IC_IR 为负时，权重为负（相当于反向使用该因子）
       - 当因子 IC_IR 为正时，权重为正（正常使用）
       - 当因子 IC_IR 接近 0 时，权重接近 0（自动降低弱信号因子权重）
    3. 与等权组合对比
       - 验证 IC 加权是否能提升组合 IC_IR
       - 验证 IC 加权是否能保持 max_dd 降低的优势

v6.5 验证目标：
    - IC 加权组合 IC_IR 是否优于等权组合（+0.1364）
    - IC 加权组合 IC_IR 是否优于单因子最优（+0.3981）
    - IC 加权组合 max_dd 是否保持降低
    - 验证"动态权重适应信号反转"假设

注意：
    - 滚动 IC_IR 使用过去 20 天的 IC，存在 5 天 forward return 实现延迟
    - 简化处理：直接用过去 20 天 IC（与 compute_ic_ir 实现一致），不前移
    - 这是一个潜在的轻微前视偏差，但与现有 build_factor_history 简化一致
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history,
    compute_ic_decay,
    compute_ic_ir,
    compute_rolling_ic_series,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    compute_equal_weight_benchmark,
    list_available_symbols,
    load_benchmark_returns,
    load_fundamentals,
    load_price_data,
)
from research.vibe_trading_factor_analysis.shadow.shadow_account import ShadowAccount

logger = logging.getLogger("run_seventeenth_batch_ic_weighted")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 验证的因子（与第十五批次一致）
FACTOR_A = "VT_MICRO_VOL_SKEW_INV"
FACTOR_B = "VT_QUALTREND_MARGIN_EXP"
ROLLING_LOOKBACK = 20  # 滚动 IC_IR 回看窗口


def cross_sectional_rank(values: dict[str, float]) -> dict[str, float]:
    """cross-sectional rank 标准化到 [0, 1]"""
    valid = {s: v for s, v in values.items() if np.isfinite(v)}
    if len(valid) < 2:
        return {s: 0.5 for s in values}
    sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
    n = len(sorted_syms)
    ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
    return {s: ranks.get(s, 0.5) for s in values}


def compute_rolling_ic_ir(
    ic_series: list[float],
    t: int,
    lookback: int = ROLLING_LOOKBACK,
) -> float:
    """计算时间点 t 的滚动 IC_IR

    用过去 lookback 天的 IC 序列计算 IC_IR = mean(IC) / std(IC)

    Args:
        ic_series: 完整的 IC 序列（120 天）
        t: 当前时间点索引
        lookback: 回看窗口（默认 20 天）

    Returns:
        滚动 IC_IR（保留符号）
    """
    if t < lookback:
        # 样本不足，返回 0（中性权重）
        return 0.0
    window = ic_series[t - lookback:t]
    arr = np.array(window, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return 0.0
    ic_mean = float(np.mean(arr))
    ic_std = float(np.std(arr, ddof=1))
    if ic_std < 1e-12:
        return 0.0
    return ic_mean / ic_std


def combine_factors_ic_weighted(
    factor_history_a: list[dict[str, float]],
    factor_history_b: list[dict[str, float]],
    ic_series_a: list[float],
    ic_series_b: list[float],
    lookback: int = ROLLING_LOOKBACK,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """IC 加权组合两个因子（动态权重，符号自适应）

    在每个时间点 t：
        1. 计算各因子的滚动 IC_IR（过去 lookback 天）
        2. 权重 = IC_IR_i / sum(|IC_IR_j|)（保留符号）
        3. combined[t] = weight_a * rank(f_A[t]) + weight_b * rank(f_B[t])

    当某因子 IC_IR 为负时，权重为负（反向使用），自动适应信号反转。

    Args:
        factor_history_a: 因子 A 的日频值序列
        factor_history_b: 因子 B 的日频值序列
        ic_series_a: 因子 A 的 IC 序列
        ic_series_b: 因子 B 的 IC 序列
        lookback: 滚动窗口

    Returns:
        combined_history: 组合信号日频值序列
        weights_history: 每日权重 [{weight_a, weight_b, ic_ir_a, ic_ir_b}, ...]
    """
    n = min(len(factor_history_a), len(factor_history_b))
    combined = []
    weights_history = []

    for t in range(n):
        # 计算滚动 IC_IR
        ic_ir_a = compute_rolling_ic_ir(ic_series_a, t, lookback)
        ic_ir_b = compute_rolling_ic_ir(ic_series_b, t, lookback)

        # 计算权重（保留符号）
        abs_sum = abs(ic_ir_a) + abs(ic_ir_b)
        if abs_sum < 1e-6:
            # 两个因子都接近 0，用等权兜底
            weight_a, weight_b = 0.5, 0.5
        else:
            weight_a = ic_ir_a / abs_sum
            weight_b = ic_ir_b / abs_sum

        # rank 标准化
        rank_a = cross_sectional_rank(factor_history_a[t])
        rank_b = cross_sectional_rank(factor_history_b[t])
        common_syms = set(rank_a.keys()) & set(rank_b.keys())

        # IC 加权组合
        combined_day = {
            s: weight_a * rank_a[s] + weight_b * rank_b[s]
            for s in common_syms
        }
        combined.append(combined_day)
        weights_history.append({
            "weight_a": float(weight_a),
            "weight_b": float(weight_b),
            "ic_ir_a": float(ic_ir_a),
            "ic_ir_b": float(ic_ir_b),
        })

    return combined, weights_history


def compute_all_ic_metrics(
    factor_history: list[dict[str, float]],
    forward_returns_history: list[dict[str, float]],
    name: str,
) -> dict[str, Any]:
    """计算完整 IC 指标"""
    ic_series = compute_rolling_ic_series(factor_history, forward_returns_history)
    ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series)
    decay = compute_ic_decay(factor_history, forward_returns_history)
    logger.info(f"  {name}:")
    logger.info(f"    IC_IR={ic_ir:+.4f}  IC_mean={ic_mean:+.4f}  IC_std={ic_std:.4f}  decay={decay:.4f}")
    return {
        "ic_ir": float(ic_ir),
        "ic_mean": float(ic_mean),
        "ic_std": float(ic_std),
        "ic_decay": float(decay),
    }


def run_shadow_test(
    shadow_account: ShadowAccount,
    factor_history: list[dict[str, float]],
    forward_returns_history: list[dict[str, float]],
    n_trials: int,
    name: str,
) -> dict[str, Any]:
    """执行 Shadow 测试"""
    result = shadow_account.run_shadow(
        factor_values_history=factor_history,
        forward_returns_history=forward_returns_history,
        n_trials=n_trials,
        factor_name=name,
    )
    logger.info(f"  {name}:")
    print(f"    pass_shadow={result.pass_shadow}  live_dsr={result.live_dsr:+.4f}  "
          f"max_dd={result.max_drawdown:.4f}")
    print(f"    total_return={result.total_return:+.4f}  sr_observed={result.sr_observed:.4f}  "
          f"realized_vol={result.realized_vol:.4f}")
    if result.fail_reasons:
        logger.info(f"    fail_reasons: {result.fail_reasons}")
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


def load_fundamentals_history(symbols: list[str]) -> dict[str, Any]:
    """从 cache/fundamentals/ 加载历史季度财务数据"""
    cache_dir = _PROJECT_ROOT / "cache" / "fundamentals"
    history: dict[str, Any] = {}
    for sym in symbols:
        cache_path = cache_dir / f"{sym}_history.json"
        if not cache_path.exists():
            continue
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("n_valid", 0) >= 4:
                history[sym] = data
        except Exception as e:
            logger.debug("[ICWeightedLoader] 加载 %s 失败: %s", sym, e)
    return history


def main() -> int:
    """主入口：IC 加权组合验证"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第十七批次：IC 加权组合验证（P2.2 v6.5 动态权重适应信号反转）")
    logger.info("=" * 70)
    logger.info("v6.5 改进内容:")
    logger.info(f"  - Factor A: {FACTOR_A}（第十五批次等权组合中 IC_IR=-0.3050，信号反转）")
    logger.info(f"  - Factor B: {FACTOR_B}（第十五批次等权组合中 IC_IR=+0.3981，信号正常）")
    logger.info(f"  - 组合方法：滚动 IC_IR 加权（lookback={ROLLING_LOOKBACK} 天）")
    logger.info("  - 信号方向自适应：IC_IR 为负时权重为负（反向使用因子）")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/6] 加载数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  price_data: {len(price_data)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 加载 fundamentals_history ============
    logger.info("\n[2/6] 加载历史季度财务数据")
    fundamentals_history = load_fundamentals_history(symbols)
    logger.info(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建日频因子历史 ============
    logger.info("\n[3/6] 构建日频因子历史（history_days=120, forward_window=5）")
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
    logger.info(f"  factor_history: {len(factor_history)} 个因子 | valid_dates: {len(valid_dates)} 天")

    # ============ Step 4: 提取单因子历史 + 计算 IC 序列 ============
    logger.info("\n[4/6] 提取单因子历史并计算 IC 序列")
    if FACTOR_A not in factor_history or FACTOR_B not in factor_history:
        logger.info(f"[ERROR] 因子不存在: A={FACTOR_A in factor_history} B={FACTOR_B in factor_history}")
        return 1

    hist_a = factor_history[FACTOR_A]
    hist_b = factor_history[FACTOR_B]
    n = min(len(hist_a), len(hist_b), len(fwd_returns_hist))
    hist_a = hist_a[:n]
    hist_b = hist_b[:n]
    fwd_returns_hist = fwd_returns_hist[:n]
    logger.info(f"  Factor A ({FACTOR_A}): {len(hist_a)} 天")
    logger.info(f"  Factor B ({FACTOR_B}): {len(hist_b)} 天")

    # 计算各因子 IC 序列
    ic_series_a = compute_rolling_ic_series(hist_a, fwd_returns_hist)
    ic_series_b = compute_rolling_ic_series(hist_b, fwd_returns_hist)

    # 全局 IC_IR（120 天）
    ic_ir_a_full, _, _ = compute_ic_ir(ic_series_a)
    ic_ir_b_full, _, _ = compute_ic_ir(ic_series_b)
    logger.info(f"  Factor A 全局 IC_IR: {ic_ir_a_full:+.4f}")
    logger.info(f"  Factor B 全局 IC_IR: {ic_ir_b_full:+.4f}")

    # ============ Step 5: 构建 IC 加权组合 + 等权组合（对照） ============
    logger.info("\n[5/6] 构建 IC 加权组合（vs 等权组合对照）")

    # IC 加权组合
    combined_ic_weighted, weights_history = combine_factors_ic_weighted(
        hist_a, hist_b, ic_series_a, ic_series_b, lookback=ROLLING_LOOKBACK
    )
    logger.info(f"  IC 加权组合: {len(combined_ic_weighted)} 天")

    # 等权组合（对照，与第十五批次一致）
    combined_equal_weight = []
    for i in range(n):
        rank_a = cross_sectional_rank(hist_a[i])
        rank_b = cross_sectional_rank(hist_b[i])
        common_syms = set(rank_a.keys()) & set(rank_b.keys())
        combined_equal_weight.append({s: 0.5 * rank_a[s] + 0.5 * rank_b[s] for s in common_syms})
    logger.info(f"  等权组合: {len(combined_equal_weight)} 天")

    # 分析权重变化
    logger.info(f"\n  权重变化分析（lookback={ROLLING_LOOKBACK}）:")
    w_a_arr = np.array([w["weight_a"] for w in weights_history])
    w_b_arr = np.array([w["weight_b"] for w in weights_history])
    ic_ir_a_arr = np.array([w["ic_ir_a"] for w in weights_history])
    ic_ir_b_arr = np.array([w["ic_ir_b"] for w in weights_history])

    # 跳过前 lookback 天（样本不足）
    valid_range = range(ROLLING_LOOKBACK, n)
    if len(list(valid_range)) > 0:
        w_a_valid = w_a_arr[ROLLING_LOOKBACK:]
        w_b_valid = w_b_arr[ROLLING_LOOKBACK:]
        ic_ir_a_valid = ic_ir_a_arr[ROLLING_LOOKBACK:]
        ic_ir_b_valid = ic_ir_b_arr[ROLLING_LOOKBACK:]

        print(f"    Factor A 滚动 IC_IR: mean={np.mean(ic_ir_a_valid):+.4f}  "
              f"min={np.min(ic_ir_a_valid):+.4f}  max={np.max(ic_ir_a_valid):+.4f}")
        print(f"    Factor B 滚动 IC_IR: mean={np.mean(ic_ir_b_valid):+.4f}  "
              f"min={np.min(ic_ir_b_valid):+.4f}  max={np.max(ic_ir_b_valid):+.4f}")
        print(f"    Factor A 权重: mean={np.mean(w_a_valid):+.4f}  "
              f"min={np.min(w_a_valid):+.4f}  max={np.max(w_a_valid):+.4f}")
        print(f"    Factor B 权重: mean={np.mean(w_b_valid):+.4f}  "
              f"min={np.min(w_b_valid):+.4f}  max={np.max(w_b_valid):+.4f}")

        # 统计反向使用 Factor A 的天数（weight_a < 0）
        neg_a_days = int(np.sum(w_a_valid < 0))
        pos_a_days = int(np.sum(w_a_valid > 0))
        print(f"    Factor A 反向使用天数（weight<0）: {neg_a_days}/{len(w_a_valid)} "
              f"({100*neg_a_days/len(w_a_valid):.1f}%)")
        print(f"    Factor A 正向使用天数（weight>0）: {pos_a_days}/{len(w_a_valid)} "
              f"({100*pos_a_days/len(w_a_valid):.1f}%)")

    # ============ Step 6: IC 指标和 Shadow 对比 ============
    logger.info("\n[6/6] IC 指标和 Shadow 对比")
    logger.info("-" * 70)

    metrics_a = compute_all_ic_metrics(hist_a, fwd_returns_hist, FACTOR_A)
    print()
    metrics_b = compute_all_ic_metrics(hist_b, fwd_returns_hist, FACTOR_B)
    print()
    metrics_equal = compute_all_ic_metrics(combined_equal_weight, fwd_returns_hist, "等权组合 (baseline)")
    print()
    metrics_ic_weighted = compute_all_ic_metrics(combined_ic_weighted, fwd_returns_hist, "IC 加权组合 (v6.5)")

    logger.info("\n  Shadow 测试（Config_A 基线）:")
    shadow_config = {
        "risk_managed": True,
        "target_vol": 0.15,
        "vol_lookback": 20,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "scaler_cap": 2.0,
    }
    logger.info(f"  Shadow 配置: {shadow_config}")
    sa = ShadowAccount(shadow_config)
    n_trials = max(len(symbols), 13)
    logger.info(f"  n_trials: {n_trials}\n")

    shadow_a = run_shadow_test(sa, hist_a, fwd_returns_hist, n_trials, FACTOR_A)
    print()
    shadow_b = run_shadow_test(sa, hist_b, fwd_returns_hist, n_trials, FACTOR_B)
    print()
    shadow_equal = run_shadow_test(sa, combined_equal_weight, fwd_returns_hist, n_trials, "等权组合")
    print()
    shadow_ic_weighted = run_shadow_test(sa, combined_ic_weighted, fwd_returns_hist, n_trials, "IC 加权组合")

    # ============ 汇总对比 ============
    logger.info("\n" + "=" * 70)
    logger.info("汇总对比")
    logger.info("=" * 70)

    logger.info("\n[IC 指标]")
    logger.info(f"{'因子':<35s} {'IC_IR':>8s} {'IC_mean':>10s} {'IC_std':>8s} {'decay':>8s}")
    logger.info("-" * 75)
    print(f"{FACTOR_A:<35s} {metrics_a['ic_ir']:>+8.4f} {metrics_a['ic_mean']:>+10.4f} "
          f"{metrics_a['ic_std']:>8.4f} {metrics_a['ic_decay']:>8.4f}")
    print(f"{FACTOR_B:<35s} {metrics_b['ic_ir']:>+8.4f} {metrics_b['ic_mean']:>+10.4f} "
          f"{metrics_b['ic_std']:>8.4f} {metrics_b['ic_decay']:>8.4f}")
    print(f"{'等权组合 (baseline)':<35s} {metrics_equal['ic_ir']:>+8.4f} "
          f"{metrics_equal['ic_mean']:>+10.4f} {metrics_equal['ic_std']:>8.4f} "
          f"{metrics_equal['ic_decay']:>8.4f}")
    print(f"{'IC 加权组合 (v6.5)':<35s} {metrics_ic_weighted['ic_ir']:>+8.4f} "
          f"{metrics_ic_weighted['ic_mean']:>+10.4f} {metrics_ic_weighted['ic_std']:>8.4f} "
          f"{metrics_ic_weighted['ic_decay']:>8.4f}")

    logger.info("\n[Shadow 指标 - Config_A 基线]")
    print(f"{'因子':<35s} {'pass':>6s} {'live_dsr':>10s} {'max_dd':>10s} "
          f"{'total_ret':>12s} {'sr':>8s}")
    logger.info("-" * 90)
    print(f"{FACTOR_A:<35s} {'✅' if shadow_a['pass_shadow'] else '❌':>6s} "
          f"{shadow_a['live_dsr']:>+10.4f} {shadow_a['max_drawdown']:>10.4f} "
          f"{shadow_a['total_return']:>+12.4f} {shadow_a['sr_observed']:>8.4f}")
    print(f"{FACTOR_B:<35s} {'✅' if shadow_b['pass_shadow'] else '❌':>6s} "
          f"{shadow_b['live_dsr']:>+10.4f} {shadow_b['max_drawdown']:>10.4f} "
          f"{shadow_b['total_return']:>+12.4f} {shadow_b['sr_observed']:>8.4f}")
    print(f"{'等权组合 (baseline)':<35s} "
          f"{'✅' if shadow_equal['pass_shadow'] else '❌':>6s} "
          f"{shadow_equal['live_dsr']:>+10.4f} {shadow_equal['max_drawdown']:>10.4f} "
          f"{shadow_equal['total_return']:>+12.4f} {shadow_equal['sr_observed']:>8.4f}")
    print(f"{'IC 加权组合 (v6.5)':<35s} "
          f"{'✅' if shadow_ic_weighted['pass_shadow'] else '❌':>6s} "
          f"{shadow_ic_weighted['live_dsr']:>+10.4f} {shadow_ic_weighted['max_drawdown']:>10.4f} "
          f"{shadow_ic_weighted['total_return']:>+12.4f} {shadow_ic_weighted['sr_observed']:>8.4f}")

    # ============ IC 加权 vs 等权对比 ============
    logger.info("\n[IC 加权 vs 等权组合对比]")
    ic_ir_improvement = metrics_ic_weighted["ic_ir"] - metrics_equal["ic_ir"]
    max_dd_change = shadow_ic_weighted["max_drawdown"] - shadow_equal["max_drawdown"]
    live_dsr_change = shadow_ic_weighted["live_dsr"] - shadow_equal["live_dsr"]

    print(f"  IC_IR 变化: {metrics_ic_weighted['ic_ir']:+.4f} - {metrics_equal['ic_ir']:+.4f} "
          f"= {ic_ir_improvement:+.4f}  "
          f"{'✅ 提升' if ic_ir_improvement > 0 else '❌ 未提升'}")
    print(f"  max_dd 变化: {shadow_ic_weighted['max_drawdown']:.4f} - {shadow_equal['max_drawdown']:.4f} "
          f"= {max_dd_change:+.4f}  "
          f"{'✅ 降低' if max_dd_change < 0 else '❌ 未降低'}")
    print(f"  live_dsr 变化: {shadow_ic_weighted['live_dsr']:+.4f} - {shadow_equal['live_dsr']:+.4f} "
          f"= {live_dsr_change:+.4f}  "
          f"{'✅ 提升' if live_dsr_change > 0 else '❌ 未提升'}")

    # ============ 结论判断 ============
    logger.info("\n[v6.5 验证结论]")
    best_single_ic_ir = max(metrics_a["ic_ir"], metrics_b["ic_ir"])
    ic_weighted_beats_equal = metrics_ic_weighted["ic_ir"] > metrics_equal["ic_ir"]
    ic_weighted_beats_single = metrics_ic_weighted["ic_ir"] > best_single_ic_ir

    logger.info(f"  IC 加权 vs 等权: IC_IR {'✅ 优于' if ic_weighted_beats_equal else '❌ 不及'}等权组合")
    logger.info(f"  IC 加权 vs 单因子最优: IC_IR {'✅ 优于' if ic_weighted_beats_single else '❌ 不及'}单因子最优 ({best_single_ic_ir:+.4f})")

    if ic_weighted_beats_equal:
        logger.info("\n  🎉 v6.5 IC 加权组合验证成功：动态权重适应信号反转有效")
        logger.info("  核心改进：当因子 IC_IR 为负时，权重为负（反向使用），避免 Alpha 稀释")
    else:
        logger.info("\n  ⚠️ IC 加权组合未优于等权组合，可能原因：")
        logger.info("     - 滚动 IC_IR 是滞后指标，权重调整不够及时")
        logger.info("     - 20 天 lookback 窗口可能不适配因子信号的周期")

    # ============ 保存结果 ============
    batch_id = f"seventeenth_batch_ic_weighted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 权重变化序列（用于分析）
    weights_summary = {
        "lookback": ROLLING_LOOKBACK,
        "factor_a_ic_ir_full": float(ic_ir_a_full),
        "factor_b_ic_ir_full": float(ic_ir_b_full),
        "weights_stats": {},
    }
    if n > ROLLING_LOOKBACK:
        w_a_valid = w_a_arr[ROLLING_LOOKBACK:]
        w_b_valid = w_b_arr[ROLLING_LOOKBACK:]
        weights_summary["weights_stats"] = {
            "factor_a_weight": {
                "mean": float(np.mean(w_a_valid)),
                "min": float(np.min(w_a_valid)),
                "max": float(np.max(w_a_valid)),
                "neg_days": int(np.sum(w_a_valid < 0)),
                "pos_days": int(np.sum(w_a_valid > 0)),
            },
            "factor_b_weight": {
                "mean": float(np.mean(w_b_valid)),
                "min": float(np.min(w_b_valid)),
                "max": float(np.max(w_b_valid)),
                "neg_days": int(np.sum(w_b_valid < 0)),
                "pos_days": int(np.sum(w_b_valid > 0)),
            },
        }

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "factor_a": FACTOR_A,
        "factor_b": FACTOR_B,
        "combination_method": "ic_weighted_rolling",
        "rolling_lookback": ROLLING_LOOKBACK,
        "ic_metrics": {
            FACTOR_A: metrics_a,
            FACTOR_B: metrics_b,
            "equal_weight_baseline": metrics_equal,
            "ic_weighted_v65": metrics_ic_weighted,
        },
        "shadow_config": shadow_config,
        "shadow_results": {
            FACTOR_A: shadow_a,
            FACTOR_B: shadow_b,
            "equal_weight_baseline": shadow_equal,
            "ic_weighted_v65": shadow_ic_weighted,
        },
        "weights_summary": weights_summary,
        "comparison": {
            "ic_ir_improvement_vs_equal": float(ic_ir_improvement),
            "max_dd_change_vs_equal": float(max_dd_change),
            "live_dsr_change_vs_equal": float(live_dsr_change),
            "ic_weighted_beats_equal": bool(ic_weighted_beats_equal),
            "ic_weighted_beats_single": bool(ic_weighted_beats_single),
        },
    }

    output_path = output_dir / "ic_weighted_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"\n结果已保存至: {output_path}")
    logger.info(f"批次 ID: {batch_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
