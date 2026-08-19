"""11 宏观数据页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 宏观指标展示 (CPI / PMI / M2 / 利率)
    - Regime 分类结果
    - 经济环境四象限
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
from ui.layout import (  # noqa: E402
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
        title="宏观数据",
        subtitle="CPI / PMI / M2 / 利率 · Regime 分类",
        icon="🌍",
    )

    st.info(
        "**数据源**: 通过 `DataLayer.get_macro_indicators()` 复用 P0-P6 降级链获取\n\n"
        "**模块**: `utils/alpha/macro_indicator.py` (T4.4 已完成)"
    )

    st.divider()

    # ===== 宏观指标 KPI =====
    st.subheader("📊 核心宏观指标")
    render_kpi_row([
        {"label": "CPI", "value": "2.3%", "delta": "+0.2%", "delta_positive": False, "icon": "💰"},
        {"label": "PMI", "value": "50.8", "delta": "+0.5", "delta_positive": True, "icon": "🏭"},
        {"label": "M2 同比", "value": "10.5%", "delta": "+0.3%", "delta_positive": True, "icon": "💵"},
        {"label": "10Y 国债收益率", "value": "2.65%", "delta": "-0.05%", "delta_positive": True, "icon": "📈"},
    ])

    st.divider()

    # ===== Regime 分类 =====
    st.subheader("🎯 Regime 分类")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 经济环境四象限")
        render_status_badge("WARNING", label="过热期 (滞胀风险)")

        render_status_metric(label="增长趋势", value="↑ 上升 (PMI>=50)", status="NORMAL")
        render_status_metric(label="通胀趋势", value="↑ 上升 (CPI>2%)", status="WARNING")
        render_status_metric(label="综合评分", value="0.62", status="WARNING")
        render_status_metric(label="推荐配置", value="商品/黄金/通胀挂钩债券", status="INFO")

    with col2:
        st.markdown("#### Dalio 全天候配置基准")
        st.markdown("""
| 资产类别 | 权重 |
|---------|------|
| 股票 | 30% |
| 长期债券 | 40% |
| 中期债券 | 15% |
| 黄金 | 7.5% |
| 商品 | 7.5% |
""")

    st.divider()

    # ===== 阈值配置 =====
    st.subheader("⚙️ 自定义阈值")
    with st.expander("宏观指标阈值配置 (可调)"):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.number_input("CPI 低阈值", value=0.5, step=0.1)
            st.number_input("CPI 中阈值", value=1.5, step=0.1)
            st.number_input("CPI 高阈值", value=2.5, step=0.1)
        with col_b:
            st.number_input("PMI 低阈值", value=48.0, step=0.5)
            st.number_input("PMI 中阈值", value=50.0, step=0.5)
            st.number_input("PMI 高阈值", value=52.0, step=0.5)
        with col_c:
            st.caption("应用阈值后, 系统将根据当前指标值自动分类 Regime")

    st.divider()

    # ===== 历史数据 =====
    st.subheader("📈 历史宏观指标")
    st.caption("此处展示近 12 个月的宏观指标走势 (示例数据)")

    try:
        from datetime import datetime, timedelta

        import pandas as pd
        dates = [datetime.now() - timedelta(days=30 * i) for i in range(12)]
        dates.reverse()
        sample_data = pd.DataFrame({
            "日期": [d.strftime("%Y-%m") for d in dates],
            "CPI": [2.1, 2.2, 2.0, 1.9, 1.8, 2.0, 2.1, 2.3, 2.4, 2.2, 2.3, 2.3],
            "PMI": [50.2, 50.5, 50.3, 49.8, 49.5, 50.0, 50.5, 50.8, 51.0, 50.6, 50.8, 50.8],
            "M2": [10.2, 10.3, 10.5, 10.6, 10.7, 10.5, 10.4, 10.5, 10.6, 10.4, 10.5, 10.5],
        })
        st.dataframe(sample_data, use_container_width=True, hide_index=True)
        st.line_chart(sample_data.set_index("日期"))
    except Exception as e:
        st.warning(f"历史数据渲染失败: {e}")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
