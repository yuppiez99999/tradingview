"""AI交易竞技场 — v5.10 多策略实时对抗面板 · 中文版"""

import os
import sys

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(page_title="AI交易竞技场", page_icon="🏆", layout="wide")

# ═══════════════════════════════════════════════
# 全局样式 — 与 QuantMind 前台风格对齐
# ═══════════════════════════════════════════════
from ui.components.common import inject_global_style

inject_global_style()

CUSTOM_CSS = """
<style>
/* 全局字体优化 */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* 玻璃态卡片 */
.glass-card {
    background: linear-gradient(135deg, rgba(22, 27, 34, 0.85), rgba(13, 17, 23, 0.9));
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(230, 237, 243, 0.1);
    border-radius: 14px;
    padding: 1rem 1.15rem;
    margin-bottom: 0.65rem;
    box-shadow: 0 2px 24px rgba(0, 0, 0, 0.25);
    transition: all 0.2s ease;
}
.glass-card:hover {
    border-color: rgba(230, 237, 243, 0.2);
    box-shadow: 0 4px 32px rgba(0, 0, 0, 0.35);
}

/* 强调卡片 - 用于P&L等高亮区域 */
.glass-card-accent {
    background: linear-gradient(135deg, rgba(24, 144, 255, 0.08), rgba(139, 92, 246, 0.06));
    border: 1px solid rgba(24, 144, 255, 0.2);
}

/* 指标标题 */
.metric-label {
    font-size: 0.72rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8b949e;
    margin-bottom: 0.2rem;
    font-weight: 500;
}

/* 大数字 */
.metric-big {
    font-size: 2rem;
    font-weight: 800;
    color: #f8fafc;
    line-height: 1.15;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
}

/* 正/负颜色 */
.metric-up {
    color: #22c55e;
    font-weight: 600;
}
.metric-down {
    color: #ef4444;
    font-weight: 600;
}

/* 徽章 */
.badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.22rem 0.65rem;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.badge-live {
    background: rgba(34, 197, 94, 0.12);
    color: #22c55e;
    border: 1px solid rgba(34, 197, 94, 0.3);
}
.badge-live::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #22c55e;
    animation: pulse-dot 1.5s ease-in-out infinite;
}
@keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
}
.badge-primary {
    background: rgba(24, 144, 255, 0.12);
    color: #1890ff;
    border: 1px solid rgba(24, 144, 255, 0.3);
}
.badge-warning {
    background: rgba(245, 158, 11, 0.12);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.3);
}
.badge-purple {
    background: rgba(139, 92, 246, 0.12);
    color: #8b5cf6;
    border: 1px solid rgba(139, 92, 246, 0.3);
}

/* 小标题 */
.section-title {
    font-size: 0.85rem;
    font-weight: 700;
    color: #c9d1d9;
    letter-spacing: 0.05em;
    margin-bottom: 0.6rem;
    display: flex;
    align-items: center;
    gap: 0.45rem;
}

/* 分隔行 */
.stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.3rem 0;
    font-size: 0.82rem;
    color: #8b949e;
}
.stat-row .value {
    color: #f8fafc;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}

/* 强度条 */
.strength-bar {
    height: 6px;
    background: rgba(230, 237, 243, 0.08);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 0.4rem;
}
.strength-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s ease;
}

/* 分割线 */
.hairline {
    margin: 0.5rem 0;
    border-top: 1px solid rgba(230, 237, 243, 0.08);
}

/* 图例 */
.legend-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin-right: 0.4rem;
    vertical-align: middle;
}

/* 滚动条 */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(230, 237, 243, 0.1); border-radius: 3px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════
def _now():
    return now_bj()


def _card(html: str, accent: bool = False):
    cls = "glass-card glass-card-accent" if accent else "glass-card"
    st.markdown(f'<div class="{cls}">{html}</div>', unsafe_allow_html=True)


# (保留备用) 用于未来扩展的区块标题辅助函数
# def _section_title(text: str, icon: str = ""):
#     return f'<div class="section-title">{icon} {text}</div>'


# ═══════════════════════════════════════════════
# 侧边栏 — 竞技场控制
# ═══════════════════════════════════════════════
with st.sidebar:
    st.markdown("### 🎛️ 竞技场控制台")

    symbol = st.selectbox(
        "📌 交易标的",
        [
            "BTC/USDT 5分钟",
            "ETH/USDT 5分钟",
            "沪深300 5分钟",
            "中证1000 5分钟",
            "上证50 5分钟",
        ],
    )
    strategy = st.selectbox(
        "🧠 AI策略",
        ["Mirofish v3", "Kronos v2", "趋势追踪", "AI Hedge Fund 20", "GLM-5 盘中决策"],
    )

    st.divider()
    n_sim = st.slider(
        "📊 模拟交易笔数", 5000, 100000, 36402, 1000, help="控制蒙特卡洛模拟的精度"
    )
    vol_factor = st.slider(
        "📈 波动率因子", 0.1, 3.0, 1.0, 0.05, help="调整价格随机游走波动幅度"
    )

    st.divider()
    st.checkbox("🔄 实时脉冲图", value=True, key="show_pulse")
    st.checkbox("🕸️ 关系网络图", value=True, key="show_graph")
    st.checkbox("📡 概率落点板", value=True, key="show_lattice")

    st.divider()
    st.caption(f"🕐 数据刷新: {_now():%Y-%m-%d %H:%M:%S}")


# ═══════════════════════════════════════════════
# 模拟核心数据
# ═══════════════════════════════════════════════
np.random.seed(42)
total_pnl = 436827.0
realized_pnl = 8974.0
win_rate = 0.71
sharpe = 4.21
n_trades = 36402
top_multiplier = 6.34
btc_price = 75260.0

# ═══════════════════════════════════════════════
# 顶部标题栏
# ═══════════════════════════════════════════════
h_left, h_mid, h_right = st.columns([3, 2.5, 1.5])

with h_left:
    st.markdown(
        '<div style="font-size:0.72rem;letter-spacing:0.12em;color:#8b949e;text-transform:uppercase;margin-bottom:0.15rem;">'  # noqa: E501
        "量化策略系统 v5.10 · AI实时交易竞技场</div>"
        '<div style="display:flex;align-items:center;gap:0.7rem;">'
        '<div style="width:40px;height:40px;border-radius:12px;'
        "background:linear-gradient(135deg,#f59e0b,#8b5cf6);"
        "display:flex;align-items:center;justify-content:center;"
        'font-size:1.2rem;font-weight:800;color:#fff;">🏆</div>'
        '<span style="font-size:1.5rem;font-weight:800;letter-spacing:-0.02em;color:#f8fafc;">'
        "AI FABLE 5 · 米罗鱼</span>"
        "</div>",
        unsafe_allow_html=True,
    )

with h_mid:
    st.markdown(
        '<div style="display:flex;gap:0.5rem;align-items:center;margin-top:1.4rem;">'
        '<span class="badge badge-live">● 实盘运行中</span>'
        '<span class="badge badge-primary">主网</span>'
        '<span class="badge badge-warning">第 7,185 轮</span>'
        '<span class="badge badge-purple">5 策略并行</span>'
        "</div>",
        unsafe_allow_html=True,
    )

with h_right:
    now = _now()
    st.markdown(
        f'<div style="text-align:right;margin-top:0.5rem;">'
        f'<div style="font-size:0.72rem;color:#8b949e;letter-spacing:0.08em;">北京时间</div>'
        f'<div style="font-size:1.25rem;font-weight:700;color:#f8fafc;font-variant-numeric:tabular-nums;">'
        f"{now:%Y-%m-%d %H:%M:%S}</div>"
        f'<div style="font-size:0.72rem;color:#8b949e;">UTC {datetime.now(UTC):%H:%M:%S}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

# ═══════════════════════════════════════════════
# 第一行：总盈亏 + 核心指标
# ═══════════════════════════════════════════════
st.markdown("---")
c1, c2, c3, c4 = st.columns([2.4, 1.0, 1.0, 1.6])

with c1:
    html = (
        '<div class="metric-label">AI FABLE 5 · 累计盈亏 (Total P&L)</div>'
        f'<div class="metric-big">¥{total_pnl:,.0f}</div>'
        '<div style="margin-top:0.55rem;display:flex;gap:1.5rem;font-size:0.82rem;">'
        f'<div><span style="color:#8b949e;">已实现盈亏</span> <span class="metric-up">+¥{realized_pnl:,}</span></div>'
        f'<div><span style="color:#8b949e;">策略来源</span> <span style="color:#f8fafc;font-weight:600;">Mirofish v3</span></div>'  # noqa: E501
        f'<div><span style="color:#8b949e;">交易笔数</span> <span style="color:#f8fafc;font-weight:600;">{n_trades:,}</span></div>'  # noqa: E501
        "</div>"
        '<div style="margin-top:0.5rem;display:flex;gap:1.5rem;font-size:0.82rem;">'
        '<div><span class="metric-up">+¥8,974</span> <span style="color:#8b949e;">5月14日 做多</span></div>'
        '<div><span class="metric-up">+¥6,887</span> <span style="color:#8b949e;">6月4日 做多</span></div>'
        '<div><span class="metric-up">+¥5,062</span> <span style="color:#8b949e;">5月7日 做多</span></div>'
        '<div><span class="metric-up">+¥4,897</span> <span style="color:#8b949e;">5月14日 做空</span></div>'
        "</div>"
    )
    _card(html, accent=True)

with c2:
    _card(
        '<div class="metric-label">胜率</div>'
        f'<div style="font-size:1.75rem;font-weight:700;color:#f8fafc;">{win_rate*100:.0f}%</div>'
        '<div class="metric-up" style="font-size:0.78rem;">Win Rate</div>'
        '<div class="hairline"></div>'
        '<div class="metric-label">盈亏比</div>'
        '<div style="font-size:1.1rem;font-weight:600;color:#f8fafc;">2.83</div>'
    )

with c3:
    _card(
        '<div class="metric-label">夏普比率</div>'
        f'<div style="font-size:1.75rem;font-weight:700;color:#f8fafc;">{sharpe:.2f}</div>'
        '<div class="metric-up" style="font-size:0.78rem;">风险调整后</div>'
        '<div class="hairline"></div>'
        '<div class="metric-label">最大回撤</div>'
        '<div class="metric-down" style="font-size:1.1rem;font-weight:600;">-8.3%</div>'
    )

with c4:
    # 迷你盈亏曲线
    spark_x = np.arange(30)
    spark_y = np.cumsum(np.random.RandomState(99).randn(30) * 180) + 8000
    fig_spark = go.Figure(
        go.Scatter(
            x=spark_x,
            y=spark_y,
            mode="lines",
            fill="tozeroy",
            line=dict(color="#22c55e", width=2.2),
            fillcolor="rgba(34,197,94,0.1)",
        )
    )
    fig_spark.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=90,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    html = (
        '<div class="metric-label">最佳战绩 · BTC · 预测市场</div>'
        '<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<div><div style="font-size:1.75rem;font-weight:700;color:#f8fafc;">×{top_multiplier:.2f}</div>'
        f'<div class="metric-up">+¥{realized_pnl:,}</div></div>'
        "</div>"
    )
    _card(html)
    st.plotly_chart(
        fig_spark,
        use_container_width=True,
        config={"displayModeBar": False},
        key="spark_pnl",
    )

# ═══════════════════════════════════════════════
# 第二行：概率落点板
# ═══════════════════════════════════════════════
if st.session_state.get("show_lattice", True):
    st.markdown("### 📡 概率落点板 · 36,402 笔交易 · 一个面板")
    board_left, board_right = st.columns([1, 2.5])

    with board_left:
        _card(
            '<div class="metric-label">实时收敛统计</div>'
            '<div style="margin-top:0.55rem;">'
            '<div class="stat-row"><span>已落小球数</span><span class="value">0</span></div>'
            '<div class="stat-row"><span>落入盈利区</span><span class="metric-up">100.0%</span></div>'
            '<div class="stat-row"><span>单笔期望收益</span><span class="metric-up">+¥30</span></div>'
            '<div class="stat-row"><span>本轮盈亏</span><span class="metric-up">+¥331</span></div>'
            '<div class="stat-row"><span>历史总计</span><span class="value">36,402</span></div>'
            '<div class="stat-row"><span>累计已实现</span><span class="metric-up">+¥438,000</span></div>'
            "</div>"
            '<div class="hairline"></div>'
            '<div style="font-size:0.72rem;color:#6e7681;line-height:1.5;">'
            "大数定律 — 6倍赔率棋盘需要足够多的重复次数才能收敛。当前策略通过高频微交易积累统计优势。"
            "</div>"
        )

    with board_right:
        # 高尔顿板散点 + 分布直方图
        rs = np.random.RandomState(7)
        bins = np.arange(-12, 13)
        counts = rs.poisson(lam=55, size=len(bins))
        counts[len(bins) // 2 + 1 :] += rs.poisson(
            35, size=len(bins) - len(bins) // 2 - 1
        )

        fig_board = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.65, 0.35],
            vertical_spacing=0.05,
        )
        # 散点
        drop_x = rs.normal(0, 2.8, 350)
        drop_y = rs.uniform(0, 100, 350)
        fig_board.add_trace(
            go.Scatter(
                x=drop_x,
                y=drop_y,
                mode="markers",
                marker=dict(size=3.5, color="rgba(201,209,217,0.3)", symbol="circle"),
                name="落点",
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )
        fig_board.add_vline(
            x=0,
            line=dict(color="#f59e0b", dash="dot", width=1.8),
            annotation_text="均衡线",
            annotation_font_color="#f59e0b",
            row=1,
            col=1,
        )
        # 直方图
        bar_colors = ["#22c55e" if v >= 0 else "#ef4444" for v in bins]
        fig_board.add_trace(
            go.Bar(
                x=bins,
                y=counts,
                marker=dict(color=bar_colors, opacity=0.85),
                name="分布",
            ),
            row=2,
            col=1,
        )
        fig_board.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=10, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            font=dict(color="#c9d1d9", size=11),
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False),
            xaxis2=dict(showgrid=False, zeroline=False, title="收益分布"),
            yaxis2=dict(showgrid=False, zeroline=False, title="频次"),
        )
        st.plotly_chart(
            fig_board,
            use_container_width=True,
            config={"displayModeBar": False},
            key="lattice",
        )

# ═══════════════════════════════════════════════
# 第三行：关系网络图
# ═══════════════════════════════════════════════
if st.session_state.get("show_graph", True):
    st.markdown("### 🕸️ 米罗鱼 · 关系网络仿真")
    g_left, g_right = st.columns([1.5, 2.5])

    with g_left:
        _card(
            '<div class="metric-label">节点分类图例</div>'
            '<div style="margin-top:0.5rem;font-size:0.82rem;color:#c9d1d9;">'
            '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">'
            '<span class="legend-dot" style="background:#ef4444;"></span>空头信号节点</div>'
            '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">'
            '<span class="legend-dot" style="background:#22c55e;"></span>多头信号节点</div>'
            '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">'
            '<span class="legend-dot" style="background:#f59e0b;"></span>中位数路径</div>'
            '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">'
            '<span class="legend-dot" style="background:#8b5cf6;"></span>催化剂事件</div>'
            '<div style="display:flex;align-items:center;gap:0.5rem;">'
            '<span class="legend-dot" style="background:#1890ff;"></span>聚类中心枢纽</div>'
            "</div>"
            '<div class="hairline"></div>'
            '<div class="stat-row"><span>空头路径数</span><span class="value">334</span></div>'
            '<div class="stat-row"><span>多头路径数</span><span class="value">1,069</span></div>'
            '<div class="stat-row"><span>模拟路径总数</span><span class="value">2,047</span></div>'
            '<div class="stat-row"><span>节点 / 边倍数</span><span class="value">312 / 8×</span></div>'
            '<div class="stat-row"><span>置信度</span><span class="metric-up">95.6%</span></div>'
            '<div class="stat-row"><span>预测方向</span><span class="metric-up">▲ 看涨</span></div>'
        )

    with g_right:
        rs = np.random.RandomState(5)
        n_nodes = 80
        x = rs.normal(0, 1, n_nodes)
        y = rs.normal(0, 1, n_nodes)
        sizes = rs.uniform(6, 24, n_nodes)
        colors = rs.choice(
            ["#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#1890ff"], n_nodes
        )

        fig_graph = go.Figure()
        # 边
        edge_idx = rs.choice(n_nodes, size=(70, 2))
        for a, b in edge_idx:
            if a != b:
                fig_graph.add_trace(
                    go.Scatter(
                        x=[x[a], x[b]],
                        y=[y[a], y[b]],
                        mode="lines",
                        line=dict(color="rgba(201,209,217,0.07)", width=0.8),
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
        # 节点
        fig_graph.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="markers",
                marker=dict(
                    size=sizes,
                    color=colors,
                    opacity=0.88,
                    line=dict(color="rgba(248,250,252,0.25)", width=1),
                ),
                name="信号节点",
                hoverinfo="skip",
            )
        )
        # 中位数路径
        t = np.linspace(-2.2, 2.2, 120)
        fig_graph.add_trace(
            go.Scatter(
                x=t,
                y=np.sin(t * 1.1) * 0.55,
                mode="lines",
                line=dict(color="#f59e0b", width=3, dash="dot"),
                name="中位数路径",
            )
        )
        # 高亮大节点
        for idx in [10, 25, 50]:
            fig_graph.add_trace(
                go.Scatter(
                    x=[x[idx]],
                    y=[y[idx]],
                    mode="markers",
                    marker=dict(
                        size=sizes[idx] * 2.2,
                        color=colors[idx],
                        opacity=0.25,
                        line=dict(color=colors[idx], width=2),
                    ),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        fig_graph.update_layout(
            height=330,
            margin=dict(l=5, r=5, t=5, b=5),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            xaxis=dict(showgrid=False, zeroline=False, visible=False),
            yaxis=dict(showgrid=False, zeroline=False, visible=False),
        )
        st.plotly_chart(
            fig_graph,
            use_container_width=True,
            config={"displayModeBar": False},
            key="network_graph",
        )

    # 边分布
    edge_cols = st.columns([3, 1])
    with edge_cols[1]:
        _card(
            '<div class="metric-label">节点 #75 · 边 121 条</div>'
            '<div style="margin-top:0.45rem;">'
            '<div class="stat-row"><span>信号强度</span><span class="metric-up">90.0%</span></div>'
            '<div class="stat-row"><span>关联条目</span><span class="value">798</span></div>'
            '<div class="hairline"></div>'
            '<div class="stat-row"><span>P(上涨)</span><span class="metric-up">0.83</span></div>'
            '<div class="stat-row"><span>P(下跌)</span><span class="metric-down">0.17</span></div>'
            '<div class="stat-row"><span>相对盘口优势</span><span class="metric-up">+24%</span></div>'
            '<div class="stat-row"><span>综合置信度</span><span class="metric-up">95.6%</span></div>'
            "</div>"
        )
        edge_bins = ["0-2", "3-5", "6-10", "11-20", "20+"]
        edge_vals = [8, 14, 22, 11, 5]
        fig_edge = go.Figure(
            go.Bar(
                x=edge_bins,
                y=edge_vals,
                marker=dict(
                    color=["#1890ff", "#1890ff", "#f59e0b", "#ef4444", "#ef4444"],
                    opacity=0.85,
                ),
            )
        )
        fig_edge.update_layout(
            height=130,
            margin=dict(l=5, r=5, t=10, b=25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c9d1d9", size=10),
            xaxis=dict(showgrid=False, title="边数区间"),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(
            fig_edge,
            use_container_width=True,
            config={"displayModeBar": False},
            key="edge_dist",
        )

# ═══════════════════════════════════════════════
# 第四行：BTC 分时脉冲
# ═══════════════════════════════════════════════
if st.session_state.get("show_pulse", True):
    st.markdown("### ⚡ BTC/USDT 分时脉冲图")
    p_left, p_mid, p_right = st.columns([1, 4, 1])

    with p_left:
        _card(
            '<div class="metric-label">实时报价</div>'
            f'<div style="font-size:1.6rem;font-weight:700;color:#f8fafc;">${btc_price:,.2f}</div>'
            '<div class="metric-up" style="font-size:0.85rem;">+$20.07 (0.03%)</div>'
            '<div class="hairline"></div>'
            '<div class="stat-row"><span>当前轮次</span><span class="value">第 7,185 轮</span></div>'
            '<div class="stat-row"><span>持仓成本</span><span class="value">$0.00</span></div>'
            '<div class="stat-row"><span>目标价位</span><span class="metric-up">$76,000</span></div>'
        )

    with p_mid:
        rs = np.random.RandomState(9)
        times = pd.date_range(end=datetime.now(UTC), periods=80, freq="1min")
        price = 75260 + np.cumsum(rs.randn(80) * 18)
        target = 76000

        fig_pulse = go.Figure()
        fig_pulse.add_trace(
            go.Scatter(
                x=times,
                y=price,
                mode="lines",
                line=dict(color="#1890ff", width=2.5),
                fill="tozeroy",
                fillcolor="rgba(24,144,255,0.07)",
                name="实时价格",
            )
        )
        fig_pulse.add_hline(
            y=target,
            line=dict(color="#f59e0b", dash="dash", width=2),
            annotation_text="目标 ¥76,000",
            annotation_font_color="#f59e0b",
        )
        # Parlay 标记点
        mk_t = [times[12], times[30], times[50], times[65]]
        mk_p = [price[12], price[30], price[50], price[65]]
        fig_pulse.add_trace(
            go.Scatter(
                x=mk_t,
                y=mk_p,
                mode="markers+text",
                text=["+¥1,785", "连胜", "三连胜", "大满贯"],
                textposition="top center",
                textfont=dict(color="#22c55e", size=10),
                marker=dict(
                    size=11,
                    color="#22c55e",
                    symbol="circle",
                    line=dict(color="white", width=1.8),
                ),
                name="连赢标记",
            )
        )
        fig_pulse.update_layout(
            height=240,
            margin=dict(l=25, r=30, t=20, b=35),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c9d1d9", size=11),
            xaxis=dict(
                showgrid=False, zeroline=False, tickformat="%H:%M", title="时间 (UTC)"
            ),
            yaxis=dict(showgrid=False, zeroline=False, tickprefix="$", title="价格"),
            showlegend=False,
        )
        st.plotly_chart(
            fig_pulse,
            use_container_width=True,
            config={"displayModeBar": False},
            key="pulse_chart",
        )

    with p_right:
        _card(
            '<div class="metric-label">当前赔率</div>'
            '<div style="margin-top:0.4rem;">'
            '<div class="stat-row"><span>📈 看涨</span><span class="metric-up">58¢</span></div>'
            '<div class="stat-row"><span>📉 看跌</span><span class="metric-down">44¢</span></div>'
            "</div>"
        )
        _card(
            '<div class="metric-label">脉冲强度</div>'
            '<div class="strength-bar">'
            '<div class="strength-fill" style="width:78%;background:linear-gradient(90deg,#f59e0b,#ef4444);"></div>'
            "</div>"
            '<div style="margin-top:0.3rem;display:flex;justify-content:space-between;font-size:0.78rem;">'
            '<span style="color:#8b949e;">偏空</span>'
            '<span style="color:#f59e0b;font-weight:700;">78%</span>'
            '<span style="color:#8b949e;">偏多</span>'
            "</div>"
        )

# ═══════════════════════════════════════════════
# 底部状态栏
# ═══════════════════════════════════════════════
st.divider()
footer_cols = st.columns([3, 1])
with footer_cols[0]:
    st.caption(
        "⚠️ 以上内容为模拟演示界面，仅用于展示量化策略系统 v5.10 的可视化与多策略对比能力，不构成任何投资建议。"
        "所有数据均为随机生成的演示数据，不代表真实市场行情。"
    )
with footer_cols[1]:
    st.caption(f"引擎版本: v5.10 · 渲染时间: {_now():%H:%M:%S}")
