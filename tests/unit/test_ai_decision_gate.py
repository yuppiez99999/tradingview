# -*- coding: utf-8 -*-
"""decision_gate 测试: shadow/paper/auto 模式切换 + 硬风控拦截"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.decision_gate import RiskContext, apply_mode, run_hard_risk
from ai_decision.models import TradingDecision


def _dec(action="buy", conf=0.8, verdict="AUTO"):
    return TradingDecision(symbol="600519", action=action, strength=0.6,
                           confidence=conf, verdict_type=verdict)


def test_hard_risk_blacklist_veto():
    d = _dec()
    rc = RiskContext(symbol="600519", blacklist=("600519",))
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "黑名单" in gate.veto_reason


def test_hard_risk_limit_up_no_buy():
    d = _dec(action="buy")
    rc = RiskContext(symbol="600519", is_limit_up=True)
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "涨停" in gate.veto_reason


def test_hard_risk_single_pct_exceed():
    d = _dec()
    rc = RiskContext(symbol="600519", portfolio_value=1_000_000,
                     proposed_notional=50_000)  # 5% > 2%
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "单笔" in gate.veto_reason


def test_mode_shadow_never_executes():
    d = _dec()
    rc = RiskContext(symbol="600519")
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="shadow")
    assert out.executed is False
    assert out.mode == "shadow"


def test_mode_paper_never_executes():
    d = _dec()
    rc = RiskContext(symbol="600519")
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="paper")
    assert out.executed is False


def test_mode_auto_executes_when_clean():
    d = _dec(conf=0.85, verdict="AUTO")
    rc = RiskContext(symbol="600519", portfolio_value=1_000_000,
                     proposed_notional=10_000)
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="auto")
    assert out.executed is True
    assert out.veto is False


def test_mode_auto_escalates_low_confidence():
    d = _dec(conf=0.5, verdict="REVIEW")
    rc = RiskContext(symbol="600519", portfolio_value=1_000_000,
                     proposed_notional=10_000)
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="auto")
    assert out.executed is False
    assert out.escalation is True


def test_veto_blocks_even_auto():
    d = _dec()
    rc = RiskContext(symbol="600519", blacklist=("600519",))
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="auto")
    assert out.action == "veto"
    assert out.executed is False
