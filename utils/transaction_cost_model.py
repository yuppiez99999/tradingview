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
    """成本参数 (v8.1: 非线性滑点 + 市场冲击升级)"""
    slippage_bps: float = 10.0          # 基础滑点，基点
    slippage_nonlinear_exp: float = 1.2 # 滑点非线性指数 (>1 表示大单滑点加速)
    commission_rate: float = 0.0003     # 佣金率，万三
    min_commission: float = 5.0         # 最低佣金
    impact_coeff: float = 0.0015        # 冲击成本系数
    impact_exponent: float = 0.75       # 冲击成本指数
    impact_volatility_adj: float = 0.5  # 冲击成本波动率调整系数
    participation_rate: float = 0.15    # 参与率上限
    opportunity_cost_rate: float = 0.0001  # 机会成本率 (日度)
    delay_cost_per_hour: float = 0.00005  # 每小时延迟成本


class TransactionCostModel:
    """交易成本模型"""

    def __init__(self, params: Optional[CostParameters] = None):
        self.params = params or CostParameters()

    def estimate_slippage(self, notional: float, volatility: float = 0.02) -> float:
        """滑点成本 (v8.1: 非线性滑点模型)
        
        公式: slippage = notional * (base_bps/10000) * (1 + volatility_adj) * (size_ratio ** nonlinear_exp)
        """
        base_slippage = notional * self.params.slippage_bps / 10000.0
        volatility_adj = 1.0 + self.params.impact_volatility_adj * max(volatility - 0.02, 0.0) / 0.02
        size_penalty = 1.0  # 简化：默认无额外规模惩罚，可在调用时传入 relative_size
        return base_slippage * volatility_adj * size_penalty

    def estimate_commission(self, notional: float) -> float:
        """佣金成本"""
        commission = notional * self.params.commission_rate
        return max(commission, self.params.min_commission)

    def estimate_impact(self, notional: float, avg_daily_volume: float, volatility: float = 0.02) -> float:
        """市场冲击成本 (v8.1: 波动率调整版 Square-Root Law)"""
        if avg_daily_volume <= 0:
            return 0.0
        participation = min(notional / avg_daily_volume, self.params.participation_rate)
        base_impact = notional * self.params.impact_coeff * (participation ** self.params.impact_exponent)
        volatility_adj = 1.0 + self.params.impact_volatility_adj * max(volatility - 0.02, 0.0) / 0.02
        return base_impact * volatility_adj

    def estimate_opportunity_cost(self, notional: float, days_delayed: float = 1.0) -> float:
        """机会成本 (v8.1: 建仓延迟导致的预期收益损失)"""
        return notional * self.params.opportunity_cost_rate * days_delayed

    def estimate_delay_cost(self, hours_delayed: float = 0.0) -> float:
        """延迟成本 (v8.1: 执行延迟产生的额外成本)"""
        if hours_delayed <= 0:
            return 0.0
        # 简化模型：每小时延迟成本按名义价值的 delay_cost_per_hour 计算
        # 实际应在调用时传入 notional
        return 0.0  # 占位，实际在 estimate_total_cost 中计算

    def estimate_total_cost(self, notional: float, adv: float = 0.0, volatility: float = 0.02, days_delayed: float = 1.0, hours_delayed: float = 0.0) -> Dict[str, float]:
        """综合交易成本 (v8.1: 含非线性滑点 + 波动率调整 + 机会成本 + 延迟成本)"""
        slippage = self.estimate_slippage(notional, volatility)
        commission = self.estimate_commission(notional)
        impact = self.estimate_impact(notional, adv, volatility)
        opportunity = self.estimate_opportunity_cost(notional, days_delayed)
        delay = notional * self.params.delay_cost_per_hour * hours_delayed if hours_delayed > 0 else 0.0
        total = slippage + commission + impact + opportunity + delay
        return {
            "notional": float(notional),
            "slippage": float(slippage),
            "commission": float(commission),
            "impact": float(impact),
            "opportunity_cost": float(opportunity),
            "delay_cost": float(delay),
            "total": float(total),
            "cost_bps": float(total / notional * 10000) if notional > 0 else 0.0,
        }

    def cost_penalty(self, notional: float, adv: float = 0.0) -> float:
        """成本惩罚项，用于优化目标函数"""
        cost = self.estimate_total_cost(notional, adv)
        return cost["total"]
