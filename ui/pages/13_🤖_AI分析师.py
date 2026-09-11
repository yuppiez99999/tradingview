"""AI Hedge Fund — 19位大师级AI分析师联合决策面板"""

import os
import sys

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI分析师", page_icon="🤖", layout="wide")
st.title("🤖 AI Hedge Fund — 大师级AI分析师")
st.caption("19位顶级投资大师风格AI分析师 + 风控 + 组合管理 → 联合交易决策")

from ui.components.common import inject_global_style
from ui.components.system_status import render_alert_card

inject_global_style()

# ── 检查依赖 ──
try:
    from quant_modules.ai_hedge_fund.data_adapter import get_data_source_status
    from quant_modules.ai_hedge_fund.orchestrator import (
        get_available_analysts,
        print_trading_output,  # noqa: F401
        run_ai_hedge_fund,
    )

    _AVAILABLE = True
except ImportError as e:
    render_alert_card(
        "模块加载失败", f"❌ AI Hedge Fund 模块加载失败: {e}", level="error"
    )
    render_alert_card(
        "安装提示",
        "请确保已安装: pip install langgraph langchain langchain-openai python-dotenv pandas numpy",
        level="info",
    )
    st.stop()
    _AVAILABLE = False

# ── 侧边栏配置 ──
with st.sidebar:
    st.header("⚙️ 分析配置")

    # LLM 设置
    st.subheader("🔑 LLM 设置")
    provider = st.selectbox(
        "LLM 提供商",
        ["OpenAI", "DeepSeek", "Anthropic", "Groq", "Moonshot"],
        index=0,
        help="需要设置对应的 API Key 环境变量",
    )
    model_map = {
        "OpenAI": "gpt-4o-mini",
        "DeepSeek": "deepseek-chat",
        "Anthropic": "claude-sonnet-4-20250514",
        "Groq": "llama-3.1-70b-versatile",
        "Moonshot": "moonshot-v1-8k",
    }
    model = st.text_input("模型名", value=model_map.get(provider, "gpt-4o-mini"))

    # 日期范围
    st.subheader("📅 分析时间范围")
    end_date = st.date_input("结束日期", value=now_bj().date())
    start_date = st.date_input(
        "开始日期", value=now_bj().date() - timedelta(days=90)
    )

    # 显示推理过程
    show_reasoning = st.checkbox("显示详细推理", value=False)

    # 数据源状态
    st.subheader("📡 数据源状态")
    try:
        ds_status = get_data_source_status()
        for name, ok in ds_status.items():
            st.caption(f"{'✅' if ok else '❌'} {name}")
    except Exception:
        st.caption("⚠️ 无法检查数据源状态")

# ── 主区域 ──
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📊 选择标的")
    ticker_input = st.text_input(
        "股票代码 (逗号/空格分隔)",
        value="600036, 000001, 300750, 600519",
        help="支持 A 股代码格式: 600036, 000001 等",
        placeholder="600036, 000001, 300750",
    )
    tickers = [t.strip() for t in ticker_input.replace(",", " ").split() if t.strip()]

with col2:
    st.subheader("👥 选择分析师")

    try:
        all_analysts = get_available_analysts()
        analyst_options = {
            a["key"]: f"{a['display_name']} — {a['description']}" for a in all_analysts
        }
        # 默认选择核心分析师
        default_keys = [
            "warren_buffett",
            "ben_graham",
            "charlie_munger",
            "peter_lynch",
            "cathie_wood",
            "michael_burry",
            "fundamentals_analyst",
            "technical_analyst",
            "valuation_analyst",
            "stanley_druckenmiller",
        ]
        default_selected = [k for k in default_keys if k in analyst_options]

        st.caption(f"共 {len(all_analysts)} 位分析师可选")

        # 快速选择
        quick_select = st.radio(
            "快速选择",
            [
                "核心团队 (10位)",
                "价值投资派 (5位)",
                "混合派 (8位)",
                "全部 (19位)",
                "自定义",
            ],
            index=0,
            horizontal=False,
        )

        if quick_select == "核心团队 (10位)":
            selected_analysts = default_selected
        elif quick_select == "价值投资派 (5位)":
            selected_analysts = [
                "warren_buffett",
                "ben_graham",
                "charlie_munger",
                "mohnish_pabrai",
                "phil_fisher",
            ]
        elif quick_select == "混合派 (8位)":
            selected_analysts = [
                "warren_buffett",
                "ben_graham",
                "cathie_wood",
                "michael_burry",
                "stanley_druckenmiller",
                "nassim_taleb",
                "fundamentals_analyst",
                "technical_analyst",
            ]
        elif quick_select == "全部 (19位)":
            selected_analysts = list(analyst_options.keys())
        else:
            selected_analysts = st.multiselect(
                "选择分析师",
                options=list(analyst_options.keys()),
                default=default_selected,
                format_func=lambda k: analyst_options[k].split(" — ")[0],
            )

    except Exception as e:
        render_alert_card("获取失败", f"获取分析师列表失败: {e}", level="error")
        selected_analysts = None

# ── 显示选中的分析师 ──
if selected_analysts:
    with st.expander(
        f"📋 已选择 {len(selected_analysts)} 位分析师 (点击查看详情)", expanded=False
    ):
        for key in selected_analysts:
            info = analyst_options.get(key, key)
            st.caption(f"• {info}")

# ── 运行按钮 ──
st.markdown("---")
run_col1, run_col2, run_col3 = st.columns([2, 1, 2])

with run_col2:
    run_btn = st.button("🚀 启动 AI 分析", type="primary", use_container_width=True)

if run_btn:
    if not tickers:
        render_alert_card("输入缺失", "请至少输入一个股票代码", level="warning")
        st.stop()

    render_alert_card(
        "分析中",
        f"正在分析 {len(tickers)} 只标的，使用 {len(selected_analysts) if selected_analysts else '全部'} 位分析师...",
        level="info",
    )

    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        status_text.text("⏳ 正在获取财务数据...")
        progress_bar.progress(10)

        result = run_ai_hedge_fund(
            tickers=tickers,
            start_date=start_date.strftime("%Y-%m-%d") if start_date else None,
            end_date=end_date.strftime("%Y-%m-%d") if end_date else None,
            selected_analysts=selected_analysts,
            show_reasoning=show_reasoning,
            model_name=model,
            model_provider=provider.upper(),
        )

        progress_bar.progress(100)
        status_text.text("✅ 分析完成")

        if not result.get("success"):
            render_alert_card(
                "分析失败",
                f"分析失败: {result.get('error', '未知错误')}",
                level="error",
            )
            st.stop()

        # ── 结果展示 ──
        st.markdown("---")
        st.header("📊 分析结果")

        decisions = result.get("decisions", {})
        signals = result.get("analyst_signals", {})

        # 最终决策卡片
        st.subheader("🎯 最终交易决策")

        if not decisions:
            render_alert_card("决策缺失", "未生成交易决策", level="warning")
        else:
            decision_cols = st.columns(min(len(decisions), 4))
            for i, (ticker, decision) in enumerate(decisions.items()):
                with decision_cols[i % 4]:
                    if isinstance(decision, dict):
                        action = decision.get("action", "hold")
                        qty = decision.get("quantity", 0)
                        conf = decision.get("confidence", 0)
                        reasoning = decision.get("reasoning", "")

                        action_color = {
                            "buy": "green",
                            "sell": "red",
                            "short": "orange",
                            "cover": "blue",
                            "hold": "gray",
                        }.get(action, "gray")

                        action_cn = {
                            "buy": "买入",
                            "sell": "卖出",
                            "short": "做空",
                            "cover": "平空",
                            "hold": "持有",
                        }.get(action, action)

                        with st.container(border=True):
                            st.metric(ticker, f"{action_cn} x{qty}")
                            st.caption(f"信心: {conf}%")
                            st.caption(reasoning[:100])

        # 分析师信号汇总表
        st.markdown("---")
        st.subheader("📋 分析师信号矩阵")

        signal_data = []
        for agent_id, agent_signals in signals.items():
            if agent_id == "risk_management_agent":
                continue
            display_name = (
                agent_id.replace("_agent", "")
                .replace("_analyst", "")
                .replace("_", " ")
                .title()
            )
            for ticker in tickers:
                sig = agent_signals.get(ticker, {})
                signal = sig.get("signal", "—")
                confidence = sig.get("confidence", 0) if isinstance(sig, dict) else 0
                signal_data.append(
                    {
                        "分析师": display_name,
                        "标的": ticker,
                        "信号": signal.upper(),
                        "信心%": confidence,
                    }
                )

        if signal_data:
            df_signals = pd.DataFrame(signal_data)

            # 彩色信号矩阵
            for ticker in tickers:
                ticker_signals = [s for s in signal_data if s["标的"] == ticker]
                bullish = sum(1 for s in ticker_signals if s["信号"] == "BULLISH")
                bearish = sum(1 for s in ticker_signals if s["信号"] == "BEARISH")
                neutral = sum(1 for s in ticker_signals if s["信号"] == "NEUTRAL")
                total = len(ticker_signals)

                with st.expander(
                    f"{ticker} | 🟢{bullish} 🔴{bearish} 🟡{neutral} (共{total}位)"
                ):
                    tdf = df_signals[df_signals["标的"] == ticker]
                    st.dataframe(tdf, use_container_width=True, hide_index=True)

            # 下载结果
            st.download_button(
                "📥 下载分析报告 (JSON)",
                data=json.dumps(result, ensure_ascii=False, indent=2, default=str),
                file_name=f"AI_Hedge_Fund_{now_bj().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
            )

    except Exception as e:
        progress_bar.empty()
        status_text.empty()
        render_alert_card("运行失败", f"运行失败: {e}", level="error")
        st.exception(e)

# ── 底部信息 ──
st.markdown("---")
st.caption(
    "⚠️ 免责声明: 本系统仅供教育和研究目的使用，不构成任何投资建议。"
    "所有交易决策均由 AI 模型基于历史数据生成，过往表现不代表未来结果。"
    "请咨询专业金融顾问做出投资决策。"
)
st.caption("© 2026 AI Hedge Fund | 集成于量化策略系统 v5.6")
