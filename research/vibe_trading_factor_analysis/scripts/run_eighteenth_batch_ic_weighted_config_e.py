"""S3 第十八批次：IC 加权组合 + Config_E 超激进参数测试（P2.2 v6.6）

设计背景：
    第十七批次 IC 加权组合 IC_IR=+0.4434（优于单因子最优 +0.3981），但 Shadow 未通过：
        - live_dsr=-0.3446（远低于 0.5 阈值）
        - max_dd=0.1490（略超 0.12 阈值）
        - total_return=+0.2744（收益保留较好）

根因分析：
    - VT_MICRO_VOL_SKEW_INV 信号反转严重，IC 加权虽能部分适应，但反向使用仍有损失
    - Config_A 基线参数（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5）
      对单因子 MARGIN_EXP 都无法控制回撤（max_dd=20.42%），对组合也不够激进

v6.6 改进：
    借鉴第十四批次 MARGIN_EXP 用 Config_E 通过 Shadow 的经验，
    用 Config_E 超激进参数测试 IC 加权组合：
        - Config_A 基线（对照）：target_vol=0.15, dd_threshold=0.05, dd_factor=0.5
        - Config_E 超激进：target_vol=0.08, dd_threshold=0.02, dd_factor=0.2
        - Config_Mid 中间：target_vol=0.10, dd_threshold=0.03, dd_factor=0.3
        - Config_Conservative 保守：target_vol=0.12, dd_threshold=0.04, dd_factor=0.4

v6.6 验证目标：
    - IC 加权组合在 Config_E 下能否通过 Shadow（live_dsr > 0.5, max_dd < 0.12）
    - 不同参数配置下的 max_dd / live_dsr / total_return 权衡
    - 找到 IC 加权组合的最优 Shadow 参数
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

logger = logging.getLogger("run_eighteenth_batch_ic_weighted_config_e")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 验证的因子（与第十七批次一致）
FACTOR_A = "VT_MICRO_VOL_SKEW_INV"
FACTOR_B = "VT_QUALTREND_MARGIN_EXP"
ROLLING_LOOKBACK = 20  # 滚动 IC_IR 回看窗口

# v6.6 多组 Shadow 参数配置（借鉴第十四批次 MARGIN_EXP 成功经验）
SHADOW_CONFIGS = {
    "Config_A_baseline": {
        "target_vol": 0.15,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "desc": "基线（第十七批次已测，对照）",
    },
    "Config_Conservative": {
        "target_vol": 0.12,
        "dd_derisk_threshold": 0.04,
        "dd_derisk_factor": 0.4,
        "desc": "保守方案（适度激进）",
    },
    "Config_Mid": {
        "target_vol": 0.10,
        "dd_derisk_threshold": 0.03,
        "dd_derisk_factor": 0.3,
        "desc": "中间方案",
    },
    "Config_E_aggressive": {
        "target_vol": 0.08,
        "dd_derisk_threshold": 0.02,
        "dd_derisk_factor": 0.2,
        "desc": "超激进（MARGIN_EXP 成功经验）",
    },
}


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
    print(f"    pass_shadow={result.pass_shadow}  live_dsr={result.live_dsr:+.4f}  "
          f"max_dd={result.max_drawdown:.4f}")
    print(f"      total_return={result.total_return:+.4f}  sr_observed={result.sr_observed:.4f}  "
          f"realized_vol={result.realized_vol:.4f}  avg_scaler={result.avg_scaler:.4f}")
    if result.fail_reasons:
        logger.info(f"      fail_reasons: {result.fail_reasons}")
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
            logger.debug("[ConfigELoader] 加载 %s 失败: %s", sym, e)
    return history


def main() -> int:
    """主入口：IC 加权组合 + Config_E 超激进参数测试"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第十八批次：IC 加权组合 + Config_E 超激进参数测试（P2.2 v6.6）")
    logger.info("=" * 70)
    logger.info("v6.6 改进内容:")
    logger.info(f"  - Factor A: {FACTOR_A}（信号反转因子）")
    logger.info(f"  - Factor B: {FACTOR_B}（信号正常因子）")
    logger.info(f"  - 组合方法：IC 加权（lookback={ROLLING_LOOKBACK}）")
    logger.info("  - Shadow 参数：4 组对比（Config_A → Config_E 梯度激进）")
    print()
    logger.info("v6.6 验证目标:")
    logger.info("  - IC 加权组合在 Config_E 下能否通过 Shadow（live_dsr > 0.5, max_dd < 0.12）")
    logger.info("  - 不同参数下的 max_dd / live_dsr / total_return 权衡")
    logger.info("  - 找到 IC 加权组合的最优 Shadow 参数")

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

    # ============ Step 4: 提取单因子 + 构建 IC 加权组合 ============
    logger.info("\n[4/5] 提取单因子并构建 IC 加权组合")
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

    # IC 加权组合
    combined_ic_weighted, _weights_history = combine_factors_ic_weighted(
        hist_a, hist_b, ic_series_a, ic_series_b, lookback=ROLLING_LOOKBACK
    )
    logger.info(f"  IC 加权组合: {len(combined_ic_weighted)} 天")

    # 组合 IC 指标
    ic_ir_combo, ic_mean_combo, ic_std_combo = compute_ic_ir(
        compute_rolling_ic_series(combined_ic_weighted, fwd_returns_hist)
    )
    decay_combo = compute_ic_decay(combined_ic_weighted, fwd_returns_hist)
    print(f"  组合 IC_IR: {ic_ir_combo:+.4f}  IC_mean: {ic_mean_combo:+.4f}  "
          f"IC_std: {ic_std_combo:.4f}  decay: {decay_combo:.4f}")

    # ============ Step 5: 多组 Shadow 参数测试 ============
    logger.info("\n[5/5] 多组 Shadow 参数测试")
    logger.info("-" * 70)
    n_trials = max(len(symbols), 13)
    logger.info(f"  n_trials: {n_trials}\n")

    shadow_results: dict[str, dict[str, Any]] = {}

    for config_name, cfg in SHADOW_CONFIGS.items():
        logger.info(f"\n  [{config_name}] {cfg['desc']}")
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
        result = run_shadow_test(sa, combined_ic_weighted, fwd_returns_hist, n_trials, f"IC 加权组合-{config_name}")
        result["config"] = {k: v for k, v in cfg.items() if k != "desc"}
        result["desc"] = cfg["desc"]
        shadow_results[config_name] = result

    # ============ 汇总对比 ============
    logger.info("\n" + "=" * 70)
    logger.info("汇总对比")
    logger.info("=" * 70)

    logger.info("\n[IC 加权组合 IC 指标]")
    logger.info(f"  IC_IR: {ic_ir_combo:+.4f}")
    logger.info(f"  IC_mean: {ic_mean_combo:+.4f}")
    logger.info(f"  IC_std: {ic_std_combo:.4f}")
    logger.info(f"  decay: {decay_combo:.4f}")

    logger.info("\n[Shadow 多配置对比]")
    print(f"{'配置':<25s} {'pass':>6s} {'live_dsr':>10s} {'max_dd':>10s} "
          f"{'total_ret':>12s} {'sr':>8s} {'avg_scaler':>12s}")
    logger.info("-" * 90)
    for config_name, res in shadow_results.items():
        print(f"{config_name:<25s} {'✅' if res['pass_shadow'] else '❌':>6s} "
              f"{res['live_dsr']:>+10.4f} {res['max_drawdown']:>10.4f} "
              f"{res['total_return']:>+12.4f} {res['sr_observed']:>8.4f} "
              f"{res['avg_scaler']:>12.4f}")

    # ============ 找最优配置 ============
    logger.info("\n[最优配置分析]")
    # 优先：通过 Shadow 的配置
    passed_configs = [k for k, v in shadow_results.items() if v["pass_shadow"]]
    if passed_configs:
        logger.info(f"  ✅ 通过 Shadow 的配置: {passed_configs}")
        # 在通过的配置中找 live_dsr 最高的
        best_config = max(passed_configs, key=lambda k: shadow_results[k]["live_dsr"])
        best = shadow_results[best_config]
        logger.info(f"  🎉 最优配置: {best_config}")
        print(f"     live_dsr={best['live_dsr']:+.4f}  max_dd={best['max_drawdown']:.4f}  "
              f"total_return={best['total_return']:+.4f}")
    else:
        logger.info("  ❌ 无配置通过 Shadow")
        # 找 max_dd 最低的
        min_dd_config = min(shadow_results.keys(),
                            key=lambda k: shadow_results[k]["max_drawdown"])
        min_dd_res = shadow_results[min_dd_config]
        logger.info(f"  最低 max_dd 配置: {min_dd_config}")
        print(f"     max_dd={min_dd_res['max_drawdown']:.4f}  "
              f"live_dsr={min_dd_res['live_dsr']:+.4f}  "
              f"total_return={min_dd_res['total_return']:+.4f}")
        # 找 live_dsr 最高的
        max_dsr_config = max(shadow_results.keys(),
                             key=lambda k: shadow_results[k]["live_dsr"])
        max_dsr_res = shadow_results[max_dsr_config]
        logger.info(f"  最高 live_dsr 配置: {max_dsr_config}")
        print(f"     live_dsr={max_dsr_res['live_dsr']:+.4f}  "
              f"max_dd={max_dsr_res['max_drawdown']:.4f}  "
              f"total_return={max_dsr_res['total_return']:+.4f}")

    # ============ 趋势分析 ============
    logger.info("\n[参数敏感性趋势分析]")
    config_order = ["Config_A_baseline", "Config_Conservative", "Config_Mid", "Config_E_aggressive"]
    print(f"{'配置':<25s} {'target_vol':>12s} {'dd_threshold':>14s} "
          f"{'max_dd':>10s} {'live_dsr':>10s} {'total_ret':>12s}")
    logger.info("-" * 90)
    for cfg_name in config_order:
        if cfg_name not in shadow_results:
            continue
        res = shadow_results[cfg_name]
        cfg = res["config"]
        print(f"{cfg_name:<25s} {cfg['target_vol']:>12.2f} "
              f"{cfg['dd_derisk_threshold']:>14.2f} "
              f"{res['max_drawdown']:>10.4f} {res['live_dsr']:>+10.4f} "
              f"{res['total_return']:>+12.4f}")
    logger.info("\n  观察要点:")
    logger.info("  1. target_vol 越低 → max_dd 越低（缩放更激进）")
    logger.info("  2. dd_threshold 越低 → 触发去杠杆越早（max_dd 控制更好）")
    logger.info("  3. dd_factor 越低 → 去杠杆幅度越大（敞口降得更多）")
    logger.info("  4. 但过度激进 → Alpha 信号被压缩（live_dsr 可能下降）")
    logger.info("  5. 需要在 max_dd 控制和 live_dsr 保持之间找平衡")

    # ============ v6.6 结论 ============
    logger.info("\n[v6.6 验证结论]")
    if passed_configs:
        logger.info(f"  🎉 IC 加权组合在 {passed_configs} 配置下通过 Shadow 验证")
        logger.info("  → IC 加权组合 + 超激进参数 = 完整 Shadow 通过")
        logger.info("  → 验证了 IC 加权方法学的有效性（适应信号反转 + 风险管理控制回撤）")
    else:
        best_max_dd = min(res["max_drawdown"] for res in shadow_results.values())
        best_live_dsr = max(res["live_dsr"] for res in shadow_results.values())
        logger.info("  ⚠️ 即使 Config_E 超激进参数也无法让 IC 加权组合通过 Shadow")
        logger.info(f"     最低 max_dd: {best_max_dd:.4f} {'< 0.12 ✅' if best_max_dd < 0.12 else '>= 0.12 ❌'}")
        logger.info(f"     最高 live_dsr: {best_live_dsr:+.4f} {'> 0.5 ✅' if best_live_dsr > 0.5 else '<= 0.5 ❌'}")
        logger.info("  → 根因可能是 VT_MICRO_VOL_SKEW_INV 信号反转过于严重")
        logger.info("  → 后续可尝试：剔除 VT_MICRO_VOL_SKEW_INV，只用 MARGIN_EXP 单因子")
        logger.info("  → 或寻找其他与 MARGIN_EXP 信号方向一致的因子组合")

    # ============ 保存结果 ============
    batch_id = f"eighteenth_batch_ic_weighted_config_e_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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

    output_path = output_dir / "ic_weighted_config_e_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"\n结果已保存至: {output_path}")
    logger.info(f"批次 ID: {batch_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
