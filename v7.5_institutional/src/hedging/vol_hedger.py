# -*- coding: utf-8 -*-
"""
v7.5 波动率对冲引擎 —— VIX 分级 + 期权保护性 Put

数学基础:
    VIX 分级:
        VIX ∈ (30, 40]:  Put Spread  (虚值两档, 成本可控)
        VIX ∈ (40, 60]:  裸虚值 Put  (Delta ≈ -0.2, 权利金 = 组合×0.5%)
        VIX > 60:        紧急 Put    (VIX>40 时流动性折价生效)

    流动性折价 (继承 v7.2):
        Coverage_actual = Coverage_target × max(0.015, 1 - (VIX - 40)/50)
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger("v75.hedging.vol")


class VolHedger:
    """波动率对冲器: 基于 VIX 分级买入保护性期权"""

    def __init__(self,
                 vix_trigger: float = 30.0,
                 vix_emergency: float = 60.0,
                 budget_low_pct: float = 0.003,
                 budget_mid_pct: float = 0.005,
                 budget_high_pct: float = 0.008,
                 delta_target: float = -0.2):
        """
        Args:
            vix_trigger: 触发阈值
            vix_emergency: 紧急阈值
            budget_low_pct: VIX 30-40 时权利金预算 (组合市值占比)
            budget_mid_pct: VIX 40-60 时权利金预算
            budget_high_pct: VIX > 60 时权利金预算
            delta_target: 目标 Delta
        """
        self.vix_trigger = float(vix_trigger)
        self.vix_emergency = float(vix_emergency)
        self.budget_low = float(budget_low_pct)
        self.budget_mid = float(budget_mid_pct)
        self.budget_high = float(budget_high_pct)
        self.delta_target = float(delta_target)

    def compute_hedge(self, vix: float, portfolio_value: float) -> Dict[str, object]:
        """根据 VIX 计算期权对冲指令

        Args:
            vix: VIX 指数值
            portfolio_value: 组合市值

        Returns:
            对冲指令字典
        """
        if vix <= self.vix_trigger:
            return {"action": "NO_HEDGE",
                    "reason": f"VIX {vix:.1f} ≤ 触发阈值 {self.vix_trigger}"}

        # 分级决策
        if vix <= 40.0:
            budget = self.budget_low * portfolio_value
            action = "BUY_PUT_SPREAD"
            strike_long = "ATM - 2 strikes"   # 买入更虚的 Put
            strike_short = "ATM - 4 strikes"  # 卖出更虚的 Put
            coverage = 1.0
        elif vix <= self.vix_emergency:
            budget = self.budget_mid * portfolio_value
            action = "BUY_BARE_PUT"
            strike_long = "OTM (Delta ≈ -0.2)"
            strike_short = None
            coverage = 1.0
        else:
            budget = self.budget_high * portfolio_value
            action = "BUY_EMERGENCY_PUT"
            strike_long = "OTM (Delta ≈ -0.15)"
            strike_short = None
            # 流动性折价: VIX > 40 时实际覆盖率衰减
            coverage = max(0.015, 1.0 - (vix - 40.0) / 50.0)

        result = {
            "action": action,
            "vix": float(vix),
            "instrument": "50ETF_OPTIONS",
            "direction": "BUY",
            "option_type": "PUT",
            "long_strike_desc": strike_long,
            "short_strike_desc": strike_short,
            "delta_target": self.delta_target,
            "budget": float(budget),
            "budget_pct": float(budget / portfolio_value) if portfolio_value > 0 else 0.0,
            "target_coverage": float(coverage),
            "actual_coverage": float(coverage),
            "portfolio_value": float(portfolio_value),
        }

        logger.info("Vol 对冲: VIX=%.1f → %s, 预算=%.0f, 覆盖率=%.2f",
                    vix, action, budget, coverage)
        return result
