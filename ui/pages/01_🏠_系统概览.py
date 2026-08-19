"""系统概览 — 参考 QuantMind Dashboard 设计的系统首页"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from datetime import datetime

import streamlit as st

st.title("🏠 系统概览")
st.caption("量化策略系统 v5.10 — 康波周期 + 十五五规划 + 社保基金ETF追踪 + AI交易竞技场")

from ui.components.common import inject_global_style
from ui.components.module_loader import get_system_module
from ui.components.system_status import (
    render_alert_card,
    render_connector_status,
    render_module_grid,
    render_status_card,
)

inject_global_style()

with st.spinner("正在加载系统模块..."):
    try:
        mod = get_system_module()
        system_ok = True
    except Exception as e:
        st.error(f"❌ 系统模块加载失败: {e}")
        st.stop()


# === 顶部指标卡（参考 QuantMind Dashboard） ===
st.markdown("### 📊 系统速览")
try:
    configs = mod.config_manager.get_all()
    strategy_count = len(mod.strategy_registry.list())
    etf_count = len(mod.ETFFundFlowMonitor.ETF_LIST)
    commodity_count = len(mod.KommoCommodityMonitor.COMMODITY_LIST)
    positions_count = 0
    try:
        import json
        positions_path = os.path.join(_BASE_DIR, 'config', 'positions.json')
        if os.path.exists(positions_path):
            with open(positions_path, encoding='utf-8') as f:
                pos_data = json.load(f)
            positions_count = len(pos_data.get('positions', {}))
    except Exception:
        pass
except Exception:
    configs = {}
    strategy_count = 0
    etf_count = 0
    commodity_count = 0
    positions_count = 0

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("📦 配置类别", len(configs))
c2.metric("🧩 注册策略", strategy_count)
c3.metric("📡 监测ETF", etf_count)
c4.metric("🛢️ 监测商品", commodity_count)
c5.metric("📋 持仓标的", positions_count)
c6.metric("🕐 当前时间", datetime.now().strftime('%H:%M'))

st.markdown("---")

# === 模块状态 ===
st.subheader("📦 模块可用性")


def _package_available(pkg_name):
    try:
        import importlib
        return importlib.util.find_spec(pkg_name) is not None
    except Exception:
        return False


modules = {
    '数据提供层': mod.data_provider.get('get_quotes_batch') is not None,
    '自动交易系统': mod.auto_trading.get('AutoTradingSystem') is not None,
    '再平衡引擎': mod.rebalance_engine.get('RebalancingEngine') is not None,
    '每日报告': mod.daily_report.get('generate_daily_report') is not None,
    '止损止盈监控': mod.stop_loss.get('StopLossMonitor') is not None,
    '策略注册表': mod.strategy_registry is not None,
    '连接器管理器': mod.connector_manager is not None,
    'ETF资金流向': True,
    '投资组合优化': _package_available('pandas') or _package_available('numpy'),
    '康波周期': _package_available('yfinance') or _package_available('tushare'),
    '十五五规划': mod.FIFTEEN_FIVE_AVAILABLE,
    '社保基金ETF': mod.SOCIAL_SECURITY_ETF_AVAILABLE,
}
render_module_grid(modules, cols=4)

st.markdown("---")

# === 连接器状态 ===
st.subheader("🔗 数据源连接器")
try:
    cs = mod.connector_manager.get_status()
    render_connector_status(cs)
except Exception as e:
    st.warning(f"连接器状态获取失败: {e}")

st.markdown("---")

# === 配置与目录 ===
col1, col2 = st.columns(2)
with col1:
    st.subheader("⚙️ 配置摘要")
    try:
        for k in configs:
            st.caption(f"• {k}")
    except Exception as e:
        st.warning(f"配置加载异常: {e}")

with col2:
    st.subheader("📁 目录状态")
    dirs = [
        ("数据缓存", os.path.join(_BASE_DIR, 'data', 'cache')),
        ("报告目录", os.path.join(_BASE_DIR, 'reports')),
        ("日志目录", getattr(mod, 'LOG_DIR', '')),
    ]
    for label, dpath in dirs:
        if os.path.exists(dpath):
            cnt = len(os.listdir(dpath))
            st.metric(label, f"{cnt} 个条目")
        else:
            st.metric(label, "不存在")

st.markdown("---")

# === 配置文件 ===
st.subheader("📋 配置文件")
config_files = [
    'config/portfolio.yaml', 'config/settings.yaml',
    'config/positions.json', 'config/rebalance.yaml',
    'config/risk.yaml',
]
cf_cols = st.columns(len(config_files))
for i, cf in enumerate(config_files):
    path = os.path.join(_BASE_DIR, cf)
    exists = os.path.exists(path)
    with cf_cols[i]:
        st.markdown(f"{'✅' if exists else '❌'} `{cf}`")

st.markdown("---")

# === 降级状态 ===
st.subheader("🛡️ 优雅降级")
try:
    fallback = mod.graceful_fallback.is_fallback_mode()
    if fallback:
        render_alert_card("优雅降级", "部分数据源不可用，已自动切换到备用数据源", level="warning")
    else:
        render_status_card("系统运行状态", "正常运行，未触发降级", level="success")
except Exception:
    render_status_card("降级状态检测", "暂不可用", level="info")

# 刷新
if st.button("🔄 刷新系统状态"):
    st.cache_resource.clear()
    st.rerun()
