"""测试 utils/etf_fund_tracker (ETF 国家队资金监测模块, 集成版)

覆盖: 信号阈值 / 建议聚合 / 资金流汇总(monkeypatch K线) / 报告章节 / 归档。
数据源全部使用 mock 或注入, 不依赖网络。
运行: python -m pytest tests/test_etf_fund_tracker.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import etf_fund_tracker as eft  # noqa: E402


def _mk_flow(net_flow_yi, trend="中性", category="宽基核心", code="510300"):
    return {
        "code": code,
        "name": f"ETF{code}",
        "category": category,
        "price": 3.0,
        "change_pct": 0.1,
        "amount_yi": 5.0,
        "net_flow_yi": net_flow_yi,
        "avg_net_flow_yi": net_flow_yi / 5,
        "positive_days": "3/5",
        "trend": trend,
        "daily_flows": [{"date": f"2026-09-0{d}", "flow_yi": 1.0} for d in range(1, 6)],
    }


class TestSignalThresholds:
    def test_high_buy_signal(self):
        sig = eft.detect_state_fund_signals([_mk_flow(60.0, trend="连续流入")])
        assert len(sig) == 1
        assert sig[0]["confidence"] == "高"
        assert sig[0]["signal_type"] == "强加仓信号"

    def test_high_buy_no_trend(self):
        sig = eft.detect_state_fund_signals([_mk_flow(60.0, trend="中性")])
        assert sig[0]["signal_type"] == "大额加仓信号"
        assert sig[0]["confidence"] == "高"

    def test_medium_sell(self):
        sig = eft.detect_state_fund_signals([_mk_flow(-12.0)])
        assert sig[0]["confidence"] == "中"
        assert "减仓" in sig[0]["signal_type"]

    def test_low_watch(self):
        sig = eft.detect_state_fund_signals([_mk_flow(2.5)])
        assert sig[0]["confidence"] == "低"
        assert sig[0]["signal_type"] == "关注信号"

    def test_below_threshold_no_signal(self):
        assert eft.detect_state_fund_signals([_mk_flow(1.0)]) == []


class TestSuggestion:
    def test_overall_and_rotation(self):
        flows = [_mk_flow(40.0, category="宽基核心"), _mk_flow(-60.0, category="避险资产", code="518880")]
        sug = eft.build_investment_suggestion(flows, [])
        assert sug["overall_trend"] == "净流出"  # -20亿
        assert sug["style_rotation"]["宽基核心"] == "增持"
        assert sug["style_rotation"]["避险资产"] == "减持"


class TestFlowSummary:
    def test_summary_with_mock_kline(self, monkeypatch):
        def fake_kline(code, market, days=5, source_mode="auto"):
            return {"code": code, "kline": eft.generate_mock_kline(code, days), "source": "模拟数据"}

        monkeypatch.setattr(eft, "get_etf_kline", fake_kline)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST[:3], days=5, source_mode="mock")
        assert len(flows) == 3
        for f in flows:
            assert set(["code", "name", "category", "price", "net_flow_yi", "trend", "daily_flows"]).issubset(f.keys())
            assert len(f["daily_flows"]) == 5

    def test_report_sections(self):
        flows = [_mk_flow(20.0, trend="以流入为主")]
        sig = eft.detect_state_fund_signals(flows)
        report = eft.generate_report(flows, sig, [], window_days=5)
        for section in ["今日行情速览", "国家队信号检测", "资金流向TOP10", "风险提示", "全量监测标的明细"]:
            assert section in report


def test_archive_writes(tmp_path):
    path = eft.archive_to_daily_reports("# 测试报告\n", archive_root=str(tmp_path))
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        assert "测试报告" in f.read()


class TestTrackerClass:
    def test_tracker_end_to_end_mock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eft, "REPORT_DIR", str(tmp_path))
        tracker = eft.ETFFundFlowTracker(days=5, source="mock", top_n=10, archive_dir=str(tmp_path))
        tracker.analyze_fund_flow()
        assert len(tracker.flow_results) == len(eft.ETF_LIST)
        tracker.detect_signals()
        tracker.get_investment_suggestion()
        report_file, report = tracker.run(archive=True)
        assert os.path.isfile(report_file)
        assert os.path.isfile(os.path.join(str(tmp_path), "latest.md"))
        assert "ETF 国家队资金监测报告" in report

    def test_flow_data_shape_feeds_social_security(self, monkeypatch):
        """真实 flow_data 格式可被 SocialSecurityETFTracker.analyze 消费"""
        monkeypatch.setattr(eft, "get_etf_kline",
                            lambda code, market, days=5, source_mode="auto": {
                                "code": code,
                                "kline": eft.generate_mock_kline(code, days),
                                "source": "模拟数据",
                            })
        tracker = eft.ETFFundFlowTracker(days=5, source="mock")
        tracker.analyze_fund_flow()
        try:
            from utils.social_security_etf import SocialSecurityETFTracker

            ss = SocialSecurityETFTracker()
            analysis = ss.analyze(tracker.flow_data)
            assert "signals" in analysis and "recommendations" in analysis
        except (ImportError, ModuleNotFoundError):
            pytest.skip("social_security_etf 不可用, 跳过模型消费测试")
