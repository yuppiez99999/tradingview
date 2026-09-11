"""decision_gate 测试: shadow/paper/auto 模式切换 + 硬风控拦截"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai_decision.decision_gate import RiskContext, apply_mode, run_hard_risk
from ai_decision.models import TradingDecision


def _dec(action="buy", conf=0.8, verdict="AUTO"):
    return TradingDecision(
        symbol="600519",
        action=action,
        strength=0.6,
        confidence=conf,
        verdict_type=verdict,
    )


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
    rc = RiskContext(
        symbol="600519", portfolio_value=1_000_000, proposed_notional=50_000
    )  # 5% > 2%
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
    rc = RiskContext(
        symbol="600519", portfolio_value=1_000_000, proposed_notional=10_000
    )
    gate = run_hard_risk(d, rc)
    out = apply_mode(d, gate, mode="auto")
    assert out.executed is True
    assert out.veto is False


def test_mode_auto_escalates_low_confidence():
    d = _dec(conf=0.5, verdict="REVIEW")
    rc = RiskContext(
        symbol="600519", portfolio_value=1_000_000, proposed_notional=10_000
    )
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


# ============================================================
# S-2 (Issue #13): 涨跌停状态接入主链
# ============================================================
# 巡检事实: L1 的涨跌停分支早已实现, 但主链 (CLI / run_decision) 从不设置
# RiskContext.is_limit_up/is_limit_down → 该保护形同虚设。以下锁定新语义。


def test_price_limit_status_injection_blocks_buy():
    """price_limit_status 标注涨停 → 买入被否决 (主链注入路径)。"""
    d = _dec(action="buy")
    rc = RiskContext(symbol="600519", price_limit_status={"600519": "limit_up"})
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "涨停" in gate.veto_reason
    assert gate.risk_checks["limit_up"] is True


def test_price_limit_status_injection_blocks_sell_on_limit_down():
    d = _dec(action="sell")
    rc = RiskContext(symbol="600519", price_limit_status={"600519": "limit_down"})
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "跌停" in gate.veto_reason


def test_price_limit_status_normal_does_not_veto():
    d = _dec(action="buy")
    rc = RiskContext(symbol="600519", price_limit_status={"600519": "normal"})
    gate = run_hard_risk(d, rc)
    assert gate.veto is False
    assert gate.risk_checks["limit_up"] is False


def test_stale_price_limit_status_is_fail_closed():
    """S-2 核心: 状态过期时, 即使是 normal 也不能证明今日未涨停 → 保守拒绝买入。"""
    d = _dec(action="buy")
    rc = RiskContext(
        symbol="600519",
        price_limit_status={"600519": "normal"},
        price_limit_stale=True,
    )
    gate = run_hard_risk(d, rc)
    assert gate.veto is True
    assert "涨停" in gate.veto_reason
    assert gate.risk_checks["price_limit_stale"] is True


def test_explicit_flag_wins_over_status_map():
    """显式 is_limit_up=True 优先于 status map (调用方/测试强制否决)。"""
    d = _dec(action="buy")
    rc = RiskContext(
        symbol="600519", is_limit_up=True, price_limit_status={"600519": "normal"}
    )
    assert run_hard_risk(d, rc).veto is True


def test_unknown_symbol_is_not_vetoed():
    """状态表中无该标的 → 不误伤 (仅对已标注标的生效)。"""
    d = _dec(action="buy")
    d.symbol = "601988"
    rc = RiskContext(symbol="601988", price_limit_status={"600519": "limit_up"})
    assert run_hard_risk(d, rc).veto is False
