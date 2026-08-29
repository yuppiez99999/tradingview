"""
量化策略系统 v5.10 — Streamlit 多页面 UI 主入口
"""

import os
import sys

# 确保主项目目录在 path 中
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import streamlit as st

st.set_page_config(
    page_title="量化策略系统 v5.10",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 多页面导航 — 参考 QuantMind 的功能分组与前台能力
pg = st.navigation(
    {
        "📊 智能仪表盘": [
            st.Page("pages/01_🏠_系统概览.py", title="系统概览"),
            st.Page("pages/02_📊_实时监控.py", title="实时监控"),
            st.Page("pages/12_📝_报告管理.py", title="报告管理"),
        ],
        "🔄 交易执行": [
            st.Page("pages/03_🔄_再平衡执行.py", title="再平衡执行"),
            st.Page("pages/14_🔗_对冲再平衡联动.py", title="对冲联动"),
        ],
        "📈 策略分析": [
            st.Page("pages/04_📈_投资组合优化.py", title="组合优化"),
            st.Page("pages/06_💰_ETF资金流向.py", title="ETF资金流向"),
            st.Page("pages/11_💎_大宗商品监控.py", title="大宗商品"),
        ],
        "🛡️ 风险管理": [
            st.Page("pages/05_🛡️_风险监控.py", title="风险监控"),
            st.Page("pages/16_📉_Greeks监控面板.py", title="Greeks 监控"),
        ],
        "🔬 宏观研究": [
            st.Page("pages/07_🌊_康波周期分析.py", title="康波周期"),
            st.Page("pages/08_🏛️_十五五规划.py", title="十五五规划"),
            st.Page("pages/09_🏦_社保基金追踪.py", title="社保追踪"),
            st.Page("pages/10_🔬_宏观综合分析.py", title="宏观综合"),
        ],
        "🤖 AI 投研": [
            st.Page("pages/投研平台.py", title="投研平台"),
            st.Page("pages/13_🤖_AI分析师.py", title="AI分析师"),
            st.Page("pages/15_🏆_AI交易竞技场.py", title="交易竞技场"),
        ],
    }
)

pg.run()
