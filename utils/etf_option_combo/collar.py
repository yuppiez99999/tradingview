"""领口策略引擎 (Collar).

持有现货 + 买入虚值看跌期权 (下行保护) + 卖出虚值看涨期权 (融资),
构建零成本或低成本的下行保护组合.

业务规则 (spec.md §5.2):
    1. 三腿构建: 现货多头 + 买OTM Put + 卖OTM Call, 同标的
    2. 零成本目标: Put权利金 ≈ Call权利金
    3. 保护带宽度: (Call.strike - Put.strike) / spot ≥ 10%
    4. Put OTM上限: Put OTM ≤ 10%
    5. 禁止: 回撤L3时禁止新开领口
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


class CollarEngine(ComboBase):
    """领口策略引擎.

    现货 + 买Put + 卖Call, 零成本下行保护.
    """

    def __init__(
        self,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: ComboStateManager | None = None,
        protective_put_engine: object | None = None,
    ) -> None:
        super().__init__(
            StrategyType.COLLAR,
            config,
            chain_fetcher,
            risk_manager,
            greek_manager,
            state_manager,
        )
        self._put_otm_pct = config.get("put_otm_pct", 0.05)
        self._call_otm_pct = config.get("call_otm_pct", 0.05)
        self._protection_band_min = config.get("protection_band_min", 0.10)
        self._put_otm_max = config.get("put_otm_max", 0.10)
        self._max_net_cost_pct = config.get("max_net_cost_pct", 0.005)
        self._dte_min = config.get("dte_min", 30)
        self._dte_max = config.get("dte_max", 60)
        self._preferred_dte = config.get("preferred_dte", 45)
        self._blocked_levels = set(config.get("blocked_drawdown_levels", ["L3"]))
        self._protective_put_engine = protective_put_engine

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        """获取CALL期权链 (占位, _select_legs 内部重新获取 put/call 链)."""
        return self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type="CALL",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=(0.02, 0.08), min_volume=0, spot_price=spot_price,
        )

    def _select_legs(
        self,
        underlying: str,
        spot_price: float,
        option_chain: list[dict],
        spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        """选择领口三腿 — 现货 + 买Put + 卖Call."""
        shares = spot_position.get("shares", 0)
        if shares < _ETF_OPTION_MULTIPLIER:
            return (None, "COLLAR_NO_UNDERLYING")

        put_chain = self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type="PUT",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=(0.02, self._put_otm_max),
            min_volume=0, spot_price=spot_price,
        )
        call_chain = self.chain_fetcher.get_option_chain(
            underlying=underlying, option_type="CALL",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=(0.02, 0.08),
            min_volume=0, spot_price=spot_price,
        )

        if not put_chain or not call_chain:
            return (None, "COLLAR_NO_OPTION_DATA")

        has_existing_put = self._has_existing_put_protection(underlying)
        pair = self._search_zero_cost_pair(put_chain, call_chain, spot_price)

        if pair is None:
            logger.warning("标的 %s 无法构建零/低成本领口", underlying)
            return (None, "COLLAR_NO_ZERO_COST")

        put_contract, call_contract = pair
        expiry_str = put_contract["expiry"]
        expiry = date.fromisoformat(expiry_str) if isinstance(expiry_str, str) else expiry_str

        legs: list[ComboLeg] = []
        if not has_existing_put:
            legs.append(ComboLeg(
                instrument="OPTION", underlying=underlying, option_type="PUT",
                side=LegSide.BUY, strike=put_contract["strike"], expiry=expiry,
                quantity=1, multiplier=_ETF_OPTION_MULTIPLIER,
                premium=put_contract["premium"],
            ))
        else:
            logger.info("标的 %s 已有认沽保护, 仅新增Call腿", underlying)

        legs.append(ComboLeg(
            instrument="OPTION", underlying=underlying, option_type="CALL",
            side=LegSide.SELL, strike=call_contract["strike"], expiry=expiry,
            quantity=1, multiplier=_ETF_OPTION_MULTIPLIER,
            premium=call_contract["premium"],
        ))

        logger.info(
            "领口腿选择: %s Put K=%.2f(%.4f) Call K=%.2f(%.4f) 净成本=%.4f",
            underlying, put_contract["strike"], put_contract["premium"],
            call_contract["strike"], call_contract["premium"],
            put_contract["premium"] - call_contract["premium"],
        )
        return tuple(legs)

    def _search_zero_cost_pair(
        self,
        put_chain: list[dict],
        call_chain: list[dict],
        spot_price: float,
    ) -> tuple[dict, dict] | None:
        """搜索零成本/低成本Put-Call组合."""
        candidates: list[tuple[float, dict, dict]] = []
        for put in put_chain:
            put_otm = (spot_price - put["strike"]) / spot_price
            if put_otm > self._put_otm_max or put_otm <= 0:
                continue
            for call in call_chain:
                if call["dte"] != put["dte"]:
                    continue
                band_width = (call["strike"] - put["strike"]) / spot_price
                if band_width < self._protection_band_min:
                    continue
                net_cost = put["premium"] - call["premium"]
                if abs(net_cost) <= self._max_net_cost_pct * spot_price:
                    score = abs(net_cost) + abs(put_otm - self._put_otm_pct) * spot_price
                    candidates.append((score, put, call))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return (candidates[0][1], candidates[0][2])

    def _validate_business_rules(
        self, legs: tuple[ComboLeg, ...], spot_price: float
    ) -> str | None:
        """校验领口业务规则."""
        put_leg = None
        call_leg = None
        for leg in legs:
            if leg.option_type == "PUT" and leg.side == LegSide.BUY:
                put_leg = leg
            elif leg.option_type == "CALL" and leg.side == LegSide.SELL:
                call_leg = leg

        if call_leg is None:
            return "COLLAR_MISSING_CALL"

        if put_leg is not None:
            underlyings = {leg.underlying for leg in legs}
            if len(underlyings) > 1:
                return "COLLAR_UNDERLYING_MISMATCH"

            band_width = (call_leg.strike - put_leg.strike) / spot_price
            if band_width < self._protection_band_min:
                logger.warning("保护带宽度 %.4f < %.4f", band_width, self._protection_band_min)
                return "COLLAR_BAND_TOO_NARROW"

            put_otm = (spot_price - put_leg.strike) / spot_price
            if put_otm > self._put_otm_max:
                return "COLLAR_PUT_OTM_TOO_DEEP"

            net_cost = put_leg.premium - call_leg.premium
            if net_cost > self._max_net_cost_pct * spot_price:
                return "COLLAR_COST_TOO_HIGH"

        if self._is_blocked_by_drawdown():
            return "COLLAR_BLOCKED_BY_DRAWDOWN"

        return None

    def _has_existing_put_protection(self, underlying: str) -> bool:
        """检测已有认沽保护仓位."""
        if self._protective_put_engine is None:
            return False
        try:
            state = getattr(self._protective_put_engine, "state", {})
            positions = state.get("active_protections", {})
            return underlying in positions
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _is_blocked_by_drawdown(self) -> bool:
        """检查回撤分级是否阻断."""
        if self.risk_manager is None:
            return False
        try:
            risk_state: dict = getattr(self.risk_manager, "get_risk_state", lambda: {})()
            level = risk_state.get("drawdown_level", "L0")
            return bool(level in self._blocked_levels)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
