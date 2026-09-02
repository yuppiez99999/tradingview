"""生产运营中心 — Production Edition 运营件四件套之 Dashboard (T5, 2026-09-02).

只读消费者: Health Score 聚合 JSON + shadow 状态 + 降级日志.
版面五件套 (方案 §4.3): 评分卡 / 五维雷达 / 关键指标 / 异常时间线 / 灰度进度.
"""

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from ui.components.common import inject_global_style
from ui.components.health_center import (
    derive_status_color,
    load_anomaly_timeline,
    load_health_history,
    load_latest_health,
    load_shadow_progress,
)

st.set_page_config(page_title="生产运营中心", page_icon="🏭", layout="wide")
inject_global_style()

st.title("🏭 生产运营中心")
st.caption("v8.7 Production Edition — 系统健康评分 · 异常时间线 · 灰度进度（只读视图）")

_ROOT = Path(_BASE_DIR)
DIM_ORDER = ["model", "data", "trading", "risk", "capital"]
DIM_CN = {"model": "模型", "data": "数据", "trading": "交易", "risk": "风险", "capital": "资金"}


def _score_trend_fig(history: list[dict]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[h["date"] for h in history],
        y=[h["total_score"] for h in history],
        mode="lines+markers",
        name="总分",
        line=dict(color="#0068c9"),
    ))
    fig.add_hline(y=85, line_dash="dot", line_color="green",
                  annotation_text="GREEN ≥85")
    fig.add_hline(y=70, line_dash="dot", line_color="orange",
                  annotation_text="RED <70")
    fig.update_layout(
        height=260, margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(range=[0, 105]), xaxis_title=None,
    )
    return fig


def _radar_fig(latest: dict) -> go.Figure:
    dims = latest.get("dimensions", {})
    labels = [DIM_CN.get(k, k) for k in DIM_ORDER]
    values = [float(dims.get(k, {}).get("score", 0.0)) for k in DIM_ORDER]
    fig = go.Figure(go.Scatterpolar(
        r=values + [values[0]],
        theta=labels + [labels[0]],
        fill="toself",
        line=dict(color="#0068c9"),
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(range=[0, 100], showticklabels=True)),
        height=320, margin=dict(l=60, r=60, t=30, b=30),
        showlegend=False,
    )
    return fig


def _render_score_card(latest: dict) -> None:
    color = derive_status_color(latest.get("status", ""))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("系统健康总分", f"{latest.get('total_score', 0):.1f} / 100")
    c2.metric("状态", str(latest.get("status", "—")))
    c3.metric("数据日期", str(latest.get("date", "—")))
    c4.metric("降级维度", f"{len(latest.get('degraded_dimensions', []))} / 5")
    st.markdown(
        f'<div style="height:6px;border-radius:3px;background:{color};"></div>',
        unsafe_allow_html=True,
    )


def _render_dimensions(latest: dict) -> None:
    dims = latest.get("dimensions", {})
    cols = st.columns(5)
    for i, key in enumerate(DIM_ORDER):
        d = dims.get(key, {})
        degraded = "⚠️ " if d.get("degraded") else ""
        with cols[i]:
            st.markdown(
                f"**{degraded}{DIM_CN.get(key, key)}** · "
                f"{float(d.get('score', 0)):.0f} 分"
                f"（权重 {d.get('weight', 0):.0%}）"
            )
            detail = d.get("detail", {})
            reason = detail.get("reason")
            if reason:
                st.caption(f"原因: {reason}")
            elif key == "data":
                st.caption(f"当日降级条目: {detail.get('entries', '—')}")
            elif key == "capital":
                st.caption(f"交易日数: {detail.get('trading_day_count', '—')}")
            else:
                st.caption("正常")


def _render_metrics(latest: dict, progress: dict) -> None:
    capital_detail = latest.get("dimensions", {}).get("capital", {}).get("detail", {})
    data_detail = latest.get("dimensions", {}).get("data", {}).get("detail", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV (shadow)", f"{capital_detail.get('nav', float('nan')):.4f}"
              if isinstance(capital_detail.get("nav"), (int, float)) else "—")
    c2.metric("shadow 交易日数", progress.get("trading_day_count", "—"))
    c3.metric("当日降级条目", data_detail.get("entries", "—"))
    c4.metric("Sharpe / Alpha / VaR", "—（未接入）")
    st.caption("指标集 v1 范围: 评分 JSON + shadow 状态可得项; 归因/风险产物接入后扩展。")


def _render_timeline(events: list[dict]) -> None:
    if not events:
        st.success("近 7 日无降级/异常记录")
        return
    rows = [
        {
            "时间": str(e.get("ts", ""))[:19],
            "scope": str(e.get("scope", "")),
            "key": str(e.get("key", "")),
        }
        for e in events
    ]
    st.dataframe(rows, use_container_width=True, height=240)


def _render_progress(progress: dict) -> None:
    st.markdown(f"**当前阶段**: {progress.get('current_stage', '—')}"
                f"（已运行 {progress.get('trading_day_count', 0)} 个交易日）")
    cols = st.columns(len(progress["stages"]))
    for col, s in zip(cols, progress["stages"], strict=True):
        with col:
            icon = {"已完成": "✅", "进行中": "🔵", "待切换": "🟣", "未开始": "⬜"}.get(
                s["status"], "⬜"
            )
            st.markdown(f"{icon} **{s['name']}**")
            st.caption(f"{s['target']} · {s['status']}")


# === 渲染主流程 ===
history = load_health_history(_ROOT, days=30)
latest = load_latest_health(_ROOT)

if latest is None:
    st.warning("尚无 Health Score 数据——请先运行 scripts/compute_health_score.py"
               "（计划任务 System_HealthScore 每交易日 17:05 自动产出）。")
    st.stop()

with st.container(border=True):
    st.markdown("### 一、系统健康总评")
    _render_score_card(latest)

left, right = st.columns([3, 2])
with left:
    st.markdown("### 总分趋势（近 30 日）")
    st.plotly_chart(_score_trend_fig(history), use_container_width=True)
with right:
    st.markdown("### 五维雷达")
    st.plotly_chart(_radar_fig(latest), use_container_width=True)

with st.container(border=True):
    st.markdown("### 二、五维明细")
    _render_dimensions(latest)

with st.container(border=True):
    st.markdown("### 三、关键指标")
    _render_metrics(latest, load_shadow_progress(_ROOT))

with st.container(border=True):
    st.markdown("### 四、异常时间线（近 7 日降级记录）")
    _render_timeline(load_anomaly_timeline(_ROOT, days=7))

with st.container(border=True):
    st.markdown("### 五、灰度进度（v9_200w_preset）")
    _render_progress(load_shadow_progress(_ROOT))
