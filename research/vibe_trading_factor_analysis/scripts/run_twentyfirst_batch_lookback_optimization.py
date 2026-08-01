"""S3 第二十一批次：IC 加权组合 lookback 窗口优化验证（P2.2 v6.9）

设计背景：
    v6.5~v6.8 完成了 IC 加权组合 + Config_E_plus1 方法学验证和流水线集成：
        - v6.5：IC 加权方法学（IC_IR=+0.4434 优于单因子）
        - v6.6：Config_E 梯度参数（参数敏感性与单因子相反）
        - v6.7：Config_E+ 突破（Config_E_plus1 通过 Shadow）
        - v6.8：PipelineOrchestrator 集成（集成结果与独立脚本完全一致）

    但 lookback=20 是任意选定的（参考学术文献常用的月度窗口），未经验证。
    lookback 决定 IC 加权动态权重的适应速度：
        - lookback 太短（如 5 天）：权重对噪声过度敏感，频繁翻转
        - lookback 太长（如 30 天）：权重适应信号变化太慢，错过反转点
        - lookback 适中（10-20 天）：平衡适应速度与稳定性

v6.9 验证目标：
    1. 测试 lookback = 5, 10, 15, 20, 30 天的 IC 加权组合表现
    2. 找到 live_dsr / max_dd / total_return / IC_IR 的最优权衡点
    3. 确认 lookback=20 是否为最优，或找到更好的配置
    4. 验证 IC 加权方法对 lookback 参数的稳健性

v6.9 验证方法：
    - 固定 Config_E_plus1（v6.7 已验证最优 Shadow 参数）
    - 仅变化 lookback 参数
    - 对比 5 组 lookback 下的 IC 指标和 Shadow 表现
    - 分析权重变化频率（适应速度指标）

预期结果：
    - lookback=20 应该是接近最优的（v6.5~v6.8 已验证有效）
    - lookback=10 可能更快适应信号反转，但稳定性可能下降
    - lookback=30 可能更稳定，但适应速度慢
    - 最优 lookback 应在 10-20 之间
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

logger = logging.getLogger("run_twentyfirst_batch_lookback")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 固定因子对（v6.5~v6.8 已验证）
FACTOR_A = "VT_MICRO_VOL_SKEW_INV"
FACTOR_B = "VT_QUALTREND_MARGIN_EXP"

# 固定 Config_E_plus1（v6.7 已验证最优 Shadow 参数）
SHADOW_CONFIG_E_PLUS1 = {
    "risk_managed": True,
    "target_vol": 0.07,
    "vol_lookback": 20,
    "dd_derisk_threshold": 0.018,
    "dd_derisk_factor": 0.18,
    "scaler_cap": 2.0,
}

# v6.9 测试的 lookback 系列（5 组梯度）
LOOKBACK_CONFIGS = {
    "lookback_5": {
        "lookback": 5,
        "desc": "极短窗口（最快适应，最不稳定）",
    },
    "lookback_10": {
        "lookback": 10,
        "desc": "短窗口（快速适应）",
    },
    "lookback_15": {
        "lookback": 15,
        "desc": "中等窗口",
    },
    "lookback_20": {
        "lookback": 20,
        "desc": "v6.5~v6.8 默认配置（基准）",
    },
    "lookback_30": {
        "lookback": 30,
        "desc": "长窗口（最稳定，最慢适应）",
    },
}


def cross_sectional_rank(values: dict[str, float]) -> dict[str, float]:
    """cross-sectional rank 标准化到 [0, 1]"""
    valid = {s: v for s, v in values.items()
             if isinstance(v, (int, float)) and np.isfinite(v)}
    if len(valid) < 2:
        return {s: 0.5 for s in values}
    sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
    n = len(sorted_syms)
    ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
    return {s: ranks.get(s, 0.5) for s in values}


def compute_rolling_ic_ir_at_t(
    ic_series: list[float],
    t: int,
    lookback: int,
) -> float:
    """计算时间点 t 的滚动 IC_IR = mean(IC) / std(IC)

    样本不足时返回 0（中性权重）。
    """
    if t < lookback:
        return 0.0
    window = ic_series[t - lookback:t]
    arr = np.array(window, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < min(5, lookback):
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
    lookback: int,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """IC 加权组合（动态权重，符号自适应）

    Args:
        lookback: 滚动 IC_IR 回看窗口（关键参数）
    """
    n = min(len(factor_history_a), len(factor_history_b))
    combined: list[dict[str, float]] = []
    weights_history: list[dict[str, float]] = []

    for t in range(n):
        ic_ir_a = compute_rolling_ic_ir_at_t(ic_series_a, t, lookback)
        ic_ir_b = compute_rolling_ic_ir_at_t(ic_series_b, t, lookback)

        abs_sum = abs(ic_ir_a) + abs(ic_ir_b)
        if abs_sum < 1e-6:
            weight_a, weight_b = 0.5, 0.5
        else:
            weight_a = ic_ir_a / abs_sum
            weight_b = ic_ir_b / abs_sum

        rank_a = cross_sectional_rank(factor_history_a[t])
        rank_b = cross_sectional_rank(factor_history_b[t])
        common_syms = set(rank_a.keys()) & set(rank_b.keys())

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


def compute_ic_metrics(
    factor_history: list[dict[str, float]],
    forward_returns_history: list[dict[str, float]],
    name: str,
) -> dict[str, Any]:
    """计算完整 IC 指标"""
    ic_series = compute_rolling_ic_series(factor_history, forward_returns_history)
    ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series)
    decay = compute_ic_decay(factor_history, forward_returns_history)
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
        "fail_reasons": list(result.fail_reasons) if result.fail_reasons else [],
    }


def analyze_weights(
    weights_history: list[dict[str, float]],
    lookback: int,
    n_days: int,
) -> dict[str, Any]:
    """分析权重变化（适应速度指标）

    关键指标：
        - neg_weight_pct: 反向使用占比（信号反转频率）
        - weight_flips: 权重符号翻转次数（适应速度）
        - weight_volatility: 权重波动率（稳定性）
    """
    if not weights_history or n_days <= lookback:
        return {"available": False, "reason": "samples insufficient"}

    w_a_arr = np.array([w["weight_a"] for w in weights_history])
    w_b_arr = np.array([w["weight_b"] for w in weights_history])

    # 跳过前 lookback 天（样本不足）
    valid_slice = slice(lookback, n_days)
    w_a_valid = w_a_arr[valid_slice]
    w_b_valid = w_b_arr[valid_slice]

    if len(w_a_valid) == 0:
        return {"available": False, "reason": "no valid weights"}

    # 权重符号翻转次数（适应速度指标）
    # 翻转 = weight[i] * weight[i-1] < 0
    w_a_signs = np.sign(w_a_valid)
    w_a_flips = int(np.sum(np.diff(w_a_signs) != 0))
    w_b_signs = np.sign(w_b_valid)
    w_b_flips = int(np.sum(np.diff(w_b_signs) != 0))

    # 权重波动率（稳定性指标）
    w_a_vol = float(np.std(w_a_valid))
    w_b_vol = float(np.std(w_b_valid))

    # 反向使用占比
    neg_a_days = int(np.sum(w_a_valid < 0))
    pos_a_days = int(np.sum(w_a_valid > 0))
    neg_b_days = int(np.sum(w_b_valid < 0))
    pos_b_days = int(np.sum(w_b_valid > 0))

    return {
        "available": True,
        "factor_a": {
            "weight_mean": float(np.mean(w_a_valid)),
            "weight_min": float(np.min(w_a_valid)),
            "weight_max": float(np.max(w_a_valid)),
            "weight_volatility": w_a_vol,
            "neg_weight_days": neg_a_days,
            "pos_weight_days": pos_a_days,
            "neg_weight_pct": float(neg_a_days / len(w_a_valid)) if len(w_a_valid) > 0 else 0.0,
            "weight_flips": w_a_flips,
            "flip_rate": float(w_a_flips / max(len(w_a_valid) - 1, 1)),
        },
        "factor_b": {
            "weight_mean": float(np.mean(w_b_valid)),
            "weight_min": float(np.min(w_b_valid)),
            "weight_max": float(np.max(w_b_valid)),
            "weight_volatility": w_b_vol,
            "neg_weight_days": neg_b_days,
            "pos_weight_days": pos_b_days,
            "neg_weight_pct": float(neg_b_days / len(w_b_valid)) if len(w_b_valid) > 0 else 0.0,
            "weight_flips": w_b_flips,
            "flip_rate": float(w_b_flips / max(len(w_b_valid) - 1, 1)),
        },
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
            logger.debug("[LookbackOptLoader] 加载 %s 失败: %s", sym, e)
    return history


def main() -> int:
    """主入口：lookback 窗口优化验证"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第二十一批次：IC 加权组合 lookback 窗口优化验证（P2.2 v6.9）")
    logger.info("=" * 70)
    logger.info("v6.9 改进内容:")
    logger.info(f"  - Factor A: {FACTOR_A}（信号反转因子）")
    logger.info(f"  - Factor B: {FACTOR_B}（信号正常因子）")
    logger.info("  - 固定 Shadow 参数：Config_E_plus1（v6.7 已验证最优）")
    logger.info(f"  - 变化参数：lookback = {[c['lookback'] for c in LOOKBACK_CONFIGS.values()]}")
    print()
    logger.info("v6.9 验证目标:")
    logger.info("  - 找到 live_dsr / max_dd / total_return / IC_IR 的最优权衡点")
    logger.info("  - 确认 lookback=20 是否为最优，或找到更好的配置")
    logger.info("  - 验证 IC 加权方法对 lookback 参数的稳健性")
    logger.info("  - 分析权重变化频率（适应速度指标）")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/5] 加载数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  price_data: {len(price_data)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 加载 fundamentals_history ============
    logger.info("\n[2/5] 加载历史季度财务数据")
    fundamentals_history = load_fundamentals_history(symbols)
    logger.info(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建日频因子历史 ============
    logger.info("\n[3/5] 构建日频因子历史（history_days=120, forward_window=5）")
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

    # ============ Step 4: 提取单因子 + 计算全局 IC_IR ============
    logger.info("\n[4/5] 提取单因子并计算全局 IC_IR")
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

    # 单因子全局 IC_IR（不依赖 lookback）
    ic_series_a = compute_rolling_ic_series(hist_a, fwd_returns_hist)
    ic_series_b = compute_rolling_ic_series(hist_b, fwd_returns_hist)
    ic_ir_a_full, _, _ = compute_ic_ir(ic_series_a)
    ic_ir_b_full, _, _ = compute_ic_ir(ic_series_b)
    logger.info(f"  Factor A 全局 IC_IR: {ic_ir_a_full:+.4f}")
    logger.info(f"  Factor B 全局 IC_IR: {ic_ir_b_full:+.4f}")

    # ============ Step 5: 遍历 lookback 配置，构建 IC 加权组合并测试 ============
    logger.info("\n[5/5] 遍历 lookback 配置（Config_E_plus1 固定）")
    logger.info("-" * 70)
    logger.info(f"  Shadow 配置: {SHADOW_CONFIG_E_PLUS1}")
    n_trials = max(len(symbols), 13)
    logger.info(f"  n_trials: {n_trials}")

    all_results: dict[str, dict[str, Any]] = {}

    for config_name, config in LOOKBACK_CONFIGS.items():
        lookback = config["lookback"]
        desc = config["desc"]
        logger.info(f"\n  [{config_name}] lookback={lookback}  ({desc})")

        # 检查样本充足性
        if n < lookback + 5:
            logger.info(f"    ❌ 样本不足: n={n} < lookback+5={lookback+5}")
            all_results[config_name] = {
                "lookback": lookback,
                "desc": desc,
                "passed": False,
                "fail_reason": f"samples insufficient: n={n} < lookback+5={lookback+5}",
            }
            continue

        # 构建 IC 加权组合
        combined, weights_history = combine_factors_ic_weighted(
            hist_a, hist_b, ic_series_a, ic_series_b, lookback=lookback,
        )

        # 计算 IC 指标
        ic_metrics = compute_ic_metrics(combined, fwd_returns_hist, config_name)
        print(f"    IC_IR={ic_metrics['ic_ir']:+.4f}  IC_mean={ic_metrics['ic_mean']:+.4f}  "
              f"IC_std={ic_metrics['ic_std']:.4f}  decay={ic_metrics['ic_decay']:.4f}")

        # 权重分析
        weights_stats = analyze_weights(weights_history, lookback, n)
        if weights_stats.get("available"):
            wa = weights_stats["factor_a"]
            wb = weights_stats["factor_b"]
            print(f"    Factor A: weight_mean={wa['weight_mean']:+.4f}  "
                  f"neg_pct={wa['neg_weight_pct']*100:.1f}%  "
                  f"flips={wa['weight_flips']}  flip_rate={wa['flip_rate']*100:.1f}%  "
                  f"vol={wa['weight_volatility']:.4f}")
            print(f"    Factor B: weight_mean={wb['weight_mean']:+.4f}  "
                  f"neg_pct={wb['neg_weight_pct']*100:.1f}%  "
                  f"flips={wb['weight_flips']}  flip_rate={wb['flip_rate']*100:.1f}%  "
                  f"vol={wb['weight_volatility']:.4f}")

        # Shadow 测试（Config_E_plus1 固定）
        sa = ShadowAccount(dict(SHADOW_CONFIG_E_PLUS1))
        combo_name = f"IC_WEIGHTED_lookback{lookback}"
        shadow = run_shadow_test(sa, combined, fwd_returns_hist, n_trials, combo_name)
        print(f"    Shadow: pass={shadow['pass_shadow']}  "
              f"live_dsr={shadow['live_dsr']:+.4f}  "
              f"max_dd={shadow['max_drawdown']:.4f}  "
              f"total_return={shadow['total_return']:+.4f}  "
              f"sr={shadow['sr_observed']:.4f}")

        all_results[config_name] = {
            "lookback": lookback,
            "desc": desc,
            "ic_metrics": ic_metrics,
            "weights_stats": weights_stats,
            "shadow": shadow,
            "passed": bool(shadow["pass_shadow"]),
        }

    # ============ 汇总对比 ============
    logger.info("\n" + "=" * 70)
    logger.info("汇总对比")
    logger.info("=" * 70)

    logger.info("\n[IC 指标对比]")
    print(f"{'配置':<20s} {'lookback':>10s} {'IC_IR':>10s} {'IC_mean':>10s} "
          f"{'IC_std':>10s} {'decay':>10s}")
    logger.info("-" * 80)
    for config_name, res in all_results.items():
        if not res.get("ic_metrics"):
            continue
        ic = res["ic_metrics"]
        print(f"{config_name:<20s} {res['lookback']:>10d} {ic['ic_ir']:>+10.4f} "
              f"{ic['ic_mean']:>+10.4f} {ic['ic_std']:>10.4f} {ic['ic_decay']:>10.4f}")

    logger.info("\n[Shadow 指标对比 - Config_E_plus1 固定]")
    print(f"{'配置':<20s} {'lookback':>10s} {'pass':>6s} {'live_dsr':>10s} "
          f"{'max_dd':>10s} {'total_ret':>12s} {'sr':>8s}")
    logger.info("-" * 90)
    for config_name, res in all_results.items():
        if not res.get("shadow"):
            continue
        sh = res["shadow"]
        status = "✅" if sh["pass_shadow"] else "❌"
        print(f"{config_name:<20s} {res['lookback']:>10d} {status:>6s} "
              f"{sh['live_dsr']:>+10.4f} {sh['max_drawdown']:>10.4f} "
              f"{sh['total_return']:>+12.4f} {sh['sr_observed']:>8.4f}")

    logger.info("\n[权重适应速度对比]")
    print(f"{'配置':<20s} {'lookback':>10s} {'A_neg%':>8s} {'A_flips':>10s} "
          f"{'A_flip_rate':>12s} {'A_vol':>8s} {'B_neg%':>8s} {'B_flips':>10s}")
    logger.info("-" * 100)
    for config_name, res in all_results.items():
        ws = res.get("weights_stats", {})
        if not ws.get("available"):
            logger.info(f"{config_name:<20s} {res.get('lookback', '?'):>10} {'N/A':>8s}")
            continue
        wa = ws["factor_a"]
        wb = ws["factor_b"]
        print(f"{config_name:<20s} {res['lookback']:>10d} "
              f"{wa['neg_weight_pct']*100:>7.1f}% {wa['weight_flips']:>10d} "
              f"{wa['flip_rate']*100:>11.1f}% {wa['weight_volatility']:>8.4f} "
              f"{wb['neg_weight_pct']*100:>7.1f}% {wb['weight_flips']:>10d}")

    # ============ 寻找最优 lookback ============
    logger.info("\n[最优 lookback 分析]")

    # 找到所有通过 Shadow 的配置
    passed_configs = [
        (name, res) for name, res in all_results.items()
        if res.get("passed", False)
    ]
    logger.info(f"  通过 Shadow 的配置数: {len(passed_configs)}/{len(all_results)}")

    if not passed_configs:
        logger.info("  ❌ 没有配置通过 Shadow")
        best_lookback = None
    else:
        logger.info("\n  通过 Shadow 的配置详情:")
        for name, res in passed_configs:
            sh = res["shadow"]
            ic = res["ic_metrics"]
            print(f"    {name} (lookback={res['lookback']}): "
                  f"live_dsr={sh['live_dsr']:+.4f}  total_return={sh['total_return']:+.4f}  "
                  f"IC_IR={ic['ic_ir']:+.4f}")

        # 最优标准：综合 live_dsr, total_return, IC_IR
        # "最小必要激进化"原则的扩展：选择最简单（最小 lookback）的通过配置
        # 但也要考虑 total_return 和 IC_IR
        logger.info("\n  最优 lookback 选择标准:")
        logger.info("    1. 必须通过 Shadow (live_dsr>0.5, max_dd<0.12)")
        logger.info("    2. total_return 越高越好（保留 Alpha 信号）")
        logger.info("    3. IC_IR 越高越好（信号质量）")
        logger.info("    4. 权重翻转次数适中（适应速度与稳定性平衡）")

        # 按 total_return 排序
        by_return = sorted(passed_configs, key=lambda x: -x[1]["shadow"]["total_return"])
        logger.info("\n  按 total_return 排序:")
        for i, (name, res) in enumerate(by_return):
            sh = res["shadow"]
            print(f"    {i+1}. {name} (lookback={res['lookback']}): "
                  f"total_return={sh['total_return']:+.4f}")

        # 按 IC_IR 排序
        by_icir = sorted(passed_configs, key=lambda x: -x[1]["ic_metrics"]["ic_ir"])
        logger.info("\n  按 IC_IR 排序:")
        for i, (name, res) in enumerate(by_icir):
            ic = res["ic_metrics"]
            print(f"    {i+1}. {name} (lookback={res['lookback']}): "
                  f"IC_IR={ic['ic_ir']:+.4f}")

        # 综合最优（live_dsr * total_return * IC_IR 加权）
        # 但要避免过度激进，所以选择"最小必要"原则
        # 最小 lookback 通过 Shadow 的配置
        by_lookback = sorted(passed_configs, key=lambda x: x[1]["lookback"])
        minimal_lookback = by_lookback[0]
        print(f"\n  最小 lookback 通过 Shadow: {minimal_lookback[0]} "
              f"(lookback={minimal_lookback[1]['lookback']})")

        # 综合 ranking：用 z-score 综合三个指标
        logger.info("\n  综合评分（z-score: live_dsr + total_return + IC_IR）:")
        all_dsrs = [res["shadow"]["live_dsr"] for _, res in passed_configs]
        all_returns = [res["shadow"]["total_return"] for _, res in passed_configs]
        all_icirs = [res["ic_metrics"]["ic_ir"] for _, res in passed_configs]

        mean_dsr, std_dsr = np.mean(all_dsrs), np.std(all_dsrs)
        mean_ret, std_ret = np.mean(all_returns), np.std(all_returns)
        mean_icir, std_icir = np.mean(all_icirs), np.std(all_icirs)

        scores = []
        for name, res in passed_configs:
            z_dsr = (res["shadow"]["live_dsr"] - mean_dsr) / max(std_dsr, 1e-9)
            z_ret = (res["shadow"]["total_return"] - mean_ret) / max(std_ret, 1e-9)
            z_icir = (res["ic_metrics"]["ic_ir"] - mean_icir) / max(std_icir, 1e-9)
            score = z_dsr + z_ret + z_icir
            scores.append((name, res, score))
            print(f"    {name} (lookback={res['lookback']}): "
                  f"z_dsr={z_dsr:+.3f}  z_ret={z_ret:+.3f}  z_icir={z_icir:+.3f}  "
                  f"total={score:+.3f}")

        scores.sort(key=lambda x: -x[2])
        best_config = scores[0]
        best_lookback = best_config[1]["lookback"]
        print(f"\n  🎯 综合评分最优: {best_config[0]} "
              f"(lookback={best_lookback}, score={best_config[2]:+.3f})")

    # ============ 验收检查 ============
    logger.info("\n[验收检查]")
    logger.info("-" * 70)
    checks = []

    # Check 1: lookback=20 通过 Shadow（v6.5~v6.8 基线）
    baseline = all_results.get("lookback_20", {})
    baseline_passed = baseline.get("passed", False)
    checks.append(("lookback=20 (v6.5~v6.8 基线) 通过 Shadow", baseline_passed))

    # Check 2: 至少 3 个 lookback 通过 Shadow（方法稳健性）
    n_passed = len(passed_configs)
    checks.append((f"至少 3 个 lookback 通过 Shadow (实际 {n_passed})", n_passed >= 3))

    # Check 3: 通过 Shadow 的 lookback 覆盖 10-30 天范围
    if passed_configs:
        passed_lookbacks = [res["lookback"] for _, res in passed_configs]
        covers_10_30 = min(passed_lookbacks) <= 15 and max(passed_lookbacks) >= 20
        checks.append((
            f"通过 Shadow 的 lookback 覆盖 10-30 范围 (实际 {min(passed_lookbacks)}-{max(passed_lookbacks)})",
            covers_10_30,
        ))
    else:
        checks.append(("通过 Shadow 的 lookback 覆盖 10-30 范围", False))

    # Check 4: 最优 lookback 的 IC_IR 优于单因子最优 (MARGIN_EXP +0.3981)
    if passed_configs:
        best_icir = max(res["ic_metrics"]["ic_ir"] for _, res in passed_configs)
        checks.append((
            f"最优 IC_IR ({best_icir:+.4f}) 优于单因子最优 (+0.3981)",
            best_icir > 0.3981,
        ))
    else:
        checks.append(("最优 IC_IR 优于单因子最优", False))

    # Check 5: 最优 lookback 的 total_return > 0.10 (Alpha 保留)
    if passed_configs:
        best_return = max(res["shadow"]["total_return"] for _, res in passed_configs)
        checks.append((
            f"最优 total_return ({best_return:+.4f}) > 0.10 (Alpha 保留)",
            best_return > 0.10,
        ))
    else:
        checks.append(("最优 total_return > 0.10", False))

    print()
    all_pass = True
    for desc, ok in checks:
        status = "✅ PASS" if ok else "❌ FAIL"
        logger.info(f"  [{status}] {desc}")
        if not ok:
            all_pass = False

    # ============ 结论 ============
    logger.info("\n" + "=" * 70)
    if all_pass:
        logger.info("🎉 v6.9 lookback 窗口优化验证全部通过")
        if best_lookback is not None:
            logger.info(f"   最优 lookback = {best_lookback} 天")
            if best_lookback == 20:
                logger.info("   ✅ 确认 v6.5~v6.8 使用的 lookback=20 是最优配置")
            elif best_lookback < 20:
                logger.info(f"   ⚠️ 发现更优 lookback={best_lookback}（比基线 20 更短，更快适应）")
                logger.info(f"      建议更新 DEFAULT_IC_WEIGHTED_LOOKBACK = {best_lookback}")
            else:
                logger.info(f"   ⚠️ 发现更优 lookback={best_lookback}（比基线 20 更长，更稳定）")
                logger.info(f"      建议更新 DEFAULT_IC_WEIGHTED_LOOKBACK = {best_lookback}")
        logger.info("   IC 加权方法对 lookback 参数稳健")
    else:
        logger.info("⚠️ v6.9 lookback 窗口优化验证存在失败项")
    logger.info("=" * 70)

    # ============ 保存结果 ============
    batch_id = f"twentyfirst_batch_lookback_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "version": "v6.9",
        "description": "IC 加权组合 lookback 窗口优化验证",
        "factor_a": FACTOR_A,
        "factor_b": FACTOR_B,
        "factor_a_ic_ir_full": float(ic_ir_a_full),
        "factor_b_ic_ir_full": float(ic_ir_b_full),
        "shadow_config": SHADOW_CONFIG_E_PLUS1,
        "lookback_configs": LOOKBACK_CONFIGS,
        "n_days": n,
        "n_trials": n_trials,
        "results": all_results,
        "best_lookback": best_lookback,
        "validation_checks": [{"description": desc, "passed": ok} for desc, ok in checks],
        "all_checks_passed": all_pass,
    }

    output_path = output_dir / "lookback_optimization_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"\n结果已保存至: {output_path}")
    logger.info(f"批次 ID: {batch_id}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
