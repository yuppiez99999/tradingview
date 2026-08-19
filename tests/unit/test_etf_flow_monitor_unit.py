"""etf_flow_monitor 单元测试 — ETF资金流向监控."""

from __future__ import annotations

from utils.etf_flow_monitor import (
    ETF_TO_STOCKS,
    NATIONAL_TEAM_ETFS,
    SIGNAL_THRESHOLDS,
    ETFRealTimeTracker,
)


class TestConstants:
    def test_signal_thresholds(self):
        assert SIGNAL_THRESHOLDS["high"] == 50
        assert SIGNAL_THRESHOLDS["medium"] == 10
        assert SIGNAL_THRESHOLDS["low"] == 2

    def test_national_team_etfs(self):
        codes = [e["code"] for e in NATIONAL_TEAM_ETFS]
        assert "510300" in codes
        assert "510050" in codes
        assert len(codes) >= 10

    def test_etf_to_stocks(self):
        assert "510300" in ETF_TO_STOCKS
        assert "个股票池" in ETF_TO_STOCKS["510300"]


class TestDetectSignals:
    def test_no_signals(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 0, "name": "沪深300", "category": "宽基", "change_pct": 0, "trend": "中性", "source": "none"},
        }
        signals = tracker.detect_signals(flow_data)
        assert signals == []

    def test_high_inflow(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 60, "name": "沪深300", "category": "宽基", "change_pct": 1.5, "trend": "流入", "source": "wind"},
        }
        signals = tracker.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "高"
        assert "强加仓" in signals[0]["signal_type"]

    def test_medium_inflow(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 15, "name": "沪深300", "category": "宽基", "change_pct": 0.5, "trend": "流入", "source": "wind"},
        }
        signals = tracker.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "中"

    def test_low_inflow(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 3, "name": "沪深300", "category": "宽基", "change_pct": 0.1, "trend": "流入", "source": "wind"},
        }
        signals = tracker.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "低"

    def test_high_outflow(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": -60, "name": "沪深300", "category": "宽基", "change_pct": -1.5, "trend": "流出", "source": "wind"},
        }
        signals = tracker.detect_signals(flow_data)
        assert len(signals) == 1
        assert signals[0]["confidence"] == "高"
        assert "减仓" in signals[0]["signal_type"]

    def test_multiple_sorted_by_confidence(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 60, "name": "A", "category": "C", "change_pct": 1, "trend": "流入", "source": "s"},
            "510050": {"net_flow_yi": 5, "name": "B", "category": "C", "change_pct": 0.2, "trend": "流入", "source": "s"},
        }
        signals = tracker.detect_signals(flow_data)
        assert len(signals) == 2
        assert signals[0]["confidence"] == "高"
        assert signals[1]["confidence"] == "低"


class TestGetSignalSummary:
    def test_empty(self):
        tracker = ETFRealTimeTracker()
        summary = tracker.get_signal_summary({})
        assert summary["total_flow_yi"] == 0
        assert summary["overall_trend"] == "平衡"
        assert summary["signal_count"] == 0

    def test_with_data(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": 60, "name": "沪深300", "category": "宽基", "change_pct": 1.5, "trend": "流入", "source": "wind"},
            "510050": {"net_flow_yi": -30, "name": "上证50", "category": "宽基", "change_pct": -0.8, "trend": "流出", "source": "wind"},
        }
        summary = tracker.get_signal_summary(flow_data)
        assert summary["total_flow_yi"] == 30
        assert summary["overall_trend"] == "净流入"
        assert summary["signal_count"] == 2
        assert summary["strong_signals"] == 1

    def test_net_outflow(self):
        tracker = ETFRealTimeTracker()
        flow_data = {
            "510300": {"net_flow_yi": -60, "name": "A", "category": "C", "change_pct": -1, "trend": "流出", "source": "s"},
        }
        summary = tracker.get_signal_summary(flow_data)
        assert summary["overall_trend"] == "净流出"


class TestToWindCode:
    def test_sh_etf(self):
        tracker = ETFRealTimeTracker()
        assert tracker._to_wind_code("510300") == "510300.SH"
        assert tracker._to_wind_code("510050") == "510050.SH"
        assert tracker._to_wind_code("588000") == "588000.SH"

    def test_sz_etf(self):
        tracker = ETFRealTimeTracker()
        assert tracker._to_wind_code("159915") == "159915.SZ"
