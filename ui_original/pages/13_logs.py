"""13 系统日志页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - LLM 路由调用审计
    - 策略注册审计事件
    - 数据质量报告
    - 风险总线事件
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]

# noqa: E402
from ui.auth import require_auth  # noqa: E402
from ui.data_loader import (  # noqa: E402
    load_llm_router_calls,
    load_recent_data_quality,
    load_risk_bus_events,
    load_strategy_registry_events,
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
        title="系统日志",
        subtitle="LLM 路由 / 策略注册 / 数据质量 / 风险事件",
        icon="📝",
    )

    selected_date = st.date_input("选择日期", datetime.now())

    # 顶部 KPI
    llm_calls = load_llm_router_calls(selected_date)
    risk_events = load_risk_bus_events(selected_date)
    strategy_events = load_strategy_registry_events()
    data_quality_reports = load_recent_data_quality(n=5)

    render_kpi_row(
        [
            {"label": "LLM 调用数", "value": str(len(llm_calls)), "icon": "🤖"},
            {"label": "风险事件数", "value": str(len(risk_events)), "icon": "📡"},
            {"label": "策略注册事件", "value": str(len(strategy_events)), "icon": "📦"},
            {
                "label": "数据质量报告",
                "value": str(len(data_quality_reports)),
                "icon": "✨",
            },
        ]
    )

    st.divider()

    # ===== Tab 分类展示 =====
    tab_llm, tab_risk, tab_strategy, tab_dq = st.tabs(
        [
            f"🤖 LLM 路由 ({len(llm_calls)})",
            f"📡 风险事件 ({len(risk_events)})",
            f"📦 策略注册 ({len(strategy_events)})",
            f"✨ 数据质量 ({len(data_quality_reports)})",
        ]
    )

    # LLM 路由审计
    with tab_llm:
        if llm_calls:
            try:
                import pandas as pd

                df = pd.DataFrame(llm_calls)
                st.dataframe(df, use_container_width=True, hide_index=True)

                # 按 provider 分组统计
                if "provider" in df.columns:
                    st.subheader("按 Provider 分组")
                    provider_stats = (
                        df.groupby("provider")
                        .agg(
                            总调用数=("provider", "count"),
                            成功数=("success", lambda x: sum(1 for v in x if v)),
                            平均延迟_ms=("latency_ms", "mean"),
                        )
                        .reset_index()
                    )
                    st.dataframe(
                        provider_stats, use_container_width=True, hide_index=True
                    )
            except Exception as e:
                st.warning(f"LLM 调用日志渲染失败: {e}")
                st.json(llm_calls[:20])
        else:
            render_empty_state(
                f"{selected_date.strftime('%Y-%m-%d')} 无 LLM 调用记录", icon="🤖"
            )

    # 风险事件
    with tab_risk:
        if risk_events:
            try:
                import pandas as pd

                df = pd.DataFrame(risk_events)
                st.dataframe(df, use_container_width=True, hide_index=True)

                if "event_type" in df.columns:
                    st.subheader("事件类型分布")
                    st.bar_chart(df["event_type"].value_counts())
            except Exception as e:
                st.warning(f"风险事件渲染失败: {e}")
                st.json(risk_events[:20])
        else:
            render_empty_state(
                f"{selected_date.strftime('%Y-%m-%d')} 无风险事件", icon="📡"
            )

    # 策略注册事件
    with tab_strategy:
        if strategy_events:
            try:
                import pandas as pd

                df = pd.DataFrame(strategy_events)
                st.dataframe(df, use_container_width=True, hide_index=True)

                if "action" in df.columns:
                    st.subheader("操作类型分布")
                    st.bar_chart(df["action"].value_counts())
            except Exception as e:
                st.warning(f"策略注册事件渲染失败: {e}")
                st.json(strategy_events[:20])
        else:
            render_empty_state("无策略注册事件", icon="📦")

    # 数据质量报告
    with tab_dq:
        if data_quality_reports:
            for i, report in enumerate(data_quality_reports):
                with st.expander(f"数据质量报告 #{i+1}", expanded=(i == 0)):
                    st.json(report)
        else:
            render_empty_state("无数据质量报告", icon="✨")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
