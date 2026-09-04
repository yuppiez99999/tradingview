"""ETF现货与期权联动对冲交易策略组合包.

提供五大联动对冲组合策略的建仓、调仓、风控监控与到期管理:
- 备兑看涨 (Covered Call)
- 领口策略 (Collar)
- 现金担保看跌 (Cash-Secured Put)
- 垂直价差 (Vertical Spread)
- 日历价差 (Calendar Spread)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .combo_base import ComboBase, OptionChainFetcher
    from .combo_backtest import ComboBacktest
    from .combo_orchestrator import ComboOrchestrator
    from .combo_risk_manager import ComboRiskManager
    from .combo_state import ComboStateManager
    from .covered_call import CoveredCallEngine
    from .collar import CollarEngine
    from .cash_secured_put import CashSecuredPutEngine
    from .vertical_spread import VerticalSpreadEngine
    from .calendar_spread import CalendarSpreadEngine

__all__ = [
    "ComboOrchestrator",
    "ComboBase",
    "CoveredCallEngine",
    "CollarEngine",
    "CashSecuredPutEngine",
    "VerticalSpreadEngine",
    "CalendarSpreadEngine",
    "ComboRiskManager",
    "ComboStateManager",
    "ComboBacktest",
    "OptionChainFetcher",
]


def __getattr__(name: str):
    if name == "ComboBase":
        from .combo_base import ComboBase
        return ComboBase
    if name == "OptionChainFetcher":
        from .combo_base import OptionChainFetcher
        return OptionChainFetcher
    if name == "CoveredCallEngine":
        from .covered_call import CoveredCallEngine
        return CoveredCallEngine
    if name == "CollarEngine":
        from .collar import CollarEngine
        return CollarEngine
    if name == "CashSecuredPutEngine":
        from .cash_secured_put import CashSecuredPutEngine
        return CashSecuredPutEngine
    if name == "VerticalSpreadEngine":
        from .vertical_spread import VerticalSpreadEngine
        return VerticalSpreadEngine
    if name == "CalendarSpreadEngine":
        from .calendar_spread import CalendarSpreadEngine
        return CalendarSpreadEngine
    if name == "ComboOrchestrator":
        from .combo_orchestrator import ComboOrchestrator
        return ComboOrchestrator
    if name == "ComboRiskManager":
        from .combo_risk_manager import ComboRiskManager
        return ComboRiskManager
    if name == "ComboStateManager":
        from .combo_state import ComboStateManager
        return ComboStateManager
    if name == "ComboBacktest":
        from .combo_backtest import ComboBacktest
        return ComboBacktest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")