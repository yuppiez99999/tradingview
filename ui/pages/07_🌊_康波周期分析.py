# -*- coding: utf-8 -*-
"""康波周期分析 — 周期阶段判定 + 行业轮动 + 大宗商品信号"""
import sys, os
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import streamlit as st
import pandas as pd
from datetime import datetime

st.title("🌊 康波周期分析")
st.caption("第六轮康波（AI/算力驱动）周期阶段判定 + 行业配置建议 + 大宗商品信号")

from ui.components.module_loader import get_system_module
from ui.components.common import inject_global_style
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

if not mod.KONDRATIEV_AVAILABLE:
    render_alert_card("模块不可用", "❌ 康波周期分析模块不可用，请检查 `utils/kondratiev_cycle.py`", level="error")
    st.stop()

@st.cache_data(ttl=300)
def _run_kondratiev_analysis():
    """缓存康波周期分析结果，5分钟TTL"""
    analyzer = mod.KondratievCycleAnalyzer()
    phase = analyzer.get_current_phase()
    sectors = analyzer.get_sector_allocation()
    commodities = analyzer.get_commodity_signals()
    overlay = analyzer.get_fifteen_five_overlay()
    report = analyzer.generate_report()
    return phase, sectors, commodities, overlay, report

if st.button("🚀 运行康波周期分析", type="primary"):
    with st.spinner("运行康波周期 + 十五五交叠分析..."):
        phase, sectors, commodities, overlay, report = _run_kondratiev_analysis()

    # === 周期阶段仪表盘 ===
    st.subheader("📍 康波周期阶段")
    cols = st.columns(4)
    with cols[0]:
        st.metric("当前阶段", phase.get('phase_name_cn', '未知'))
    with cols[1]:
        st.metric("进度", f"{phase.get('progress_pct', 0)}%")
    with cols[2]:
        st.metric("置信度", phase.get('confidence', '未知'))
    with cols[3]:
        st.metric("风险等级", phase.get('risk_level', '未知'))

    render_alert_card("推荐风格", f"**推荐风格**: {phase.get('recommended_style', '')}", level="info")
    if phase.get('estimated_transition'):
        st.caption(f"预计转入下一阶段: {phase.get('estimated_transition')}")

    # === 行业配置 ===
    st.subheader("📈 行业配置建议")
    if sectors:
        sector_data = [{"行业": s.get('sector', ''), "综合得分": s.get('combined_score', 0),
                        "建议": s.get('recommendation', '')} for s in sectors]
        sector_df = pd.DataFrame(sector_data)
        st.dataframe(sector_df, use_container_width=True, hide_index=True)
        st.bar_chart(sector_df.set_index("行业")["综合得分"], use_container_width=True)

    # === 大宗商品信号 ===
    st.subheader("🛢️ 大宗商品周期信号")
    if commodities:
        comm_data = [{"品种": c.get('name', ''), "当前信号": c.get('current_signal', ''),
                      "康波建议": c.get('kondratiev_recommendation', '')} for c in commodities]
        st.dataframe(pd.DataFrame(comm_data), use_container_width=True, hide_index=True)

    # === 十五五交叠 ===
    st.subheader("🔗 十五五与康波交叠")
    if overlay:
        st.markdown(f"> {overlay.get('synergy_conclusion', '')}")

    # === 下载报告 ===
    st.download_button(
        "📥 下载康波周期报告", report,
        file_name=f"康波周期分析_{datetime.now().strftime('%Y%m%d')}.md",
        mime="text/markdown",
    )

else:
    render_alert_card("待运行分析", "👆 点击上方按钮开始康波周期 + 十五五交叠分析", level="info")
    st.markdown("""
    ### 分析内容
    - **周期阶段判定**: 衰退/复苏/繁荣/滞胀 — 当前属于第六轮康波（AI/算力驱动）哪个阶段
    - **行业配置建议**: 基于周期阶段的行业轮动策略
    - **大宗商品信号**: 黄金/白银/铜/锡/铝/原油等大宗商品的操作信号
    - **十五五交叠分析**: 康波周期与十五五规划的政策叠加效应
    """)
