"""对冲公共类型 (B3.3 抽取)

本模块集中对冲子系统共享的枚举与数据类, 避免 hedge_engine_v59 与 hedge_strategy_executor
重复定义导致 isinstance 失败。

依赖: dataclasses / enum (标准库)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


# ── 对冲类型枚举 ──
class HedgeType(Enum):
    NONE = "none"
    FUTURES_SHORT = "futures_short"
    PUT_PROTECTIVE = "put_protective"
    PUT_SPREAD = "put_spread"
    COLLAR = "collar"
    DYNAMIC_DELTA = "dynamic_delta"


# ── 对冲信号强度 ──
class HedgeSignalStrength(Enum):
    NO_HEDGE = 0
    LIGHT = 1  # 25%
    MODERATE = 2  # 50%
    STRONG = 3  # 75%
    FULL = 4  # 100%


# ── 对冲建议数据类 ──
@dataclass
class HedgeRecommendation:
    """对冲建议"""

    hedge_type: HedgeType = HedgeType.NONE
    strength: HedgeSignalStrength = HedgeSignalStrength.NO_HEDGE
    urgency_score: float = 0.0

    futures_instruments: List[str] = field(default_factory=list)
    futures_contracts: Dict[str, int] = field(default_factory=dict)
    futures_notional: Dict[str, float] = field(default_factory=dict)
    futures_margin: Dict[str, float] = field(default_factory=dict)

    options_instruments: List[str] = field(default_factory=list)
    options_strategy: str = ""
    options_contracts: List[Dict] = field(default_factory=list)
    options_cost: float = 0.0
    options_max_loss: float = 0.0

    hedge_ratio: float = 0.0
    effective_hedge_pct: float = 0.0
    expected_beta_after: float = 0.0
    expected_drawdown_reduce: float = 0.0

    reasoning: str = ""
    risk_signals: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    stress_tests: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # v5.10 P0-8
    sector_warnings: List[str] = field(default_factory=list)  # v5.10 P0-6
    correlation_warning: str = ""  # v5.10 P0-7
    mrc_warnings: List[str] = field(default_factory=list)  # v5.10 P0-6


__all__ = [
    "HedgeType",
    "HedgeSignalStrength",
    "HedgeRecommendation",
]
