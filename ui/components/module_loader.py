"""共享模块加载器 — 为 UI 页提供主系统模块的兼容属性层

历史问题 (SC-11, 2026-09-12):
    原实现 exec 一个从未入版本库的本地文件 ``量化策略系统 v5.10.py``
    (700 个提交中均不存在) —— 新克隆/CI/沙箱环境下 ``FileNotFoundError``,
    依赖它的 10 个 UI 页 (01/03/04/05/06/07/08/09/10/11) 全部不可用;
    且旧 v5.10 模块上的 ``fetch_etf_flow_data`` 等属性在统一入口 v8.6 中
    也从未存在, 属双重死链。

修复:
    不再 exec 任何入口脚本, 改为从 ``cli.handlers.support`` 直接导入
    真实模块符号 (各可用性 flag / 分析器类 / 追踪器), 并用
    ``cli.handlers.helpers.get_etf_flow_data`` 提供 ``fetch_etf_flow_data``
    等价能力 —— 单一事实源, 与 CLI 链同源。
"""

from __future__ import annotations

import os
import sys

import streamlit as st

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)


@st.cache_resource
def _load_system_module():
    """构造主系统模块的兼容属性层 (单一事实源: cli.handlers.support)."""
    from cli.handlers import helpers, support

    mod = _SystemModuleCompat()
    # 可用性 flag (与 CLI 链同源, 不再复制定义)
    for flag in (
        "SOCIAL_SECURITY_ETF_AVAILABLE",
        "FIFTEEN_FIVE_AVAILABLE",
        "KONDRATIEV_AVAILABLE",
    ):
        setattr(mod, flag, getattr(support, flag, False))
    # 分析器 / 追踪器 / 引擎类
    for name in (
        "SocialSecurityETFTracker",
        "FifteenFivePlanAnalyzer",
        "KondratievCycleAnalyzer",
        "KommoCommodityMonitor",
        "ETFFundFlowMonitor",
        "StrategyRegistry",
        "ExcelDrivenRebalancingEngineV4",
        "PortfolioOptimizationEngine",
    ):
        setattr(mod, name, getattr(support, name, None))
    # 数据获取 (原 v5.10 的 fetch_etf_flow_data → 集成版 helpers.get_etf_flow_data)
    mod.fetch_etf_flow_data = helpers.get_etf_flow_data
    # 系统概览页 (01) 的连接器/降级状态查询 —— 全部走 try/except 容错,
    # 这里提供缺省为 None 的安全桩 (页面自身已处理 None)
    for attr in (
        "config_manager",
        "connector_manager",
        "data_provider",
        "auto_trading",
        "rebalance_engine",
        "daily_report",
        "stop_loss",
        "graceful_fallback",
    ):
        object.__setattr__(mod, attr, getattr(support, attr, None))
    # _check_commodity_module 定义在 helpers
    mod._check_commodity_module = helpers._check_commodity_module
    return mod


class _SystemModuleCompat:
    """兼容层: 旧 UI 页访问的 ``mod.<attr>`` 全部在 _load_system_module
    中显式赋值; 未声明属性报可读 AttributeError (不再静默吞拼写错误)。"""

    def __getattr__(self, name: str) -> object:
        # 仅对"看起来像模块属性"的访问给可读错误; dunder 交给默认机制
        if name.startswith("__"):
            raise AttributeError(name)
        raise AttributeError(
            f"系统模块兼容层未提供 '{name}' — 请在 "
            f"ui/components/module_loader.py 的 _load_system_module 中显式接入"
        )


def get_system_module() -> _SystemModuleCompat:
    """返回缓存的主系统模块兼容层。"""
    return _load_system_module()
