"""大宗商品监控 — 康波周期大宗商品 + 宏观指标 + 基本面"""

import os
import sys

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)


import pandas as pd
import streamlit as st

st.title("💎 大宗商品监控")
st.caption("康波周期大宗商品全维度监控 — 价格/趋势/预警 + 宏观指标")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

mod = get_system_module()

tab1, tab2 = st.tabs(["🛢️ 康波商品监控", "💎 大宗商品基本面"])


@st.cache_data(ttl=300)
def _run_commodity_monitor(ts_token):
    monitor = mod.KommoCommodityMonitor(ts_token=ts_token)
    return monitor.monitor(), monitor


with tab1:
    st.subheader("康波周期大宗商品监控")

    if st.button("🔍 获取商品数据", type="primary"):
        ts_token = os.environ.get("TS_TOKEN", "")

        with st.spinner("获取商品价格与宏观指标..."):
            (commodity_result, macro), monitor = _run_commodity_monitor(ts_token)

        # === 商品价格 ===
        st.subheader("🛢️ 商品价格与趋势")
        if commodity_result:
            comm_data = []
            for item in commodity_result:
                alert = item.get("预警", "正常")
                alert_icon = "🔴" if "突破" in alert or "跌破" in alert else "🟢"
                comm_data.append(
                    {
                        "品种": item.get("名称", ""),
                        "分类": item.get("分类", ""),
                        "最新价格": item.get("最新价格", 0),
                        "日涨幅%": item.get("日涨幅", 0),
                        "月涨幅%": item.get("月涨幅", 0),
                        "趋势": item.get("趋势", ""),
                        "预警": f"{alert_icon} {alert}",
                    }
                )
            comm_df = pd.DataFrame(comm_data)

            def highlight_alert(val):
                if "🔴" in val:
                    return "background-color: #fff2f0; font-weight: bold"
                return ""

            st.dataframe(
                comm_df.style.map(highlight_alert, subset=["预警"]),
                use_container_width=True,
                hide_index=True,
            )

        # === 宏观指标 ===
        if macro:
            st.subheader("🌍 宏观指标")
            macro_cols = st.columns(len(macro))
            for i, (k, v) in enumerate(macro.items()):
                with macro_cols[i % len(macro)]:
                    st.metric(k, v if v else "N/A")

        # 报告下载
        report = monitor.generate_report()
        st.download_button(
            "📥 下载商品监控报告",
            report,
            file_name=f"康波商品监控_{now_bj().strftime('%Y%m%d')}.md",
            mime="text/markdown",
        )
    else:
        render_alert_card("数据获取", "👆 点击获取大宗商品实时数据", level="info")
        st.markdown(f"""
        ### 监测商品 (共 {len(mod.KommoCommodityMonitor.COMMODITY_LIST)} 只)
        """)
        for item in mod.KommoCommodityMonitor.COMMODITY_LIST:
            st.caption(f"• **{item['name']}** ({item['symbol']}) — {item['category']}")

with tab2:
    st.subheader("大宗商品基本面分析 (Wind数据)")

    if st.button("💎 获取基本面数据", type="primary"):
        commodity_avail = mod._check_commodity_module()
        if not commodity_avail:
            render_alert_card(
                "模块不可用",
                "⚠️ 大宗商品基本面模块不可用，需要 `大宗商品基本面综合.py`",
                level="warning",
            )
        else:
            with st.spinner("获取铜、金、银等大宗商品数据..."):
                try:
                    sys.path.insert(
                        0, os.path.join(_BASE_DIR, "..", "03_投研与策略生成")
                    )
                    from 大宗商品基本面综合 import get_copper_fundamentals

                    result = get_copper_fundamentals()

                    render_status_card(
                        "数据来源",
                        f"数据来源: {result.get('数据来源', '未知')}",
                        level="success",
                    )
                    fund_cols = st.columns(2)
                    for i, (k, v) in enumerate(result.items()):
                        with fund_cols[i % 2]:
                            st.metric(k, v if v else "N/A")

                except Exception as e:
                    render_alert_card("分析失败", f"基本面分析失败: {e}", level="error")
    else:
        render_alert_card(
            "数据获取", "👆 点击获取大宗商品基本面数据 (Wind/同花顺)", level="info"
        )
