"""S3 第十批次流水线跑批脚本（P2.2 集成验证）

集成改进：
    P2.2 质量变化类因子（QualityTrend 新类别）：
      - VT_QUALTREND_ROE_DELTA: ROE YoY 变化
      - VT_QUALTREND_MARGIN_EXP: 毛利率 YoY 扩张
      - VT_QUALTREND_DEBT_RED: 负债率 YoY 下降
      - VT_QUALTREND_GROWTH_ACCEL: 净利润增长率加速

数据基础：
    - cache/fundamentals/{symbol}_history.json (历史季度财务数据)
    - PipelineOrchestrator.run() 会自动加载 fundamentals_history
    - QualityTrend 类因子在 fundamentals_history 真实时进入评估

验收标准：
    - 至少 1 个 QualityTrend 因子通过 G1（与现有 Quality/Growth 因子正交）
    - 至少 1 个 QualityTrend 因子通过 G2（IC_IR >= 0.3）
    - QualityTrend 因子的 history_valid_ratio > 0.5（数据有效）
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

logger = logging.getLogger("run_tenth_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑第十批次流水线（P2.2 集成验证）"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第十批次流水线跑批（P2.2 集成验证 - 质量变化类因子）")
    logger.info("=" * 70)
    logger.info("集成改进:")
    logger.info("  P2.2: 4 个 QualityTrend 因子（ROE/毛利率/负债率/增长加速 YoY）")
    logger.info("  自动加载 fundamentals_history 缓存")
    logger.info("  QualityTrend 与 Quality/Growth 水平值正交验证")

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
    batch_id = f"tenth_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")
    logger.info(f"  shadow risk_managed: {orchestrator.shadow_account.risk_managed}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/5] 执行 8 级流水线（含 4 个 QualityTrend 因子）")
    logger.info("  QualityTrend 因子:")
    logger.info("    - VT_QUALTREND_ROE_DELTA (ROE YoY)")
    logger.info("    - VT_QUALTREND_MARGIN_EXP (毛利率 YoY)")
    logger.info("    - VT_QUALTREND_DEBT_RED (负债率 YoY 下降)")
    logger.info("    - VT_QUALTREND_GROWTH_ACCEL (增长率加速)")
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
        # fundamentals_history 由 orchestrator 自动加载
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

    # P2.2 验收：4 个 QualityTrend 因子表现
    logger.info("\n  P2.2 验收 - 4 个 QualityTrend 因子:")
    quality_trend_factors = [
        "VT_QUALTREND_ROE_DELTA",
        "VT_QUALTREND_MARGIN_EXP",
        "VT_QUALTREND_DEBT_RED",
        "VT_QUALTREND_GROWTH_ACCEL",
    ]
    qt_pass_g1 = 0
    qt_pass_g2 = 0
    qt_deferred = 0
    for fname in quality_trend_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g1 = f.get("g1_orthogonality") or {}
            g2 = f.get("g2_ic_stability") or {}
            state = f.get("state", "-")
            ic_ir = g2.get("ic_ir_estimated", 0)
            max_corr = g1.get("max_abs_corr", 0)
            ic_mean = g2.get("ic_mean", 0)
            logger.info(f"    {fname:32s} | state={state:25s} | max_corr={max_corr:.3f} | IC={ic_mean:+.4f} | IC_IR={ic_ir:+.4f}")
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
    logger.info("  P2.2 验收汇总:")
    logger.info(f"    QualityTrend 通过 G1: {qt_pass_g1} / 4")
    logger.info(f"    QualityTrend 通过 G2: {qt_pass_g2} / 4")
    logger.info(f"    QualityTrend 因 fundamentals_history 不足 defer: {qt_deferred} / 4")

    # ============ Step 5: 写入报告 ============
    logger.info("\n[5/5] 写入批次报告")
    md_path = _write_tenth_batch_report(result, symbols, n_trials)
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
    logger.info("S3 第十批次流水线跑批完成（P2.2 集成验证）")
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


def _write_tenth_batch_report(result, symbols, n_trials: int) -> Path:
    """写入第十批次报告"""
    batch_dir = REPORTS_DIR / result.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "BATCH_REPORT.md"

    total = max(result.total_candidates, 1)

    # QualityTrend 因子详情
    quality_trend_factors = [
        "VT_QUALTREND_ROE_DELTA",
        "VT_QUALTREND_MARGIN_EXP",
        "VT_QUALTREND_DEBT_RED",
        "VT_QUALTREND_GROWTH_ACCEL",
    ]
    qt_rows = ""
    for fname in quality_trend_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g1 = f.get("g1_orthogonality") or {}
            g2 = f.get("g2_ic_stability") or {}
            state = f.get("state", "-")
            ic_ir = g2.get("ic_ir_estimated", 0)
            ic_mean = g2.get("ic_mean", 0)
            max_corr = g1.get("max_abs_corr", 0)
            max_corr_factor = g1.get("max_corr_factor", "-")
            qt_rows += (
                f"| {fname} | {state} | {max_corr:.3f} ({max_corr_factor}) | "
                f"{ic_mean:+.4f} | {ic_ir:+.4f} | "
                f"{'✅' if g1.get('passed') else '❌'} | {'✅' if g2.get('passed') else '❌'} |\n"
            )
        else:
            qt_rows += f"| {fname} | 未找到 | - | - | - | - | - |\n"

    content = f"""# 第十批次流水线跑批报告 - {result.batch_id}

> P2.2 集成验证（质量变化类因子 QualityTrend）
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `{result.batch_id}` |
| 数据标的数 | {len(symbols)} |
| 候选因子总数 | {result.total_candidates} |
| n_trials | {n_trials} |
| shadow risk_managed | True（P2.1c 默认） |

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

## 3. P2.2 验收 - 4 个 QualityTrend 因子

| 因子 | 状态 | G1 max_corr | IC 均值 | IC_IR | G1 | G2 |
|------|------|------------|---------|-------|----|----|
{qt_rows}

### 设计经济含义

| 因子 | 公式 | 经济含义 |
|------|------|---------|
| VT_QUALTREND_ROE_DELTA | `roe[q] - roe[q-4]` | ROE 改善 → 盈利能力增强 → 看涨 |
| VT_QUALTREND_MARGIN_EXP | `gross_margin[q] - gross_margin[q-4]` | 毛利率扩张 → 议价能力增强 |
| VT_QUALTREND_DEBT_RED | `-(debt_to_equity[q] - debt_to_equity[q-4])` | 负债率下降 → 财务风险降低 |
| VT_QUALTREND_GROWTH_ACCEL | YoY 增长率的 QoQ 变化 | 增长率加速 → 二阶导为正 |

### 与现有因子库正交性预期

- QUA_ROE/QUA_GROSS_MARGIN/QUA_DEBT_TO_EQUITY 是水平值，QualityTrend 是变化率（不同维度）
- VT_GROWTH_COMPOSITE 是单期增长率，VT_QUALTREND_GROWTH_ACCEL 是增长率变化率（二阶导）

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
