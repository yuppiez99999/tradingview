"""08 TCA 执行归因页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 详细展示 TCA (Transaction Cost Analysis) 执行归因
    - 执行前预估 vs 执行后实际对比
    - 成本分解 (佣金/滑点/市场冲击)
    - 成交明细审计
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # type: ignore[import-not-found]

# noqa: E402
from ui.auth import require_auth  # noqa: E402
from ui.data_loader import (  # noqa: E402
    load_attribution_panel,
    load_tca_fills,
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
        title="TCA 执行归因",
        subtitle="Transaction Cost Analysis · 交易成本分析",
        icon="💰",
    )

    selected_date = st.date_input("选择日期", now_bj())

    # 从归因面板读取 TCA 聚合视图
    panel = load_attribution_panel(selected_date)
    tca_panel = panel.get("tca", {}) if panel else {}

    # 从 fills JSONL 读取逐笔记录
    fills = load_tca_fills(selected_date)

    if not tca_panel and not fills:
        render_empty_state(
            f"{selected_date.strftime('%Y-%m-%d')} 无 TCA 数据", icon="💰"
        )
        return

    # ===== 顶部 KPI =====
    render_kpi_row(
        [
            {
                "label": "总成本 (bps)",
                "value": f"{tca_panel.get('total_cost_bps', 0):.2f}",
                "icon": "💵",
            },
            {
                "label": "佣金 (bps)",
                "value": f"{tca_panel.get('commission_bps', 0):.2f}",
                "icon": "📋",
            },
            {
                "label": "滑点 (bps)",
                "value": f"{tca_panel.get('slippage_bps', 0):.2f}",
                "icon": "📊",
            },
            {
                "label": "市场冲击 (bps)",
                "value": f"{tca_panel.get('market_impact_bps', 0):.2f}",
                "icon": "💥",
            },
        ]
    )

    st.divider()

    # ===== 执行前预估 vs 执行后实际 =====
    st.subheader("⚖️ 执行前预估 vs 执行后实际")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("预估总成本", f"¥{tca_panel.get('estimated_total_cost', 0):,.2f}")
    with col2:
        st.metric("实际总成本", f"¥{tca_panel.get('actual_total_cost', 0):,.2f}")
    with col3:
        est = tca_panel.get("estimated_total_cost", 0)
        act = tca_panel.get("actual_total_cost", 0)
        diff = act - est
        st.metric(
            "偏差",
            f"¥{diff:,.2f}",
            delta=f"{diff/est*100:.2f}%" if est > 0 else "-",
            delta_color="inverse" if diff > 0 else "normal",
        )

    st.info(
        "**HC-6 同步路径延迟约束**: TCA 执行前预估延迟 <50ms\n\n"
        "**TCA 异常处理**: 异常不阻断主路径, 仅在 `plan.meta` 记录 `tca_error`"
    )

    st.divider()

    # ===== 成交明细 =====
    st.subheader("📝 成交明细")
    if fills:
        st.metric("成交笔数", len(fills))
        try:
            import pandas as pd

            df = pd.DataFrame(fills)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 按标的聚合成本
            if "symbol" in df.columns and "total_cost_bps" in df.columns:
                st.subheader("按标的聚合成本 (bps)")
                agg = df.groupby("symbol")["total_cost_bps"].agg(
                    ["mean", "max", "count"]
                )
                st.dataframe(agg, use_container_width=True)
                st.bar_chart(agg["mean"])
        except Exception as e:
            st.warning(f"成交明细渲染失败: {e}")
            st.json(fills[:20])
    else:
        render_empty_state("无成交记录", icon="📝")

    st.divider()

    # ===== TCA 估算历史 =====
    st.subheader("📊 TCA 估算历史")
    st.caption(
        "TCA 执行前预估需持久化至 `reports/tca/estimate_{date}.jsonl` 并支持 `get_history()` 查询"
    )
    # 这里仅展示说明, 实际历史查询由后端 API 提供
    st.info("如需查看历史估算记录, 请访问 `reports/tca/estimate_*.jsonl` 文件")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
