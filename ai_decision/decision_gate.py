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
    # P0-M5 (2026-09-13): 缺省 None (未知) — 原默认 1_000_000 是虚构基数,
    # 上游不注入真实净值时单笔/日内限额检查基于错误基数放行。
    # 有 proposed_notional 而净值未知 → gate fail-closed 拒绝 (见 run_hard_risk)。
    portfolio_value: float | None = None
    proposed_notional: float = 0.0  # 计划下单名义金额
    daily_used_pct: float = 0.0  # 日内已用净值比例
    is_limit_up: bool = False  # 涨停不可买
    is_limit_down: bool = False  # 跌停不可卖
    blacklist: tuple = ()  # 黑名单标的
    agent_veto: bool = False  # RiskAgent 否决
    agent_veto_reason: str = ""
    # S-2 (2026-09-11, Issue #13): 涨跌停状态接入主链
    # 背景: L1 的 is_limit_up/is_limit_down 分支早已实现, 但主链 (CLI/run_decision)
    # 从不设置这两个字段 → 涨跌停保护形同虚设 (沙箱实测: 默认 rc 的 limit_up 检查恒 False)。
    # 现:
    #   price_limit_status: 各标的当日涨跌停状态 {code: "limit_up"/"limit_down"/"normal"}
    #     由 utils.price_limit_refresh 刷新 (基于前收盘价按板块规则计算), 主链自动注入;
    #   price_limit_stale: 状态是否过期 (刷新时间早于本交易日 → 禁止买入, fail-closed);
    #     涨停会连续多日, 旧状态持久化会把"昨日涨停"当"今日涨停"持续 veto。
    price_limit_status: dict = field(default_factory=dict)
    price_limit_stale: bool = False

    def is_limit_up_for(self, symbol: str) -> bool:
        """判断标的当前是否处于涨停不可买状态 (S-2)。

        显式 ``is_limit_up=True`` 优先 (调用方/测试强否决);
        否则查 ``price_limit_status``; **状态过期时保守返回 True**
        (涨停状态陈旧 → 宁可拒绝买入也不放行)。
        """
        if self.is_limit_up:
            return True
        status = (self.price_limit_status or {}).get(symbol)
        if status is None:
            return False
        if status == "limit_up":
            return True
        if status == "normal" and self.price_limit_stale:
            return True  # 陈旧状态 + 非涨停 → 不能证明今日未涨停, 保守拒绝
        return False

    def is_limit_down_for(self, symbol: str) -> bool:
        """判断标的当前是否处于跌停不可卖状态 (S-2); 语义同 is_limit_up_for。"""
        if self.is_limit_down:
            return True
        status = (self.price_limit_status or {}).get(symbol)
        if status is None:
            return False
        if status == "limit_down":
            return True
        if status == "normal" and self.price_limit_stale:
            return True
        return False


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
    # S-2: 经访问器读取 (支持 price_limit_status 注入 + 过期保守拒绝)
    limit_up_veto = rc.is_limit_up_for(decision.symbol)
    limit_down_veto = rc.is_limit_down_for(decision.symbol)
    if decision.action == "buy" and limit_up_veto:
        res.veto = True
        res.veto_reason = f"{decision.symbol} 涨停, 不可买入"
        res.passed = False
        checks["limit_up"] = True
    elif decision.action == "sell" and limit_down_veto:
        res.veto = True
        res.veto_reason = f"{decision.symbol} 跌停, 不可卖出"
        res.passed = False
        checks["limit_down"] = True
    else:
        checks["limit_up"] = limit_up_veto
        checks["limit_down"] = limit_down_veto
    if rc.price_limit_stale:
        # 审计可见: 状态陈旧时对 normal 标的也保守拒绝买入/卖出
        checks["price_limit_stale"] = True

    # 4. 单笔金额上限 (<= 净值 max_single_pct)
    # P0-M5: 有下单金额但净值未知 → fail-closed (虚构基数等于没有风控)
    single_pct = 0.0
    if rc.proposed_notional and not rc.portfolio_value:
        checks["single_pct"] = {
            "value": None,
            "limit": float(get_config("gate.max_single_pct", 0.02)),
            "ok": False,
            "reason": "portfolio_value missing",
        }
        res.veto = True
        res.veto_reason = res.veto_reason + "; " if res.veto_reason else ""
        res.veto_reason += (
            f"净值数据缺失 (fail-closed): proposed_notional={rc.proposed_notional:.0f} "
            "但 portfolio_value 未知, 无法校验单笔/日内上限"
        )
        res.passed = False
    else:
        single_pct = (
            rc.proposed_notional / rc.portfolio_value if rc.portfolio_value else 0.0
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
    # H1 修复 (2026-09-12): require_judge_auto 语义 = "必须有真实辩论的 AUTO 裁决
    # 才能自动放行"。原实现把 FAST 与 AUTO 并列豁免, 而修复前辩论引擎从未触发、
    # verdict_type 恒为 FAST → 该闸从未生效 (FAST 冒充 AUTO)。
    # 现仅 AUTO 豁免; 未触发辩论的快速聚合 (FAST) 一律升级人工。
    # 如需恢复旧行为, 显式配置 gate.require_judge_auto=false。
    if require_auto and decision.verdict_type != "AUTO":
        escalation = True
        esc_reasons.append(f"裁决类型 {decision.verdict_type} 非 AUTO")
    # M5 修复 (2026-09-12): 决策链降级 (五 Agent 规则兜底 / judge 无响应 /
    # Mock provider) → 强制升级人工。原缺陷: 降级后无标记观点继续聚合,
    # 仅靠低置信度"碰巧"被 min_confidence 拦截, 实质 fail-open。
    if getattr(decision, "degraded", False):
        escalation = True
        reasons = "; ".join(getattr(decision, "degraded_reasons", []) or [])
        esc_reasons.append(f"决策链降级 (degraded): {reasons}")

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
