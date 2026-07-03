# -*- coding: utf-8 -*-
"""
流动性风险控制与交易成本建模

来源：整合自 E:\各种PY程序\11_量化策略\liquidity_risk_control.py

核心功能：
1. 四层交易成本分解（佣金/印花税/滑点/冲击成本）
2. 流动性评分（基于代码前缀自动识别流动性特征）
3. 分批执行策略生成
4. 冲击成本估算（订单大小 + 资产特性）

设计理念：
  大订单自动拆分为小批次，最小化市场冲击；
  不同资产类型有不同的流动性特征，自动调整执行参数。

使用方式：
  from utils.liquidity_risk import LiquidityRiskController
  
  controller = LiquidityRiskController(portfolio_config)
  costs = controller.model_trading_costs(orders)
  batches = controller.generate_batch_execution(order, max_batch_ratio=0.2)
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger('liquidity_risk')


@dataclass
class TradeOrder:
    """标准化交易订单"""
    code: str
    side: str           # 'buy' | 'sell'
    amount: float       # 交易金额
    price: Optional[float] = None
    shares: Optional[int] = None
    priority: int = 0   # 优先级（越小越高）


@dataclass
class CostBreakdown:
    """交易成本分解"""
    commission: float = 0.0       # 佣金
    stamp_duty: float = 0.0       # 印花税（仅卖出）
    slippage: float = 0.0         # 滑点
    impact: float = 0.0           # 冲击成本
    total: float = 0.0            # 总成本
    ratio: float = 0.0            # 成本比例


class LiquidityRiskController:
    """流动性风险控制器 — 交易成本建模 + 分批执行策略"""

    # 默认成本参数
    COMMISSION_RATE = 0.0003       # 佣金 0.03%
    STAMP_DUTY_RATE = 0.001        # 印花税 0.1%（仅卖出）
    SLIPPAGE_RATE = 0.0003         # 基础滑点 0.03%
    IMPACT_RATE = 0.0002           # 基础冲击成本 0.02%

    # 流动性分级（基于代码前缀）
    LIQUIDITY_FACTORS = {
        # ETF — 高流动性
        '51': 1.0,   # 上证ETF
        '15': 1.1,   # 深证ETF
        '58': 1.2,   # 科创ETF
        # 个股 — 大盘蓝筹
        '60': 1.2,   # 上证主板
        # 个股 — 中小盘
        '00': 1.3,   # 深证主板
        '30': 1.4,   # 创业板
        '68': 1.5,   # 科创板
    }

    # 冲击成本乘数（基于订单金额）
    IMPACT_MULTIPLIERS = [
        (1_000_000, 2.0),   # >100万: 2x
        (500_000, 1.5),     # >50万: 1.5x
        (100_000, 1.2),     # >10万: 1.2x
        (50_000, 1.0),      # >5万: 1.0x (基准)
        (0, 0.8),           # <5万: 0.8x
    ]

    def __init__(self,
                 commission_rate: float = None,
                 stamp_duty_rate: float = None,
                 slippage_rate: float = None,
                 impact_rate: float = None,
                 max_single_batch_ratio: float = 0.20,
                 execution_window_days: int = 30):
        """
        Args:
            commission_rate: 佣金率，默认 0.0003
            stamp_duty_rate: 印花税率，默认 0.001
            slippage_rate: 基础滑点率，默认 0.0003
            impact_rate: 基础冲击成本率，默认 0.0002
            max_single_batch_ratio: 单批次最大执行比例（占日成交量），默认 0.20
            execution_window_days: 最大执行窗口（天），默认 30
        """
        self.commission_rate = commission_rate or self.COMMISSION_RATE
        self.stamp_duty_rate = stamp_duty_rate or self.STAMP_DUTY_RATE
        self.slippage_rate = slippage_rate or self.SLIPPAGE_RATE
        self.impact_rate = impact_rate or self.IMPACT_RATE
        self.max_single_batch_ratio = max_single_batch_ratio
        self.execution_window_days = execution_window_days

    def get_liquidity_factor(self, code: str) -> float:
        """
        获取资产流动性因子。

        基于代码前缀自动识别：
        - 51xxxx (ETF): 1.0 高流动性
        - 60xxxx (主板): 1.2 中等
        - 30xxxx (创业板): 1.4 较低
        - 688xxx (科创板): 1.5 最低
        """
        # 精确匹配 ETF 代码
        for prefix in ['51', '15', '58']:
            if code.startswith(prefix):
                return self.LIQUIDITY_FACTORS.get(prefix, 1.0)
        # 个股前缀匹配
        prefix2 = code[:2]
        return self.LIQUIDITY_FACTORS.get(prefix2, 1.2)

    def get_impact_multiplier(self, amount: float, code: str) -> float:
        """
        获取冲击成本乘数。

        综合考虑：
        - 订单大小（金额越大，冲击越大）
        - 资产类型（科创板/创业板流动性较差，冲击更大）
        """
        base = 1.0
        for threshold, multiplier in self.IMPACT_MULTIPLIERS:
            if amount >= threshold:
                base = multiplier
                break

        # 科创板/创业板额外冲击
        if code.startswith('688') or code.startswith('30'):
            base *= 1.3

        return base

    def model_trading_costs(self, orders: List[TradeOrder]) -> Dict:
        """
        建模交易成本（四层分解）。

        Args:
            orders: 交易订单列表

        Returns:
            {
                'breakdown': CostBreakdown,
                'total_amount': float,
                'avg_cost_per_order': float,
                'efficiency': str,          # excellent/good/acceptable/poor
                'per_order': [{code, side, amount, costs}]
            }
        """
        breakdown = CostBreakdown()
        total_amount = 0.0
        per_order = []

        for order in orders:
            amount = order.amount
            if amount <= 0:
                continue

            total_amount += amount
            liq_factor = self.get_liquidity_factor(order.code)
            imp_factor = self.get_impact_multiplier(amount, order.code)

            # 四层成本
            commission = amount * self.commission_rate
            stamp = amount * self.stamp_duty_rate if order.side == 'sell' else 0.0
            slippage = amount * self.slippage_rate * liq_factor
            impact = amount * self.impact_rate * imp_factor

            breakdown.commission += commission
            breakdown.stamp_duty += stamp
            breakdown.slippage += slippage
            breakdown.impact += impact

            per_order.append({
                'code': order.code,
                'side': order.side,
                'amount': amount,
                'costs': {
                    'commission': round(commission, 2),
                    'stamp_duty': round(stamp, 2),
                    'slippage': round(slippage, 2),
                    'impact': round(impact, 2),
                    'total': round(commission + stamp + slippage + impact, 2),
                }
            })

        breakdown.total = sum([
            breakdown.commission, breakdown.stamp_duty,
            breakdown.slippage, breakdown.impact
        ])
        breakdown.ratio = breakdown.total / max(total_amount, 1)

        # 效率评级
        if breakdown.ratio < 0.001:
            efficiency = 'excellent'
        elif breakdown.ratio < 0.002:
            efficiency = 'good'
        elif breakdown.ratio < 0.005:
            efficiency = 'acceptable'
        elif breakdown.ratio < 0.01:
            efficiency = 'poor'
        else:
            efficiency = 'very_poor'

        return {
            'breakdown': breakdown,
            'total_amount': total_amount,
            'avg_cost_per_order': breakdown.total / max(len(orders), 1),
            'efficiency': efficiency,
            'per_order': per_order,
        }

    def generate_batch_execution(self, order: TradeOrder,
                                  daily_volume: Optional[float] = None) -> List[TradeOrder]:
        """
        生成分批执行计划。

        大订单自动拆分为多批次：
        - 单批次不超过日成交量的 20%
        - 批次间隔至少 1 天
        - 最多分 execution_window_days 批次

        Args:
            order: 原始订单
            daily_volume: 日均成交额（可选，不提供则仅按金额拆分）

        Returns:
            拆分后的订单列表
        """
        if order.amount <= 50_000:
            return [order]  # 小额订单不拆分

        # 按日成交量限制
        if daily_volume and daily_volume > 0:
            max_batch_amount = daily_volume * self.max_single_batch_ratio
        else:
            # 默认：每批最多 10万元
            max_batch_amount = 100_000

        # 计算批次数
        num_batches = max(1, int(np.ceil(order.amount / max_batch_amount)))
        num_batches = min(num_batches, self.execution_window_days)

        batch_amount = order.amount / num_batches
        batches = []

        for i in range(num_batches):
            batches.append(TradeOrder(
                code=order.code,
                side=order.side,
                amount=round(batch_amount, 2),
                price=order.price,
                shares=order.shares // num_batches if order.shares else None,
                priority=order.priority + i,  # 后续批次优先级递减
            ))

        return batches

    def assess_portfolio_liquidity(self, positions: Dict[str, float],
                                    total_value: float) -> Dict:
        """
        评估组合整体流动性风险。

        Args:
            positions: {code: market_value}
            total_value: 组合总市值

        Returns:
            {
                'score': float,              # 流动性评分 [0-100]
                'level': str,                # high/medium/low
                'concentration': float,      # 集中度
                'worst_exit_days': float,    # 最差退出天数
                'per_asset': {code: {factor, weight, exit_days}}
            }
        """
        if total_value <= 0:
            return {'score': 0, 'level': 'unknown', 'error': '总市值为0'}

        per_asset = {}
        weighted_factor_sum = 0.0
        weights_sum = 0.0

        for code, value in positions.items():
            if value <= 0:
                continue
            weight = value / total_value
            liq_factor = self.get_liquidity_factor(code)
            exit_days = max(1, int(value / 100_000 * liq_factor))

            per_asset[code] = {
                'liquidity_factor': liq_factor,
                'weight': round(weight, 4),
                'exit_days': exit_days,
            }
            weighted_factor_sum += liq_factor * weight
            weights_sum += weight

        if weights_sum == 0:
            return {'score': 100, 'level': 'high', 'note': '无持仓'}

        avg_factor = weighted_factor_sum / weights_sum
        score = max(0, min(100, (2.0 - avg_factor) * 50))  # factor 1.0→50, 2.0→0

        # 集中度：最大单只权重
        max_weight = max((v['weight'] for v in per_asset.values()), default=0)

        # 最差退出天数
        worst_exit = max((v['exit_days'] for v in per_asset.values()), default=0)

        if score >= 70:
            level = 'high'
        elif score >= 40:
            level = 'medium'
        else:
            level = 'low'

        return {
            'score': round(score, 1),
            'level': level,
            'avg_liquidity_factor': round(avg_factor, 2),
            'concentration': round(max_weight, 4),
            'worst_exit_days': worst_exit,
            'per_asset': per_asset,
        }

    def optimize_execution_sequence(self, orders: List[TradeOrder]) -> List[TradeOrder]:
        """
        优化执行顺序：先卖后买，确保资金充足。

        规则：
        1. 卖出订单优先（回笼资金）
        2. 同方向按流动性从高到低
        3. 同方向同流动性按金额从小到大
        """
        sells = [o for o in orders if o.side == 'sell']
        buys = [o for o in orders if o.side == 'buy']

        # 卖出：流动性高优先 + 金额小优先
        sells.sort(key=lambda o: (self.get_liquidity_factor(o.code), o.amount))

        # 买入：流动性高优先 + 金额小优先
        buys.sort(key=lambda o: (self.get_liquidity_factor(o.code), o.amount))

        return sells + buys
