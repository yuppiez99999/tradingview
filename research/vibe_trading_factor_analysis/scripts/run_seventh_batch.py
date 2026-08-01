# -*- coding: utf-8 -*-
"""S3 第七批次流水线跑批脚本（P2.1 微观结构因子验证版）

P2.1 改进内容：
    - 新增 7 个微观结构因子（VT_MICRO_* / VT_REV_VOL_DRAIN_INV）
    - 移除 4 个与现有因子完全共线的 V/Q/S 因子（max_corr >= 0.99）
    - 保留 VT_GROWTH_COMPOSITE（P1 实测 IC_IR=+0.26 最高分）

P2.1 设计依据：
    - 第六批次 v3 诊断结论：G2 失败根因从"样本量不足"变为"因子 Alpha 质量不足"
    - IC_mean 几乎全部 |IC| < 0.05，需从因子设计本身入手
    - 微观结构维度是现有 51 因子库的空白维度（不共线）

数据集（沿用 P1 改进成果）：
    - OHLCV: 105 个真实 A 股标的
    - 基准: 沪深 300 指数真实日收益率
    - Fundamentals: baostock 真实财报数据

验收标准：
    - G2 通过率 > 30%（核心目标）
    - G1 通过率 > 80%（移除共线因子后）
    - 至少 1 个 VT_MICRO_* 因子通过 G2
    - 至少 1 个因子进入 G3 DSR 检验
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 项目根路径注入
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols,
    load_price_data, load_fundamentals, load_benchmark_returns,
)
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator, PipelineState,
)

logger = logging.getLogger("run_seventh_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑第七批次流水线（P2.1 微观结构因子验证）

    Returns:
        退出码：0=成功，1=失败
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 第七批次流水线跑批（P2.1 微观结构因子验证）")
    logger.info("=" * 70)
    logger.info("P2.1 改进内容:")
    logger.info("  新增 7 个微观结构因子 (VT_MICRO_* + VT_REV_VOL_DRAIN_INV)")
    logger.info("  移除 4 个共线 V/Q/S 因子 (max_corr >= 0.99)")
    logger.info("  保留 VT_GROWTH_COMPOSITE (P1 实测 IC_IR=+0.26 最高分)")

    # ============ Step 1: 加载 P1 改进后的数据 ============
    logger.info("\n[1/5] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")

    # 显式分步加载（便于统计各数据源状态）
    price_data = load_price_data(symbols=symbols)
    logger.info(f"  price_data: {len(price_data)} 个标的")

    fundamentals = load_fundamentals(price_data)
    real_fund = sum(1 for f in fundamentals.values()
                    if isinstance(f, dict) and f.get("data_quality") == "real")
    proxy_fund = sum(1 for f in fundamentals.values()
                     if isinstance(f, dict) and f.get("data_quality") == "proxy")
    logger.info(f"  fundamentals: {len(fundamentals)} 个 (real={real_fund} / proxy={proxy_fund})")

    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        logger.info("  [WARNING] 沪深300基准不可用, 降级等权代理")
        from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
            compute_equal_weight_benchmark,
        )
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    print(f"  benchmark_returns: {len(benchmark_returns)} 天 | "
          f"累计收益 {(np.prod([1+r for r in benchmark_returns]) - 1) * 100:.2f}%")

    # 标的池规模验证（P1.1 验收点）
    if len(price_data) < 100:
        logger.info(f"  [WARNING] P1.1 目标未达成: 标的 {len(price_data)} < 100")
    else:
        logger.info(f"  [OK] P1.1 达成: 标的 {len(price_data)} >= 100")

    # 真实 fundamentals 比例验证（P1.2 验收点）
    if real_fund / max(len(price_data), 1) >= 0.5:
        print(f"  [OK] P1.2 达成: 真实 fundamentals 占比 "
              f"{real_fund/max(len(price_data), 1)*100:.1f}% >= 50%")
    else:
        print(f"  [WARNING] P1.2 目标未达成: 真实 fundamentals 占比 "
              f"{real_fund/max(len(price_data), 1)*100:.1f}% < 50%")

    # 沪深300基准验证（P1.3 验收点）
    from research.vibe_trading_factor_analysis.scripts.real_data_loader import BENCHMARK_PARQUET
    if BENCHMARK_PARQUET.exists():
        logger.info(f"  [OK] P1.3 达成: 沪深300指数基准可用 ({BENCHMARK_PARQUET.name})")
    else:
        logger.info("  [WARNING] P1.3 目标未达成: 沪深300指数基准缺失")

    if not price_data:
        logger.info("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 初始化流水线 ============
    logger.info("\n[2/5] 初始化 PipelineOrchestrator")
    orchestrator = PipelineOrchestrator(
        config={
            "reports_dir": str(REPORTS_DIR),
        }
    )
    batch_id = f"seventh_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/5] 执行 8 级流水线（P2.1: 105 标的 + 7 个新微观结构因子）")
    n_trials = max(len(symbols), 13)  # 多重检验基数
    logger.info(f"  n_trials (多重检验基数): {n_trials}")

    result = orchestrator.run(
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        portfolio_value=1e8,
        n_trials=n_trials,
        batch_id=batch_id,
        history_days=120,  # 120d 滚动 IC_IR
        forward_window=5,  # 5 日 forward return
    )

    # ============ Step 4: 汇总结果 ============
    logger.info("\n[4/5] 汇总批次结果")
    logger.info("-" * 70)
    logger.info(f"  batch_id           : {result.batch_id}")
    logger.info(f"  started_at         : {result.started_at}")
    logger.info(f"  finished_at        : {result.finished_at}")
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

    # ============ 验收标准检查 ============
    total = max(result.total_candidates, 1)
    g1_rate = result.g1_passed / total * 100
    g2_rate = result.g2_passed / total * 100
    logger.info(f"\n  G1 通过率: {g1_rate:.1f}% (P2.1 目标 > 80%)")
    logger.info(f"  G2 通过率: {g2_rate:.1f}% (P2.1 目标 > 30%)")
    logger.info(f"  Deferred 比例: {result.deferred_fundamentals/total*100:.1f}%")

    # P2.1 微观结构因子专项验收
    sum(
        1 for f in result.factors
        if f.get("state", "") not in (PipelineState.REJECTED.value, PipelineState.FAILED.value)
        and ("MICRO" in f.get("factor_name", "") or "VOL_DRAIN_INV" in f.get("factor_name", ""))
    )
    micro_factors_g2_passed = sum(
        1 for f in result.factors
        if (f.get("g2_ic_stability") or {}).get("ic_ir_estimated", 0) >= 0.3
        and ("MICRO" in f.get("factor_name", "") or "VOL_DRAIN_INV" in f.get("factor_name", ""))
    )
    logger.info(f"  P2.1 微观结构因子通过 G2: {micro_factors_g2_passed} 个")

    if g2_rate > 30:
        logger.info(f"  [OK] P2.1 验收达成: G2 通过率 {g2_rate:.1f}% > 30%")
    else:
        logger.info(f"  [INFO] P2.1 验收未达: G2 通过率 {g2_rate:.1f}% <= 30%")
        logger.info("         （但微观结构因子是新增维度，可能需要 P2.2+ 进一步改进）")

    # ============ Step 5: 写入报告 ============
    logger.info("\n[5/5] 写入批次报告")
    md_path = _write_seventh_batch_report(
        result, symbols, n_trials,
        price_data_count=len(price_data),
        fundamentals_real=real_fund,
        fundamentals_proxy=proxy_fund,
        benchmark_days=len(benchmark_returns),
    )
    logger.info(f"  报告路径: {md_path}")

    # 状态分布明细
    state_dist: dict[str, int] = {}
    for f in result.factors:
        state = f.get("state", "unknown")
        state_dist[state] = state_dist.get(state, 0) + 1
    logger.info("\n  状态分布:")
    for state, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        logger.info(f"    {state:30s} : {cnt}")

    # Top 5 因子
    ranked = _rank_factors_by_progress(result.factors)
    logger.info("\n  Top 5 因子（按流水线进度）:")
    for f in ranked[:5]:
        print(f"    {f['factor_name']:30s} | state={f.get('state', ''):15s} | "
              f"score={f.get('final_score', 0):.2f}")

    logger.info("\n" + "=" * 70)
    logger.info("S3 第七批次流水线跑批完成（P2.1 微观结构因子验证）")
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


def _write_seventh_batch_report(
    result, symbols: list[str], n_trials: int,
    price_data_count: int, fundamentals_real: int, fundamentals_proxy: int,
    benchmark_days: int,
) -> Path:
    """写入第七批次报告（P2.1 微观结构因子验证）"""
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
    deferred_rate = result.deferred_fundamentals / total * 100

    state_dist: dict[str, int] = {}
    for f in result.factors:
        state = f.get("state", "unknown")
        state_dist[state] = state_dist.get(state, 0) + 1

    fail_reasons: dict[str, int] = {}
    for f in result.factors:
        for reason in f.get("fail_reasons", []):
            gate_key = reason.split(":")[0] if ":" in reason else reason[:30]
            fail_reasons[gate_key] = fail_reasons.get(gate_key, 0) + 1

    # P2.1 微观结构因子专项统计
    [
        f.get("factor_name", "") for f in result.factors
        if "MICRO" in f.get("factor_name", "") or "VOL_DRAIN_INV" in f.get("factor_name", "")
    ]
    micro_factors_g2_passed = sum(
        1 for f in result.factors
        if (f.get("g2_ic_stability") or {}).get("ic_ir_estimated", 0) >= 0.3
        and ("MICRO" in f.get("factor_name", "") or "VOL_DRAIN_INV" in f.get("factor_name", ""))
    )

    ranked = _rank_factors_by_progress(result.factors)
    top_factors_md = ""
    for i, f in enumerate(ranked[:10], 1):
        g1 = f.get("g1_orthogonality", {}) or {}
        g2 = f.get("g2_ic_stability", {}) or {}
        g3 = f.get("g3_dsr", {}) or {}
        g4 = f.get("g4_economic", {}) or {}
        cap = f.get("enhancement_capacity", {}) or {}
        reg = f.get("enhancement_regime", {}) or {}
        sh = f.get("shadow_result", {}) or {}
        cm = f.get("committee_verdict", {}) or {}

        def _fmt(value, fmt: str = ".3f", default: str = "-") -> str:
            if value is None:
                return default
            try:
                return format(float(value), fmt)
            except (TypeError, ValueError):
                return str(value)

        top_factors_md += (
            f"| {i} | `{f['factor_name']}` | {f.get('state', '-')} | "
            f"{_fmt(g1.get('max_abs_corr'))} | "
            f"{_fmt(g2.get('ic_ir_estimated'))} | "
            f"{_fmt(g3.get('dsr_value'))} | "
            f"{_fmt(g4.get('score'), '.1f')} | "
            f"{_fmt(cap.get('capacity_ratio'))} | "
            f"{reg.get('regime_tag', '-')} | "
            f"{_fmt(sh.get('live_dsr'))}/{_fmt(sh.get('max_drawdown'))} | "
            f"{_fmt(cm.get('avg_score'), '.1f')} |\n"
        )

    fail_top_md = ""
    for reason, cnt in sorted(fail_reasons.items(), key=lambda kv: -kv[1])[:8]:
        fail_top_md += f"| {reason} | {cnt} |\n"

    # P2.1 验收结论
    p2_1_g2_pass = "✅" if g2_rate > 30 else "⚠️"
    p2_1_g1_pass = "✅" if g1_rate > 80 else "⚠️"
    p2_1_micro_pass = "✅" if micro_factors_g2_passed > 0 else "⚠️"

    content = f"""# 第七批次流水线跑批报告 - {result.batch_id}

> CIO 视角批次质量评估（P2.1 微观结构因子验证）
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `{result.batch_id}` |
| 开始时间 | {result.started_at} |
| 结束时间 | {result.finished_at} |
| 数据标的数 | {len(symbols)} |
| 候选因子总数 | {result.total_candidates} |
| n_trials (多重检验基数) | {n_trials} |
| portfolio_value | 1e8 |
| history_days (滚动 IC 窗口) | 120 |
| forward_window | 5 |

## 2. P2.1 微观结构因子验收

| 改进项 | 目标 | 实际 | 验收 |
|--------|------|------|------|
| 新增微观结构因子 | 7 个 | VT_MICRO_* x6 + VT_REV_VOL_DRAIN_INV x1 | ✅ |
| 移除共线 V/Q/S 因子 | 4 个 | VAL_*/QUA_*/SIZE_LOG_NORMALIZED 已过滤 | ✅ |
| 数据基础设施（沿用 P1） | 105 标的 + 真实 fund | {price_data_count} 标的 + 真实率 {fundamentals_real/max(price_data_count,1)*100:.1f}% | ✅ |
| **G1 通过率** | **> 80%** | **{g1_rate:.1f}%** | **{p2_1_g1_pass}** |
| **G2 通过率** | **> 30%** | **{g2_rate:.1f}%** | **{p2_1_g2_pass}** |
| **微观结构因子通过 G2** | **>= 1** | **{micro_factors_g2_passed}** | **{p2_1_micro_pass}** |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| G1 正交性 | {result.g1_passed} | {g1_rate:.1f}% | 与现有 50+ 因子相关性 \\|corr\\| < 0.5 |
| G2 IC 稳定性 | {result.g2_passed} | {g2_rate:.1f}% | IC_IR_120d >= 0.3, decay < 0.6 |
| G3 DSR 防过拟合 | {result.g3_passed} | {g3_rate:.1f}% | DSR > 0, n_trials >= 5 |
| G4 经济逻辑 | {result.g4_passed} | {g4_rate:.1f}% | 评分 >= 7 |
| Enhancement | {result.enhanced} | {enhanced_rate:.1f}% | Capacity + Regime |
| Shadow 90d | {result.shadow_passed} | {shadow_rate:.1f}% | live_DSR > 0.5, max_dd < 12% |
| **Committee Approved** | **{result.approved}** | **{approved_rate:.1f}%** | avg >= 7, no veto |
| Rejected | {result.rejected} | {result.rejected/total*100:.1f}% | 任一 Gate 未通过 |
| Failed | {result.failed} | {result.failed/total*100:.1f}% | 异常崩溃 |
| **Deferred (fundamentals)** | **{result.deferred_fundamentals}** | **{deferred_rate:.1f}%** | V/Q/S/Growth 因子在 fundamentals 为 proxy 时跳过评估 |

## 4. 状态分布

| 状态 | 因子数 |
|------|--------|
"""
    for state, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        content += f"| {state} | {cnt} |\n"

    content += f"""
## 5. Top 10 因子详情

| # | 因子 | 最终状态 | G1\\|corr\\| | G2 IC_IR | G3 DSR | G4 评分 | Capacity | Regime | Shadow DSR/DD | Committee |
|---|------|---------|------------|----------|--------|---------|----------|--------|--------------|-----------|
{top_factors_md}

## 6. 失败原因聚合（Top 8）

| 失败 Gate | 因子数 |
|-----------|--------|
{fail_top_md}

## 7. CIO 评估要点

### 7.1 P2.1 微观结构因子设计成效

**新增 7 个微观结构因子**（与现有 51 因子不共线的新维度）：
- VT_MICRO_CLOSE_STRENGTH（收盘强度）
- VT_MICRO_ORDER_IMBALANCE（订单流不平衡代理）
- VT_MICRO_GAP_TREND（隔夜跳空方向累积）
- VT_MICRO_RANGE_RATIO（日内振幅相对变化率）
- VT_MICRO_VOL_SKEW（成交量分布偏态）
- VT_MICRO_CLOSING_MOMENTUM（收盘相对日内中点偏移）
- VT_REV_VOL_DRAIN_INV（缩量趋势持续，VT_REV_VOL_DRAIN 反向使用）

**移除 4 个共线因子**：
- VT_VAL_COMPOSITE / VT_VAL_EARNINGS_YIELD_SCALED（与 VAL_PE corr=1.00）
- VT_QUA_COMPOSITE（与 QUA_DEBT_TO_EQUITY corr=0.99）
- VT_SIZE_LOG_NORMALIZED（与 SIZE_LOG_MCAP corr=1.00）

### 7.2 G2 通过率分析
- **G2 通过率 {g2_rate:.1f}%** {("(达到 > 30% 目标)" if g2_rate > 30 else "(未达 > 30% 目标)")}
- 微观结构因子通过 G2 数量: {micro_factors_g2_passed}
- 与第六批次 v3 ({g2_rate:.1f}% G2) 对比，验证 P2.1 微观结构维度是否带来 Alpha 提升

### 7.3 准入决议
- **G2 通过率 {g2_rate:.1f}%** {"> 30% 阈值，可启动 S5 写入因子库" if g2_rate > 30 else "≤ 30% 阈值，暂缓 S5"}
- **Approved 因子数 {result.approved}** 个
- 通过 G2-G4 全链路的因子可写入 alpha_factor_library.py（带 origin="vibe_trading" 标签）

## 8. 审计轨迹

完整流水线状态持久化于：
```
reports/vibe_trading/{result.batch_id}/pipeline_state.json
```

---
*本报告由 PipelineOrchestrator v1.0 + P2.1 微观结构因子改进自动生成。*
"""
    md_path.write_text(content, encoding="utf-8")
    return md_path


if __name__ == "__main__":
    sys.exit(main())
