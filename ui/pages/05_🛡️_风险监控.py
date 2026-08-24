"""风险监控 v2.0 — 止损止盈 + VaR + 相关性矩阵 + 最大回撤曲线 + 集中度风险"""
import json
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="风险监控", page_icon="🛡️", layout="wide")
st.title("🛡️ 风险监控 v2.0")
st.caption("止损止盈状态 + VaR风险度量 + 相关性矩阵 + 最大回撤 — 全方位动态风控")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import (
    render_alert_card,
    render_kpi_row,
    render_status_card,
)

inject_global_style()

mod = get_system_module()

# ── 配置路径 ──
STOP_LOSS_CONFIG = os.path.join(_BASE_DIR, 'config', 'rebalance_stop_loss_v43.json')
POSITIONS_FILE = os.path.join(_BASE_DIR, 'config', 'positions.json')

@st.cache_data(ttl=30)
def load_stop_loss_rules():
    if not os.path.exists(STOP_LOSS_CONFIG):
        return []
    with open(STOP_LOSS_CONFIG, encoding='utf-8') as f:
        return json.load(f)

@st.cache_data(ttl=30)
def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {}
    with open(POSITIONS_FILE, encoding='utf-8') as f:
        return json.load(f)

rules = load_stop_loss_rules()
positions = load_positions()
pos_data = positions.get('positions', {})
prices_data = positions.get('prices', {})

# ── 侧边栏 ──
with st.sidebar:
    st.subheader("🎛️ 风控操作")
    if st.button("🔄 重新同步止损规则", use_container_width=True):
        try:
            strategy_registry = mod.StrategyRegistry()
            engine = mod.ExcelDrivenRebalancingEngineV4(strategy_registry=strategy_registry)
            engine.load_all()
            rules = engine.sync_to_stop_loss_monitor()
            st.success(f"已同步 {len(rules)} 条")
            st.rerun()
        except Exception as e:
            st.error(f"同步失败: {e}")

    if not rules and st.button("📂 从再平衡引擎加载", use_container_width=True):
        try:
            strategy_registry = mod.StrategyRegistry()
            engine = mod.ExcelDrivenRebalancingEngineV4(strategy_registry=strategy_registry)
            engine.load_all()
            if engine.is_loaded:
                rules = engine.get_stop_loss_rules()
                st.success(f"已加载 {len(rules)} 条规则")
        except Exception as e:
            st.error(f"加载失败: {e}")

    st.divider()
    st.download_button(
        "📥 导出风险报告", "",
        file_name=f"风险监控_{datetime.now():%Y%m%d}.md",
        mime="text/markdown", use_container_width=True,
    )

# ── 主区域: Tab切换 ──
tab1, tab2, tab3, tab4 = st.tabs(["📋 止损止盈状态", "📊 VaR & 风险度量", "🔗 相关性矩阵", "📉 回撤分析"])

# ═══════════════════════════════════════════════════════════════
# Tab 1: 止损止盈状态
# ═══════════════════════════════════════════════════════════════
with tab1:
    if not rules:
        render_alert_card("规则缺失", "未找到止损止盈规则。请在左侧面板同步规则。", level="warning")
        st.stop()

    status_data = []
    for rule in rules:
        code = str(rule.get('code', ''))
        name = rule.get('name', code)
        sl_price = rule.get('stop_loss_price', 0)
        tp_price = rule.get('take_profit_price', 0)
        sl_pct = rule.get('stop_loss_pct', -0.15) * 100
        tp_pct = rule.get('take_profit_pct', 0.40) * 100

        current = prices_data.get(code, 0)
        if not current:
            pdata = pos_data.get(code, {})
            current = pdata.get('avg_cost', 0)

        status = "🟢 安全"
        alert_level = 0
        if sl_price and current > 0:
            diff_sl_pct = (current - sl_price) / sl_price * 100 if sl_price else 0
            if current <= sl_price:
                status = "🔴 触及止损"
                alert_level = 3
            elif diff_sl_pct < 5:
                status = "🟡 接近止损"
                alert_level = 1
            elif diff_sl_pct < 10:
                status = "🟠 关注"
                alert_level = 0
        if tp_price and current > 0 and current >= tp_price:
            status = "🔵 触及止盈"
            alert_level = 2

        status_data.append({
            "名称": name, "代码": code,
            "当前价": f"￥{current:.2f}" if current else "未知",
            "止损价": f"￥{sl_price:.2f}" if sl_price else "-",
            "距止损": f"{((current - sl_price)/sl_price*100):+.1f}%" if sl_price and current else "-",
            "止盈价": f"￥{tp_price:.2f}" if tp_price else "-",
            "距止盈": f"{((tp_price - current)/current*100):+.1f}%" if tp_price and current else "-",
            "状态": status,
            "风险权重": rule.get('risk_weight', 0),
            "_alert": alert_level,
            "_current_raw": current,
            "_sl_raw": sl_price,
        })

    status_df = pd.DataFrame(status_data)

    def color_status(val):
        if '触及止损' in val:
            return 'background-color: #fff2f0; color: #ff4d4f; font-weight: bold'
        elif '接近止损' in val:
            return 'background-color: #fffbe6; color: #faad14'
        elif '触及止盈' in val:
            return 'background-color: #e6f7ff; color: #1890ff'
        elif '关注' in val:
            return 'background-color: #fff7e6; color: #fa8c16'
        return 'background-color: #f6ffed; color: #52c41a'

    styled = status_df.style.map(color_status, subset=['状态'])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── 统计卡片 ──
    alerts = [s for s in status_data if '触及' in s['状态']]
    warnings = [s for s in status_data if '接近' in s['状态']]
    attention = [s for s in status_data if '关注' in s['状态'] and '触及' not in s['状态']]
    safe = [s for s in status_data if '安全' in s['状态']]

    render_kpi_row([
        ("🔴 触发止损/止盈", str(len(alerts)), "需立即处理" if alerts else "无"),
        ("🟡 接近止损", str(len(warnings)), ""),
        ("🟠 需关注", str(len(attention)), ""),
        ("🟢 安全", str(len(safe)), ""),
        ("📊 总标的", str(len(status_data)), ""),
    ], cols=5)

    # ── 风险热度图 ──
    st.subheader("🌡️ 风险热度分布")
    heat_data = []
    for d in status_data:
        raw_c = d['_current_raw']
        raw_sl = d['_sl_raw']
        if raw_c > 0 and raw_sl > 0:
            heat_data.append({
                "名称": d['名称'],
                "距止损%": round((raw_c - raw_sl) / raw_sl * 100, 1),
                "警报级别": d['_alert'],
            })

    if heat_data:
        heat_df = pd.DataFrame(heat_data).set_index('名称')
        fig = px.bar(heat_df, x=heat_df.index, y='距止损%',
                     color='距止损%', color_continuous_scale=['red', 'orange', 'yellow', 'green'],
                     title='各标的距止损线距离 (%)')
        fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="止损线")
        fig.add_hline(y=5, line_dash="dot", line_color="orange", annotation_text="警戒线")
        fig.update_layout(height=350, margin=dict(t=40))
        st.plotly_chart(fig, use_container_width=True)

    # ── 风险权重分布饼图 ──
    if status_data:
        pos_weights = [(d['名称'], d['风险权重']) for d in status_data if d['风险权重'] > 0]
        if pos_weights:
            fig2 = px.pie(names=[w[0] for w in pos_weights],
                         values=[w[1] for w in pos_weights],
                         title='风险权重分布', hole=0.4)
            fig2.update_layout(height=350, margin=dict(t=40))
            st.plotly_chart(fig2, use_container_width=True)

# ═══════════════════════════════════════════════════════════════
# Tab 2: VaR & 风险度量
# ═══════════════════════════════════════════════════════════════
with tab2:
    st.subheader("📊 VaR 风险度量 (Value at Risk)")

    # ── 基于持仓权重的VaR估算 ──
    weights = []
    names_list = []
    volatilities = []
    prices_list = []

    for rule in rules:
        code = str(rule.get('code', ''))
        name = rule.get('name', code)
        w = float(rule.get('risk_weight', rule.get('position_weight', 0)))
        if w <= 0:
            continue
        current = prices_data.get(code, 0)
        if not current:
            pdata = pos_data.get(code, {})
            current = pdata.get('avg_cost', 0)

        names_list.append(name)
        weights.append(w)
        prices_list.append(current)
        # 根据风控类型估算波动率: 科技30-40%, 能源25-35%, 制造28-38% 等
        risk_level = rule.get('risk_level', 'medium')
        vol_map = {'high': 0.38, 'medium': 0.28, 'low': 0.18}
        volatilities.append(vol_map.get(risk_level, 0.28))

    if weights:
        total_weight = sum(weights)
        norm_weights = [w / total_weight for w in weights]

        # 组合波动率估算 (简化 — 假设相关度0.5)
        rho = 0.5
        port_var = sum(norm_weights[i]**2 * volatilities[i]**2 for i in range(len(weights)))
        for i in range(len(weights)):
            for j in range(i+1, len(weights)):
                port_var += 2 * norm_weights[i] * norm_weights[j] * volatilities[i] * volatilities[j] * rho
        port_vol = np.sqrt(port_var)

        # VaR (95%置信度, 1天)
        from scipy import stats
        z_95 = 1.645  # 95%置信度
        var_1d_pct = port_vol * z_95  # 1天VaR百分比
        var_5d_pct = port_vol * z_95 * np.sqrt(5)  # 5天VaR
        var_20d_pct = port_vol * z_95 * np.sqrt(20)  # 月度VaR

        # 条件VaR (CVaR / Expected Shortfall)
        cvar_1d_pct = port_vol * stats.norm.pdf(z_95) / 0.05

        total_value = 2000000  # 组合总价值 (可从positions读取)

        st.markdown("#### 组合 VaR 测算 (基于持仓权重 × 波动率)")
        vc1, vc2, vc3, vc4, vc5 = st.columns(5)
        vc1.metric("组合波动率 (年化)", f"{port_vol*100:.1f}%")
        vc2.metric("VaR (1日, 95%)", f"{var_1d_pct*100:.1f}%", delta=f"￥{var_1d_pct*total_value:,.0f}")
        vc3.metric("VaR (5日, 95%)", f"{var_5d_pct*100:.1f}%", delta=f"￥{var_5d_pct*total_value:,.0f}")
        vc4.metric("VaR (月度, 95%)", f"{var_20d_pct*100:.1f}%", delta=f"￥{var_20d_pct*total_value:,.0f}")
        vc5.metric("CVaR (1日)", f"{cvar_1d_pct*100:.1f}%", delta=f"预期尾部损失 ￥{cvar_1d_pct*total_value:,.0f}")

        # ──波动率贡献柱状图 ──
        st.markdown("#### 各标的波动率贡献")
        vol_contrib = pd.DataFrame({
            "名称": names_list,
            "权重(%)": [w * 100 for w in norm_weights],
            "波动率(%)": [v * 100 for v in volatilities],
            "风险贡献(%)": [norm_weights[i] * volatilities[i] * 100 for i in range(len(weights))],
        }).sort_values("风险贡献(%)", ascending=True)
        vol_contrib = vol_contrib.set_index("名称")

        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(y=vol_contrib.index, x=vol_contrib['风险贡献(%)'],
                                orientation='h', name='风险贡献', marker_color='#ff7a45'))
        fig_vol.add_trace(go.Bar(y=vol_contrib.index, x=vol_contrib['权重(%)'],
                                orientation='h', name='持仓权重', marker_color='#91d5ff', opacity=0.7))
        fig_vol.update_layout(barmode='overlay', height=400, margin=dict(t=20))
        st.plotly_chart(fig_vol, use_container_width=True)

        # ── 置信度曲线 ──
        st.markdown("#### 不同置信度下的 VaR")
        conf_levels = [0.90, 0.95, 0.975, 0.99]
        z_scores = [1.28, 1.645, 1.96, 2.33]
        var_data = []
        for cl, z in zip(conf_levels, z_scores, strict=True):
            var_pct = port_vol * z
            var_data.append({
                "置信度": f"{cl*100:.0f}%",
                "VaR(1日%)": f"{var_pct*100:.1f}%",
                "VaR(1日金额)": f"￥{var_pct*total_value:,.0f}",
                "z值": z,
            })
        st.dataframe(pd.DataFrame(var_data), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════
# Tab 3: 相关性矩阵
# ═══════════════════════════════════════════════════════════════
with tab3:
    st.subheader("🔗 标的间相关性矩阵 (估算)")

    # 基于行业分类估算相关性
    industry_map = {}
    for rule in rules:
        code = str(rule.get('code', ''))
        name = rule.get('name', code)
        w = float(rule.get('risk_weight', rule.get('position_weight', 0)))
        if w <= 0:
            continue
        industry_map[code] = rule.get('action_type', '科技')

    codes = list(industry_map.keys())
    names = [rule.get('name', c) for c in codes for rule in rules if rule.get('code') == c]

    if len(codes) >= 2:
        # 构建估算的相关矩阵 (同行业=0.8, 近行业=0.6, 不同=0.25)
        industry_groups = {
            '科技': ['科技成长', 'AI', '半导体', '新能源'],
            '能源': ['能源转型', '石化', '化工', '煤化工'],
            '制造': ['高端制造', '机器人', '电网'],
            '金融': ['金融', '证券', '银行'],
            '医药': ['医药', '医疗'],
        }

        def est_corr(i1, i2):
            if i1 == i2:
                return 1.0
            for grp in industry_groups.values():
                if i1 in grp and i2 in grp:
                    return 0.75
            return 0.25

        names_list_unique = []
        seen = set()
        for rule in rules:
            code = str(rule.get('code', ''))
            if code not in seen and code in codes:
                names_list_unique.append(rule.get('name', code))
                seen.add(code)

        n = len(names_list_unique) if len(names_list_unique) == len(codes) else len(codes)
        corr_matrix = np.eye(n)
        for i in range(n):
            for j in range(i+1, n):
                c = est_corr(list(industry_map.values())[i] if i < len(industry_map) else '科技',
                            list(industry_map.values())[j] if j < len(industry_map) else '科技')
                corr_matrix[i][j] = corr_matrix[j][i] = c

        display_names = names_list_unique[:n] if n <= len(names_list_unique) else list(industry_map.keys())[:n]

        fig_corr = go.Figure(data=go.Heatmap(
            z=corr_matrix, x=display_names, y=display_names,
            colorscale='RdBu_r', zmid=0.5, text=np.round(corr_matrix, 2),
            texttemplate='%{text}', textfont={"size": 10},
            colorbar=dict(title="相关系数"),
        ))
        fig_corr.update_layout(height=max(400, n * 35), margin=dict(t=20))
        st.plotly_chart(fig_corr, use_container_width=True)

        # ── 集中度风险 ──
        st.subheader("🏗️ 集中度风险分析")
        # HHI (Herfindahl-Hirschman Index)
        if weights:
            hhi = sum((w/sum(weights)*100)**2 for w in weights)
            hhi_level = "高度集中" if hhi > 2500 else "中度集中" if hhi > 1500 else "分散"
            hhi_color = "#ff4d4f" if hhi > 2500 else "#faad14" if hhi > 1500 else "#52c41a"

            conc1, conc2, conc3 = st.columns(3)
            conc1.metric("HHI 指数", f"{hhi:.0f}", delta=hhi_level)
            conc2.metric("有效持仓数", f"{1/(sum((w/sum(weights))**2 for w in weights)):.1f}")
            conc3.metric("最大单权重", f"{max(norm_weights)*100:.1f}%")

            if hhi > 2500:
                render_alert_card("集中度风险", "组合集中度偏高，建议增加不同行业标的分散风险", level="warning")
            elif hhi > 1500:
                render_status_card("集中度状态", "组合集中度适中，可适当增加差异化资产", level="info")
            else:
                render_status_card("分散度良好", "组合分散度良好", level="success")

    else:
        st.info("至少需要2个标的才能计算相关性矩阵")

# ═══════════════════════════════════════════════════════════════
# Tab 4: 回撤分析
# ═══════════════════════════════════════════════════════════════
with tab4:
    st.subheader("📉 最大回撤分析")

    # ── 模拟回撤曲线 ──
    # 从持仓价格历史估算 (简化: 使用当前价格 ± 随机波动)
    dates = pd.date_range(end=datetime.now(), periods=60, freq='B')

    if status_data:
        # 选一个基准标的生成模拟NAV
        base_price = 1.0
        np.random.seed(42)
        nav = [base_price]
        for _i in range(1, 60):
            ret = np.random.normal(0.0005, 0.015)  # 日收益 N(0.05%, 1.5%)
            nav.append(nav[-1] * (1 + ret))

        nav_series = pd.Series(nav, index=dates)

        # 计算回撤
        rolling_max = nav_series.cummax()
        drawdown = (nav_series - rolling_max) / rolling_max * 100

        # ── 回撤曲线图 ──
        fig_dd = go.Figure()

        # NAV曲线
        fig_dd.add_trace(go.Scatter(x=dates, y=nav_series, mode='lines',
                                     name='组合净值', line=dict(color='#1890ff', width=2),
                                     yaxis='y'))

        # 回撤填充
        fig_dd.add_trace(go.Scatter(x=dates, y=drawdown, mode='lines',
                                     name='回撤(%)', line=dict(color='#ff4d4f', width=1),
                                     fill='tozeroy', fillcolor='rgba(255,77,79,0.15)',
                                     yaxis='y2'))

        fig_dd.update_layout(
            title='组合净值与回撤曲线 (模拟数据)',
            yaxis=dict(title='净值', side='left'),
            yaxis2=dict(title='回撤(%)', overlaying='y', side='right',
                       range=[min(drawdown)*1.2, 5]),
            height=450, margin=dict(t=50), hovermode='x unified',
            legend=dict(orientation='h', y=1.1),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

        # ── 回撤统计 ──
        max_dd = drawdown.min()
        max_dd_date = drawdown.idxmin()
        days_to_max_dd = (max_dd_date - dates[0]).days

        # 恢复天数 (假设恢复时间 = 回撤幅度 * 20天)
        recovery_days = int(abs(max_dd) * 20)

        dd1, dd2, dd3, dd4 = st.columns(4)
        dd1.metric("最大回撤", f"{max_dd:.2f}%", delta="模拟数据" if abs(max_dd) < 20 else "⚠️ 超出阈值")
        dd2.metric("回撤峰值日期", max_dd_date.strftime('%Y-%m-%d'))
        dd3.metric("到达峰值天数", f"{days_to_max_dd} 天")
        dd4.metric("预计恢复天数", f"{recovery_days} 天")

        # ── 回撤分布统计 ──
        dd_bins = pd.cut(drawdown, bins=[-40, -30, -20, -10, -5, 0],
                         labels=['-40%~-30%', '-30%~-20%', '-20%~-10%', '-10%~-5%', '-5%~0%'])
        dd_dist = dd_bins.value_counts().sort_index()

        st.markdown("#### 回撤区间分布")
        dist_df = pd.DataFrame({
            "回撤区间": dd_dist.index.tolist(),
            "天数": dd_dist.values.tolist(),
            "占比": [f"{v/len(drawdown)*100:.1f}%" for v in dd_dist.values],
        })
        # 水平条形图
        fig_dist = px.bar(dist_df, x='天数', y='回撤区间', orientation='h',
                         color='天数', color_continuous_scale='Reds',
                         title='回撤区间分布 (60个交易日)')
        fig_dist.update_layout(height=250, margin=dict(t=40))
        st.plotly_chart(fig_dist, use_container_width=True)

        # ── 止损线标记 ──
        st.markdown("---")
        st.markdown("#### 止损/止盈线设定指南")
        sl_guide = pd.DataFrame([
            {"风险偏好": "保守", "单只止损": "-8%", "单只止盈": "+25%", "组合止损": "-12%"},
            {"风险偏好": "中性", "单只止损": "-15%", "单只止盈": "+40%", "组合止损": "-20%"},
            {"风险偏好": "进取", "单只止损": "-20%", "单只止盈": "+50%", "组合止损": "-25%"},
        ])
        st.dataframe(sl_guide, use_container_width=True, hide_index=True)
