# -*- coding: utf-8 -*-
"""social_security_etf 单元测试 — 社保基金ETF风格追踪全分支覆盖

被测模块: utils/social_security_etf.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.social_security_etf import (  # noqa: E402
    NATIONAL_TEAM_SIGNAL_CONFIG,
    SOCIAL_SECURITY_STYLES,
    NationalTeamSignalDetector,
    SocialSecurityETFTracker,
    SocialSecurityStyleClassifier,
)


class TestStyleConfig:
    def test_four_styles(self):
        assert set(SOCIAL_SECURITY_STYLES.keys()) == {"顺周期", "高端制造", "资源", "防御"}

    def test_weights_sum_to_one(self):
        total = sum(s["weight"] for s in SOCIAL_SECURITY_STYLES.values())
        assert total == pytest.approx(1.0)

    def test_each_style_has_required_keys(self):
        required = {"weight", "description", "representative_stocks",
                    "matching_etfs", "cycle_signal", "recommended_action"}
        for name, cfg in SOCIAL_SECURITY_STYLES.items():
            assert required.issubset(cfg.keys()), name
            assert cfg["matching_etfs"]
            for etf in cfg["matching_etfs"]:
                assert {"code", "name", "category", "match_score"}.issubset(etf.keys())

    def test_no_duplicate_etf_codes_across_styles(self):
        codes = [e["code"] for s in SOCIAL_SECURITY_STYLES.values() for e in s["matching_etfs"]]
        assert len(codes) == len(set(codes))


class TestStyleClassifier:
    def test_classify_known_etf(self):
        c = SocialSecurityStyleClassifier()
        r = c.classify_etf("588000")
        assert r is not None
        assert r["social_style"] == "高端制造"
        assert r["match_score"] == 95
        assert r["style_weight"] == pytest.approx(0.35)

    def test_classify_defense_etf(self):
        c = SocialSecurityStyleClassifier()
        r = c.classify_etf("512170")
        assert r["social_style"] == "防御"
        assert r["recommended_action"] == "标配"

    def test_classify_unknown_returns_none(self):
        c = SocialSecurityStyleClassifier()
        assert c.classify_etf("999999") is None

    def test_get_all_classifications_sorted_desc(self):
        c = SocialSecurityStyleClassifier()
        results = c.get_all_etf_classifications()
        scores = [r["match_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_get_all_classifications_no_duplicates(self):
        c = SocialSecurityStyleClassifier()
        results = c.get_all_etf_classifications()
        codes = [r["code"] for r in results]
        assert len(codes) == len(set(codes))

    def test_get_all_classifications_count(self):
        c = SocialSecurityStyleClassifier()
        results = c.get_all_etf_classifications()
        expected = sum(len(s["matching_etfs"]) for s in SOCIAL_SECURITY_STYLES.values())
        assert len(results) == expected

    def test_get_style_summary(self):
        c = SocialSecurityStyleClassifier()
        summary = c.get_style_summary()
        assert set(summary.keys()) == set(SOCIAL_SECURITY_STYLES.keys())
        for name, info in summary.items():
            assert info["etf_count"] == len(SOCIAL_SECURITY_STYLES[name]["matching_etfs"])
            assert info["stock_count"] == len(SOCIAL_SECURITY_STYLES[name]["representative_stocks"])
            assert len(info["top_etfs"]) <= 3
            assert len(info["top_stocks"]) <= 3


class TestSignalDetector:
    def test_high_buy_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 60, "trend": "上升", "name": "科创50"}})
        assert len(signals) == 1
        assert signals[0]["confidence"] == "高"
        assert signals[0]["signal_type"] == "国家队强加仓信号"
        assert signals[0]["social_style"] == "高端制造"

    def test_medium_buy_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 20}})
        assert signals[0]["confidence"] == "中"
        assert signals[0]["signal_type"] == "国家队加仓信号"

    def test_low_buy_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 5}})
        assert signals[0]["confidence"] == "低"
        assert signals[0]["signal_type"] == "国家队关注信号"

    def test_high_sell_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": -60}})
        assert signals[0]["confidence"] == "高"
        assert signals[0]["signal_type"] == "国家队强减仓信号"

    def test_medium_sell_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": -20}})
        assert signals[0]["confidence"] == "中"
        assert signals[0]["signal_type"] == "国家队减仓信号"

    def test_low_sell_signal(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": -5}})
        assert signals[0]["confidence"] == "低"
        assert signals[0]["signal_type"] == "国家队减持关注"

    def test_no_signal_when_below_threshold(self):
        d = NationalTeamSignalDetector()
        assert d.detect_signals({"588000": {"net_flow_yi": 1}}) == []
        assert d.detect_signals({"588000": {"net_flow_yi": 0}}) == []
        assert d.detect_signals({"588000": {"net_flow_yi": -1}}) == []

    def test_unknown_etf_unmatched_style(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"999999": {"net_flow_yi": 60, "name": "x"}})
        assert signals[0]["social_style"] == "未匹配"
        assert signals[0]["style_recommendation"] == "-"

    def test_default_name_and_category(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 60}})
        assert signals[0]["name"] == "588000"
        assert signals[0]["category"] == "未知"

    def test_sorting_by_confidence_then_flow(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({
            "588000": {"net_flow_yi": 20, "name": "a"},
            "512880": {"net_flow_yi": 60, "name": "b"},
        })
        assert signals[0]["confidence"] == "高"
        assert signals[0]["code"] == "512880"

    def test_empty_flow_data(self):
        d = NationalTeamSignalDetector()
        assert d.detect_signals({}) == []


class TestStyleFlowSummary:
    def test_action_increase_when_total_high(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 60, "name": "a"}})
        summary = d.get_style_flow_summary(signals)
        assert summary["高端制造"]["action"] == "增持"
        assert summary["高端制造"]["strong_buy"] == 1

    def test_action_decrease_when_total_low(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": -60, "name": "a"}})
        summary = d.get_style_flow_summary(signals)
        assert summary["高端制造"]["action"] == "减持"
        assert summary["高端制造"]["strong_sell"] == 1

    def test_action_watch_when_small_positive(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": 5, "name": "a"}})
        summary = d.get_style_flow_summary(signals)
        assert summary["高端制造"]["action"] == "关注"

    def test_action_hold_when_small_negative(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"588000": {"net_flow_yi": -5, "name": "a"}})
        summary = d.get_style_flow_summary(signals)
        assert summary["高端制造"]["action"] == "观望"

    def test_unmatched_style_grouped(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({"999999": {"net_flow_yi": 60, "name": "x"}})
        summary = d.get_style_flow_summary(signals)
        assert "未匹配" in summary

    def test_empty_signals(self):
        d = NationalTeamSignalDetector()
        assert d.get_style_flow_summary([]) == {}

    def test_same_style_accumulates_across_signals(self):
        d = NationalTeamSignalDetector()
        signals = d.detect_signals({
            "588000": {"net_flow_yi": 5, "name": "a"},
            "512760": {"net_flow_yi": 5, "name": "b"},
        })
        summary = d.get_style_flow_summary(signals)
        assert summary["高端制造"]["signal_count"] == 2
        assert summary["高端制造"]["total_flow_yi"] == pytest.approx(10)
        assert summary["高端制造"]["action"] == "关注"


class TestTrackerAnalyze:
    def test_analyze_without_flow_data(self):
        t = SocialSecurityETFTracker()
        r = t.analyze()
        assert set(r["style_summary"].keys()) == set(SOCIAL_SECURITY_STYLES.keys())
        assert r["signals"] == []
        assert r["style_flows"] == {}
        assert len(r["recommendations"]) == 4

    def test_analyze_with_flow_data(self):
        t = SocialSecurityETFTracker()
        r = t.analyze({"588000": {"net_flow_yi": 60, "name": "a"}})
        assert len(r["signals"]) == 1
        assert "高端制造" in r["style_flows"]

    def test_analyze_recommendations_content(self):
        t = SocialSecurityETFTracker()
        r = t.analyze()
        for rec in r["recommendations"]:
            assert {"style", "target_weight", "action", "rationale", "matching_etfs"}.issubset(rec.keys())
            assert len(rec["matching_etfs"]) <= 2


class TestGenerateReport:
    def test_report_without_flow_data(self):
        t = SocialSecurityETFTracker()
        report = t.generate_report()
        assert "社保基金ETF风格追踪报告" in report
        assert "投资建议" in report
        assert "ETF与社保基金风格映射" in report

    def test_report_with_flow_data_includes_signals(self):
        t = SocialSecurityETFTracker()
        report = t.generate_report({"588000": {"net_flow_yi": 60, "name": "科创50"}})
        assert "国家队资金流向信号" in report

    def test_report_saved_to_file(self, tmp_path):
        t = SocialSecurityETFTracker()
        t.generate_report({"588000": {"net_flow_yi": 60, "name": "a"}}, save_dir=str(tmp_path))
        files = list(Path(str(tmp_path)).glob("*.md"))
        assert len(files) == 1
        assert "社保基金ETF追踪" in files[0].name

    def test_report_saved_without_flow_data(self, tmp_path):
        t = SocialSecurityETFTracker()
        t.generate_report(save_dir=str(tmp_path))
        files = list(Path(str(tmp_path)).glob("*.md"))
        assert len(files) == 1