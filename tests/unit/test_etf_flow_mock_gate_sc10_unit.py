"""SC-10 回归: ETF 资金监测 mock 兜底的降级语义 (2026-09-12)

背景 (审查报告 §五 补扫「增强融合 + 复权因子」时发现的同源模式):
    utils/etf_fund_tracker 的 P6 模拟兜底 "永不崩溃" 设计在 Wind/akshare
    均不可用时静默接管, 但信号/建议/报告三件套**不含任何顶层降级标记**:
    - 模拟净流 = uniform(5000万, 5亿) × ±1, 24 只 ETF × 5 日累计期望
      足以触发 50 亿 "国家队强加仓信号" (实测 20 次 seed 试验 13 次越线)
    - 社保消费链 _build_etf_flow_data 丢弃 source 字段 →
      SocialSecurityETFTracker.analyze 无从知晓, 假信号进建议与报告

本套件锁死三件事:
    1. mock 降级时信号类型必须是 "信号不可用(模拟数据)", 不得是加仓/减仓
    2. 建议/报告必须显式标注模拟数据 (mock_degraded / 警示横幅)
    3. 社保分析收到 mock 数据时跳过信号检测, 只保留静态风格分析
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import etf_fund_tracker as eft  # noqa: E402


def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制全链走 P6 模拟兜底 (等价于 Wind/akshare 均不可用)."""
    monkeypatch.setattr(eft, "WIND_MCP_AVAILABLE", False)
    monkeypatch.setattr(eft, "AK_AVAILABLE", False)


class TestMockSourceDegradesSignals:
    def test_mock_kline_flags_flow_items(self, monkeypatch: pytest.MonkeyPatch):
        _force_mock(monkeypatch)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST[:3], days=5)
        assert flows, "应仍有模拟数据产出 (P6 兜底设计)"
        assert all(f["mock_degraded"] is True for f in flows)

    def test_mock_signals_not_trading_actions(self, monkeypatch: pytest.MonkeyPatch):
        """mock 数据下不得出现 加仓/减仓/关注 交易级信号."""
        _force_mock(monkeypatch)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST, days=5)
        signals = eft.detect_state_fund_signals(flows)
        assert signals, "应产出降级占位信号 (可审计), 不是空列表静默"
        for s in signals:
            assert "加仓" not in s["signal_type"], (
                f"模拟数据不得产生交易级信号: {s['signal_type']}"
            )
            assert "减仓" not in s["signal_type"]
            assert s["confidence"] == "数据降级"

    def test_mock_seed_noise_would_have_triggered_high_signal(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """先红实证的固化: 均匀随机净流 5 日累计天然越 50 亿线.

        修复前 (无降级语义), 该数据会产出 "强加仓信号"(高置信度)。
        """
        _force_mock(monkeypatch)
        random.seed(42)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST, days=5)
        # 修复后: 即便累计净流越大也只产生降级占位
        big = [f for f in flows if f["net_flow_yi"] >= 50]
        signals = {s["code"]: s for s in eft.detect_state_fund_signals(flows)}
        for f in big:
            s = signals.get(f["code"])
            assert s is None or "不可用" in s["signal_type"]

    def test_real_source_not_flagged(self, monkeypatch: pytest.MonkeyPatch):
        """真实数据源 (mock_degraded=False) 的信号链路不受影响."""
        monkeypatch.setattr(
            eft,
            "get_etf_kline",
            lambda code, market, days=5, source_mode="auto": {
                "code": code,
                "kline": [
                    {
                        "date": f"2026-09-{d:02d}",
                        "close": 1.0,
                        "change_pct": 1.0,
                        "volume": 1000,
                        "amount": 6e9,
                        "net_flow": 6e9,
                    }
                    for d in range(1, 6)
                ],
                "source": "Wind MCP",
            },
        )
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST[:2], days=5)
        assert all(f["mock_degraded"] is False for f in flows)
        signals = eft.detect_state_fund_signals(flows)
        assert any("加仓" in s["signal_type"] for s in signals)


class TestMockSourceDegradesSuggestionAndReport:
    def test_suggestion_marks_mock(self, monkeypatch: pytest.MonkeyPatch):
        _force_mock(monkeypatch)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST, days=5)
        sigs = eft.detect_state_fund_signals(flows)
        sug = eft.build_investment_suggestion(flows, sigs)
        assert sug["mock_degraded"] is True
        assert "模拟数据" in sug["overall_trend"]

    def test_report_has_mock_banner(self, monkeypatch: pytest.MonkeyPatch):
        _force_mock(monkeypatch)
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST, days=5)
        sigs = eft.detect_state_fund_signals(flows)
        report = eft.generate_report(flows, sigs, [], window_days=5)
        assert "数据降级警示" in report, "报告顶部必须有显式模拟数据警示"

    def test_real_report_no_false_banner(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            eft,
            "get_etf_kline",
            lambda code, market, days=5, source_mode="auto": {
                "code": code,
                "kline": eft.generate_mock_kline(code, days),
                "source": "Wind MCP",  # 假装真实源
            },
        )
        flows = eft.calculate_fund_flow_summary(eft.ETF_LIST[:2], days=5)
        report = eft.generate_report(flows, [], [], window_days=5)
        assert "数据降级警示" not in report


class TestTrackerFlowDataCarriesSource:
    def test_flow_data_carries_mock_flag(self, monkeypatch: pytest.MonkeyPatch):
        _force_mock(monkeypatch)
        tracker = eft.ETFFundFlowTracker(days=5, source="auto")
        tracker.analyze_fund_flow()
        assert tracker.flow_data
        assert all("mock_degraded" in v for v in tracker.flow_data.values())
        assert all(v["mock_degraded"] is True for v in tracker.flow_data.values())

    def test_social_security_skips_signals_on_mock(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """社保消费链: mock 数据 → analyze 跳过信号检测并标注降级."""
        _force_mock(monkeypatch)
        tracker = eft.ETFFundFlowTracker(days=5, source="auto")
        tracker.analyze_fund_flow()
        try:
            from utils.social_security_etf import SocialSecurityETFTracker
        except (ImportError, ModuleNotFoundError):
            pytest.skip("social_security_etf 不可用")
        analysis = SocialSecurityETFTracker().analyze(tracker.flow_data)
        assert analysis.get("data_degraded") is True
        assert analysis.get("degraded_reason")
        assert analysis["signals"] == [], "模拟数据不得产生国家队信号"
        # 静态风格分析仍在 (非降级路径的价值保留)
        assert analysis["style_summary"]

    def test_social_security_real_data_still_detects(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        try:
            from utils.social_security_etf import SocialSecurityETFTracker
        except (ImportError, ModuleNotFoundError):
            pytest.skip("social_security_etf 不可用")
        real_flow = {
            "510300": {
                "name": "沪深300ETF",
                "net_flow_yi": 60.0,
                "trend": "连续流入",
                "category": "宽基核心",
                "mock_degraded": False,
            }
        }
        analysis = SocialSecurityETFTracker().analyze(real_flow)
        assert analysis.get("data_degraded") is None
        assert analysis["signals"], "真实数据 60 亿净流入应产生信号"


class TestUIModuleLoaderCompat:
    """SC-11: module_loader 不再 exec 不存在的 v5.10 脚本."""

    def test_loader_does_not_exec_missing_entry(self):
        src = (PROJECT_ROOT / "ui" / "components" / "module_loader.py").read_text(
            encoding="utf-8"
        )
        assert "_MODULE_PATH" not in src and "exec_module" not in src, (
            "module_loader 不得再 exec 本地入口脚本 (v5.10.py 从未入库, "
            "新克隆环境 FileNotFoundError)"
        )

    def test_compat_layer_exposes_declared_attrs(self):
        pytest.importorskip("streamlit")
        mod = __import__(
            "ui.components.module_loader",
            fromlist=["_SystemModuleCompat"],
        )
        compat = mod._SystemModuleCompat()
        assert hasattr(compat, "__getattr__")
        with pytest.raises(AttributeError, match="not_provided_attr"):
            compat.not_provided_attr  # noqa: B018
