"""
AI Hedge Fund 编排器 — LangGraph 工作流 + LangChain Agent 协作

适配量化策略 v5.6 集成，使用本地数据源替代 Financial Datasets API
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

logger = logging.getLogger("ai_hedge_fund.orchestrator")

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
    logger.warning(
        "安装: pip install langgraph langchain langchain-openai python-dotenv"
    )

# ── 导入 Agent 模块 ──
from quant_modules.ai_hedge_fund.agents.portfolio_manager import (
    portfolio_management_agent,
)
from quant_modules.ai_hedge_fund.agents.risk_manager import risk_management_agent
from quant_modules.ai_hedge_fund.data_adapter import clear_cache
from quant_modules.ai_hedge_fund.debate_layer import (
    debate_node,
)  # Wave 6 W6.2.1 多空辩论层
from quant_modules.ai_hedge_fund.graph.state import AgentState
from quant_modules.ai_hedge_fund.risk_debate_layer import (
    risk_debate_node,
)  # v8.6+ 3方风控辩论
from quant_modules.ai_hedge_fund.utils.analysts import (
    get_agents_list,
    get_analyst_nodes,
)


def parse_hedge_fund_response(response) -> dict | None:
    """解析 JSON 响应"""
    try:
        return json.loads(response)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"JSON 解析失败: {e}")
        return None


def start(state: AgentState) -> AgentState:
    """工作流起始节点"""
    return state


def create_workflow(selected_analysts: list[str] | None = None):
    """创建 LangGraph 分析工作流"""
    if not _LANGGRAPH_AVAILABLE:
        raise RuntimeError(
            "LangGraph 不可用，请安装: pip install langgraph langchain langchain-openai"
        )

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

    # Wave 6 W6.2.1: 多空辩论层 (在分析师之后、风控之前)
    workflow.add_node("debate_layer", debate_node)

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
            # 分析师 → 辩论层 (Wave 6: 原来直接连 risk_management, 现在中间插入 debate_layer)
            workflow.add_edge(node_name, "debate_layer")

    # 辩论层 → 风控 → [风控辩论] → 组合管理
    workflow.add_edge("debate_layer", "risk_management_agent")

    _risk_debate_disabled = os.environ.get(
        "AI_HEDGE_RISK_DEBATE_DISABLED", ""
    ).lower() in ("1", "true", "yes")
    if not _risk_debate_disabled:
        workflow.add_node("risk_debate", risk_debate_node)
        workflow.add_edge("risk_management_agent", "risk_debate")
        workflow.add_edge("risk_debate", "portfolio_manager")
    else:
        workflow.add_edge("risk_management_agent", "portfolio_manager")

    if hedge_node_name:
        # 对冲分析师: 同时接收 分析师信号 + portfolio_manager 输出
        for analyst_key in selected_analysts:
            if analyst_key in analyst_nodes and analyst_key not in (
                hedge_key,
                "risk_manager",
                "portfolio_manager",
            ):
                node_name = analyst_nodes[analyst_key][0]
                workflow.add_edge(node_name, hedge_node_name)
        workflow.add_edge("portfolio_manager", hedge_node_name)
        workflow.add_edge(hedge_node_name, END)
    else:
        workflow.add_edge("portfolio_manager", END)

    workflow.set_entry_point("start_node")

    return workflow


def _build_historical_lessons_block(tickers: list[str]) -> str:
    """W.B.2 团队级共享记忆: 构建历史教训文本块 (feature-flag 控制)

    在 run_ai_hedge_fund 构建 state 时调用, 注入 state["data"]["historical_lessons"].
    flag 关闭或异常时返回空字符串 (零侵入).
    """
    try:
        from utils.ai_memory.team_memory_hub import get_team_memory_hub

        hub = get_team_memory_hub()
        return hub.build_lessons_prompt_block(tickers)
    except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
        logger.debug("build_historical_lessons_block 失败: %s", e)
        return ""


def run_ai_hedge_fund(
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    portfolio: dict | None = None,
    show_reasoning: bool = False,
    selected_analysts: list[str] | None = None,
    model_name: str | None = None,
    model_provider: str | None = None,
    initial_cash: float = 100_000.0,
    margin_requirement: float = 0.0,
    checkpoint_enabled: bool = False,
    checkpoint_dir: str | None = None,
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
            "success": False,
            "error": "LangGraph 未安装。请运行: pip install langgraph langchain langchain-openai",
            "decisions": {},
            "analyst_signals": {},
        }

    if not tickers:
        return {
            "success": False,
            "error": "请指定至少一个股票代码",
            "decisions": {},
            "analyst_signals": {},
        }

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
                t: {
                    "long": 0,
                    "short": 0,
                    "long_cost_basis": 0.0,
                    "short_cost_basis": 0.0,
                    "short_margin_used": 0.0,
                }
                for t in tickers
            },
            "realized_gains": {t: {"long": 0.0, "short": 0.0} for t in tickers},
        }

    # 清缓存确保新鲜数据
    clear_cache()

    logger.info(
        f"启动 AI Hedge Fund 分析: tickers={tickers}, period={start_date}~{end_date}"
    )
    if selected_analysts:
        logger.info(f"选择分析师: {selected_analysts}")

    try:
        workflow = create_workflow(selected_analysts)

        # ── checkpoint: 崩溃恢复 (v8.6+ 融合 TradingAgents) ──
        _checkpointer = None
        _invoke_config = None
        if checkpoint_enabled:
            try:
                from quant_modules.ai_hedge_fund.graph import (
                    get_checkpointer,
                    thread_id,
                )

                _cp_dir = checkpoint_dir or os.path.join(
                    os.path.expanduser("~"), ".ai_hedge_fund"
                )
                _portfolio_key = "portfolio_" + "_".join(tickers[:3])
                _tid = thread_id(
                    _portfolio_key, end_date or datetime.now().strftime("%Y-%m-%d")
                )
                _checkpointer_ctx = get_checkpointer(_cp_dir, _portfolio_key)
                _checkpointer = _checkpointer_ctx.__enter__()
                _invoke_config = {"configurable": {"thread_id": _tid}}
                logger.info(f"checkpoint 已启用: dir={_cp_dir}, thread_id={_tid}")
            except Exception as _e:
                logger.warning(f"checkpoint 启用失败, 回退无 checkpoint 模式: {_e}")
                _checkpointer = None
                _invoke_config = None

        agent = (
            workflow.compile(checkpointer=_checkpointer)
            if _checkpointer
            else workflow.compile()
        )

        # W.B.2 团队级共享记忆: 注入历史教训到 state (feature-flag 控制)
        historical_lessons = _build_historical_lessons_block(tickers)

        final_state = (
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content="Make trading decisions based on the provided data."
                        )
                    ],
                    "data": {
                        "tickers": tickers,
                        "portfolio": portfolio,
                        "start_date": start_date,
                        "end_date": end_date,
                        "analyst_signals": {},
                        "historical_lessons": historical_lessons,
                    },
                    "metadata": {
                        "show_reasoning": show_reasoning,
                        "model_name": model_name or default_model,
                        "model_provider": model_provider or default_provider,
                    },
                },
                config=_invoke_config,
            )
            if _invoke_config
            else agent.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content="Make trading decisions based on the provided data."
                        )
                    ],
                    "data": {
                        "tickers": tickers,
                        "portfolio": portfolio,
                        "start_date": start_date,
                        "end_date": end_date,
                        "analyst_signals": {},
                        "historical_lessons": historical_lessons,
                    },
                    "metadata": {
                        "show_reasoning": show_reasoning,
                        "model_name": model_name or default_model,
                        "model_provider": model_provider or default_provider,
                    },
                }
            )
        )

        decisions = parse_hedge_fund_response(final_state["messages"][-1].content)
        signals = final_state["data"]["analyst_signals"]

        # 提取对冲分析师信号
        hedge_signal = signals.get("hedge_analyst_agent", {})

        # ── 决策日志 (v8.6+ 融合 TradingAgents TradingMemoryLog) ──
        try:
            from quant_modules.ai_hedge_fund.utils.trading_memory import (
                TradingMemoryLog,
            )

            _log_path = os.environ.get("AI_HEDGE_MEMORY_LOG_PATH") or os.path.join(
                os.path.expanduser("~"), ".ai_hedge_fund", "memory", "trading_memory.md"
            )
            _mem = TradingMemoryLog({"memory_log_path": _log_path})
            _trade_date = end_date or datetime.now().strftime("%Y-%m-%d")
            for _tk in tickers:
                _dec = decisions.get(_tk) if decisions else None
                if _dec:
                    _dec_text = (
                        json.dumps(_dec, ensure_ascii=False)
                        if isinstance(_dec, dict)
                        else str(_dec)
                    )
                    _mem.store_decision(_tk, _trade_date, _dec_text)
        except Exception as _e:
            logger.debug("决策日志记录失败: %s", _e)

        return {
            "success": True,
            "decisions": decisions or {},
            "analyst_signals": signals,
            "hedge_signal": hedge_signal,
            "tickers": tickers,
            "period": f"{start_date} ~ {end_date}",
        }

    except Exception as e:
        logger.error(f"AI Hedge Fund 分析失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "decisions": {},
            "analyst_signals": {},
        }
    finally:
        _ctx = locals().get("_checkpointer_ctx")
        if _ctx is not None:
            try:
                _ctx.__exit__(None, None, None)
            except Exception:
                pass


def get_available_analysts() -> list[dict]:
    """获取可用分析师列表"""
    return get_agents_list()


def print_trading_output(result: dict):
    """打印交易决策输出"""
    if not result.get("success"):
        return

    decisions = result.get("decisions", {})
    signals = result.get("analyst_signals", {})
    hedge_signal = result.get("hedge_signal", {})

    # 打印各分析师信号
    for agent_id, agent_signals in signals.items():
        if agent_id in ("risk_management_agent",):
            continue
        agent_id.replace("_agent", "").replace("_", " ").title()
        for _ticker, sig in agent_signals.items():
            signal = sig.get("signal", "?")
            sig.get("confidence", 0)
            {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(signal, "⚪")

    # 打印最终决策
    if not decisions:
        pass
    else:
        for _ticker, decision in decisions.items():
            if isinstance(decision, dict):
                action = decision.get("action", "hold")
                decision.get("quantity", 0)
                decision.get("confidence", 0)
                decision.get("reasoning", "")
                {
                    "buy": "📈",
                    "sell": "📉",
                    "short": "🔻",
                    "cover": "📤",
                    "hold": "⏸️",
                }.get(action, "❓")

    # 打印对冲建议
    if hedge_signal and hedge_signal.get("hedge_ratio", 0) > 0:
        reasoning = hedge_signal.get("reasoning", "")
        if reasoning:
            try:
                import json

                r = json.loads(reasoning)
                if "hedge_recommendation" in r:
                    rec = r["hedge_recommendation"]
                    if rec.get("futures_contracts"):
                        pass
                    if rec.get("options_strategy") not in (None, "NONE"):
                        pass
                if "risk_warnings" in r:
                    for _w in r["risk_warnings"]:
                        pass
            except (json.JSONDecodeError, KeyError):
                if len(reasoning) > 120:
                    reasoning = reasoning[:120] + "..."


# ═══════════════════════════════════════════════════════════════
# CLI 直接入口 (独立测试用)
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Hedge Fund — 多分析师决策系统")
    parser.add_argument("--ticker", "-t", nargs="+", required=True, help="股票代码")
    parser.add_argument("--analysts", "-a", nargs="*", help="选择分析师 (默认全部)")
    parser.add_argument("--start-date", default=None, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--show-reasoning", action="store_true", help="显示分析详情")
    parser.add_argument("--model", default=None, help="LLM 模型名")
    parser.add_argument("--provider", default=None, help="LLM 提供商")
    parser.add_argument("--list-analysts", action="store_true", help="列出可用分析师")

    args = parser.parse_args()

    if args.list_analysts:
        analysts = get_available_analysts()
        for _a in analysts:
            pass
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
