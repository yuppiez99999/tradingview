"""自动对冲再平衡进化系统 v1.0。

本包实现系统自主根据市场状态选择 ETF/期权/期货对冲工具并执行自动再平衡，
约束年化收益 ≥ 8% 且最大回撤 < 20%。

核心组件 (8 个):
    AutoHedgeRebalanceEngine  — 主协调器，编排 10 阶段决策闭环
    AuditLogger               — 决策审计日志 (SQLite 持久化)
    HedgeToolDataFetcher      — 对冲工具行情获取器 (三类工具降级链)
    CostBenefitFilter         — 成本效益过滤器 (1.5 倍阈值)
    HedgeToolSelector         — 对冲工具自动选择器 (决策表选择)
    StrategyStateMachine      — 策略等级状态机 (6 档 + 冷却期)
    CircuitBreaker            — 紧急熔断器 (单日跌幅>5%/回撤>25%)
    TargetMonitor             — 目标达成监控器 (滚动 252 日 + 纠偏 + 预检)

使用示例:
    from utils.auto_hedge_rebalance import AutoHedgeRebalanceEngine
    engine = AutoHedgeRebalanceEngine(config_path="config/auto_hedge_rebalance.yaml")
    plan = engine.run_eod_decision(portfolio_volatility=0.18, portfolio_drawdown_60d=0.05)
"""

from __future__ import annotations

from utils.auto_hedge_rebalance.audit_logger import AuditLogger
from utils.auto_hedge_rebalance.circuit_breaker import CircuitBreaker, EmergencyAction
from utils.auto_hedge_rebalance.cost_benefit_filter import CostBenefitFilter, PortfolioRisk
from utils.auto_hedge_rebalance.data_fetcher import HedgeToolDataFetcher
from utils.auto_hedge_rebalance.engine import AutoHedgeRebalanceEngine
from utils.auto_hedge_rebalance.exceptions import (
    AllHedgeToolPriceUnavailable,
    AllToolsUnavailable,
    AutoHedgeRebalanceError,
    BacktestPrecheckTimeout,
    CircuitBreakerActive,
    PortfolioRiskAssessmentError,
    RegimeIsCalm,
    ToolPriceUnavailable,
)
from utils.auto_hedge_rebalance.models import (
    AuditRecord,
    AutoHedgePlan,
    BreakerStatus,
    CorrectionAction,
    FilterResult,
    HedgeToolType,
    MonitorResult,
    OptionsStrategy,
    PrecheckResult,
    StrategyLevel,
    StrategyState,
    StrategySwitchEvent,
    ToolSelection,
    TransitionResult,
)
from utils.auto_hedge_rebalance.strategy_state_machine import StrategyStateMachine
from utils.auto_hedge_rebalance.target_monitor import TargetMonitor
from utils.auto_hedge_rebalance.tool_selector import HedgeToolSelector, MarketRegime

__all__ = [
    # 主协调器
    "AutoHedgeRebalanceEngine",
    # 8个组件类
    "AuditLogger",
    "HedgeToolDataFetcher",
    "CostBenefitFilter",
    "HedgeToolSelector",
    "StrategyStateMachine",
    "CircuitBreaker",
    "TargetMonitor",
    # 辅助类型
    "PortfolioRisk",
    "EmergencyAction",
    "MarketRegime",
    # 异常
    "AutoHedgeRebalanceError",
    "PortfolioRiskAssessmentError",
    "AllHedgeToolPriceUnavailable",
    "BacktestPrecheckTimeout",
    "CircuitBreakerActive",
    "ToolPriceUnavailable",
    "RegimeIsCalm",
    "AllToolsUnavailable",
    # 枚举
    "StrategyLevel",
    "CorrectionAction",
    "HedgeToolType",
    "OptionsStrategy",
    # 数据类
    "ToolSelection",
    "FilterResult",
    "PrecheckResult",
    "MonitorResult",
    "StrategyState",
    "StrategySwitchEvent",
    "TransitionResult",
    "BreakerStatus",
    "AutoHedgePlan",
    "AuditRecord",
]

__version__ = "1.0.0"