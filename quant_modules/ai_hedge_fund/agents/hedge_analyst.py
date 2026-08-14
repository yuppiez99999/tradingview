# -*- coding: utf-8 -*-
"""
AI Hedge Fund — 对冲分析师 Agent (v5.8)

集成 Taleb (尾部风险) + Burry (做空信号) + Druckenmiller (宏观)
三位一体对冲决策框架。

核心职责:
1. 评估组合是否需要系统性对冲
2. 推荐对冲工具 (股指期货/ETF期权)
3. 确定对冲比例和策略
4. 输出结构化对冲信号供信号融合引擎使用

工作流程:
  组合数据 → 风险指标计算 → Taleb尾部风险分析 → 
  Burry做空信号验证 → Druckenmiller宏观确认 → 
  综合对冲建议 → 输出结构化JSON
"""

import json
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger('ai_hedge_fund.hedge_analyst')


class HedgeAnalystSignal(BaseModel):
    overall_hedge_decision: str = Field(description="NO_HEDGE|LIGHT_HEDGE|MODERATE_HEDGE|STRONG_HEDGE|FULL_HEDGE")
    hedge_urgency_score: float = Field(description="0-1, 越高越紧急")
    taleb_tail_risk_score: float = Field(description="0-1 尾部风险评分")
    burry_valuation_risk_score: float = Field(description="0-1 估值风险评分")
    druckenmiller_macro_cycle: str = Field(description="EXPANSION|PEAK|CONTRACTION|RECOVERY")
    preferred_instrument: str = Field(description="IF|IC|IH|IM|510300_PUT|510050_PUT|NONE")
    hedge_ratio: float = Field(description="0-1, 建议对冲比例")
    confidence: int = Field(description="0-100 分析置信度")
    reasoning: str = Field(description="分析推理过程")


# ── 对冲分析师 System Prompt ──

HEDGE_ANALYST_PROMPT = """你是一位全球顶尖的对冲基金经理，融合了三位大师的思维框架:

1. **Nassim Taleb** — 尾部风险专家: 关注黑天鹅事件、反脆弱性、凸性收益、杠铃策略。核心问题: "这个组合在极端市场环境下会怎样？"
2. **Michael Burry** — 大空头: 关注市场估值泡沫、系统性风险、非对称做空机会。核心问题: "市场是否被高估？是否存在做空机会？"
3. **Stanley Druckenmiller** — 宏观投资者: 关注宏观经济周期、央行政策、资金流向、跨资产联动。核心问题: "宏观环境是否支持当前持仓？"

## 分析框架

你必须从以下三个维度分别分析，然后给出综合结论:

### 1. Taleb 维度 — 尾部风险与反脆弱性
- 组合是否存在尾部风险？最大回撤场景估计？
- 持仓是否具有凸性(下跌有限、上涨无限)？
- 用杠铃策略评估: 90%极安全资产 + 10%极高风险资产？
- 是否存在"火鸡问题"(被过去平静麻痹)？
- 推荐行动: 是否需要"买入保险"？

### 2. Burry 维度 — 估值与做空信号
- 持仓板块估值是否过高？
- 是否存在市场泡沫特征(杠杆率高、散户狂热等)？
- 哪些标的/板块可能存在做空机会？
- 系统性风险评分(1-10)？
- 推荐行动: 是否需要减持/做空/对冲？

### 3. Druckenmiller 维度 — 宏观环境
- 当前宏观经济周期阶段？(扩张/顶峰/收缩/复苏)
- 央行货币政策方向？(宽松/紧缩/中性)
- 资金流向: 流入权益还是流出？
- 跨资产信号: 债券/商品/汇率给出什么信息？
- 推荐行动: 是否需要调整仓位方向？

## 输出格式

必须输出严格的JSON，包含以下字段:

overall_hedge_decision: "NO_HEDGE|LIGHT_HEDGE|MODERATE_HEDGE|STRONG_HEDGE|FULL_HEDGE"
hedge_urgency_score: 0.0-1.0
taleb_tail_risk_score: 0.0-1.0
burry_valuation_risk_score: 0.0-1.0
druckenmiller_macro_cycle: "EXPANSION|PEAK|CONTRACTION|RECOVERY"
preferred_instrument: "IF|IC|IH|IM|510300_PUT|510050_PUT|NONE"
hedge_ratio: 0.0-1.0
confidence: 0-100
reasoning: "分析推理过程"

## 输入数据

你会收到以下数据:
- 组合持仓和权重
- 实时价格数据
- 市场指数数据
- 可选: 基本面/宏观数据

请基于这些数据做出专业判断。如果你的判断缺乏足够数据支持，请在confidence中体现。"""


def hedge_analyst_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    对冲分析师 Agent — 三位一体对冲决策
    
    Args:
        state: LangGraph AgentState, 包含:
            - state["data"]["tickers"]: 分析标的列表
            - state["data"]["portfolio"]: 组合持仓
            - state["data"]["analyst_signals"]: 其他分析师的信号
            - state["data"]["market_data"]: 市场数据 (可选)
            - state["metadata"]["model_name"]: LLM模型名
            - state["metadata"]["model_provider"]: LLM提供商
            
    Returns:
        更新的 state, 包含 hedge_analyst_agent 的分析结果
    """
    from quant_modules.ai_hedge_fund.graph.state import show_agent_reasoning
    from langchain_core.messages import HumanMessage
    
    # 提取数据
    data = state.get("data", {})
    metadata = state.get("metadata", {})
    tickers = data.get("tickers", [])
    portfolio = data.get("portfolio", {})
    analyst_signals = data.get("analyst_signals", {})
    market_data = data.get("market_data", {})
    
    # 收集关键信号用于分析
    taleb_signal = analyst_signals.get("nassim_taleb_agent", {})
    burry_signal = analyst_signals.get("michael_burry_agent", {})
    druckenmiller_signal = analyst_signals.get("stanley_druckenmiller_agent", {})
    risk_mgr_signal = analyst_signals.get("risk_management_agent", {})
    
    # 构建分析上下文
    context = _build_hedge_context(tickers, portfolio, market_data, taleb_signal, burry_signal, druckenmiller_signal)
    
    # 构建消息
    message_content = f"""{HEDGE_ANALYST_PROMPT}

## 当前分析上下文

{context}

请基于以上数据，从 Taleb/Burry/Druckenmiller 三个维度进行对冲分析，输出JSON。
"""
    
    # 调用LLM
    try:
        from quant_modules.ai_hedge_fund.utils.llm import call_llm
        
        agent_id = "hedge_analyst_agent"
        
        response = call_llm(
            prompt=message_content,
            pydantic_model=HedgeAnalystSignal,
            agent_name=agent_id,
            state=state,
            default_factory=_create_default_hedge_signal,
        )
        
        # 显示推理过程
        if metadata.get("show_reasoning"):
            show_agent_reasoning(response, "Hedge Analyst (Taleb+Burry+Druckenmiller)")
        
        # 保存到状态
        analyst_signals["hedge_analyst_agent"] = {
            "ticker": "PORTFOLIO",
            "signal": _map_hedge_to_signal(response.overall_hedge_decision),
            "confidence": response.confidence,
            "hedge_ratio": response.hedge_ratio,
            "urgency_score": response.hedge_urgency_score,
            "reasoning": response.reasoning,
        }
        
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError) as e:
        logger.warning(f"Hedge Analyst LLM调用失败: {e}，使用规则引擎回退")
        analyst_signals["hedge_analyst_agent"] = _rule_based_hedge_fallback(analyst_signals)

    return {
        "messages": state.get("messages", []),
        "data": {**data, "analyst_signals": analyst_signals},
        "metadata": metadata,
    }


def _build_hedge_context(
    tickers: list,
    portfolio: dict,
    market_data: dict,
    taleb_signal: dict,
    burry_signal: dict,
    druckenmiller_signal: dict,
) -> str:
    """构建对冲分析上下文"""
    lines = []
    
    lines.append(f"### 组合概况")
    lines.append(f"分析标的: {', '.join(tickers) if tickers else 'N/A'}")
    lines.append(f"组合现金: {portfolio.get('cash', 'N/A')}")
    lines.append(f"持仓市值: {portfolio.get('positions', {})}")
    
    # Taleb信号摘要
    if taleb_signal:
        lines.append(f"\n### Taleb 尾部风险分析 (已有)")
        for ticker, sig in taleb_signal.items():
            if isinstance(sig, dict):
                lines.append(f"  {ticker}: {sig.get('signal', '?')} (confidence={sig.get('confidence', 0)})")
    
    # Burry信号摘要
    if burry_signal:
        lines.append(f"\n### Burry 做空/逆势分析 (已有)")
        for ticker, sig in burry_signal.items():
            if isinstance(sig, dict):
                lines.append(f"  {ticker}: {sig.get('signal', '?')} (confidence={sig.get('confidence', 0)})")
    
    # Druckenmiller信号摘要
    if druckenmiller_signal:
        lines.append(f"\n### Druckenmiller 宏观分析 (已有)")
        for ticker, sig in druckenmiller_signal.items():
            if isinstance(sig, dict):
                lines.append(f"  {ticker}: {sig.get('signal', '?')} (confidence={sig.get('confidence', 0)})")
    
    # 市场数据
    if market_data:
        lines.append(f"\n### 市场数据")
        for k, v in market_data.items():
            if isinstance(v, (int, float, str)):
                lines.append(f"  {k}: {v}")
    
    return "\n".join(lines)


def _map_hedge_to_signal(hedge_decision: str) -> str:
    """将对冲决策映射为交易信号"""
    mapping = {
        "NO_HEDGE": "neutral",
        "LIGHT_HEDGE": "neutral",
        "MODERATE_HEDGE": "bearish",
        "STRONG_HEDGE": "bearish",
        "FULL_HEDGE": "bearish",
    }
    return mapping.get(hedge_decision, "neutral")


def _create_default_hedge_signal() -> HedgeAnalystSignal:
    """创建默认对冲信号（LLM调用失败时使用）"""
    return HedgeAnalystSignal(
        overall_hedge_decision="MODERATE_HEDGE",
        hedge_urgency_score=0.5,
        taleb_tail_risk_score=0.4,
        burry_valuation_risk_score=0.4,
        druckenmiller_macro_cycle="PEAK",
        preferred_instrument="IC",
        hedge_ratio=0.5,
        confidence=40,
        reasoning="LLM调用失败，使用默认值",
    )


def _rule_based_hedge_fallback(analyst_signals: dict) -> dict:
    """当LLM不可用时的规则引擎回退"""
    bearish_count = 0
    bullish_count = 0
    taleb_concern = 0
    burry_concern = 0
    
    for agent_key, signals in analyst_signals.items():
        if agent_key == "nassim_taleb_agent":
            for ticker, sig in signals.items():
                if isinstance(sig, dict) and sig.get('signal') == 'bearish':
                    taleb_concern += 1
        elif agent_key == "michael_burry_agent":
            for ticker, sig in signals.items():
                if isinstance(sig, dict) and sig.get('signal') == 'bearish':
                    burry_concern += 1
                    bearish_count += 1
        elif agent_key in ("stanley_druckenmiller_agent", "warren_buffett_agent"):
            for ticker, sig in signals.items():
                if isinstance(sig, dict):
                    if sig.get('signal') == 'bearish':
                        bearish_count += 1
                    elif sig.get('signal') == 'bullish':
                        bullish_count += 1
    
    # 简单规则: Taleb+Burry都悲观 → 对冲
    tail_concerning = taleb_concern >= 1
    burry_concerning = burry_concern >= 1
    overall_bearish = bearish_count >= bullish_count + 1
    
    if tail_concerning and burry_concerning:
        hedge_decision = "STRONG_HEDGE"
        urgency = 0.8
        ratio = 0.75
    elif tail_concerning or (burry_concerning and overall_bearish):
        hedge_decision = "MODERATE_HEDGE"
        urgency = 0.55
        ratio = 0.50
    elif overall_bearish:
        hedge_decision = "LIGHT_HEDGE"
        urgency = 0.35
        ratio = 0.25
    else:
        hedge_decision = "NO_HEDGE"
        urgency = 0.1
        ratio = 0.0
    
    return {
        "ticker": "PORTFOLIO",
        "signal": _map_hedge_to_signal(hedge_decision),
        "confidence": 60,
        "hedge_ratio": ratio,
        "urgency_score": urgency,
        "reasoning": json.dumps({
            "overall_hedge_decision": hedge_decision,
            "hedge_urgency_score": urgency,
            "taleb_concern_signals": taleb_concern,
            "burry_concern_signals": burry_concern,
            "bearish_vs_bullish": f"{bearish_count} vs {bullish_count}",
            "hedge_recommendation": {"hedge_ratio": ratio},
            "note": "规则引擎回退 — LLM不可用时的保守估计",
        }, ensure_ascii=False),
    }
