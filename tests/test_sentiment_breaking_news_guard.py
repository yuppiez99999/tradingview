"""
重大负面新闻 Guard 单元测试 (P2-增强, v8.4 2026-07-30)
=====================================================
验证 RiskGuardIntegrator.guard_sentiment_breaking_news 在以下场景的行为:

    1. 重大负面新闻 (direction=negative, confidence>=0.9) → 暂停建仓
    2. 中等负面新闻 (direction=negative, confidence<0.9) → 不触发
    3. 正面新闻 (direction=positive) → 不触发
    4. iFinD MCP 不可用 → fail-open, 不阻塞
    5. Feature Flag 关闭 → 跳过
    6. 无持仓标的 → 跳过
    7. 已有 CRITICAL 状态 → 不降级 (保持 CRITICAL)
    8. 保留平仓订单, 仅拦截建仓订单

设计原则:
    - Mock IFinDNewsAnalyzer, 避免依赖 iFinD MCP 在线
    - 不依赖磁盘文件, 使用内存中的 plan/pnl_report
    - AAA 模式: Arrange → Act → Assert
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Mock 数据结构 — 与 utils.ifind_news_analyzer.StockInsight 一致
# ============================================================
@dataclass
class MockStockInsight:
    """模拟 StockInsight (与 utils.ifind_news_analyzer.StockInsight 字段一致)"""

    symbol: str
    name: str
    direction: str
    confidence: float
    reasons: list[str] = None
    news_count: int = 0
    updated_at: str = "2026-07-30T10:00:00"

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


# ============================================================
# 测试夹具 — 构造基础 plan 和 pnl_report
# ============================================================
def _make_base_plan():
    """构造基础次日交易计划, 包含建仓订单和平仓订单"""
    return {
        "phase": {"daily_capital": 150000, "day_capital": 150000},
        "execution_plan": {
            "morning_orders": [
                {
                    "code": "600519.SH",
                    "action": "BUY",
                    "shares": 100,
                    "est_amount": 50000,
                },
                {
                    "code": "000858.SZ",
                    "action": "SELL",
                    "shares": 50,
                    "est_amount": 25000,
                },
            ],
            "afternoon_orders": [
                {
                    "code": "002371.SZ",
                    "action": "BUY",
                    "shares": 200,
                    "est_amount": 100000,
                },
            ],
        },
        "market_state": {
            "build_allowed": True,
            "spot_build_allowed": True,
            "circuit_level": "NORMAL",
        },
        "risk_guard": {},
        "hedge_fund_overlays": {"v77_notes": {}},
    }


def _make_pnl_report_with_holdings():
    """构造带持仓的 pnl_report (使用 portfolio_pnl.details 格式)"""
    return {
        "portfolio_pnl": {
            "summary": {
                "total_cost": 1000000,
                "total_market_value": 1100000,
                "total_pnl": 100000,
            },
            "details": [
                {"code": "600519.SH", "name": "贵州茅台"},
                {"code": "000858.SZ", "name": "五粮液"},
            ],
        }
    }


# ============================================================
# 测试用例
# ============================================================


class TestSentimentBreakingNewsGuard:
    """重大负面新闻 Guard 测试套件"""

    def test_1_critical_negative_news_triggers_pause(self):
        """测试 1: confidence>=0.9 的负面新闻 → 必须触发暂停建仓

        Arrange: 1 只标的 direction=negative confidence=0.95
        Act: 调用 guard_sentiment_breaking_news
        Assert: build_allowed=False, spot_build_allowed=False, circuit_level=WARNING
        """
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        pnl_report = _make_pnl_report_with_holdings()

        mock_insights = [
            MockStockInsight(
                symbol="600519",
                name="贵州茅台",
                direction="negative",
                confidence=0.95,
                reasons=["业绩不及预期", "高管被调查"],
                news_count=5,
            ),
            MockStockInsight(
                symbol="000858",
                name="五粮液",
                direction="neutral",
                confidence=0.4,
                reasons=["无重大消息"],
                news_count=2,
            ),
        ]

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = True
        mock_analyzer.batch_analyze.return_value = mock_insights

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert (
            rg["status"] == "TRIGGERED"
        ), f"status 应为 TRIGGERED, 实际={rg['status']}"
        assert rg["triggered"] is True
        assert "600519" in rg["triggered_symbols"]
        assert len(rg["details"]) == 1
        assert rg["details"][0]["confidence"] >= 0.9

        ms = result_plan["market_state"]
        assert ms["build_allowed"] is False, "build_allowed 必须为 False"
        assert ms["spot_build_allowed"] is False, "spot_build_allowed 必须为 False"
        assert (
            ms["circuit_level"] == "WARNING"
        ), f"circuit_level 应升级为 WARNING, 实际={ms['circuit_level']}"

        # 验证: 建仓订单被拦截, 平仓订单保留
        morning = result_plan["execution_plan"]["morning_orders"]
        assert len(morning) == 1, "morning_orders 应仅剩 1 笔平仓订单"
        assert morning[0]["action"] == "SELL", "保留的应为 SELL 订单"

        afternoon = result_plan["execution_plan"]["afternoon_orders"]
        assert len(afternoon) == 0, "afternoon_orders 的建仓订单应被全部拦截"

    def test_2_moderate_negative_news_does_not_trigger(self):
        """测试 2: confidence<0.9 的负面新闻 → 不触发

        Arrange: 标的 direction=negative confidence=0.7
        Act: 调用 guard
        Assert: status=OK, build_allowed 保持 True
        """
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        pnl_report = _make_pnl_report_with_holdings()

        mock_insights = [
            MockStockInsight(
                symbol="600519",
                name="贵州茅台",
                direction="negative",
                confidence=0.7,  # 低于 0.9 阈值
                reasons=["季度下滑"],
                news_count=3,
            ),
        ]

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = True
        mock_analyzer.batch_analyze.return_value = mock_insights

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "OK", f"status 应为 OK, 实际={rg['status']}"
        assert rg["triggered"] is False
        assert result_plan["market_state"]["build_allowed"] is True
        assert result_plan["market_state"]["spot_build_allowed"] is True

    def test_3_positive_news_does_not_trigger(self):
        """测试 3: direction=positive 即使 confidence=0.95 也不触发"""
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        pnl_report = _make_pnl_report_with_holdings()

        mock_insights = [
            MockStockInsight(
                symbol="600519",
                name="贵州茅台",
                direction="positive",
                confidence=0.95,
                reasons=["业绩大增"],
                news_count=4,
            ),
        ]

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = True
        mock_analyzer.batch_analyze.return_value = mock_insights

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "OK"
        assert rg["triggered"] is False

    def test_4_ifind_unavailable_fails_open(self):
        """测试 4: iFinD MCP 不可用 → fail-open, 不阻塞交易"""
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        pnl_report = _make_pnl_report_with_holdings()

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = False  # iFinD 不可用

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "SKIP"
        assert rg["reason"] == "ifind_mcp_unavailable"
        # fail-open: 不修改 market_state
        assert result_plan["market_state"]["build_allowed"] is True
        assert result_plan["market_state"]["spot_build_allowed"] is True

    def test_5_feature_flag_disabled_skips_guard(self):
        """测试 5: USE_SENTIMENT_GUARD=False → 跳过"""
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        pnl_report = _make_pnl_report_with_holdings()

        # Act
        with patch.dict(os.environ, {"USE_SENTIMENT_GUARD": "False"}):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "DISABLED"
        assert rg["reason"] == "feature_flag_off"
        assert result_plan["market_state"]["build_allowed"] is True

    def test_6_no_holdings_skips_guard(self):
        """测试 6: 无持仓标的 → 跳过

        场景: pnl_report 无持仓数据, plan 也无建仓订单
        """
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        # 空 plan (无建仓订单, 无 positions)
        plan = {
            "phase": {"daily_capital": 150000},
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "market_state": {"build_allowed": True, "spot_build_allowed": True},
            "risk_guard": {},
        }
        # 空 pnl_report, 无持仓数据
        pnl_report = {"portfolio_pnl": {"summary": {}}}

        # Act
        result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "SKIP"
        assert rg["reason"] == "no_holdings"

    def test_7_existing_critical_not_downgraded(self):
        """测试 7: 已有 CRITICAL 状态时不降级为 WARNING

        场景: KillSwitch 已先触发 CRITICAL, 负面新闻 Guard 不应覆盖为 WARNING
        """
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        # 模拟 KillSwitch 已设置 CRITICAL
        plan["market_state"]["circuit_level"] = "CRITICAL"
        plan["market_state"]["build_allowed"] = False
        plan["market_state"]["spot_build_allowed"] = False
        pnl_report = _make_pnl_report_with_holdings()

        mock_insights = [
            MockStockInsight(
                symbol="600519",
                name="贵州茅台",
                direction="negative",
                confidence=0.95,
                reasons=["业绩造假"],
                news_count=8,
            ),
        ]

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = True
        mock_analyzer.batch_analyze.return_value = mock_insights

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert: circuit_level 保持 CRITICAL (不降级)
        ms = result_plan["market_state"]
        assert ms["circuit_level"] == "CRITICAL", "已有 CRITICAL 不应被降级为 WARNING"
        assert ms["build_allowed"] is False
        assert ms["spot_build_allowed"] is False
        # 但仍然记录触发详情
        rg = result_plan["risk_guard"]["sentiment_breaking_news"]
        assert rg["status"] == "TRIGGERED"
        assert "600519" in rg["triggered_symbols"]

    def test_8_keep_sell_orders_only(self):
        """测试 8: 触发时仅保留 SELL/REDUCE 订单, 拦截 BUY 订单"""
        # Arrange
        from utils.risk_guard_integrator import RiskGuardIntegrator

        rgi = RiskGuardIntegrator(report_date="2026-07-30")
        plan = _make_base_plan()
        # 添加更多混合订单
        plan["execution_plan"]["morning_orders"] = [
            {"code": "600519.SH", "action": "BUY", "shares": 100},
            {"code": "000858.SZ", "action": "SELL", "shares": 50},
            {"code": "002371.SZ", "action": "BUY", "shares": 200},
            {"code": "510300.SH", "action": "REDUCE", "shares": 30},
        ]
        pnl_report = _make_pnl_report_with_holdings()

        mock_insights = [
            MockStockInsight(
                symbol="600519",
                name="贵州茅台",
                direction="negative",
                confidence=0.92,
                reasons=["监管立案"],
                news_count=6,
            ),
        ]

        mock_analyzer = MagicMock()
        mock_analyzer.available.return_value = True
        mock_analyzer.batch_analyze.return_value = mock_insights

        # Act
        with patch(
            "utils.ifind_news_analyzer.IFinDNewsAnalyzer", return_value=mock_analyzer
        ):
            result_plan = rgi.guard_sentiment_breaking_news(pnl_report, plan)

        # Assert: morning_orders 应仅剩 SELL 和 REDUCE
        morning = result_plan["execution_plan"]["morning_orders"]
        actions = [o["action"] for o in morning]
        assert "BUY" not in actions, "BUY 订单应被拦截"
        assert "SELL" in actions, "SELL 订单应保留"
        assert "REDUCE" in actions, "REDUCE 订单应保留"
        assert len(morning) == 2, f"应保留 2 笔平仓订单, 实际 {len(morning)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
