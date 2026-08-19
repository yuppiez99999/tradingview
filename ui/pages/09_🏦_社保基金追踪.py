"""社保基金ETF风格追踪 — 风格分类 + 国家队信号 + 配置建议"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import pandas as pd
import streamlit as st

st.title("🏦 社保基金ETF风格追踪")
st.caption("社保基金四大投资风格配置 + ETF风格映射 + 国家队信号检测")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

if not mod.SOCIAL_SECURITY_ETF_AVAILABLE:
    render_alert_card("模块不可用", "❌ 社保基金ETF追踪模块不可用，请检查 `utils/social_security_etf.py`", level="error")
    st.stop()

@st.cache_data(ttl=300)
def _run_social_security_analysis():
    """缓存社保基金ETF分析结果，5分钟TTL"""
    tracker = mod.SocialSecurityETFTracker()
    summary = tracker.classifier.get_style_summary()
    etf_class = tracker.classifier.get_all_etf_classifications()
    flow_data = mod.fetch_etf_flow_data()
    report = tracker.generate_report(flow_data=flow_data if flow_data else None)
    return summary, etf_class, flow_data, report

if st.button("🚀 运行社保基金ETF分析", type="primary"):
    with st.spinner("运行社保基金ETF风格追踪分析..."):
        summary, etf_class, flow_data, report = _run_social_security_analysis()

    # === 四大风格配置 ===
    st.subheader("📊 社保基金四大投资风格")
    if summary:
        style_cols = st.columns(len(summary))
        for i, (style, info) in enumerate(summary.items()):
            action = info.get('recommended_action', '标配')
            icon_map = {"超配": "📈", "标配": "📊", "低配": "📉"}
            icon = icon_map.get(action, "➡️")
            with style_cols[i]:
                color = "#52c41a" if action == "超配" else "#faad14" if action == "标配" else "#1890ff"
                st.markdown(f"""<div style="padding:14px;border-radius:10px;background:#fafafa;
                border-top:4px solid {color};text-align:center;">
                <div style="font-size:32px;">{icon}</div>
                <div style="font-size:18px;font-weight:bold;margin:8px 0;">{style}</div>
                <div style="font-size:14px;">权重: {info.get('weight', 0):.0%}</div>
                <div style="font-size:14px;color:{color};font-weight:bold;">{action}</div>
                <div style="font-size:12px;color:#888;margin-top:4px;">
                {', '.join(info.get('top_etfs', [])[:2])}
                </div>
                </div>""", unsafe_allow_html=True)

    # === ETF风格映射 ===
    st.subheader("🔗 ETF风格映射")
    if etf_class:
        etf_data = [{"ETF名称": e.get('name', ''), "社保风格": e.get('social_style', ''),
                     "匹配度": e.get('match_score', 0)} for e in etf_class[:15]]
        etf_df = pd.DataFrame(etf_data)

        def color_match(val):
            if val >= 85:
                return 'background-color: #f6ffed; color: #52c41a'
            elif val >= 70:
                return 'background-color: #fffbe6; color: #faad14'
            return ''

        st.dataframe(etf_df.style.map(color_match, subset=['匹配度']),
                     use_container_width=True, hide_index=True)

    # === 资金流增强 ===
    st.subheader("💰 实时资金流数据")
    if flow_data:
        render_status_card("资金流数据", f"已获取 {len(flow_data)} 只ETF资金流数据", level="success")
        flow_table = [{"名称": data.get('name', code), "代码": code,
                       "净流入(亿)": data.get('net_flow_yi', 0),
                       "趋势": data.get('trend', '中性'),
                       "类别": data.get('category', '')}
                      for code, data in list(flow_data.items())[:15]]
        st.dataframe(pd.DataFrame(flow_table), use_container_width=True, hide_index=True)
    else:
        render_alert_card("资金流数据缺失", "⚠️ 未能获取实时资金流数据，将使用静态分析", level="warning")

    # === 生成报告 ===
    st.download_button(
        "📥 下载社保基金ETF报告", report,
        file_name=f"社保基金ETF追踪_{datetime.now().strftime('%Y%m%d')}.md",
        mime="text/markdown",
    )

    with st.expander("📄 报告预览"):
        st.markdown(report)

else:
    render_alert_card("待运行分析", "👆 点击上方按钮开始社保基金ETF风格追踪分析", level="info")
    st.markdown("""
    ### 分析内容
    - **四大投资风格**: 稳健价值型 / 成长进取型 / 周期轮动型 / 防御避险型
    - **ETF风格映射**: 将主流ETF按社保基金投资风格分类
    - **国家队信号**: 结合ETF资金流向检测国家队加仓/减仓信号
    - **配置建议**: 超配/标配/低配 建议 + 代表ETF推荐
    """)
