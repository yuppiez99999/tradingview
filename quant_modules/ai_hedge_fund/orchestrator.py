# -*- coding: utf-8 -*-
"""
AI Hedge Fund 编排器 — LangGraph 工作流 + LangChain Agent 协作

适配量化策略 v5.6 集成，使用本地数据源替代 Financial Datasets API
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger('ai_hedge_fund.orchestrator')

# ── 优雅导入 LangChain/LangGraph 依赖 ──
_LANGGRAPH_AVAILABLE = False
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, StateGraph
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    logger.warning("langgraph/langchain 未安装，AI Hedge Fund 工作流不可用。")
    logger.warning("安装: pip install langgraph langchain langchain-openai python-dotenv")

# ── 导入 Agent 模块 ──
from quant_modules.ai_hedge_fund.utils.analysts import (
    ANALYST_CONFIG,
    get_analyst_nodes,
    get_agents_list,
)
from quant_modules.ai_hedge_fund.agents.risk_manager import risk_management_agent
from quant_modules.ai_hedge_fund.agents.portfolio_manager import portfolio_management_agent
from quant_modules.ai_hedge_fund.graph.state import AgentState, show_agent_reasoning
from quant_modules.ai_hedge_fund.data_adapter import clear_cache


def parse_hedge_fund_response(response) -> Optional[dict]:
    """解析 JSON 响应"""
    try:
        return json.loads(response)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"JSON 解析失败: {e}")
        return None


def start(state: AgentState) -> AgentState:
    """工作流起始节点"""
    return state


def create_workflow(selected_analysts: list[str] = None):
    """创建 LangGraph 分析工作流"""
    if not _LANGGRAPH_AVAILABLE:
        raise RuntimeError("LangGraph 不可用，请安装: pip install langgraph langchain langchain-openai")

    workflow = StateGraph(AgentState)
    workflow.add_node("start_node", start)

    analyst_nodes = get_analyst_nodes()
    if selected_analysts is None:
        selected_analysts = list(analyst_nodes.keys())

    # 并行添加分析师节点
    for analyst_key in selected_analysts:
        if analyst_key in analyst_nodes:
            node_name, node_func = analyst_nodes[analyst_key]
            workflow.add_node(node_name, node_func)
            workflow.add_edge("start_node", node_name)

    # 风控 + 组合管理 + 对冲分析
    workflow.add_node("risk_management_agent", risk_management_agent)
    workflow.add_node("portfolio_manager", portfolio_management_agent)
    
    # 对冲分析师 — 在组合决策之后进行对冲覆盖
    hedge_key = "hedge_analyst"
    hedge_node_name = None
    if hedge_key in analyst_nodes:
        hedge_node_name = analyst_nodes[hedge_key][0]
        workflow.add_node(hedge_node_name, analyst_nodes[hedge_key][1])

    for analyst_key in selected_analysts:
        if analyst_key in analyst_nodes:
            node_name = analyst_nodes[analyst_key][0]
            if analyst_key == hedge_key:
                continue  # 对冲分析师单独连接
            workflow.add_edge(node_name, "risk_management_agent")

    workflow.add_edge("risk_management_agent", "portfolio_manager")
    
    if hedge_node_name:
        # 对冲分析师: 同时接收 分析师信号 + portfolio_manager 输出
        for analyst_key in selected_analysts:
            if analyst_key in analyst_nodes and analyst_key not in (
                hedge_key, "risk_manager", "portfolio_manager"
            ):
                node_name = analyst_nodes[analyst_key][0]
                workflow.add_edge(node_name, hedge_node_name)
        workflow.add_edge("portfolio_manager", hedge_node_name)
        workflow.add_edge(hedge_node_name, END)
    else:
        workflow.add_edge("portfolio_manager", END)
    
    workflow.set_entry_point("start_node")

    return workflow


def run_ai_hedge_fund(
    tickers: list[str],
    start_date: str = None,
    end_date: str = None,
    portfolio: dict = None,
    show_reasoning: bool = False,
    selected_analysts: list[str] = None,
    model_name: str = None,
    model_provider: str = None,
    initial_cash: float = 100_000.0,
    margin_requirement: float = 0.0,
) -> dict:
    """
    运行 AI Hedge Fund 分析

    Args:
        tickers: 股票代码列表，如 ['600036', '000001', '300750']
        start_date: 开始日期 'YYYY-MM-DD'
        end_date: 结束日期 'YYYY-MM-DD'
        portfolio: 现有持仓
        show_reasoning: 是否打印分析详情
        selected_analysts: 选择的分析师 (None=全部)
        model_name: LLM 模型名
        model_provider: LLM 提供商
        initial_cash: 初始现金
        margin_requirement: 保证金要求

    Returns:
        {
            'decisions': {ticker: {action, quantity, confidence, reasoning}},
            'analyst_signals': {agent_id: {ticker: {signal, confidence}}},
            'success': bool,
        }
    """
    if not _LANGGRAPH_AVAILABLE:
        return {
            'success': False,
            'error': 'LangGraph 未安装。请运行: pip install langgraph langchain langchain-openai',
            'decisions': {},
            'analyst_signals': {},
        }

    if not tickers:
        return {'success': False, 'error': '请指定至少一个股票代码', 'decisions': {}, 'analyst_signals': {}}

    # 默认日期: 最近 3 个月
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        from datetime import timedelta
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    # 默认模型配置
    default_model = os.environ.get("AI_HEDGE_MODEL", "gpt-4o-mini")
    default_provider = os.environ.get("AI_HEDGE_PROVIDER", "OpenAI")

    # 构建持仓
    if portfolio is None:
        portfolio = {
            "cash": initial_cash,
            "margin_requirement": margin_requirement,
            "margin_used": 0.0,
            "positions": {
                t: {"long": 0, "short": 0, "long_cost_basis": 0.0, "short_cost_basis": 0.0, "short_margin_used": 0.0}
                for t in tickers
            },
            "realized_gains": {
                t: {"long": 0.0, "short": 0.0} for t in tickers
            },
        }

    # 清缓存确保新鲜数据
    clear_cache()

    logger.info(f"启动 AI Hedge Fund 分析: tickers={tickers}, period={start_date}~{end_date}")
    if selected_analysts:
        logger.info(f"选择分析师: {selected_analysts}")

    try:
        workflow = create_workflow(selected_analysts)
        agent = workflow.compile()

        final_state = agent.invoke({
            "messages": [HumanMessage(content="Make trading decisions based on the provided data.")],
            "data": {
                "tickers": tickers,
                "portfolio": portfolio,
                "start_date": start_date,
                "end_date": end_date,
                "analyst_signals": {},
            },
            "metadata": {
                "show_reasoning": show_reasoning,
                "model_name": model_name or default_model,
                "model_provider": model_provider or default_provider,
            },
        })

        decisions = parse_hedge_fund_response(final_state["messages"][-1].content)
        signals = final_state["data"]["analyst_signals"]
        
        # 提取对冲分析师信号
        hedge_signal = signals.get("hedge_analyst_agent", {})
        
        return {
            'success': True,
            'decisions': decisions or {},
            'analyst_signals': signals,
            'hedge_signal': hedge_signal,
            'tickers': tickers,
            'period': f"{start_date} ~ {end_date}",
        }

    except Exception as e:
        logger.error(f"AI Hedge Fund 分析失败: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'decisions': {},
            'analyst_signals': {},
        }


def get_available_analysts() -> list[dict]:
    """获取可用分析师列表"""
    return get_agents_list()


def print_trading_output(result: dict):
    """打印交易决策输出"""
    if not result.get('success'):
        print(f"\n❌ 分析失败: {result.get('error', '未知错误')}")
        return

    decisions = result.get('decisions', {})
    signals = result.get('analyst_signals', {})
    hedge_signal = result.get('hedge_signal', {})

    print("\n" + "=" * 60)
    print("  🤖 AI Hedge Fund — 多分析师决策报告")
    print("=" * 60)
    print(f"  时间范围: {result.get('period', 'N/A')}")
    print(f"  分析标的: {', '.join(result.get('tickers', []))}")
    print("-" * 60)

    # 打印各分析师信号
    print("\n📊 分析师信号汇总:")
    for agent_id, agent_signals in signals.items():
        if agent_id in ("risk_management_agent",):
            continue
        display_name = agent_id.replace('_agent', '').replace('_', ' ').title()
        print(f"\n  [{display_name}]")
        for ticker, sig in agent_signals.items():
            signal = sig.get('signal', '?')
            conf = sig.get('confidence', 0)
            emoji = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '🟡'}.get(signal, '⚪')
            print(f"    {emoji} {ticker}: {signal.upper()} (信心: {conf}%)")

    # 打印最终决策
    print("\n🎯 最终交易决策:")
    if not decisions:
        print("  无决策")
    else:
        for ticker, decision in decisions.items():
            if isinstance(decision, dict):
                action = decision.get('action', 'hold')
                qty = decision.get('quantity', 0)
                conf = decision.get('confidence', 0)
                reason = decision.get('reasoning', '')
                emoji = {'buy': '📈', 'sell': '📉', 'short': '🔻', 'cover': '📤', 'hold': '⏸️'}.get(action, '❓')
                print(f"  {emoji} {ticker}: {action.upper()} x{qty} (信心:{conf}%) — {reason[:80]}")

    # 打印对冲建议
    if hedge_signal and hedge_signal.get("hedge_ratio", 0) > 0:
        print("\n🛡️ 对冲策略建议 (Taleb+Burry+Druckenmiller):")
        print("-" * 60)
        print(f"  对冲决策: {hedge_signal.get('signal', 'neutral')}")
        print(f"  对冲比率: {hedge_signal.get('hedge_ratio', 0)*100:.0f}%")
        print(f"  紧急程度: {hedge_signal.get('urgency_score', 0)*100:.0f}%")
        reasoning = hedge_signal.get('reasoning', '')
        if reasoning:
            try:
                import json
                r = json.loads(reasoning)
                if 'hedge_recommendation' in r:
                    rec = r['hedge_recommendation']
                    print(f"  推荐工具: {rec.get('preferred_instrument', 'N/A')}")
                    if rec.get('futures_contracts'):
                        print(f"  期货合约: {rec['futures_contracts']}")
                    if rec.get('options_strategy') not in (None, 'NONE'):
                        print(f"  期权策略: {rec.get('options_strategy', '')} x {rec.get('options_contracts', 0)}张")
                    print(f"  执行时机: {rec.get('execution_timing', '')}")
                if 'risk_warnings' in r:
                    for w in r['risk_warnings']:
                        print(f"  ⚠️ {w}")
            except (json.JSONDecodeError, KeyError):
                if len(reasoning) > 120:
                    reasoning = reasoning[:120] + "..."
                print(f"  详情: {reasoning}")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# CLI 直接入口 (独立测试用)
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Hedge Fund — 多分析师决策系统")
    parser.add_argument('--ticker', '-t', nargs='+', required=True, help='股票代码')
    parser.add_argument('--analysts', '-a', nargs='*', help='选择分析师 (默认全部)')
    parser.add_argument('--start-date', default=None, help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end-date', default=None, help='结束日期 YYYY-MM-DD')
    parser.add_argument('--show-reasoning', action='store_true', help='显示分析详情')
    parser.add_argument('--model', default=None, help='LLM 模型名')
    parser.add_argument('--provider', default=None, help='LLM 提供商')
    parser.add_argument('--list-analysts', action='store_true', help='列出可用分析师')

    args = parser.parse_args()

    if args.list_analysts:
        analysts = get_available_analysts()
        for a in analysts:
            print(f"  [{a['key']}] {a['display_name']} — {a['description']}")
        sys.exit(0)

    result = run_ai_hedge_fund(
        tickers=args.ticker,
        start_date=args.start_date,
        end_date=args.end_date,
        selected_analysts=args.analysts,
        show_reasoning=args.show_reasoning,
        model_name=args.model,
        model_provider=args.provider,
    )
    print_trading_output(result)
