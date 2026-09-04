"""日历价差策略引擎 (Calendar Spread).

同一行权价、不同到期日的两份同类期权组成的价差组合,
卖出近月期权 + 买入远月期权, 利用近月 Theta 衰减快于远月获利.

业务规则 (spec.md §5.5):
    1. 同行权价: 两腿必须同一行权价
    2. 近远月顺序: 卖近月 + 买远月 (买入日历价差)
    3. 期限结构: 仅Contango(远月IV≥近月IV)时建仓
    4. 近月DTE: ∈ [20, 40], 首选30
    5. 禁止: 近月DTE < 5时禁止新建
"""

from __future__ import annotations

import logging
from datetime import date

from .combo_base import (
    ComboBase,
    ComboLeg,
    LegSide,
    OptionChainFetcher,
    StrategyType,
)

logger = logging.getLogger(__name__)

_ETF_OPTION_MULTIPLIER = 10000


class CalendarSpreadEngine(ComboBase):
    """日历价差策略引擎."""

    def __init__(
        self,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: object | None = None,
    ) -> None:
        super().__init__(
            StrategyType.CALENDAR_SPREAD, config, chain_fetcher,
            risk_manager, greek_manager, state_manager,
        )
        self._near_dte_min = config.get("near_month_dte_min", 20)
        self._near_dte_max = config.get("near_month_dte_max", 40)
        self._preferred_near_dte = config.get("preferred_near_dte", 30)
        self._far_dte_min = config.get("far_month_dte_min", 50)
        self._far_dte_max = config.get("far_month_dte_max", 90)
        self._require_contango = config.get("require_contango", True)
        self._blocked_near_dte = config.get("blocked_near_dte", 5)

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        """获取IV期限结构转占位链 (_select_legs 内部重新获取 iv_term)."""
        atm_strike = round(spot_price, 2)
        iv_term = self.chain_fetcher.get_iv_term_structure(underlying, atm_strike, spot_price=spot_price)
        return [{"strike": atm_strike, **t} for t in iv_term]

    def _select_legs(
        self, underlying: str, spot_price: float,
        option_chain: list[dict], spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        atm_strike = round(spot_price, 2)
        iv_term = self.chain_fetcher.get_iv_term_structure(underlying, atm_strike, spot_price=spot_price)
        if len(iv_term) < 2:
            return (None, "CAL_NO_IV_TERM_STRUCTURE")

        near = self._select_near_month(iv_term)
        if near is None:
            return (None, "CAL_NO_NEAR_MONTH")
        if near["dte"] < self._blocked_near_dte:
            return (None, "CAL_NEAR_EXPIRY_TOO_CLOSE")

        far = self._select_far_month(iv_term, near)
        if far is None:
            return (None, "CAL_NO_FAR_MONTH")

        if self._require_contango and far["iv"] < near["iv"]:
            logger.info("Backwardation: 远月IV %.4f < 近月IV %.4f", far["iv"], near["iv"])
            return (None, "CAL_BACKWARDATION")

        near_exp = date.fromisoformat(near["expiry"])
        far_exp = date.fromisoformat(far["expiry"])

        sell_near = ComboLeg(
            instrument="OPTION", underlying=underlying, option_type="CALL",
            side=LegSide.SELL, strike=atm_strike, expiry=near_exp,
            quantity=1, multiplier=_ETF_OPTION_MULTIPLIER, premium=near["premium"],
        )
        buy_far = ComboLeg(
            instrument="OPTION", underlying=underlying, option_type="CALL",
            side=LegSide.BUY, strike=atm_strike, expiry=far_exp,
            quantity=1, multiplier=_ETF_OPTION_MULTIPLIER, premium=far["premium"],
        )
        logger.info("日历价差: %s K=%.2f 卖近月DTE=%d+买远月DTE=%d", underlying, atm_strike, near["dte"], far["dte"])
        return (sell_near, buy_far)

    def _select_near_month(self, iv_term: list[dict]) -> dict | None:
        candidates = [t for t in iv_term if self._near_dte_min <= t["dte"] <= self._near_dte_max]
        if not candidates:
            return None
        return min(candidates, key=lambda t: abs(t["dte"] - self._preferred_near_dte))

    def _select_far_month(self, iv_term: list[dict], near: dict) -> dict | None:
        candidates = [t for t in iv_term if self._far_dte_min <= t["dte"] <= self._far_dte_max and t["dte"] > near["dte"]]
        if not candidates:
            candidates = [t for t in iv_term if t["dte"] > near["dte"] + 20]
        if not candidates:
            return None
        return min(candidates, key=lambda t: t["dte"])

    def _validate_business_rules(self, legs: tuple[ComboLeg, ...], spot_price: float) -> str | None:
        if len(legs) != 2:
            return "CAL_LEG_COUNT_MISMATCH"
        sell_leg, buy_leg = legs[0], legs[1]
        if sell_leg.strike != buy_leg.strike:
            return "CAL_STRIKE_MISMATCH"
        if sell_leg.option_type != buy_leg.option_type:
            return "CAL_TYPE_MISMATCH"
        if sell_leg.side != LegSide.SELL or buy_leg.side != LegSide.BUY:
            return "CAL_WRONG_DIRECTION"
        if sell_leg.expiry is None or buy_leg.expiry is None:
            return "CAL_NO_EXPIRY"
        if sell_leg.expiry >= buy_leg.expiry:
            return "CAL_WRONG_DIRECTION"
        near_dte = (sell_leg.expiry - date.today()).days
        if near_dte < self._blocked_near_dte:
            return "CAL_NEAR_EXPIRY_TOO_CLOSE"
        return None
