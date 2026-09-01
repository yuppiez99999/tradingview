"""
v7.5 CostModel — 交易成本模型 (Almgren-Chriss & 固定费率)
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §4.4
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class CostConfig:
    """成本配置"""
    commission_stock: float = 0.00025       # 万 2.5 股票佣金
    commission_futures: float = 0.000023    # 万 0.23 期货
    commission_options: float = 5.0         # 元/张 期权

    stamp_duty: float = 0.001              # 印花税 (卖出 0.1%)
    transfer_fee: float = 0.00002          # 过户费

    slippage_coef: float = 0.142           # Almgren-Chriss 平方根系数
    volatility_scaling: bool = True

    margin_long: float = 0.06              # 融资利率
    margin_short: float = 0.06             # 融券利率
    repo: float = 0.018                    # 逆回购利率


class CostModel:
    """交易成本估算"""

    def __init__(self, config: CostConfig | None = None):
        self.cfg = config or CostConfig()

    def commission(self, notional: float, asset_type: str = 'stock',
                   side: str = 'BUY') -> float:
        """
        佣金估算

        Args:
            notional: 名义金额
            asset_type: 'stock' | 'futures' | 'options'
            side: 'BUY' | 'SELL'
        """
        if asset_type == 'stock':
            cost = notional * self.cfg.commission_stock
            if side == 'SELL':
                cost += notional * self.cfg.stamp_duty  # 印花税
            cost += notional * self.cfg.transfer_fee
            return cost
        if asset_type == 'futures':
            return notional * self.cfg.commission_futures
        if asset_type == 'options':
            return self.cfg.commission_options
        return 0.0

    def market_impact(self, qty: int, daily_volume: int,
                      volatility: float, price: float) -> float:
        """
        Almgren-Chriss 市场冲击模型（平方根）

        $$
        \text{Impact} = \\sigma \\cdot \\eta \\cdot \\sqrt{Q / V}
        $$

        Args:
            qty: 委托数量
            daily_volume: 日成交量
            volatility: 日波动率
            price: 当前价格
        """
        # BT-8: 入口钳制, 防御 qty/price 非正导致 NaN 或负冲击
        qty = int(qty) if qty is not None else 0
        price = float(price) if price is not None else 0.0
        if qty <= 0 or price <= 0 or daily_volume <= 0:
            return 0.0
        volatility = float(volatility) if volatility is not None else 0.0
        if volatility <= 0 or not np.isfinite(volatility):
            return 0.0

        # BT-2/BT-6: participation 钳制到 (0, 1]。大单 (qty > daily_volume) 时
        # sqrt(participation) 不应 > 1 (否则冲击成本爆炸, 违反容量约束)。
        participation_rate = min(qty / daily_volume, 1.0)
        impact_bps = self.cfg.slippage_coef * volatility * np.sqrt(participation_rate)

        if self.cfg.volatility_scaling:
            impact_bps *= volatility / 0.02  # 以 2% vol 为基准

        return impact_bps * price * qty

    def total_cost(self, qty: int, price: float, daily_volume: int,
                   volatility: float, asset_type: str = 'stock',
                   side: str = 'BUY') -> dict:
        """
        总交易成本

        Returns:
            {commission, impact, total, bps}
        """
        notional = qty * price

        comm = self.commission(notional, asset_type, side)
        impact = self.market_impact(qty, daily_volume, volatility, price)
        total = comm + impact
        bps = total / notional if notional > 0 else 0.0

        return {
            'commission': comm,
            'market_impact': impact,
            'total_cost': total,
            'bps': bps,
            'notional': notional,
        }

    def financing_cost(self, notional: float, days: int = 1,
                       position_type: str = 'long') -> float:
        """
        融资成本

        Args:
            notional: 名义金额
            days: 持有天数
            position_type: 'long' | 'short' | 'repo'
        """
        if position_type == 'repo':
            annual_rate = self.cfg.repo
        elif position_type == 'short':
            annual_rate = self.cfg.margin_short
        else:
            annual_rate = self.cfg.margin_long

        return notional * annual_rate * (days / 365)

    # ============================================================
    # 便捷方法: trade_cost (兼容测试 API)
    # ============================================================
    def trade_cost(self,
                   symbol: str,
                   qty: int,
                   price: float,
                   side: str = "BUY",
                   asset_type: str = "stock",
                   adv: int = 0,
                   volatility: float = 0.02) -> dict:
        """
        综合交易成本 (佣金 + 滑点 + 印花税)

        Args:
            symbol: 标的代码
            qty: 委托数量 (正数)
            price: 单价
            side: 'BUY' | 'SELL'
            asset_type: 'stock' | 'futures' | 'options'
            adv: 日成交量 (Average Daily Volume)，用于滑点估算
            volatility: 日波动率

        Returns:
            {commission, slippage, stamp_duty, total, bps, notional}
        """
        qty = int(abs(qty))
        price = float(price)
        notional = qty * price

        # 1) 佣金 (含过户费)
        commission = self.commission(notional, asset_type, side)

        # 2) 印花税 (仅卖出)
        stamp_duty = 0.0
        if asset_type == 'stock' and side.upper() == 'SELL':
            stamp_duty = notional * self.cfg.stamp_duty
            # commission 已含印花税，需分离报告
            commission = commission - stamp_duty
        # 买入时 commission 已含过户费，无印花税

        # 3) 滑点 / 市场冲击
        daily_volume = int(adv) if adv and adv > 0 else max(qty * 100, 1)
        slippage = self.market_impact(qty, daily_volume, volatility, price)

        total = commission + slippage + stamp_duty
        bps = total / notional if notional > 0 else 0.0

        return {
            "symbol": symbol,
            "side": side,
            "asset_type": asset_type,
            "qty": qty,
            "price": price,
            "notional": notional,
            "commission": float(commission),
            "slippage": float(slippage),
            "stamp_duty": float(stamp_duty),
            "total": float(total),
            "bps": float(bps),
        }


class AlmgrenChrissCost(CostModel):
    """
    增强版 Almgren-Chriss 成本模型

    含永久冲击 + 临时冲击 + 波动率缩放
    """

    def __init__(self, config: CostConfig | None = None,
                 permanent_impact: float = 0.1,
                 temporary_impact: float = 0.15):
        super().__init__(config)
        self.permanent_impact = permanent_impact
        self.temporary_impact = temporary_impact

    def total_cost(self, qty: int, price: float, daily_volume: int,
                   volatility: float, asset_type: str = 'stock',
                   side: str = 'BUY', time_horizon: int = 1) -> dict:
        base = super().total_cost(qty, price, daily_volume, volatility,
                                  asset_type, side)
        notional = base['notional']
        participation = qty / daily_volume if daily_volume > 0 else 0

        # 永久冲击
        permanent = self.permanent_impact * volatility * participation * notional

        # 临时冲击
        temp = (self.temporary_impact * volatility *
                np.sqrt(participation / time_horizon) * notional)

        total = base['total_cost'] + permanent + temp
        return {
            'commission': base['commission'],
            'market_impact': base['market_impact'],
            'permanent_impact': permanent,
            'temporary_impact': temp,
            'total_cost': total,
            'bps': total / notional if notional > 0 else 0.0,
            'notional': notional,
        }
