# -*- coding: utf-8 -*-
"""对冲+再平衡联动分析 — v5.9 组合自触发 + 多指数对冲 + 成本过滤 UI"""
import sys, os, json
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="对冲再平衡联动", page_icon="🔗", layout="wide")
st.title("🔗 对冲+再平衡联动分析 v5.9")
st.caption("五阶段联合决策引擎 — 组合自触发 + 多指数Beta加权 + 成本效益过滤")

from ui.components.common import inject_global_style
from ui.components.system_status import render_alert_card, render_status_card

inject_global_style()

# ── 侧边栏 ──
with st.sidebar:
    st.subheader("🎛️ 操作")
    
    # v5.9: 对冲模式选择
    hedge_mode = st.selectbox(
        "对冲模式",
        options=["tail_only", "dynamic", "fixed", "none"],
        index=0,
        format_func=lambda x: {
            "tail_only": "尾部保护 (推荐) — vol>28%或DD>12%触发",
            "dynamic": "动态对冲 — 基于波动率调整",
            "fixed": "固定比例 — 基于状态固定对冲",
            "none": "不对冲 — 仅再平衡",
        }[x],
        help="v5.9 组合自触发对冲模式"
    )
    
    # v5.9: 组合参数估算
    est_vol = st.slider("估算组合年化波动率", 0.05, 0.50, 0.18, 0.01,
                       help="来自近期每日收益年化std，默认18%")
    est_dd = st.slider("估算组合60日最大回撤", 0.0, 0.35, 0.0, 0.01,
                      help="60日窗口内峰谷最大跌幅，默认0%")
    
    auto_execute = st.checkbox("自动执行模式", value=False, 
        help="生成执行计划后自动保存执行记录（实际交易需手动确认）")
    show_reasoning = st.checkbox("显示详细推理", value=True,
        help="展示每阶段的详细推理过程")
    skip_ai = st.checkbox("跳过AI分析", value=False,
        help="仅使用规则引擎，不调用LLM")
    
    st.divider()
    
    if st.button("🚀 运行联动分析 v5.9", use_container_width=True, type="primary"):
        st.session_state.run_analysis = True
    
    st.divider()
    
    st.subheader("📖 五阶段工作流 (v5.9)")
    st.markdown("""
    1. **风险评估** — Beta(IF/IC/IM)/VaR/集中度/波动率
    2. **对冲决策** — 组合自触发 → 多指数Beta加权分配
    3. **再平衡检查** — 板块轮动权重 → 波动率驱动阈值
    4. **联合优化** — 对冲后敞口 vs 再平衡分布一致性
    5. **执行计划** — 优先级/窗口/绩效预估/警告
    
    **v5.9 核心改进**:
    - 组合自身波动率+回撤驱动(非CSI300)
    - IC/IM/IF 按Beta比例分配对冲
    - 成本效益过滤: 预期收益 > 1.5x成本
    """)
    
    st.divider()
    st.caption(f"更新时间: {datetime.now():%Y-%m-%d %H:%M}")

# ── 初始化 ──
if 'run_analysis' not in st.session_state:
    st.session_state.run_analysis = False
if 'analysis_result' not in st.session_state:
    st.session_state.analysis_result = None
if 'analysis_error' not in st.session_state:
    st.session_state.analysis_error = None

# ── 运行分析 ──
if st.session_state.run_analysis:
    st.session_state.run_analysis = False
    st.session_state.analysis_error = None
    
    try:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator, HedgeMode, run_joint_analysis
        )
        
        # v5.9: 对冲模式映射
        mode_map = {
            "tail_only": HedgeMode.TAIL_ONLY,
            "dynamic": HedgeMode.DYNAMIC,
            "fixed": HedgeMode.FIXED,
            "none": HedgeMode.NONE,
        }
        selected_mode = mode_map.get(hedge_mode, HedgeMode.TAIL_ONLY)
        
        with st.spinner("🔍 正在执行五阶段联动分析 v5.9..."):
            integrator = HedgeRebalanceIntegrator(
                base_dir=_BASE_DIR, hedge_mode=selected_mode
            )
            
            # Phase 1
            with st.status("Phase 1/5: 评估组合风险...", expanded=True) as status1:
                risk = integrator.assess_risk()
                status1.update(label="Phase 1/5: 风险评估完成 ✅ (多指数Beta)", state="complete")
            
            # Phase 2 (v5.9: 组合自触发)
            with st.status("Phase 2/5: 对冲决策 (组合自触发)...", expanded=True) as status2:
                external = {}  # v5.9 外部信号降权至5%, 仅保留接口
                hedge = integrator.decide_hedge(
                    risk, 
                    portfolio_volatility=est_vol,
                    portfolio_drawdown_60d=est_dd,
                )
                status2.update(label="Phase 2/5: 对冲决策完成 ✅", state="complete")
            
            # Phase 3 (v5.9: 波动率驱动阈值)
            with st.status("Phase 3/5: 再平衡检查 (波动率驱动)...", expanded=True) as status3:
                rebalance = integrator.check_rebalance(risk, portfolio_volatility=est_vol)
                status3.update(label="Phase 3/5: 再平衡检查完成 ✅", state="complete")
            
            # Phase 4
            with st.status("Phase 4/5: 联合优化...", expanded=True) as status4:
                adj_hedge, adj_rebalance, warnings = integrator.joint_optimize(risk, hedge, rebalance)
                status4.update(label=f"Phase 4/5: 联合优化完成 ✅ ({len(warnings)} 项警告)", state="complete")
            
            # Phase 5
            with st.status("Phase 5/5: 生成执行计划...", expanded=True) as status5:
                plan = integrator.generate_execution_plan(risk, adj_hedge, adj_rebalance, warnings)
                report_path = integrator.save_report(plan)
                status5.update(label="Phase 5/5: 执行计划生成完成 ✅", state="complete")
            
            st.session_state.analysis_result = {
                'risk': risk, 'hedge': hedge, 'rebalance': rebalance,
                'adj_hedge': adj_hedge, 'adj_rebalance': adj_rebalance,
                'warnings': warnings, 'plan': plan, 'report_path': report_path,
                'sector_weights': integrator._get_sector_adjusted_weights(),
                'hedge_mode': hedge_mode,  # v5.9
                'est_vol': est_vol,        # v5.9
                'est_dd': est_dd,          # v5.9
            }
        
        render_alert_card("联动分析完成", f"✅ 联动分析完成！报告已保存: {report_path}", level="success")
        
    except Exception as e:
        st.session_state.analysis_error = str(e)
        render_alert_card("分析失败", f"❌ 分析失败: {e}", level="error")
        import traceback
        st.code(traceback.format_exc())

# ── 显示结果 ──
result = st.session_state.analysis_result

if result is None and st.session_state.analysis_error is None:
    render_alert_card("待运行分析", "👆 点击左侧「运行联动分析 v5.9」按钮开始分析 — 组合自触发 + 多指数Beta加权", level="info")
    st.stop()

if st.session_state.analysis_error and not result:
    st.stop()

# ═══════════════════════════════════════════
# 三列关键指标
# ═══════════════════════════════════════════
plan = result['plan']
risk = result['risk']
hedge = result['hedge']
rebalance = result['rebalance']

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("组合市值", f"¥{risk.total_value:,.0f}", 
              delta=f"敞口 {risk.stock_exposure/risk.total_value*100:.0f}%" if risk.total_value > 0 else None)
with col2:
    st.metric("执行优先级", plan.execution_priority,
              delta=plan.execution_window)
with col3:
    st.metric("预估年化收益", f"{plan.estimated_annual_return*100:.1f}%",
              delta=f"夏普 {plan.estimated_sharpe:.2f}")
with col4:
    delta_str = f"-{plan.estimated_max_drawdown*100:.0f}bp" if plan.estimated_max_drawdown < 0.18 else f"{plan.estimated_max_drawdown*100:.0f}%"
    st.metric("预估最大回撤", f"{plan.estimated_max_drawdown*100:.1f}%",
              delta=f"波动率 {plan.estimated_volatility*100:.1f}%")

st.divider()

# ═══════════════════════════════════════════
# 主区域: Tab切换
# ═══════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs([
    "🛡️ 对冲决策", "🔄 再平衡清单", "⚙️ 联合优化", "📋 执行计划"
])

with tab1:
    st.subheader("🛡️ 对冲决策 (v5.9 组合自触发)")
    
    regime_display = {
        "calm": "🟢 平静期 (vol<18%,DD<8%)", "mild": "🟡 温和波动 (vol 18-25%)",
        "high": "🟠 中高波动 (vol 25-35%)", "tail": "🔴 尾部事件 (vol>35%或DD>18%)"
    }
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("组合状态", regime_display.get(hedge.regime.value, hedge.regime.value))
        st.metric("对冲需求", "需要" if hedge.needed else "无需")
        st.metric("对冲模式", result.get('hedge_mode', 'tail_only'))
    with c2:
        st.metric("对冲比率", f"{hedge.hedge_ratio*100:.0f}%")
        max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
        beta_after = hedge.expected_beta_after
        st.metric("最大Beta (对冲后)", f"{beta_after:.2f}", delta=f"{beta_after-max_beta:+.2f}")
        # 显示多指数Beta
        st.caption(f"CSI300β={risk.beta_csi300:.2f} | CSI500β={risk.beta_csi500:.2f} | CSI1000β={risk.beta_csi1000:.2f}")
    with c3:
        st.metric("总名义价值", f"¥{hedge.total_notional:,.0f}")
        st.metric("总保证金", f"¥{hedge.total_margin:,.0f}",
                  delta=f"{hedge.total_margin/risk.total_value*100:.1f}% 资产" if risk.total_value > 0 else None)
        # v5.9: 显示触发条件
        vol_str = f"vol={result.get('est_vol', 0)*100:.0f}%" if result.get('est_vol') else ""
        dd_str = f"dd={result.get('est_dd', 0)*100:.0f}%" if result.get('est_dd') else ""
        if vol_str or dd_str:
            st.caption(f"组合自评: {vol_str} {dd_str}")
    
    if hedge.needed and hedge.futures_contracts:
        st.markdown("**期货合约明细**")
        contracts_data = []
        for code, n in hedge.futures_contracts.items():
            contracts_data.append({
                "品种": code,
                "方向": "做空",
                "手数": n,
                "名义价值": f"¥{hedge.futures_notional.get(code, 0):,.0f}",
                "保证金": f"¥{hedge.futures_margin.get(code, 0):,.0f}",
            })
        st.dataframe(pd.DataFrame(contracts_data), use_container_width=True, hide_index=True)
    
    if hedge.fallback_used:
        render_alert_card("回退价格", f"⚠️ 以下品种使用回退价格: {', '.join(hedge.fallback_used)}", level="warning")
    
    if show_reasoning:
        st.markdown(f"**推理**: {hedge.reasoning}")
    
    st.caption(f"价格数据源: {hedge.price_source}")

with tab2:
    st.subheader("🔄 再平衡清单")
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("再平衡类型", rebalance.rebalance_type.upper())
    with c2:
        st.metric("动态阈值", f"{rebalance.threshold*100:.0f}%")
    with c3:
        st.metric("需调整标的", f"{len(rebalance.positions_to_adjust)} 只")
    with c4:
        net_prefix = "+" if rebalance.net_cash_flow > 0 else ""
        st.metric("净现金流", f"{net_prefix}¥{rebalance.net_cash_flow:,.0f}")
    
    if rebalance.needed and rebalance.positions_to_adjust:
        st.markdown("**调整明细**")
        adj_data = []
        for pw in rebalance.positions_to_adjust:
            adj_data.append({
                "操作": pw.action,
                "代码": pw.code,
                "名称": pw.name,
                "板块": pw.category,
                "目标权重": f"{pw.target_weight*100:.1f}%",
                "当前权重": f"{pw.current_weight*100:.1f}%",
                "偏差": f"{pw.deviation_pct*100:.1f}%",
                "调整金额": f"¥{pw.adjustment:,.0f}",
                "调整股数": pw.adjustment_shares,
            })
        df_adj = pd.DataFrame(adj_data)
        
        # 颜色标注
        def highlight_action(val):
            if val == 'BUY':
                return 'background-color: #1a3a1a; color: #4ade80'
            elif val == 'SELL':
                return 'background-color: #3a1a1a; color: #f87171'
            return ''
        
        styled = df_adj.style.map(highlight_action, subset=['操作'])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    
    # 板块轮动权重
    st.markdown("**板块轮动目标权重**")
    sector_w = result['sector_weights']
    sw_data = [{"板块": k, "目标权重": f"{v*100:.0f}%"} for k, v in sector_w.items()]
    st.dataframe(pd.DataFrame(sw_data), use_container_width=True, hide_index=True)
    
    if show_reasoning:
        st.markdown(f"**推理**: {rebalance.reasoning}")

with tab3:
    st.subheader("⚙️ 联合优化")
    
    # 敞口对比
    after_hedge = risk.stock_exposure * (1 - adj_hedge.hedge_ratio) if adj_hedge.needed else risk.stock_exposure
    rebalance_net = adj_rebalance.net_cash_flow if adj_rebalance.needed else 0
    after_rebalance = risk.stock_exposure - rebalance_net
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("对冲后净敞口", f"¥{after_hedge:,.0f}")
    with c2:
        st.metric("再平衡后敞口", f"¥{after_rebalance:,.0f}")
    with c3:
        discrepancy = abs(after_hedge - after_rebalance) / risk.stock_exposure * 100 if risk.stock_exposure > 0 else 0
        color = "normal" if discrepancy < 5 else ("off" if discrepancy < 15 else "inverse")
        st.metric("敞口偏差", f"{discrepancy:.1f}%", delta="可接受" if discrepancy < 5 else "需调整")
    
    if result['warnings']:
        render_alert_card("警告信息", "\n".join([f"- {w}" for w in result['warnings']]), level="warning")
    else:
        render_status_card("一致性检查", "对冲后敞口与再平衡后分布一致，无需调整", level="success")
    
    # 保证金检查
    if adj_hedge.needed:
        required_margin = adj_hedge.total_margin
        available_cash = risk.cash + abs(rebalance_net)  # 简化估算
        margin_ratio = required_margin / risk.total_value * 100 if risk.total_value > 0 else 0
        st.metric("保证金/总资产", f"{margin_ratio:.1f}%",
                  delta="充足" if margin_ratio < 8 else "偏高")

with tab4:
    st.subheader("📋 执行计划")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        priority_color = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(plan.execution_priority, "⚪")
        st.metric("优先级", f"{priority_color} {plan.execution_priority}")
    with c2:
        st.metric("执行窗口", plan.execution_window)
    with c3:
        st.metric("报告路径", plan.timestamp[:10])
    
    st.markdown(f"**综合摘要**: {plan.summary}")
    
    # 警告
    if plan.warning_flags:
        render_alert_card("注意事项", "\n".join([f"- {w}" for w in plan.warning_flags]), level="warning")
    
    # 绩效预估表
    st.markdown("**绩效预估对比**")
    perf_data = {
        "指标": ["年化收益", "最大回撤", "夏普比率", "年化波动率"],
        "联动策略": [
            f"{plan.estimated_annual_return*100:.1f}%",
            f"{plan.estimated_max_drawdown*100:.1f}%",
            f"{plan.estimated_sharpe:.2f}",
            f"{plan.estimated_volatility*100:.1f}%",
        ],
        "基准静态": ["5-7%", "15-19%", "0.3-0.5", "18-22%"],
    }
    st.dataframe(pd.DataFrame(perf_data).set_index("指标"), use_container_width=True)
    
    # 自动化执行
    if auto_execute:
        st.divider()
        st.subheader("🚀 自动执行确认")
        
        if adj_hedge.needed:
            st.markdown(f"**对冲操作**: 对冲 {adj_hedge.hedge_ratio*100:.0f}% 敞口")
        if adj_rebalance.needed and adj_rebalance.positions_to_adjust:
            st.markdown(f"**再平衡操作**: {len(adj_rebalance.positions_to_adjust)} 只标的")
        
        if st.button("✅ 确认执行", use_container_width=True, type="primary"):
            try:
                exec_record = {
                    'timestamp': datetime.now().isoformat(),
                    'mode': 'hedge_rebalance_auto_execute',
                    'plan_summary': plan.summary,
                    'hedge': {'needed': adj_hedge.needed, 'ratio': adj_hedge.hedge_ratio},
                    'rebalance': {'needed': adj_rebalance.needed, 'type': adj_rebalance.rebalance_type},
                }
                exec_dir = os.path.join(_BASE_DIR, '..', 'reports', 'executions')
                os.makedirs(exec_dir, exist_ok=True)
                exec_path = os.path.join(exec_dir, f'exec_{datetime.now():%Y%m%d_%H%M%S}.json')
                with open(exec_path, 'w', encoding='utf-8') as f:
                    json.dump(exec_record, f, ensure_ascii=False, indent=2, default=str)
                render_alert_card("执行记录已保存", f"✅ 执行记录已保存: {exec_path}", level="success")
                render_alert_card("执行提示", "💡 实际交易需通过券商API完成，当前仅记录执行意图。", level="info")
            except Exception as e:
                render_alert_card("保存失败", f"保存失败: {e}", level="error")

# ── 底部 ──
st.divider()
st.caption("⚠️ 以上分析仅供参考，不构成投资建议。期货/期权交易有杠杆风险。")
