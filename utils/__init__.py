"""
v7.5 工具包

含 WonderTrader/wtpy 风格集成模块:
- wt_structs: 统一数据结构 (Tick/Bar/Order/Trade/Position/Contract)
- wt_contracts_manager: 合约规格管理器
- wt_spread_strategy: 价差策略框架 (ETF配对/期现套利)
- wt_hedge_strategy: 组合对冲策略模板 (Beta/尾部风险/动态)
- wt_tick_engine: Tick级事件驱动回测引擎
"""

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
