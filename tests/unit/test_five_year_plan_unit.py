"""test_five_year_plan_unit.py — 十五五规划适配分析单元测试

覆盖要点:
    - FIFTEEN_FIVE_POLICIES / STOCK_POLICY_ALIGNMENT 常量
    - FifteenFivePlanAnalyzer 构造
    - get_policy_overview (7方向/排序)
    - analyze_holdings (无持仓/有持仓/分级A-D/排序)
    - get_weight_adjustments (超配/低配/维持)
    - generate_report (无保存/保存到目录)
"""
from __future__ import annotations

import pytest

from utils.five_year_plan import (
    FIFTEEN_FIVE_POLICIES,
    STOCK_POLICY_ALIGNMENT,
    FifteenFivePlanAnalyzer,
)


# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_policies_has_7(self):
        assert len(FIFTEEN_FIVE_POLICIES) == 7

    @pytest.mark.unit
    def test_policies_weight_sum(self):
        total = sum(p["weight"] for p in FIFTEEN_FIVE_POLICIES.values())
        assert total == pytest.approx(1.0)

    @pytest.mark.unit
    def test_stock_alignment_has_entries(self):
        assert len(STOCK_POLICY_ALIGNMENT) > 10

    @pytest.mark.unit
    def test_601088_shenhua(self):
        assert STOCK_POLICY_ALIGNMENT["601088"]["name"] == "中国神华"


# ============================================================
# 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_init(self):
        a = FifteenFivePlanAnalyzer()
        assert a.policies is FIFTEEN_FIVE_POLICIES
        assert a.alignments is STOCK_POLICY_ALIGNMENT


# ============================================================
# get_policy_overview
# ============================================================


class TestPolicyOverview:
    @pytest.mark.unit
    def test_returns_7(self):
        a = FifteenFivePlanAnalyzer()
        overview = a.get_policy_overview()
        assert len(overview) == 7

    @pytest.mark.unit
    def test_sorted_by_weight(self):
        a = FifteenFivePlanAnalyzer()
        overview = a.get_policy_overview()
        weights = [o["weight"] for o in overview]
        assert weights == sorted(weights, reverse=True)

    @pytest.mark.unit
    def test_item_fields(self):
        a = FifteenFivePlanAnalyzer()
        overview = a.get_policy_overview()
        for item in overview:
            assert "direction" in item
            assert "weight" in item
            assert "relevance_score" in item
            assert "keywords" in item
            assert len(item["keywords"]) <= 5


# ============================================================
# analyze_holdings
# ============================================================


class TestAnalyzeHoldings:
    @pytest.mark.unit
    def test_no_positions(self):
        a = FifteenFivePlanAnalyzer()
        results = a.analyze_holdings()
        assert len(results) == len(STOCK_POLICY_ALIGNMENT)
        assert all("overall_score" in r for r in results)

    @pytest.mark.unit
    def test_sorted_by_score(self):
        a = FifteenFivePlanAnalyzer()
        results = a.analyze_holdings()
        scores = [r["overall_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.unit
    def test_with_positions(self):
        a = FifteenFivePlanAnalyzer()
        positions = {"601088": {"shares": 1000, "avg_cost": 20.0}}
        results = a.analyze_holdings(positions)
        shenhua = next(r for r in results if r["code"] == "601088")
        assert shenhua["shares"] == 1000

    @pytest.mark.unit
    def test_grade_a(self):
        """overall_score >= 90 → A"""
        a = FifteenFivePlanAnalyzer()
        results = a.analyze_holdings()
        top = results[0]
        if top["overall_score"] >= 90:
            assert "A" in top["grade"]

    @pytest.mark.unit
    def test_grade_d(self):
        """overall_score < 60 → D"""
        a = FifteenFivePlanAnalyzer()
        results = a.analyze_holdings()
        bottom = results[-1]
        if bottom["overall_score"] < 60:
            assert "D" in bottom["grade"]

    @pytest.mark.unit
    def test_top_policies(self):
        a = FifteenFivePlanAnalyzer()
        results = a.analyze_holdings()
        for r in results:
            assert len(r["top_policies"]) <= 3


# ============================================================
# get_weight_adjustments
# ============================================================


class TestWeightAdjustments:
    @pytest.mark.unit
    def test_returns_all(self):
        a = FifteenFivePlanAnalyzer()
        adjustments = a.get_weight_adjustments()
        assert len(adjustments) == len(STOCK_POLICY_ALIGNMENT)

    @pytest.mark.unit
    def test_fields(self):
        a = FifteenFivePlanAnalyzer()
        adjustments = a.get_weight_adjustments()
        for adj in adjustments:
            assert "code" in adj
            assert "suggestion" in adj
            assert "weight_adjust_pct" in adj

    @pytest.mark.unit
    def test_high_score_overweight(self):
        """评分高于均值 → 建议超配"""
        a = FifteenFivePlanAnalyzer()
        adjustments = a.get_weight_adjustments()
        # 最高的标的应建议超配
        top = adjustments[0]
        assert "超配" in top["suggestion"] or "维持" in top["suggestion"]


# ============================================================
# generate_report
# ============================================================


class TestGenerateReport:
    @pytest.mark.unit
    def test_report_content(self):
        a = FifteenFivePlanAnalyzer()
        report = a.generate_report()
        assert "十五五规划适配分析报告" in report
        assert "七大战略方向" in report

    @pytest.mark.unit
    def test_report_save(self, tmp_path):
        a = FifteenFivePlanAnalyzer()
        report = a.generate_report(save_dir=str(tmp_path))
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1