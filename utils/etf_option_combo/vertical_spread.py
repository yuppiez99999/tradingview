"""垂直价差策略引擎 (Vertical Spread).

同一到期日、不同行权价的两份同类期权组成的价差组合.

业务规则 (spec.md §5.4):
    1. 同到期日: 两腿必须同一到期月份
    2. 行权价顺序: 借方认购买低卖高, 借方认沽买高卖低
    3. 价差宽度: ∈ [0.05, 0.30]
    4. 最大亏损: 净权利金支出 ≤ 单标的预算0.5%
    5. 禁止: 不得替代现货底仓
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
from .combo_state import ComboStateManager

logger = logging.getLogger(__name__)

_ETF_OPTION_MULTIPLIER = 10000
_SPREAD_TYPES = ("debit_call", "debit_put", "credit_call", "credit_put")


class VerticalSpreadEngine(ComboBase):
    """垂直价差策略引擎."""

    def __init__(
        self,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: ComboStateManager | None = None,
    ) -> None:
        super().__init__(
            StrategyType.VERTICAL_SPREAD, config, chain_fetcher,
            risk_manager, greek_manager, state_manager,
        )
        self._width_min = config.get("spread_width_min", 0.05)
        self._width_max = config.get("spread_width_max", 0.30)
        self._max_loss_pct = config.get("max_loss_budget_pct", 0.005)
        self._min_volume = config.get("min_volume", 100)
        self._dte_min = config.get("dte_min", 30)
        self._dte_max = config.get("dte_max", 60)
        self._preferred_dte = config.get("preferred_dte", 45)

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        """获取CALL期权链 (占位, _select_legs 内部按 spread_type 重新获取)."""
        return self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type="CALL",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=None, min_volume=self._min_volume, spot_price=spot_price,
        )

    def _select_legs(
        self, underlying: str, spot_price: float,
        option_chain: list[dict], spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        spread_type = spot_position.get("spread_type", "debit_call")
        if spread_type not in _SPREAD_TYPES:
            return (None, "VS_INVALID_SPREAD_TYPE")
        if spot_position.get("replace_spot", False):
            return (None, "VS_CANNOT_REPLACE_SPOT")

        option_type = "CALL" if "call" in spread_type else "PUT"
        chain = self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type=option_type,
            dte_range=(self._dte_min, self._dte_max),
            otm_range=None, min_volume=self._min_volume, spot_price=spot_price,
        )
        if not chain:
            return (None, "VS_NO_OPTION_DATA")

        pair = self._select_spread_pair(chain, spread_type)
        if pair is None:
            return (None, "VS_NO_VALID_PAIR")

        long_c, short_c = pair
        expiry_str = long_c["expiry"]
        expiry = date.fromisoformat(expiry_str) if isinstance(expiry_str, str) else expiry_str

        long_leg = ComboLeg(
            instrument="OPTION", underlying=underlying, option_type=option_type,
            side=LegSide.BUY, strike=long_c["strike"], expiry=expiry,
            quantity=1, multiplier=_ETF_OPTION_MULTIPLIER, premium=long_c["premium"],
        )
        short_leg = ComboLeg(
            instrument="OPTION", underlying=underlying, option_type=option_type,
            side=LegSide.SELL, strike=short_c["strike"], expiry=expiry,
            quantity=1, multiplier=_ETF_OPTION_MULTIPLIER, premium=short_c["premium"],
        )
        logger.info("垂直价差[%s]: %s 买K=%.2f+卫K=%.2f", spread_type, underlying, long_leg.strike, short_leg.strike)
        return (long_leg, short_leg)

    def _select_spread_pair(self, chain: list[dict], spread_type: str) -> tuple[dict, dict] | None:
        by_dte: dict[int, list[dict]] = {}
        for c in chain:
            by_dte.setdefault(c["dte"], []).append(c)
        if not by_dte:
            return None
        best_dte = min(by_dte.keys(), key=lambda d: abs(d - self._preferred_dte))
        contracts = sorted(by_dte[best_dte], key=lambda c: c["strike"])
        for i in range(len(contracts)):
            for j in range(i + 1, len(contracts)):
                low_c, high_c = contracts[i], contracts[j]
                width = high_c["strike"] - low_c["strike"]
                if width < self._width_min:
                    continue
                if width > self._width_max:
                    break
                if spread_type in ("debit_call", "credit_put"):
                    return (low_c, high_c)
                else:
                    return (high_c, low_c)
        return None

    def _validate_business_rules(self, legs: tuple[ComboLeg, ...], spot_price: float) -> str | None:
        if len(legs) != 2:
            return "VS_LEG_COUNT_MISMATCH"
        long_leg, short_leg = legs[0], legs[1]
        if long_leg.option_type != short_leg.option_type:
            return "VS_TYPE_MISMATCH"
        if long_leg.expiry != short_leg.expiry:
            return "VS_EXPIRY_MISMATCH"
        if long_leg.side != LegSide.BUY or short_leg.side != LegSide.SELL:
            return "VS_WRONG_SIDE"
        width = abs(long_leg.strike - short_leg.strike)
        if width < self._width_min:
            return "VS_WIDTH_TOO_NARROW"
        if width > self._width_max:
            return "VS_WIDTH_TOO_WIDE"
        net_cost = long_leg.premium - short_leg.premium
        total_capital = self.config.get("total_capital", 2_000_000)
        if net_cost * _ETF_OPTION_MULTIPLIER > total_capital * self._max_loss_pct:
            return "VS_MAX_LOSS_EXCEEDED"
        return None
