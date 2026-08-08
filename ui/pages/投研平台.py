# -*- coding: utf-8 -*-
"""投研平台 — 参考 QuantMind Research Platform 设计"""
import sys, os
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import streamlit as st
import pandas as pd
from datetime import datetime
import random

st.title("🔬 投研平台")
st.caption("候选股票池 · 模型打分 · 多周期收益 · 量化筛选与决策辅助")

from ui.components.names import STOCK_NAME_MAP, resolve_name, get_style as get_asset_style
from ui.components.sidebar import render_sidebar
from ui.components.common import inject_global_style

inject_global_style()
render_sidebar()

# ═══════════════════════════════════════════════════════════════
# 模拟研究数据
# ═══════════════════════════════════════════════════════════════
@st.cache_data(ttl=300)
def load_research_data():
    candidates = []
    for code, name in list(STOCK_NAME_MAP.items())[:40]:
        score = round(random.uniform(0.55, 0.95), 2)
        candidates.append({
            "代码": code,
            "名称": name,
            "模型分": score,
            "1日收益%": round(random.uniform(-3, 3), 2),
            "5日收益%": round(random.uniform(-8, 8), 2),
            "20日收益%": round(random.uniform(-15, 15), 2),
            "60日收益%": round(random.uniform(-25, 25), 2),
            "风格": get_asset_style(code)[0],
        })
    return pd.DataFrame(candidates)


df = load_research_data()

# ═══════════════════════════════════════════════════════════════
# 顶部筛选区
# ═══════════════════════════════════════════════════════════════
st.subheader("🔎 量化筛选")
f1, f2, f3 = st.columns(3)
with f1:
    min_score = st.slider("最低模型分", 0.0, 1.0, 0.7, 0.05)
with f2:
    style_filter = st.multiselect(
        "风格筛选",
        options=sorted(df["风格"].unique().tolist()),
        default=sorted(df["风格"].unique().tolist()),
    )
with f3:
    batch = st.selectbox("批次", ["全部", "批次A", "批次B", "批次C"], index=0)

filtered = df[
    (df["模型分"] >= min_score)
    & (df["风格"].isin(style_filter))
].copy()

st.markdown(f"当前筛选结果：**{len(filtered)}** 只标的")

# ═══════════════════════════════════════════════════════════════
# 候选池总览
# ═══════════════════════════════════════════════════════════════
st.subheader("📋 候选股票池")
if not filtered.empty:
    st.dataframe(
        filtered.sort_values("模型分", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("没有符合当前筛选条件的标的")

# ═══════════════════════════════════════════════════════════════
# 模型分排行
# ═══════════════════════════════════════════════════════════════
st.subheader("🧠 模型分排行")
if not filtered.empty:
    top = filtered.sort_values("模型分", ascending=False).head(15)
    st.bar_chart(top.set_index("名称")["模型分"], use_container_width=True)

# ═══════════════════════════════════════════════════════════════
# 多周期收益对比
# ═══════════════════════════════════════════════════════════════
st.subheader("📈 多周期收益对比")
if not filtered.empty:
    periods = ["1日收益%", "5日收益%", "20日收益%", "60日收益%"]
    selected = st.multiselect(
        "选择展示标的",
        options=filtered["名称"].tolist(),
        default=filtered.sort_values("模型分", ascending=False).head(8)["名称"].tolist(),
    )
    if selected:
        sel_df = filtered[filtered["名称"].isin(selected)][["名称"] + periods].set_index("名称")
        st.dataframe(sel_df, use_container_width=True)
        st.bar_chart(sel_df.T, use_container_width=True)
