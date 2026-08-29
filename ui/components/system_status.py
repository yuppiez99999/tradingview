"""系统状态组件 — 统一卡片/徽章/KPI，对齐 QuantMind 前台风格"""

import streamlit as st


def status_badge(available: bool, label: str) -> str:
    """返回状态徽章HTML"""
    return (
        f'<span class="badge badge-success">✅ {label}</span>'
        if available
        else f'<span class="badge badge-error">❌ {label}</span>'
    )


def render_module_grid(modules: dict, cols: int = 4):
    """以统一卡片网格渲染模块可用性状态"""
    items = list(modules.items())
    rows = [items[i : i + cols] for i in range(0, len(items), cols)]

    for row in rows:
        columns = st.columns(cols)
        for i, (name, available) in enumerate(row):
            with columns[i]:
                icon = "✅" if available else "❌"
                status_text = "可用" if available else "不可用"
                border_color = "#bbf7d0" if available else "#fecaca"
                st.markdown(
                    f"""<div class="qm-card" style="border-color: {border_color};">
                    <div class="qm-card-title">{icon} {name}</div>
                    <div class="qm-card-body" style="color: #64748b;">{status_text}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )


def render_connector_status(status: dict):
    """渲染数据源连接器状态"""
    cols = st.columns(4)
    with cols[0]:
        st.metric("活跃连接器", status.get("active_connector") or "None")
    with cols[1]:
        fallback = status.get("fallback_mode", False)
        st.metric("降级模式", "⚠️ 是" if fallback else "✅ 否")
    with cols[2]:
        st.metric("已注册", status.get("total_connectors", 0))
    with cols[3]:
        st.metric("可用", status.get("available_connectors", 0))


def render_kpi_row(metrics: list, cols: int = 4):
    """通用 KPI 行"""
    columns = st.columns(cols)
    for i, (title, value, delta) in enumerate(metrics):
        with columns[i % cols]:
            st.metric(title, value, delta=delta)


def render_alert_card(title: str, message: str, level: str = "info"):
    """统一告警/提示卡片"""
    border_color = {
        "success": "#bbf7d0",
        "warning": "#fde68a",
        "error": "#fecaca",
        "info": "#bfdbfe",
    }.get(level, "#bfdbfe")
    st.markdown(
        f"""<div class="qm-card" style="border-color: {border_color};">
        <div class="qm-card-title">{title}</div>
        <div class="qm-card-body">{message}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_status_card(title: str, status: str, detail: str = "", level: str = "info"):
    """统一状态卡：标题 + 状态 + 详情"""
    badge = {
        "success": '<span class="badge badge-success">正常</span>',
        "warning": '<span class="badge badge-warning">警告</span>',
        "error": '<span class="badge badge-error">异常</span>',
        "info": '<span class="badge badge-info">信息</span>',
    }.get(level, '<span class="badge badge-info">信息</span>')
    border_color = {
        "success": "#bbf7d0",
        "warning": "#fde68a",
        "error": "#fecaca",
        "info": "#bfdbfe",
    }.get(level, "#bfdbfe")
    st.markdown(
        f"""<div class="qm-card" style="border-color: {border_color};">
        <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="qm-card-title" style="margin:0;text-align:left;">{title}</div>
        {badge}
        </div>
        <div class="qm-card-body" style="color: #64748b;">{status}</div>
        <div class="qm-card-body" style="color: #334155;">{detail}</div>
        </div>""",
        unsafe_allow_html=True,
    )
