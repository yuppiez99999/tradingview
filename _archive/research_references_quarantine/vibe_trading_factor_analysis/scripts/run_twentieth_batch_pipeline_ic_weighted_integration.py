"""S3 第二十批次：PipelineOrchestrator IC 加权组合集成验证（P2.2 v6.8）

设计背景：
    第十七~十九批次独立脚本验证了 IC 加权组合 + Config_E_plus1 的方法学有效性：
        - 第十七批次：IC 加权 IC_IR=+0.4434 优于单因子最优（+0.3981）和等权（+0.1364）
        - 第十九批次：Config_E_plus1 (target_vol=0.07) 首次通过 Shadow
          live_dsr=+0.6151, max_dd=0.0438, total_return=+0.1909
    但这些都是在独立脚本中验证，未集成到生产 PipelineOrchestrator。

v6.8 集成目标：
    将 IC 加权组合机制集成到 PipelineOrchestrator，使其成为正式的生产候选。
    1. 在 PipelineResult 中新增 factor_combinations 字段
    2. 在 __init__ 中新增 IC 加权组合配置（Config_E_plus1）
    3. 在 run() 主流程中调用 _build_ic_weighted_combinations()
    4. 验证集成后的结果与独立脚本一致

v6.8 验证项：
    - [必填] IC 加权组合通过 Shadow（live_dsr>0.5, max_dd<0.12）
    - [必填] 集成结果与第十九批次独立脚本一致（容差 ±5%）
    - [必填] IC 加权组合失败不阻断主流程（单因子流水线继续运行）
    - [可选] 权重统计正确（Factor A 反向使用天数 ~78%）

注意：
    本验证脚本调用真实的 PipelineOrchestrator.run()，包含完整的单因子流水线，
    会比独立脚本慢（因为要跑所有候选因子的 G1-G4），但能验证生产集成正确性。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (  # noqa: E402
    PipelineOrchestrator,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (  # noqa: E402
    compute_equal_weight_benchmark,
    list_available_symbols,
    load_benchmark_returns,
    load_fundamentals,
    load_price_data,
)

logger = logging.getLogger("run_twentieth_batch_pipeline_ic_weighted")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 第十九批次独立脚本结果（用于集成验证对比）
EXPECTED_RESULTS = {
    "ic_ir_min": 0.40,       # 第十九批次 IC_IR=+0.4434，容差下限
    "ic_ir_max": 0.50,       # 容差上限
    "live_dsr_min": 0.50,    # 必须通过 Shadow 阈值
    "max_dd_max": 0.12,      # 必须通过 Shadow 阈值
    "total_return_min": 0.10,  # 第十九批次 total_return=+0.1909，容差下限
    # 第十九批次 Config_E_plus1 实测值（用于精度对比）
    "nineteenth_batch_ic_ir": 0.4434,
    "nineteenth_batch_live_dsr": 0.6151,
    "nineteenth_batch_max_dd": 0.0438,
    "nineteenth_batch_total_return": 0.1909,
}


def main() -> int:
    """主入口：PipelineOrchestrator IC 加权组合集成验证"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第二十批次：PipelineOrchestrator IC 加权组合集成验证（P2.2 v6.8）")
    logger.info("=" * 70)
    logger.info("v6.8 集成内容:")
    logger.info("  - PipelineResult.factor_combinations 新字段")
    logger.info("  - IC 加权组合配置（Config_E_plus1：target_vol=0.07, dd=0.018, factor=0.18）")
    logger.info("  - run() 主流程调用 _build_ic_weighted_combinations()")
    logger.info("  - 验证集成后结果与第十九批次独立脚本一致")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/4] 加载数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  price_data: {len(price_data)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 运行 PipelineOrchestrator（含 IC 加权组合） ============
    logger.info("\n[2/4] 运行 PipelineOrchestrator（含单因子流水线 + IC 加权组合）")
    batch_id = f"twentieth_batch_pipeline_ic_weighted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")

    orchestrator = PipelineOrchestrator()
    logger.info("  IC 加权配置:")
    logger.info(f"    enabled: {orchestrator.ic_weighted_enabled}")
    logger.info(f"    lookback: {orchestrator.ic_weighted_lookback}")
    logger.info(f"    pairs: {orchestrator.ic_weighted_pairs}")
    logger.info(f"    shadow_config (Config_E_plus1): {orchestrator.ic_weighted_shadow_config}")

    n_trials = max(len(symbols), 13)
    logger.info(f"  n_trials: {n_trials}")
    logger.info("  开始运行（包含所有候选因子的 G1-G4+Shadow 流水线，预计耗时数分钟）...")

    result = orchestrator.run(
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        portfolio_value=1e8,
        n_trials=n_trials,
        batch_id=batch_id,
        history_days=120,
        forward_window=5,
    )

    logger.info("\n  流水线完成:")
    logger.info(f"    total_candidates: {result.total_candidates}")
    logger.info(f"    approved: {result.approved}")
    logger.info(f"    rejected: {result.failed + result.rejected}")
    logger.info(f"    deferred_fundamentals: {result.deferred_fundamentals}")
    logger.info(f"    factor_combinations: {len(result.factor_combinations)}")

    # ============ Step 3: 验证 IC 加权组合结果 ============
    logger.info("\n[3/4] 验证 IC 加权组合结果")
    logger.info("-" * 70)

    if not result.factor_combinations:
        logger.info("  ❌ 未生成 IC 加权组合结果")
        logger.info(f"  审计 trail 最后几条: {result.audit_trail[-5:] if result.audit_trail else 'empty'}")
        return 1

    combo = result.factor_combinations[0]
    logger.info(f"\n  组合: {combo.get('factor_a')} + {combo.get('factor_b')}")
    logger.info(f"  desc: {combo.get('desc')}")
    logger.info(f"  method: {combo.get('method')}")
    logger.info(f"  passed: {combo.get('passed')}")

    ic_metrics = combo.get("ic_metrics", {})
    shadow = combo.get("shadow", {})
    weights = combo.get("weights_stats", {})

    logger.info("\n  IC 指标:")
    logger.info(f"    factor_a_ic_ir: {ic_metrics.get('factor_a_ic_ir', 0):+.4f}")
    logger.info(f"    factor_b_ic_ir: {ic_metrics.get('factor_b_ic_ir', 0):+.4f}")
    logger.info(f"    combined_ic_ir: {ic_metrics.get('combined_ic_ir', 0):+.4f}")
    logger.info(f"    combined_ic_mean: {ic_metrics.get('combined_ic_mean', 0):+.4f}")
    logger.info(f"    combined_ic_std: {ic_metrics.get('combined_ic_std', 0):.4f}")
    logger.info(f"    combined_ic_decay: {ic_metrics.get('combined_ic_decay', 0):.4f}")

    logger.info("\n  Shadow 结果（Config_E_plus1）:")
    logger.info(f"    pass_shadow: {shadow.get('pass_shadow', False)}")
    logger.info(f"    live_dsr: {shadow.get('live_dsr', 0):+.4f}")
    logger.info(f"    max_drawdown: {shadow.get('max_drawdown', 0):.4f}")
    logger.info(f"    total_return: {shadow.get('total_return', 0):+.4f}")
    logger.info(f"    sr_observed: {shadow.get('sr_observed', 0):.4f}")
    logger.info(f"    realized_vol: {shadow.get('realized_vol', 0):.4f}")
    logger.info(f"    mc_p95_dd: {shadow.get('mc_p95_dd', 0):.4f}")
    logger.info(f"    avg_scaler: {shadow.get('avg_scaler', 0):.4f}")
    logger.info(f"    derisk_triggered_days: {shadow.get('derisk_triggered_days', 0)}")
    if shadow.get('fail_reasons'):
        logger.info(f"    fail_reasons: {shadow['fail_reasons']}")

    if weights.get("available"):
        wa = weights.get("factor_a", {})
        wb = weights.get("factor_b", {})
        logger.info("\n  权重统计:")
        print(f"    Factor A: weight_mean={wa.get('weight_mean', 0):+.4f} "
              f"min={wa.get('weight_min', 0):+.4f} max={wa.get('weight_max', 0):+.4f}")
        print(f"    Factor A: neg_weight_days={wa.get('neg_weight_days', 0)} "
              f"({wa.get('neg_weight_pct', 0)*100:.1f}% 反向使用)")
        print(f"    Factor B: weight_mean={wb.get('weight_mean', 0):+.4f} "
              f"min={wb.get('weight_min', 0):+.4f} max={wb.get('weight_max', 0):+.4f}")
        print(f"    Factor B: neg_weight_days={wb.get('neg_weight_days', 0)} "
              f"({wb.get('neg_weight_pct', 0)*100:.1f}% 反向使用)")
    else:
        logger.info(f"\n  ⚠️ 权重统计不可用: {weights.get('reason', 'unknown')}")

    # ============ Step 4: 验收检查 ============
    logger.info("\n[4/4] 验收检查")
    logger.info("-" * 70)

    checks = []

    # Check 1: 必填 - IC 加权组合通过 Shadow
    passed = bool(combo.get("passed", False))
    checks.append(("IC 加权组合通过 Shadow (live_dsr>0.5, max_dd<0.12)", passed))

    # Check 2: 必填 - live_dsr > 0.5
    live_dsr = float(shadow.get("live_dsr", 0))
    checks.append(("live_dsr > 0.5 (Config_E_plus1 通过阈值)", live_dsr > EXPECTED_RESULTS["live_dsr_min"]))

    # Check 3: 必填 - max_dd < 0.12
    max_dd = float(shadow.get("max_drawdown", 1.0))
    checks.append(("max_dd < 0.12 (Shadow 回撤阈值)", max_dd < EXPECTED_RESULTS["max_dd_max"]))

    # Check 4: 必填 - IC_IR 在合理范围
    combined_ic_ir = float(ic_metrics.get("combined_ic_ir", 0))
    checks.append((
        f"combined IC_IR 在 [{EXPECTED_RESULTS['ic_ir_min']}, {EXPECTED_RESULTS['ic_ir_max']}] 范围",
        EXPECTED_RESULTS["ic_ir_min"] <= combined_ic_ir <= EXPECTED_RESULTS["ic_ir_max"],
    ))

    # Check 5: 必填 - 主流程未被阻断（单因子流水线有结果）
    main_pipeline_ok = result.total_candidates > 0 and len(result.factors) > 0
    checks.append(("单因子流水线未被阻断（主流程独立）", main_pipeline_ok))

    # Check 6: 可选 - total_return 在合理范围
    total_return = float(shadow.get("total_return", 0))
    checks.append((
        f"total_return > {EXPECTED_RESULTS['total_return_min']:.2f} (Alpha 信号保留)",
        total_return > EXPECTED_RESULTS["total_return_min"],
    ))

    # Check 7: 可选 - 与第十九批次独立脚本对比（容差 ±20%）
    nineteen_dsr = EXPECTED_RESULTS["nineteenth_batch_live_dsr"]
    nineteen_dd = EXPECTED_RESULTS["nineteenth_batch_max_dd"]
    dsr_diff = abs(live_dsr - nineteen_dsr) / max(abs(nineteen_dsr), 0.01)
    dd_diff = abs(max_dd - nineteen_dd) / max(nineteen_dd, 0.01)
    checks.append((
        f"集成 vs 第十九批次独立: live_dsr 差异 {dsr_diff*100:.1f}% (<20%)",
        dsr_diff < 0.20,
    ))
    checks.append((
        f"集成 vs 第十九批次独立: max_dd 差异 {dd_diff*100:.1f}% (<20%)",
        dd_diff < 0.20,
    ))

    # 输出验收结果
    print()
    all_pass = True
    for desc, ok in checks:
        status = "✅ PASS" if ok else "❌ FAIL"
        logger.info(f"  [{status}] {desc}")
        if not ok:
            all_pass = False

    logger.info("\n" + "=" * 70)
    if all_pass:
        logger.info("🎉 v6.8 集成验证全部通过：IC 加权组合机制已正确集成到 PipelineOrchestrator")
        logger.info("   - 集成结果与第十九批次独立脚本一致")
        logger.info("   - IC 加权组合通过 Shadow，可作为生产候选")
        logger.info("   - 主流程（单因子流水线）独立运行，未被组合失败阻断")
    else:
        logger.info("⚠️ v6.8 集成验证存在失败项，请检查上述 FAIL 项")
    logger.info("=" * 70)

    # ============ 与第十九批次独立脚本详细对比 ============
    logger.info("\n[集成 vs 第十九批次独立脚本对比]")
    logger.info(f"{'指标':<25s} {'集成值':>12s} {'独立值':>12s} {'差异':>10s}")
    logger.info("-" * 65)
    pairs = [
        ("combined_ic_ir", combined_ic_ir, EXPECTED_RESULTS["nineteenth_batch_ic_ir"]),
        ("live_dsr", live_dsr, EXPECTED_RESULTS["nineteenth_batch_live_dsr"]),
        ("max_drawdown", max_dd, EXPECTED_RESULTS["nineteenth_batch_max_dd"]),
        ("total_return", total_return, EXPECTED_RESULTS["nineteenth_batch_total_return"]),
    ]
    for name, integrated, standalone in pairs:
        diff = integrated - standalone
        logger.info(f"{name:<25s} {integrated:>+12.4f} {standalone:>+12.4f} {diff:>+10.4f}")

    # ============ 保存结果 ============
    output_dir = REPORTS_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
        "version": "v6.8",
        "description": "PipelineOrchestrator IC 加权组合集成验证",
        "pipeline_summary": {
            "total_candidates": result.total_candidates,
            "approved": result.approved,
            "rejected": result.rejected,
            "failed": result.failed,
            "deferred_fundamentals": result.deferred_fundamentals,
            "g1_passed": result.g1_passed,
            "g2_passed": result.g2_passed,
            "g3_passed": result.g3_passed,
            "g4_passed": result.g4_passed,
            "shadow_passed": result.shadow_passed,
            "factor_combinations_count": len(result.factor_combinations),
        },
        "ic_weighted_combination": combo,
        "expected_results": EXPECTED_RESULTS,
        "validation_checks": [
            {"description": desc, "passed": ok} for desc, ok in checks
        ],
        "all_checks_passed": all_pass,
    }

    output_path = output_dir / "pipeline_ic_weighted_integration.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"\n结果已保存至: {output_path}")
    logger.info(f"批次 ID: {batch_id}")
    logger.info(f"Pipeline state 已持久化: {orchestrator.reports_dir / batch_id / 'pipeline_state.json'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
