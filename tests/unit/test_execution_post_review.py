"""ms_strategy.src.execution.post_execution_review 单元测试 — ExecutionReviewer.review/save/markdown"""
from __future__ import annotations

from datetime import datetime

import pytest

from ms_strategy.src.execution.post_execution_review import (
    ExecutionGrade,
    ExecutionReviewer,
    FillRecord,
    OrderExecutionSummary,
)


def _fill(symbol="510300.SH", side="BUY", quantity=1000, fill_price=4.00,
          decision_price=4.00, arrival_price=4.00, vwap_price=4.00,
          algo="ICEBERG", order_id="ORD1") -> FillRecord:
    return FillRecord(
        symbol=symbol, side=side, quantity=quantity,
        fill_price=fill_price, decision_price=decision_price,
        arrival_price=arrival_price, vwap_price=vwap_price,
        algo=algo, order_id=order_id,
    )


def test_review_empty_fills():
    """无成交记录 → overall_score=0 且产生 weakness 洞察"""
    reviewer = ExecutionReviewer()
    report = reviewer.review([])
    assert report.overall_score == 0.0
    assert any(i.category == "weakness" for i in report.insights)


def test_review_single_order_grade_a():
    """零滑点成交 → 评级 A, 综合评分满分"""
    reviewer = ExecutionReviewer()
    fills = [_fill(fill_price=4.000, decision_price=4.000,
                   arrival_price=4.000, vwap_price=4.000)]
    report = reviewer.review(fills)
    assert report.total_orders == 1
    assert report.total_fills == 1
    assert report.order_summaries[0].grade == ExecutionGrade.A
    # 零滑点 → 满分
    assert report.overall_score == pytest.approx(100.0, abs=1e-6)


def test_review_slippage_calculation_buy():
    """BUY 成交价高于决策价 → 正滑点 (不利), 评级随滑点升高下降"""
    reviewer = ExecutionReviewer()
    # 成交 4.04 vs 决策 4.00 → 滑点 100bp → D 级
    fills = [_fill(fill_price=4.04, decision_price=4.00,
                   arrival_price=4.01, vwap_price=4.02)]
    report = reviewer.review(fills)
    s = report.order_summaries[0]
    assert s.decision_slippage > 0
    assert s.grade == ExecutionGrade.D
    # D 级应拉低综合评分
    assert report.overall_score < 100.0


def test_review_implementation_shortfall():
    """实施差额 (IS) = |成交价-决策价| × 数量"""
    reviewer = ExecutionReviewer()
    fills = [_fill(fill_price=4.05, decision_price=4.00, quantity=2000,
                   arrival_price=4.02, vwap_price=4.03)]
    report = reviewer.review(fills)
    s = report.order_summaries[0]
    # IS = (4.05-4.00)*2000 = 100
    assert s.implementation_shortfall == pytest.approx(100.0, abs=1e-6)
    assert report.total_is_value == pytest.approx(100.0, abs=1e-6)


def test_review_grade_distribution():
    """多订单评级分布正确累计"""
    reviewer = ExecutionReviewer()
    fills = [
        _fill(order_id="O1", fill_price=4.00, decision_price=4.00),  # A
        _fill(order_id="O2", fill_price=4.04, decision_price=4.00),  # D
    ]
    report = reviewer.review(fills)
    assert report.grade_distribution.get("A", 0) == 1
    assert report.grade_distribution.get("D", 0) == 1


def test_review_save_report(tmp_path):
    """save_report: 落盘 JSON 到 tmp_path 且可回读"""
    reviewer = ExecutionReviewer()
    fills = [_fill(fill_price=4.005, decision_price=4.000)]
    report = reviewer.review(fills)
    path = reviewer.save_report(report, str(tmp_path))
    import json
    data = json.loads(__import__("pathlib").Path(path).read_text(encoding="utf-8"))
    assert data["total_orders"] == 1
    assert "overall_score" in data


def test_review_markdown_report():
    """generate_markdown_report: 返回非空 Markdown 字符串"""
    reviewer = ExecutionReviewer()
    fills = [_fill(fill_price=4.002, decision_price=4.000)]
    report = reviewer.review(fills)
    md = reviewer.generate_markdown_report(report)
    assert isinstance(md, str)
    assert "执行复盘报告" in md
    assert "综合评分" in md


def test_review_recommendations_when_high_slippage():
    """高滑点 (>20bp) → 生成改进建议"""
    reviewer = ExecutionReviewer()
    fills = [_fill(order_id="O1", fill_price=4.06, decision_price=4.00,
                   arrival_price=4.03, vwap_price=4.04)]
    report = reviewer.review(fills)
    assert len(report.recommendations) > 0


def test_order_execution_summary_dataclass():
    """OrderExecutionSummary 字段完整性 (to_dict 结构)"""
    s = OrderExecutionSummary(
        symbol="X", side="BUY", total_quantity=100, filled_quantity=100,
        fill_rate=1.0, avg_fill_price=4.0, decision_price=4.0,
        arrival_price=4.0, vwap_price=4.0,
        decision_slippage=1.0, arrival_slippage=1.0, vwap_deviation=1.0,
        implementation_shortfall=0.0, market_impact=0.0, timing_cost=0.0,
        fee_total=0.0, grade=ExecutionGrade.A,
    )
    assert s.fill_rate == 1.0
    assert s.grade == ExecutionGrade.A
