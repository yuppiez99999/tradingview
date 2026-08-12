"""G15 工业级事件驱动回测引擎子包。

驱动 HedgeStrategy.on_tick/on_bar 回调;订单队列 + 撮合 + 延迟模型;
产出与 hedge_rebalance_backtest.BacktestResult 同构对象,便于偏差验证。

子模块:
    - order_queue:     订单队列(FIFO + 撤单 + 状态机)
    - constraints:     涨跌停/停牌约束工具
    - matching_engine: 撮合引擎(TICK/BAR/HYBRID 三模式)
    - latency_model:   延迟模型(Fixed/Random/Queue)      [Day 3]
    - adapters:        策略适配器 + DataFeed + ResultBuilder  [Day 3]
    - event_driven_engine: 主引擎 + 事件循环                [Day 4]
    - result_converter: EngineSummary → BacktestResult 转换 [Day 5]
    - vectorbt_bridge:  vectorbt 向量化回测对照桥接          [W6.4.1]
    - a_share_rules:    A 股 T+1 + 涨跌停规则 (SimTradeLab 借鉴) [W6.4.2]

设计原则(AGENTS.md):
    - 不可变性(§5.1): 所有状态变更走 dataclasses.replace()
    - 多小文件(§5.3): 每个模块 120-400 行
    - 复用数据类: 全量使用 utils.wt_structs 的 dataclass
"""
from utils.backtest.a_share_rules import (
    AShareTradingRules,
    PositionLot,
    T1FilterResult,
    T1PositionTracker,
    bar_to_date,
    filter_order_t1,
)
from utils.backtest.adapters import (
    OrderSubmitter,
    StrategyAdapter,
    _EngineBackedHedgeContext,
)
from utils.backtest.constraints import (
    check_tradable,
    is_at_limit,
    is_suspended,
)
from utils.backtest.event_driven_engine import (
    EngineSnapshot,
    EngineSummary,
    EventDrivenEngine,
    PendingOrder,
    Position,
)
from utils.backtest.latency_model import (
    FixedLatency,
    LatencyModel,
    QueueLatency,
    RandomLatency,
)
from utils.backtest.matching_engine import (
    FillEvent,
    FillReason,
    MatchingEngine,
    MatchingMode,
    OrderType,
    RejectReason,
)
from utils.backtest.order_queue import OrderQueue, OrderState
from utils.backtest.result_converter import ConversionConfig, ResultConverter
from utils.backtest.vectorbt_bridge import (
    ComparisonReport,
    VectorBtBridge,
    generate_ma_cross_signals,
)
from utils.backtest.deflated_sharpe import (  # W6.6.2 T07 修复
    DSRResult,
    deflated_sharpe_ratio,
)
from utils.backtest.honest_validation import (  # W6.6.2 三件套编排器
    CPCVSummary,
    HonestValidationResult,
    run_honest_validation,
)

__all__ = [
    # adapters
    "OrderSubmitter",
    "StrategyAdapter",
    "_EngineBackedHedgeContext",
    # order_queue
    "OrderQueue",
    "OrderState",
    # constraints
    "check_tradable",
    "is_at_limit",
    "is_suspended",
    # matching_engine
    "FillEvent",
    "FillReason",
    "MatchingEngine",
    "MatchingMode",
    "OrderType",
    "RejectReason",
    # latency_model
    "FixedLatency",
    "LatencyModel",
    "QueueLatency",
    "RandomLatency",
    # event_driven_engine
    "EngineSnapshot",
    "EngineSummary",
    "EventDrivenEngine",
    "PendingOrder",
    "Position",
    # result_converter
    "ConversionConfig",
    "ResultConverter",
    # vectorbt_bridge (W6.4.1)
    "ComparisonReport",
    "VectorBtBridge",
    "generate_ma_cross_signals",
    # a_share_rules (W6.4.2)
    "AShareTradingRules",
    "PositionLot",
    "T1FilterResult",
    "T1PositionTracker",
    "bar_to_date",
    "filter_order_t1",
    # deflated_sharpe (W6.6.2 T07 修复)
    "DSRResult",
    "deflated_sharpe_ratio",
    # honest_validation (W6.6.2 三件套编排器)
    "CPCVSummary",
    "HonestValidationResult",
    "run_honest_validation",
]
