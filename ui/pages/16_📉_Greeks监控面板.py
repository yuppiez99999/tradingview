"""
Greeks 监控面板 v1.0

功能:
  - 组合级 Greeks 总览（Delta / Gamma / Theta / Vega / Rho）
  - 按标的分布与告警
  - 按到期日 Greeks 明细
  - 数据源: options_positions.json / futures_options_scanner / 演示数据
"""

import os
import sys
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Greeks 监控面板", page_icon="📉", layout="wide")
st.title("📉 Greeks 监控面板")
st.caption("期权组合敏感性监控 — Delta / Gamma / Theta / Vega / Rho")

from ui.components.common import inject_global_style
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

# ============================================================
# 数据层
# ============================================================


@st.cache_data(ttl=30)
def _load_option_contracts() -> list:
    try:
        from quant_modules.greeks_calculator import (
            build_demo_contracts,
            load_positions_for_greeks,
        )

        contracts = load_positions_for_greeks()
        if not contracts:
            contracts = build_demo_contracts(
                {
                    "510300": 3.8,
                    "510050": 2.7,
                    "000300": 3850.0,
                }
            )
        return contracts
    except Exception as e:
        st.warning(f"Greeks 数据加载失败，使用空数据: {e}")
        return []


@st.cache_data(ttl=30)
def _compute_portfolio(contracts):
    try:
        from quant_modules.greeks_calculator import (
            aggregate_portfolio_greeks,
            greeks_to_dataframe,
        )

        portfolio = aggregate_portfolio_greeks(contracts)
        summary_df = greeks_to_dataframe(portfolio)
        return portfolio, summary_df
    except Exception as e:
        st.error(f"Greeks 计算失败: {e}")
        return {}, pd.DataFrame()


contracts = _load_option_contracts()
portfolio, summary_df = _compute_portfolio(contracts)

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.subheader("🎛️ Greeks 操作")
    if st.button("🔄 刷新 Greeks", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("**数据说明**")
    st.caption(
        "- 优先读取 `config/options_positions.json`\n- 空文件时使用演示持仓\n- Greeks 基于 Black-Scholes 计算"
    )

# ============================================================
# 顶部 KPI
# ============================================================
if not summary_df.empty:
    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    total_delta = summary_df["Delta"].sum()
    total_gamma = summary_df["Gamma"].sum()
    total_theta = summary_df["Theta"].sum()
    total_vega = summary_df["Vega"].sum()
    total_rho = summary_df["Rho"].sum()
    kpi1.metric("组合 Delta", f"{total_delta:+.2f}")
    kpi2.metric("组合 Gamma", f"{total_gamma:+.2f}")
    kpi3.metric("组合 Theta/日", f"{total_theta:+.2f}")
    kpi4.metric("组合 Vega", f"{total_vega:+.2f}")
    kpi5.metric("组合 Rho", f"{total_rho:+.2f}")

    # 告警汇总
    warnings = []
    for ul, pg in portfolio.items():
        warnings.extend([f"**{ul}**: {w}" for w in pg.warnings])
    if warnings:
        render_alert_card(" Greeks 告警", "\n".join(warnings), level="warning")
    else:
        render_status_card(
            " Greeks 状态", "当前组合 Greeks 暴露处于常规区间", level="success"
        )
else:
    st.info(
        "暂无 Greeks 数据，请先配置 `config/options_positions.json`，或等待演示数据加载。"
    )

# ============================================================
# 主区域: Tab 切换
# ============================================================
tab1, tab2, tab3 = st.tabs(["📋 组合 Greeks", "📊 分布与暴露", "🗓️ 到期日明细"])

with tab1:
    if summary_df.empty:
        st.stop()

    st.markdown("#### 按标的 Greeks 汇总")
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # 下载
    csv = summary_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 下载 Greeks 汇总",
        data=csv,
        file_name=f"greeks_summary_{datetime.now():%Y%m%d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

with tab2:
    if summary_df.empty:
        st.stop()

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### Delta 分布")
        fig_delta = px.bar(
            summary_df,
            x="标的",
            y="Delta",
            color="Delta",
            color_continuous_scale=["red", "yellow", "green"],
        )
        fig_delta.update_layout(height=350, margin=dict(t=40))
        st.plotly_chart(fig_delta, use_container_width=True)

    with col_b:
        st.markdown("##### Vega 分布")
        fig_vega = px.bar(
            summary_df,
            x="标的",
            y="Vega",
            color="Vega",
            color_continuous_scale=["red", "yellow", "green"],
        )
        fig_vega.update_layout(height=350, margin=dict(t=40))
        st.plotly_chart(fig_vega, use_container_width=True)

    st.markdown("##### Gamma vs Theta 风险散点")
    fig_gt = px.scatter(
        summary_df,
        x="Gamma",
        y="Theta",
        size="净张数",
        color="标的",
        hover_data=["标的", "Delta", "Vega"],
    )
    fig_gt.update_layout(height=400, margin=dict(t=40))
    st.plotly_chart(fig_gt, use_container_width=True)

with tab3:
    if summary_df.empty:
        st.stop()

    selected = st.selectbox("选择标的查看到期日明细", options=list(portfolio.keys()))
    pg = portfolio.get(selected)
    if pg:
        expiry_df = pd.DataFrame(
            [
                {
                    "到期日": exp,
                    "Delta": round(bucket.get("delta", 0.0), 4),
                    "Gamma": round(bucket.get("gamma", 0.0), 4),
                    "Theta": round(bucket.get("theta", 0.0), 4),
                    "Vega": round(bucket.get("vega", 0.0), 4),
                    "净张数": bucket.get("contracts", 0),
                }
                for exp, bucket in sorted(pg.exposure_by_expiry.items())
            ]
        )
        st.dataframe(expiry_df, use_container_width=True, hide_index=True)

        if not expiry_df.empty:
            fig_exp = go.Figure()
            for col in ["Delta", "Gamma", "Theta", "Vega"]:
                fig_exp.add_trace(
                    go.Bar(x=expiry_df["到期日"], y=expiry_df[col], name=col)
                )
            fig_exp.update_layout(
                barmode="group",
                height=380,
                margin=dict(t=40),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_exp, use_container_width=True)
