"""现金担保看跌策略引擎 (Cash-Secured Put).

备用等额现金的同时卖出虚值看跌期权, 若标的价格跌破行权价则按行权价买入标的.

业务规则 (spec.md §5.3):
    1. 现金担保: 冻结 strike × 10000 × qty 现金
    2. OTM看跌: OTM ∈ [3%, 8%], 默认5%
    3. 抄底建仓: 被指派后按行权价买入标的
    4. 权利金预算: 年化权利金收入 ≤ 总资本1.0%
    5. 禁止: Kill Switch触发时禁止新开仓
"""

from __future__ import annotations

import logging
from datetime import date

from .combo_base import (
    ComboBase,
    ComboLeg,
    ComboResult,
    LegSide,
    OptionChainFetcher,
    StrategyType,
)
from .combo_state import ComboStateManager

logger = logging.getLogger(__name__)

_ETF_OPTION_MULTIPLIER = 10000


class CashSecuredPutEngine(ComboBase):
    """现金担保看跌策略引擎."""

    def __init__(
        self,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: ComboStateManager | None = None,
    ) -> None:
        super().__init__(
            StrategyType.CASH_SECURED_PUT,
            config,
            chain_fetcher,
            risk_manager,
            greek_manager,
            state_manager,
        )
        self._otm_pct = config.get("otm_pct", 0.05)
        self._otm_range = config.get("otm_pct_range", (0.03, 0.08))
        self._dte_min = config.get("dte_min", 30)
        self._dte_max = config.get("dte_max", 60)
        self._preferred_dte = config.get("preferred_dte", 45)
        self._annual_budget = config.get("annual_budget_pct", 0.010)
        self._max_single_weight = config.get("max_single_weight", 0.20)
        self._blocked_levels = set(config.get("blocked_drawdown_levels", ["L2", "L3"]))

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        return self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type="PUT",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=self._otm_range, min_volume=0, spot_price=spot_price,
        )

    def _select_legs(
        self, underlying: str, spot_price: float,
        option_chain: list[dict], spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        target_weight = spot_position.get("target_weight", 0.0)
        current_weight = spot_position.get("current_weight", 0.0)
        if current_weight >= target_weight and target_weight > 0:
            logger.info("标的 %s 已达目标权重, 跳过CSP", underlying)
            return (None, "CSP_TARGET_WEIGHT_REACHED")

        available_cash = spot_position.get("available_cash", 0.0)
        if available_cash <= 0:
            return (None, "CSP_INSUFFICIENT_CASH")

        best = self._select_best_put(option_chain, spot_price, self._otm_pct)
        if best is None:
            return (None, "CSP_NO_OPTION_DATA")

        required_cash = best["strike"] * _ETF_OPTION_MULTIPLIER
        if available_cash < required_cash:
            logger.warning("现金不足: 需 %.0f, 可用 %.0f", required_cash, available_cash)
            return (None, "CSP_INSUFFICIENT_CASH")

        sell_qty = min(int(available_cash // required_cash), 1)
        expiry_str = best["expiry"]
        expiry = date.fromisoformat(expiry_str) if isinstance(expiry_str, str) else expiry_str

        leg = ComboLeg(
            instrument="OPTION", underlying=underlying, option_type="PUT",
            side=LegSide.SELL, strike=best["strike"], expiry=expiry,
            quantity=sell_qty, multiplier=_ETF_OPTION_MULTIPLIER,
            premium=best["premium"],
        )
        logger.info("CSP腿: %s K=%.2f DTE=%d premium=%.4f", underlying, leg.strike, best["dte"], leg.premium)
        return (leg,)

    def _select_best_put(self, chain: list[dict], spot: float, target_otm: float) -> dict | None:
        if not chain:
            return None
        scored: list[tuple[float, dict]] = []
        for c in chain:
            otm = (spot - c["strike"]) / spot
            if otm <= 0:
                continue
            scored.append((abs(otm - target_otm) * 10 + abs(c["dte"] - self._preferred_dte) / 60, c))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    def _validate_business_rules(self, legs: tuple[ComboLeg, ...], spot_price: float) -> str | None:
        for leg in legs:
            if leg.option_type != "PUT" or leg.side != LegSide.SELL:
                return "CSP_INVALID_LEG"
            otm = (spot_price - leg.strike) / spot_price
            if otm < self._otm_range[0] or otm > self._otm_range[1]:
                return "CSP_OTM_OUT_OF_RANGE"
            if leg.expiry is not None:
                dte = (leg.expiry - date.today()).days
                if dte < self._dte_min or dte > self._dte_max:
                    return "CSP_DTE_OUT_OF_RANGE"
        if self._is_budget_exceeded():
            return "CSP_BUDGET_EXCEEDED"
        if self._is_kill_switch_active():
            return "CSP_KILL_SWITCH_ACTIVE"
        if self._is_blocked_by_drawdown():
            return "CSP_BLOCKED_BY_DRAWDOWN"
        return None

    def handle_assignment(self, assigned_leg: ComboLeg) -> ComboResult:
        from datetime import datetime
        generated_at = datetime.now().isoformat(timespec="seconds")
        buy_shares = assigned_leg.quantity * _ETF_OPTION_MULTIPLIER
        logger.info("CSP被指派: %s 按K=%.2f买入%d份", assigned_leg.underlying, assigned_leg.strike, buy_shares)
        if self.state_manager is not None:
            try:
                total_capital = self.config.get("total_capital", 2_000_000)
                weight = (buy_shares * assigned_leg.strike) / total_capital
                if weight > self._max_single_weight:
                    logger.warning("标的 %s 权重 %.4f > %.4f, 触发再平衡", assigned_leg.underlying, weight, self._max_single_weight)
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        return ComboResult(
            strategy_type=StrategyType.CASH_SECURED_PUT, underlying=assigned_leg.underlying,
            orders=(), greeks=self._empty_greeks(), net_premium=0.0,
            budget_remaining=0.0, error_code=None, error_msg="ASSIGNMENT_HANDLED",
            generated_at=generated_at,
        )

    def _is_budget_exceeded(self) -> bool:
        if self.state_manager is None:
            return False
        try:
            budget = self.state_manager.get_budget(StrategyType.CASH_SECURED_PUT.value)
            total_capital = float(self.config.get("total_capital", 2_000_000))
            return bool(budget.get("ytd_income", 0.0) >= total_capital * self._annual_budget)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _is_kill_switch_active(self) -> bool:
        if self.risk_manager is None:
            return False
        try:
            risk_state: dict = getattr(self.risk_manager, "get_risk_state", lambda: {})()
            return bool(risk_state.get("kill_switch_active", False))
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _is_blocked_by_drawdown(self) -> bool:
        if self.risk_manager is None:
            return False
        try:
            risk_state: dict = getattr(self.risk_manager, "get_risk_state", lambda: {})()
            return bool(risk_state.get("drawdown_level", "L0") in self._blocked_levels)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
