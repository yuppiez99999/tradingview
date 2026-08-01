"""S3 第九批次流水线跑批脚本（P2.1c+P2.1d 集成验证）

集成三项改进：
    1. P2.1c 集成：risk_managed=True 作为 Shadow 默认配置（PipelineOrchestrator 集成）
       - 波动率缩放（目标年化 15%）+ 回撤去杠杆（>5% 降至 50%）
       - 预期 VT_MICRO_VOL_SKEW_INV 通过 Shadow → 进入 Committee
    2. P2.1d 批量反向因子：4 个新反向因子
       - VT_REV_OVERREACTION_INV (IC_IR -0.20 → +0.20 预期)
       - VT_MOM_OVERNIGHT_GAP_INV (IC_IR -0.14 → +0.14 预期)
       - VT_MOM_HIGH_VOL_ALPHA_INV (IC_IR -0.13 → +0.13 预期)
       - VT_VOL_CLUSTERING_INV (IC_IR -0.13 → +0.13 预期)
    3. Committee 评审：通过 Shadow 的因子自动进入 FactorCommittee 评审

验收标准：
    - VT_MICRO_VOL_SKEW_INV 通过 Shadow（启用 risk_managed）→ 进入 Committee
    - 至少 1 个新反向因子通过 G2（IC_IR >= 0.3 难达，但观察效果）
    - Shadow 通过率 > 0（首个因子通过完整 7 级流水线）
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
    list_available_symbols,
    load_benchmark_returns,
    load_fundamentals,
    load_price_data,
)

logger = logging.getLogger("run_ninth_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑第九批次流水线（P2.1c+P2.1d 集成验证）"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第九批次流水线跑批（P2.1c+P2.1d 集成验证）")
    logger.info("=" * 70)
    logger.info("集成改进:")
    logger.info("  P2.1c: risk_managed=True 作为 Shadow 默认配置")
    logger.info("  P2.1d: 4 个新反向因子 (VT_REV_OVERREACTION_INV 等)")
    logger.info("  Committee: 通过 Shadow 的因子自动进入评审")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/5] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")

    price_data = load_price_data(symbols=symbols)
    logger.info(f"  price_data: {len(price_data)} 个标的")

    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
            compute_equal_weight_benchmark,
        )
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  benchmark_returns: {len(benchmark_returns)} 天")

    if not price_data:
        logger.info("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 初始化流水线（risk_managed=True 默认启用）============
    logger.info("\n[2/5] 初始化 PipelineOrchestrator（Shadow 默认启用风险管理层）")
    orchestrator = PipelineOrchestrator(
        config={
            "reports_dir": str(REPORTS_DIR),
            # risk_managed 默认 True（P2.1c 集成），可通过 shadow_risk_managed=False 关闭
        }
    )
    batch_id = f"ninth_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")
    logger.info(f"  shadow risk_managed: {orchestrator.shadow_account.risk_managed}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/5] 执行 8 级流水线（含 4 个新反向因子 + 风险管理 Shadow）")
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
    logger.info(f"  Shadow 通过(risk_managed): {result.shadow_passed}")
    logger.info(f"  Committee 通过(Approved): {result.approved}")
    logger.info(f"  Rejected            : {result.rejected}")
    logger.info(f"  Failed              : {result.failed}")
    logger.info(f"  Deferred(fundamentals): {result.deferred_fundamentals}")
    logger.info("-" * 70)

    # P2.1c 验收：VT_MICRO_VOL_SKEW_INV 是否通过 Shadow
    target = next((f for f in result.factors if f.get("factor_name") == "VT_MICRO_VOL_SKEW_INV"), None)
    if target:
        shadow = target.get("shadow_result") or {}
        committee = target.get("committee_verdict") or {}
        logger.info("\n  P2.1c 验收 - VT_MICRO_VOL_SKEW_INV:")
        logger.info(f"    state          : {target.get('state', '-')}")
        logger.info(f"    shadow pass    : {shadow.get('pass_shadow', False)}")
        logger.info(f"    shadow max_dd  : {shadow.get('max_drawdown', 0):.4f} (阈值 < 0.12)")
        logger.info(f"    shadow mc_p95  : {shadow.get('monte_carlo_p95_dd', 0):.4f} (阈值 < 0.18)")
        logger.info(f"    shadow live_dsr: {shadow.get('live_dsr', 0):.4f} (阈值 > 0.5)")
        logger.info(f"    risk_managed   : {shadow.get('risk_managed', False)}")
        logger.info(f"    avg_scaler     : {shadow.get('avg_scaler', 0):.4f}")
        logger.info(f"    derisk_days    : {shadow.get('derisk_triggered_days', 0)}")
        logger.info(f"    committee      : avg={committee.get('avg_score', 0):.2f} approved={committee.get('approved', False)}")
        logger.info(f"    chair_decision : {committee.get('chair_decision', '-')}")

    # P2.1d 验收：4 个新反向因子的 G2 表现
    logger.info("\n  P2.1d 验收 - 4 个新反向因子:")
    new_inv_factors = ["VT_REV_OVERREACTION_INV", "VT_MOM_OVERNIGHT_GAP_INV",
                       "VT_MOM_HIGH_VOL_ALPHA_INV", "VT_VOL_CLUSTERING_INV"]
    for fname in new_inv_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g2 = f.get("g2_ic_stability") or {}
            logger.info(f"    {fname:30s} | IC_IR={g2.get('ic_ir_estimated', 0):+.4f} | state={f.get('state', '-')}")

    # ============ Step 5: 写入报告 ============
    logger.info("\n[5/5] 写入批次报告")
    md_path = _write_ninth_batch_report(result, symbols, n_trials)
    logger.info(f"  报告路径: {md_path}")

    # 状态分布
    state_dist: dict[str, int] = {}
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
        logger.info(f"    {f['factor_name']:30s} | state={f.get('state', ''):20s} | score={f.get('final_score', 0):.2f}")

    logger.info("\n" + "=" * 70)
    logger.info("S3 第九批次流水线跑批完成（P2.1c+P2.1d 集成验证）")
    logger.info("=" * 70)
    return 0


def _rank_factors_by_progress(factors: list[dict]) -> list[dict]:
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


def _write_ninth_batch_report(result, symbols: list[str], n_trials: int) -> Path:
    """写入第九批次报告"""
    batch_dir = REPORTS_DIR / result.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "BATCH_REPORT.md"

    total = max(result.total_candidates, 1)
    g1_rate = result.g1_passed / total * 100
    g2_rate = result.g2_passed / total * 100
    g3_rate = result.g3_passed / total * 100
    g4_rate = result.g4_passed / total * 100
    enhanced_rate = result.enhanced / total * 100
    shadow_rate = result.shadow_passed / total * 100
    approved_rate = result.approved / total * 100

    # VT_MICRO_VOL_SKEW_INV 详情
    target = next((f for f in result.factors if f.get("factor_name") == "VT_MICRO_VOL_SKEW_INV"), None)
    target_md = ""
    if target:
        shadow = target.get("shadow_result") or {}
        committee = target.get("committee_verdict") or {}
        target_md = f"""
## 3. P2.1c 验收 - VT_MICRO_VOL_SKEW_INV 完整流水线

| Gate | 结果 | 关键指标 |
|------|------|---------|
| G1 正交性 | {"✅" if (target.get('g1_orthogonality') or {}).get('passed') else "❌"} | max_corr={(target.get('g1_orthogonality') or {}).get('max_abs_corr', 0):.3f} |
| G2 IC 稳定性 | {"✅" if (target.get('g2_ic_stability') or {}).get('passed') else "❌"} | IC_IR={(target.get('g2_ic_stability') or {}).get('ic_ir_estimated', 0):.4f} |
| G3 DSR | {"✅" if (target.get('g3_dsr') or {}).get('gate_3_pass') else "❌"} | DSR={(target.get('g3_dsr') or {}).get('dsr_value', 0):.4f} |
| G4 经济逻辑 | {"✅" if (target.get('g4_economic') or {}).get('passed') else "❌"} | score={(target.get('g4_economic') or {}).get('score', 0):.1f} |
| Enhancement | {"✅" if (target.get('enhancement_capacity') or {}).get('pass_capacity') else "❌"} | capacity_ratio={(target.get('enhancement_capacity') or {}).get('capacity_ratio', 0):.2f} |
| **Shadow (risk_managed)** | **{"✅" if shadow.get('pass_shadow') else "❌"}** | **max_dd={shadow.get('max_drawdown', 0):.4f}, live_dsr={shadow.get('live_dsr', 0):.4f}** |
| **Committee** | **{"✅" if committee.get('approved') else "❌"}** | **avg={committee.get('avg_score', 0):.2f}, chair={committee.get('chair_decision', '-')}** |
| **最终状态** | **{target.get('state', '-')}** | **approved={target.get('approved', False)}** |

### Shadow 风险管理层详情
- risk_managed: {shadow.get('risk_managed', False)}
- max_drawdown: {shadow.get('max_drawdown', 0):.4f}（阈值 < 0.12）
- mc_p95_dd: {shadow.get('monte_carlo_p95_dd', 0):.4f}（阈值 < 0.18）
- live_dsr: {shadow.get('live_dsr', 0):.4f}（阈值 > 0.5）
- avg_scaler: {shadow.get('avg_scaler', 0):.4f}
- derisk_triggered_days: {shadow.get('derisk_triggered_days', 0)}

### Committee 评审详情
- avg_score: {committee.get('avg_score', 0):.2f}（阈值 >= 7.0）
- has_veto: {committee.get('has_veto', False)}
- chair_decision: {committee.get('chair_decision', '-')}
- approved: {committee.get('approved', False)}
"""

    # P2.1d 新反向因子表
    new_inv_factors = ["VT_REV_OVERREACTION_INV", "VT_MOM_OVERNIGHT_GAP_INV",
                       "VT_MOM_HIGH_VOL_ALPHA_INV", "VT_VOL_CLUSTERING_INV"]
    p21d_md = """
## 4. P2.1d 验收 - 4 个新反向因子

| 因子 | 原始 IC_IR | 预期反向 IC_IR | 实际 IC_IR | G2 通过 | 状态 |
|------|----------|-------------|----------|--------|------|
"""
    for fname in new_inv_factors:
        f = next((x for x in result.factors if x.get("factor_name") == fname), None)
        if f:
            g2 = f.get("g2_ic_stability") or {}
            ic_ir = g2.get("ic_ir_estimated", 0)
            # 原始因子的 IC_IR（从第八批次数据）
            orig_map = {
                "VT_REV_OVERREACTION_INV": -0.20,
                "VT_MOM_OVERNIGHT_GAP_INV": -0.14,
                "VT_MOM_HIGH_VOL_ALPHA_INV": -0.13,
                "VT_VOL_CLUSTERING_INV": -0.13,
            }
            orig = orig_map.get(fname, 0)
            expected = -orig
            passed = "✅" if g2.get("passed") else "❌"
            p21d_md += f"| {fname} | {orig:+.2f} | {expected:+.2f} | {ic_ir:+.4f} | {passed} | {f.get('state', '-')} |\n"
        else:
            p21d_md += f"| {fname} | - | - | - | - | 未找到 |\n"

    content = f"""# 第九批次流水线跑批报告 - {result.batch_id}

> P2.1c+P2.1d 集成验证（risk_managed 默认 + 4 个新反向因子 + Committee 评审）
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
| G1 正交性 | {result.g1_passed} | {g1_rate:.1f}% |
| G2 IC 稳定性 | {result.g2_passed} | {g2_rate:.1f}% |
| G3 DSR 防过拟合 | {result.g3_passed} | {g3_rate:.1f}% |
| G4 经济逻辑 | {result.g4_passed} | {g4_rate:.1f}% |
| Enhancement | {result.enhanced} | {enhanced_rate:.1f}% |
| **Shadow (risk_managed)** | **{result.shadow_passed}** | **{shadow_rate:.1f}%** |
| **Committee (Approved)** | **{result.approved}** | **{approved_rate:.1f}%** |
{target_md}
{p21d_md}
## 5. 结论

- **P2.1c 集成验证**：risk_managed=True 作为 Shadow 默认配置已集成到 PipelineOrchestrator
- **P2.1d 反向因子**：4 个新反向因子已添加，观察其 IC_IR 反转效果
- **Committee 评审**：通过 Shadow 的因子自动进入 FactorCommittee 评审
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


if __name__ == "__main__":
    sys.exit(main())
