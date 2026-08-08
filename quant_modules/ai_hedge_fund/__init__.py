# -*- coding: utf-8 -*-
"""
AI Hedge Fund 模块 — 集成到量化策略 v5.6+
19 个 AI 分析师 Agent + LangGraph 工作流编排

使用方式:
    from quant_modules.ai_hedge_fund.orchestrator import run_ai_hedge_fund
    result = run_ai_hedge_fund(tickers=['600036', '000001'])

注意: 本模块需要 langchain/langgraph 依赖，按需安装:
    pip install langgraph langchain langchain-openai python-dotenv
"""

__all__ = [
    'ANALYST_CONFIG',
    'get_analyst_nodes',
    'get_agents_list',
    'run_ai_hedge_fund',
    'is_available',
]


def is_available() -> bool:
    """检查 AI Hedge Fund 是否可用 (langchain/langgraph 已安装)"""
    try:
        import langgraph
        import langchain_core
        return True
    except ImportError:
        return False


def get_analyst_nodes():
    """获取分析师节点映射 (懒加载)"""
    from quant_modules.ai_hedge_fund.utils.analysts import get_analyst_nodes as _get
    return _get()


def get_agents_list():
    """获取分析师列表 (懒加载)"""
    from quant_modules.ai_hedge_fund.utils.analysts import get_agents_list as _get
    return _get()
