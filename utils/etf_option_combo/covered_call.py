"""备兑看涨策略引擎 (Covered Call).

持有 ETF 现货多头的同时卖出虚值看涨期权, 通过收取权利金增强持仓收益.

业务规则 (spec.md §5.1):
    1. 现货担保: 仅对已有现货多头的标的生成, 卖出数量 ≤ 现货对应合约单位数
    2. OTM选择: OTM程度 ∈ [2%, 8%], 默认5%
    3. DTE选择: DTE ∈ [30, 60], 首选45天
    4. 权利金预算: 年化权利金收入 ≤ 总资本1.5%
    5. 禁止: Kill Switch触发或回撤L2+时禁止新开仓
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


class CoveredCallEngine(ComboBase):
    """备兑看涨策略引擎.

    持有现货 + 卖出OTM Call, 收取权利金增强收益.
    """

    def __init__(
        self,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: object | None = None,
    ) -> None:
        super().__init__(
            StrategyType.COVERED_CALL,
            config,
            chain_fetcher,
            risk_manager,
            greek_manager,
            state_manager,
        )
        self._otm_pct = config.get("otm_pct", 0.05)
        self._otm_range = config.get("otm_pct_range", (0.02, 0.08))
        self._dte_min = config.get("dte_min", 30)
        self._dte_max = config.get("dte_max", 60)
        self._preferred_dte = config.get("preferred_dte", 45)
        self._annual_budget = config.get("annual_budget_pct", 0.015)
        self._blocked_levels = set(config.get("blocked_drawdown_levels", ["L2", "L3"]))

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        """获取CALL期权链."""
        return self.chain_fetcher.get_option_chain(
            underlying=underlying,
            option_type="CALL",
            dte_range=(self._dte_min, self._dte_max),
            otm_range=self._otm_range,
            min_volume=0,
            spot_price=spot_price,
        )

    def _select_legs(
        self,
        underlying: str,
        spot_price: float,
        option_chain: list[dict],
        spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        """选择备兑看涨腿 — 卖出OTM Call."""
        shares = spot_position.get("shares", 0)
        if shares < _ETF_OPTION_MULTIPLIER:
            logger.warning("标的 %s 现货持仓 %d 不足 %d, 无法构建备兑看涨", underlying, shares, _ETF_OPTION_MULTIPLIER)
            return (None, "CC_NO_UNDERLYING")

        max_contracts = shares // _ETF_OPTION_MULTIPLIER
        target_otm = self._otm_pct

        best_contract = self._select_best_call(option_chain, spot_price, target_otm)
        if best_contract is None:
            logger.warning("标的 %s 无满足条件的OTM Call合约", underlying)
            return (None, "CC_NO_OPTION_DATA")

        sell_qty = min(max_contracts, 1)
        expiry_str = best_contract["expiry"]
        expiry = date.fromisoformat(expiry_str) if isinstance(expiry_str, str) else expiry_str

        leg = ComboLeg(
            instrument="OPTION",
            underlying=underlying,
            option_type="CALL",
            side=LegSide.SELL,
            strike=best_contract["strike"],
            expiry=expiry,
            quantity=sell_qty,
            multiplier=_ETF_OPTION_MULTIPLIER,
            premium=best_contract["premium"],
        )
        logger.info(
            "备兑看涨腿选择: %s K=%.2f DTE=%d premium=%.4f qty=%d",
            underlying, leg.strike, best_contract["dte"], leg.premium, sell_qty,
        )
        return (leg,)

    def _select_best_call(
        self,
        chain: list[dict],
        spot_price: float,
        target_otm: float,
    ) -> dict | None:
        """从期权链中选择最优OTM Call — 最接近目标OTM且DTE接近preferred."""
        if not chain:
            return None

        scored: list[tuple[float, dict]] = []
        for contract in chain:
            otm = (contract["strike"] - spot_price) / spot_price
            if otm <= 0:
                continue
            otm_score = abs(otm - target_otm)
            dte_score = abs(contract["dte"] - self._preferred_dte) / 60.0
            total_score = otm_score * 10.0 + dte_score
            scored.append((total_score, contract))

        if not scored:
            return None
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    def _validate_business_rules(
        self, legs: tuple[ComboLeg, ...], spot_price: float
    ) -> str | None:
        """校验备兑看涨业务规则."""
        for leg in legs:
            if leg.instrument != "OPTION" or leg.option_type != "CALL":
                return "CC_INVALID_LEG"
            if leg.side != LegSide.SELL:
                return "CC_WRONG_SIDE"

            otm = (leg.strike - spot_price) / spot_price
            if otm < self._otm_range[0] or otm > self._otm_range[1]:
                logger.warning("OTM %.4f 越界 %s", otm, self._otm_range)
                return "CC_OTM_OUT_OF_RANGE"

            if leg.expiry is not None:
                dte = (leg.expiry - date.today()).days
                if dte < self._dte_min or dte > self._dte_max:
                    return "CC_DTE_OUT_OF_RANGE"

        if self._is_budget_exceeded():
            return "CC_BUDGET_EXCEEDED"

        if self._is_blocked_by_drawdown():
            return "CC_BLOCKED_BY_DRAWDOWN"

        return None

    def _is_budget_exceeded(self) -> bool:
        """检查年化权利金收入预算是否超限."""
        if self.state_manager is None:
            return False
        try:
            budget = self.state_manager.get_budget(StrategyType.COVERED_CALL.value)
            total_capital = self.config.get("total_capital", 2_000_000)
            limit = total_capital * self._annual_budget
            return budget.get("ytd_income", 0.0) >= limit
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def _is_blocked_by_drawdown(self) -> bool:
        """检查回撤分级是否阻断."""
        if self.risk_manager is None:
            return False
        try:
            risk_state = getattr(self.risk_manager, "get_risk_state", lambda: {})()
            level = risk_state.get("drawdown_level", "L0")
            return level in self._blocked_levels
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
