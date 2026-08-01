# -*- coding: utf-8 -*-
"""test_finance_agents_shadow_mode_integration.py — Shadow Mode 集成测试

测试范围:
  - 完整 Shadow Mode 流程: orchestrate → shadow_compare → save_audit_log
  - 多 Agent 协作 (5 个 Agent 同时调用)
  - 与 SignalFusionEngine 的对比 (用 Mock)
  - 审计日志多交易日累积

设计原则:
  - 跨模块集成 (orchestrator + agents + audit log)
  - Mock LLM 调用 (SentimentAgent 默认 use_llm=False)
  - <5s 完成
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.finance_agent_orchestrator import (
    AgentConsensus,
    FinanceAgentOrchestrator,
)


@pytest.fixture
def orchestrator(tmp_path):
    """用 tmp_path 隔离审计日志目录"""
    return FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")


@pytest.fixture
def multi_symbol_context():
    """多标的 mock 上下文 (3 个标的)"""
    return {
        "600276.SH": {
            "kline": [
                {"close": 30 + i * 0.3, "volume": 1e7, "amount": 3e8}
                for i in range(30)
            ],
            "fundamentals": {
                "pe": 35, "pb": 6, "roe": 0.12,
                "pe_percentile": 0.50, "pb_percentile": 0.50,
            },
            "news_items": [
                {"title": "恒瑞医药创新药获批", "content": "利好", "symbol": "600276.SH"},
            ],
            "macro_data": {
                "bond_10y_yield": 0.028, "north_flow": 3e9,
                "industry_score": 0.65, "index_return_20d": 0.02,
            },
            "position_weight": 0.08,
            "beta": 1.0,
        },
        "000001.SZ": {
            "kline": [
                {"close": 15 - i * 0.1, "volume": 5e7, "amount": 7e8}
                for i in range(30)
            ],
            "fundamentals": {
                "pe": 8, "pb": 0.7, "roe": 0.11,
                "pe_percentile": 0.10, "pb_percentile": 0.05,
            },
            "news_items": [
                {"title": "平安银行业绩稳定", "content": "净利润增长", "symbol": "000001.SZ"},
            ],
            "macro_data": {
                "bond_10y_yield": 0.028, "north_flow": 3e9,
                "industry_score": 0.50, "index_return_20d": 0.02,
            },
            "position_weight": 0.12,
            "beta": 1.2,
        },
        "510300.SH": {
            "kline": [
                {"close": 4.5 + 0.01 * math.sin(i / 3), "volume": 1e8, "amount": 4e8}
                for i in range(30)
            ],
            "fundamentals": {
                "pe": 12, "pb": 1.3, "roe": 0.10,
                "pe_percentile": 0.30, "pb_percentile": 0.20,
            },
            "news_items": [],
            "macro_data": {
                "bond_10y_yield": 0.028, "north_flow": 3e9,
                "industry_score": 0.60, "index_return_20d": 0.02,
            },
            "position_weight": 0.05,
            "beta": 1.0,
        },
    }


# 在文件底部导入 math (避免循环)
import math  # noqa: E402

# ============================================================
# Shadow Mode 完整流程集成测试
# ============================================================


class TestShadowModeFullFlow:
    """Shadow Mode 完整流程: orchestrate → compare → audit"""

    @pytest.mark.integration
    def test_shadow_mode_full_flow_single_symbol(self, orchestrator):
        """单标的完整 Shadow Mode 流程"""
        ctx = {
            "kline": [
                {"close": 10 + i * 0.2, "volume": 1e7, "amount": 1e8}
                for i in range(30)
            ],
            "fundamentals": {
                "pe": 15, "pb": 2, "roe": 0.20,
                "pe_percentile": 0.10, "pb_percentile": 0.15,
            },
            "news_items": [{"title": "业绩利好", "content": "增长", "symbol": "X"}],
            "macro_data": {
                "bond_10y_yield": 0.024, "north_flow": 5e9,
                "industry_score": 0.70, "index_return_20d": 0.04,
            },
            "position_weight": 0.05,
            "beta": 1.0,
        }

        # 1. orchestrate
        consensus = orchestrator.orchestrate("TEST.SH", ctx)
        assert consensus.action in ("buy", "hold", "sell", "veto")
        assert len(consensus.agent_decisions) >= 3

        # 2. shadow_compare (用 mock fusion)
        mock_fusion = MagicMock()
        mock_fusion.strength = 0.3
        diff = orchestrator.shadow_compare(mock_fusion, consensus)
        assert diff.fusion_strength == 0.3
        assert diff.agent_strength == consensus.strength

        # 3. save_audit_log
        log_path = orchestrator.save_audit_log(consensus, diff, trade_date="20260726")
        assert log_path is not None
        assert log_path.exists()

        # 4. load_audit_log round-trip
        loaded = orchestrator.load_audit_log("20260726")
        assert len(loaded) == 1
        assert loaded[0]["consensus"]["symbol"] == "TEST.SH"

    @pytest.mark.integration
    def test_shadow_mode_multi_symbol(self, orchestrator, multi_symbol_context):
        """多标的 Shadow Mode (3 个标的同时分析)"""
        results = {}
        for symbol, ctx in multi_symbol_context.items():
            consensus = orchestrator.orchestrate(symbol, ctx)
            results[symbol] = consensus

        # 所有标的都应有决策
        assert len(results) == 3
        for symbol, consensus in results.items():
            assert consensus.symbol == symbol
            assert consensus.action in ("buy", "hold", "sell", "veto")

        # 000001.SZ 应该看多 (PE 分位 10% + PB 分位 5%)
        assert results["000001.SZ"].strength > 0

    @pytest.mark.integration
    def test_shadow_mode_veto_propagates_to_audit(self, orchestrator, tmp_path):
        """veto 决策正确写入审计日志"""
        # 构造暴跌场景 (回撤 > 25%)
        kline = []
        for i in range(20):
            kline.append({"close": 10 + i * 0.2, "volume": 1e7, "amount": 1e8})
        for i in range(10):
            kline.append({"close": 14 - i * 0.4, "volume": 1e7, "amount": 1e8})

        consensus = orchestrator.orchestrate("VETO.SH", {"kline": kline})
        assert consensus.action == "veto"
        assert consensus.veto is True

        log_path = orchestrator.save_audit_log(consensus, None, trade_date="20260726")
        assert log_path is not None

        loaded = orchestrator.load_audit_log("20260726")
        assert loaded[-1]["consensus"]["action"] == "veto"
        assert loaded[-1]["consensus"]["veto"] is True


# ============================================================
# 多 Agent 协作测试
# ============================================================


class TestMultiAgentCollaboration:
    """5 个 Agent 协作测试"""

    @pytest.mark.integration
    def test_all_5_agents_called(self, orchestrator):
        """所有 5 个 Agent 都被调用 (即使数据缺失)"""
        ctx = {
            "kline": [{"close": 10, "volume": 1e7, "amount": 1e8} for _ in range(30)],
            "fundamentals": {"pe": 15, "pb": 2, "pe_percentile": 0.50},
            "news_items": [{"title": "test", "content": "x"}],
            "macro_data": {"bond_10y_yield": 0.03},
            "position_weight": 0.05,
            "beta": 1.0,
        }
        consensus = orchestrator.orchestrate("ALL.SH", ctx)
        agent_names = [d["agent_name"] for d in consensus.agent_decisions]
        assert "value" in agent_names
        assert "momentum" in agent_names
        assert "sentiment" in agent_names
        assert "risk" in agent_names
        assert "macro" in agent_names

    @pytest.mark.integration
    def test_unavailable_agent_skipped(self, orchestrator):
        """不可用的 Agent 被跳过 (但仍记录为 hold)"""
        # 只提供 kline, 不提供 fundamentals/news/macro
        ctx = {
            "kline": [
                {"close": 10 + i * 0.1, "volume": 1e7, "amount": 1e8}
                for i in range(30)
            ],
        }
        consensus = orchestrator.orchestrate("X", ctx)
        # momentum 和 risk 应该可用 (kline 足够)
        # value/sentiment/macro 因数据缺失跳过
        assert len(consensus.agent_decisions) >= 1
        # 至少 momentum 或 risk 有决策
        agent_names = [d["agent_name"] for d in consensus.agent_decisions]
        assert "momentum" in agent_names or "risk" in agent_names


# ============================================================
# 审计日志多交易日累积
# ============================================================


class TestAuditLogAccumulation:
    """审计日志多交易日累积"""

    @pytest.mark.integration
    def test_multi_day_audit_log(self, orchestrator):
        """多交易日审计日志分别存储"""
        for trade_date in ["20260726", "20260727", "20260728"]:
            consensus = AgentConsensus(
                symbol="X", action="hold", strength=0.1,
            )
            orchestrator.save_audit_log(consensus, None, trade_date=trade_date)

        # 各自独立
        assert len(orchestrator.load_audit_log("20260726")) == 1
        assert len(orchestrator.load_audit_log("20260727")) == 1
        assert len(orchestrator.load_audit_log("20260728")) == 1

    @pytest.mark.integration
    def test_same_day_multiple_entries(self, orchestrator):
        """同日多条审计日志累积 (多标的)"""
        for symbol in ["A", "B", "C"]:
            consensus = AgentConsensus(
                symbol=symbol, action="hold", strength=0.0,
            )
            orchestrator.save_audit_log(consensus, None, trade_date="20260726")

        loaded = orchestrator.load_audit_log("20260726")
        assert len(loaded) == 3
        symbols = [entry["consensus"]["symbol"] for entry in loaded]
        assert set(symbols) == {"A", "B", "C"}
