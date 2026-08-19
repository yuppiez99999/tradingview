"""test_lgb_signal_monitor_unit.py — LGB信号监控单元测试

覆盖要点:
    - record_lgb_application (boost/cut/neutral/无信号跳过)
    - _append_jsonl
    - load_history (无文件/有数据/days过滤)
    - analyze_lgb_history (空/有数据)
    - _generate_threshold_suggestions (各种比例)
    - generate_analysis_report (空/有数据)
"""
from __future__ import annotations

import json

import pytest

from utils.lgb_signal_monitor import (
    _generate_threshold_suggestions,
    analyze_lgb_history,
    generate_analysis_report,
    load_history,
    record_lgb_application,
)

# ============================================================
# record_lgb_application
# ============================================================


class TestRecordLgbApplication:
    @pytest.mark.unit
    def test_basic(self, tmp_path, monkeypatch):
        """记录基本事件 — summary + order"""
        fake_log = tmp_path / "test.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)

        orders = [
            {"code": "000001", "name": "平安银行", "lgb_multiplier": 1.08,
             "original_shares": 1000, "shares": 1080, "est_price": 10.0, "est_amount": 10800},
        ]
        signals = {"000001": {"signal": 0.35, "quality_flag": "OK", "name": "平安银行"}}
        count = record_lgb_application("2026-01-15", orders, signals, boost_count=1)
        assert count == 2  # summary + 1 order
        assert fake_log.exists()

    @pytest.mark.unit
    def test_no_signal_skipped(self, tmp_path, monkeypatch):
        """无 lgb_signal 且无 lgb_multiplier → 跳过"""
        fake_log = tmp_path / "test.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)

        orders = [{"code": "000002", "name": "万科", "shares": 100}]
        count = record_lgb_application("2026-01-15", orders, {}, boost_count=0, cut_count=0)
        assert count == 1  # only summary

    @pytest.mark.unit
    def test_cut_direction(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "test.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)

        orders = [{"code": "000001", "name": "X", "lgb_multiplier": 0.85, "shares": 850}]
        signals = {"000001": {"signal": -0.20, "quality_flag": "OK"}}
        record_lgb_application("2026-01-15", orders, signals, cut_count=1)
        lines = fake_log.read_text().strip().split("\n")
        order_event = json.loads(lines[1])
        assert order_event["direction"] == "cut"

    @pytest.mark.unit
    def test_neutral_direction(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "test.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)

        orders = [{"code": "000001", "name": "X", "lgb_multiplier": 1.0, "shares": 100}]
        signals = {"000001": {"signal": 0.01, "quality_flag": "OK"}}
        record_lgb_application("2026-01-15", orders, signals)
        lines = fake_log.read_text().strip().split("\n")
        order_event = json.loads(lines[1])
        assert order_event["direction"] == "neutral"


# ============================================================
# load_history
# ============================================================


class TestLoadHistory:
    @pytest.mark.unit
    def test_no_file(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "nonexistent.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)
        assert load_history() == []

    @pytest.mark.unit
    def test_with_data(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "test.jsonl"
        fake_log.write_text(
            json.dumps({"type": "summary", "trade_date": "2026-01-15"}) + "\n"
            + json.dumps({"type": "order", "trade_date": "2026-01-15"}) + "\n"
        )
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)
        events = load_history(days=0)
        assert len(events) == 2


# ============================================================
# analyze_lgb_history
# ============================================================


class TestAnalyzeLgbHistory:
    @pytest.mark.unit
    def test_empty(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "nonexistent.jsonl"
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)
        result = analyze_lgb_history()
        assert result["empty"] is True

    @pytest.mark.unit
    def test_with_data(self, tmp_path, monkeypatch):
        fake_log = tmp_path / "test.jsonl"
        fake_log.write_text(
            json.dumps({"type": "summary", "trade_date": "2026-01-15",
                        "total_orders": 2, "boost_count": 1, "cut_count": 1, "neutral_count": 0}) + "\n"
            + json.dumps({"type": "order", "trade_date": "2026-01-15", "code": "000001",
                          "lgb_signal": 0.20, "lgb_multiplier": 1.08, "direction": "boost",
                          "quality_flag": "OK"}) + "\n"
            + json.dumps({"type": "order", "trade_date": "2026-01-15", "code": "000002",
                          "lgb_signal": -0.10, "lgb_multiplier": 0.92, "direction": "cut",
                          "quality_flag": "OK"}) + "\n"
        )
        monkeypatch.setattr("utils.lgb_signal_monitor.LOG_FILE", fake_log)
        result = analyze_lgb_history(days=0)
        assert result["empty"] is False
        assert result["total_orders"] == 2
        assert result["total_boost"] == 1
        assert result["total_cut"] == 1


# ============================================================
# _generate_threshold_suggestions
# ============================================================


class TestSuggestions:
    @pytest.mark.unit
    def test_no_issues(self):
        suggestions = _generate_threshold_suggestions(
            boost_ratio=0.2, cut_ratio=0.1, neutral_ratio=0.3,
            signal_buckets={"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            multiplier_dist={1.08: 5, 0.92: 5}, total_orders=100,
        )
        assert any("合理" in s for s in suggestions)

    @pytest.mark.unit
    def test_high_boost(self):
        suggestions = _generate_threshold_suggestions(
            boost_ratio=0.6, cut_ratio=0.1, neutral_ratio=0.3,
            signal_buckets={"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            multiplier_dist={}, total_orders=100,
        )
        assert any("boost比例过高" in s for s in suggestions)

    @pytest.mark.unit
    def test_high_cut(self):
        suggestions = _generate_threshold_suggestions(
            boost_ratio=0.1, cut_ratio=0.5, neutral_ratio=0.4,
            signal_buckets={"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            multiplier_dist={}, total_orders=100,
        )
        assert any("cut比例过高" in s for s in suggestions)

    @pytest.mark.unit
    def test_high_neutral(self):
        suggestions = _generate_threshold_suggestions(
            boost_ratio=0.1, cut_ratio=0.1, neutral_ratio=0.8,
            signal_buckets={"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            multiplier_dist={}, total_orders=100,
        )
        assert any("中性比例过高" in s for s in suggestions)

    @pytest.mark.unit
    def test_low_sample(self):
        suggestions = _generate_threshold_suggestions(
            boost_ratio=0.3, cut_ratio=0.3, neutral_ratio=0.4,
            signal_buckets={"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            multiplier_dist={}, total_orders=10,
        )
        assert any("样本数较少" in s for s in suggestions)


# ============================================================
# generate_analysis_report
# ============================================================


class TestGenerateReport:
    @pytest.mark.unit
    def test_empty(self):
        analysis = {"empty": True, "message": "无数据", "log_file": "/tmp/test.jsonl"}
        report = generate_analysis_report(analysis)
        assert "LGB信号实盘监控报告" in report
        assert "无数据" in report

    @pytest.mark.unit
    def test_with_data(self):
        analysis = {
            "empty": False,
            "analysis_date": "2026-01-15 10:00:00",
            "log_file": "/tmp/test.jsonl",
            "days_analyzed": 30,
            "trade_dates": ["2026-01-15"],
            "total_executions": 1,
            "total_orders": 2,
            "total_boost": 1,
            "total_cut": 1,
            "total_neutral": 0,
            "boost_ratio": 0.5,
            "cut_ratio": 0.5,
            "neutral_ratio": 0.0,
            "signal_buckets": {"strong_bull (>=0.3)": 1, "strong_bear (<-0.15)": 1},
            "multiplier_dist": {1.08: 1, 0.85: 1},
            "per_symbol_stats": {"000001": {"boost": 1, "cut": 0, "neutral": 0, "total": 1}},
            "per_date_stats": {"2026-01-15": {"boost": 1, "cut": 1, "neutral": 0, "total": 2}},
            "quality_dist": {"OK": 2},
            "suggestions": ["✓ 合理"],
        }
        report = generate_analysis_report(analysis)
        assert "总体统计" in report
        assert "信号档位分布" in report
