# -*- coding: utf-8 -*-
"""
ai_decision.rag_context — 实时 RAG 上下文构建
=============================================

扩展 ModelRouter._build_rag_context 的思路, 接入实时行情/新闻/研报/宏观,
做时效/来源/相关性三层过滤后, 构建注入给 AI 辩论/聚合的标准化文本上下文.

设计原则:
  - 与现有 FinanceAgentOrchestrator / ModelRouter 解耦, 仅消费其 dict 输出
  - 三层过滤: 时效性 (as_of 新鲜度) / 来源 (可信度分级) / 相关性 (与 symbol 匹配)
  - 若上游无可用的真实数据, 回退到 Mock 生成的占位上下文, 保证全链路可跑
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ai_decision.models import DecisionContext

logger = logging.getLogger("ai_decision.rag_context")

# 来源可信度分级 (越高越优先保留)
_SOURCE_TIER: Dict[str, int] = {
    "official": 3,     # 交易所/公司公告/监管
    "broker": 2,       # 券商研报
    "news": 1,         # 财经新闻
    "social": 0,       # 社交/论坛
}

# 相关性时间窗 (默认 7 天以内的新闻/研报视为相关)
_RELEVANCE_WINDOW_DAYS = 7


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _relevance_filter(items: List[Dict[str, Any]], symbol: str,
                      now: datetime) -> List[str]:
    """相关性 + 时效性 + 来源三层过滤, 返回保留的新闻/研报摘要文本"""
    kept: List[str] = []
    scored: List[tuple] = []
    for it in items:
        text = str(it.get("text") or it.get("title") or it.get("content") or "")
        if not text.strip():
            continue
        # 相关性: 必须提及 symbol 或为空 symbol (占位全部保留)
        if symbol and symbol not in text and it.get("symbol") not in (None, symbol):
            continue
        # 时效性
        dt = _parse_dt(it.get("time") or it.get("as_of") or it.get("date"))
        fresh = True
        if dt is not None:
            fresh = (now - dt) <= timedelta(days=_RELEVANCE_WINDOW_DAYS)
        # 来源分级
        tier = _SOURCE_TIER.get(str(it.get("source_type", "news")).lower(), 1)
        if not fresh and tier < 2:
            continue  # 过期且低来源直接丢弃
        scored.append((tier, fresh, text[:300]))
    # 按来源分级降序, 时效优先, 最多保留 8 条
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for _, _, text in scored[:8]:
        kept.append(text)
    return kept


def build_context(symbol: str,
                  market_data: Optional[Dict[str, Any]] = None,
                  fundamentals: Optional[Dict[str, Any]] = None,
                  news: Optional[List[Dict[str, Any]]] = None,
                  macro: Optional[Dict[str, Any]] = None,
                  agent_decisions: Optional[List[Dict[str, Any]]] = None,
                  agent_consensus: Optional[Dict[str, Any]] = None) -> DecisionContext:
    """构建决策上下文

    各上游数据均可为空 (Mock 场景), 缺失项以占位填充, 保证全链路可跑.
    """
    now = datetime.now()
    market_data = market_data or {}
    fundamentals = fundamentals or {}
    news = news or []
    macro = macro or {}

    news_items = _relevance_filter(news, symbol, now)
    if not news_items:
        news_items = ["[Mock] 暂无实时新闻事件, 使用规则兜底上下文."]

    ctx = DecisionContext(
        symbol=symbol,
        market_data=market_data,
        fundamentals=fundamentals,
        news_items=news_items,
        macro_data=macro,
        agent_decisions=agent_decisions or [],
        agent_consensus=agent_consensus or {},
        as_of=now.isoformat(),
    )
    return ctx


def context_to_prompt(ctx: DecisionContext) -> str:
    """将 DecisionContext 序列化为注入 prompt 的结构化文本"""
    lines: List[str] = []
    lines.append(f"# 决策上下文 — {ctx.symbol} (as_of: {ctx.as_of})")
    md = ctx.market_data
    if md:
        lines.append("## 实时行情")
        for k in ("close", "change_pct", "volume", "turnover", "high", "low"):
            if k in md:
                lines.append(f"- {k}: {md[k]}")
    fd = ctx.fundamentals
    if fd:
        lines.append("## 基本面")
        for k in ("pe", "pb", "roe", "eps", "market_cap"):
            if k in fd:
                lines.append(f"- {k}: {fd[k]}")
    mc = ctx.macro_data
    if mc:
        lines.append("## 宏观")
        for k, v in mc.items():
            lines.append(f"- {k}: {v}")
    if ctx.news_items:
        lines.append("## 相关新闻/事件")
        for n in ctx.news_items:
            lines.append(f"- {n}")
    ac = ctx.agent_consensus
    if ac:
        lines.append("## 五 Agent 加权共识")
        lines.append(f"- action: {ac.get('action')}  strength: {ac.get('strength')}  "
                     f"confidence: {ac.get('confidence')}")
    return "\n".join(lines)
