"""10 回测页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - V9 基线回测指标展示
    - 回测报告浏览
    - Sharpe / DSR / 回撤 / 年化等核心指标
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
from ui.data_loader import list_files, read_text  # noqa: E402
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
        title="回测",
        subtitle="V9 基线 + LGB Enhanced 回测报告",
        icon="🔬",
    )

    # ===== V9 基线指标 =====
    st.subheader("📊 V9 基线指标 (生产基线)")
    st.caption("评估标准: DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0")

    render_kpi_row([
        {"label": "DSR", "value": "8", "delta": "达标 (>=5)", "delta_positive": True, "icon": "🎯"},
        {"label": "年化收益", "value": "19.62%", "delta": "达标 (>=15%)", "delta_positive": True, "icon": "📈"},
        {"label": "最大回撤", "value": "-9.95%", "delta": "达标 (<=10%)", "delta_positive": True, "icon": "📉"},
        {"label": "Sharpe CV", "value": "0.88", "delta": "达标 (<1.0)", "delta_positive": True, "icon": "📡"},
    ])

    st.success("✅ V9 Regime-Specific LGB 模型全部达标, 当前生产基线 (2026-07-25 确认)")

    st.divider()

    # ===== LGB Enhanced 报告列表 =====
    st.subheader("📝 LGB Enhanced 回测报告")
    lgb_reports = list_files("lgb_enhanced/lgb_enhanced_report_*.md")
    if lgb_reports:
        selected_report = st.selectbox(
            "选择报告",
            options=lgb_reports,
            format_func=lambda p: p.name,
        )
        if selected_report:
            content = read_text(selected_report, default="")
            if content:
                st.markdown(content)
            else:
                render_empty_state("报告内容为空", icon="📄")
    else:
        render_empty_state("无 LGB Enhanced 报告", icon="📝")

    st.divider()

    # ===== 流水线状态 =====
    st.subheader("🔄 流水线状态")
    pipeline_states = list_files("vibe_trading/*/pipeline_state.json")[:10]
    if pipeline_states:
        try:
            import pandas as pd
            rows = []
            for p in pipeline_states:
                rows.append({"文件": p.name, "路径": str(p.parent)})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"流水线状态渲染失败: {e}")
    else:
        render_empty_state("无流水线状态记录", icon="🔄")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
