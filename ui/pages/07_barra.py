# -*- coding: utf-8 -*-
"""07 Barra 因子归因页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 详细展示 Barra 10 因子归因
    - 风险分解 (主动风险 / 因子风险 / 特异性风险)
    - 信息比率分解
    - 行业因子贡献
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
        title="Barra 因子归因",
        subtitle="Barra 10 风格因子 + 8 行业因子 PnL 拆分",
        icon="📈",
    )

    selected_date = st.date_input("选择日期", datetime.now())

    panel = load_attribution_panel(selected_date)
    if not panel:
        render_empty_state("无归因数据", icon="📈")
        return

    factor = panel.get("factor", {})
    if not factor or factor.get("status") in ("disabled", "feature_flag_disabled", "empty"):
        render_empty_state(
            f"Barra 因子归因未生成 (status={factor.get('status', 'empty') if factor else 'empty'})",
            icon="📈",
        )
        return

    # ===== 风险分解 =====
    st.subheader("📊 风险分解")
    render_kpi_row([
        {"label": "主动风险 (TE)", "value": f"{factor.get('active_risk', 0)*100:.2f}%", "icon": "📏"},
        {"label": "因子风险", "value": f"{factor.get('factor_risk', 0)*100:.2f}%", "icon": "🎯"},
        {"label": "特异性风险", "value": f"{factor.get('specific_risk', 0)*100:.2f}%", "icon": "🔍"},
        {"label": "信息比率 (IR)", "value": f"{factor.get('information_ratio', 0):.4f}", "icon": "📡"},
    ])

    st.divider()

    # ===== 10 风格因子贡献 =====
    st.subheader("🎨 10 风格因子贡献")
    style_factors = ["Size", "Beta", "Momentum", "Residual Volatility",
                     "Non-linear Size", "Book-to-Price", "Liquidity",
                     "Earnings Yield", "Growth", "Leverage"]

    factors_list = factor.get("factors", [])
    style_factor_data = []
    if isinstance(factors_list, list):
        for f in factors_list:
            if isinstance(f, dict) and f.get("name") in style_factors:
                style_factor_data.append(f)

    if style_factor_data:
        try:
            import pandas as pd
            df = pd.DataFrame(style_factor_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 因子贡献柱状图
            if "name" in df.columns and "pnl_contribution" in df.columns:
                st.subheader("因子 PnL 贡献分布")
                chart_data = df.set_index("name")["pnl_contribution"]
                st.bar_chart(chart_data)
        except Exception as e:
            st.warning(f"风格因子渲染失败: {e}")
            st.json(style_factor_data)
    else:
        render_empty_state("无 10 风格因子数据", icon="🎨")

    st.divider()

    # ===== 行业因子贡献 =====
    st.subheader("🏭 行业因子贡献")
    industry_factors = []
    if isinstance(factors_list, list):
        for f in factors_list:
            if isinstance(f, dict) and f.get("name") not in style_factors:
                industry_factors.append(f)

    if industry_factors:
        try:
            import pandas as pd
            df = pd.DataFrame(industry_factors)
            st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"行业因子渲染失败: {e}")
            st.json(industry_factors)
    else:
        render_empty_state("无行业因子数据", icon="🏭")

    st.divider()

    # ===== 公式说明 =====
    with st.expander("📖 Barra 风险分解公式"):
        st.markdown("""
**主动风险分解**:

$$\\sigma_A^2 = \\sigma_F^2 + \\sigma_S^2$$

其中:
- $\\sigma_A$ = 主动风险 (Tracking Error)
- $\\sigma_F$ = 因子风险贡献
- $\\sigma_S$ = 特异性风险

**信息比率 (Information Ratio)**:

$$IR = \\frac{\\alpha}{\\sigma_A}$$

**10 风格因子**: Size / Beta / Momentum / Residual Volatility / Non-linear Size / 
Book-to-Price / Liquidity / Earnings Yield / Growth / Leverage

**8 行业因子**: 基于申万一级行业分类
""")

    # ===== 原始数据 =====
    with st.expander("查看原始 JSON"):
        st.json(factor)


if __name__ == "__main__" or "streamlit" in __file__:
    main()
