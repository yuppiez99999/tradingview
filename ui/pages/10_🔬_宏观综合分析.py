"""宏观综合分析 — 一键运行康波周期 + 十五五规划 + 社保基金ETF"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import pandas as pd
import streamlit as st

st.title("🔬 宏观综合分析")
st.caption("一键运行三大 v5.2 宏观分析模块 — 康波周期 + 十五五规划 + 社保基金ETF")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

st.markdown("---")

# 检查所有模块可用性
avail = {
    "康波周期": mod.KONDRATIEV_AVAILABLE,
    "十五五规划": mod.FIFTEEN_FIVE_AVAILABLE,
    "社保基金ETF": mod.SOCIAL_SECURITY_ETF_AVAILABLE,
}

cols = st.columns(3)
for i, (name, ok) in enumerate(avail.items()):
    with cols[i]:
        if ok:
            render_status_card(name, "模块可用", level="success")
        else:
            render_alert_card(name, "模块不可用", level="error")

if not any(avail.values()):
    render_alert_card("模块不可用", "所有 v5.2 分析模块均不可用，请检查 utils/ 目录", level="error")
    st.stop()

@st.cache_data(ttl=300)
def _run_macro_kondratiev():
    analyzer = mod.KondratievCycleAnalyzer()
    return analyzer.get_current_phase(), analyzer.get_sector_allocation(), analyzer.get_commodity_signals()

@st.cache_data(ttl=300)
def _run_macro_fifteen_five():
    analyzer = mod.FifteenFivePlanAnalyzer()
    return analyzer.analyze_holdings(), analyzer.get_weight_adjustments()

@st.cache_data(ttl=300)
def _run_macro_social_security():
    tracker = mod.SocialSecurityETFTracker()
    return tracker.classifier.get_style_summary(), mod.fetch_etf_flow_data(), tracker

if st.button("🚀 一键运行宏观综合分析", type="primary"):
    results = {}

    # 使用选项卡展示三大分析
    tab1, tab2, tab3 = st.tabs(["🌊 康波周期", "🏛️ 十五五规划", "🏦 社保基金ETF"])

    # === 1. 康波周期 ===
    with tab1:
        if mod.KONDRATIEV_AVAILABLE:
            with st.spinner("运行康波周期分析..."):
                try:
                    phase, sectors, commodities = _run_macro_kondratiev()

                    st.subheader("📍 周期阶段")
                    pc = st.columns(4)
                    pc[0].metric("当前阶段", phase.get('phase_name_cn', '未知'))
                    pc[1].metric("进度", f"{phase.get('progress_pct', 0)}%")
                    pc[2].metric("置信度", phase.get('confidence', '未知'))
                    pc[3].metric("风险等级", phase.get('risk_level', '未知'))
                    render_alert_card("推荐风格", f"**推荐风格**: {phase.get('recommended_style', '')}", level="info")

                    st.subheader("📈 行业配置")
                    if sectors:
                        sec_df = pd.DataFrame(sectors)
                        st.dataframe(sec_df[['sector', 'combined_score', 'recommendation']],
                                     use_container_width=True, hide_index=True)

                    st.subheader("🛢️ 商品信号")
                    if commodities:
                        st.dataframe(pd.DataFrame(commodities), use_container_width=True, hide_index=True)

                    results['kondratiev'] = True
                except Exception as e:
                    render_alert_card("康波周期分析失败", f"康波周期分析失败: {e}", level="error")
                    results['kondratiev'] = False
        else:
            render_alert_card("模块不可用", "⚠️ 康波周期模块不可用", level="warning")
            results['kondratiev'] = None

    # === 2. 十五五规划 ===
    with tab2:
        if mod.FIFTEEN_FIVE_AVAILABLE:
            with st.spinner("运行十五五规划分析..."):
                try:
                    holdings, adjustments = _run_macro_fifteen_five()

                    st.subheader("📊 持仓适配评级")
                    if holdings:
                        hold_data = [{"名称": h.get('name', ''), "评分": h.get('overall_score', 0),
                                      "等级": h.get('grade', '')} for h in holdings]
                        st.dataframe(pd.DataFrame(hold_data), use_container_width=True, hide_index=True)

                    st.subheader("⚖️ 权重调整")
                    if adjustments:
                        adj_data = [{"名称": a.get('name', ''), "建议": a.get('suggestion', ''),
                                     "幅度": f"{a.get('weight_adjust_pct', 0):+.1f}%"}
                                    for a in adjustments if a.get('weight_adjust_pct', 0) != 0]
                        if adj_data:
                            st.dataframe(pd.DataFrame(adj_data), use_container_width=True, hide_index=True)
                        else:
                            render_alert_card("权重调整", "无需调整", level="info")

                    results['fifteen_five'] = True
                except Exception as e:
                    render_alert_card("十五五规划分析失败", f"十五五规划分析失败: {e}", level="error")
                    results['fifteen_five'] = False
        else:
            render_alert_card("模块不可用", "⚠️ 十五五规划模块不可用", level="warning")
            results['fifteen_five'] = None

    # === 3. 社保基金ETF ===
    with tab3:
        if mod.SOCIAL_SECURITY_ETF_AVAILABLE:
            with st.spinner("运行社保基金ETF分析..."):
                try:
                    summary, flow_data, ss_tracker = _run_macro_social_security()

                    st.subheader("📊 四大投资风格")
                    if summary:
                        sc = st.columns(len(summary))
                        for i, (style, info) in enumerate(summary.items()):
                            action = info.get('recommended_action', '标配')
                            icon = {"超配": "📈", "标配": "📊", "低配": "📉"}.get(action, "➡️")
                            with sc[i]:
                                st.metric(f"{icon} {style}", f"{info.get('weight', 0):.0%}", delta=action)

                    if flow_data:
                        render_status_card("资金流数据", f"已获取 {len(flow_data)} 只ETF资金流数据", level="success")

                    report = ss_tracker.generate_report(flow_data=flow_data or None)
                    with st.expander("📄 完整报告"):
                        st.markdown(report)

                    results['social_security'] = True
                except Exception as e:
                    render_alert_card("社保基金ETF分析失败", f"社保基金ETF分析失败: {e}", level="error")
                    results['social_security'] = False
        else:
            render_alert_card("模块不可用", "⚠️ 社保基金ETF模块不可用", level="warning")
            results['social_security'] = None

    # === 汇总 ===
    st.markdown("---")
    st.subheader("📋 分析结果汇总")
    success = sum(1 for v in results.values() if v is True)
    total = sum(1 for v in results.values() if v is not None)
    render_alert_card("宏观综合分析完成", f"🔬 宏观综合分析完成: **{success}/{total}** 模块成功", level="success")

    rcols = st.columns(3)
    for i, (name, result) in enumerate(results.items()):
        with rcols[i]:
            if result is True:
                render_status_card(name, "分析成功", level="success")
            elif result is False:
                render_alert_card(name, "分析失败", level="error")
            else:
                render_alert_card(name, "模块不可用", level="warning")

else:
    render_alert_card("待运行分析", "👆 点击上方按钮一键运行三大宏观分析模块", level="info")
    st.markdown(f"""
    ### 运行内容
    1. **康波周期分析** — 周期阶段判定 + 行业配置 + 商品信号 + 十五五交叠
    2. **十五五规划分析** — 持仓对标 + 政策对齐评分 + 权重调整建议
    3. **社保基金ETF追踪** — 风格分类 + ETF映射 + 资金流增强 + 配置建议

    > 所有报告将自动归档到 `每日报告归档/{datetime.now().strftime('%Y-%m-%d')}/`
    """)
