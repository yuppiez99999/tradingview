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
"""

# ============================================================
# 8.4 模块整合 — 新增 re-export (T1.4)
# ============================================================
# Feature Flag 框架 (T1.3 实现, 三层保护 Layer 3)
# 注意: 使用 try/except 避免单测环境下 yaml 缺失导致 import 失败
try:
    from utils.infra.feature_flags import (
        FeatureFlags,
        FlagError,
        FlagNotFoundError,
        FlagPermissionError,
        is_enabled as flag_is_enabled,
        enable as flag_enable,
        disable as flag_disable,
        list_flags as flag_list,
        audit_trail as flag_audit_trail,
        reload as flag_reload,
    )
except ImportError:  # pragma: no cover
    # yaml 未安装时静默跳过, 单测环境会单独处理
    pass

# ============================================================
# 原有模块 (v7.5 — 严禁删除, V9 基线依赖)
# ============================================================
# 原有模块
from .etf_flow_monitor import ETFRealTimeTracker, refresh_etf_flow_signals, get_etf_flow_summary
from .wt_execution_algo import MinImpactExecutor, TWAPExecutor, VWAPExecutor, execute_order_with_algorithm
from .wt_backtest_engine import BacktestEngine, ETFSignalStrategy, BacktestDataLoader, run_etf_signal_backtest, compare_strategies
from .wt_risk_control import RiskControl, StopLossManager, PortfolioRiskAnalyzer, RiskReportGenerator, create_risk_control, create_stop_loss_manager

# WonderTrader 风格新模块
from .wt_structs import (
    TickData, BarData, OrderData, TradeData,
    PositionData, ContractData,
    tick_to_dict, bar_to_dict,
)
from .wt_contracts_manager import (
    ContractsManager, DEFAULT_CONTRACTS, get_contracts_manager,
)
from .wt_spread_strategy import (
    SpreadDefinition, SpreadCalculator,
    SpreadStrategy, SpreadContext, SpreadBacktester,
    ETF_PAIR_SPREADS,
)
from .wt_hedge_strategy import (
    HedgePosition, PortfolioMetrics,
    HedgeStrategy, HedgeContext,
    BetaHedgeStrategy, TailRiskHedgeStrategy, DynamicHedgeStrategy,
)
from .wt_tick_engine import (
    TickMatcher, TickBacktestEngine,
    ticks_from_csv, bars_from_csv, run_tick_backtest,
)

__all__ = [
    # 8.4 模块整合 — Feature Flag (T1.4)
    'FeatureFlags',
    'FlagError',
    'FlagNotFoundError',
    'FlagPermissionError',
    'flag_is_enabled',
    'flag_enable',
    'flag_disable',
    'flag_list',
    'flag_audit_trail',
    'flag_reload',
    # 原有模块
    'ETFRealTimeTracker',
    'refresh_etf_flow_signals',
    'get_etf_flow_summary',
    'MinImpactExecutor',
    'TWAPExecutor',
    'VWAPExecutor',
    'execute_order_with_algorithm',
    'BacktestEngine',
    'ETFSignalStrategy',
    'BacktestDataLoader',
    'run_etf_signal_backtest',
    'compare_strategies',
    'RiskControl',
    'StopLossManager',
    'PortfolioRiskAnalyzer',
    'RiskReportGenerator',
    'create_risk_control',
    'create_stop_loss_manager',
    # WonderTrader 风格统一数据结构
    'TickData',
    'BarData',
    'OrderData',
    'TradeData',
    'PositionData',
    'ContractData',
    'tick_to_dict',
    'bar_to_dict',
    # 合约管理器
    'ContractsManager',
    'DEFAULT_CONTRACTS',
    'get_contracts_manager',
    # 价差策略
    'SpreadDefinition',
    'SpreadCalculator',
    'SpreadStrategy',
    'SpreadContext',
    'SpreadBacktester',
    'ETF_PAIR_SPREADS',
    # 对冲策略
    'HedgePosition',
    'PortfolioMetrics',
    'HedgeStrategy',
    'HedgeContext',
    'BetaHedgeStrategy',
    'TailRiskHedgeStrategy',
    'DynamicHedgeStrategy',
    # Tick 级回测引擎
    'TickMatcher',
    'TickBacktestEngine',
    'ticks_from_csv',
    'bars_from_csv',
    'run_tick_backtest',
]
