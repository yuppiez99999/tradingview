"""S3 首批次真实数据流水线跑批脚本

用真实 A 股 OHLCV 数据驱动 PipelineOrchestrator 的 8 级流水线。

输入：cache/ohlcv/*.parquet（23 个真实标的 2 年数据）
输出：reports/vibe_trading/{batch_id}/pipeline_state.json

执行步骤：
1. 通过 real_data_loader 加载真实价格 + 等权基准 + 财务代理
2. 调用 PipelineOrchestrator.run() 跑完整 8 级流水线
3. 汇总各 Gate 通过率、状态分布、审计轨迹
4. 写入批次报告 markdown（CIO 视角，便于人工 Assess）

注意：本批次为「架构验证批」，目标是验证 8 级状态机在真实数据下能完整流转，
不要求大批量因子通过（首批次预计通过率较低，因为简化实现 + 23 标的样本量小）。
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

from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (  # noqa: E402
    PipelineOrchestrator,
    PipelineState,
)
from research.vibe_trading_factor_analysis.scripts.real_data_loader import (  # noqa: E402
    list_available_symbols,
    load_all_for_pipeline,
)

logger = logging.getLogger("run_first_batch")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def main() -> int:
    """主入口：跑首批次流水线

    Returns:
        退出码：0=成功，1=失败
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("S3 流水线跑批（批次 ID 可指定，第五批次验证 P1.5 改进: 重新设计候选因子避免与现有因子共线）")
    logger.info("=" * 70)

    # ============ Step 1: 加载真实数据 ============
    logger.info("\n[1/4] 加载真实 A 股历史数据")
    symbols = list_available_symbols()
    logger.info(f"  可用标的数: {len(symbols)}")
    logger.info(f"  标的列表: {symbols}")

    price_data, fundamentals, benchmark_returns = load_all_for_pipeline(symbols=symbols)
    logger.info(f"  price_data: {len(price_data)} 个标的")
    logger.info(f"  fundamentals: {len(fundamentals)} 个标的")
    logger.info(f"  benchmark_returns: {len(benchmark_returns)} 天")

    # 检查 fundamentals 数据质量
    if fundamentals:
        real_cnt = sum(1 for f in fundamentals.values() if isinstance(f, dict) and f.get("data_quality") == "real")
        proxy_cnt = sum(1 for f in fundamentals.values() if isinstance(f, dict) and f.get("data_quality") == "proxy")
        missing_cnt = len(fundamentals) - real_cnt - proxy_cnt
        logger.info(f"  fundamentals 数据质量: real={real_cnt} proxy={proxy_cnt} missing={missing_cnt}")

    if not price_data:
        logger.info("[ERROR] 价格数据加载失败")
        return 1

    # ============ Step 2: 初始化流水线 ============
    logger.info("\n[2/4] 初始化 PipelineOrchestrator")
    orchestrator = PipelineOrchestrator(
        config={
            "reports_dir": str(REPORTS_DIR),
        }
    )
    batch_id = f"fifth_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"  batch_id: {batch_id}")

    # ============ Step 3: 跑流水线 ============
    logger.info("\n[3/4] 执行 8 级流水线")
    n_trials = max(len(symbols), 13)  # 至少 13 个候选因子的多重检验基数
    logger.info(f"  n_trials (多重检验基数): {n_trials}")

    result = orchestrator.run(
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        portfolio_value=1e8,
        n_trials=n_trials,
        batch_id=batch_id,
    )

    # ============ Step 4: 汇总结果 ============
    logger.info("\n[4/4] 汇总批次结果")
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

    # ============ 写入首批次报告 Markdown ============
    md_path = _write_batch_report_md(result, symbols, n_trials)
    logger.info(f"\n  批次报告写入: {md_path}")

    # ============ 状态分布明细 ============
    state_dist: dict[str, int] = {}
    for f in result.factors:
        state = f.get("state", "unknown")
        state_dist[state] = state_dist.get(state, 0) + 1
    logger.info("\n  状态分布:")
    for state, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        logger.info(f"    {state:25s} : {cnt}")

    # ============ Top 5 通过最远的因子 ============
    ranked = _rank_factors_by_progress(result.factors)
    logger.info("\n  Top 5 因子（按流水线进度）:")
    for f in ranked[:5]:
        logger.info(f"    {f['factor_name']:30s} | state={f['state']:15s} | score={f.get('final_score', 0):.2f}")

    # ============ 失败原因 Top 5 ============
    fail_reasons: dict[str, int] = {}
    for f in result.factors:
        for reason in f.get("fail_reasons", []):
            # 提取 Gate 名作为聚合 key
            gate_key = reason.split(":")[0] if ":" in reason else reason[:30]
            fail_reasons[gate_key] = fail_reasons.get(gate_key, 0) + 1
    logger.info("\n  失败原因 Top 5:")
    for reason, cnt in sorted(fail_reasons.items(), key=lambda kv: -kv[1])[:5]:
        logger.info(f"    {reason:30s} : {cnt}")

    logger.info("\n" + "=" * 70)
    logger.info("S3 首批次流水线跑批完成")
    logger.info("=" * 70)
    return 0


def _rank_factors_by_progress(factors: list[dict]) -> list[dict]:
    """按流水线进度对因子排序（approved > committee_pending > shadow > enhanced > g4 > g3 > g2 > g1 > rejected > failed）"""
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
        PipelineState.REJECTED.value: 1,
        PipelineState.FAILED.value: 0,
    }
    return sorted(
        factors,
        key=lambda f: (progress_order.get(f.get("state", ""), 0), f.get("final_score", 0)),
        reverse=True,
    )


def _write_batch_report_md(result, symbols: list[str], n_trials: int) -> Path:
    """写入 CIO 视角批次报告 Markdown

    Args:
        result: PipelineResult
        symbols: 数据标的列表
        n_trials: 多重检验基数

    Returns:
        报告文件路径
    """
    batch_dir = REPORTS_DIR / result.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "BATCH_REPORT.md"

    # 计算通过率
    total = max(result.total_candidates, 1)
    g1_rate = result.g1_passed / total * 100
    g2_rate = result.g2_passed / total * 100
    g3_rate = result.g3_passed / total * 100
    g4_rate = result.g4_passed / total * 100
    enhanced_rate = result.enhanced / total * 100
    shadow_rate = result.shadow_passed / total * 100
    approved_rate = result.approved / total * 100
    deferred_rate = result.deferred_fundamentals / total * 100

    # 状态分布
    state_dist: dict[str, int] = {}
    for f in result.factors:
        state = f.get("state", "unknown")
        state_dist[state] = state_dist.get(state, 0) + 1

    # 失败原因聚合
    fail_reasons: dict[str, int] = {}
    for f in result.factors:
        for reason in f.get("fail_reasons", []):
            gate_key = reason.split(":")[0] if ":" in reason else reason[:30]
            fail_reasons[gate_key] = fail_reasons.get(gate_key, 0) + 1

    # Top 因子详情
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
            """安全格式化数值（None / 非数字返回 default）"""
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

    # 失败原因 Top
    fail_top_md = ""
    for reason, cnt in sorted(fail_reasons.items(), key=lambda kv: -kv[1])[:8]:
        fail_top_md += f"| {reason} | {cnt} |\n"

    content = f"""# 首批次流水线跑批报告 - {result.batch_id}

> CIO 视角批次质量评估（v1.0）
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

## 2. 各 Gate 通过率

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

## 3. 状态分布

| 状态 | 因子数 |
|------|--------|
"""
    for state, cnt in sorted(state_dist.items(), key=lambda kv: -kv[1]):
        content += f"| {state} | {cnt} |\n"

    content += f"""
## 4. Top 10 因子详情（按流水线进度排序）

| # | 因子 | 最终状态 | G1\\|corr\\| | G2 IC_IR | G3 DSR | G4 评分 | Capacity | Regime | Shadow DSR/DD | Committee |
|---|------|---------|------------|----------|--------|---------|----------|--------|--------------|-----------|
{top_factors_md}

## 5. 失败原因聚合（Top 8）

| 失败 Gate | 因子数 |
|-----------|--------|
{fail_top_md}

## 6. CIO 评估要点

### 6.1 数据质量
- 使用真实 A 股 OHLCV 数据（{len(symbols)} 个标的，2 年）
- 510300 ETF 数据缺失，用 23 标的等权日收益作为基准代理（符合 project_memory 硬约束「避免 synthesize_ohlcv_from_returns」）
- fundamentals 使用价格代理（pe/pb/roe 占位），Gate4 经济逻辑评分应考虑此降级

### 6.2 简化实现风险
- **Gate2 IC 稳定性**：当前用单期 IC 经验映射 IC_IR，未做真实 120d 滚动（可能高估 IC_IR）
- **Gate3 DSR**：用最近 30 日多空 PnL 作为输入，样本量不达 120d 标准（DSR 检验功效偏弱）
- **Shadow 90d**：用 `[candidate.values] * 90` 复制因子值历史，未做真实日频因子值滚动（实际 live_DSR 会被高估）
- **Regime**：用占位历史，Regime 划分可信度有限

### 6.3 下批次改进方向
1. 实现 `compute_factor_history(price_data, factor_def)` 返回日频因子值序列
2. Gate2 改为真实 120d 滚动 IC_IR
3. Gate3 / Shadow 用真实 90 日每日因子值 + forward returns
4. 补齐 510300 ETF 真实数据（用 wind-mcp-skill）
5. 扩展标的覆盖至全市场（≥300 标的）

### 6.4 准入决议
- **本批次为架构验证批，不写入生产因子库**
- 待 S4 分析后，决定是否进入 S5（写入 alpha_factor_library.py）
- 若 G1-G4 通过率均 > 30% 且 Shadow 通过率 > 0%，可批准进入 S5
- 若通过率过低（< 10%），需先修复简化实现风险，再跑第二批次

## 7. 审计轨迹

完整流水线状态持久化于：
```
reports/vibe_trading/{result.batch_id}/pipeline_state.json
```

包含每个候选因子的：
- 各 Gate 详细结果（max_abs_corr, IC, DSR, economic_score, capacity_ratio, live_dsr 等）
- 失败原因列表
- 委员会各 Agent 评分与最终决议

---
*本报告由 PipelineOrchestrator v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
"""
    md_path.write_text(content, encoding="utf-8")
    return md_path


if __name__ == "__main__":
    sys.exit(main())
