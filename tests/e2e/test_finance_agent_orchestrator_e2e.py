# -*- coding: utf-8 -*-
"""test_finance_agent_orchestrator_e2e.py — E2E 测试

测试范围:
  - 真实 audit_log 目录 (data/agent_orchestrator_audit/)
  - 与 SignalFusionEngine 的真实集成 (而非 MagicMock)
  - 多标的批量 Shadow Mode 对比

设计原则:
  - 使用真实文件 IO (但写到 tmp_path 避免污染生产)
  - 使用真实 SignalFusionEngine (而非 Mock)
  - 验证端到端数据流
"""
from __future__ import annotations


import pytest

from utils.finance_agent_orchestrator import FinanceAgentOrchestrator
from utils.signal_fusion import SignalFusionEngine, FusionSignal


# ============================================================
# E2E: 真实 SignalFusion 集成
# ============================================================


class TestE2ERealSignalFusion:
    """端到端: 真实 SignalFusionEngine 对比"""

    @pytest.mark.e2e
    def test_e2e_real_signal_fusion_vs_agent_consensus(self, tmp_path):
        """真实 SignalFusion 输出 vs Agent 共识"""
        # 1. 初始化真实 SignalFusionEngine
        fusion = SignalFusionEngine()

        # 2. 初始化 orchestrator (用 tmp_path 隔离审计日志)
        orch = FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")

        # 3. 准备 mock 标的上下文
        symbol = "600276.SH"
        ctx = {
            "kline": [
                {"close": 30 + i * 0.3, "volume": 1e7, "amount": 3e8}
                for i in range(30)
            ],
            "fundamentals": {
                "pe": 35, "pb": 6, "roe": 0.12,
                "pe_percentile": 0.15, "pb_percentile": 0.20,
            },
            "news_items": [{"title": "业绩利好", "content": "增长", "symbol": symbol}],
            "macro_data": {
                "bond_10y_yield": 0.024, "north_flow": 8e9,
                "industry_score": 0.75, "index_return_20d": 0.06,
            },
            "position_weight": 0.08,
            "beta": 1.1,
        }

        # 4. SignalFusion 调用 (alpha 信号)
        alpha_signals = {
            symbol: {"strength": 0.5, "confidence": 0.8},
        }
        fusion_results = fusion.fuse(alpha_signals=alpha_signals)
        assert len(fusion_results) >= 1
        fusion_signal = fusion_results[0]
        assert isinstance(fusion_signal, FusionSignal)
        assert fusion_signal.symbol == symbol

        # 5. Agent orchestrate
        consensus = orch.orchestrate(symbol, ctx)
        assert consensus.symbol == symbol

        # 6. shadow_compare (真实 FusionSignal)
        diff = orch.shadow_compare(fusion_signal, consensus)
        assert diff.symbol == symbol
        assert diff.fusion_strength == fusion_signal.strength
        assert diff.agent_strength == consensus.strength

        # 7. save_audit_log
        log_path = orch.save_audit_log(consensus, diff, trade_date="20260726")
        assert log_path is not None
        assert log_path.exists()

        # 8. 验证审计日志内容
        loaded = orch.load_audit_log("20260726")
        assert len(loaded) == 1
        entry = loaded[0]
        assert entry["consensus"]["symbol"] == symbol
        assert entry["diff"]["fusion_strength"] == fusion_signal.strength
        assert "agent_decisions" in entry["consensus"]
        assert len(entry["consensus"]["agent_decisions"]) >= 3


# ============================================================
# E2E: 多标的批量 Shadow Mode
# ============================================================


class TestE2EMultiSymbolBatch:
    """端到端: 多标的批量对比"""

    @pytest.mark.e2e
    def test_e2e_multi_symbol_batch_shadow_mode(self, tmp_path):
        """多标的批量 Shadow Mode (3 个标的)"""
        fusion = SignalFusionEngine()
        orch = FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")

        symbols_and_contexts = {
            "600276.SH": {
                "kline": [{"close": 30 + i * 0.3, "volume": 1e7, "amount": 3e8} for i in range(30)],
                "fundamentals": {"pe": 35, "pb": 6, "pe_percentile": 0.15, "pb_percentile": 0.20, "roe": 0.18},
                "news_items": [{"title": "业绩利好", "content": "增长", "symbol": "600276.SH"}],
                "macro_data": {"bond_10y_yield": 0.024, "north_flow": 8e9, "industry_score": 0.75, "index_return_20d": 0.06},
                "position_weight": 0.08,
                "beta": 1.1,
            },
            "000001.SZ": {
                "kline": [{"close": 15 + i * 0.05, "volume": 5e7, "amount": 7e8} for i in range(30)],
                "fundamentals": {"pe": 8, "pb": 0.7, "pe_percentile": 0.10, "pb_percentile": 0.05, "roe": 0.11},
                "news_items": [{"title": "业绩稳定", "content": "增长", "symbol": "000001.SZ"}],
                "macro_data": {"bond_10y_yield": 0.024, "north_flow": 8e9, "industry_score": 0.50, "index_return_20d": 0.06},
                "position_weight": 0.12,
                "beta": 1.2,
            },
            "510300.SH": {
                "kline": [{"close": 4.5 + 0.01 * i, "volume": 1e8, "amount": 4e8} for i in range(30)],
                "fundamentals": {"pe": 12, "pb": 1.3, "pe_percentile": 0.30, "pb_percentile": 0.20, "roe": 0.10},
                "news_items": [],
                "macro_data": {"bond_10y_yield": 0.024, "north_flow": 8e9, "industry_score": 0.60, "index_return_20d": 0.06},
                "position_weight": 0.05,
                "beta": 1.0,
            },
        }

        # 1. SignalFusion 批量调用
        alpha_signals = {
            sym: {"strength": 0.3, "confidence": 0.7}
            for sym in symbols_and_contexts
        }
        fusion_results = fusion.fuse(alpha_signals=alpha_signals)
        fusion_map = {r.symbol: r for r in fusion_results}

        # 2. Agent 批量 orchestrate + compare
        diffs = []
        for symbol, ctx in symbols_and_contexts.items():
            consensus = orch.orchestrate(symbol, ctx)
            fusion_signal = fusion_map.get(symbol)
            if fusion_signal:
                diff = orch.shadow_compare(fusion_signal, consensus)
                diffs.append(diff)
                orch.save_audit_log(consensus, diff, trade_date="20260726")

        # 3. 验证
        assert len(diffs) == 3
        for diff in diffs:
            assert diff.symbol in symbols_and_contexts
            assert -1.0 <= diff.fusion_strength <= 1.0
            assert -1.0 <= diff.agent_strength <= 1.0
            assert isinstance(diff.direction_match, bool)

        # 4. 审计日志累积
        loaded = orch.load_audit_log("20260726")
        assert len(loaded) == 3


# ============================================================
# E2E: 数据缺失场景
# ============================================================


class TestE2EDataMissingScenarios:
    """端到端: 数据缺失场景的安全降级"""

    @pytest.mark.e2e
    def test_e2e_completely_empty_context(self, tmp_path):
        """完全空的上下文 → hold + 审计日志记录"""
        orch = FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")
        consensus = orch.orchestrate("EMPTY.SH", {})
        assert consensus.action == "hold"
        assert consensus.strength == 0.0

        log_path = orch.save_audit_log(consensus, None, trade_date="20260726")
        assert log_path is not None

    @pytest.mark.e2e
    def test_e2e_partial_data_no_crash(self, tmp_path):
        """部分数据缺失不应崩溃"""
        orch = FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")
        # 只提供 kline, 不提供其他
        ctx = {
            "kline": [{"close": 10 + i * 0.1} for i in range(30)],  # 缺 volume/amount
        }
        consensus = orch.orchestrate("PARTIAL.SH", ctx)
        # 应该正常返回 (即使部分 Agent 跳过)
        assert consensus.action in ("buy", "hold", "sell", "veto")
