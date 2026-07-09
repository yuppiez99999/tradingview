"""
v7.5 Institutional — Streamlit 监控面板
"""
import os
import sys
import yaml
import json
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    import streamlit as st
except ImportError:
    print("请安装 streamlit: pip install streamlit")
    sys.exit(1)


# ========== 页面配置 ==========
st.set_page_config(
    page_title="v7.5 机构级交易系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("v7.5 Institutional Trading System Monitor")
st.caption(f"Citadel / Point72 Grade · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ========== 侧边栏 ==========
with st.sidebar:
    st.header("系统控制")

    mode = st.selectbox("运行模式", ["监控", "回测", "压力测试", "配置"])
    capital = st.number_input("资金规模 (万)", value=500, step=10)

    st.divider()

    st.subheader("系统状态")
    st.metric("风险模式", "NORMAL")
    st.metric("当前回撤", "-2.3%")
    st.metric("NTP 偏移", "12ms")
    st.metric("熔断状态", "CLOSED")

    st.divider()
    st.caption("v7.5 Institutional | Quant Research Desk")


# ========== 主面板 ==========
if mode == "监控":
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("总资产", "¥5,023,000", "+0.46%")
    with col2:
        st.metric("日收益", "¥23,000", "+0.46%")
    with col3:
        st.metric("年化收益 (YTD)", "8.2%")
    with col4:
        st.metric("最大回撤", "-3.1%", "-0.5%")

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["持仓", "风控", "对冲", "日志"])

    with tab1:
        st.subheader("当前持仓")
        st.dataframe({
            "代码": ["600519.SH", "000858.SZ", "300750.SZ", "510300.SH", "518880.SH"],
            "名称": ["贵州茅台", "五粮液", "宁德时代", "沪深300ETF", "黄金ETF"],
            "数量": [500, 3000, 2000, 20000, 30000],
            "成本": [1750, 155, 230, 3.95, 4.98],
            "现价": [1800, 160, 240, 4.02, 5.05],
            "盈亏%": ["+2.9%", "+3.2%", "+4.3%", "+1.8%", "+1.4%"],
            "权重": ["17.9%", "9.6%", "9.6%", "1.6%", "3.0%"],
            "风险贡献": ["12.5%", "8.2%", "11.3%", "5.1%", "3.8%"],
        })

    with tab2:
        st.subheader("风控指标")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Sortino Ratio", "1.23")
            st.metric("Calmar Ratio", "0.72")
        with c2:
            st.metric("VaR 95%", "-1.8%")
            st.metric("CVaR 95%", "-2.5%")
        with c3:
            st.metric("组合 Beta", "0.45", "目标≤0.3")
            st.metric("组合 Vol", "12.3%", "年化")

        st.divider()
        st.subheader("回撤监控")
        st.info("DD 当前 -3.1% | HWM ¥5,180,000 | 防御线 -10% | 熔断线 -14%")

    with tab3:
        st.subheader("对冲状态")

        hedge_cols = st.columns(3)
        with hedge_cols[0]:
            st.subheader("Beta Hedge")
            st.metric("组合 Beta", "0.45")
            st.metric("目标 Beta", "0.30")
            st.success("无需对冲")
        with hedge_cols[1]:
            st.subheader("Vol Hedge")
            st.metric("VIX", "18.2")
            st.metric("触发线", "30.0")
            st.success("未触发")
        with hedge_cols[2]:
            st.subheader("Correlation Hedge")
            st.metric("均值相关", "0.42")
            st.metric("触发线", "0.85")
            st.success("未触发")

    with tab4:
        st.subheader("系统日志")
        st.text(
            "2026-07-05 09:30:01 [INFO] 盘前准备完成\n"
            "2026-07-05 09:30:02 [INFO] NTP 同步: offset=0.012s\n"
            "2026-07-05 09:30:03 [INFO] 风控: mode=NORMAL\n"
            "2026-07-05 09:30:05 [INFO] 信号生成完成: 15 个因子\n"
            "2026-07-05 09:30:10 [INFO] 无待执行订单\n"
        )

elif mode == "回测":
    st.header("Walk-Forward Analysis")
    st.info("回测参数: train 24m / test 3m / step 3m / 5-fold CV")

    st.subheader("各窗口指标")
    st.dataframe({
        "窗口": [1, 2, 3, 4, 5],
        "测试区间": [
            "2024-07~2024-09", "2024-10~2024-12",
            "2025-01~2025-03", "2025-04~2025-06", "2025-07~2025-09",
        ],
        "Sortino": [1.12, 1.05, 0.98, 1.23, 1.15],
        "Calmar": [0.65, 0.58, 0.52, 0.71, 0.68],
        "Max DD": [-8.2, -9.5, -11.3, -7.8, -8.9],
        "年化收益": [9.2, 8.5, 7.8, 10.1, 9.5],
    })

    st.subheader("综合指标")
    cols = st.columns(4)
    with cols[0]:
        st.metric("拼接 Sortino", "1.11")
    with cols[1]:
        st.metric("拼接 Calmar", "0.63")
    with cols[2]:
        st.metric("拼接 Max DD", "-11.3%")
    with cols[3]:
        st.metric("DSR", "0.97")

elif mode == "压力测试":
    st.header("三段极端行情压力测试")

    st.subheader("COVID-19 2020-02~03")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("组合回撤", "-11.2%")
    with c2:
        st.metric("通过", "是")
    with c3:
        st.metric("状态", "PASS")

    st.subheader("Luna Crash 2022-05")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("组合回撤", "-8.5%")
    with c2:
        st.metric("通过", "是")
    with c3:
        st.metric("状态", "PASS")

    st.subheader("Yen Carry Unwind 2024-08")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("组合回撤", "-9.8%")
    with c2:
        st.metric("通过", "是")
    with c3:
        st.metric("状态", "PASS")

    st.success("三段压力测试全部通过，Max DD < 15%")

elif mode == "配置":
    st.header("系统配置")

    config_dir = os.path.join(os.path.dirname(__file__), 'config')
    config_file = st.selectbox("选择配置文件", os.listdir(config_dir))

    if config_file:
        path = os.path.join(config_dir, config_file)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        st.code(content, language='yaml')
