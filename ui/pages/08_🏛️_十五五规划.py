"""十五五规划分析 — 持仓对标 + 政策对齐度评分 + 权重调整建议"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import pandas as pd
import streamlit as st

st.title("🏛️ 十五五规划适配分析")
st.caption("持仓对标十五五规划七大战略方向 — 政策对齐度评分 + 权重调整建议")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card

inject_global_style()

mod = get_system_module()

if not mod.FIFTEEN_FIVE_AVAILABLE:
    render_alert_card("模块不可用", "❌ 十五五规划分析模块不可用，请检查 `utils/five_year_plan.py`", level="error")
    st.stop()

@st.cache_data(ttl=300)
def _run_fifteen_five_analysis():
    """缓存十五五规划分析结果，5分钟TTL"""
    analyzer = mod.FifteenFivePlanAnalyzer()
    overview = analyzer.get_policy_overview()
    holdings = analyzer.analyze_holdings()
    adjustments = analyzer.get_weight_adjustments()
    report = analyzer.generate_report()
    return overview, holdings, adjustments, report

if st.button("🚀 运行十五五规划分析", type="primary"):
    with st.spinner("运行十五五规划适配分析..."):
        overview, holdings, adjustments, report = _run_fifteen_five_analysis()

    # === 七大战略方向 ===
    st.subheader("📋 十五五规划七大战略方向")
    if overview:
        ov_data = [{"战略方向": o.get('direction', ''), "权重": f"{o.get('weight', 0):.0%}",
                    "优先级评分": o.get('relevance_score', 0)} for o in overview]
        st.dataframe(pd.DataFrame(ov_data), use_container_width=True, hide_index=True)

    # === 持仓适配评级 ===
    st.subheader("📊 持仓十五五适配评级")
    if holdings:
        hold_data = [{"名称": h.get('name', ''), "综合评分": h.get('overall_score', 0),
                      "等级": h.get('grade', '')} for h in holdings]
        hold_df = pd.DataFrame(hold_data)

        def color_grade(val):
            if val == 'A':
                return 'background-color: #f6ffed; color: #52c41a; font-weight: bold'
            elif val == 'B':
                return 'background-color: #e6f7ff; color: #1890ff'
            elif val == 'C':
                return 'background-color: #fffbe6; color: #faad14'
            elif val == 'D':
                return 'background-color: #fff2f0; color: #ff4d4f'
            return ''

        st.dataframe(hold_df.style.map(color_grade, subset=['等级']),
                     use_container_width=True, hide_index=True)
        st.bar_chart(hold_df.set_index("名称")["综合评分"], use_container_width=True)

    # === 权重调整建议 ===
    st.subheader("⚖️ 权重调整建议")
    if adjustments:
        adj_data = [{"名称": adj.get('name', ''), "建议": adj.get('suggestion', ''),
                     "调整幅度": f"{adj.get('weight_adjust_pct', 0):+.1f}%"} for adj in adjustments]
        adj_df = pd.DataFrame(adj_data)

        def highlight_adjust(val):
            if val.startswith('+'):
                return 'color: #52c41a; font-weight: bold'
            elif val.startswith('-'):
                return 'color: #ff4d4f; font-weight: bold'
            return ''

        st.dataframe(adj_df.style.map(highlight_adjust, subset=['调整幅度']),
                     use_container_width=True, hide_index=True)

    # === 下载报告 ===
    st.download_button(
        "📥 下载十五五规划报告", report,
        file_name=f"十五五规划适配_{datetime.now().strftime('%Y%m%d')}.md",
        mime="text/markdown",
    )

else:
    render_alert_card("待运行分析", "👆 点击上方按钮开始十五五规划适配分析", level="info")
    st.markdown("""
    ### 分析内容
    - **七大战略方向**: 制造强国、数字经济、绿色低碳、安全发展、乡村振兴、民生保障、改革开放
    - **持仓对标**: 每只标的与七大战略方向的关联度评分
    - **政策对齐度**: 持仓组合整体与十五五规划的一致性评级 (A/B/C/D)
    - **权重调整**: 基于政策对齐度的权重增减建议
    """)
