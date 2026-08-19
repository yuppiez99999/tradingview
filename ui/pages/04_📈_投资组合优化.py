"""投资组合优化 — 多策略资产配置对比"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import pandas as pd
import streamlit as st

st.title("📈 投资组合优化")
st.caption("多策略资产配置对比 — 等权重 / 风险平价 / 风险配比 / 因子配比 / 自定义配置")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

with st.spinner("加载优化引擎..."):
    try:
        mod = get_system_module()
    except Exception as e:
        render_alert_card("模块加载失败", f"模块加载失败: {e}", level="error")
        st.stop()

# === 参数设置 ===
with st.sidebar:
    st.subheader("⚙️ 回测参数")
    initial_capital = st.number_input("初始资金(万元)", 50, 1000, 100, 10) * 10000
    start_date = st.date_input("回测起始", pd.to_datetime("2020-01-01"))
    end_date = st.date_input("回测结束", pd.to_datetime("2026-06-01"))

@st.cache_data(ttl=600)
def _run_portfolio_optimization(start_date_str, end_date_str):
    """缓存投资组合优化结果，10分钟TTL"""
    engine = mod.PortfolioOptimizationEngine()
    engine.generate_simulation_data(start_date=start_date_str, end_date=end_date_str)
    engine.calculate_correlation_matrix()
    results = engine.run_all_strategies()
    return engine, results

# 执行按钮
if st.button("🚀 运行多策略优化", type="primary"):
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    with st.spinner("运行多策略优化..."):
        engine, results = _run_portfolio_optimization(start_str, end_str)

    if not results:
        render_alert_card("回测结果缺失", "未能生成回测结果，请检查数据。", level="warning")
        st.stop()

    # === 策略对比表 ===
    st.subheader("📊 策略表现汇总")
    compare_data = []
    for name, r in results.items():
        compare_data.append({
            "策略": name,
            "总收益率%": round(r['total_return'], 2),
            "年化收益%": round(r['annual_return'], 2),
            "夏普比率": round(r['sharpe_ratio'], 2),
            "最大回撤%": round(r['max_drawdown'], 2),
            "Calmar比率": round(r['calmar_ratio'], 2),
            "胜率%": round(r['win_rate'], 2),
            "最终资金": round(r['final_equity'], 0),
        })
    compare_df = pd.DataFrame(compare_data)

    # 高亮最佳值
    def highlight_best(s, best_idx):
        is_max = s.name in ['总收益率%', '年化收益%', '夏普比率', 'Calmar比率', '胜率%', '最终资金']
        vals = s.values
        best_val = max(vals) if is_max else min(vals)
        return ['background-color: #f6ffed; font-weight: bold' if v == best_val else '' for v in vals]

    styled = compare_df.style
    for col in ['总收益率%', '年化收益%', '夏普比率', '最大回撤%', 'Calmar比率', '胜率%']:
        styled = styled.apply(lambda s, c=col: ['background-color: #f6ffed; font-weight: bold'
                               if s.name == c and v == (max(s.values) if c != '最大回撤%' else min(s.values))
                               else '' for v in s], axis=0)

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # === 图表对比 ===
    st.subheader("📈 策略对比图表")
    chart_cols = st.columns(2)

    with chart_cols[0]:
        chart1_data = pd.DataFrame({
            "策略": [d["策略"] for d in compare_data],
            "年化收益%": [d["年化收益%"] for d in compare_data],
            "最大回撤%": [d["最大回撤%"] for d in compare_data],
        }).set_index("策略")
        st.bar_chart(chart1_data, use_container_width=True)

    with chart_cols[1]:
        chart2_data = pd.DataFrame({
            "策略": [d["策略"] for d in compare_data],
            "夏普比率": [d["夏普比率"] for d in compare_data],
            "Calmar比率": [d["Calmar比率"] for d in compare_data],
        }).set_index("策略")
        st.bar_chart(chart2_data, use_container_width=True)

    # === 最佳策略权重 ===
    st.subheader("🏆 最佳策略权重配置")
    best = max(results.items(), key=lambda x: x[1]['sharpe_ratio'])
    render_status_card("最优策略", f"**{best[0]}** — 夏普比率: {best[1]['sharpe_ratio']:.2f} | 收益率: {best[1]['total_return']:.2f}%", level="success")

    weights = best[1]['weights']
    w_data = []
    for code, w in sorted(weights.items(), key=lambda x: -x[1]):
        info = engine.portfolio.get(code, {})
        w_data.append({"标的": info.get('name', code), "权重": w * 100})
    w_df = pd.DataFrame(w_data)

    st.vega_lite_chart(w_df, {
        "width": "container",
        "mark": {"type": "bar", "tooltip": True},
        "encoding": {
            "x": {"field": "权重", "type": "quantitative", "title": "权重 (%)"},
            "y": {"field": "标的", "type": "nominal", "sort": "-x"},
            "color": {"field": "标的", "type": "nominal"},
        },
    }, use_container_width=True)

    # === 相关性矩阵 ===
    if engine.corr_matrix is not None:
        st.subheader("🔗 资产相关性矩阵")
        corr_df = engine.corr_matrix
        # 重命名列
        rename_map = {c: engine.portfolio.get(c, {}).get('name', c) for c in corr_df.columns}
        corr_df = corr_df.rename(columns=rename_map, index=rename_map)
        st.dataframe(corr_df.style.background_gradient(cmap='RdYlGn', vmin=-1, vmax=1),
                     use_container_width=True)

else:
    render_alert_card("待运行分析", "👆 点击上方按钮运行多策略优化分析", level="info")
    st.markdown("""
    ### 5种优化策略说明
    - **等权重**: 所有资产配置相同权重
    - **风险平价**: 使各资产风险贡献相等
    - **风险配比**: 根据历史波动率反向配置
    - **因子配比**: 基于风险等级因子配置
    - **自定义配置**: 预设的社保基金风格配置
    """)
