"""实时持仓监控 — 增强版量化监控面板（完整标的名称映射 + 仪表盘风格）"""

import os
import sys
from typing import Any

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.title("📊 实时持仓监控")
st.caption("持仓明细 · 权重分布 · 净值走势 · 风格分布 · 再平衡偏差")

from ui.components.names import STOCK_NAME_MAP, resolve_name
from ui.components.names import get_style as get_asset_style
from ui.components.sidebar import render_sidebar

render_sidebar()

# ═══════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════
CONFIG_DIR = os.path.join(_BASE_DIR, "config")
POSITIONS_FILE = os.path.join(CONFIG_DIR, "positions.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "price_history.jsonl")
PORTFOLIO_YAML = os.path.join(CONFIG_DIR, "portfolio.yaml")


@st.cache_data(ttl=60)
def load_data():
    positions = {}
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            positions = json.load(f)

    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    history.append(json.loads(line.strip()))
                except Exception:
                    pass

    portfolio_config = {"assets": [], "target_weights": {}, "names": {}}
    if os.path.exists(PORTFOLIO_YAML):
        try:
            import yaml

            with open(PORTFOLIO_YAML, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            for asset in cfg.get("assets", []):
                code = str(asset.get("code", ""))
                portfolio_config["assets"].append(asset)
                portfolio_config["target_weights"][code] = asset.get("target_weight", 0)
                if asset.get("name"):
                    portfolio_config["names"][code] = asset["name"]
        except Exception:
            pass

    return positions, history[-120:], portfolio_config


positions_data, history, port_cfg = load_data()
pos = positions_data.get("positions", {})
prices = positions_data.get("prices", {})
cash = positions_data.get("cash", 0)
last_update = positions_data.get("last_update", "未知")
initial_capital = positions_data.get("initial_capital", 3000000)

# ═══════════════════════════════════════════════════════════════
# 指标卡片
# ═══════════════════════════════════════════════════════════════
uses_live = bool(prices)


def calc_total_value():
    total = cash
    for code, p in pos.items():
        shares = p.get("shares", 0)
        price = prices.get(code, 0) or p.get("avg_cost", 0)
        total += shares * price
    return total


total_value = calc_total_value()
pos_value = max(total_value - cash, 0)
pnl_pct = (
    ((total_value - initial_capital) / initial_capital * 100) if initial_capital else 0
)

metric_cols = st.columns(5)
metric_cols[0].metric(
    "💰 账户总值",
    f"￥{total_value:,.0f}",
    delta="📡 实时" if uses_live else "📊 成本估算",
)
metric_cols[1].metric("📦 持仓市值", f"￥{pos_value:,.0f}")
metric_cols[2].metric("💵 可用现金", f"￥{cash:,.0f}")
metric_cols[3].metric("📈 总收益率", f"{pnl_pct:+.1f}%")
metric_cols[4].metric("🕐 最后更新", str(last_update)[:19] if last_update else "未知")

if not uses_live:
    st.caption("⚠️ 当前显示成本估算值，盘中时段将切换为实时行情")

st.markdown("---")

# ═══════════════════════════════════════════════════════════════
# 持仓明细表
# ═══════════════════════════════════════════════════════════════
st.subheader("📋 持仓明细")

if pos:
    table_data = []
    for code, pdata in pos.items():
        shares = pdata.get("shares", 0)
        avg_cost = pdata.get("avg_cost", 0)
        price = prices.get(code, 0) or avg_cost
        mv = shares * price
        cost_basis = shares * avg_cost
        unrealized_pnl = mv - cost_basis
        unrealized_pnl_pct = (
            (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0
        )
        aw = (mv / total_value * 100) if total_value > 0 else 0
        tw = port_cfg["target_weights"].get(code, 0) * 100
        dev = aw - tw
        name = resolve_name(code, pdata.get("name", ""), port_cfg["names"])
        style_label, style_color = get_asset_style(code)

        price_is_live = code in prices and prices[code] > 0
        price_str = f"￥{price:,.2f} {'📡' if price_is_live else '💰'}"

        table_data.append(
            {
                "名称": name,
                "代码": code,
                "风格": style_label,
                "持仓": f"{shares:,}股",
                "成本价": f"￥{avg_cost:,.2f}",
                "现价": price_str,
                "市值": f"￥{mv:,.0f}",
                "未实现盈亏": f"￥{unrealized_pnl:+,.0f}",
                "盈亏%": f"{unrealized_pnl_pct:+.1f}%",
                "实际权重": f"{aw:.1f}%",
                "目标权重": f"{tw:.0f}%",
                "偏差": f"{dev:+.1f}%",
            }
        )

    df = pd.DataFrame(table_data)

    def color_pnl(val):
        if isinstance(val, str) and val.startswith("￥"):
            if val.startswith("￥+"):
                return "color: #cf1322; font-weight: bold"
            elif val.startswith("￥-"):
                return "color: #389e0d; font-weight: bold"
        if isinstance(val, str) and val.endswith("%"):
            if val.startswith("+"):
                return "color: #cf1322"
            elif val.startswith("-"):
                return "color: #389e0d"
        return ""

    styled = df.style.map(color_pnl, subset=["未实现盈亏", "盈亏%"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    style_counts: dict[str, Any] = {}
    for d in table_data:
        s = d["风格"]
        style_counts[s] = style_counts.get(s, 0) + 1
    style_str = " · ".join(f"{s}×{c}" for s, c in sorted(style_counts.items()))
    st.caption(f"🏷️ 风格分布: {style_str} | 📊 总标的: {len(table_data)} 只")
else:
    st.info("暂无持仓数据。请在 `config/positions.json` 中配置持仓信息。")

# ═══════════════════════════════════════════════════════════════
# 可视化图表
# ═══════════════════════════════════════════════════════════════
if pos:
    left, right = st.columns(2)

    with left:
        st.subheader("🏷️ 权重分布（按风格着色）")
        pie_data = []
        style_colors = {
            "成长": "#722ED1",
            "周期": "#FAAD14",
            "防御": "#52C41A",
            "资源": "#1890FF",
            "ETF": "#13C2C2",
            "观察": "#8C8C8C",
            "个股": "#F5222D",
        }
        for code, pdata in pos.items():
            shares = pdata.get("shares", 0)
            price = prices.get(code, 0) or pdata.get("avg_cost", 0)
            mv = shares * price
            w = (mv / total_value * 100) if total_value > 0 else 0
            name = resolve_name(code, pdata.get("name", ""), port_cfg["names"])
            style_label, _ = get_asset_style(code)
            pie_data.append({"标的": name, "权重": max(w, 0.01), "风格": style_label})

        pie_df = pd.DataFrame(pie_data)
        st.vega_lite_chart(
            pie_df,
            {
                "width": "container",
                "mark": {"type": "arc", "innerRadius": 45, "tooltip": True},
                "encoding": {
                    "theta": {"field": "权重", "type": "quantitative"},
                    "color": {
                        "field": "风格",
                        "type": "nominal",
                        "scale": {"range": list(style_colors.values())},
                    },
                },
            },
            use_container_width=True,
        )

    with right:
        st.subheader("🎯 权重偏差 (目标 vs 实际)")
        dev_data = []
        for code, tw_val in port_cfg["target_weights"].items():
            pdata = pos.get(code, {})
            shares = pdata.get("shares", 0)
            price = prices.get(code, 0) or pdata.get("avg_cost", 0)
            mv = shares * price
            aw = (mv / total_value * 100) if total_value > 0 else 0
            tw_pct = tw_val * 100
            dev = aw - tw_pct
            name = resolve_name(code, pdata.get("name", ""), port_cfg["names"])
            dev_data.append({"标的": name, "偏差%": dev})
        if dev_data:
            dev_df = pd.DataFrame(dev_data)
            st.bar_chart(dev_df.set_index("标的")["偏差%"], use_container_width=True)
        else:
            st.caption("无目标权重配置，请在 portfolio.yaml 中设置")

# ═══════════════════════════════════════════════════════════════
# 历史净值 + 风格市值分布
# ═══════════════════════════════════════════════════════════════
if pos:
    chart_left, chart_right = st.columns(2)

    with chart_left:
        if history and len(history) > 2:
            st.subheader("💹 账户总值变化")
            tv_data = [
                {"时间": h.get("time", ""), "总值": h.get("total_value", 0)}
                for h in history
            ]
            tv_df = pd.DataFrame(tv_data)
            st.line_chart(tv_df.set_index("时间"), use_container_width=True)
        else:
            st.subheader("💹 账户总值变化")
            st.caption("暂无历史数据（需积累 price_history.jsonl）")

    with chart_right:
        st.subheader("📊 风格市值分布")
        style_mv: dict[str, Any] = {}
        for code, pdata in pos.items():
            shares = pdata.get("shares", 0)
            price = prices.get(code, 0) or pdata.get("avg_cost", 0)
            mv = shares * price
            style_label, _ = get_asset_style(code)
            style_mv[style_label] = style_mv.get(style_label, 0) + mv
        if style_mv:
            mv_df = pd.DataFrame({"市值": style_mv})
            mv_df.index.name = "风格"
            st.bar_chart(mv_df, use_container_width=True)

# ═══════════════════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.subheader("⚙️ 监控设置")
    refresh_sec = st.slider("自动刷新间隔(秒)", 10, 300, 60, 10)
    st.caption(f"⏱️ 每 {refresh_sec}s 刷新")
    st.caption(f"🕐 当前: {now_bj().strftime('%H:%M:%S')}")

    if st.button("🔄 立即刷新", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.subheader("📋 标的名称映射")
    st.caption(f"内置映射覆盖 **{len(STOCK_NAME_MAP)}** 只标的")
    st.caption(f"portfolio.yaml 补充 **{len(port_cfg['names'])}** 个名称")

    with st.expander("🔍 搜索标的"):
        search = st.text_input("输入代码或名称", placeholder="如: 601088 或 神华")
        if search:
            search = search.strip()
            matches = []
            for code, name in STOCK_NAME_MAP.items():
                if search.upper() in code.upper() or search in name:
                    matches.append(f"`{code}` → {name}")
            if port_cfg["names"]:
                for code, name in port_cfg["names"].items():
                    if search.upper() in code.upper() or search in name:
                        m = f"`{code}` → {name} (yaml)"
                        if m not in matches:
                            matches.append(m)
            if matches:
                for m in matches[:20]:
                    st.caption(m)
            else:
                st.caption("未找到匹配项")

# JavaScript 自动刷新（兼容 Streamlit 1.58）
components.html(
    f"""
<script>
    setTimeout(function() {{
        window.location.reload();
    }}, {refresh_sec * 1000});
</script>
""",
    height=0,
)
