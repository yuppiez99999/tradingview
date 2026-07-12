# -*- coding: utf-8 -*-
"""
交易成本模型 (Transaction Cost Model)

顶级对冲基金标准组件：
- 滑点模型 (Slippage)
- 佣金模型 (Commission)
- 市场冲击成本模型 (Market Impact)
- 综合交易成本估算

供执行算法和组合优化使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class CostParameters:
    """成本参数"""
    slippage_bps: float = 10.0          # 滑点，基点
    commission_rate: float = 0.0003     # 佣金率，万三
    min_commission: float = 5.0         # 最低佣金
    impact_coeff: float = 0.0015        # 冲击成本系数
    impact_exponent: float = 0.75       # 冲击成本指数
    participation_rate: float = 0.15    # 参与率上限


class TransactionCostModel:
    """交易成本模型"""

    def __init__(self, params: Optional[CostParameters] = None):
        self.params = params or CostParameters()

    def estimate_slippage(self, notional: float) -> float:
        """滑点成本"""
        return notional * self.params.slippage_bps / 10000.0

    def estimate_commission(self, notional: float) -> float:
        """佣金成本"""
        commission = notional * self.params.commission_rate
        return max(commission, self.params.min_commission)

    def estimate_impact(self, notional: float, avg_daily_volume: float) -> float:
        """市场冲击成本 (Square-Root Law 简化版)"""
        if avg_daily_volume <= 0:
            return 0.0
        participation = min(notional / avg_daily_volume, self.params.participation_rate)
        return notional * self.params.impact_coeff * (participation ** self.params.impact_exponent)

    def estimate_total_cost(self, notional: float, adv: float = 0.0) -> Dict[str, float]:
        """综合交易成本"""
        slippage = self.estimate_slippage(notional)
        commission = self.estimate_commission(notional)
        impact = self.estimate_impact(notional, adv)
        total = slippage + commission + impact
        return {
            "notional": float(notional),
            "slippage": float(slippage),
            "commission": float(commission),
            "impact": float(impact),
            "total": float(total),
            "cost_bps": float(total / notional * 10000) if notional > 0 else 0.0,
        }

    def cost_penalty(self, notional: float, adv: float = 0.0) -> float:
        """成本惩罚项，用于优化目标函数"""
        cost = self.estimate_total_cost(notional, adv)
        return cost["total"]
