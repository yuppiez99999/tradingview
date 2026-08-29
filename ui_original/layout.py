"""Streamlit UI 共享布局组件 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

设计目标:
    1. 统一页头 (Page Header): 标题 + 副标题 + 时间戳
    2. 统一侧边栏 (Sidebar): 导航 + 系统状态 + 用户信息
    3. 状态徽标 (Status Badge): NORMAL/WARNING/CRITICAL 视觉标识
    4. KPI 卡片 (Metric Card): 数值 + 变化 + 颜色
    5. 容器组件 (Container): 卡片/面板/分割线

设计原则:
    - 所有组件均无副作用, 仅渲染 UI
    - 不直接读取文件, 数据通过参数传入
    - 可独立测试 (mock streamlit)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

logger = logging.getLogger("ui.layout")

# ============================================================
# 颜色 / 样式常量
# ============================================================

COLOR_NORMAL = "#28a745"  # 绿
COLOR_WARNING = "#ffc107"  # 黄
COLOR_CRITICAL = "#dc3545"  # 红
COLOR_INFO = "#17a2b8"  # 青
COLOR_MUTED = "#6c757d"  # 灰

STATUS_COLORS = {
    "NORMAL": COLOR_NORMAL,
    "WARNING": COLOR_WARNING,
    "CRITICAL": COLOR_CRITICAL,
    "INFO": COLOR_INFO,
    "DISABLED": COLOR_MUTED,
}


def _get_streamlit():
    """获取 streamlit 模块 (若可用)."""
    try:
        import streamlit as st  # type: ignore[import-not-found]

        return st
    except ImportError:
        return None


# ============================================================
# 页头组件
# ============================================================


def render_page_header(
    title: str,
    subtitle: str = "",
    icon: str = "",
    show_timestamp: bool = True,
) -> None:
    """渲染页面页头.

    Args:
        title: 主标题
        subtitle: 副标题
        icon: emoji 图标
        show_timestamp: 是否显示当前时间戳
    """
    st = _get_streamlit()
    if st is None:
        return

    prefix = f"{icon} " if icon else ""
    st.title(f"{prefix}{title}")
    if subtitle:
        st.caption(subtitle)
    if show_timestamp:
        st.caption(f"⏱️ 数据时点: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.divider()


# ============================================================
# 状态徽标
# ============================================================


def render_status_badge(
    status: str,
    label: str | None = None,
    size: str = "normal",
) -> None:
    """渲染状态徽标.

    Args:
        status: 状态 (NORMAL/WARNING/CRITICAL/INFO/DISABLED)
        label: 自定义标签 (None 时用 status)
        size: 尺寸 (normal/small)
    """
    st = _get_streamlit()
    if st is None:
        return

    color = STATUS_COLORS.get(status.upper(), COLOR_MUTED)
    text = label or status.upper()
    font_size = "0.85em" if size == "small" else "1.0em"
    padding = "2px 8px" if size == "small" else "4px 12px"

    st.markdown(
        f'<span style="background-color:{color}; color:white; '
        f"padding:{padding}; border-radius:4px; font-size:{font_size}; "
        f'font-weight:bold;">{text}</span>',
        unsafe_allow_html=True,
    )


def render_status_metric(label: str, value: str, status: str = "NORMAL") -> None:
    """渲染带状态色的指标.

    Args:
        label: 指标名
        value: 指标值
        status: 状态色 (NORMAL/WARNING/CRITICAL)
    """
    st = _get_streamlit()
    if st is None:
        return

    color = STATUS_COLORS.get(status.upper(), COLOR_NORMAL)
    st.markdown(
        f'<div style="border-left: 4px solid {color}; padding-left: 8px; margin: 4px 0;">'
        f'<div style="color: {COLOR_MUTED}; font-size: 0.8em;">{label}</div>'
        f'<div style="font-size: 1.2em; font-weight: bold; color: {color};">{value}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ============================================================
# KPI 卡片
# ============================================================


def render_kpi_card(
    label: str,
    value: str,
    delta: str | None = None,
    delta_positive: bool = True,
    icon: str = "",
) -> None:
    """渲染 KPI 卡片.

    Args:
        label: 指标名
        value: 指标值
        delta: 变化值 (e.g. "+0.46%")
        delta_positive: 变化是否为正向 (True 绿色, False 红色)
        icon: emoji 图标
    """
    st = _get_streamlit()
    if st is None:
        return

    delta_color = COLOR_NORMAL if delta_positive else COLOR_CRITICAL
    icon_prefix = f"{icon} " if icon else ""

    delta_html = ""
    if delta:
        delta_html = f'<div style="color: {delta_color}; font-size: 0.85em; margin-top: 4px;">{delta}</div>'

    st.markdown(
        f'<div style="background-color: #f8f9fa; padding: 12px; border-radius: 8px; '
        f'border: 1px solid #e9ecef; margin: 4px 0;">'
        f'<div style="color: {COLOR_MUTED}; font-size: 0.8em;">{icon_prefix}{label}</div>'
        f'<div style="font-size: 1.5em; font-weight: bold; color: #212529;">{value}</div>'
        f"{delta_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_kpi_row(items: list) -> None:
    """渲染 KPI 行 (多个卡片并排).

    Args:
        items: [{label, value, delta, delta_positive, icon}, ...]
    """
    st = _get_streamlit()
    if st is None:
        return

    if not items:
        return

    cols = st.columns(len(items))
    for col, item in zip(cols, items, strict=True):
        with col:
            render_kpi_card(
                label=item.get("label", ""),
                value=item.get("value", "-"),
                delta=item.get("delta"),
                delta_positive=item.get("delta_positive", True),
                icon=item.get("icon", ""),
            )


# ============================================================
# 侧边栏组件
# ============================================================


def render_sidebar(
    system_status: str = "NORMAL",
    intraday_mode: bool = False,
    username: str | None = None,
    extra_metrics: list | None = None,
) -> dict[str, Any]:
    """渲染侧边栏.

    Args:
        system_status: 系统状态 (NORMAL/WARNING/CRITICAL)
        intraday_mode: 是否为盘中实时模式
        username: 当前用户名
        extra_metrics: 额外的指标 [{label, value, status}]

    Returns:
        侧边栏配置字典 (e.g. {"intraday_mode": True})
    """
    st = _get_streamlit()
    if st is None:
        return {"intraday_mode": intraday_mode}

    with st.sidebar:
        st.header("🎛️ 系统控制")

        # 系统状态
        st.subheader("系统状态")
        render_status_badge(system_status, size="normal")

        # 盘中实时模式开关
        new_intraday = st.toggle(
            "🔄 盘中实时刷新",
            value=intraday_mode,
            help="启用后盘中时段每分钟自动刷新数据",
        )

        st.divider()

        # 核心指标
        if extra_metrics:
            st.subheader("核心指标")
            for m in extra_metrics:
                render_status_metric(
                    label=m.get("label", ""),
                    value=m.get("value", "-"),
                    status=m.get("status", "NORMAL"),
                )

            st.divider()

        # 用户信息
        if username:
            st.subheader("用户")
            st.write(f"👤 {username}")

        st.divider()
        st.caption("终极量化交易系统 8.4 | L7 归因 + 前端")

    return {"intraday_mode": new_intraday}


# ============================================================
# 通用容器
# ============================================================


def render_info_panel(title: str, body: str, status: str = "INFO") -> None:
    """渲染信息面板.

    Args:
        title: 面板标题
        body: 面板内容 (支持 Markdown)
        status: 状态色 (INFO/NORMAL/WARNING/CRITICAL)
    """
    st = _get_streamlit()
    if st is None:
        return

    color = STATUS_COLORS.get(status.upper(), COLOR_INFO)
    st.markdown(
        f'<div style="border-left: 4px solid {color}; padding: 8px 12px; '
        f'background-color: #f8f9fa; margin: 8px 0; border-radius: 4px;">'
        f'<div style="font-weight: bold; color: {color};">{title}</div>'
        f'<div style="margin-top: 4px;">{body}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render_empty_state(message: str = "暂无数据", icon: str = "📭") -> None:
    """渲染空状态提示.

    Args:
        message: 提示信息
        icon: emoji 图标
    """
    st = _get_streamlit()
    if st is None:
        return

    st.markdown(
        f'<div style="text-align: center; padding: 48px; color: {COLOR_MUTED};">'
        f'<div style="font-size: 3em;">{icon}</div>'
        f'<div style="font-size: 1.1em; margin-top: 12px;">{message}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render_error_state(message: str, detail: str = "") -> None:
    """渲染错误状态.

    Args:
        message: 错误信息
        detail: 详细信息
    """
    st = _get_streamlit()
    if st is None:
        return

    st.error(f"❌ {message}")
    if detail:
        st.caption(detail)


# ============================================================
# 工具函数
# ============================================================


def format_percent(value: Any, decimals: int = 2) -> str:
    """格式化百分比.

    Args:
        value: 数值 (0.046 → "+4.60%")
        decimals: 小数位数

    Returns:
        格式化后的字符串; NaN/Inf/非数值返回 "-"
    """
    try:
        v = float(value)
        if not math.isfinite(v):
            return "-"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v*100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "-"


def format_number(value: Any, decimals: int = 2) -> str:
    """格式化数字.

    使用 Python 默认 banker's rounding (round-half-to-even),
    e.g. 1234.5 → "1,234" (不是 "1,235").

    Args:
        value: 数值
        decimals: 小数位数

    Returns:
        格式化后的字符串; NaN/Inf/非数值返回 "-"
    """
    try:
        v = float(value)
        if not math.isfinite(v):
            return "-"
        return f"{v:,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def format_currency(value: Any, symbol: str = "¥") -> str:
    """格式化货币.

    Args:
        value: 数值
        symbol: 货币符号

    Returns:
        格式化后的字符串; NaN/Inf/非数值返回 "-"
    """
    try:
        v = float(value)
        if not math.isfinite(v):
            return "-"
        return f"{symbol}{v:,.2f}"
    except (TypeError, ValueError):
        return "-"


def get_status_from_value(
    value: Any,
    warning_threshold: float = 0.5,
    critical_threshold: float = 0.8,
    higher_is_worse: bool = True,
) -> str:
    """根据数值返回状态色.

    Args:
        value: 数值 (0.0-1.0)
        warning_threshold: 警告阈值
        critical_threshold: 危险阈值
        higher_is_worse: True 时数值越高越危险

    Returns:
        NORMAL / WARNING / CRITICAL
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "DISABLED"

    if higher_is_worse:
        if v >= critical_threshold:
            return "CRITICAL"
        if v >= warning_threshold:
            return "WARNING"
        return "NORMAL"
    else:
        # 数值越低越危险
        if v <= (1 - critical_threshold):
            return "CRITICAL"
        if v <= (1 - warning_threshold):
            return "WARNING"
        return "NORMAL"


__all__ = [
    # 颜色常量
    "COLOR_NORMAL",
    "COLOR_WARNING",
    "COLOR_CRITICAL",
    "COLOR_INFO",
    "COLOR_MUTED",
    "STATUS_COLORS",
    # 页头
    "render_page_header",
    # 状态徽标
    "render_status_badge",
    "render_status_metric",
    # KPI 卡片
    "render_kpi_card",
    "render_kpi_row",
    # 侧边栏
    "render_sidebar",
    # 容器
    "render_info_panel",
    "render_empty_state",
    "render_error_state",
    # 工具函数
    "format_percent",
    "format_number",
    "format_currency",
    "get_status_from_value",
]
