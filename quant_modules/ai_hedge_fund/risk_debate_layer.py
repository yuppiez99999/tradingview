"""3 方风控辩论层 (v8.6+ 融合 TradingAgents risk_mgmt)

借鉴 TauricResearch/TradingAgents 的 aggressive/conservative/neutral 三方辩论机制,
在 risk_management_agent 与 portfolio_manager 之间插入一层风控辩论节点。

与 debate_layer.py (多空研究员辩论) 的区别:
    - debate_layer: Bull vs Bear (研究方向对抗)
    - risk_debate_layer: Aggressive vs Conservative vs Neutral (风控立场对抗)

辩论流程:
    每轮 3 个 debator 各自基于风控分析 + 对方上轮论证生成新论证
    默认 1 轮 (可配置 max_risk_discuss_rounds)

降级: LLM 不可用时返回规则模式 (基于波动率 + 仓位风险等级聚合)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("ai_hedge_fund.risk_debate")

_MAX_ROUNDS = int(os.environ.get("AI_HEDGE_RISK_DEBATE_ROUNDS", "1"))


class RiskDebateResult(BaseModel):
    """3 方风控辩论结果"""
    aggressive_stance: Literal["aggressive", "neutral", "conservative"] = "neutral"
    conservative_stance: Literal["aggressive", "neutral", "conservative"] = "neutral"
    neutral_stance: Literal["aggressive", "neutral", "conservative"] = "neutral"
    aggressive_argument: str = Field(default="", description="激进方论证")
    conservative_argument: str = Field(default="", description="保守方论证")
    neutral_argument: str = Field(default="", description="中立方论证")
    consensus: Literal["approve", "reduce", "reject"] = "approve"
    summary: str = Field(default="", description="辩论摘要")


def _build_prompt(role: str, trader_decision: str, risk_report: str,
                  history: str, other_responses: dict[str, str]) -> str:
    """构建 debator prompt (借鉴 TradingAgents aggressive/conservative/neutral)"""
    role_desc = {
        "aggressive": (
            "As the Aggressive Risk Analyst, champion high-reward opportunities. "
            "Focus on upside, growth potential, and competitive advantages. "
            "Counter the conservative and neutral stances with data-driven rebuttals."
        ),
        "conservative": (
            "As the Conservative Risk Analyst, prioritize capital preservation. "
            "Focus on downside, tail risk, and margin of safety. "
            "Counter the aggressive and neutral stances with caution-driven rebuttals."
        ),
        "neutral": (
            "As the Neutral Risk Analyst, balance risk and reward objectively. "
            "Weigh both upside and downside without bias. "
            "Mediate between aggressive and conservative, identifying the most prudent path."
        ),
    }[role]

    other_args = "\n".join(
        f"{k} analyst: {v}" for k, v in other_responses.items() if v
    )

    return (
        f"{role_desc}\n\n"
        f"Trader's decision:\n{trader_decision}\n\n"
        f"Risk analysis report:\n{risk_report}\n\n"
        f"Current debate history:\n{history}\n\n"
        f"Other analysts' last arguments:\n{other_args}\n\n"
        f"If there are no responses from other viewpoints yet, present your own argument "
        f"based on the available data. Engage actively by addressing concerns raised, "
        f"refuting weaknesses in opposing logic. Output conversationally without special formatting."
    )


def _invoke_debator(role: str, prompt: str, state: dict) -> str:
    """调用 LLM 生成 debator 论证, 失败返回空字符串"""
    try:
        from quant_modules.ai_hedge_fund.llm.models import ModelProvider, get_model
        from quant_modules.ai_hedge_fund.utils.llm import get_agent_model_config

        model_name, model_provider = get_agent_model_config(state, "risk_debate")
        provider_enum = ModelProvider(model_provider) if isinstance(model_provider, str) else model_provider
        llm = get_model(model_name, provider_enum)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        return f"{role.capitalize()} Analyst: {content}"
    except Exception as e:
        logger.debug("risk debator %s LLM 调用失败, 降级规则模式: %s", role, e)
        return ""


def _rule_based_fallback(risk_report: str) -> RiskDebateResult:
    """LLM 不可用时的规则模式降级"""
    text = (risk_report or "").lower()
    if "high" in text or "危险" in text or "reject" in text:
        return RiskDebateResult(
            aggressive_argument="规则模式: 高风险但存在机会",
            conservative_argument="规则模式: 高风险, 建议减仓",
            neutral_argument="规则模式: 高风险, 谨慎观望",
            consensus="reduce",
            summary="规则模式降级: 检测到高风险信号",
        )
    return RiskDebateResult(
        aggressive_argument="规则模式: 风险可控, 可加仓",
        conservative_argument="规则模式: 风险可控, 维持仓位",
        neutral_argument="规则模式: 风险中性, 维持",
        consensus="approve",
        summary="规则模式降级: 风险可控",
    )


def risk_debate_node(state) -> dict:
    """3 方风控辩论节点 (插入 risk_management_agent → portfolio_manager 之间)

    读取 state["data"]["analyst_signals"]["risk_management_agent"] 作为风控报告
    + state["messages"][-1] 作为 trader_decision
    输出 state["data"]["analyst_signals"]["risk_debate"]
    """
    data = state.get("data", {})
    signals = data.get("analyst_signals", {})

    risk_report = json.dumps(
        signals.get("risk_management_agent", {}),
        ensure_ascii=False,
    )
    trader_decision = ""
    try:
        last_msg = state["messages"][-1]
        trader_decision = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    except (IndexError, KeyError, AttributeError):
        pass

    history = ""
    responses: dict[str, str] = {}
    for round_idx in range(_MAX_ROUNDS):
        for role in ("aggressive", "conservative", "neutral"):
            prompt = _build_prompt(role, trader_decision, risk_report, history, responses)
            argument = _invoke_debator(role, prompt, state)
            if argument:
                responses[role] = argument
                history += "\n" + argument

    if not any(responses.values()):
        result = _rule_based_fallback(risk_report)
    else:
        aggressive = responses.get("aggressive", "")
        conservative = responses.get("conservative", "")
        neutral = responses.get("neutral", "")
        consensus = "approve"
        if "reject" in conservative.lower() or "reject" in neutral.lower():
            consensus = "reject"
        elif "reduce" in conservative.lower() or "reduce" in neutral.lower():
            consensus = "reduce"
        result = RiskDebateResult(
            aggressive_argument=aggressive,
            conservative_argument=conservative,
            neutral_argument=neutral,
            consensus=consensus,
            summary=f"3 方风控辩论完成 ({_MAX_ROUNDS} 轮), 共识: {consensus}",
        )

    new_signals = {**signals, "risk_debate": result.model_dump()}
    return {"data": {"analyst_signals": new_signals}}
