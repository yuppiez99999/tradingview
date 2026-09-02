"""
v7.5 工具包 (8.4 模块整合 Phase 1 更新)

含 WonderTrader/wtpy 风格集成模块:
- wt_structs: 统一数据结构 (Tick/Bar/Order/Trade/Position/Contract)
- wt_contracts_manager: 合约规格管理器
- wt_spread_strategy: 价差策略框架 (ETF配对/期现套利)
- wt_hedge_strategy: 组合对冲策略模板 (Beta/尾部风险/动态)
- wt_tick_engine: Tick级事件驱动回测引擎

8.4 模块整合 (T1.4) 新增 re-export:
- FeatureFlags: 三层保护 Layer 3 (utils/infra/feature_flags.py)

迁移原则 (ARCHITECTURE §2.2):
- 子模块物理位置迁移后, 旧路径通过此文件 re-export 保持可用
- 严禁删除 re-export, 否则破坏 V9 基线 (HC-1)
- 验证脚本: scripts/_verify_reexport_compat.py

P0-3 懒加载 (2026-09-01):
- 原实现 eager import 9 个 wt_*/etf 模块, 经测量把 scipy.stats (2.2s)
  和 pandas (1.5s) 整条链拉进任何 `import utils.*` — import 耗时 5.3s
- 现改为 PEP 562 module-level __getattr__: 所有 re-export 符号与
  `from utils import X` / `from utils import *` 用法 100% 兼容 (HC-1 不变),
  但首次访问才真正 import 对应子模块; import utils.data_provider < 1s
"""

from __future__ import annotations

import importlib
from typing import Any

# ============================================================
# P0-3 (2026-09-01): 懒 re-export 表
# 符号名 → (所属子模块, 子模块内原名); 原名为 None 表示同名 re-export
# 与原 eager `from .xxx import y [as z]` 完全等价, 仅延迟到首次访问
# ============================================================
_LAZY_REEXPORTS: dict[str, tuple[str, str | None]] = {
    # feature flag 框架 (T1.3, 三层保护 Layer 3; 均为别名 re-export)
    "FeatureFlags": ("utils.infra.feature_flags", None),
    "FlagError": ("utils.infra.feature_flags", None),
    "FlagNotFoundError": ("utils.infra.feature_flags", None),
    "FlagPermissionError": ("utils.infra.feature_flags", None),
    "flag_audit_trail": ("utils.infra.feature_flags", "audit_trail"),
    "flag_disable": ("utils.infra.feature_flags", "disable"),
    "flag_enable": ("utils.infra.feature_flags", "enable"),
    "flag_is_enabled": ("utils.infra.feature_flags", "is_enabled"),
    "flag_list": ("utils.infra.feature_flags", "list_flags"),
    "flag_reload": ("utils.infra.feature_flags", "reload"),
    # etf_flow_monitor
        "ETFRealTimeTracker": ("utils.etf_flow_monitor", None),
        "get_etf_flow_summary": ("utils.etf_flow_monitor", None),
        "refresh_etf_flow_signals": ("utils.etf_flow_monitor", None),
    # wt_backtest_engine
        "BacktestDataLoader": ("utils.wt_backtest_engine", None),
        "BacktestEngine": ("utils.wt_backtest_engine", None),
        "ETFSignalStrategy": ("utils.wt_backtest_engine", None),
        "compare_strategies": ("utils.wt_backtest_engine", None),
        "run_etf_signal_backtest": ("utils.wt_backtest_engine", None),
    # wt_contracts_manager
        "DEFAULT_CONTRACTS": ("utils.wt_contracts_manager", None),
        "ContractsManager": ("utils.wt_contracts_manager", None),
        "get_contracts_manager": ("utils.wt_contracts_manager", None),
    # wt_execution_algo
        "MinImpactExecutor": ("utils.wt_execution_algo", None),
        "TWAPExecutor": ("utils.wt_execution_algo", None),
        "VWAPExecutor": ("utils.wt_execution_algo", None),
        "execute_order_with_algorithm": ("utils.wt_execution_algo", None),
    # wt_hedge_strategy
        "BetaHedgeStrategy": ("utils.wt_hedge_strategy", None),
        "DynamicHedgeStrategy": ("utils.wt_hedge_strategy", None),
        "HedgeContext": ("utils.wt_hedge_strategy", None),
        "HedgePosition": ("utils.wt_hedge_strategy", None),
        "HedgeStrategy": ("utils.wt_hedge_strategy", None),
        "PortfolioMetrics": ("utils.wt_hedge_strategy", None),
        "TailRiskHedgeStrategy": ("utils.wt_hedge_strategy", None),
    # wt_risk_control
        "PortfolioRiskAnalyzer": ("utils.wt_risk_control", None),
        "RiskControl": ("utils.wt_risk_control", None),
        "RiskReportGenerator": ("utils.wt_risk_control", None),
        "StopLossManager": ("utils.wt_risk_control", None),
        "create_risk_control": ("utils.wt_risk_control", None),
        "create_stop_loss_manager": ("utils.wt_risk_control", None),
    # wt_spread_strategy
        "ETF_PAIR_SPREADS": ("utils.wt_spread_strategy", None),
        "SpreadBacktester": ("utils.wt_spread_strategy", None),
        "SpreadCalculator": ("utils.wt_spread_strategy", None),
        "SpreadContext": ("utils.wt_spread_strategy", None),
        "SpreadDefinition": ("utils.wt_spread_strategy", None),
        "SpreadStrategy": ("utils.wt_spread_strategy", None),
    # wt_structs
        "BarData": ("utils.wt_structs", None),
        "ContractData": ("utils.wt_structs", None),
        "OrderData": ("utils.wt_structs", None),
        "PositionData": ("utils.wt_structs", None),
        "TickData": ("utils.wt_structs", None),
        "TradeData": ("utils.wt_structs", None),
        "bar_to_dict": ("utils.wt_structs", None),
        "tick_to_dict": ("utils.wt_structs", None),
    # wt_tick_engine
        "TickBacktestEngine": ("utils.wt_tick_engine", None),
        "TickMatcher": ("utils.wt_tick_engine", None),
        "bars_from_csv": ("utils.wt_tick_engine", None),
        "run_tick_backtest": ("utils.wt_tick_engine", None),
        "ticks_from_csv": ("utils.wt_tick_engine", None),
}


class _Missing:
    """哨兵: 区分 getattr 返回 None 与属性不存在"""


_MISSING = _Missing()


def __getattr__(name: str) -> Any:
    """PEP 562 懒 re-export — 首次访问时 import 对应子模块并缓存到 globals

    兼容语义:
        from utils import BacktestEngine   → 触发本函数, 与原 eager import 等价
        from utils import flag_is_enabled  → 别名 re-export (原模块内叫 is_enabled)
        from utils import trade_calendar   → 子模块 fallback, importlib 动态加载
        utils.wt_structs                   → 子模块属性访问, 动态加载
        不存在的符号                       → AttributeError (与原行为一致)
    """
    entry = _LAZY_REEXPORTS.get(name)
    if entry is not None:
        module_name, orig_name = entry
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            # 原 __init__ 对 feature_flags 用 try/except ImportError 静默降级,
            # 保持一致: 不可用时表现为 AttributeError 而非崩溃
            raise AttributeError(
                f"module 'utils' cannot re-export {name!r}: "
                f"import {module_name} failed (依赖缺失)"
            ) from None
        attr = getattr(module, orig_name or name, _MISSING)
        if attr is _MISSING:
            raise AttributeError(
                f"module {module_name!r} has no attribute {orig_name or name!r}"
            )
        # 缓存到 globals: 后续访问零开销, 且 pickle/inspect 可直接工作
        globals()[name] = attr
        return attr

    # 子模块访问兼容 (utils.trade_calendar / utils.wt_structs 等)
    try:
        return importlib.import_module(f"utils.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module 'utils' has no attribute {name!r}") from None


def __dir__() -> list[str]:
    """dir(utils) 包含全部懒 re-export 符号 (与原 __all__ 一致)"""
    return sorted(set(globals()) | set(_LAZY_REEXPORTS))


__all__ = [
    "DEFAULT_CONTRACTS",
    "ETF_PAIR_SPREADS",
    "BacktestDataLoader",
    "BacktestEngine",
    "BarData",
    "BetaHedgeStrategy",
    "ContractData",
    # 合约管理器
    "ContractsManager",
    "DynamicHedgeStrategy",
    # 原有模块
    "ETFRealTimeTracker",
    "ETFSignalStrategy",
    # 8.4 模块整合 — Feature Flag (T1.4)
    "FeatureFlags",
    "FlagError",
    "FlagNotFoundError",
    "FlagPermissionError",
    "HedgeContext",
    # 对冲策略
    "HedgePosition",
    "HedgeStrategy",
    "MinImpactExecutor",
    "OrderData",
    "PortfolioMetrics",
    "PortfolioRiskAnalyzer",
    "PositionData",
    "RiskControl",
    "RiskReportGenerator",
    "SpreadBacktester",
    "SpreadCalculator",
    "SpreadContext",
    # 价差策略
    "SpreadDefinition",
    "SpreadStrategy",
    "StopLossManager",
    "TWAPExecutor",
    "TailRiskHedgeStrategy",
    "TickBacktestEngine",
    # WonderTrader 风格统一数据结构
    "TickData",
    # Tick 级回测引擎
    "TickMatcher",
    "TradeData",
    "VWAPExecutor",
    "bar_to_dict",
    "bars_from_csv",
    "compare_strategies",
    "create_risk_control",
    "create_stop_loss_manager",
    "execute_order_with_algorithm",
    "flag_audit_trail",
    "flag_disable",
    "flag_enable",
    "flag_is_enabled",
    "flag_list",
    "flag_reload",
    "get_contracts_manager",
    "get_etf_flow_summary",
    "refresh_etf_flow_signals",
    "run_etf_signal_backtest",
    "run_tick_backtest",
    "tick_to_dict",
    "ticks_from_csv",
]
