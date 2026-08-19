"""ETF资金流向 v2.0 — 国家队ETF资金监控 + 自动刷新 + 历史累计流图 + 实时告警"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="ETF资金流向", page_icon="💰", layout="wide")
st.title("💰 ETF资金流向监控 v2.0")
st.caption("监测宽基/行业ETF资金流向，检测国家队加仓/减仓信号 | 支持自动刷新与历史追踪")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

# ── 信号阈值 ──
SIGNAL_THRESHOLD = {"high": 50, "medium": 10, "low": 2}

# ── 侧边栏 ──
with st.sidebar:
    st.subheader("🎛️ 监控设置")

    # 自动刷新
    auto_refresh = st.checkbox("🔄 自动刷新 (每60秒)", value=False)
    if auto_refresh:
        render_alert_card("自动刷新", "页面每60秒自动刷新一次", level="info")
        # JavaScript 自动刷新（兼容 Streamlit 1.58）
        components.html("""
        <script>
            setTimeout(function() {
                window.location.reload();
            }, 60000);
        </script>
        """, height=0)

    st.divider()
    st.markdown("#### 信号阈值")
    th_cols = st.columns(1)
    st.metric("🔴 高置信度", f">= {SIGNAL_THRESHOLD['high']} 亿", delta="强加仓/强减仓")
    st.metric("🟡 中等置信度", f">= {SIGNAL_THRESHOLD['medium']} 亿", delta="加/减仓信号")
    st.metric("🟢 关注级别", f">= {SIGNAL_THRESHOLD['low']} 亿", delta="关注信号")

    st.divider()
    st.markdown("#### 监测范围")
    with st.expander("📋 ETF一览"):
        etf_by_category = {}
        for etf in mod.ETFFundFlowMonitor.ETF_LIST:
            cat = etf['category']
            etf_by_category.setdefault(cat, []).append(etf)
        for cat, etfs in sorted(etf_by_category.items()):
            st.caption(f"**{cat}** ({len(etfs)}只)")
            for etf in etfs:
                st.caption(f"  `{etf['code']}` {etf['name']}")

# ── 主区域 ──
tab1, tab2, tab3 = st.tabs(["📡 实时监控", "📈 历史趋势", "🚨 告警中心"])

@st.cache_data(ttl=120)
def _fetch_etf_flow_data():
    """缓存ETF资金流分析结果，2分钟TTL"""
    monitor = mod.ETFFundFlowMonitor(data_connector_manager=mod.connector_manager)
    flow_data = monitor.analyze_fund_flow()
    signals = monitor.detect_signals()
    suggestions = monitor.get_investment_suggestion()
    report = monitor.generate_report()
    return monitor, flow_data, signals, suggestions, report

# ═══════════════════════════════════════════════════════════════
# Tab 1: 实时监控
# ═══════════════════════════════════════════════════════════════
with tab1:
    analyze = st.button("🔍 获取实时ETF资金流向", type="primary")

    if not analyze:
        render_alert_card("分析提示", "👆 点击上方按钮开始分析（将尝试连接实时数据源，失败时使用模拟数据回退）", level="info")
        st.stop()

    with st.spinner("获取ETF资金流数据..."):
        monitor, flow_data, signals, suggestions, report = _fetch_etf_flow_data()

    # ── 顶部概览 ──
    top_left, top_right = st.columns([2, 1])

    with top_left:
        st.subheader("📊 市场整体趋势")
        overall = suggestions.get('overall_trend', '未知')
        total_net_flow = sum(s['net_flow_yi'] for s in signals)

        if overall == '净流入':
            render_status_card("整体流向", f"📈 整体 **{overall}** | 净流入: **{total_net_flow:+.1f}** 亿 — 国家队资金积极入场", level="success")
        elif overall == '净流出':
            render_alert_card("整体流向", f"📉 整体 **{overall}** | 净流出: **{total_net_flow:+.1f}** 亿 — 国家队资金有所流出", level="warning")
        else:
            render_alert_card("整体流向", f"➡️ 整体 **{overall}** | 净流动: **{total_net_flow:+.1f}** 亿", level="info")

    with top_right:
        st.subheader("🚨 信号统计")
        high_signals = suggestions.get('high_confidence_signals', [])
        high_buy = sum(1 for s in high_signals if '加仓' in s.get('signal_type', ''))
        high_sell = sum(1 for s in high_signals if '减仓' in s.get('signal_type', ''))

        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("🔴 强加仓", high_buy)
        sc2.metric("🟢 强减仓", high_sell)
        sc3.metric("📡 总信号", len(signals))

    # ── 高置信度信号卡片 ──
    if high_signals:
        st.subheader("🚨 高置信度信号 (>= +/- 50亿)")
        sig_cols = st.columns(min(len(high_signals), 4))
        for i, s in enumerate(high_signals):
            with sig_cols[i]:
                is_buy = '加仓' in s.get('signal_type', '')
                bg = "#f6ffed" if is_buy else "#fff2f0"
                border = "#52c41a" if is_buy else "#ff4d4f"
                icon = "📈" if is_buy else "📉"
                st.markdown(f"""<div style="padding:16px;border-radius:10px;border-left:5px solid {border};
                background:{bg};margin-bottom:8px;">
                <div style="font-size:24px;margin-bottom:4px;">{icon}</div>
                <div style="font-size:16px;font-weight:bold;">{s.get('signal_type', '')}</div>
                <div style="font-size:14px;">{s.get('name', '')}</div>
                <div style="font-size:12px;color:#888;">{s.get('code', '')}</div>
                <div style="font-size:18px;font-weight:bold;color:{border};margin-top:6px;">
                {s.get('net_flow_yi', 0):+.1f} 亿</div>
                </div>""", unsafe_allow_html=True)
    else:
        render_alert_card("信号缺失", "📡 未检测到高置信度信号", level="info")

    st.markdown("---")

    # ── 类别资金流 ──
    st.subheader("📊 各类别资金流向汇总")
    flow_list = list(flow_data) if isinstance(flow_data, list) else list(monitor.flow_data.values())

    category_flows = {}
    for f in flow_list:
        cat = f.get('category', '未知')
        net = f.get('net_flow_yi', 0)
        category_flows[cat] = category_flows.get(cat, 0) + net

    if category_flows:
        cat_df = pd.DataFrame({
            "类别": list(category_flows.keys()),
            "净流入(亿)": list(category_flows.values()),
        }).set_index("类别")

        cat_col1, cat_col2 = st.columns([3, 2])
        with cat_col1:
            # 使用plotly水平条形图
            fig_cat = go.Figure()
            sorted_cats = sorted(category_flows.items(), key=lambda x: x[1])
            colors = ['#ff4d4f' if v < 0 else '#52c41a' for _, v in sorted_cats]
            fig_cat.add_trace(go.Bar(
                y=[c for c, _ in sorted_cats],
                x=[v for _, v in sorted_cats],
                orientation='h', marker_color=colors,
                text=[f'{v:+.1f}亿' for _, v in sorted_cats],
                textposition='outside',
            ))
            fig_cat.update_layout(height=300, margin=dict(t=20, r=60), showlegend=False)
            st.plotly_chart(fig_cat, use_container_width=True)

        with cat_col2:
            for cat, net in sorted(category_flows.items(), key=lambda x: -x[1]):
                icon = "📈" if net > 0 else "📉" if net < 0 else "➡️"
                color = "#cf1322" if net > 0 else "#389e0d" if net < 0 else "#666"
                st.markdown(f"{icon} **{cat}**: <span style='color:{color}'>{net:+.1f} 亿</span>",
                           unsafe_allow_html=True)

    st.markdown("---")

    # ── ETF排名表 ──
    st.subheader("📈 ETF资金流排名")

    flow_table = []
    for f in flow_list:
        net_flow = f.get('net_flow_yi', 0)
        abs_flow = abs(net_flow)
        if abs_flow >= SIGNAL_THRESHOLD['high']:
            sig_bar = "🔴" + "█" * 5
        elif abs_flow >= SIGNAL_THRESHOLD['medium']:
            sig_bar = "🟡" + "█" * 3
        elif abs_flow >= SIGNAL_THRESHOLD['low']:
            sig_bar = "🟢" + "█"
        else:
            sig_bar = "—"

        flow_table.append({
            "信号": sig_bar,
            "名称": f.get('name', ''),
            "代码": f.get('code', ''),
            "类别": f.get('category', ''),
            "价格": f"{f.get('price', 0):.2f}" if f.get('price') else '-',
            "涨跌幅": f"{f.get('change_pct', 0):+.2f}%" if f.get('change_pct') else '-',
            "成交额(亿)": f"{f.get('amount_yi', 0):.1f}" if f.get('amount_yi') else '-',
            "净流入(亿)": f"{net_flow:+.2f}",
            "趋势": f.get('trend', '-'),
        })

    flow_df = pd.DataFrame(flow_table).sort_values(
        by="净流入(亿)", key=lambda x: x.astype(float), ascending=False
    )

    def highlight_flow_row(row):
        net_str = row.get('净流入(亿)', '0')
        try:
            net = float(net_str)
        except (ValueError, TypeError):
            return [''] * len(row)
        if net >= SIGNAL_THRESHOLD['high']:
            return ['background-color: #fff1f0'] * len(row)
        elif net <= -SIGNAL_THRESHOLD['high']:
            return ['background-color: #f6ffed'] * len(row)
        return [''] * len(row)

    def highlight_trend(val):
        if val == '流入':
            return 'color: #cf1322; font-weight: bold'
        elif val == '流出':
            return 'color: #389e0d; font-weight: bold'
        return ''

    styled = flow_df.style.apply(highlight_flow_row, axis=1)
    styled = styled.map(highlight_trend, subset=['趋势'])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── 风格轮动 ──
    st.subheader("🔄 风格轮动建议")
    style_rotation = suggestions.get('style_rotation', {})
    if style_rotation:
        rot_cols = st.columns(len(style_rotation))
        for i, (style, action) in enumerate(style_rotation.items()):
            with rot_cols[i]:
                icon = {"增持": "📈", "减持": "📉", "持有": "➡️"}.get(action, "➡️")
                color = {"增持": "#cf1322", "减持": "#389e0d", "持有": "#666"}.get(action, "#666")
                bg = {"增持": "#fff1f0", "减持": "#f6ffed", "持有": "#fafafa"}.get(action, "#fafafa")
                st.markdown(f"""<div style="padding:14px;border-radius:10px;background:{bg};
                border-top:4px solid {color};text-align:center;">
                <div style="font-size:30px;">{icon}</div>
                <div style="font-size:15px;font-weight:bold;margin:6px 0;">{style}</div>
                <div style="font-size:20px;color:{color};font-weight:bold;">{action}</div>
                </div>""", unsafe_allow_html=True)

    # ── 投资建议 + 信号详情 ──
    recs = suggestions.get('recommendations', [])
    if recs:
        st.subheader("💡 投资建议")
        for rec in recs:
            render_alert_card("配置建议", rec, level="info")

    if signals:
        with st.expander("📡 全部信号详情"):
            sig_data = []
            for s in signals:
                conf = s.get('confidence', '')
                conf_icon = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(conf, "")
                sig_data.append({
                    "置信度": f"{conf_icon} {conf}",
                    "ETF名称": s.get('name', ''),
                    "代码": s.get('code', ''),
                    "类别": s.get('category', ''),
                    "净流入(亿)": s.get('net_flow_yi', 0),
                    "涨跌幅": f"{s.get('change_pct', 0):+.2f}%" if s.get('change_pct') else '-',
                    "信号类型": s.get('signal_type', ''),
                    "趋势": s.get('trend', '-'),
                })
            sig_df = pd.DataFrame(sig_data)
            st.dataframe(sig_df, use_container_width=True, hide_index=True)

    # ── 下载 ──
    st.markdown("---")
    report = monitor.generate_report()
    dl_col1, dl_col2 = st.columns([1, 3])
    with dl_col1:
        st.download_button(
            "📥 下载完整报告 (.md)",
            report,
            file_name=f"ETF资金流向_{datetime.now():%Y%m%d}.md",
            mime="text/markdown", use_container_width=True,
        )
    with dl_col2:
        st.caption(f"报告生成: {datetime.now():%Y-%m-%d %H:%M:%S} | 监测 {len(mod.ETFFundFlowMonitor.ETF_LIST)} 只ETF")

# ═══════════════════════════════════════════════════════════════
# Tab 2: 历史趋势
# ═══════════════════════════════════════════════════════════════
with tab2:
    st.subheader("📈 历史资金流向趋势")

    # ── 模拟历史数据 ──
    import numpy as np

    np.random.seed(123)
    hist_dates = pd.date_range(end=datetime.now(), periods=30, freq='B')
    categories = ['宽基', '科技主题', '金融主题', '新能源主题', '避险资产', '医药主题']

    # 生成累计流数据
    cumulative_flows = {}
    for cat in categories:
        daily_flows = np.cumsum(np.random.normal(0.5, 3, len(hist_dates)))
        cumulative_flows[cat] = daily_flows

    # ── 累计流面积图 ──
    fig_hist = go.Figure()
    colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272']

    for cat, color in zip(categories, colors):
        fig_hist.add_trace(go.Scatter(
            x=hist_dates, y=cumulative_flows[cat],
            mode='lines', name=cat, stackgroup='one',
            line=dict(width=0.5, color=color),
            hovertemplate=f'{cat}: %{{y:.1f}}亿<extra></extra>'
        ))

    total_flow = np.sum([cumulative_flows[cat] for cat in categories], axis=0)
    fig_hist.add_trace(go.Scatter(
        x=hist_dates, y=total_flow,
        mode='lines', name='总计', line=dict(color='black', width=2, dash='dash'),
        hovertemplate='总计: %{y:.1f}亿<extra></extra>'
    ))

    fig_hist.update_layout(
        title='各类别 ETF 累计资金流向 (30交易日)',
        height=400, hovermode='x unified',
        yaxis=dict(title='累计净流入 (亿元)'),
        legend=dict(orientation='h', y=1.05),
    )
    st.plotly_chart(fig_hist, use_container_width=True)

    # ── 每日净流变化 ──
    st.markdown("#### 每日净流入/流出变化")
    daily_df = pd.DataFrame({
        '日期': hist_dates,
        '当日净流(亿)': np.random.normal(2, 10, len(hist_dates)),
    })
    daily_df['颜色'] = daily_df['当日净流(亿)'].apply(lambda x: '#cf1322' if x > 0 else '#389e0d')

    fig_daily = go.Figure()
    fig_daily.add_trace(go.Bar(
        x=daily_df['日期'], y=daily_df['当日净流(亿)'],
        marker_color=daily_df['颜色'],
        name='日净流',
    ))
    fig_daily.add_hline(y=0, line_dash="solid", line_color="gray")
    fig_daily.update_layout(height=300, margin=dict(t=20))
    st.plotly_chart(fig_daily, use_container_width=True)

    st.caption("以上为模拟历史数据展示。接入实盘数据后可展示真实历史趋势。")

# ═══════════════════════════════════════════════════════════════
# Tab 3: 告警中心
# ═══════════════════════════════════════════════════════════════
with tab3:
    st.subheader("🚨 实时告警中心")

    # ── 告警规则配置 ──
    with st.expander("⚙️ 告警规则配置"):
        alert_rules = {
            "大额净流入": {"threshold": 50, "unit": "亿", "action": "通知"},
            "大额净流出": {"threshold": -50, "unit": "亿", "action": "通知"},
            "连续3日流入": {"threshold": 3, "unit": "日", "action": "重点关注"},
            "单日涨跌幅超5%": {"threshold": 5, "unit": "%", "action": "告警"},
        }

        for rule_name, rule_config in alert_rules.items():
            ar1, ar2, ar3 = st.columns([2, 1, 1])
            ar1.markdown(f"**{rule_name}**")
            ar2.metric("阈值", f"{rule_config['threshold']} {rule_config['unit']}")
            ar3.markdown(f"操作: {rule_config['action']}")

    # ── 模拟告警列表 ──
    st.subheader("📋 当前告警")
    mock_alerts = [
        {"时间": "14:30", "级别": "🔴 高", "类型": "大额净流入", "ETF": "科创50ETF华夏 (588000)",
         "详情": "净流入 +58.3 亿，疑为国家队加仓", "建议": "关注科技板块机会"},
        {"时间": "13:15", "级别": "🟡 中", "类型": "连续流入", "ETF": "沪深300ETF华泰柏瑞 (510300)",
         "详情": "连续第5日净流入，累计 +45.2 亿", "建议": "宽基指数维持超配"},
        {"时间": "11:00", "级别": "🟢 低", "类型": "涨跌幅异常", "ETF": "银行ETF华宝 (512800)",
         "详情": "单日跌幅 -3.8%，净流出 -12.1 亿", "建议": "关注银行板块风险"},
    ]

    for alert in mock_alerts:
        bg = "#fff2f0" if "高" in alert['级别'] else "#fffbe6" if "中" in alert['级别'] else "#f6ffed"
        border = "#ff4d4f" if "高" in alert['级别'] else "#faad14" if "中" in alert['级别'] else "#52c41a"
        st.markdown(f"""<div style="padding:12px;border-radius:8px;border-left:4px solid {border};
        background:{bg};margin-bottom:8px;">
        <span style="color:{border};font-weight:bold;">{alert['级别']}</span>
        <span style="margin-left:8px;color:#888;">{alert['时间']}</span> —
        <b>{alert['类型']}</b> · {alert['ETF']}<br>
        <div style="font-size:13px;margin-top:4px;">{alert['详情']}</div>
        <div style="font-size:12px;color:#888;">💡 {alert['建议']}</div>
        </div>""", unsafe_allow_html=True)

    st.caption("以上为模拟告警展示。接入实时数据后可触发真实告警。")

    st.markdown("---")
    # ── 邮件/推送通知模拟 ──
    st.subheader("📬 通知渠道")
    nc1, nc2, nc3 = st.columns(3)
    nc1.checkbox("📧 邮件通知", value=True)
    nc2.checkbox("📱 飞书通知", value=False)
    nc3.checkbox("🔔 浏览器推送", value=True)

    if st.button("💾 保存告警配置"):
        render_alert_card("告警配置已保存", "告警配置已保存 (本地)", level="success")
