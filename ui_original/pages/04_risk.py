"""04 风险页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 风险指标 (VaR / CVaR / Beta / Vol)
    - 回撤监控
    - 风险总线事件审计
    - Kill Switch 状态
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
from ui.data_loader import load_risk_bus_events  # noqa: E402
from ui.layout import (  # noqa: E402
    render_empty_state,
    render_kpi_row,
    render_page_header,
    render_status_metric,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="风险监控",
        subtitle="VaR / CVaR / 回撤 / Kill Switch",
        icon="🛡️",
    )

    # ===== 风险 KPI =====
    render_kpi_row(
        [
            {
                "label": "VaR 95%",
                "value": "-1.8%",
                "delta": "-0.2%",
                "delta_positive": False,
                "icon": "📊",
            },
            {
                "label": "CVaR 95%",
                "value": "-2.5%",
                "delta": "-0.3%",
                "delta_positive": False,
                "icon": "📉",
            },
            {
                "label": "组合 Beta",
                "value": "0.45",
                "delta": "目标≤0.3",
                "delta_positive": False,
                "icon": "⚖️",
            },
            {
                "label": "年化波动率",
                "value": "12.3%",
                "delta": "+0.5%",
                "delta_positive": False,
                "icon": "📈",
            },
        ]
    )

    st.divider()

    # ===== 回撤监控 =====
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📉 回撤监控")
        render_status_metric(label="当前回撤", value="-3.1%", status="NORMAL")
        render_status_metric(label="历史最高水位", value="¥5,180,000", status="INFO")
        render_status_metric(label="防御线", value="-10%", status="WARNING")
        render_status_metric(label="熔断线", value="-14%", status="CRITICAL")
        render_status_metric(label="Kill Switch", value="CLOSED", status="NORMAL")

    with col2:
        st.subheader("⚖️ 风险限额")
        render_status_metric(label="单日回撤限额", value="3%", status="WARNING")
        render_status_metric(label="3日累计回撤限额", value="5%", status="WARNING")
        render_status_metric(label="MAX_DRAWDOWN_LIMIT", value="15%", status="CRITICAL")
        render_status_metric(label="目标年化波动率", value="15%", status="INFO")
        render_status_metric(label="回撤去杠杆阈值", value="5%", status="WARNING")

    st.divider()

    # ===== 风险总线事件 =====
    st.subheader("📡 风险总线事件 (今日)")
    events = load_risk_bus_events()
    if events:
        st.metric("事件数量", len(events))

        # 按事件类型分组
        try:
            import pandas as pd

            df = pd.DataFrame(events)
            if "event_type" in df.columns:
                st.subheader("事件类型分布")
                type_counts = df["event_type"].value_counts()
                st.bar_chart(type_counts)

            with st.expander("查看事件明细"):
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"事件渲染失败: {e}")
            st.json(events[:10])  # 仅显示前 10 条
    else:
        render_empty_state("今日无风险事件", icon="📡")

    st.divider()

    # ===== Kill Switch 状态 =====
    st.subheader("🚨 Kill Switch 状态")
    col_ks1, col_ks2, col_ks3 = st.columns(3)
    with col_ks1:
        st.metric("状态", "CLOSED")
    with col_ks2:
        st.metric("触发次数", 0)
    with col_ks3:
        st.metric("最后检查", "刚刚")

    st.info(
        "**Kill Switch 同步路径延迟约束**: <1ms (HC-6 硬约束)\n\n"
        "**RiskBus 故障隔离**: 总线故障不影响 KillSwitch 主路径 (try-except 包裹)"
    )


if __name__ == "__main__" or "streamlit" in __file__:
    main()
