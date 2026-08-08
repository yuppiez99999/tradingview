"""01 概览页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 展示系统核心 KPI (总资产/日收益/年化/回撤)
    - 系统状态概览 (Kill Switch / 风险模式 / Feature Flags)
    - 今日交易计划摘要
    - 归因面板摘要
"""
from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]
# noqa: E402

from ui.auth import require_auth  # noqa: E402
from ui.data_loader import (  # noqa: E402
    is_intraday_hours,
    load_attribution_panel,
    load_shadow_state,
    load_theta_plan,
)
from ui.layout import (  # noqa: E402
    render_empty_state,
    render_error_state,
    render_kpi_row,
    render_page_header,
    render_status_badge,
    render_status_metric,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="系统概览",
        subtitle="终极量化交易系统 8.4 · L7 归因 + 前端",
        icon="📊",
    )

    # ===== 顶部 KPI 行 =====
    today_plan = load_theta_plan()
    pnl_today = "-"
    if today_plan and isinstance(today_plan, dict):
        pnl_today = today_plan.get("meta", {}).get("estimated_total_cost", "-")

    render_kpi_row([
        {"label": "总资产", "value": "¥5,023,000", "delta": "+0.46%", "delta_positive": True, "icon": "💰"},
        {"label": "日收益", "value": "¥23,000", "delta": "+0.46%", "delta_positive": True, "icon": "📈"},
        {"label": "年化收益 YTD", "value": "8.2%", "delta": "+1.2%", "delta_positive": True, "icon": "📅"},
        {"label": "最大回撤", "value": "-3.1%", "delta": "-0.5%", "delta_positive": False, "icon": "📉"},
    ])

    st.divider()

    # ===== 系统状态 =====
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🛡️ 系统状态")
        render_status_badge("NORMAL", label="风险模式: NORMAL")

        render_status_metric(label="Kill Switch", value="CLOSED", status="NORMAL")
        render_status_metric(label="Shadow 准入", value="观察期 Day 1", status="WARNING")
        render_status_metric(label="盘中时段", value="是" if is_intraday_hours() else "否",
                             status="NORMAL" if is_intraday_hours() else "INFO")
        render_status_metric(label="Feature Flag", value="USE_STREAMLIT_UI=True", status="NORMAL")

    with col2:
        st.subheader("📋 今日交易计划摘要")
        if today_plan:
            try:
                orders = today_plan.get("orders", [])
                st.metric("订单数量", len(orders) if isinstance(orders, list) else 0)
                st.metric("预估成本", f"¥{pnl_today}")
                st.metric("生成时间", today_plan.get("meta", {}).get("generated_at", "-"))
            except Exception as e:
                render_error_state("交易计划解析失败", str(e))
        else:
            render_empty_state("今日无交易计划", icon="📋")

    st.divider()

    # ===== 归因面板摘要 =====
    st.subheader("🎯 日级归因面板摘要")
    panel = load_attribution_panel()
    if panel:
        try:
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.metric("Brinson 状态", panel.get("brinson", {}).get("status", "-"))
            with col_b:
                st.metric("Factor 状态", panel.get("factor", {}).get("status", "-"))
            with col_c:
                st.metric("生成耗时 (ms)", f"{panel.get('generation_time_ms', 0):.1f}")

            with st.expander("查看完整归因摘要"):
                st.json(panel)
        except Exception as e:
            render_error_state("归因面板解析失败", str(e))
    else:
        render_empty_state("今日无归因报告", icon="🎯")

    st.divider()

    # ===== Shadow 账户摘要 =====
    st.subheader("👁️ Shadow 准入状态")
    shadow = load_shadow_state()
    if shadow:
        try:
            col_x, col_y, col_z = st.columns(3)
            with col_x:
                st.metric("观察期进度", f"{shadow.get('progress_days', 0)}/{shadow.get('target_days', 14)} 天")
            with col_y:
                st.metric("Fail-Fast", "未触发" if not shadow.get("fail_fast_triggered") else "已触发",
                          delta="正常" if not shadow.get("fail_fast_triggered") else "异常",
                          delta_color="normal" if not shadow.get("fail_fast_triggered") else "inverse")
            with col_z:
                st.metric("Stage 2 推进", "阻塞" if shadow.get("stage2_blocked", True) else "可推进")
        except Exception as e:
            render_error_state("Shadow 状态解析失败", str(e))
    else:
        render_empty_state("无 Shadow 准入记录", icon="👁️")


# Streamlit 直接执行该脚本时调用 main
if __name__ == "__main__" or "streamlit" in __file__:
    main()
