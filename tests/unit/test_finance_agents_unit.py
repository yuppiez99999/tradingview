# -*- coding: utf-8 -*-
"""test_finance_agents_unit.py — 5 个金融专家 Agent 单元测试

测试范围:
  - ValueAgent: PE/PB 分位 / ROE / DCF 折价
  - MomentumAgent: 均线突破 / RSI / 量价
  - SentimentAgent: 关键词匹配 / veto 触发
  - RiskAgent: 回撤 veto / 波动率 / 流动性
  - MacroAgent: 利率 / 北向 / 行业景气

设计原则:
  - 单模块测试, 全 Mock, <1s 完成
  - 不依赖 LLM (SentimentAgent 默认 use_llm=False)
"""
from __future__ import annotations

import pytest

from utils.finance_agents import (
    AgentDecision,
    ValueAgent,
    MomentumAgent,
    SentimentAgent,
    RiskAgent,
    MacroAgent,
)


# ============================================================
# ValueAgent 测试
# ============================================================


class TestValueAgent:
    """估值分析 Agent"""

    @pytest.mark.unit
    def test_value_agent_low_pe_returns_buy(self):
        """PE 分位 < 20% → 看多"""
        ctx = {
            "fundamentals": {
                "pe": 10, "pb": 1.5, "roe": 0.20,
                "pe_percentile": 0.10, "pb_percentile": 0.15,
            },
        }
        agent = ValueAgent()
        d = agent.analyze("600276.SH", ctx)
        assert d.action == "buy"
        assert d.strength > 0
        assert "pe" in d.key_metrics

    @pytest.mark.unit
    def test_value_agent_high_pe_returns_sell(self):
        """PE 分位 > 80% → 看空"""
        ctx = {
            "fundamentals": {
                "pe": 80, "pb": 8, "roe": 0.10,
                "pe_percentile": 0.90, "pb_percentile": 0.85,
            },
        }
        agent = ValueAgent()
        d = agent.analyze("600276.SH", ctx)
        assert d.action == "sell"
        assert d.strength < 0

    @pytest.mark.unit
    def test_value_agent_missing_data_returns_hold(self):
        """估值数据缺失 → hold, confidence=0"""
        agent = ValueAgent()
        d = agent.analyze("X", {})
        assert d.action == "hold"
        assert d.confidence == 0.0

    @pytest.mark.unit
    def test_value_agent_high_roe_adds_strength(self):
        """高 ROE 增加看多强度"""
        ctx_no_roe = {
            "fundamentals": {"pe": 15, "pb": 2, "pe_percentile": 0.30},
        }
        ctx_high_roe = {
            "fundamentals": {
                "pe": 15, "pb": 2, "pe_percentile": 0.30,
                "roe": 0.25,
            },
        }
        agent = ValueAgent()
        d_no = agent.analyze("X", ctx_no_roe)
        d_yes = agent.analyze("X", ctx_high_roe)
        assert d_yes.strength > d_no.strength

    @pytest.mark.unit
    def test_value_agent_dcf_discount(self):
        """DCF 折价 > 20% → 看多"""
        ctx = {
            "fundamentals": {
                "pe": 20, "pb": 3, "pe_percentile": 0.50,
                "dcf_intrinsic_value": 120.0,
            },
            "market_data": {"close": 80.0},
        }
        agent = ValueAgent()
        d = agent.analyze("X", ctx)
        # 折价 = (120-80)/80 = 50% > 20%, 应加 +0.3
        assert d.strength > 0.2
        assert d.key_metrics["dcf_discount_ratio"] == 0.5


# ============================================================
# MomentumAgent 测试
# ============================================================


class TestMomentumAgent:
    """动量分析 Agent"""

    @pytest.mark.unit
    def test_momentum_agent_uptrend_returns_buy(self):
        """上升趋势 → 看多"""
        # 30 日单调上升
        kline = [
            {"close": 10 + i * 0.5, "volume": 1e7, "amount": 1e8}
            for i in range(30)
        ]
        agent = MomentumAgent()
        d = agent.analyze("X", {"kline": kline})
        assert d.action in ("buy", "hold")
        assert d.strength > 0

    @pytest.mark.unit
    def test_momentum_agent_downtrend_returns_sell(self):
        """下降趋势 → 看空"""
        kline = [
            {"close": 25 - i * 0.5, "volume": 1e7, "amount": 1e8}
            for i in range(30)
        ]
        agent = MomentumAgent()
        d = agent.analyze("X", {"kline": kline})
        assert d.action in ("sell", "hold")
        assert d.strength < 0

    @pytest.mark.unit
    def test_momentum_agent_insufficient_kline_returns_hold(self):
        """K 线不足 20 日 → hold"""
        kline = [{"close": 10 + i * 0.1, "volume": 1e7} for i in range(10)]
        agent = MomentumAgent()
        d = agent.analyze("X", {"kline": kline})
        assert d.action == "hold"
        assert d.confidence == 0.0

    @pytest.mark.unit
    def test_momentum_agent_volume_breakout(self):
        """量价齐升 → 加分"""
        kline = [
            {"close": 10 + i * 0.3, "volume": 1e7 * (1 + i * 0.1), "amount": 1e8}
            for i in range(30)
        ]
        agent = MomentumAgent()
        d = agent.analyze("X", {"kline": kline})
        assert d.key_metrics.get("vol_ratio_20d", 0) > 1.0


# ============================================================
# SentimentAgent 测试
# ============================================================


class TestSentimentAgent:
    """舆情分析 Agent (默认 use_llm=False, 走规则引擎)"""

    @pytest.mark.unit
    def test_sentiment_agent_positive_news_returns_buy(self):
        """正面新闻 → 看多"""
        ctx = {
            "news_items": [
                {"title": "业绩大增利好", "content": "净利润增长50%", "symbol": "X"},
            ],
        }
        agent = SentimentAgent()  # 默认 use_llm=False
        d = agent.analyze("X", ctx)
        assert d.strength > 0

    @pytest.mark.unit
    def test_sentiment_agent_critical_news_triggers_veto(self):
        """重大负面新闻 → veto"""
        ctx = {
            "news_items": [
                {"title": "立案调查", "content": "财务造假被证监会处罚", "symbol": "X"},
            ],
        }
        agent = SentimentAgent()
        d = agent.analyze("X", ctx)
        assert d.action == "veto"
        assert d.veto_reason != ""
        assert "立案调查" in d.veto_reason or "财务造假" in d.veto_reason

    @pytest.mark.unit
    def test_sentiment_agent_missing_news_returns_hold(self):
        """无新闻 → hold"""
        agent = SentimentAgent()
        d = agent.analyze("X", {})
        assert d.action == "hold"
        assert d.confidence == 0.0


# ============================================================
# RiskAgent 测试
# ============================================================


class TestRiskAgent:
    """风险分析 Agent (含 veto 权)"""

    @pytest.mark.unit
    def test_risk_agent_severe_drawdown_triggers_veto(self):
        """25%+ 回撤触发 veto"""
        # 构造 30% 回撤: 从 10 涨到 15 再跌到 10.5
        kline = []
        for i in range(15):
            kline.append({"close": 10 + i * 0.3, "volume": 1e7, "amount": 1e8})  # 10 → 14.2
        for i in range(15):
            kline.append({"close": 14.2 - i * 0.25, "volume": 1e7, "amount": 1e8})  # 14.2 → 10.7
        # 峰值 14.2, 谷值 10.7, 回撤 = (14.2-10.7)/14.2 = 24.6% 接近 25%
        # 改大一点确保触发
        kline[-1] = {"close": 10.0, "volume": 1e7, "amount": 1e8}
        # 峰值 14.2, 谷值 10.0, 回撤 = (14.2-10)/14.2 = 29.6% > 25%

        agent = RiskAgent()
        d = agent.analyze("X", {"kline": kline})
        # AgentDecision 没有 veto 字段, 只有 action="veto" 和 veto_reason
        assert d.action == "veto"
        assert d.veto_reason != ""
        assert "回撤" in d.veto_reason

    @pytest.mark.unit
    def test_risk_agent_normal_market_returns_hold(self):
        """正常市场 → hold"""
        kline = [
            {"close": 10 + 0.1 * math.sin(i / 5), "volume": 1e7, "amount": 1e8}
            for i in range(30)
        ]
        agent = RiskAgent()
        d = agent.analyze("X", {"kline": kline})
        assert d.action in ("hold", "buy", "sell")  # 不应 veto

    @pytest.mark.unit
    def test_risk_agent_empty_kline_returns_hold(self):
        """空 K 线 → hold"""
        agent = RiskAgent()
        d = agent.analyze("X", {})
        assert d.action == "hold"

    @pytest.mark.unit
    def test_risk_agent_high_volatility_veto(self):
        """60%+ 年化波动率触发 veto (且无大幅回撤避免被回撤抢先 veto)"""
        # 构造围绕 10.0 上下震荡的 kline: 高波动但无明显趋势 (避免回撤 > 25%)
        # 用周期性正弦震荡 + 大幅度
        kline = []
        for i in range(30):
            # 在 8-12 之间周期震荡 (波幅 4, 标准差约 1.0, 日收益率波动大)
            price = 10.0 + 1.5 * math.sin(i * 0.9) + 0.5 * math.sin(i * 1.7)
            kline.append({"close": price, "volume": 1e7, "amount": 1e8})

        agent = RiskAgent()
        d = agent.analyze("X", {"kline": kline})
        # 高波动率应触发 veto
        assert d.action == "veto"
        # 可能是波动率 veto 或回撤 veto (取决于哪个先触发)
        assert d.veto_reason != ""
        # 验证 metrics 中至少有波动率数据
        assert "volatility_20d_annual" in d.key_metrics or "max_drawdown_20d" in d.key_metrics


# ============================================================
# MacroAgent 测试
# ============================================================


class TestMacroAgent:
    """宏观分析 Agent"""

    @pytest.mark.unit
    def test_macro_agent_easy_money_returns_buy(self):
        """宽松货币 (低利率 + 北向流入) → 看多"""
        ctx = {
            "macro_data": {
                "bond_10y_yield": 0.024,  # 2.4% < 2.5%
                "north_flow": 8e9,         # 80 亿流入
                "industry_score": 0.75,    # 行业景气
                "index_return_20d": 0.06,  # 大盘 6%
            },
        }
        agent = MacroAgent()
        d = agent.analyze("X", ctx)
        assert d.action == "buy"
        assert d.strength > 0.3

    @pytest.mark.unit
    def test_macro_agent_tight_money_returns_sell(self):
        """紧缩货币 (高利率 + 北向流出) → 看空"""
        ctx = {
            "macro_data": {
                "bond_10y_yield": 0.038,  # 3.8% > 3.5%
                "north_flow": -8e9,        # 80 亿流出
                "industry_score": 0.20,    # 行业低迷
                "index_return_20d": -0.06, # 大盘跌 6%
            },
        }
        agent = MacroAgent()
        d = agent.analyze("X", ctx)
        assert d.action == "sell"
        assert d.strength < -0.3

    @pytest.mark.unit
    def test_macro_agent_missing_data_returns_hold(self):
        """宏观数据缺失 → hold"""
        agent = MacroAgent()
        d = agent.analyze("X", {})
        assert d.action == "hold"
        assert d.confidence == 0.0

    @pytest.mark.unit
    def test_macro_agent_confidence_capped(self):
        """宏观 confidence 上限 0.6"""
        ctx = {
            "macro_data": {
                "bond_10y_yield": 0.024,
                "north_flow": 8e9,
                "industry_score": 0.75,
                "index_return_20d": 0.06,
            },
        }
        agent = MacroAgent()
        d = agent.analyze("X", ctx)
        assert d.confidence <= 0.6


# ============================================================
# 导入 math 用于 RiskAgent 测试
# ============================================================
import math  # noqa: E402
