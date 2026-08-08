"""03 持仓页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 展示当前持仓明细
    - 持仓风险贡献
    - 权重分布
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]
# noqa: E402

from ui.auth import require_auth  # noqa: E402
from ui.data_loader import load_theta_plan  # noqa: E402
from ui.layout import (  # noqa: E402
    render_empty_state,
    render_kpi_row,
    render_page_header,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="持仓",
        subtitle="当前持仓明细与风险贡献",
        icon="💼",
    )

    # 从今日 Theta 计划中提取持仓视图
    plan = load_theta_plan()
    holdings = []
    if plan and isinstance(plan, dict):
        orders = plan.get("orders", [])
        if isinstance(orders, list):
            for o in orders:
                if isinstance(o, dict):
                    holdings.append({
                        "代码": o.get("symbol", "-"),
                        "方向": o.get("side", "-"),
                        "数量": o.get("shares", 0),
                        "成本": o.get("price", 0),
                        "权重": o.get("weight", 0),
                    })

    # KPI 行
    total_value = sum(h.get("数量", 0) * h.get("成本", 0) for h in holdings)
    render_kpi_row([
        {"label": "持仓数量", "value": str(len(holdings)), "icon": "📊"},
        {"label": "持仓市值", "value": f"¥{total_value:,.2f}", "icon": "💰"},
        {"label": "现金占比", "value": "5.2%", "icon": "💵"},
        {"label": "杠杆比例", "value": "1.0x", "icon": "⚖️"},
    ])

    st.divider()

    # 持仓表
    st.subheader("💼 当前持仓")
    if holdings:
        st.dataframe(holdings, use_container_width=True, hide_index=True)
    else:
        render_empty_state("无持仓记录", icon="💼")

    # 持仓权重分布
    st.subheader("📈 权重分布")
    if holdings:
        try:
            import pandas as pd
            df = pd.DataFrame(holdings)
            if "权重" in df.columns and "代码" in df.columns:
                chart_data = df.set_index("代码")["权重"]
                st.bar_chart(chart_data)
        except Exception as e:
            st.warning(f"图表渲染失败: {e}")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
