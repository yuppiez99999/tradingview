# -*- coding: utf-8 -*-
"""06 Brinson 归因页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 详细展示 Brinson-Fachler 三效应
    - 配置/选股/交互效应分解
    - 与基准对比 (沪深300/中证500)
    - 行业归因明细
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
from ui.data_loader import load_attribution_panel
from ui.layout import (
    render_empty_state,
    render_kpi_row,
    render_page_header,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="Brinson 归因",
        subtitle="Brinson-Fachler 配置/选股/交互效应分解",
        icon="⚖️",
    )

    # 基准选择
    col_bench, col_date = st.columns(2)
    with col_bench:
        benchmark = st.selectbox("基准", ["沪深300", "中证500", "中证1000"], index=0)
    with col_date:
        selected_date = st.date_input("选择日期", datetime.now())

    panel = load_attribution_panel(selected_date)
    if not panel:
        render_empty_state("无归因数据", icon="⚖️")
        return

    brinson = panel.get("brinson", {})
    if not brinson or brinson.get("status") in ("disabled", "feature_flag_disabled", "empty"):
        render_empty_state(
            f"Brinson 归因未生成 (status={brinson.get('status', 'empty') if brinson else 'empty'})",
            icon="⚖️",
        )
        return

    # ===== Brinson 数学恒等式验证 =====
    st.subheader("📐 Brinson 数学恒等式")
    st.latex(r"\sum_{i} (AR_i + SR_i + IR_i) = R_p - R_b")

    ar = brinson.get("allocation_return", 0)
    sr = brinson.get("selection_return", 0)
    ir = brinson.get("interaction_return", 0)
    total_active = ar + sr + ir

    render_kpi_row([
        {"label": "配置效应 AR", "value": f"{ar*100:.4f}%", "icon": "📊"},
        {"label": "选股效应 SR", "value": f"{sr*100:.4f}%", "icon": "🎯"},
        {"label": "交互效应 IR", "value": f"{ir*100:.4f}%", "icon": "🔄"},
        {"label": "主动收益合计", "value": f"{total_active*100:.4f}%", "icon": "Σ"},
    ])

    st.divider()

    # ===== 行业明细 =====
    st.subheader("🏭 行业归因明细")
    sectors = brinson.get("sectors", [])
    if sectors:
        try:
            import pandas as pd
            df = pd.DataFrame(sectors)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 行业贡献柱状图
            if "allocation_return" in df.columns:
                st.subheader("行业配置贡献分布")
                chart_data = df.set_index(df.columns[0])["allocation_return"]
                st.bar_chart(chart_data)
        except Exception as e:
            st.warning(f"行业明细渲染失败: {e}")
            st.json(sectors)
    else:
        render_empty_state("无行业明细数据", icon="🏭")

    st.divider()

    # ===== 公式说明 =====
    with st.expander("📖 Brinson-Fachler 公式说明"):
        st.markdown("""
**配置效应 (Allocation Return, AR)**:

$$AR_i = (w_{p,i} - w_{b,i}) \\times (R_{b,i} - R_b)$$

衡量组合在行业 i 上超配/低配带来的收益贡献。

**选股效应 (Selection Return, SR)**:

$$SR_i = w_{b,i} \\times (R_{p,i} - R_{b,i})$$

衡量组合在行业 i 内选股能力带来的超额收益。

**交互效应 (Interaction Return, IR)**:

$$IR_i = (w_{p,i} - w_{b,i}) \\times (R_{p,i} - R_{b,i})$$

衡量配置与选股同时偏离基准的联合效应。

**数学恒等式**:

$$\\sum_{i} (AR_i + SR_i + IR_i) = R_p - R_b$$
""")

    # ===== 原始数据 =====
    with st.expander("查看原始 JSON"):
        st.json(brinson)


if __name__ == "__main__" or "streamlit" in __file__:
    main()
