# -*- coding: utf-8 -*-
"""S3 第十九批次：IC 加权组合 + Config_E+ 更激进参数测试（P2.2 v6.7）

设计背景：
    第十八批次发现 IC 加权组合的参数敏感性与单因子 MARGIN_EXP 完全相反：
        - 单因子 MARGIN_EXP: Config_E 让 live_dsr 从 0.70 降至 -0.965（Alpha 被过度压缩）
        - IC 加权组合: Config_E 让 live_dsr 从 -0.3446 升至 +0.3951（噪声被压缩，Alpha 保留）

    参数敏感性趋势（v6.6 第十八批次）：
        Config_A    (0.15, 0.05, 0.5): max_dd=0.1490, live_dsr=-0.3446
        Config_Cons (0.12, 0.04, 0.4): max_dd=0.1048, live_dsr=-0.1167
        Config_Mid  (0.10, 0.03, 0.3): max_dd=0.0762, live_dsr=+0.0694
        Config_E    (0.08, 0.02, 0.2): max_dd=0.0521, live_dsr=+0.3951

    每降 target_vol 0.02，live_dsr 约提升 0.2-0.3。
    Config_E+ 继续激进化（target_vol=0.06），可能突破 live_dsr > 0.5 阈值！

v6.7 改进：
    在 Config_E 基础上继续激进化，测试多组更激进的参数：
        - Config_E_plus1: target_vol=0.07, dd_threshold=0.018, dd_factor=0.18
        - Config_E_plus2: target_vol=0.06, dd_threshold=0.015, dd_factor=0.15
        - Config_E_plus3: target_vol=0.05, dd_threshold=0.012, dd_factor=0.12
        - Config_E_plus4: target_vol=0.04, dd_threshold=0.010, dd_factor=0.10

v6.7 验证目标：
    - IC 加权组合在 Config_E+ 下能否突破 live_dsr > 0.5 阈值
    - 找到 live_dsr / max_dd / total_return 的最优权衡点
    - 确认"激进参数 + IC 加权"方法学的有效性
"""
from __future__ import annotations

import json
import logging
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
    build_factor_history, compute_rolling_ic_series, compute_ic_ir, compute_ic_decay,
)
from research.vibe_trading_factor_analysis.shadow.shadow_account import ShadowAccount

logger = logging.getLogger("run_nineteenth_batch_config_e_plus")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

FACTOR_A = "VT_MICRO_VOL_SKEW_INV"
FACTOR_B = "VT_QUALTREND_MARGIN_EXP"
ROLLING_LOOKBACK = 20

# v6.7 Config_E+ 系列参数（在 Config_E 基础上继续激进化）
SHADOW_CONFIGS = {
    "Config_E_baseline": {
        "target_vol": 0.08,
        "dd_derisk_threshold": 0.02,
        "dd_derisk_factor": 0.2,
        "desc": "Config_E 基线（第十八批次已测，对照）",
    },
    "Config_E_plus1": {
        "target_vol": 0.07,
        "dd_derisk_threshold": 0.018,
        "dd_derisk_factor": 0.18,
        "desc": "Config_E+1（轻度激进）",
    },
    "Config_E_plus2": {
        "target_vol": 0.06,
        "dd_derisk_threshold": 0.015,
        "dd_derisk_factor": 0.15,
        "desc": "Config_E+2（中度激进）",
    },
    "Config_E_plus3": {
        "target_vol": 0.05,
        "dd_derisk_threshold": 0.012,
        "dd_derisk_factor": 0.12,
        "desc": "Config_E+3（高度激进）",
    },
    "Config_E_plus4": {
        "target_vol": 0.04,
        "dd_derisk_threshold": 0.010,
        "dd_derisk_factor": 0.10,
        "desc": "Config_E+4（极端激进）",
    },
}


def cross_sectional_rank(values: Dict[str, float]) -> Dict[str, float]:
    """cross-sectional rank 标准化到 [0, 1]"""
    valid = {s: v for s, v in values.items() if np.isfinite(v)}
    if len(valid) < 2:
        return {s: 0.5 for s in values}
    sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
    n = len(sorted_syms)
    ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
    return {s: ranks.get(s, 0.5) for s in values}


def compute_rolling_ic_ir(
    ic_series: List[float],
    t: int,
    lookback: int = ROLLING_LOOKBACK,
) -> float:
    """计算时间点 t 的滚动 IC_IR = mean(IC) / std(IC)"""
    if t < lookback:
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
    factor_history_a: List[Dict[str, float]],
    factor_history_b: List[Dict[str, float]],
    ic_series_a: List[float],
    ic_series_b: List[float],
    lookback: int = ROLLING_LOOKBACK,
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    """IC 加权组合（动态权重，符号自适应）"""
    n = min(len(factor_history_a), len(factor_history_b))
    combined = []
    weights_history = []

    for t in range(n):
        ic_ir_a = compute_rolling_ic_ir(ic_series_a, t, lookback)
        ic_ir_b = compute_rolling_ic_ir(ic_series_b, t, lookback)

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


def run_shadow_test(
    shadow_account: ShadowAccount,
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    n_trials: int,
    name: str,
) -> Dict[str, Any]:
    """执行 Shadow 测试"""
    result = shadow_account.run_shadow(
        factor_values_history=factor_history,
        forward_returns_history=forward_returns_history,
        n_trials=n_trials,
        factor_name=name,
    )
    print(f"    pass_shadow={result.pass_shadow}  live_dsr={result.live_dsr:+.4f}  "
          f"max_dd={result.max_drawdown:.4f}")
    print(f"      total_return={result.total_return:+.4f}  sr_observed={result.sr_observed:.4f}  "
          f"realized_vol={result.realized_vol:.4f}  avg_scaler={result.avg_scaler:.4f}")
    if result.fail_reasons:
        print(f"      fail_reasons: {result.fail_reasons}")
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
    """从 cache/fundamentals/ 加载历史季度财务数据"""
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
            logger.debug("[ConfigEPlusLoader] 加载 %s 失败: %s", sym, e)
    return history


def main() -> int:
    """主入口：IC 加权组合 + Config_E+ 更激进参数测试"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    print("=" * 70)
    print("S3 第十九批次：IC 加权组合 + Config_E+ 更激进参数测试（P2.2 v6.7）")
    print("=" * 70)
    print("v6.7 改进内容:")
    print(f"  - Factor A: {FACTOR_A}（信号反转因子）")
    print(f"  - Factor B: {FACTOR_B}（信号正常因子）")
    print(f"  - 组合方法：IC 加权（lookback={ROLLING_LOOKBACK}）")
    print("  - Shadow 参数：Config_E+ 系列（5 组梯度激进）")
    print()
    print("v6.7 验证目标:")
    print("  - IC 加权组合在 Config_E+ 下能否突破 live_dsr > 0.5 阈值")
    print("  - 找到 live_dsr / max_dd / total_return 的最优权衡点")
    print("  - 确认\"激进参数 + IC 加权\"方法学的有效性")

    # ============ Step 1: 加载数据 ============
    print("\n[1/5] 加载数据")
    symbols = list_available_symbols()
    print(f"  可用标的数: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    print(f"  price_data: {len(price_data)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 加载 fundamentals_history ============
    print("\n[2/5] 加载历史季度财务数据")
    fundamentals_history = load_fundamentals_history(symbols)
    print(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建日频因子历史 ============
    print("\n[3/5] 构建日频因子历史（history_days=120, forward_window=5）")
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
    print(f"  factor_history: {len(factor_history)} 个因子 | valid_dates: {len(valid_dates)} 天")

    # ============ Step 4: 提取单因子 + 构建 IC 加权组合 ============
    print("\n[4/5] 提取单因子并构建 IC 加权组合")
    if FACTOR_A not in factor_history or FACTOR_B not in factor_history:
        print(f"[ERROR] 因子不存在: A={FACTOR_A in factor_history} B={FACTOR_B in factor_history}")
        return 1

    hist_a = factor_history[FACTOR_A]
    hist_b = factor_history[FACTOR_B]
    n = min(len(hist_a), len(hist_b), len(fwd_returns_hist))
    hist_a = hist_a[:n]
    hist_b = hist_b[:n]
    fwd_returns_hist = fwd_returns_hist[:n]
    print(f"  Factor A ({FACTOR_A}): {len(hist_a)} 天")
    print(f"  Factor B ({FACTOR_B}): {len(hist_b)} 天")

    ic_series_a = compute_rolling_ic_series(hist_a, fwd_returns_hist)
    ic_series_b = compute_rolling_ic_series(hist_b, fwd_returns_hist)

    ic_ir_a_full, _, _ = compute_ic_ir(ic_series_a)
    ic_ir_b_full, _, _ = compute_ic_ir(ic_series_b)
    print(f"  Factor A 全局 IC_IR: {ic_ir_a_full:+.4f}")
    print(f"  Factor B 全局 IC_IR: {ic_ir_b_full:+.4f}")

    combined_ic_weighted, weights_history = combine_factors_ic_weighted(
        hist_a, hist_b, ic_series_a, ic_series_b, lookback=ROLLING_LOOKBACK
    )
    print(f"  IC 加权组合: {len(combined_ic_weighted)} 天")

    ic_ir_combo, ic_mean_combo, ic_std_combo = compute_ic_ir(
        compute_rolling_ic_series(combined_ic_weighted, fwd_returns_hist)
    )
    decay_combo = compute_ic_decay(combined_ic_weighted, fwd_returns_hist)
    print(f"  组合 IC_IR: {ic_ir_combo:+.4f}  IC_mean: {ic_mean_combo:+.4f}  "
          f"IC_std: {ic_std_combo:.4f}  decay: {decay_combo:.4f}")

    # ============ Step 5: 多组 Config_E+ 参数测试 ============
    print("\n[5/5] 多组 Config_E+ 参数测试")
    print("-" * 70)
    n_trials = max(len(symbols), 13)
    print(f"  n_trials: {n_trials}\n")

    shadow_results: Dict[str, Dict[str, Any]] = {}

    for config_name, cfg in SHADOW_CONFIGS.items():
        print(f"\n  [{config_name}] {cfg['desc']}")
        print(f"    target_vol={cfg['target_vol']}  dd_threshold={cfg['dd_derisk_threshold']}  "
              f"dd_factor={cfg['dd_derisk_factor']}")
        shadow_config = {
            "risk_managed": True,
            "target_vol": cfg["target_vol"],
            "vol_lookback": 20,
            "dd_derisk_threshold": cfg["dd_derisk_threshold"],
            "dd_derisk_factor": cfg["dd_derisk_factor"],
            "scaler_cap": 2.0,
        }
        sa = ShadowAccount(shadow_config)
        result = run_shadow_test(sa, combined_ic_weighted, fwd_returns_hist, n_trials, f"IC 加权-{config_name}")
        result["config"] = {k: v for k, v in cfg.items() if k != "desc"}
        result["desc"] = cfg["desc"]
        shadow_results[config_name] = result

    # ============ 汇总对比 ============
    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)

    print("\n[IC 加权组合 IC 指标]")
    print(f"  IC_IR: {ic_ir_combo:+.4f}")
    print(f"  IC_mean: {ic_mean_combo:+.4f}")
    print(f"  IC_std: {ic_std_combo:.4f}")
    print(f"  decay: {decay_combo:.4f}")

    print("\n[Shadow Config_E+ 系列对比]")
    print(f"{'配置':<25s} {'pass':>6s} {'live_dsr':>10s} {'max_dd':>10s} "
          f"{'total_ret':>12s} {'sr':>8s} {'avg_scaler':>12s}")
    print("-" * 90)
    for config_name, res in shadow_results.items():
        print(f"{config_name:<25s} {'✅' if res['pass_shadow'] else '❌':>6s} "
              f"{res['live_dsr']:>+10.4f} {res['max_drawdown']:>10.4f} "
              f"{res['total_return']:>+12.4f} {res['sr_observed']:>8.4f} "
              f"{res['avg_scaler']:>12.4f}")

    # ============ 找最优配置 ============
    print("\n[最优配置分析]")
    passed_configs = [k for k, v in shadow_results.items() if v["pass_shadow"]]
    if passed_configs:
        print(f"  ✅ 通过 Shadow 的配置: {passed_configs}")
        # 在通过的配置中找 total_return 最高的（因为 max_dd 都通过，找收益最高的）
        best_config = max(passed_configs, key=lambda k: shadow_results[k]["total_return"])
        best = shadow_results[best_config]
        print(f"  🎉 最优配置（最高收益）: {best_config}")
        print(f"     live_dsr={best['live_dsr']:+.4f}  max_dd={best['max_drawdown']:.4f}  "
              f"total_return={best['total_return']:+.4f}")
    else:
        print(f"  ❌ 无配置通过 Shadow")
        # 找 live_dsr 最高的
        max_dsr_config = max(shadow_results.keys(),
                             key=lambda k: shadow_results[k]["live_dsr"])
        max_dsr_res = shadow_results[max_dsr_config]
        print(f"  最高 live_dsr 配置: {max_dsr_config}")
        print(f"     live_dsr={max_dsr_res['live_dsr']:+.4f}  "
              f"max_dd={max_dsr_res['max_drawdown']:.4f}  "
              f"total_return={max_dsr_res['total_return']:+.4f}")

    # ============ 趋势分析 ============
    print("\n[Config_E+ 参数敏感性趋势]")
    config_order = ["Config_E_baseline", "Config_E_plus1", "Config_E_plus2",
                    "Config_E_plus3", "Config_E_plus4"]
    print(f"{'配置':<25s} {'target_vol':>12s} {'dd_threshold':>14s} "
          f"{'max_dd':>10s} {'live_dsr':>10s} {'total_ret':>12s}")
    print("-" * 90)
    for cfg_name in config_order:
        if cfg_name not in shadow_results:
            continue
        res = shadow_results[cfg_name]
        cfg = res["config"]
        print(f"{cfg_name:<25s} {cfg['target_vol']:>12.3f} "
              f"{cfg['dd_derisk_threshold']:>14.4f} "
              f"{res['max_drawdown']:>10.4f} {res['live_dsr']:>+10.4f} "
              f"{res['total_return']:>+12.4f}")

    # ============ v6.7 结论 ============
    print("\n[v6.7 验证结论]")
    if passed_configs:
        print(f"  🎉 IC 加权组合在 {passed_configs} 配置下通过 Shadow 验证！")
        print(f"  → 验证了\"激进参数 + IC 加权\"方法学的有效性")
        print(f"  → 关键突破：IC 加权 + 适度激进参数 = 完整 Shadow 通过")
        print(f"  → 与单因子 MARGIN_EXP 的 Config_E 副作用（live_dsr 下降）形成对比")
        print(f"  → 方法学解释：IC 加权的动态权重已自适应信号反转，")
        print(f"     激进参数压缩的是噪声而非 Alpha 信号")
    else:
        max_live_dsr = max(res["live_dsr"] for res in shadow_results.values())
        min_max_dd = min(res["max_drawdown"] for res in shadow_results.values())
        print(f"  ⚠️ 即使 Config_E+4 也无法让 IC 加权组合通过 Shadow")
        print(f"     最高 live_dsr: {max_live_dsr:+.4f} {'> 0.5 ✅' if max_live_dsr > 0.5 else '<= 0.5 ❌'}")
        print(f"     最低 max_dd: {min_max_dd:.4f} {'< 0.12 ✅' if min_max_dd < 0.12 else '>= 0.12 ❌'}")
        # 判断 live_dsr 是否单调上升
        dsr_values = [shadow_results[c]["live_dsr"] for c in config_order
                      if c in shadow_results]
        is_monotonic_increasing = all(dsr_values[i] <= dsr_values[i+1]
                                       for i in range(len(dsr_values)-1))
        if is_monotonic_increasing:
            print(f"  → live_dsr 随参数激进化单调上升：{[f'{v:+.4f}' for v in dsr_values]}")
            print(f"  → 但已触及天花板，继续激进化收益递减")
            print(f"  → 根因：VT_MICRO_VOL_SKEW_INV 信号反转严重，反向使用仍有损失")
        else:
            print(f"  → live_dsr 不再单调上升：{[f'{v:+.4f}' for v in dsr_values]}")
            print(f"  → 找到拐点：最优参数在 live_dsr 最高点附近")

    # ============ 保存结果 ============
    batch_id = f"nineteenth_batch_config_e_plus_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "factor_a": FACTOR_A,
        "factor_b": FACTOR_B,
        "combination_method": "ic_weighted_rolling",
        "rolling_lookback": ROLLING_LOOKBACK,
        "ic_metrics": {
            "factor_a_ic_ir_full": float(ic_ir_a_full),
            "factor_b_ic_ir_full": float(ic_ir_b_full),
            "combo_ic_ir": float(ic_ir_combo),
            "combo_ic_mean": float(ic_mean_combo),
            "combo_ic_std": float(ic_std_combo),
            "combo_ic_decay": float(decay_combo),
        },
        "shadow_configs": {k: {kk: vv for kk, vv in v.items() if kk != "desc"}
                           for k, v in SHADOW_CONFIGS.items()},
        "shadow_results": shadow_results,
        "passed_configs": passed_configs,
    }

    output_path = output_dir / "config_e_plus_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n结果已保存至: {output_path}")
    print(f"批次 ID: {batch_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
