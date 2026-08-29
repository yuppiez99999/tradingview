"""
ai_decision.decision_gate — 决策门 (硬风控独立 + 模式开关)
=========================================================

铁律: 硬风控永远独立于 AI 运行, 不受 AI 输出影响.
  - 黑名单 / 单笔金额上限 / 日内累计上限 / 涨跌停处理 / 价格保护
  - RiskAgent 一票否决 (来自五 Agent 共识)

模式开关:
  - shadow: 仅写审计日志, 不产出可执行指令, 不触发下单 (默认)
  - paper:  产出模拟指令, 不触发 broker
  - auto:   经阈值 + 升级规则后放行执行

升级规则 (auto 也需谨慎):
  - 置信度 < min_confidence -> 升级人工
  - Judge 裁决类型非 AUTO -> 升级人工
  - 单笔 > 净值 max_single_pct 或 日内累计 > max_daily_pct -> 升级人工
  - 任何硬风控失败 -> veto, 不执行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_decision.config import get_config
from ai_decision.models import TradingDecision

logger = logging.getLogger("ai_decision.decision_gate")


@dataclass
class RiskContext:
    """风控所需运行态数据。

    Attributes:
        symbol: 标的代码
        portfolio_value: 组合净值
        proposed_notional: 计划下单名义金额
        daily_used_pct: 日内已用净值比例
        is_limit_up: 是否涨停 (涨停不可买)
        is_limit_down: 是否跌停 (跌停不可卖)
        blacklist: 黑名单标的元组
        agent_veto: RiskAgent 是否一票否决
        agent_veto_reason: RiskAgent 否决原因
    """

    symbol: str = ""
    portfolio_value: float = 1_000_000.0  # 组合净值
    proposed_notional: float = 0.0  # 计划下单名义金额
    daily_used_pct: float = 0.0  # 日内已用净值比例
    is_limit_up: bool = False  # 涨停不可买
    is_limit_down: bool = False  # 跌停不可卖
    blacklist: tuple = ()  # 黑名单标的
    agent_veto: bool = False  # RiskAgent 否决
    agent_veto_reason: str = ""


@dataclass
class GateResult:
    """硬风控检查结果。

    Attributes:
        passed: 是否通过硬风控 (veto 取反)
        veto: 是否硬否决
        veto_reason: 否决原因汇总文本
        risk_checks: 各项检查明细键值对
    """

    passed: bool = True  # 是否通过硬风控
    veto: bool = False
    veto_reason: str = ""
    risk_checks: dict[str, Any] = field(default_factory=dict)


def run_hard_risk(decision: TradingDecision, rc: RiskContext) -> GateResult:
    """独立硬风控检查 (永远运行, 与 AI 输出无关)。

    依次执行黑名单、RiskAgent 否决、涨跌停、单笔金额上限、日内累计上限
    五项检查，任一失败即 veto。

    Args:
        decision: 待校验的交易决策
        rc: 风控运行态数据

    Returns:
        GateResult: 含 passed/veto/veto_reason/risk_checks 的检查结果
    """
    checks: dict[str, Any] = {}
    res = GateResult()

    # 1. 黑名单
    blocked = decision.symbol in rc.blacklist
    checks["blacklist"] = {"symbol": decision.symbol, "blocked": blocked}
    if blocked:
        res.veto = True
        res.veto_reason = f"标的 {decision.symbol} 在黑名单"
        res.passed = False

    # 2. RiskAgent 否决
    checks["agent_veto"] = {"veto": rc.agent_veto, "reason": rc.agent_veto_reason}
    if rc.agent_veto:
        res.veto = True
        res.veto_reason = f"RiskAgent 否决: {rc.agent_veto_reason}"
        res.passed = False

    # 3. 涨跌停处理
    if decision.action == "buy" and rc.is_limit_up:
        res.veto = True
        res.veto_reason = f"{decision.symbol} 涨停, 不可买入"
        res.passed = False
        checks["limit_up"] = True
    elif decision.action == "sell" and rc.is_limit_down:
        res.veto = True
        res.veto_reason = f"{decision.symbol} 跌停, 不可卖出"
        res.passed = False
        checks["limit_down"] = True
    else:
        checks["limit_up"] = rc.is_limit_up
        checks["limit_down"] = rc.is_limit_down

    # 4. 单笔金额上限 (<= 净值 max_single_pct)
    single_pct = (
        rc.proposed_notional / rc.portfolio_value if rc.portfolio_value else 1.0
    )
    max_single = float(get_config("gate.max_single_pct", 0.02))
    checks["single_pct"] = {
        "value": round(single_pct, 4),
        "limit": max_single,
        "ok": single_pct <= max_single,
    }
    if single_pct > max_single:
        res.veto = True
        res.veto_reason = res.veto_reason + "; " if res.veto_reason else ""
        res.veto_reason += f"单笔 {single_pct:.2%} 超过上限 {max_single:.2%}"
        res.passed = False

    # 5. 日内累计上限
    total_pct = rc.daily_used_pct + single_pct
    max_daily = float(get_config("gate.max_daily_pct", 0.10))
    checks["daily_pct"] = {
        "value": round(total_pct, 4),
        "limit": max_daily,
        "ok": total_pct <= max_daily,
    }
    if total_pct > max_daily:
        res.veto = True
        res.veto_reason = res.veto_reason + "; " if res.veto_reason else ""
        res.veto_reason += f"日内累计 {total_pct:.2%} 超过上限 {max_daily:.2%}"
        res.passed = False

    res.risk_checks = checks
    return res


def apply_mode(
    decision: TradingDecision, gate: GateResult, mode: str | None = None
) -> TradingDecision:
    """按运行模式决定最终执行态并写入 decision。

    根据 gate 结果与运行模式 (shadow/paper/auto) 设置 decision 的
    executed/escalation/escalation_reason 字段。硬风控否决时强制
    action="veto" 且不可执行。

    Args:
        decision: 待写入执行态的交易决策
        gate: 硬风控检查结果
        mode: 覆盖配置中的模式 (shadow/paper/auto)；None 时读全局配置

    Returns:
        TradingDecision: 写入 mode/veto/executed/escalation 等字段后的决策对象
    """
    if mode is None:
        mode = get_config("mode", "shadow")
    decision.mode = mode
    decision.veto = gate.veto
    decision.veto_reason = gate.veto_reason
    decision.risk_checks = gate.risk_checks

    # 硬风控否决 -> 永远不执行
    if gate.veto:
        decision.action = "veto"
        decision.executed = False
        decision.escalation = True
        decision.escalation_reason = "硬风控否决, 需人工复核"
        return decision

    # 升级规则判断
    min_conf = float(get_config("gate.min_confidence", 0.7))
    require_auto = bool(get_config("gate.require_judge_auto", True))
    escalation = False
    esc_reasons = []
    if decision.confidence < min_conf:
        escalation = True
        esc_reasons.append(f"置信度 {decision.confidence:.2f} < {min_conf:.2f}")
    if require_auto and decision.verdict_type not in ("AUTO", "FAST"):
        escalation = True
        esc_reasons.append(f"裁决类型 {decision.verdict_type} 非 AUTO")

    if mode == "shadow":
        decision.executed = False
        decision.escalation = escalation
        decision.escalation_reason = "; ".join(esc_reasons)
    elif mode == "paper":
        decision.executed = False  # 模拟指令, 不触发 broker
        decision.escalation = escalation
        decision.escalation_reason = "; ".join(esc_reasons)
    elif mode == "auto":
        # P0 修复: auto 模式下仅 buy/sell 决策可 executed=True
        # 原代码不检查 action, hold/veto 决策也会 executed=True, 触发真实下单
        if decision.action not in ("buy", "sell"):
            decision.executed = False
            decision.escalation = True
            decision.escalation_reason = (
                f"action={decision.action} 不可执行, 仅 buy/sell 允许自动下单"
            )
        elif escalation:
            decision.executed = False
            decision.escalation = True
            decision.escalation_reason = "升级人工: " + "; ".join(esc_reasons)
        else:
            decision.executed = True
            decision.escalation = False
    else:
        # 未知模式视为 shadow
        decision.executed = False
        decision.escalation = True
        decision.escalation_reason = f"未知模式 {mode}, 按 shadow 处理"
    return decision
