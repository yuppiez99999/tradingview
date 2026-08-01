# -*- coding: utf-8 -*-
"""S3 第十一批次流水线跑批脚本（P2.2 v5 最终改进验证 - 差异化极端值处理方案）

版本演进历史：
    v1（第十批次基线）：
        ROE_DELTA:   roe[q] - roe[q-4]                     IC_IR=+0.1364, max_corr=0.543 (MOM_252D)
        MARGIN_EXP:   gm[q] - gm[q-4]                       IC_IR=+0.1270, max_corr=0.279 (QUA_ROE)
        DEBT_RED:     -(d2e[q] - d2e[q-4])                  IC_IR=+0.0000, max_corr=0.776 (QUA_DEBT_TO_EQUITY 共线)
        GROWTH_ACCEL: 净利润 YoY 加速                       IC_IR=+0.2676, max_corr=0.292 (MOM_20D)
    v2（rank 标准化方案，已废弃）：
        ROE_DELTA:   rank(roe[q]) - rank(roe[q-4])          IC_IR=+0.0240 ❌
        MARGIN_EXP:   rank(gm[q]) - rank(gm[q-4])            IC_IR=+0.0612 ❌
        DEBT_RED:     current_ratio[q] - cr[q-4]             IC_IR=+0.0839 ✅
        GROWTH_ACCEL: rank(0.5*yoy_pni + 0.5*rev)            IC_IR=+0.0224 ❌
    v3（双信号 + winsorize，部分回退）：
        ROE_DELTA:   winsorize(roe[q]-roe[q-4])              IC_IR=+0.1811 ✅ (+33% vs v1)
        MARGIN_EXP:   winsorize(gm[q]-gm[q-4])               IC_IR=+0.2742 ✅ (+116% vs v1，接近 0.3 阈值！)
        DEBT_RED:     current_ratio[q] - cr[q-4]             IC_IR=+0.0839 ✅ (保留 v2)
        GROWTH_ACCEL: winsorize(0.5*yoy_pni + 0.5*rev)        IC_IR=+0.0066 ❌ (双信号本身不如净利润)
    v4（全部 winsorize + 净利润回退）：
        ROE_DELTA:   winsorize(roe[q]-roe[q-4], p5/p95)      IC_IR=+0.1811 ✅ (与 v3 一致)
        MARGIN_EXP:   winsorize(gm[q]-gm[q-4], p5/p95)       IC_IR=+0.2742 ✅ (与 v3 一致)
        DEBT_RED:     current_ratio[q] - cr[q-4]             IC_IR=+0.0839 ✅ (与 v2/v3 一致)
        GROWTH_ACCEL: winsorize(np YoY 加速, p5/p95)          IC_IR=+0.1409 ❌ (winsorize 反而降低！v1 是 0.2676)
    v5（最终方案，差异化极端值处理）：
        ROE_DELTA:   winsorize(roe[q]-roe[q-4], p5/p95)      预期 IC_IR ≥ 0.18（v3/v4 验证有效）
        MARGIN_EXP:   winsorize(gm[q]-gm[q-4], p5/p95)       预期 IC_IR ≥ 0.27（v3/v4 验证有效）
        DEBT_RED:     current_ratio[q] - cr[q-4]             预期 IC_IR ≈ 0.08（v2/v3/v4 验证有效）
        GROWTH_ACCEL: 纯 v1 净利润 YoY 加速（不做 winsorize）  预期 IC_IR ≈ 0.27（v1 基线水平）

核心教训：
    1. rank 标准化丢失 Pearson IC 强度信息，导致 IC_IR 大幅下降（v2 失败根因）
    2. winsorize 对小量级变化因子（ROE_DELTA/MARGIN_EXP）有效：极端值是噪声，裁剪后 IC_IR 提升
    3. winsorize 对大量级变化因子（GROWTH_ACCEL）有害：极端值携带 Alpha 信号，裁剪后 IC_IR 下降
    4. yoy_pni/revenue 双信号在 Q1/Q3 季报披露稀疏，不如净利润 YoY 加速信号稳健
    5. 不同因子的极端值处理策略应因子的信号特征差异化选择（v5 核心创新）

数据基础：
    - cache/fundamentals/{symbol}_history.json (schema_version=2, 含 revenue/yoy_pni 字段)

验收标准：
    - VT_QUALTREND_GROWTH_ACCEL v5 IC_IR ≥ 0.20（恢复至 v1 水平，不做 winsorize）
    - VT_QUALTREND_MARGIN_EXP v5 IC_IR ≥ 0.27（v3/v4 验证，接近 0.3 阈值）
    - VT_QUALTREND_DEBT_RED v5 通过 G1（max_corr < 0.5，与 QUA_DEBT_TO_EQUITY 解耦）
    - 至少 1 个 QualityTrend 因子通过 G2（IC_IR ≥ 0.3）
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


# 项目根路径注入
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols,
    load_price_data, load_fundamentals, load_benchmark_returns,
    compute_equal_weight_benchmark,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator, PipelineState,
)

logger = logging.getLogger("run_eleventh_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑第十一批次流水线（P2.2 v5 最终改进验证）"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第十一批次流水线跑批（P2.2 v5 最终改进验证 - 差异化极端值处理方案）")
    logger.info("=" * 70)
    logger.info("v5 最终改进决策（基于 v2/v3/v4 失败教训的差异化方案）:")
    logger.info("  VT_QUALTREND_ROE_DELTA   : winsorize(roe[q]-roe[q-4])  - v3/v4 验证 IC_IR +0.1811 ✅")
    logger.info("  VT_QUALTREND_MARGIN_EXP   : winsorize(gm[q]-gm[q-4])   - v3/v4 验证 IC_IR +0.2742 ✅ (接近 0.3)")
    logger.info("  VT_QUALTREND_DEBT_RED     : current_ratio[q]-cr[q-4]   - v2 解共线有效，保留")
    logger.info("  VT_QUALTREND_GROWTH_ACCEL : 纯 v1 净利润 YoY 加速      - v4 实测 winsorize 有害(0.1409)，回退 v1(0.2676)")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/5] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")

    price_data = load_price_data(symbols=symbols)
    logger.info(f"  price_data: {len(price_data)} 个标的")

    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  benchmark_returns: {len(benchmark_returns)} 天")

    if not price_data:
        logger.info("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 初始化流水线 ============
    logger.info("\n[2/5] 初始化 PipelineOrchestrator")
    orchestrator = PipelineOrchestrator(
        config={
            "reports_dir": str(REPORTS_DIR),
            # P2.1c: risk_managed 默认启用
        }
    )
    batch_id = f"eleventh_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")
    logger.info(f"  shadow risk_managed: {orchestrator.shadow_account.risk_managed}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/5] 执行 8 级流水线（含 4 个 QualityTrend v5 因子）")
    logger.info("  QualityTrend v5 因子（差异化极端值处理方案）:")
    logger.info("    - VT_QUALTREND_ROE_DELTA (winsorize(roe[q] - roe[q-4], p5/p95))")
    logger.info("    - VT_QUALTREND_MARGIN_EXP (winsorize(gm[q] - gm[q-4], p5/p95))")
    logger.info("    - VT_QUALTREND_DEBT_RED (current_ratio[q] - current_ratio[q-4])")
    logger.info("    - VT_QUALTREND_GROWTH_ACCEL (纯 v1: (np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1))")
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
        # fundamentals_history 由 orchestrator 自动加载（v2 schema）
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
    logger.info(f"  Shadow 通过(risk_managed): {result.shadow_passed}")
    logger.info(f"  Committee 通过(Approved): {result.approved}")
    logger.info(f"  Rejected            : {result.rejected}")
    logger.info(f"  Failed              : {result.failed}")
    logger.info(f"  Deferred(fundamentals): {result.deferred_fundamentals}")
    logger.info("-" * 70)

    # P2.2 v5 验收：4 个 QualityTrend 因子表现（v1 基线 → v5 最终方案对比）
    logger.info("\n  P2.2 v5 验收 - 4 个 QualityTrend 因子（v1 基线 → v5 最终方案对比）:")
    quality_trend_factors = [
        ("VT_QUALTREND_ROE_DELTA",     0.543, 0.1364, "MOM_252D"),
        ("VT_QUALTREND_MARGIN_EXP",    0.279, 0.1270, "QUA_ROE"),
        ("VT_QUALTREND_DEBT_RED",      0.776, 0.0000, "QUA_DEBT_TO_EQUITY"),
        ("VT_QUALTREND_GROWTH_ACCEL",  0.292, 0.2676, "MOM_20D"),
    ]
    qt_pass_g1 = 0
    qt_pass_g2 = 0
    qt_deferred = 0
    logger.info(f"    {'因子':32s} | v1_corr v1_ICIR | v5_corr v5_ICIR | v5_max_corr_factor")
    logger.info(f"    {'-'*32}-+-{'-'*17}-+-{'-'*17}-+-{'-'*30}")
    for fname, v1_corr, v1_icir, _v1_factor in quality_trend_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g1 = f.get("g1_orthogonality") or {}
            g2 = f.get("g2_ic_stability") or {}
            state = f.get("state", "-")
            v5_icir = g2.get("ic_ir_estimated", 0)
            v5_corr = g1.get("max_abs_corr", 0)
            v5_factor = g1.get("max_corr_factor", "-")
            g2.get("ic_mean", 0)
            improved_corr = "✅" if v5_corr < v1_corr else ("=" if abs(v5_corr - v1_corr) < 0.01 else "❌")
            improved_icir = "✅" if abs(v5_icir) > abs(v1_icir) else ("=" if abs(v5_icir - v1_icir) < 0.005 else "❌")
            print(f"    {fname:32s} | {v1_corr:.3f}  {v1_icir:+.4f} | "
                  f"{v5_corr:.3f}{improved_corr} {v5_icir:+.4f}{improved_icir} | {v5_factor}")
            if state == "deferred_fundamentals":
                qt_deferred += 1
            else:
                if g1.get("passed"):
                    qt_pass_g1 += 1
                if g2.get("passed"):
                    qt_pass_g2 += 1
        else:
            logger.info(f"    {fname:32s} | 未找到")

    print()
    logger.info("  P2.2 v5 验收汇总:")
    logger.info(f"    QualityTrend 通过 G1: {qt_pass_g1} / 4")
    logger.info(f"    QualityTrend 通过 G2: {qt_pass_g2} / 4")
    logger.info(f"    QualityTrend 因 fundamentals_history 不足 defer: {qt_deferred} / 4")

    # ============ Step 5: 写入报告 ============
    logger.info("\n[5/5] 写入批次报告")
    md_path = _write_eleventh_batch_report(result, symbols, n_trials)
    logger.info(f"  报告路径: {md_path}")

    # 状态分布
    state_dist: dict = {}
    for f in result.factors:
        state = f.get("state", "unknown")
        state_dist[state] = state_dist.get(state, 0) + 1
    logger.info("\n  状态分布:")
    for state, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        logger.info(f"    {state:30s} : {cnt}")

    # Top 5 因子
    ranked = _rank_factors_by_progress(result.factors)
    logger.info("\n  Top 5 因子:")
    for f in ranked[:5]:
        logger.info(f"    {f['factor_name']:32s} | state={f.get('state', ''):20s} | score={f.get('final_score', 0):.2f}")

    logger.info("\n" + "=" * 70)
    logger.info("S3 第十一批次流水线跑批完成（P2.2 v5 差异化极端值处理方案）")
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
        key=lambda f: (progress_order.get(f.get("state", ""), 0), f.get("final_score", 0)),
        reverse=True,
    )


def _write_eleventh_batch_report(result, symbols, n_trials: int) -> Path:
    """写入第十一批次报告"""
    batch_dir = REPORTS_DIR / result.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "BATCH_REPORT.md"

    total = max(result.total_candidates, 1)

    # QualityTrend v5 因子详情（含 v1→v5 对比，v5 = ROE/MARGIN winsorize + DEBT_RED current_ratio + GROWTH_ACCEL 纯 v1）
    quality_trend_factors = [
        ("VT_QUALTREND_ROE_DELTA",     0.543, 0.1364, "MOM_252D",         "winsorize(roe[q] - roe[q-4], p5/p95)"),
        ("VT_QUALTREND_MARGIN_EXP",    0.279, 0.1270, "QUA_ROE",          "winsorize(gm[q] - gm[q-4], p5/p95)"),
        ("VT_QUALTREND_DEBT_RED",      0.776, 0.0000, "QUA_DEBT_TO_EQUITY", "current_ratio[q] - current_ratio[q-4]"),
        ("VT_QUALTREND_GROWTH_ACCEL",  0.292, 0.2676, "MOM_20D",          "(np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1)"),
    ]
    qt_rows = ""
    for fname, v1_corr, v1_icir, v1_factor, _v5_formula in quality_trend_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g1 = f.get("g1_orthogonality") or {}
            g2 = f.get("g2_ic_stability") or {}
            state = f.get("state", "-")
            v5_icir = g2.get("ic_ir_estimated", 0)
            v5_corr = g1.get("max_abs_corr", 0)
            v5_factor = g1.get("max_corr_factor", "-")
            g2.get("ic_mean", 0)
            corr_change = v5_corr - v1_corr
            icir_change = v5_icir - v1_icir
            qt_rows += (
                f"| {fname} | {state} | "
                f"{v1_corr:.3f} ({v1_factor}) | {v1_icir:+.4f} | "
                f"{v5_corr:.3f} ({v5_factor}) | {v5_icir:+.4f} | "
                f"{corr_change:+.3f} | {icir_change:+.4f} | "
                f"{'✅' if g1.get('passed') else '❌'} | {'✅' if g2.get('passed') else '❌'} |\n"
            )
        else:
            qt_rows += f"| {fname} | 未找到 | - | - | - | - | - | - | - | - |\n"

    content = f"""# 第十一批次流水线跑批报告 - {result.batch_id}

> P2.2 v5 最终改进验证（差异化极端值处理方案）
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `{result.batch_id}` |
| 数据标的数 | {len(symbols)} |
| 候选因子总数 | {result.total_candidates} |
| n_trials | {n_trials} |
| shadow risk_managed | True（P2.1c 默认） |
| 历史数据 schema_version | 2（含 revenue/yoy_pni 字段） |
| 改进版本 | v5（差异化极端值处理：ROE/MARGIN winsorize + GROWTH 纯 v1） |

## 2. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | {result.g1_passed} | {result.g1_passed / total * 100:.1f}% |
| G2 IC 稳定性 | {result.g2_passed} | {result.g2_passed / total * 100:.1f}% |
| G3 DSR | {result.g3_passed} | {result.g3_passed / total * 100:.1f}% |
| G4 经济逻辑 | {result.g4_passed} | {result.g4_passed / total * 100:.1f}% |
| Enhancement | {result.enhanced} | {result.enhanced / total * 100:.1f}% |
| Shadow (risk_managed) | {result.shadow_passed} | {result.shadow_passed / total * 100:.1f}% |
| Committee Approved | {result.approved} | {result.approved / total * 100:.1f}% |
| Deferred (fundamentals) | {result.deferred_fundamentals} | {result.deferred_fundamentals / total * 100:.1f}% |
| Rejected | {result.rejected} | {result.rejected / total * 100:.1f}% |
| Failed | {result.failed} | {result.failed / total * 100:.1f}% |

## 3. P2.2 v5 改进对比 - 4 个 QualityTrend 因子（v1 基线 → v5 最终方案）

| 因子 | 状态 | v1 max_corr | v1 IC_IR | v5 max_corr | v5 IC_IR | corr 变化 | IC_IR 变化 | G1 | G2 |
|------|------|------------|---------|------------|---------|----------|-----------|----|----|
{qt_rows}

### v5 改进设计（基于 v2/v3/v4 失败教训的差异化方案）

| 因子 | v1 公式 | v2 公式（废弃） | v3 公式（废弃） | v4 公式（废弃） | v5 公式（最终） | 改进目标 |
|------|---------|---------|---------|---------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `roe[q] - roe[q-4]` | `rank(roe[q])-rank(roe[q-4])` | `winsorize(roe[q]-roe[q-4])` | `winsorize(roe[q]-roe[q-4])` | `winsorize(roe[q] - roe[q-4], p5/p95)` | 保留 winsorize（小量级变化有效） |
| VT_QUALTREND_MARGIN_EXP | `gm[q] - gm[q-4]` | `rank(gm[q])-rank(gm[q-4])` | `winsorize(gm[q]-gm[q-4])` | `winsorize(gm[q]-gm[q-4])` | `winsorize(gm[q] - gm[q-4], p5/p95)` | 保留 winsorize（小量级变化有效） |
| VT_QUALTREND_DEBT_RED | `-(d2e[q]-d2e[q-4])` | `current_ratio[q]-cr[q-4]` | `current_ratio[q]-cr[q-4]` | `current_ratio[q]-cr[q-4]` | `current_ratio[q] - current_ratio[q-4]` | 保留 v2 current_ratio（解共线有效） |
| VT_QUALTREND_GROWTH_ACCEL | 净利润 YoY 加速 | `rank(双信号)` | `winsorize(双信号)` | `winsorize(np YoY 加速)` | 纯 v1 净利润 YoY 加速 | 回退纯 v1（winsorize 有害，极端值携带信号） |

### 版本演进历史与教训

**v1（第十批次基线）**：
- ROE_DELTA IC_IR=+0.1364, MARGIN_EXP=+0.1270, DEBT_RED=+0.0000（共线）, GROWTH_ACCEL=+0.2676

**v2（rank 标准化方案，废弃）**：
- 全部因子 IC_IR 暴跌：ROE_DELTA→0.0240, MARGIN_EXP→0.0612, GROWTH_ACCEL→0.0224
- 失败根因：rank 标准化将连续值映射到 [0,1]，丢失 Pearson IC 强度信息
- 唯一成功：DEBT_RED current_ratio 改进（解共线 + IC_IR 0→0.0839）

**v3（双信号 + winsorize，部分回退）**：
- ROE_DELTA IC_IR 0.1364→0.1811 ✅（+33%，winsorize 有效）
- MARGIN_EXP IC_IR 0.1270→0.2742 ✅（+116%，接近 0.3 阈值！）
- GROWTH_ACCEL IC_IR 0.2676→0.0066 ❌（双信号本身不如净利润计算）
- 失败根因：yoy_pni 是百分比形式，QoQ 变化与原版净利润增长率变化含义不同；
          revenue 仅在 Q2/Q4 披露，Q1/Q3 数据稀疏

**v4（全部 winsorize + 净利润回退，部分废弃）**：
- ROE_DELTA/MARGIN_EXP：保留 v3 winsorize 改进（IC_IR 0.1811/0.2742，与 v3 一致）
- DEBT_RED：保留 v2 current_ratio 改进（IC_IR 0.0839，与 v2/v3 一致）
- GROWTH_ACCEL：回退 v1 净利润 YoY 加速 + winsorize → IC_IR=+0.1409 ❌
- 失败根因：winsorize 对 GROWTH_ACCEL 有害！v1 IC_IR=0.2676 → v4 IC_IR=0.1409（-47%）
- 关键洞察：winsorize 对小量级变化因子（ROE/MARGIN，变化值 ~0.01）有效，
  对大量级变化因子（GROWTH_ACCEL，加速度 ~0.1-1.0）有害，因极端值携带 Alpha 信号

**v5（最终方案，差异化极端值处理）**：
- ROE_DELTA/MARGIN_EXP：保留 winsorize（小量级变化，极端值是噪声，裁剪后 IC_IR 提升）
- DEBT_RED：保留 v2 current_ratio 改进（解共线有效）
- GROWTH_ACCEL：回退纯 v1 净利润 YoY 加速（不做 winsorize，保留极端值 Alpha 信号）
- 核心创新：不同因子的极端值处理策略应因子的信号特征差异化选择

### v5 改进经济含义

| 因子 | v5 公式 | 经济含义 |
|------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `winsorize(roe[q] - roe[q-4], p5/p95)` | ROE 同比改善 → 盈利能力增强（裁剪极端值噪声） |
| VT_QUALTREND_MARGIN_EXP | `winsorize(gm[q] - gm[q-4], p5/p95)` | 毛利率扩张 → 议价能力增强（裁剪极端值噪声） |
| VT_QUALTREND_DEBT_RED | `current_ratio[q] - current_ratio[q-4]` | 流动比率上升 → 短期偿债能力改善 |
| VT_QUALTREND_GROWTH_ACCEL | `(np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1)` | 净利润增长率加速 → 二阶导为正（保留极端值信号） |

## 4. 完整因子列表

| 因子名 | 类别 | 状态 | IC_IR | max_corr |
|--------|------|------|-------|----------|
"""
    for f in _rank_factors_by_progress(result.factors):
        g2 = f.get("g2_ic_stability") or {}
        g1 = f.get("g1_orthogonality") or {}
        content += (
            f"| {f.get('factor_name', '-')} | - | {f.get('state', '-')} | "
            f"{g2.get('ic_ir_estimated', 0):+.4f} | {g1.get('max_abs_corr', 0):.3f} |\n"
        )

    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(content)

    return md_path


if __name__ == "__main__":
    sys.exit(main())
