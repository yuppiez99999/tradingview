"""再平衡执行 v4.0 — AI双轨再平衡引擎 + 风险平价 + 豆包Seed盘中决策"""
import json
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="再平衡执行", page_icon="🔄", layout="wide")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

try:
    from quant_modules.ai_rebalancing_engine import (
        AIQuantRebalancingEngine,
        ExecutionPlanner,
        GoldStopLossStrategy,
        MonthlyKPITracker,
        RiskParityEngine,
        SeedRebalancer,  # noqa: F401
        run_ai_rebalance,  # noqa: F401
    )
    ENGINE_OK = True
except ImportError as e:
    ENGINE_OK = False
    _import_err = str(e)

st.title("🔄 AI量化再平衡执行 v4.0")
st.caption("双轨策略 | 风险平价 | 黄金分级止损 | 豆包Seed盘中决策")

POSITIONS_FILE = os.path.join(_BASE_DIR, 'config', 'positions.json')

@st.cache_data(ttl=60)
def load_positions():
    """加载持仓，自动解包嵌套positions键"""
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'positions' in data:
            return data['positions']
        return data
    return {}

# ── 侧边栏 ──
with st.sidebar:
    st.subheader("🎛️ 再平衡配置 v4.0")

    total_capital = st.number_input("总资金 (万元)", value=4300, step=100) * 10000
    equity_capital = st.number_input("权益组合 (万元)", value=300, step=10) * 10000

    use_llm = st.checkbox("🧠 豆包Seed LLM盘中决策", value=True)
    use_theories = st.checkbox("📊 四大理论信号", value=True)

    st.divider()
    st.caption(f"低风险理财: {(total_capital - equity_capital)/1e4:.0f}万 ({100-equity_capital/total_capital*100:.0f}%)")
    st.caption(f"现金缓冲: {equity_capital*0.08/1e4:.0f}万 (8%)")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 运行分析", type="primary", use_container_width=True):
            st.session_state['run_analysis'] = True
    with c2:
        if st.button("📂 刷新持仓", use_container_width=True):
            load_positions.clear()
            st.rerun()

    # 黄金止损状态
    with st.expander("🥇 黄金止损规则"):
        st.markdown(f"- 减半仓: ≤ **{GoldStopLossStrategy.HALF_THRESHOLD:.0%}**")
        st.markdown(f"- 清仓: ≤ **{GoldStopLossStrategy.CLEAR_THRESHOLD:.0%}**")
        st.caption("基于18.88%波动率优化 | 2024-2026仅触发1次减半")

# ── 引擎检查 ──
if not ENGINE_OK:
    render_alert_card("引擎导入失败", f"引擎导入失败: {_import_err}", level="error")
    st.stop()

positions = load_positions()
if not positions:
    render_alert_card("持仓数据缺失", "📋 暂无持仓数据，请在 config/positions.json 配置持仓", level="info")
    st.code('{"510300": {"shares": 10000, "cost": 38000, "avg_cost": 3.80}}')
    st.stop()

codes = list(positions.keys())
render_status_card("持仓状态", f"持有 {len(codes)} 标的", level="success")

# ── 获取价格 (并行) ──
prices = {}
try:
    from quant_modules.wind_mcp import get_realtime_prices_batch
    prices = get_realtime_prices_batch(codes)
except (ImportError, ConnectionError, TimeoutError, ValueError, RuntimeError):
    pass

for code in codes:
    if code not in prices:
        prices[code] = positions[code].get('avg_cost', 0)

# ═══════════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🧠 AI再平衡", "⚖️ 风险平价", "📅 执行计划", "📊 KPI追踪", "📜 历史"
])

# ═══════════════════════════════════
# Tab 1: AI再平衡
# ═══════════════════════════════════
with tab1:
    st.subheader("🧠 AI量化再平衡分析")

    if 'run_analysis' not in st.session_state:
        st.session_state['run_analysis'] = False

    if st.session_state.get('run_analysis') or st.button("🔄 重新分析", type="primary"):
        st.session_state['run_analysis'] = True
        with st.spinner("🧠 AI双轨分析中..."):
            engine = AIQuantRebalancingEngine(
                total_capital=total_capital,
                equity_capital=equity_capital,
                use_llm=use_llm,
                use_theories=use_theories
            )
            result = engine.analyze_portfolio(positions, prices)
            st.session_state['rebalance_result'] = result

    if 'rebalance_result' not in st.session_state:
        render_alert_card("待运行分析", "👆 点击「运行分析」开始", level="info")
        st.stop()

    result = st.session_state['rebalance_result']

    # 概览指标
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🕐 分析时间", result.timestamp)
    c2.metric("💰 组合总值", f"¥{result.total_value:,.0f}")
    c3.metric("📋 信号数量", len(result.signals))
    c4.metric("💵 净现金需求", f"¥{result.cash_needed:+,.0f}")
    c5.metric("📊 理论信号", len(result.theory_signals))

    st.divider()

    # ── 交易信号 ──
    if result.signals:
        st.subheader("📤 交易信号")
        signal_rows = []
        for sig in result.signals:
            price = prices.get(sig.code, 0)
            signal_rows.append({
                "代码": sig.code,
                "名称": sig.name,
                "操作": sig.action,
                "股数": f"{sig.shares:,}",
                "金额": f"¥{sig.shares * price:,.0f}",
                "置信度": f"{sig.confidence:.0%}",
                "批次": sig.execution_batch or "—",
                "理论来源": sig.theory_signal or "权重偏离",
                "止损触发": "⚠️" if sig.stop_loss_triggered else "",
                "理由": sig.reason,
            })

        def color_action(val):
            if val == "BUY":
                return 'background-color: #f6ffed; color: #52c41a'
            if val == "SELL":
                return 'background-color: #fff2f0; color: #ff4d4f'
            return ''

        df = pd.DataFrame(signal_rows)
        st.dataframe(df.style.map(color_action, subset=['操作']),
                    use_container_width=True, hide_index=True)

        # 统计
        buys = sum(1 for s in result.signals if s.action == "BUY")
        sells = sum(1 for s in result.signals if s.action == "SELL")
        stops = sum(1 for s in result.signals if s.stop_loss_triggered)
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("📥 买入", buys)
        sc2.metric("📤 卖出", sells)
        sc3.metric("⛔ 止损触发", stops)
        sc4.metric("📊 平均置信度", f"{sum(s.confidence for s in result.signals)/max(len(result.signals),1):.0%}")
    else:
        render_alert_card("组合已平衡", "✅ 组合权重已接近目标，无需调整", level="success")

    # ── DeepSeek决策 ──
    if result.llm_decision:
        st.divider()
        st.subheader("🧠 豆包Seed LLM 盘中决策")
        render_alert_card("豆包Seed LLM 盘中决策", result.llm_decision, level="info")

    # ── 权重对比图 ──
    st.divider()
    st.subheader("📊 当前 vs 目标权重")

    names, current_w, target_w, rp_w = [], [], [], []
    for code in result.portfolio_weights:
        cfg = engine.portfolio_config.get(code, {})
        cw = result.portfolio_weights.get(code, 0) * 100
        tw = result.target_weights.get(code, 0) * 100
        rpw = result.risk_parity_weights.get(code, 0) * 100
        if cw > 0.01 or tw > 0.01:
            names.append(cfg.get('name', code))
            current_w.append(cw)
            target_w.append(tw)
            rp_w.append(rpw)

    if names:
        fig = go.Figure()
        fig.add_trace(go.Bar(name='当前权重', x=names, y=current_w,
                            marker_color='#91d5ff'))
        fig.add_trace(go.Bar(name='目标权重', x=names, y=target_w,
                            marker_color='#52c41a'))
        fig.add_trace(go.Bar(name='风险平价', x=names, y=rp_w,
                            marker_color='#faad14'))
        fig.update_layout(barmode='group', height=400, margin=dict(t=20),
                         yaxis_title='权重(%)')
        st.plotly_chart(fig, use_container_width=True)

# ═══════════════════════════════════
# Tab 2: 风险平价
# ═══════════════════════════════════
with tab2:
    st.subheader("⚖️ 风险平价分析")

    rp = RiskParityEngine()
    total_rp, equity_rp = rp.compute_risk_parity_weights(total_capital, equity_capital)

    # 风险预算
    budget = rp.get_category_risk_budget()
    budget_rows = []
    for cat, info in sorted(budget.items()):
        budget_rows.append({
            "类别": cat,
            "标的数": info['count'],
            "平均风险权重": f"{info['avg_risk']:.3f}",
            "最大风险": f"{info['max_risk']:.3f}",
            "风控等级": "🔴高" if info['avg_risk'] >= 0.25 else ("🟡中" if info['avg_risk'] >= 0.12 else "🟢低"),
        })
    st.dataframe(pd.DataFrame(budget_rows), use_container_width=True, hide_index=True)

    # 风险平价权重排名
    st.subheader("权益组合 — 风险平价权重 Top 10")
    top_rp = sorted(equity_rp.items(), key=lambda x: -x[1])[:10]
    rp_rows = []
    for code, w in top_rp:
        rp_rows.append({
            "代码": code,
            "名称": engine.portfolio_config.get(code, {}).get('name', code),
            "风险平价权重": f"{w:.4%}",
            "风险权重": f"{engine.portfolio_config.get(code, {}).get('risk_weight', 0):.2f}",
        })
    st.dataframe(pd.DataFrame(rp_rows), use_container_width=True, hide_index=True)
    st.caption("风险平价: 低波动标的获得更高权重 → 各类资产贡献相等风险")

# ═══════════════════════════════════
# Tab 3: 5日执行计划
# ═══════════════════════════════════
with tab3:
    st.subheader("📅 5日建仓/再平衡执行计划")

    if 'rebalance_result' in st.session_state:
        ep = st.session_state['rebalance_result'].execution_plan
    else:
        ep = ExecutionPlanner.build_plan([], equity_capital)

    rules = ep.get('rules', {})
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("单日上限", f"{rules.get('max_daily_pct', 0.2)*100:.0f}%")
    rc2.metric("首日建仓", f"{rules.get('first_day_pct', 0.5)*100:.0f}%")
    rc3.metric("最低现金", f"¥{rules.get('min_cash_reserve', 240000):,.0f}")
    rc4.metric("熔断阈值", f"{rules.get('circuit_breaker', -0.03)*100:.0f}%")

    st.divider()

    plan = ep.get('plan', {})
    for day_name in ['Day1', 'Day2', 'Day3', 'Day4', 'Day5']:
        day = plan.get(day_name, {})
        buys = day.get('buys', [])
        sells = day.get('sells', [])

        with st.expander(f"{day_name} — 买入{len(buys)}只 / 卖出{len(sells)}只", expanded=(day_name == 'Day1')):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**📥 买入**")
                for code in buys:
                    cfg = engine.portfolio_config.get(code, {})
                    st.caption(f"`{code}` {cfg.get('name', '')}")
                if not buys:
                    st.caption("—")
            with c2:
                st.markdown("**📤 卖出**")
                for code in sells:
                    cfg = engine.portfolio_config.get(code, {})
                    st.caption(f"`{code}` {cfg.get('name', '')}")
                if not sells:
                    st.caption("—")

    st.divider()
    st.caption("建仓纪律: ①首日建仓50%确认趋势后补满 ②单日≤20%总资金 ③沪深300跌>3%暂停 ④每笔设置止损委托")

# ═══════════════════════════════════
# Tab 4: 月度KPI
# ═══════════════════════════════════
with tab4:
    st.subheader("📊 月度KPI追踪")

    targets = MonthlyKPITracker.MONTHLY_TARGETS
    kpi_rows = []
    current_month = datetime.now().month
    for m, t in sorted(targets.items()):
        is_current = "▶️ " if m == current_month else ""
        kpi_rows.append({
            "月份": f"{is_current}{m}月",
            "阶段": t['phase'],
            "目标净值": f"{t['nav_range'][0]:.2f}~{t['nav_range'][1]:.2f}",
            "最大回撤上限": f"{t['max_dd']:.0%}",
            "现金目标": f"¥{t['cash']:,}",
        })
    st.dataframe(pd.DataFrame(kpi_rows), use_container_width=True, hide_index=True)

    # 当前KPI
    if 'rebalance_result' in st.session_state:
        kpi = st.session_state['rebalance_result'].monthly_kpi
        st.divider()
        st.subheader(f"当前月度评估 ({kpi.get('month', 0)}月)")
        kc1, kc2, kc3 = st.columns(3)
        kc1.metric("净值达标", "✅" if kpi.get('nav_ok') else "❌")
        kc2.metric("回撤达标", "✅" if kpi.get('dd_ok') else "❌")
        kc3.metric("综合评级", kpi.get('overall', '—'))
        st.caption(f"阶段: {kpi.get('phase', '')} | NAV目标: {kpi.get('nav_target','')} | 回撤上限: {kpi.get('dd_target',0):.0%}")

# ═══════════════════════════════════
# Tab 5: 历史
# ═══════════════════════════════════
with tab5:
    st.subheader("📜 再平衡历史记录")

    history_file = os.path.join(_BASE_DIR, 'config', 'rebalance_history.json')
    if os.path.exists(history_file):
        with open(history_file, encoding='utf-8') as f:
            history = json.load(f)
        if history:
            st.dataframe(
                pd.DataFrame(history).sort_values('timestamp', ascending=False),
                use_container_width=True, hide_index=True
            )
            if st.button("🗑️ 清除历史"):
                os.remove(history_file)
                st.rerun()
        else:
            render_alert_card("暂无历史记录", level="info")
    else:
        render_alert_card("暂无历史记录", "暂无历史记录 | 运行分析后可保存", level="info")

# ── 底部：保存 ──
st.divider()
if 'rebalance_result' in st.session_state and st.button("💾 保存分析结果"):
    result = st.session_state['rebalance_result']
    history_file = os.path.join(_BASE_DIR, 'config', 'rebalance_history.json')

    history = []
    if os.path.exists(history_file):
        with open(history_file, encoding='utf-8') as f:
            history = json.load(f)

    history.insert(0, {
        "timestamp": result.timestamp,
        "total_value": result.total_value,
        "cash_needed": result.cash_needed,
        "signals": [
            {"code": s.code, "name": s.name, "action": s.action,
             "shares": s.shares, "confidence": s.confidence}
            for s in result.signals
        ],
        "kpi_overall": result.monthly_kpi.get('overall', ''),
        "llm_summary": result.llm_decision[:200] if result.llm_decision else "",
    })

    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history[:100], f, ensure_ascii=False, indent=2)

    render_alert_card("保存成功", "✅ 分析结果已保存", level="success")
