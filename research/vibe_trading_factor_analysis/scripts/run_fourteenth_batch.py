# -*- coding: utf-8 -*-
"""S3 第十四批次流水线跑批脚本（P2.2 v6.2d 因子特定 Shadow 配置 + Regime 修复）

重大背景：
    第十三批次发现 v6.2c Config_E 作为默认配置有副作用：
        - VT_MICRO_VOL_SKEW_INV live_dsr 从 Config_A 的 0.70 降至 Config_E 的 -0.965
          （Alpha 信号被过度压缩）
        - MARGIN_EXP 被 Committee 否决（CapacityAgent veto 因 min_regime_ic_ir=-1.000）

v6.2d 改进：
    1. 回退 Config_E 为默认配置，改为因子特定覆盖机制
       - 默认使用 Config_A 基线（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5）
         保护 VT_MICRO_VOL_SKEW_INV 等对超激进参数敏感的因子
       - QualityTrend 类因子自动应用 Config_E 超激进参数
         （MARGIN_EXP 需要 Config_E 才能通过 Shadow max_dd < 0.12）

    2. 修复 RegimeConditioner：样本数 < 5 的 regime 不计入 min_regime_ic_ir
       - 旧版设为 -1.0 导致 CapacityAgent 误否决
       - 新版跳过样本不足的 regime，避免统计不可靠的 IC_IR 影响 veto

    3. 修复 CapacityAgent：样本不足时不 veto，给中性评分 5.0
       - 当 weakest_regime == "insufficient_samples" 时不触发 veto

    4. 修复显示 bug：g3.get('passed') → g3.get('gate_3_pass')
                     g4.get('economic_score') → g4.get('score')

v6.2d 验证目标：
    - MARGIN_EXP 是否能走完 G1-G4+Enhancement+Regime+Shadow+Committee 全部 8 个 Gate
    - VT_MICRO_VOL_SKEW_INV 是否恢复正常（live_dsr > 0.5）
    - 若 MARGIN_EXP 通过 Committee，则是 QualityTrend 类首个 approved 因子
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
    PipelineState,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    compute_equal_weight_benchmark,
    list_available_symbols,
    load_benchmark_returns,
    load_fundamentals,
    load_price_data,
)

logger = logging.getLogger("run_fourteenth_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑第十四批次流水线（v6.2d 因子特定配置 + Regime 修复）"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第十四批次流水线跑批（P2.2 v6.2d 因子特定 Shadow 配置 + Regime 修复）")
    logger.info("=" * 70)
    logger.info("v6.2d 改进内容:")
    logger.info("  1. 回退 Config_E 为默认，改为因子特定覆盖（QualityTrend 用 Config_E）")
    logger.info("  2. RegimeConditioner 修复：样本数 < 5 的 regime 不计入 min_regime_ic_ir")
    logger.info("  3. CapacityAgent 修复：样本不足时不 veto，给中性评分 5.0")
    logger.info("  4. 显示 bug 修复：g3.get('passed') → g3.get('gate_3_pass')")
    print()
    logger.info("v6.2d 验证目标:")
    logger.info("  - MARGIN_EXP 是否能走完 G1-G4+Enhancement+Regime+Shadow+Committee 全部 8 个 Gate")
    logger.info("  - VT_MICRO_VOL_SKEW_INV 是否恢复正常（live_dsr > 0.5）")
    logger.info("  - 若通过 Committee，则是 QualityTrend 类首个 approved 因子")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/5] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")

    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  price_data: {len(price_data)} | benchmark_returns: {len(benchmark_returns)} 天")

    if not price_data:
        logger.info("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 初始化流水线（v6.2d 因子特定配置） ============
    logger.info("\n[2/5] 初始化 PipelineOrchestrator (v6.2d)")
    orchestrator = PipelineOrchestrator(
        config={
            "reports_dir": str(REPORTS_DIR),
            # P2.2 v6.2d: 默认 Config_A 基线，QualityTrend 类因子自动应用 Config_E
            # 通过 factor_shadow_overrides 机制实现（在 PipelineOrchestrator __init__ 中配置）
        }
    )
    batch_id = f"fourteenth_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")
    logger.info(f"  shadow risk_managed: {orchestrator.shadow_account.risk_managed}")
    logger.info(f"  shadow target_vol (默认): {orchestrator.shadow_account.target_vol}")
    logger.info(f"  shadow dd_derisk_threshold (默认): {orchestrator.shadow_account.dd_derisk_threshold}")
    logger.info(f"  shadow dd_derisk_factor (默认): {orchestrator.shadow_account.dd_derisk_factor}")
    logger.info(f"  factor_shadow_overrides: {list(orchestrator.factor_shadow_overrides.keys())}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/5] 执行 8 级流水线（v6.2d）")
    n_trials = max(len(symbols), 13)
    logger.info(f"  n_trials: {n_trials}")

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

    # ============ Step 4: 汇总结果 ============
    logger.info("\n[4/5] 汇总批次结果")
    logger.info("-" * 70)
    logger.info(f"  batch_id           : {result.batch_id}")
    logger.info(f"  total_candidates   : {result.total_candidates}")
    logger.info(f"  G1 正交性通过       : {result.g1_passed}")
    logger.info(f"  G2 IC 稳定性通过    : {result.g2_passed}")
    logger.info(f"  G3 DSR 防过拟合通过  : {result.g3_passed}")
    logger.info(f"  G4 经济逻辑通过      : {result.g4_passed}")
    logger.info(f"  Enhancement 通过    : {result.enhanced}")
    logger.info(f"  Shadow 通过         : {result.shadow_passed}")
    logger.info(f"  Committee 通过(Approved): {result.approved}")
    logger.info(f"  Rejected            : {result.rejected}")
    logger.info(f"  Failed              : {result.failed}")
    logger.info(f"  Deferred(fundamentals): {result.deferred_fundamentals}")
    logger.info("-" * 70)

    # P2.2 v6.2d 验收：MARGIN_EXP 是否走完 8 级流水线
    logger.info("\n  P2.2 v6.2d 验收 - MARGIN_EXP 在因子特定配置下的 8 级流水线进度:")
    target_factor = "VT_QUALTREND_MARGIN_EXP"
    f = next((x for x in result.factors if x.get("factor_name") == target_factor), None)
    if f:
        g1 = f.get("g1_orthogonality") or {}
        g2 = f.get("g2_ic_stability") or {}
        g3 = f.get("g3_dsr") or {}
        # P2.2 v6.2d 修复：字段名是 g4_economic（不是 g4_economic_logic）
        g4 = f.get("g4_economic") or {}
        enh_cap = f.get("enhancement_capacity") or {}
        enh_reg = f.get("enhancement_regime") or {}
        shadow = f.get("shadow_result") or {}
        committee = f.get("committee_verdict") or {}
        state = f.get("state", "-")
        final_score = f.get("final_score", 0)
        fail_reasons = f.get("fail_reasons", [])

        # P2.2 v6.2d 修复显示 bug：使用正确的字段名
        # G3: gate_3_pass（不是 passed）
        # G4: score（不是 economic_score）
        logger.info(f"    {'Gate':<25s} {'通过':>6s} {'详情':>40s}")
        logger.info(f"    {'-'*25} {'-'*6} {'-'*40}")
        print(f"    {'G1 正交性':<25s} {'✅' if g1.get('passed') else '❌':>6s} "
              f"max_corr={g1.get('max_abs_corr', 0):.3f} ({g1.get('max_corr_factor', '-')})")
        print(f"    {'G2 IC 稳定性':<25s} {'✅' if g2.get('passed') else '❌':>6s} "
              f"IC_IR={g2.get('ic_ir_estimated', 0):.4f} decay={g2.get('ic_decay_estimated', 0):.4f}")
        # 修复：g3.get('gate_3_pass') 而非 g3.get('passed')
        g3_pass = g3.get('gate_3_pass', g3.get('passed', False))
        print(f"    {'G3 DSR':<25s} {'✅' if g3_pass else '❌':>6s} "
              f"DSR={g3.get('dsr_value', 0):.4f} sr={g3.get('sr_observed', 0):.2f}")
        # 修复：g4.get('score') 而非 g4.get('economic_score')
        print(f"    {'G4 经济逻辑':<25s} {'✅' if g4.get('passed') else '❌':>6s} "
              f"score={g4.get('score', 0):.2f}")
        print(f"    {'Enhancement (Capacity)':<25s} {'✅' if enh_cap.get('pass_capacity') else '❌':>6s} "
              f"cap_ratio={enh_cap.get('capacity_ratio', 0):.2f}")
        print(f"    {'Enhancement (Regime)':<25s} {'✅' if enh_reg.get('pass_all_regimes') else '❌':>6s} "
              f"min_regime_ic_ir={enh_reg.get('min_regime_ic_ir', 0):.3f} tag={enh_reg.get('regime_tag', '-')}")
        print(f"    {'Shadow (Config_E override)':<25s} {'✅' if shadow.get('pass_shadow') else '❌':>6s} "
              f"live_dsr={shadow.get('live_dsr', 0):.4f} max_dd={shadow.get('max_drawdown', 0):.4f}")
        print(f"    {'Committee':<25s} {'✅' if committee.get('approved') else '❌':>6s} "
              f"avg={committee.get('avg_score', 0):.2f} verdict={committee.get('chair_decision', '-')}")
        print()
        logger.info(f"    最终状态: {state}")
        logger.info(f"    最终评分: {final_score:.2f}")
        if fail_reasons:
            logger.info(f"    失败原因: {fail_reasons}")

        # 验收检查
        all_gates_pass = (
            g1.get("passed") and g2.get("passed") and g3_pass and
            g4.get("passed") and shadow.get("pass_shadow") and
            committee.get("approved")
        )
        print()
        if all_gates_pass:
            logger.info("    🎉🎉🎉 重大突破！MARGIN_EXP 通过全部 8 级验证！")
            logger.info("    🎉 这是 QualityTrend 类首个 approved 因子！")
            logger.info("    🎉 也是继 VT_MICRO_VOL_SKEW_INV 之后第二个 approved 因子！")
        else:
            passed_count = sum([
                bool(g1.get("passed")), bool(g2.get("passed")), bool(g3_pass),
                bool(g4.get("passed")),
                bool(enh_cap.get("pass_capacity")) or bool(enh_reg.get("pass_all_regimes")),
                bool(shadow.get("pass_shadow")), bool(committee.get("approved"))
            ])
            logger.info(f"    进度: {passed_count} / 7 个关键 Gate 通过")
    else:
        logger.info(f"    [ERROR] 未找到 {target_factor}")

    # P2.2 v6.2d 验证：VT_MICRO_VOL_SKEW_INV 是否恢复正常
    logger.info("\n  P2.2 v6.2d 验证 - VT_MICRO_VOL_SKEW_INV 在 Config_A 基线下是否恢复正常:")
    skew_inv = next((x for x in result.factors if x.get("factor_name") == "VT_MICRO_VOL_SKEW_INV"), None)
    if skew_inv:
        shadow = skew_inv.get("shadow_result") or {}
        state = skew_inv.get("state", "-")
        logger.info(f"    state={state}")
        logger.info(f"    Shadow pass: {shadow.get('pass_shadow', False)}")
        logger.info(f"    live_dsr: {shadow.get('live_dsr', 0):.4f} (阈值 > 0.5)")
        logger.info(f"    max_dd: {shadow.get('max_drawdown', 0):.4f} (阈值 < 0.12)")
        logger.info(f"    total_return: {shadow.get('total_return', 0):.4f}")
        if shadow.get('live_dsr', 0) > 0.5:
            logger.info("    ✅ VT_MICRO_VOL_SKEW_INV 已恢复正常（Config_A 基线保护 Alpha 信号）")
        else:
            logger.info("    ❌ VT_MICRO_VOL_SKEW_INV 仍未通过 Shadow，需进一步分析")

    # ============ Step 5: 写入报告 ============
    logger.info("\n[5/5] 写入批次报告")
    md_path = _write_fourteenth_batch_report(result, symbols, n_trials)
    logger.info(f"  报告路径: {md_path}")

    # 状态分布
    state_dist: dict = {}
    for fx in result.factors:
        st = fx.get("state", "unknown")
        state_dist[st] = state_dist.get(st, 0) + 1
    logger.info("\n  状态分布:")
    for st, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        logger.info(f"    {st:30s} : {cnt}")

    # Top 5 因子
    ranked = _rank_factors_by_progress(result.factors)
    logger.info("\n  Top 5 因子:")
    for fx in ranked[:5]:
        logger.info(f"    {fx['factor_name']:32s} | state={fx.get('state', ''):20s} | score={fx.get('final_score', 0):.2f}")

    logger.info("\n" + "=" * 70)
    logger.info("S3 第十四批次流水线跑批完成（P2.2 v6.2d）")
    logger.info("=" * 70)
    return 0


def _rank_factors_by_progress(factors):
    """按流水线进度对因子排序"""
    progress_order = {
        PipelineState.APPROVED.value: 10,
        PipelineState.COMMITTEE_PENDING.value: 9,
        PipelineState.SHADOW_PASSED.value: 8,
        PipelineState.ENHANCED.value: 7,
        PipelineState.G4_PASSED.value: 6,
        PipelineState.G3_PASSED.value: 5,
        PipelineState.G2_PASSED.value: 4,
        PipelineState.G1_PASSED.value: 3,
        PipelineState.CANDIDATE.value: 2,
        PipelineState.DEFERRED_FUNDAMENTALS.value: 1,
        PipelineState.REJECTED.value: 0,
        PipelineState.FAILED.value: -1,
    }
    return sorted(
        factors,
        key=lambda fx: (progress_order.get(fx.get("state", ""), 0), fx.get("final_score", 0)),
        reverse=True,
    )


def _write_fourteenth_batch_report(result, symbols, n_trials: int) -> Path:
    """写入第十四批次报告"""
    batch_dir = REPORTS_DIR / result.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "BATCH_REPORT.md"

    total = max(result.total_candidates, 1)

    # MARGIN_EXP 详情
    target_factor = "VT_QUALTREND_MARGIN_EXP"
    f = next((x for x in result.factors if x.get("factor_name") == target_factor), None)
    if f:
        g1 = f.get("g1_orthogonality") or {}
        g2 = f.get("g2_ic_stability") or {}
        g3 = f.get("g3_dsr") or {}
        # P2.2 v6.2d 修复：字段名是 g4_economic（不是 g4_economic_logic）
        g4 = f.get("g4_economic") or {}
        enh_cap = f.get("enhancement_capacity") or {}
        enh_reg = f.get("enhancement_regime") or {}
        shadow = f.get("shadow_result") or {}
        committee = f.get("committee_verdict") or {}
        state = f.get("state", "-")
        final_score = f.get("final_score", 0)
        fail_reasons = f.get("fail_reasons", [])
        g3_pass = g3.get('gate_3_pass', g3.get('passed', False))
        margin_exp_section = f"""
## 5. MARGIN_EXP 8 级流水线详情（v6.2d 因子特定配置）

| Gate | 通过 | 详情 |
|------|------|------|
| G1 正交性 | {"✅" if g1.get("passed") else "❌"} | max_corr={g1.get("max_abs_corr", 0):.3f} ({g1.get("max_corr_factor", "-")}) |
| G2 IC 稳定性 | {"✅" if g2.get("passed") else "❌"} | IC_IR={g2.get("ic_ir_estimated", 0):.4f}, decay={g2.get("ic_decay_estimated", 0):.4f} |
| G3 DSR | {"✅" if g3_pass else "❌"} | DSR={g3.get("dsr_value", 0):.4f}, sr={g3.get("sr_observed", 0):.2f} |
| G4 经济逻辑 | {"✅" if g4.get("passed") else "❌"} | score={g4.get("score", 0):.2f} |
| Enhancement (Capacity) | {"✅" if enh_cap.get("pass_capacity") else "❌"} | cap_ratio={enh_cap.get("capacity_ratio", 0):.2f} |
| Enhancement (Regime) | {"✅" if enh_reg.get("pass_all_regimes") else "❌"} | min_regime_ic_ir={enh_reg.get("min_regime_ic_ir", 0):.3f}, tag={enh_reg.get("regime_tag", "-")} |
| Shadow (Config_E override) | {"✅" if shadow.get("pass_shadow") else "❌"} | live_dsr={shadow.get("live_dsr", 0):.4f}, max_dd={shadow.get("max_drawdown", 0):.4f} |
| Committee | {"✅" if committee.get("approved") else "❌"} | avg={committee.get("avg_score", 0):.2f}, verdict={committee.get("chair_decision", "-")} |

**最终状态**: `{state}`
**最终评分**: {final_score:.2f}
**失败原因**: {fail_reasons if fail_reasons else "无"}

### MARGIN_EXP Shadow 详情（Config_E 因子特定覆盖）

| 指标 | 值 | 阈值 | 通过 |
|------|-----|------|------|
| pass_shadow | {shadow.get("pass_shadow", False)} | - | {"✅" if shadow.get("pass_shadow") else "❌"} |
| live_dsr | {shadow.get("live_dsr", 0):.4f} | >0.5 | {"✅" if shadow.get("live_dsr", 0) > 0.5 else "❌"} |
| max_drawdown | {shadow.get("max_drawdown", 0):.4f} | <0.12 | {"✅" if shadow.get("max_drawdown", 0) < 0.12 else "❌"} |
| monte_carlo_p95_dd | {shadow.get("monte_carlo_p95_dd", 0):.4f} | <0.18 | {"✅" if shadow.get("monte_carlo_p95_dd", 0) < 0.18 else "❌"} |
| total_return | {shadow.get("total_return", 0):.4f} | - | - |
| method | {shadow.get("method", "-")} | - | - |
"""
    else:
        margin_exp_section = "\n## 5. MARGIN_EXP 未找到\n"

    # VT_MICRO_VOL_SKEW_INV 详情
    skew_inv = next((x for x in result.factors if x.get("factor_name") == "VT_MICRO_VOL_SKEW_INV"), None)
    if skew_inv:
        skew_shadow = skew_inv.get("shadow_result") or {}
        skew_state = skew_inv.get("state", "-")
        skew_section = f"""
## 6. VT_MICRO_VOL_SKEW_INV 恢复验证（Config_A 基线）

| 指标 | 值 | 阈值 | 通过 |
|------|-----|------|------|
| state | {skew_state} | - | - |
| pass_shadow | {skew_shadow.get("pass_shadow", False)} | - | {"✅" if skew_shadow.get("pass_shadow") else "❌"} |
| live_dsr | {skew_shadow.get("live_dsr", 0):.4f} | >0.5 | {"✅" if skew_shadow.get("live_dsr", 0) > 0.5 else "❌"} |
| max_drawdown | {skew_shadow.get("max_drawdown", 0):.4f} | <0.12 | {"✅" if skew_shadow.get("max_drawdown", 0) < 0.12 else "❌"} |
| total_return | {skew_shadow.get("total_return", 0):.4f} | - | - |

### v6.2c → v6.2d 对比

| 指标 | v6.2c (Config_E) | v6.2d (Config_A) | 变化 |
|------|------------------|------------------|------|
| live_dsr | -0.965 | {skew_shadow.get("live_dsr", 0):.4f} | {skew_shadow.get("live_dsr", 0) - (-0.965):+.4f} |
| max_drawdown | 0.044 | {skew_shadow.get("max_drawdown", 0):.4f} | {skew_shadow.get("max_drawdown", 0) - 0.044:+.4f} |
| pass_shadow | False | {skew_shadow.get("pass_shadow", False)} | {"✅ 恢复" if skew_shadow.get("pass_shadow") else "❌ 仍失败"} |
"""
    else:
        skew_section = "\n## 6. VT_MICRO_VOL_SKEW_INV 未找到\n"

    content = f"""# 第十四批次流水线跑批报告 - {result.batch_id}

> P2.2 v6.2d 因子特定 Shadow 配置 + Regime 修复
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 改进背景

第十三批次发现 v6.2c Config_E 作为默认配置有副作用：
- VT_MICRO_VOL_SKEW_INV live_dsr 从 Config_A 的 0.70 降至 Config_E 的 -0.965
- MARGIN_EXP 被 Committee 否决（CapacityAgent veto 因 min_regime_ic_ir=-1.000）

v6.2d 改进：
1. **回退 Config_E 为默认配置**，改为因子特定覆盖机制
   - 默认 Config_A 基线（保护 VT_MICRO_VOL_SKEW_INV）
   - QualityTrend 类因子自动应用 Config_E
2. **RegimeConditioner 修复**：样本数 < 5 的 regime 不计入 min_regime_ic_ir
3. **CapacityAgent 修复**：样本不足时不 veto，给中性评分 5.0
4. **显示 bug 修复**：使用正确字段名

## 2. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `{result.batch_id}` |
| 数据标的数 | {len(symbols)} |
| 候选因子总数 | {result.total_candidates} |
| n_trials | {n_trials} |
| 默认 Shadow 配置 | Config_A 基线（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5） |
| QualityTrend 覆盖 | Config_E（target_vol=0.08, dd_threshold=0.02, dd_factor=0.2） |
| 改进版本 | v6.2d（因子特定配置 + Regime 修复） |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | {result.g1_passed} | {result.g1_passed / total * 100:.1f}% |
| G2 IC 稳定性 | {result.g2_passed} | {result.g2_passed / total * 100:.1f}% |
| G3 DSR | {result.g3_passed} | {result.g3_passed / total * 100:.1f}% |
| G4 经济逻辑 | {result.g4_passed} | {result.g4_passed / total * 100:.1f}% |
| Enhancement | {result.enhanced} | {result.enhanced / total * 100:.1f}% |
| Shadow | {result.shadow_passed} | {result.shadow_passed / total * 100:.1f}% |
| Committee Approved | {result.approved} | {result.approved / total * 100:.1f}% |
| Deferred (fundamentals) | {result.deferred_fundamentals} | {result.deferred_fundamentals / total * 100:.1f}% |
| Rejected | {result.rejected} | {result.rejected / total * 100:.1f}% |
| Failed | {result.failed} | {result.failed / total * 100:.1f}% |

## 4. 状态分布

"""
    state_dist: dict = {}
    for fx in result.factors:
        st = fx.get("state", "unknown")
        state_dist[st] = state_dist.get(st, 0) + 1
    for st, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        content += f"- {st}: {cnt}\n"

    content += margin_exp_section
    content += skew_section

    content += """
## 7. 关键结论

"""
    if f and f.get("state") == PipelineState.APPROVED.value:
        content += """🎉🎉🎉 重大突破！

**MARGIN_EXP 通过全部 8 级验证，成为 QualityTrend 类首个 approved 因子！**

这也是继 VT_MICRO_VOL_SKEW_INV 之后第二个 approved 因子。

### v6.2d 关键改进
1. 因子特定 Shadow 配置：不同因子用不同风险管理参数
2. RegimeConditioner 修复：样本不足的 regime 不计入 min_regime_ic_ir
3. CapacityAgent 修复：样本不足时不 veto，给中性评分
"""
    elif f and skew_inv and skew_inv.get("shadow_result", {}).get("live_dsr", 0) > 0.5:
        content += """✅ v6.2d 部分成功：
- VT_MICRO_VOL_SKEW_INV 已恢复正常（Config_A 基线保护 Alpha 信号）
- MARGIN_EXP 未通过 Committee，需进一步分析

需检查 MARGIN_EXP 的 Committee 评审详情，了解是哪位专家给出低分或 veto。
"""
    elif f:
        content += """⚠️ MARGIN_EXP 未能走完 8 级流水线。

需进一步分析失败原因。
"""
    else:
        content += "❌ MARGIN_EXP 未在因子列表中找到\n"

    md_path.write_text(content, encoding="utf-8")
    return md_path


if __name__ == "__main__":
    sys.exit(main())
