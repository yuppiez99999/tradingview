"""12 行业轮动页 — 终极量化交易系统 8.4 (T5.6).

职责:
    - 行业轮动信号展示
    - 24 个申万一级行业信号
    - 动量/资金流/估值三维度融合
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
    render_page_header,
)


def main() -> None:
    """页面入口."""
    if not require_auth():
        st.stop()

    render_page_header(
        title="行业轮动",
        subtitle="24 个申万一级行业 · 动量/资金流/估值三维度融合",
        icon="🔄",
    )

    st.info(
        "**模块**: `utils/alpha/sector_rotation.py` (T4.4 已完成)\n\n"
        "**信号维度**: 动量 (momentum) + 资金流 (flow) + 估值 (valuation)\n\n"
        "**Regime 联动**: 根据 Regime 标签调整三维度权重"
    )

    st.divider()

    # ===== 行业信号表 =====
    st.subheader("📊 行业轮动信号")

    # 24 个申万一级行业
    sectors = [
        "农林牧渔", "采掘", "化工", "钢铁", "有色金属", "电子",
        "家用电器", "食品饮料", "纺织服装", "轻工制造", "医药生物", "公用事业",
        "交通运输", "房地产", "商业贸易", "休闲服务", "银行", "非银金融",
        "综合", "建筑材料", "建筑装饰", "电气设备", "机械设备", "国防军工",
    ]

    # 示例数据 (实际由 sector_rotation 模块生成)
    import random
    random.seed(42)
    data = []
    for s in sectors:
        signal = random.choice(["超配", "标配", "低配"])
        score = round(random.uniform(-1, 1), 3)
        data.append({
            "行业": s,
            "信号": signal,
            "综合得分": score,
            "动量得分": round(random.uniform(-1, 1), 3),
            "资金流得分": round(random.uniform(-1, 1), 3),
            "估值得分": round(random.uniform(-1, 1), 3),
        })

    try:
        import pandas as pd
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 信号分布
        st.subheader("信号分布")
        signal_counts = df["信号"].value_counts()
        col1, col2 = st.columns([1, 2])
        with col1:
            st.dataframe(signal_counts, use_container_width=True)
        with col2:
            st.bar_chart(signal_counts)
    except Exception as e:
        st.warning(f"行业信号渲染失败: {e}")

    st.divider()

    # ===== 三维度权重 =====
    st.subheader("⚖️ Regime 自适应权重")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 当前 Regime: 过热期")
        weights = {"动量": 0.4, "资金流": 0.35, "估值": 0.25}
        for name, w in weights.items():
            st.progress(w, text=f"{name}: {w*100:.0f}%")

    with col2:
        st.markdown("#### Regime 权重对照表")
        st.markdown("""
| Regime | 动量 | 资金流 | 估值 |
|--------|------|--------|------|
| bull | 0.5 | 0.3 | 0.2 |
| bear | 0.2 | 0.3 | 0.5 |
| choppy | 0.3 | 0.4 | 0.3 |
| rebound | 0.4 | 0.35 | 0.25 |
""")

    st.divider()

    # ===== 顶部推荐 =====
    st.subheader("🎯 顶部推荐行业")
    try:
        top_picks = df.nlargest(5, "综合得分")[["行业", "综合得分", "信号"]]
        st.dataframe(top_picks, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"顶部推荐渲染失败: {e}")


if __name__ == "__main__" or "streamlit" in __file__:
    main()
