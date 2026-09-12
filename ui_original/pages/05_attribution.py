"""05 归因面板页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 展示 T5.3 日级归因面板 JSON
    - 三合一归因 (Brinson + Barra + TCA)
    - 支持查看 Markdown 报告
    - 容错降级: 子模块异常不阻塞整体
"""

from __future__ import annotations

import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]

# noqa: E402
from ui.auth import require_auth  # noqa: E402
from ui.data_loader import (  # noqa: E402
    load_attribution_markdown,
    load_attribution_panel,
)
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
        title="归因面板",
        subtitle="Brinson + Barra + TCA 三合一归因",
        icon="🎯",
    )

    # 日期选择
    selected_date = st.date_input("选择日期", now_bj())

    panel = load_attribution_panel(selected_date)
    if not panel:
        render_empty_state(
            f"{selected_date.strftime('%Y-%m-%d')} 无归因报告", icon="🎯"
        )
        return

    # ===== 顶部 KPI =====
    render_kpi_row(
        [
            {
                "label": "生成耗时 (ms)",
                "value": f"{panel.get('generation_time_ms', 0):.1f}",
                "icon": "⏱️",
            },
            {
                "label": "Brinson 状态",
                "value": panel.get("brinson", {}).get("status", "-"),
                "icon": "⚖️",
            },
            {
                "label": "Factor 状态",
                "value": panel.get("factor", {}).get("status", "-"),
                "icon": "📈",
            },
            {
                "label": "TCA 状态",
                "value": panel.get("tca", {}).get("status", "-"),
                "icon": "💰",
            },
        ]
    )

    st.divider()

    # ===== 三大归因模块标签页 =====
    tab_brinson, tab_factor, tab_tca, tab_md = st.tabs(
        [
            "⚖️ Brinson 归因",
            "📈 Barra 因子归因",
            "💰 TCA 执行归因",
            "📄 Markdown 报告",
        ]
    )

    # Brinson 归因
    with tab_brinson:
        brinson = panel.get("brinson", {})
        if brinson and brinson.get("status") not in (
            "disabled",
            "feature_flag_disabled",
            "empty",
        ):
            st.subheader("配置效应 / 选股效应 / 交互效应")
            ar = brinson.get("allocation_return", 0)
            sr = brinson.get("selection_return", 0)
            ir = brinson.get("interaction_return", 0)
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("配置效应 (AR)", f"{ar*100:.4f}%")
            with col2:
                st.metric("选股效应 (SR)", f"{sr*100:.4f}%")
            with col3:
                st.metric("交互效应 (IR)", f"{ir*100:.4f}%")
            with col4:
                st.metric("主动收益 (R_p-R_b)", f"{(ar+sr+ir)*100:.4f}%")

            # 行业明细
            sectors = brinson.get("sectors", [])
            if sectors:
                st.subheader("行业明细")
                st.dataframe(sectors, use_container_width=True, hide_index=True)
        else:
            status = brinson.get("status", "empty") if brinson else "empty"
            render_empty_state(f"Brinson 归因未生成 (status={status})", icon="⚖️")

    # Barra 因子归因
    with tab_factor:
        factor = panel.get("factor", {})
        if factor and factor.get("status") not in (
            "disabled",
            "feature_flag_disabled",
            "empty",
        ):
            st.subheader("因子风险分解")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(
                    "主动风险 (Tracking Error)",
                    f"{factor.get('active_risk', 0)*100:.2f}%",
                )
            with col2:
                st.metric("因子风险贡献", f"{factor.get('factor_risk', 0)*100:.2f}%")
            with col3:
                st.metric("特异性风险", f"{factor.get('specific_risk', 0)*100:.2f}%")

            # 因子明细
            factors = factor.get("factors", [])
            if factors:
                st.subheader("因子贡献明细")
                st.dataframe(factors, use_container_width=True, hide_index=True)
        else:
            status = factor.get("status", "empty") if factor else "empty"
            render_empty_state(f"Barra 因子归因未生成 (status={status})", icon="📈")

    # TCA 执行归因
    with tab_tca:
        tca = panel.get("tca", {})
        if tca and tca.get("status") not in (
            "disabled",
            "feature_flag_disabled",
            "empty",
        ):
            st.subheader("执行成本分解")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("总成本 (bps)", f"{tca.get('total_cost_bps', 0):.2f}")
            with col2:
                st.metric("佣金 (bps)", f"{tca.get('commission_bps', 0):.2f}")
            with col3:
                st.metric("滑点 (bps)", f"{tca.get('slippage_bps', 0):.2f}")
            with col4:
                st.metric("市场冲击 (bps)", f"{tca.get('market_impact_bps', 0):.2f}")

            fills = tca.get("fills", [])
            if fills:
                st.subheader("成交明细")
                st.dataframe(fills, use_container_width=True, hide_index=True)
        else:
            status = tca.get("status", "empty") if tca else "empty"
            render_empty_state(f"TCA 执行归因未生成 (status={status})", icon="💰")

    # Markdown 报告
    with tab_md:
        md_text = load_attribution_markdown(selected_date)
        if md_text:
            st.markdown(md_text)
        else:
            render_empty_state("无 Markdown 报告", icon="📄")

    # ===== 原始 JSON =====
    with st.expander("查看原始 JSON"):
        st.json(panel)


if __name__ == "__main__" or "streamlit" in __file__:
    main()
