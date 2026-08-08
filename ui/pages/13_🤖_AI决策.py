#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Streamlit UI - AI盘中实时决策页面
页面编号: 13_🤖_AI决策.py
"""

import streamlit as st
import sys
import os
from pathlib import Path
from datetime import datetime

# 设置页面配置
st.set_page_config(
    page_title="AI盘中实时决策",
    page_icon="🤖",
    layout="wide",
)

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# 页面标题
st.title("🤖 GLM-5 盘中实时决策")
st.markdown("---")

# 侧边栏配置
with st.sidebar:
    st.header("⚙️ 配置")
    
    api_model = st.selectbox(
        "AI模型",
        ["glm-4-plus", "glm-4-flash", "glm-4"],
        index=0,
        help="glm-4-plus: 稳定强大(推荐) | glm-4-flash: 快速低成本"
    )
    
    check_interval = st.slider(
        "检查间隔(秒)",
        min_value=60,
        max_value=3600,
        value=300,
        step=60,
        help="盘中决策检查的时间间隔"
    )
    
    min_confidence = st.slider(
        "最小置信度",
        min_value=0.0,
        max_value=1.0,
        value=0.6,
        step=0.1,
        help="只显示置信度高于此值的交易信号"
    )
    
    enable_notifications = st.checkbox("启用风险预警通知", value=True)
    
    st.markdown("---")
    st.markdown("### 📖 使用说明")
    st.markdown("""
    1. 选择AI模型和参数
    2. 点击"生成决策"按钮
    3. 查看交易信号和风险预警
    4. 人工审核后再执行交易
    """)
    
    st.markdown("---")
    st.markdown("### ⚠️ 免责声明")
    st.markdown("""
    AI决策仅供参考,不构成投资建议。
    请人工审核后再执行交易。
    """)

# 主内容区域
col1, col2, col3 = st.columns([1, 1, 1])

with col1:
    if st.button("📊 生成决策", type="primary", use_container_width=True):
        st.session_state['generate_decision'] = True

with col2:
    if st.button("🔄 刷新持仓", use_container_width=True):
        st.session_state['refresh_positions'] = True

with col3:
    if st.button("📋 查看历史报告", use_container_width=True):
        st.session_state['view_reports'] = True

st.markdown("---")

# 生成决策
if st.session_state.get('generate_decision'):
    try:
        from utils.intraday_decision import IntradayDecisionMonitor
        
        with st.spinner("正在初始化AI决策引擎..."):
            monitor = IntradayDecisionMonitor(
                api_model=api_model,
                check_interval=check_interval,
                min_confidence=min_confidence,
                enable_notifications=enable_notifications,
            )
        
        with st.spinner("正在加载持仓数据..."):
            if not monitor.load_positions():
                st.error("❌ 持仓数据加载失败,请检查 config/positions.json")
                st.stop()
            st.success(f"✅ 已加载 {len(monitor.positions)} 只持仓")
        
        with st.spinner("正在调用GLM5生成交易决策... (需要10-30秒)"):
            decision = monitor.generate_decision()
        
        if not decision:
            st.error("❌ 决策生成失败")
            st.stop()
        
        st.success("✅ 决策生成完成!")
        
        # 显示决策结果
        st.markdown("---")
        
        # 市场概况
        st.subheader("📋 市场概况")
        st.markdown(decision.market_summary)
        
        # 交易信号
        st.subheader(f"📊 交易信号 ({len(decision.trading_signals)} 条)")
        
        if decision.trading_signals:
            # 过滤低置信度信号
            filtered_signals = [
                sig for sig in decision.trading_signals
                if sig.confidence >= min_confidence
            ]
            
            if filtered_signals:
                signal_data = []
                for sig in filtered_signals:
                    action_map = {'BUY': '买入', 'SELL': '卖出', 'HOLD': '持有', 'REDUCE': '减仓'}
                    action_cn = action_map.get(sig.action, sig.action)
                    
                    color = {'BUY': 'green', 'SELL': 'red', 'HOLD': 'blue', 'REDUCE': 'orange'}.get(sig.action, 'gray')
                    
                    signal_data.append({
                        '代码': sig.code,
                        '名称': sig.name,
                        '动作': action_cn,
                        '当前仓位': f"{sig.current_weight:.2%}",
                        '目标仓位': f"{sig.target_weight:.2%}",
                        '数量': sig.quantity,
                        '置信度': f"{sig.confidence:.2f}",
                        '紧急程度': sig.urgency,
                    })
                
                st.dataframe(signal_data, use_container_width=True)
                
                # 显示详细理由
                with st.expander("查看决策理由"):
                    for sig in filtered_signals:
                        action_map = {'BUY': '买入', 'SELL': '卖出', 'HOLD': '持有', 'REDUCE': '减仓'}
                        action_cn = action_map.get(sig.action, sig.action)
                        st.markdown(f"**{action_cn} {sig.code} {sig.name}**")
                        st.markdown(f"- 理由: {sig.reason}")
                        st.markdown(f"- 置信度: {sig.confidence:.2f}")
                        st.markdown(f"- 紧急程度: {sig.urgency}")
                        st.markdown("---")
            else:
                st.info(f"没有置信度 ≥ {min_confidence} 的交易信号")
        else:
            st.info("暂无交易信号 - 当前持仓无需调整")
        
        # 风险预警
        st.subheader(f"⚠️ 风险预警 ({len(decision.risk_alerts)} 条)")
        
        if decision.risk_alerts:
            for alert in decision.risk_alerts:
                if alert.severity == "CRITICAL":
                    st.error(f"🚨 [{alert.severity}] {alert.message}")
                elif alert.severity == "HIGH":
                    st.warning(f"⚠️ [{alert.severity}] {alert.message}")
                elif alert.severity == "MEDIUM":
                    st.info(f"⚡ [{alert.severity}] {alert.message}")
                else:
                    st.caption(f"ℹ️ [{alert.severity}] {alert.message}")
        else:
            st.success("✅ 暂无风险预警")
        
        # 组合调整建议
        if decision.portfolio_advice:
            st.subheader("💡 组合调整建议")
            st.markdown(decision.portfolio_advice)
        
        # 宏观展望
        if decision.macro_outlook:
            st.subheader("🔮 宏观展望")
            st.markdown(decision.macro_outlook)
        
        # AI置信度
        st.metric("📈 AI整体置信度", f"{decision.ai_confidence:.2%}")
        
        # 导出报告
        st.markdown("---")
        with st.spinner("正在导出决策报告..."):
            report_path = monitor.export_report(decision)
        
        if report_path:
            st.success(f"✅ 决策报告已保存: {report_path}")
            
            # 提供下载链接
            try:
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
                
                st.download_button(
                    label="📥 下载决策报告",
                    data=report_content,
                    file_name=os.path.basename(report_path),
                    mime="text/markdown",
                )
            except Exception:
                pass
        
    except ImportError:
        st.error("❌ GLM5决策模块未安装")
        st.code("pip install zhipuai", language='bash')
    except Exception as e:
        st.error(f"❌ 执行失败: {e}")
        st.exception(e)

# 刷新持仓
elif st.session_state.get('refresh_positions'):
    try:
        import json
        positions_path = Path(__file__).parent.parent / 'config' / 'positions.json'
        
        if positions_path.exists():
            with open(positions_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            positions = data.get('positions', {})
            cash = data.get('cash', 0)
            
            st.success(f"✅ 持仓数据已刷新 - {len(positions)} 只持仓, 现金: {cash:,.0f}元")
            
            # 显示持仓概览
            st.subheader("📊 持仓概览")
            
            active_positions = {k: v for k, v in positions.items() if v.get('shares', 0) > 0}
            
            if active_positions:
                holding_data = []
                for code, pos in active_positions.items():
                    holding_data.append({
                        '代码': code,
                        '名称': pos.get('name', code),
                        '股数': pos.get('shares', 0),
                        '成本价': pos.get('avg_cost', 0),
                        '目标权重': f"{pos.get('target_weight', 0):.1%}",
                        '类别': pos.get('category', 'unknown'),
                    })
                
                st.dataframe(holding_data, use_container_width=True)
            else:
                st.info("暂无活跃持仓")
        else:
            st.error(f"❌ 持仓文件不存在: {positions_path}")
            
    except Exception as e:
        st.error(f"❌ 刷新持仓失败: {e}")

# 查看历史报告
elif st.session_state.get('view_reports'):
    st.subheader("📋 历史决策报告")
    
    try:
        reports_dir = Path(__file__).parent.parent / 'reports'
        
        if not reports_dir.exists():
            st.info("暂无历史报告")
            st.stop()
        
        # 查找所有决策报告
        report_files = []
        for date_dir in sorted(reports_dir.iterdir(), reverse=True):
            if date_dir.is_dir():
                for report_file in date_dir.glob("盘中决策_*.md"):
                    report_files.append({
                        'path': report_file,
                        'date': date_dir.name,
                        'name': report_file.name,
                        'size': report_file.stat().st_size,
                    })
        
        if not report_files:
            st.info("暂无历史决策报告")
            st.stop()
        
        st.success(f"找到 {len(report_files)} 份历史报告")
        
        # 显示报告列表
        for i, report in enumerate(report_files[:20]):  # 最近20份
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.markdown(f"**{report['date']}** - {report['name']}")
            
            with col2:
                if st.button("查看", key=f"view_{i}"):
                    try:
                        with open(report['path'], 'r', encoding='utf-8') as f:
                            content = f.read()
                        st.markdown(content)
                    except Exception as e:
                        st.error(f"读取报告失败: {e}")
            
            st.markdown("---")
        
    except Exception as e:
        st.error(f"❌ 加载历史报告失败: {e}")

# 默认显示
else:
    st.markdown("""
    ## 🤖 GLM-5 盘中实时决策
    
    本模块使用 **GLM-5 AI** 分析市场数据和持仓数据,自动生成:
    
    - 📊 **交易信号**: 买入/卖出/持有/减仓建议
    - ⚠️ **风险预警**: 止损/止盈/仓位超标提醒
    - 💡 **组合调整建议**: 仓位优化方案
    - 🔮 **宏观展望**: 短期和中期的市场展望
    
    ### 📝 使用步骤
    
    1. 在左侧侧边栏选择AI模型和参数
    2. 点击上方"📊 生成决策"按钮
    3. 等待AI分析完成(约10-30秒)
    4. 查看交易信号和风险预警
    5. 人工审核后执行交易
    
    ### ⚠️ 重要提示
    
    - AI决策仅供参考,**不构成投资建议**
    - 请**人工审核**后再执行交易
    - 置信度低于0.6的建议需谨慎对待
    - 高风险预警(CRITICAL/HIGH)应立即处理
    """)
    
    # 显示系统状态
    st.markdown("---")
    st.subheader("📊 系统状态")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        try:
            import zhipuai
            st.metric("SDK状态", "✅ 已安装", f"zhipuai {zhipuai.__version__}")
        except ImportError:
            st.metric("SDK状态", "❌ 未安装", "pip install zhipuai")
    
    with col2:
        env_path = Path(__file__).parent.parent / '.env'
        if env_path.exists():
            st.metric("环境配置", "✅ .env存在")
        else:
            st.metric("环境配置", "❌ .env缺失")
    
    with col3:
        positions_path = Path(__file__).parent.parent / 'config' / 'positions.json'
        if positions_path.exists():
            st.metric("持仓文件", "✅ 存在")
        else:
            st.metric("持仓文件", "❌ 缺失")
