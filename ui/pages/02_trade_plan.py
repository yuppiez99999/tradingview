# -*- coding: utf-8 -*-
"""02 交易计划页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 展示今日 Theta 交易计划
    - 支持查看历史计划
    - 订单明细表
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]

from ui.auth import require_auth
from ui.data_loader import load_theta_plan
from ui.layout import (
    render_empty_state,
    render_page_header,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="交易计划",
        subtitle="Theta 引擎生成的当日交易指令",
        icon="📋",
    )

    # 日期选择
    col_date, col_btn = st.columns([3, 1])
    with col_date:
        selected_date = st.date_input("选择日期", datetime.now())
    with col_btn:
        st.toggle("查看历史计划", value=False)

    # 加载计划
    plan = load_theta_plan(selected_date)
    if not plan:
        render_empty_state(f"{selected_date.strftime('%Y-%m-%d')} 无交易计划", icon="📋")
        return

    # 计划元信息
    st.subheader("📋 计划元信息")
    meta = plan.get("meta", {}) if isinstance(plan, dict) else {}
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("生成时间", meta.get("generated_at", "-"))
    with col2:
        st.metric("订单数", len(plan.get("orders", [])) if isinstance(plan.get("orders"), list) else 0)
    with col3:
        st.metric("预估总成本", f"¥{meta.get('estimated_total_cost', 0):.2f}")
    with col4:
        st.metric("预估滑点 (bps)", f"{meta.get('estimated_slippage_bps', 0):.2f}")

    st.divider()

    # 订单明细
    st.subheader("📝 订单明细")
    orders = plan.get("orders", [])
    if orders and isinstance(orders, list):
        st.dataframe(orders, use_container_width=True, hide_index=True)
    else:
        render_empty_state("无订单记录", icon="📝")

    # 原始 JSON
    with st.expander("查看原始 JSON"):
        st.json(plan)


if __name__ == "__main__" or "streamlit" in __file__:
    main()
