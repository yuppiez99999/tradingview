"""09 Shadow 账户页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - Shadow 准入状态展示
    - 14 天观察期进度
    - 每日 DSR 报告
    - Fail-Fast 监控
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
    load_shadow_dsr,
    load_shadow_state,
)
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
        title="Shadow 账户",
        subtitle="14 天观察期准入流程",
        icon="👁️",
    )

    # ===== 准入状态 =====
    state = load_shadow_state()
    if not state:
        render_empty_state("无 Shadow 准入记录", icon="👁️")
        return

    progress_days = state.get("progress_days", 0)
    target_days = state.get("target_days", 14)
    fail_fast_triggered = state.get("fail_fast_triggered", False)
    stage2_blocked = state.get("stage2_blocked", True)

    # 顶部 KPI
    render_kpi_row([
        {"label": "观察期进度", "value": f"{progress_days}/{target_days} 天", "icon": "📅"},
        {"label": "Fail-Fast",
         "value": "已触发" if fail_fast_triggered else "未触发",
         "delta": "异常" if fail_fast_triggered else "正常",
         "delta_positive": not fail_fast_triggered,
         "icon": "🚨"},
        {"label": "Stage 2 推进",
         "value": "阻塞" if stage2_blocked else "可推进",
         "delta": "Stage 1" if stage2_blocked else "Stage 2",
         "delta_positive": not stage2_blocked,
         "icon": "🚦"},
        {"label": "启动时间", "value": state.get("start_date", "-"), "icon": "🚀"},
    ])

    st.divider()

    # ===== 进度条 =====
    st.subheader("📊 观察期进度")
    progress_pct = progress_days / target_days if target_days > 0 else 0
    st.progress(progress_pct, text=f"{progress_days}/{target_days} 天 ({progress_pct*100:.1f}%)")

    # 准入条件检查
    st.subheader("✅ Stage 2 推进条件")
    conditions = state.get("stage2_conditions", {})
    if conditions:
        for cond_name, cond_value in conditions.items():
            passed = bool(cond_value)
            render_status_metric(
                label=cond_name,
                value="✅ 通过" if passed else "❌ 未达标",
                status="NORMAL" if passed else "CRITICAL",
            )
    else:
        # 默认 6 个推进条件
        default_conditions = [
            ("观察期 >= 14 天", progress_days >= target_days),
            ("Fail-Fast 未触发", not fail_fast_triggered),
            ("DSR >= 5", False),  # 占位, 实际由 DSR 报告填充
            ("年化 >= 15%", False),
            ("回撤 <= 10%", False),
            ("Sharpe CV < 1.0", False),
        ]
        for name, passed in default_conditions:
            render_status_metric(
                label=name,
                value="✅ 通过" if passed else "❌ 未达标",
                status="NORMAL" if passed else "CRITICAL",
            )

    st.info(
        "**HC-4 阻塞机制**: Shadow 14 天观察期未满或任一条件未达标时, Stage 2 推进被阻塞\n\n"
        "**Fail-Fast 触发器**: 单日回撤>3% / 3日累计回撤>5% 立即终止 + 回滚"
    )

    st.divider()

    # ===== Fail-Fast 监控 =====
    st.subheader("🚨 Fail-Fast 触发器")
    col1, col2 = st.columns(2)
    with col1:
        render_status_metric(
            label="单日回撤限额",
            value="3%",
            status="WARNING",
        )
        render_status_metric(
            label="今日回撤",
            value=f"{state.get('today_drawdown', 0)*100:.2f}%",
            status="CRITICAL" if abs(state.get('today_drawdown', 0)) > 0.03 else "NORMAL",
        )
    with col2:
        render_status_metric(
            label="3日累计回撤限额",
            value="5%",
            status="WARNING",
        )
        render_status_metric(
            label="3日累计回撤",
            value=f"{state.get('three_day_drawdown', 0)*100:.2f}%",
            status="CRITICAL" if abs(state.get('three_day_drawdown', 0)) > 0.05 else "NORMAL",
        )

    st.divider()

    # ===== 今日 DSR 报告 =====
    st.subheader("📋 今日 DSR 报告")
    selected_date = st.date_input("选择日期", datetime.now())
    dsr = load_shadow_dsr(selected_date)
    if dsr:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("DSR", f"{dsr.get('dsr', 0):.2f}", delta="达标" if dsr.get('dsr', 0) >= 5 else "未达标")
        with col2:
            st.metric("年化收益", f"{dsr.get('annual_return', 0)*100:.2f}%")
        with col3:
            st.metric("最大回撤", f"{dsr.get('max_drawdown', 0)*100:.2f}%")
        with col4:
            st.metric("Sharpe CV", f"{dsr.get('sharpe_cv', 0):.4f}")

        with st.expander("查看完整 DSR 报告"):
            st.json(dsr)
    else:
        render_empty_state(f"{selected_date.strftime('%Y-%m-%d')} 无 DSR 报告", icon="📋")

    st.divider()

    # ===== 模块列表 =====
    st.subheader("📦 准入模块列表")
    modules = state.get("modules", [])
    if modules:
        try:
            import pandas as pd
            df = pd.DataFrame(modules)
            st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception:
            st.json(modules)
    else:
        st.caption("默认准入模块: T2.1 LLM Router / T2.2 Decision Theories / T2.3 Multi Factor Signal")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
