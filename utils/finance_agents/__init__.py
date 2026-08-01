# -*- coding: utf-8 -*-
"""
金融多 Agent 模块 (Finance Agents Package)
==========================================

借鉴 awesome-llm-apps/ai_hedge_fund 多 Agent 投票架构, 实现 5 个专家 Agent:
- ValueAgent        估值分析 (DCF / PE / PB)
- MomentumAgent     动量分析 (突破 / 回调)
- SentimentAgent    舆情分析 (复用 AIReportAgent)
- RiskAgent         风险分析 (回撤 / 相关性, 含 veto 权)
- MacroAgent        宏观分析 (利率 / 周期)

重要约束:
  - 仅 Shadow Mode 运行, 不进入 SignalFusionEngine 主信号路径
  - 所有 Agent 必须实现 BaseAgent.analyze() 接口
  - 决策通过 AgentDecision dataclass 标准化
  - Python 3.8.9 兼容: from __future__ import annotations

集成日期: 2026-07-26
集成批次: GitHub 周榜热门项目深度集成 (第二批)
来源: awesome-llm-apps (https://github.com/Shubhamsaboo/awesome-llm-apps)
"""

from __future__ import annotations

from utils.finance_agents.base_agent import BaseAgent, AgentDecision
from utils.finance_agents.value_agent import ValueAgent
from utils.finance_agents.momentum_agent import MomentumAgent
from utils.finance_agents.sentiment_agent import SentimentAgent
from utils.finance_agents.risk_agent import RiskAgent
from utils.finance_agents.macro_agent import MacroAgent

__all__ = [
    "AgentDecision",
    "BaseAgent",
    "MacroAgent",
    "MomentumAgent",
    "RiskAgent",
    "SentimentAgent",
    "ValueAgent",
]

__version__ = "1.0.0"
