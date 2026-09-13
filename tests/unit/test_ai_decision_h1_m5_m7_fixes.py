"""H1/M5/M7 修复回归测试 (2026-09-12)

H1: 辩论引擎死代码 — _build_debate_priors 从五 Agent 单票构造真实多空先验,
    辩论可真实触发; decision_gate 不再让 FAST 冒充 AUTO 豁免 require_judge_auto。
M5: 决策链降级标记 — 五 Agent 规则兜底 / judge Mock 降级 → decision.degraded=True,
    auto 模式强制升级人工 (fail-closed, 不再靠低置信度"碰巧"拦截)。
M7: run_decision 不再覆盖调用方预设的 RiskContext.agent_veto。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pytest

from ai_decision import orchestrator as orch
from ai_decision.consensus_aggregator import aggregate
from ai_decision.decision_gate import GateResult, RiskContext, apply_mode
from ai_decision.models import (
    DebateDecision,
    DebateRecord,
    DebateTrigger,
    TradingDecision,
)

# ============================================================
# 测试桩
# ============================================================


class _StubJudgeProv:
    """确定性 judge provider (Mock 口径, 无网络)。"""

    model_name = "mock:judge"

    def generate(self, prompt, system=None, timeout=None):
        return "看多: 动量占优, 置信度 0.85"


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(orch, "_write_audit", lambda d: "")


@pytest.fixture(autouse=True)
def _stub_judge(monkeypatch):
    from ai_decision.health import ModelHealthMonitor

    monkeypatch.setattr(
        ModelHealthMonitor, "get_provider_with_fallback", lambda self, role: _StubJudgeProv()
    )


# ============================================================
# H1: 多空先验与辩论触发
# ============================================================


def test_h1_priors_from_divergent_agents_trigger_debate():
    """多空分歧真实存在且双方置信度足够 → 辩论触发 (修复前数学上不可能)。"""
    agents = [
        {"agent_name": "trend", "action": "buy", "strength": 0.8, "confidence": 0.9},
        {"agent_name": "risk", "action": "sell", "strength": -0.7, "confidence": 0.8},
    ]
    bull, bear = orch._build_debate_priors(agents, 0.1, 0.85)
    assert bull.strength > 0 and bear.strength < 0
    trigger = DebateTrigger(
        bull_strength=bull.strength,
        bear_strength=bear.strength,
        bull_conf=bull.confidence,
        bear_conf=bear.confidence,
    )
    assert trigger.should_debate() is True


def test_h1_priors_one_sided_no_debate():
    """五 Agent 全部看多 → 无分歧, 不触发辩论 ( bear 侧零证据)。"""
    agents = [
        {"agent_name": "a", "action": "buy", "strength": 0.6, "confidence": 0.9},
        {"agent_name": "b", "action": "buy", "strength": 0.7, "confidence": 0.8},
    ]
    bull, bear = orch._build_debate_priors(agents, 0.65, 0.85)
    assert bull.strength > 0
    assert bear.strength == 0.0
    trigger = DebateTrigger(
        bull_strength=bull.strength,
        bear_strength=bear.strength,
        bull_conf=bull.confidence,
        bear_conf=bear.confidence,
    )
    assert trigger.should_debate() is False


def test_h1_priors_fallback_without_agent_details():
    """无单票明细 (规则兜底) → 回退共识标量拆分, 与历史行为一致。"""
    bull, bear = orch._build_debate_priors([], 0.5, 0.7)
    assert bull.strength == 0.5 and bear.strength == 0.0
    assert bull.confidence == 0.7


def test_h1_strength_missing_derived_from_action():
    """tradingagents 桥接路径无 strength 字段 → 由 action+confidence 推导。"""
    agents = [
        {"agent": "tradingagents", "action": "buy", "confidence": 0.9},
        {"agent": "risk", "action": "sell", "confidence": 0.85},
    ]
    bull, bear = orch._build_debate_priors(agents, 0.0, 0.0)
    assert bull.strength == pytest.approx(0.9)
    assert bear.strength == pytest.approx(-0.85)


def test_h1_run_decision_debate_actually_runs(monkeypatch):
    """端到端: 分歧场景下 run_debate 被真实调用, verdict_type=AUTO。"""
    calls: list[tuple] = []

    def _fake_agents(symbol, ctx):
        return {
            "action": "hold",
            "strength": 0.05,
            "confidence": 0.85,
            "agent_decisions": [
                {"agent_name": "trend", "action": "buy", "strength": 0.8, "confidence": 0.9},
                {"agent_name": "risk", "action": "sell", "strength": -0.7, "confidence": 0.8},
            ],
            "veto": False,
            "veto_reason": "",
            "degraded": False,
        }

    def _fake_debate(symbol, prompt, bull, bear, timeout=None):
        calls.append((symbol,))
        return (
            DebateRecord(symbol=symbol, triggered=True, rounds=2),
            DebateDecision(
                action="buy", strength=0.5, confidence=0.8, verdict_type="AUTO"
            ),
        )

    monkeypatch.setattr(orch, "_run_five_agents", _fake_agents)
    monkeypatch.setattr(orch, "run_debate", _fake_debate)

    dec = orch.run_decision("600519", mode="shadow")
    assert len(calls) == 1  # 辩论引擎被真实调用 (修复前恒为 0)
    assert dec.verdict_type == "AUTO"
    assert dec.debate is not None and dec.debate["triggered"] is True


# ============================================================
# H1b: FAST 不再冒充 AUTO 豁免
# ============================================================


def test_h1b_fast_verdict_escalates_in_auto_mode():
    gate = GateResult(passed=True, veto=False, veto_reason="", risk_checks={})
    dec = TradingDecision(
        action="buy", strength=0.6, confidence=0.9, verdict_type="FAST"
    )
    out = apply_mode(dec, gate, mode="auto")
    assert out.executed is False
    assert out.escalation is True
    assert "非 AUTO" in out.escalation_reason


def test_h1b_auto_verdict_can_execute():
    gate = GateResult(passed=True, veto=False, veto_reason="", risk_checks={})
    dec = TradingDecision(
        action="buy", strength=0.6, confidence=0.9, verdict_type="AUTO"
    )
    out = apply_mode(dec, gate, mode="auto")
    assert out.executed is True
    assert out.escalation is False


# ============================================================
# M5: 降级标记 fail-closed
# ============================================================


def _degraded_agents_stub(exc: str = "unit-test"):
    def _fake(symbol, ctx):
        return {
            "action": "buy",
            "strength": 0.8,
            "confidence": 0.9,
            "agent_decisions": [],
            "veto": False,
            "veto_reason": "",
            "mode": "shadow",
            "degraded": True,
            "degraded_reason": f"五 Agent 协调器不可用, 规则兜底: {exc}",
        }

    return _fake


def test_m5_degraded_marked_and_escalated_in_auto(monkeypatch):
    """五 Agent 兜底(降级) + 高置信度 buy → auto 模式必须升级人工, 不得执行。"""
    monkeypatch.setattr(orch, "_run_five_agents", _degraded_agents_stub("boom"))
    dec = orch.run_decision("600519", mode="auto")
    assert dec.degraded is True
    assert any("五 Agent" in r for r in dec.degraded_reasons)
    assert dec.executed is False
    assert dec.escalation is True
    assert "degraded" in dec.escalation_reason


def test_m5_mock_judge_marks_degraded(monkeypatch):
    """judge 为 Mock provider → degraded 置位, auto 模式不执行。"""
    monkeypatch.setattr(
        orch,
        "_run_five_agents",
        lambda symbol, ctx: {
            "action": "buy",
            "strength": 0.8,
            "confidence": 0.9,
            "agent_decisions": [
                {"agent_name": "a", "action": "buy", "strength": 0.8, "confidence": 0.9}
            ],
            "veto": False,
            "veto_reason": "",
            "degraded": False,
        },
    )
    dec = orch.run_decision("600519", mode="auto")
    assert any("Mock" in r for r in dec.degraded_reasons)
    assert dec.executed is False


def test_m5_judge_no_response_marks_degraded(monkeypatch):
    class _DeadProv:
        model_name = "mock:judge"

        def generate(self, prompt, system=None, timeout=None):
            return None

    from ai_decision.health import ModelHealthMonitor

    monkeypatch.setattr(
        ModelHealthMonitor, "get_provider_with_fallback", lambda self, role: _DeadProv()
    )
    monkeypatch.setattr(
        orch,
        "_run_five_agents",
        lambda symbol, ctx: {
            "action": "buy",
            "strength": 0.8,
            "confidence": 0.9,
            "agent_decisions": [
                {"agent_name": "a", "action": "buy", "strength": 0.8, "confidence": 0.9}
            ],
            "veto": False,
            "veto_reason": "",
            "degraded": False,
        },
    )
    dec = orch.run_decision("600519", mode="auto")
    assert any("judge 无响应" in r for r in dec.degraded_reasons)
    assert dec.executed is False


def test_m5_degraded_fields_serialized():
    dec = TradingDecision(degraded=True, degraded_reasons=["x"])
    d = dec.to_dict()
    assert d["degraded"] is True
    assert d["degraded_reasons"] == ["x"]


# ============================================================
# M7: 上游 agent_veto 不被覆盖
# ============================================================


def test_m7_upstream_veto_preserved(monkeypatch):
    """调用方预设 agent_veto=True + 五 Agent 无 veto → 否决必须保留。"""
    monkeypatch.setattr(
        orch,
        "_run_five_agents",
        lambda symbol, ctx: {
            "action": "buy",
            "strength": 0.8,
            "confidence": 0.9,
            "agent_decisions": [],
            "veto": False,
            "veto_reason": "",
            "degraded": False,
        },
    )
    rc = RiskContext(symbol="600519", agent_veto=True, agent_veto_reason="人工风控标记")
    dec = orch.run_decision("600519", mode="shadow", risk_context=rc)
    assert dec.veto is True
    assert dec.action == "veto"
    assert "人工风控标记" in dec.veto_reason


def test_m7_agent_veto_still_applies(monkeypatch):
    """五 Agent veto 正常路径不受影响。"""
    monkeypatch.setattr(
        orch,
        "_run_five_agents",
        lambda symbol, ctx: {
            "action": "veto",
            "strength": -1.0,
            "confidence": 0.95,
            "agent_decisions": [],
            "veto": True,
            "veto_reason": "RiskAgent: 连续跌停流动性风险",
            "degraded": False,
        },
    )
    dec = orch.run_decision("600519", mode="shadow")
    assert dec.veto is True
    assert "RiskAgent" in dec.veto_reason


# ============================================================
# L1 回归: 空视图 + debate 不崩溃
# ============================================================


def test_l1_aggregate_empty_views_with_debate_safe():
    action, _s, _c = aggregate(
        [], debate=DebateDecision(action="hold", confidence=0.5, verdict_type="HOLD")
    )
    assert action == "hold"
