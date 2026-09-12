"""
执行后效果评估与自动复盘 (Post-Execution Review & Auto-Review) — v1.0

对标顶级对冲基金执行评估标准 (Citadel / Point72 / Two Sigma):

核心指标:
1. 实施差额 (Implementation Shortfall, IS)
   IS = (决策价 - 成交均价) × 成交数量
   = 市场冲击 + 时机成本 + 交易成本

2. 滑点分析 (Slippage Analysis)
   - 决策价滑点 (Decision Slippage)
   - 到达价滑点 (Arrival Slippage)
   - VWAP 偏离 (VWAP Deviation)

3. 算法评估 (Algo Performance)
   - TWAP: 时间加权成交分布均匀度
   - VWAP: 与市场 VWAP 的偏差
   - POV: 参与率偏离目标程度

4. 订单执行质量评分
   - A/B/C/D 四级评级
   - 分标的、分时段、分算法统计

5. 自动复盘报告
   - 亮点与问题
   - 改进建议 (自动生成)
   - 策略调整建议
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("v7.5.post_execution")


# ============================================================
# 枚举与数据结构
# ============================================================

class ExecutionGrade(Enum):
    A = "A"    # 优秀: 滑点 < 5bp
    B = "B"    # 良好: 滑点 5-15bp
    C = "C"    # 一般: 滑点 15-30bp
    D = "D"    # 较差: 滑点 > 30bp


@dataclass
class FillRecord:
    """成交记录"""
    symbol: str
    side: str                  # "BUY" / "SELL"
    quantity: int
    fill_price: float
    decision_price: float      # 决策价 (信号触发价)
    arrival_price: float       # 到达价 (订单到达市场时价)
    vwap_price: float = 0.0    # 同期市场 VWAP
    timestamp: str = ""
    algo: str = ""             # 算法: TWAP/VWAP/POV/ICEBERG
    order_id: str = ""
    fee: float = 0.0           # 手续费


@dataclass
class OrderExecutionSummary:
    """单订单执行摘要"""
    symbol: str
    side: str
    total_quantity: int
    filled_quantity: int
    fill_rate: float
    avg_fill_price: float
    decision_price: float
    arrival_price: float
    vwap_price: float
    decision_slippage: float     # bp
    arrival_slippage: float      # bp
    vwap_deviation: float        # bp
    implementation_shortfall: float  # 元
    market_impact: float         # 元
    timing_cost: float           # 元
    fee_total: float             # 元
    grade: ExecutionGrade
    algo: str = ""
    duration_seconds: float = 0.0


@dataclass
class ReviewInsight:
    """复盘洞察"""
    category: str              # "strength" / "weakness" / "suggestion"
    title: str
    detail: str
    impact: str = "medium"     # "high" / "medium" / "low"
    metric: str = ""
    value: float = 0.0


@dataclass
class ExecutionReviewReport:
    """执行复盘报告"""
    report_date: str
    total_orders: int = 0
    total_fills: int = 0
    total_value: float = 0.0
    total_fee: float = 0.0
    avg_decision_slippage_bp: float = 0.0
    avg_arrival_slippage_bp: float = 0.0
    avg_vwap_deviation_bp: float = 0.0
    total_is_value: float = 0.0
    total_is_pct: float = 0.0
    grade_distribution: dict[str, int] = field(default_factory=dict)
    order_summaries: list[OrderExecutionSummary] = field(default_factory=list)
    insights: list[ReviewInsight] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    overall_score: float = 0.0  # 0-100

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "overall_score": round(self.overall_score, 1),
            "total_orders": self.total_orders,
            "total_fills": self.total_fills,
            "total_value": round(self.total_value, 2),
            "total_fee": round(self.total_fee, 2),
            "avg_decision_slippage_bp": round(self.avg_decision_slippage_bp, 2),
            "avg_arrival_slippage_bp": round(self.avg_arrival_slippage_bp, 2),
            "avg_vwap_deviation_bp": round(self.avg_vwap_deviation_bp, 2),
            "total_is_value": round(self.total_is_value, 2),
            "total_is_pct": round(self.total_is_pct, 4),
            "grade_distribution": self.grade_distribution,
            "insights": [
                {
                    "category": i.category,
                    "title": i.title,
                    "detail": i.detail,
                    "impact": i.impact,
                    "metric": i.metric,
                    "value": round(i.value, 4),
                }
                for i in self.insights
            ],
            "recommendations": self.recommendations,
        }


# ============================================================
# 执行评估引擎
# ============================================================

class ExecutionReviewer:
    """执行后效果评估与自动复盘引擎

    使用方式:
        reviewer = ExecutionReviewer()
        report = reviewer.review(fills, order_context)
        logger.info(f"综合评分: {report.overall_score}")
        for insight in report.insights:
            logger.info(f"  [{insight.category}] {insight.title}")
    """

    # 滑点阈值 (bp)
    SLIPPAGE_GOOD = 5.0      # < 5bp = A
    SLIPPAGE_OK = 15.0       # < 15bp = B
    SLIPPAGE_BAD = 30.0      # < 30bp = C

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def review(
        self,
        fills: list[FillRecord],
        order_context: dict | None = None,
    ) -> ExecutionReviewReport:
        """执行复盘分析

        Args:
            fills: 成交记录列表
            order_context: 订单上下文信息

        Returns:
            复盘报告
        """
        report_date = now_bj().strftime("%Y-%m-%d")
        report = ExecutionReviewReport(report_date=report_date)

        if not fills:
            report.insights.append(ReviewInsight(
                category="weakness",
                title="无成交记录",
                detail="今日无成交数据, 无法进行执行评估",
                impact="low",
            ))
            report.overall_score = 0.0
            return report

        # 1. 按订单分组统计
        order_groups = self._group_by_order(fills)

        # 2. 计算每笔订单的执行摘要
        order_summaries = []
        for order_id, order_fills in order_groups.items():
            summary = self._summarize_order(order_id, order_fills)
            order_summaries.append(summary)

        report.order_summaries = order_summaries
        report.total_orders = len(order_summaries)
        report.total_fills = len(fills)
        report.total_value = sum(
            s.avg_fill_price * s.filled_quantity for s in order_summaries
        )
        report.total_fee = sum(s.fee_total for s in order_summaries)

        # 3. 计算整体指标
        if report.total_value > 0:
            weights = [s.avg_fill_price * s.filled_quantity
                       for s in order_summaries]
            total_w = sum(weights)

            report.avg_decision_slippage_bp = sum(
                s.decision_slippage * w
                for s, w in zip(order_summaries, weights, strict=False)
            ) / total_w if total_w > 0 else 0

            report.avg_arrival_slippage_bp = sum(
                s.arrival_slippage * w
                for s, w in zip(order_summaries, weights, strict=False)
            ) / total_w if total_w > 0 else 0

            report.avg_vwap_deviation_bp = sum(
                s.vwap_deviation * w
                for s, w in zip(order_summaries, weights, strict=False)
            ) / total_w if total_w > 0 else 0

            report.total_is_value = sum(
                s.implementation_shortfall for s in order_summaries
            )
            report.total_is_pct = report.total_is_value / report.total_value

        # 4. 评级分布
        for s in order_summaries:
            grade = s.grade.value
            report.grade_distribution[grade] = (
                report.grade_distribution.get(grade, 0) + 1
            )

        # 5. 生成洞察
        report.insights = self._generate_insights(order_summaries, report)

        # 6. 生成建议
        report.recommendations = self._generate_recommendations(
            order_summaries, report
        )

        # 7. 综合评分
        report.overall_score = self._calc_overall_score(report)

        logger.info(
            "执行复盘完成: %d 笔订单, 综合评分 %.1f, 平均决策滑点 %.2fbp",
            report.total_orders, report.overall_score,
            report.avg_decision_slippage_bp,
        )

        return report

    # ----------------------------------------------------------
    # 订单分组与摘要
    # ----------------------------------------------------------

    def _group_by_order(
        self, fills: list[FillRecord]
    ) -> dict[str, list[FillRecord]]:
        """按订单分组"""
        groups: dict[str, list[FillRecord]] = {}
        for f in fills:
            key = f.order_id or f"{f.symbol}_{f.side}_{f.algo}"
            if key not in groups:
                groups[key] = []
            groups[key].append(f)
        return groups

    def _summarize_order(
        self,
        order_id: str,
        fills: list[FillRecord],
    ) -> OrderExecutionSummary:
        """计算单订单执行摘要"""
        symbol = fills[0].symbol
        side = fills[0].side
        algo = fills[0].algo
        decision_price = fills[0].decision_price
        arrival_price = fills[0].arrival_price
        vwap_price = fills[0].vwap_price

        total_qty = sum(f.quantity for f in fills)
        total_value = sum(f.quantity * f.fill_price for f in fills)
        avg_price = total_value / total_qty if total_qty > 0 else 0
        total_fee = sum(f.fee for f in fills)

        # 滑点计算 (bp = 0.01%)
        # 买入: 成交价比决策价高 → 正滑点 (不利)
        # 卖出: 成交价比决策价低 → 正滑点 (不利)
        if decision_price > 0:
            if side == "BUY":
                decision_slip = (avg_price - decision_price) / decision_price * 10000
                arrival_slip = (avg_price - arrival_price) / arrival_price * 10000 if arrival_price > 0 else 0
                vwap_dev = (avg_price - vwap_price) / vwap_price * 10000 if vwap_price > 0 else 0
            else:
                decision_slip = (decision_price - avg_price) / decision_price * 10000
                arrival_slip = (arrival_price - avg_price) / arrival_price * 10000 if arrival_price > 0 else 0
                vwap_dev = (vwap_price - avg_price) / vwap_price * 10000 if vwap_price > 0 else 0
        else:
            decision_slip = 0
            arrival_slip = 0
            vwap_dev = 0

        # 实施差额 (元) — 带方向 (EX-8 修复): 买入成交价高于决策价=不利(正IS),
        # 卖出成交价低于决策价=不利(正IS)。负值表示成交更优, 反映有利滑点。
        # IS = (决策价 - 成交均价) × 数量, 按买卖方向展开:
        #   BUY:  IS = (decision - avg) × qty  →  avg>decision 时 IS 为负(不利)
        #   SELL: IS = (decision - avg) × qty  →  avg<decision 时 IS 为正(有利)
        # 为使"不利=正"、便于统计, 统一取 signed: 正=成本增加(不利)。
        if decision_price > 0:
            is_value = (avg_price - decision_price) * total_qty if side == "BUY" \
                       else (decision_price - avg_price) * total_qty
        else:
            is_value = 0.0

        # 市场冲击 vs 时机成本拆分 (取绝对值, 仅用于占比分析)
        if decision_price > 0 and arrival_price > 0:
            market_impact = abs(avg_price - arrival_price) * total_qty
            timing_cost = abs(arrival_price - decision_price) * total_qty
        else:
            market_impact = 0
            timing_cost = 0

        # 评级
        grade = self._grade_slippage(decision_slip)

        return OrderExecutionSummary(
            symbol=symbol,
            side=side,
            total_quantity=total_qty,
            filled_quantity=total_qty,
            fill_rate=1.0,
            avg_fill_price=avg_price,
            decision_price=decision_price,
            arrival_price=arrival_price,
            vwap_price=vwap_price,
            decision_slippage=round(decision_slip, 2),
            arrival_slippage=round(arrival_slip, 2),
            vwap_deviation=round(vwap_dev, 2),
            implementation_shortfall=round(is_value, 2),
            market_impact=round(market_impact, 2),
            timing_cost=round(timing_cost, 2),
            fee_total=round(total_fee, 2),
            grade=grade,
            algo=algo,
        )

    def _grade_slippage(self, slippage_bp: float) -> ExecutionGrade:
        """根据滑点评级"""
        if slippage_bp < self.SLIPPAGE_GOOD:
            return ExecutionGrade.A
        if slippage_bp < self.SLIPPAGE_OK:
            return ExecutionGrade.B
        if slippage_bp < self.SLIPPAGE_BAD:
            return ExecutionGrade.C
        return ExecutionGrade.D

    # ----------------------------------------------------------
    # 洞察生成
    # ----------------------------------------------------------

    def _generate_insights(
        self,
        summaries: list[OrderExecutionSummary],
        report: ExecutionReviewReport,
    ) -> list[ReviewInsight]:
        """生成复盘洞察"""
        insights: list[ReviewInsight] = []

        if not summaries:
            return insights

        # 洞察 1: 整体执行质量
        if report.avg_decision_slippage_bp < 5:
            insights.append(ReviewInsight(
                category="strength",
                title="执行质量优秀",
                detail=f"平均决策滑点 {report.avg_decision_slippage_bp:.1f}bp, "
                       f"低于 5bp 优秀线",
                impact="high",
                metric="avg_decision_slippage_bp",
                value=report.avg_decision_slippage_bp,
            ))
        elif report.avg_decision_slippage_bp > 20:
            insights.append(ReviewInsight(
                category="weakness",
                title="执行偏差较大",
                detail=f"平均决策滑点 {report.avg_decision_slippage_bp:.1f}bp, "
                       f"超过 20bp 警戒线",
                impact="high",
                metric="avg_decision_slippage_bp",
                value=report.avg_decision_slippage_bp,
            ))

        # 洞察 2: 市场冲击分析
        impact_ratio = (
            sum(s.market_impact for s in summaries)
            / max(sum(s.implementation_shortfall for s in summaries), 1)
        )
        if impact_ratio > 0.7:
            insights.append(ReviewInsight(
                category="weakness",
                title="市场冲击占比过高",
                detail=f"市场冲击占实施差额的 {impact_ratio:.0%}, "
                       f"建议减小订单参与率或延长执行时间",
                impact="medium",
                metric="market_impact_ratio",
                value=impact_ratio,
            ))

        # 洞察 3: 时机成本
        timing_ratio = (
            sum(s.timing_cost for s in summaries)
            / max(sum(s.implementation_shortfall for s in summaries), 1)
        )
        if timing_ratio > 0.5:
            insights.append(ReviewInsight(
                category="weakness",
                title="时机成本显著",
                detail=f"时机成本占实施差额的 {timing_ratio:.0%}, "
                       f"建议优化决策到执行的延迟",
                impact="medium",
                metric="timing_cost_ratio",
                value=timing_ratio,
            ))

        # 洞察 4: A 级订单占比
        a_count = report.grade_distribution.get("A", 0)
        a_ratio = a_count / len(summaries) if summaries else 0
        if a_ratio > 0.6:
            insights.append(ReviewInsight(
                category="strength",
                title="优质订单占比高",
                detail=f"A 级执行订单 {a_count} 笔, 占比 {a_ratio:.0%}",
                impact="medium",
                metric="a_grade_ratio",
                value=a_ratio,
            ))

        # 洞察 5: D 级订单
        d_count = report.grade_distribution.get("D", 0)
        if d_count > 0:
            d_orders = [s for s in summaries if s.grade == ExecutionGrade.D]
            d_symbols = ", ".join(s.symbol for s in d_orders[:5])
            insights.append(ReviewInsight(
                category="weakness",
                title=f"{d_count} 笔订单执行较差",
                detail=f"D 级订单: {d_symbols}, 建议排查流动性或算法选择问题",
                impact="high",
                metric="d_grade_count",
                value=d_count,
            ))

        # 洞察 6: 买入 vs 卖出
        buy_fills = [s for s in summaries if s.side == "BUY"]
        sell_fills = [s for s in summaries if s.side == "SELL"]
        if buy_fills and sell_fills:
            buy_slip = np.mean([s.decision_slippage for s in buy_fills])
            sell_slip = np.mean([s.decision_slippage for s in sell_fills])
            if abs(buy_slip - sell_slip) > 10:
                worse = "买入" if buy_slip > sell_slip else "卖出"
                insights.append(ReviewInsight(
                    category="weakness",
                    title=f"{worse}方向执行质量较差",
                    detail=f"买入滑点 {buy_slip:.1f}bp vs 卖出滑点 {sell_slip:.1f}bp",
                    impact="medium",
                    metric="buy_sell_slippage_diff",
                    value=abs(buy_slip - sell_slip),
                ))

        return insights

    # ----------------------------------------------------------
    # 建议生成
    # ----------------------------------------------------------

    def _generate_recommendations(
        self,
        summaries: list[OrderExecutionSummary],
        report: ExecutionReviewReport,
    ) -> list[str]:
        """生成改进建议"""
        recs: list[str] = []

        if not summaries:
            return recs

        # 建议 1: 滑点过大
        if report.avg_decision_slippage_bp > 20:
            recs.append(
                "平均滑点超过 20bp, 建议:"
                "1) 减少单笔订单规模; "
                "2) 延长执行时间窗口; "
                "3) 改用 VWAP 或 POV 算法"
            )

        # 建议 2: 市场冲击
        impact_ratio = (
            sum(s.market_impact for s in summaries)
            / max(sum(s.implementation_shortfall for s in summaries), 1)
        )
        if impact_ratio > 0.6:
            recs.append(
                "市场冲击占比过高, 建议降低 POV 参与率目标 (当前可能过高), "
                "或拆分为更多小额订单"
            )

        # 建议 3: 算法选择
        algo_stats: dict[str, list[float]] = {}
        for s in summaries:
            if s.algo:
                algo_stats.setdefault(s.algo, []).append(s.decision_slippage)

        if len(algo_stats) > 1:
            best_algo = min(algo_stats.keys(),
                            key=lambda a: np.mean(algo_stats[a]))
            worst_algo = max(algo_stats.keys(),
                             key=lambda a: np.mean(algo_stats[a]))
            best_avg = np.mean(algo_stats[best_algo])
            worst_avg = np.mean(algo_stats[worst_algo])
            if worst_avg - best_avg > 10:
                recs.append(
                    f"{best_algo} 算法表现最优 (滑点 {best_avg:.1f}bp), "
                    f"{worst_algo} 表现最差 (滑点 {worst_avg:.1f}bp), "
                    f"建议更多使用 {best_algo}"
                )

        # 建议 4: D 级订单
        d_orders = [s for s in summaries if s.grade == ExecutionGrade.D]
        if len(d_orders) >= 2:
            recs.append(
                f"{len(d_orders)} 笔 D 级订单, 建议对这些标的:"
                "1) 检查流动性是否充足; "
                "2) 考虑使用冰山订单 (ICEBERG); "
                "3) 避开开盘/收盘高波动时段"
            )

        # 建议 5: 交易时段
        # (简单实现: 实际需按时段统计)
        if len(summaries) > 5 and report.avg_decision_slippage_bp > 10:
            recs.append(
                "建议按交易时段 (开盘/午盘/收盘) 分别统计执行质量, "
                "识别高滑点时段并避开"
            )

        if not recs:
            recs.append("执行质量良好, 继续保持当前执行策略")

        return recs

    # ----------------------------------------------------------
    # 综合评分
    # ----------------------------------------------------------

    def _calc_overall_score(self, report: ExecutionReviewReport) -> float:
        """计算综合评分 (0-100)"""
        score = 100.0

        # 滑点扣分 (占 50 分)
        slip = abs(report.avg_decision_slippage_bp)
        if slip <= 5:
            score -= 0
        elif slip <= 15:
            score -= (slip - 5) * 1.5
        elif slip <= 30:
            score -= 15 + (slip - 15) * 2
        else:
            score -= 45 + min(30, (slip - 30) * 1)

        # VWAP 偏离扣分 (占 20 分)
        vwap_dev = abs(report.avg_vwap_deviation_bp)
        if vwap_dev <= 3:
            score -= 0
        elif vwap_dev <= 10:
            score -= (vwap_dev - 3) * 1.0
        else:
            score -= 7 + min(13, (vwap_dev - 10) * 0.5)

        # 订单评级分布 (占 20 分)
        total = max(report.total_orders, 1)
        a = report.grade_distribution.get("A", 0)
        b = report.grade_distribution.get("B", 0)
        c = report.grade_distribution.get("C", 0)
        d = report.grade_distribution.get("D", 0)
        grade_score = (a * 20 + b * 15 + c * 8 + d * 0) / total
        score -= (20 - grade_score)

        # 手续费率 (占 10 分)
        if report.total_value > 0:
            fee_rate = report.total_fee / report.total_value * 10000  # bp
            if fee_rate <= 2:
                score -= 0
            elif fee_rate <= 5:
                score -= (fee_rate - 2) * 2
            else:
                score -= 6 + min(4, (fee_rate - 5) * 0.5)

        return max(0, min(100, round(score, 1)))

    # ----------------------------------------------------------
    # 报告导出
    # ----------------------------------------------------------

    def save_report(
        self,
        report: ExecutionReviewReport,
        output_dir: str,
    ) -> str:
        """保存报告为 JSON"""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"execution_review_{report.report_date}.json"
        filepath = out_dir / filename

        filepath.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("执行复盘报告已保存: %s", filepath)
        return str(filepath)

    def generate_markdown_report(
        self, report: ExecutionReviewReport
    ) -> str:
        """生成 Markdown 格式报告"""
        lines = [
            f"# 执行复盘报告 — {report.report_date}",
            "",
            f"## 综合评分: **{report.overall_score}/100**",
            "",
            "### 核心指标",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 订单总数 | {report.total_orders} 笔 |",
            f"| 成交笔数 | {report.total_fills} 笔 |",
            f"| 总成交金额 | ¥{report.total_value:,.0f} |",
            f"| 总手续费 | ¥{report.total_fee:,.2f} |",
            f"| 平均决策滑点 | {report.avg_decision_slippage_bp:.2f} bp |",
            f"| 平均到达滑点 | {report.avg_arrival_slippage_bp:.2f} bp |",
            f"| VWAP 偏离 | {report.avg_vwap_deviation_bp:.2f} bp |",
            f"| 实施差额 (IS) | ¥{report.total_is_value:,.2f} ({report.total_is_pct:.2%}) |",
            "",
            "### 执行质量分布",
            "",
        ]

        for grade in ["A", "B", "C", "D"]:
            count = report.grade_distribution.get(grade, 0)
            pct = count / report.total_orders * 100 if report.total_orders else 0
            lines.append(f"- **{grade} 级**: {count} 笔 ({pct:.1f}%)")

        lines.extend(["", "### 关键洞察", ""])

        for insight in report.insights:
            icon = "✅" if insight.category == "strength" else "⚠️" if insight.category == "weakness" else "💡"
            lines.append(f"{icon} **{insight.title}** — {insight.detail}")

        lines.extend(["", "### 改进建议", ""])
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {rec}")

        lines.extend(["", "---", f"_生成时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}_"])
        return "\n".join(lines)


__all__ = [
    "ExecutionGrade",
    "ExecutionReviewReport",
    "ExecutionReviewer",
    "FillRecord",
    "OrderExecutionSummary",
    "ReviewInsight",
]
