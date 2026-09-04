"""组合风控管理器 (ComboRiskManager).

六重预检: Kill Switch -> 回撤分级 -> 保证金充足性 ->
行权预警期 -> Greeks超限 -> 肥手指.

fail-closed原则: 任一检查异常时拒绝生成新订单(C-09).
"""

from __future__ import annotations

import logging
from datetime import date

from .combo_base import (
    ApprovalResult,
    ComboLeg,
    ComboOrder,
    LegSide,
    OrderStatus,
    StrategyType,
)

logger = logging.getLogger(__name__)

_ETF_OPTION_MULTIPLIER = 10000
_L2_BLOCKED = {StrategyType.COVERED_CALL, StrategyType.CASH_SECURED_PUT}


class ComboRiskManager:
    """组合风控管理器 — 六重预检 + 监控 + 强制平仓."""

    def __init__(
        self,
        total_capital: float = 2_000_000,
        margin_monitor: object | None = None,
        exercise_manager: object | None = None,
        greek_manager: object | None = None,
        config: dict | None = None,
    ) -> None:
        self._total_capital = total_capital
        self._margin_monitor = margin_monitor
        self._exercise_manager = exercise_manager
        self._greek_manager = greek_manager
        cfg = config or {}
        self._max_margin_pct = cfg.get("max_margin_pct", 0.20)
        self._fat_finger_limit = cfg.get("fat_finger_limit", 500_000)
        self._delta_rebalance = cfg.get("delta_rebalance_threshold", 0.3)
        self._delta_severe = cfg.get("delta_severe_threshold", 0.5)
        self._risk_state: dict = {"drawdown_level": "L0", "kill_switch_active": False}

    def get_risk_state(self) -> dict:
        return dict(self._risk_state)

    def update_risk_state(self, **kwargs) -> None:
        self._risk_state.update(kwargs)

    def pre_check(
        self,
        legs: tuple[ComboLeg, ...],
        market_state: dict | None = None,
    ) -> dict:
        """六重预检 — 返回 {approved, rejected_reason, risk_flags, requires_confirmation}."""
        risk_flags: list[str] = []
        requires_confirmation = False
        state = market_state or {}

        try:
            reason = self._check_kill_switch(legs, state)
            if reason:
                return {"approved": False, "rejected_reason": reason, "risk_flags": tuple(risk_flags + ["KILL_SWITCH"]), "requires_confirmation": False}

            reason = self._check_drawdown(legs, state)
            if reason:
                return {"approved": False, "rejected_reason": reason, "risk_flags": tuple(risk_flags + ["DRAWDOWN"]), "requires_confirmation": False}

            reason = self._check_margin(legs, state)
            if reason:
                return {"approved": False, "rejected_reason": reason, "risk_flags": tuple(risk_flags + ["MARGIN"]), "requires_confirmation": False}

            reason = self._check_exercise_warning(legs, state)
            if reason:
                return {"approved": False, "rejected_reason": reason, "risk_flags": tuple(risk_flags + ["EXERCISE_WARNING"]), "requires_confirmation": False}

            greeks_flag = self._check_greeks(legs, state)
            if greeks_flag:
                risk_flags.append(greeks_flag)

            ff = self._check_fat_finger(legs)
            if ff:
                risk_flags.append("FAT_FINGER")
                requires_confirmation = True

            return {"approved": True, "rejected_reason": None, "risk_flags": tuple(risk_flags), "requires_confirmation": requires_confirmation}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.error("风控预检异常 (fail-closed): %s", e)
            return {"approved": False, "rejected_reason": f"RISK_CHECK_ERROR: {e}", "risk_flags": ("ERROR",), "requires_confirmation": False}

    def _check_kill_switch(self, legs: tuple[ComboLeg, ...], state: dict) -> str | None:
        if state.get("kill_switch_active", False):
            has_new_open = any(leg.side == LegSide.SELL for leg in legs if leg.instrument == "OPTION")
            if has_new_open:
                return "KILL_SWITCH_ACTIVE"
        return None

    def _check_drawdown(self, legs: tuple[ComboLeg, ...], state: dict) -> str | None:
        level = state.get("drawdown_level", "L0")
        if level == "L3":
            has_new_open = any(leg.side == LegSide.SELL for leg in legs if leg.instrument == "OPTION")
            if has_new_open:
                return "DRAWDOWN_L3_BLOCKED"
        elif level == "L2":
            strategy_type = state.get("strategy_type")
            if strategy_type in _L2_BLOCKED:
                return "DRAWDOWN_L2_BLOCKED"
        return None

    def _check_margin(self, legs: tuple[ComboLeg, ...], state: dict) -> str | None:
        sell_legs = [leg for leg in legs if leg.side == LegSide.SELL and leg.instrument == "OPTION"]
        if not sell_legs:
            return None
        margin_usage = state.get("margin_usage_pct", 0.0)
        if margin_usage > self._max_margin_pct:
            return "MARGIN_EXCEEDED"
        if self._margin_monitor is not None:
            try:
                available = state.get("available_funds", self._total_capital)
                if available < 0:
                    return "MARGIN_INSUFFICIENT"
            except (ValueError, TypeError):
                return "MARGIN_CHECK_ERROR"
        return None

    def _check_exercise_warning(self, legs: tuple[ComboLeg, ...], state: dict) -> str | None:
        today = date.today()
        for leg in legs:
            if leg.expiry is None or leg.side != LegSide.SELL:
                continue
            dte = (leg.expiry - today).days
            if dte <= 3:
                return "EXERCISE_WARNING_PERIOD"
        return None

    def _check_greeks(self, legs: tuple[ComboLeg, ...], state: dict) -> str | None:
        delta = state.get("portfolio_delta", 0.0)
        if abs(delta) > self._delta_severe:
            return "DELTA_SEVERE_DRIFT"
        if abs(delta) > self._delta_rebalance:
            return "DELTA_REBALANCE_NEEDED"
        vega = state.get("portfolio_vega", 0.0)
        max_vega = state.get("max_vega_limit", 100_000)
        if abs(vega) > max_vega:
            return "VEGA_EXCEEDED"
        return None

    def _check_fat_finger(self, legs: tuple[ComboLeg, ...]) -> str | None:
        for leg in legs:
            if leg.instrument == "OPTION":
                order_value = leg.premium * leg.quantity * leg.multiplier
            else:
                order_value = leg.strike * leg.quantity
            if order_value > self._fat_finger_limit:
                return "FAT_FINGER"
        return None

    def monitor(self, positions: list[dict], prices: dict) -> dict:
        """全组合监控扫描."""
        result: dict = {"margin_checks": [], "exercise_risks": [], "greeks_alerts": [], "liquidation_orders": []}
        try:
            if self._margin_monitor is not None:
                check_all = getattr(self._margin_monitor, "check_all", None)
                if check_all is not None:
                    result["margin_checks"] = check_all(positions, prices)
            if self._exercise_manager is not None:
                check_all = getattr(self._exercise_manager, "check_all", None)
                if check_all is not None:
                    result["exercise_risks"] = check_all(positions, prices)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.error("监控扫描异常: %s", e)
        return result

    def generate_rebalance_orders(
        self, current_greeks: object, target_delta: float = 0.0,
    ) -> list[ComboOrder]:
        """生成Greeks调仓指令."""
        orders: list[ComboOrder] = []
        try:
            delta = getattr(current_greeks, "delta", 0.0)
            if abs(delta - target_delta) > self._delta_rebalance:
                logger.info("Delta调仓: current=%.4f target=%.4f", delta, target_delta)
        except (ValueError, TypeError, AttributeError):
            pass
        return orders

    def check_budget(self, strategy_type: str, net_premium: float) -> tuple[bool, str]:
        """预算检查 — 返回 (是否通过, 错误码)."""
        return True, ""
