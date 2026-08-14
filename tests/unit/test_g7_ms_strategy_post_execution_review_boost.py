"""G7 boost: ms_strategy/src/execution/post_execution_review.py 单元测试.

覆盖 ExecutionReviewer 滑点/冲击/延迟分析、评级、洞察、建议、报告生成全部公开接口,
包括空成交、零决策价、买卖方向差异、D 级订单等异常分支.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ms_strategy.src.execution.post_execution_review import (  # noqa: E402
    ExecutionGrade,
    ExecutionReviewer,
    FillRecord,
    ReviewInsight,
)


def _make_fill(symbol="510300.SH", side="BUY", qty=1000,
               fill_price=4.50, decision_price=4.49, arrival_price=4.495,
               vwap_price=4.498, algo="TWAP", order_id="O1", fee=10.0):
    return FillRecord(
        symbol=symbol, side=side, quantity=qty, fill_price=fill_price,
        decision_price=decision_price, arrival_price=arrival_price,
        vwap_price=vwap_price, algo=algo, order_id=order_id, fee=fee,
    )


# ============================================================
# 1. 数据模型
# ============================================================


class TestDataModels:
    def test_fill_record(self):
        f = _make_fill()
        assert f.symbol == "510300.SH"
        assert f.quantity == 1000

    def test_grade_enum(self):
        assert ExecutionGrade.A.value == "A"
        assert ExecutionGrade.D.value == "D"

    def test_review_insight_defaults(self):
        ri = ReviewInsight(category="strength", title="t", detail="d")
        assert ri.impact == "medium"
        assert ri.value == 0.0

    def test_report_to_dict(self):
        reviewer = ExecutionReviewer()
        report = reviewer.review([_make_fill()])
        d = report.to_dict()
        assert d["total_orders"] == 1
        assert "insights" in d
        assert "recommendations" in d
        assert isinstance(d["overall_score"], float)


# ============================================================
# 2. 评级
# ============================================================


class TestGradeSlippage:
    def test_grade_a(self):
        reviewer = ExecutionReviewer()
        assert reviewer._grade_slippage(3.0) == ExecutionGrade.A

    def test_grade_b(self):
        reviewer = ExecutionReviewer()
        assert reviewer._grade_slippage(10.0) == ExecutionGrade.B

    def test_grade_c(self):
        reviewer = ExecutionReviewer()
        assert reviewer._grade_slippage(20.0) == ExecutionGrade.C

    def test_grade_d(self):
        reviewer = ExecutionReviewer()
        assert reviewer._grade_slippage(40.0) == ExecutionGrade.D


# ============================================================
# 3. 订单分组与摘要
# ============================================================


class TestGroupByOrder:
    def test_with_order_id(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(order_id="O1"), _make_fill(order_id="O1")]
        groups = reviewer._group_by_order(fills)
        assert set(groups.keys()) == {"O1"}

    def test_without_order_id(self):
        reviewer = ExecutionReviewer()
        f1 = _make_fill(order_id="", symbol="X", side="BUY", algo="TWAP")
        f2 = _make_fill(order_id="", symbol="X", side="BUY", algo="TWAP")
        groups = reviewer._group_by_order([f1, f2])
        assert "X_BUY_TWAP" in groups


class TestSummarizeOrder:
    def test_buy(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(side="BUY", fill_price=4.50, decision_price=4.49)]
        s = reviewer._summarize_order("O1", fills)
        assert s.side == "BUY"
        assert s.decision_slippage > 0
        assert s.filled_quantity == 1000

    def test_sell(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(side="SELL", fill_price=4.49, decision_price=4.50)]
        s = reviewer._summarize_order("O1", fills)
        assert s.side == "SELL"
        assert s.decision_slippage > 0

    def test_zero_decision_price(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(decision_price=0, arrival_price=0)]
        s = reviewer._summarize_order("O1", fills)
        assert s.decision_slippage == 0
        assert s.arrival_slippage == 0
        assert s.vwap_deviation == 0
        assert s.market_impact == 0
        assert s.timing_cost == 0

    def test_zero_arrival_price(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(arrival_price=0)]
        s = reviewer._summarize_order("O1", fills)
        assert s.arrival_slippage == 0
        assert s.market_impact == 0

    def test_multi_fill_avg(self):
        reviewer = ExecutionReviewer()
        f1 = _make_fill(qty=500, fill_price=4.50, order_id="O1")
        f2 = _make_fill(qty=500, fill_price=4.52, order_id="O1")
        s = reviewer._summarize_order("O1", [f1, f2])
        assert s.filled_quantity == 1000
        assert s.avg_fill_price == pytest.approx(4.51)


# ============================================================
# 4. review 主入口
# ============================================================


class TestReview:
    def test_empty_fills(self):
        reviewer = ExecutionReviewer()
        report = reviewer.review([])
        assert report.total_orders == 0
        assert report.overall_score == 0.0
        assert len(report.insights) == 1
        assert report.insights[0].title == "无成交记录"

    def test_normal(self):
        reviewer = ExecutionReviewer()
        report = reviewer.review([_make_fill()])
        assert report.total_orders == 1
        assert report.total_fills == 1
        assert report.total_value > 0
        assert len(report.order_summaries) == 1

    def test_grade_distribution(self):
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(fill_price=4.4901, decision_price=4.4900, order_id="A1"),
            _make_fill(fill_price=4.60, decision_price=4.50, order_id="D1"),
        ]
        report = reviewer.review(fills)
        assert report.grade_distribution.get("A", 0) >= 1
        assert report.grade_distribution.get("D", 0) >= 1


# ============================================================
# 5. 洞察生成
# ============================================================


class TestInsights:
    def test_excellent_execution(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.4900, decision_price=4.4900,
                            arrival_price=4.4900, vwap_price=4.4900)]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert "执行质量优秀" in titles

    def test_large_slippage(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.60, decision_price=4.50)]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert "执行偏差较大" in titles

    def test_d_grade_orders(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=5.00, decision_price=4.00)]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert any("执行较差" in t for t in titles)

    def test_a_grade_high_ratio(self):
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(fill_price=4.4901, decision_price=4.4900, order_id="A1"),
            _make_fill(fill_price=4.4901, decision_price=4.4900, order_id="A2"),
            _make_fill(fill_price=4.4910, decision_price=4.4900, order_id="C1"),
        ]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert "优质订单占比高" in titles

    def test_buy_sell_diff(self):
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(side="BUY", fill_price=4.60, decision_price=4.50, order_id="B1"),
            _make_fill(side="SELL", fill_price=4.50, decision_price=4.50, order_id="S1"),
        ]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert any("方向执行质量较差" in t for t in titles)

    def test_market_impact_high(self):
        """市场冲击占比过高."""
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.55, arrival_price=4.50, decision_price=4.52)]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert "市场冲击占比过高" in titles

    def test_timing_cost_significant(self):
        """时机成本显著."""
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.53, arrival_price=4.50, decision_price=4.55)]
        report = reviewer.review(fills)
        titles = [i.title for i in report.insights]
        assert "时机成本显著" in titles


# ============================================================
# 6. 建议生成
# ============================================================


class TestRecommendations:
    def test_default_good(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.4900, decision_price=4.4900,
                            arrival_price=4.4900, vwap_price=4.4900)]
        report = reviewer.review(fills)
        assert any("执行质量良好" in r for r in report.recommendations)

    def test_large_slippage(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.60, decision_price=4.50)]
        report = reviewer.review(fills)
        assert any("平均滑点超过 20bp" in r for r in report.recommendations)

    def test_d_grade_orders(self):
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(fill_price=5.00, decision_price=4.00, order_id="D1"),
            _make_fill(fill_price=5.00, decision_price=4.00, order_id="D2"),
        ]
        report = reviewer.review(fills)
        assert any("D 级订单" in r for r in report.recommendations)

    def test_algo_comparison(self):
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(fill_price=4.4900, decision_price=4.4900, algo="TWAP", order_id="T1"),
            _make_fill(fill_price=4.60, decision_price=4.50, algo="VWAP", order_id="V1"),
        ]
        report = reviewer.review(fills)
        assert any("TWAP" in r and "VWAP" in r for r in report.recommendations)

    def test_market_impact_recommendation(self):
        """市场冲击占比过高建议."""
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.55, arrival_price=4.50, decision_price=4.52)]
        report = reviewer.review(fills)
        assert any("市场冲击" in r for r in report.recommendations)

    def test_session_recommendation(self):
        """len > 5 + avg slip > 10 → 时段建议."""
        reviewer = ExecutionReviewer()
        fills = [
            _make_fill(fill_price=4.515, decision_price=4.50, order_id=f"O{i}")
            for i in range(6)
        ]
        report = reviewer.review(fills)
        assert any("交易时段" in r for r in report.recommendations)


# ============================================================
# 7. 综合评分
# ============================================================


class TestOverallScore:
    def test_perfect_score(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=4.4900, decision_price=4.4900,
                            arrival_price=4.4900, vwap_price=4.4900, fee=0.0)]
        report = reviewer.review(fills)
        assert report.overall_score == 100.0

    def test_poor_score(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill(fill_price=5.00, decision_price=4.00)]
        report = reviewer.review(fills)
        assert report.overall_score < 50

    def test_score_range(self):
        reviewer = ExecutionReviewer()
        fills = [_make_fill()]
        report = reviewer.review(fills)
        assert 0 <= report.overall_score <= 100


# ============================================================
# 8. 报告导出
# ============================================================


class TestReportExport:
    def test_save_report(self, tmp_path):
        reviewer = ExecutionReviewer()
        report = reviewer.review([_make_fill()])
        path = reviewer.save_report(report, str(tmp_path))
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["total_orders"] == 1

    def test_generate_markdown(self):
        reviewer = ExecutionReviewer()
        report = reviewer.review([_make_fill()])
        md = reviewer.generate_markdown_report(report)
        assert "执行复盘报告" in md
        assert "综合评分" in md
        assert "核心指标" in md

    def test_generate_markdown_empty(self):
        reviewer = ExecutionReviewer()
        report = reviewer.review([])
        md = reviewer.generate_markdown_report(report)
        assert "执行复盘报告" in md
