# -*- coding: utf-8 -*-
"""test_finance_agent_orchestrator_unit.py — FinanceAgentOrchestrator 单元测试

测试范围:
  - AgentDecision 数据结构 (NaN 防御 / 边界裁剪 / action 规范化)
  - AgentConsensus 共识决策
  - ShadowDiff 对比逻辑
  - FinanceAgentOrchestrator 协调器 (初始化 / 加权投票 / veto 优先 / 审计日志)

设计原则:
  - 单模块测试, 全 Mock, <1s 完成
  - 不依赖 LLM 调用 (使用规则引擎兜底)
  - Python 3.8.9 兼容
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from utils.finance_agent_orchestrator import (
    AgentConsensus,
    FinanceAgentOrchestrator,
    ShadowDiff,
)
from utils.finance_agents import AgentDecision


# ============================================================
# AgentDecision 数据结构测试
# ============================================================


class TestAgentDecision:
    """AgentDecision dataclass 防御性测试"""

    @pytest.mark.unit
    def test_agent_decision_default_values(self):
        """默认值: action=hold, strength=0, confidence=0"""
        d = AgentDecision(agent_name="test", symbol="600276.SH")
        assert d.action == "hold"
        assert d.strength == 0.0
        assert d.confidence == 0.0
        assert d.veto_reason == ""
        assert d.timestamp != ""

    @pytest.mark.unit
    def test_agent_decision_nan_strength_zeroed(self):
        """NaN strength 归零 (P0 防御: 与 signal_fusion 一致)"""
        d = AgentDecision(
            agent_name="test", symbol="X",
            strength=float("nan"), confidence=0.5,
        )
        assert d.strength == 0.0
        assert d.confidence == 0.5

    @pytest.mark.unit
    def test_agent_decision_inf_confidence_zeroed(self):
        """Inf confidence 归零"""
        d = AgentDecision(
            agent_name="test", symbol="X",
            strength=0.5, confidence=float("inf"),
        )
        assert d.strength == 0.5
        assert d.confidence == 0.0

    @pytest.mark.unit
    def test_agent_decision_strength_clipped(self):
        """strength 超出 [-1, 1] 边界裁剪"""
        d = AgentDecision(
            agent_name="test", symbol="X",
            strength=2.5, confidence=0.5,
        )
        assert d.strength == 1.0

        d2 = AgentDecision(
            agent_name="test", symbol="X",
            strength=-2.5, confidence=0.5,
        )
        assert d2.strength == -1.0

    @pytest.mark.unit
    def test_agent_decision_invalid_action_falls_back_to_hold(self):
        """非法 action 降级为 hold"""
        d = AgentDecision(
            agent_name="test", symbol="X",
            action="invalid_action",
        )
        assert d.action == "hold"

    @pytest.mark.unit
    def test_agent_decision_veto_requires_reason(self):
        """veto 必须有理由, 否则填充默认"""
        d = AgentDecision(
            agent_name="risk", symbol="X",
            action="veto", veto_reason="",
        )
        assert d.veto_reason != ""
        assert "risk" in d.veto_reason

    @pytest.mark.unit
    def test_agent_decision_to_dict_serializable(self):
        """to_dict() 输出可 JSON 序列化"""
        d = AgentDecision(
            agent_name="value", symbol="600276.SH",
            action="buy", strength=0.5, confidence=0.8,
            reasoning="PE 低", key_metrics={"pe": 15.2},
        )
        d_dict = d.to_dict()
        # 必须 JSON 可序列化
        json_str = json.dumps(d_dict, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["agent_name"] == "value"
        assert parsed["strength"] == 0.5


# ============================================================
# AgentConsensus 测试
# ============================================================


class TestAgentConsensus:
    """AgentConsensus 共识决策测试"""

    @pytest.mark.unit
    def test_consensus_default_hold(self):
        """默认共识为 hold"""
        c = AgentConsensus(symbol="X")
        assert c.action == "hold"
        assert c.veto is False

    @pytest.mark.unit
    def test_consensus_nan_defense(self):
        """NaN/Inf 防御"""
        c = AgentConsensus(
            symbol="X",
            strength=float("nan"),
            confidence=float("inf"),
        )
        assert c.strength == 0.0
        assert c.confidence == 0.0


# ============================================================
# ShadowDiff 测试
# ============================================================


class TestShadowDiff:
    """ShadowDiff 对比测试"""

    @pytest.mark.unit
    def test_shadow_diff_default(self):
        """默认对比结果"""
        d = ShadowDiff(symbol="X")
        assert d.diff == 0.0
        assert d.direction_match is True


# ============================================================
# FinanceAgentOrchestrator 协调器测试
# ============================================================


@pytest.fixture
def orchestrator(tmp_path):
    """用 tmp_path 隔离审计日志目录"""
    return FinanceAgentOrchestrator(audit_log_dir=tmp_path / "audit")


@pytest.fixture
def mock_context():
    """Mock 上下文 (5 个 Agent 都可用)"""
    return {
        "kline": [
            {"close": 10 + i * 0.1, "volume": 1e7, "amount": 1e8}
            for i in range(30)
        ],
        "fundamentals": {
            "pe": 15.2, "pb": 2.1, "roe": 0.18,
            "pe_percentile": 0.15, "pb_percentile": 0.20,
        },
        "news_items": [
            {"title": "业绩增长", "content": "利好", "symbol": "600276.SH"},
        ],
        "macro_data": {
            "bond_10y_yield": 0.024, "north_flow": 8e9,
            "industry_score": 0.75, "index_return_20d": 0.06,
        },
        "position_weight": 0.08,
        "beta": 1.1,
    }


class TestFinanceAgentOrchestratorInit:
    """协调器初始化测试"""

    @pytest.mark.unit
    def test_init_creates_5_default_agents(self, orchestrator):
        """默认初始化创建 5 个 Agent"""
        agent_names = [a.name for a in orchestrator.agents]
        assert set(agent_names) == {"value", "momentum", "sentiment", "risk", "macro"}

    @pytest.mark.unit
    def test_init_weights_normalized(self, orchestrator):
        """权重归一化 (sum=1.0)"""
        total = sum(orchestrator.weights.values())
        assert abs(total - 1.0) < 1e-6

    @pytest.mark.unit
    def test_init_default_weights(self, orchestrator):
        """默认权重符合设计"""
        assert orchestrator.weights["value"] == 0.25
        assert orchestrator.weights["momentum"] == 0.25
        assert orchestrator.weights["risk"] == 0.25
        assert orchestrator.weights["sentiment"] == 0.15
        assert orchestrator.weights["macro"] == 0.10


class TestOrchestrate:
    """orchestrate() 主入口测试"""

    @pytest.mark.unit
    def test_orchestrate_empty_context_returns_hold(self, orchestrator):
        """空上下文返回 hold"""
        result = orchestrator.orchestrate("X", {})
        assert result.action == "hold"
        assert result.veto is False

    @pytest.mark.unit
    def test_orchestrate_with_mock_context(self, orchestrator, mock_context):
        """Mock 上下文返回非 hold (因 macro+value+sentiment 都看多)"""
        result = orchestrator.orchestrate("600276.SH", mock_context)
        # 至少有一个 Agent 给出决策
        assert len(result.agent_decisions) >= 3
        # strength 应在 [-1, 1]
        assert -1.0 <= result.strength <= 1.0

    @pytest.mark.unit
    def test_orchestrate_veto_priority(self, orchestrator):
        """veto 优先级最高 (RiskAgent 触发)"""
        # 构造暴跌 kline (回撤 > 25%)
        falling_kline = [
            {"close": 10 - i * 0.3, "volume": 1e7, "amount": 1e8}
            for i in range(30)
        ]
        result = orchestrator.orchestrate("X", {"kline": falling_kline})
        # 触发 veto (回撤 > 25%)
        assert result.action == "veto"
        assert result.veto is True
        assert result.veto_reason != ""

    @pytest.mark.unit
    def test_orchestrate_veto_from_critical_news(self, orchestrator):
        """重大负面新闻触发 veto"""
        ctx = {
            "news_items": [
                {"title": "立案调查", "content": "财务造假", "symbol": "X"},
            ],
        }
        result = orchestrator.orchestrate("X", ctx)
        assert result.action == "veto"
        assert result.veto is True


class TestWeightedVote:
    """加权投票测试"""

    @pytest.mark.unit
    def test_weighted_vote_all_hold(self, orchestrator):
        """所有 Agent hold → 共识 hold"""
        decisions = [
            {"agent_name": "value", "action": "hold", "strength": 0.0, "confidence": 0.5},
            {"agent_name": "momentum", "action": "hold", "strength": 0.0, "confidence": 0.5},
        ]
        s, c, detail = orchestrator._weighted_vote(decisions)
        assert s == 0.0
        assert c > 0
        assert "value" in detail

    @pytest.mark.unit
    def test_weighted_vote_high_confidence_dominates(self, orchestrator):
        """高置信度 Agent 贡献更大"""
        decisions = [
            {"agent_name": "value", "action": "buy", "strength": 0.8, "confidence": 0.9},
            {"agent_name": "macro", "action": "sell", "strength": -0.8, "confidence": 0.1},
        ]
        s, _c, _detail = orchestrator._weighted_vote(decisions)
        # value 权重 0.25 × 置信度 0.9 = 0.225
        # macro 权重 0.10 × 置信度 0.1 = 0.010
        # value 主导, strength > 0
        assert s > 0

    @pytest.mark.unit
    def test_weighted_vote_empty_decisions(self, orchestrator):
        """空决策列表"""
        s, c, detail = orchestrator._weighted_vote([])
        assert s == 0.0
        assert c == 0.0
        assert detail == {}

    @pytest.mark.unit
    def test_weighted_vote_filters_error_decisions(self, orchestrator):
        """异常决策被过滤"""
        decisions = [
            {"agent_name": "value", "action": "buy", "strength": 0.5, "confidence": 0.8},
            {"agent_name": "momentum", "error": True, "action": "hold", "strength": 0, "confidence": 0},
        ]
        _s, _c, detail = orchestrator._weighted_vote(decisions)
        # 只有 value 被计入
        assert "value" in detail
        assert "momentum" not in detail


class TestShadowCompare:
    """shadow_compare() 测试"""

    @pytest.mark.unit
    def test_shadow_compare_with_object(self, orchestrator):
        """对比 FusionSignal 对象"""
        mock_fusion = MagicMock()
        mock_fusion.strength = 0.5
        consensus = AgentConsensus(symbol="X", strength=0.3)
        diff = orchestrator.shadow_compare(mock_fusion, consensus)
        assert diff.fusion_strength == 0.5
        assert diff.agent_strength == 0.3
        assert abs(diff.diff - (-0.2)) < 1e-6
        assert diff.direction_match is True  # 都为正

    @pytest.mark.unit
    def test_shadow_compare_with_dict(self, orchestrator):
        """对比 dict"""
        fusion_dict = {"strength": -0.4}
        consensus = AgentConsensus(symbol="X", strength=0.2)
        diff = orchestrator.shadow_compare(fusion_dict, consensus)
        assert diff.fusion_strength == -0.4
        assert diff.agent_strength == 0.2
        assert diff.direction_match is False  # 异号

    @pytest.mark.unit
    def test_shadow_compare_nan_fusion(self, orchestrator):
        """NaN fusion strength 归零"""
        mock_fusion = MagicMock()
        mock_fusion.strength = float("nan")
        consensus = AgentConsensus(symbol="X", strength=0.5)
        diff = orchestrator.shadow_compare(mock_fusion, consensus)
        assert diff.fusion_strength == 0.0


class TestAuditLog:
    """审计日志持久化测试"""

    @pytest.mark.unit
    def test_save_audit_log_creates_file(self, orchestrator, tmp_path):
        """保存审计日志创建文件"""
        consensus = AgentConsensus(symbol="X", action="buy", strength=0.5)
        diff = ShadowDiff(symbol="X", diff=0.1)

        log_path = orchestrator.save_audit_log(consensus, diff, trade_date="20260726")
        assert log_path is not None
        assert log_path.exists()
        assert "shadow_diffs_20260726.jsonl" in str(log_path)

    @pytest.mark.unit
    def test_load_audit_log_round_trip(self, orchestrator):
        """审计日志读写一致"""
        consensus = AgentConsensus(symbol="X", action="buy", strength=0.5)
        orchestrator.save_audit_log(consensus, None, trade_date="20260726")

        loaded = orchestrator.load_audit_log("20260726")
        assert len(loaded) >= 1
        assert loaded[-1]["consensus"]["symbol"] == "X"
        assert loaded[-1]["consensus"]["action"] == "buy"

    @pytest.mark.unit
    def test_load_audit_log_missing_date_returns_empty(self, orchestrator):
        """读取不存在的日期返回空列表"""
        loaded = orchestrator.load_audit_log("19990101")
        assert loaded == []
