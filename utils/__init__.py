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
    )
    from utils.infra.feature_flags import (
        audit_trail as flag_audit_trail,
    )
    from utils.infra.feature_flags import (
        disable as flag_disable,
    )
    from utils.infra.feature_flags import (
        enable as flag_enable,
    )
    from utils.infra.feature_flags import (
        is_enabled as flag_is_enabled,
    )
    from utils.infra.feature_flags import (
        list_flags as flag_list,
    )
    from utils.infra.feature_flags import (
        reload as flag_reload,
    )
except ImportError:  # pragma: no cover
    # yaml 未安装时静默跳过, 单测环境会单独处理
    pass

# ============================================================
# 原有模块 (v7.5 — 严禁删除, V9 基线依赖)
# ============================================================
# 原有模块
from .etf_flow_monitor import (
    ETFRealTimeTracker,
    get_etf_flow_summary,
    refresh_etf_flow_signals,
)
from .wt_backtest_engine import (
    BacktestDataLoader,
    BacktestEngine,
    ETFSignalStrategy,
    compare_strategies,
    run_etf_signal_backtest,
)
from .wt_contracts_manager import (
    DEFAULT_CONTRACTS,
    ContractsManager,
    get_contracts_manager,
)
from .wt_execution_algo import (
    MinImpactExecutor,
    TWAPExecutor,
    VWAPExecutor,
    execute_order_with_algorithm,
)
from .wt_hedge_strategy import (
    BetaHedgeStrategy,
    DynamicHedgeStrategy,
    HedgeContext,
    HedgePosition,
    HedgeStrategy,
    PortfolioMetrics,
    TailRiskHedgeStrategy,
)
from .wt_risk_control import (
    PortfolioRiskAnalyzer,
    RiskControl,
    RiskReportGenerator,
    StopLossManager,
    create_risk_control,
    create_stop_loss_manager,
)
from .wt_spread_strategy import (
    ETF_PAIR_SPREADS,
    SpreadBacktester,
    SpreadCalculator,
    SpreadContext,
    SpreadDefinition,
    SpreadStrategy,
)

# WonderTrader 风格新模块
from .wt_structs import (
    BarData,
    ContractData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
    bar_to_dict,
    tick_to_dict,
)
from .wt_tick_engine import (
    TickBacktestEngine,
    TickMatcher,
    bars_from_csv,
    run_tick_backtest,
    ticks_from_csv,
)

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
