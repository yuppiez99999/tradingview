# -*- coding: utf-8 -*-
"""
WonderTrader 风格对冲策略模板

参考 wtpy/HedgeStrategy.py + HedgeContext.py 设计。
提供组合级 Delta 对冲策略框架,比现有 hedge_engine.py 更完善。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math

from .wt_structs import TickData, BarData, PositionData
from .wt_contracts_manager import get_contracts_manager, ContractData

logger = logging.getLogger(__name__)


@dataclass
class HedgePosition:
    """对冲持仓"""
    code: str
    direction: str = "LONG"     # "LONG"/"SHORT"
    volume: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0
    beta: float = 1.0           # 个股 Beta
    delta: float = 1.0          # 期权 Delta (期货=1)
    contract_multiplier: float = 1.0


@dataclass
class PortfolioMetrics:
    """组合指标"""
    total_value: float = 0.0      # 组合总市值
    net_exposure: float = 0.0     # 净敞口 (多头市值-空头市值)
    net_beta: float = 0.0         # 净 Beta
    net_delta: float = 0.0        # 净 Delta (期权)
    gross_exposure: float = 0.0   # 总敞口
    hedge_ratio: float = 0.0      # 对冲比例 = 空头/多头


class HedgeStrategy(ABC):
    """对冲策略基类

    用户继承此类实现组合对冲逻辑:
        class MyHedgeStrategy(HedgeStrategy):
            def on_rebalance(self, ctx):
                target_hedge = ctx.calc_target_hedge_ratio()
                ctx.adjust_hedge(target_hedge)
    """

    def __init__(self, name: str, config: Optional[Dict] = None):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"hedge.{name}")

        # 持仓
        self.long_positions: Dict[str, HedgePosition] = {}  # 多头持仓
        self.short_positions: Dict[str, HedgePosition] = {}  # 空头对冲持仓

        # 配置
        self.target_hedge_ratio = self.config.get("target_hedge_ratio", 0.3)
        self.max_hedge_ratio = self.config.get("max_hedge_ratio", 0.5)
        self.min_hedge_ratio = self.config.get("min_hedge_ratio", 0.0)
        self.rebalance_threshold = self.config.get("rebalance_threshold", 0.05)

        # 合约管理器
        self.contracts = get_contracts_manager()

    @abstractmethod
    def on_rebalance(self, ctx: "HedgeContext") -> None:
        """再平衡回调 — 用户在此实现对冲调整逻辑"""
        pass

    def on_tick(self, ctx: "HedgeContext", tick: TickData) -> None:
        """Tick 回调"""
        pass

    def on_bar(self, ctx: "HedgeContext", bar: BarData) -> None:
        """Bar 回调"""
        pass

    def add_long_position(self, code: str, volume: float, price: float,
                          beta: float = 1.0) -> None:
        """添加多头持仓"""
        contract = self.contracts.get_contract(code)
        if code in self.long_positions:
            pos = self.long_positions[code]
            new_vol = pos.volume + volume
            pos.avg_price = (pos.avg_price * pos.volume + price * volume) / new_vol if new_vol > 0 else 0
            pos.volume = new_vol
            pos.last_price = price
        else:
            self.long_positions[code] = HedgePosition(
                code=code, direction="LONG", volume=volume,
                avg_price=price, last_price=price,
                beta=beta, contract_multiplier=contract.contract_multiplier,
            )

    def add_short_position(self, code: str, volume: float, price: float,
                           beta: float = 1.0, delta: float = -1.0) -> None:
        """添加空头对冲持仓"""
        contract = self.contracts.get_contract(code)
        if code in self.short_positions:
            pos = self.short_positions[code]
            new_vol = pos.volume + volume
            pos.avg_price = (pos.avg_price * pos.volume + price * volume) / new_vol if new_vol > 0 else 0
            pos.volume = new_vol
            pos.last_price = price
        else:
            self.short_positions[code] = HedgePosition(
                code=code, direction="SHORT", volume=volume,
                avg_price=price, last_price=price,
                beta=beta, delta=delta,
                contract_multiplier=contract.contract_multiplier,
            )

    def calc_portfolio_metrics(self, prices: Dict[str, float]) -> PortfolioMetrics:
        """计算组合指标"""
        long_value = 0.0
        long_beta_value = 0.0
        for code, pos in self.long_positions.items():
            price = prices.get(code, pos.last_price)
            pos.last_price = price
            value = pos.volume * price
            long_value += value
            long_beta_value += value * pos.beta

        short_value = 0.0
        short_beta_value = 0.0
        for code, pos in self.short_positions.items():
            price = prices.get(code, pos.last_price)
            pos.last_price = price
            value = pos.volume * price * pos.contract_multiplier
            short_value += value
            short_beta_value += value * pos.beta

        total_value = long_value - short_value
        net_exposure = long_value - short_value
        net_beta = (long_beta_value - short_beta_value) / long_value if long_value > 0 else 0
        gross_exposure = long_value + short_value
        hedge_ratio = short_value / long_value if long_value > 0 else 0

        return PortfolioMetrics(
            total_value=total_value,
            net_exposure=net_exposure,
            net_beta=net_beta,
            net_delta=net_beta,  # 简化
            gross_exposure=gross_exposure,
            hedge_ratio=hedge_ratio,
        )


class HedgeContext:
    """对冲策略上下文

    提供对冲交易 API:
    - get_portfolio_metrics: 获取组合指标
    - calc_target_hedge: 计算目标对冲量
    - open_hedge: 开空对冲 (卖出期货)
    - close_hedge: 平空对冲
    - adjust_hedge: 调整对冲到目标比例
    """

    def __init__(self, strategy: HedgeStrategy):
        self.strategy = strategy
        self.contracts = get_contracts_manager()
        self.hedge_orders: List[Dict] = []
        self.current_prices: Dict[str, float] = {}

    def get_portfolio_metrics(self, prices: Optional[Dict[str, float]] = None) -> PortfolioMetrics:
        """获取组合指标"""
        if prices:
            self.current_prices = prices
        return self.strategy.calc_portfolio_metrics(self.current_prices)

    def calc_target_hedge_volume(self, hedge_code: str,
                                 target_ratio: Optional[float] = None) -> float:
        """计算目标对冲手数

        Args:
            hedge_code: 对冲工具代码(如 "IF.CFFEX")
            target_ratio: 目标对冲比例, 默认用策略配置

        Returns: 需要调整的手数 (正数=开空, 负数=平空)
        """
        ratio = target_ratio if target_ratio is not None else self.strategy.target_hedge_ratio
        metrics = self.get_portfolio_metrics()

        contract = self.contracts.get_contract(hedge_code)
        hedge_price = self.current_prices.get(hedge_code, 0)
        if hedge_price <= 0:
            return 0

        # 目标空头市值
        target_short_value = metrics.total_value * ratio
        # 当前空头市值
        current_short_value = sum(
            pos.volume * self.current_prices.get(pos.code, pos.last_price) * pos.contract_multiplier
            for pos in self.strategy.short_positions.values()
        )
        # 差额
        diff_value = target_short_value - current_short_value
        # 转换为手数
        target_hands = diff_value / (hedge_price * contract.contract_multiplier)

        return target_hands

    def open_hedge(self, hedge_code: str, hands: float, price: Optional[float] = None) -> bool:
        """开空对冲"""
        if hands <= 0:
            return False
        hedge_price = price or self.current_prices.get(hedge_code, 0)
        if hedge_price <= 0:
            return False

        self.strategy.add_short_position(hedge_code, hands, hedge_price, beta=-1.0, delta=-1.0)

        self.hedge_orders.append({
            "action": "OPEN_SHORT",
            "code": hedge_code,
            "hands": hands,
            "price": hedge_price,
            "timestamp": datetime.now().isoformat(),
        })
        return True

    def close_hedge(self, hedge_code: str, hands: float, price: Optional[float] = None) -> bool:
        """平空对冲"""
        if hedge_code not in self.strategy.short_positions:
            return False
        pos = self.strategy.short_positions[hedge_code]
        if pos.volume < hands:
            hands = pos.volume

        hedge_price = price or self.current_prices.get(hedge_code, pos.last_price)
        pos.volume -= hands

        self.hedge_orders.append({
            "action": "CLOSE_SHORT",
            "code": hedge_code,
            "hands": hands,
            "price": hedge_price,
            "timestamp": datetime.now().isoformat(),
        })
        return True

    def adjust_hedge(self, target_ratio: float, hedge_code: str = "IF.CFFEX") -> Dict:
        """调整对冲到目标比例"""
        diff_hands = self.calc_target_hedge_volume(hedge_code, target_ratio)

        if abs(diff_hands) < 1:  # 不足1手, 跳过
            return {"action": "SKIP", "diff_hands": diff_hands, "reason": "insufficient"}

        if diff_hands > 0:
            self.open_hedge(hedge_code, diff_hands)
            action = "OPEN_SHORT"
        else:
            self.close_hedge(hedge_code, -diff_hands)
            action = "CLOSE_SHORT"

        return {
            "action": action,
            "code": hedge_code,
            "hands": abs(diff_hands),
            "target_ratio": target_ratio,
        }


# === 预置对冲策略 ===

class BetaHedgeStrategy(HedgeStrategy):
    """Beta 对冲策略

    基于组合 Beta 计算对冲量, 使用股指期货空头对冲。
    适合: 已有多头组合, 需要降低系统性风险。
    """

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(name="beta_hedge", config=config)
        self.hedge_code = self.config.get("hedge_code", "IF.CFFEX")

    def on_rebalance(self, ctx: HedgeContext) -> None:
        metrics = ctx.get_portfolio_metrics()

        # 根据净 Beta 动态调整对冲比例
        if metrics.net_beta > 1.2:
            target_ratio = self.max_hedge_ratio
        elif metrics.net_beta > 0.8:
            target_ratio = self.target_hedge_ratio
        elif metrics.net_beta < 0.3:
            target_ratio = self.min_hedge_ratio
        else:
            target_ratio = self.target_hedge_ratio * metrics.net_beta

        result = ctx.adjust_hedge(target_ratio, self.hedge_code)
        self.logger.info(
            f"Beta对冲再平衡: net_beta={metrics.net_beta:.3f} "
            f"target_ratio={target_ratio:.2%} action={result.get('action')}"
        )


class TailRiskHedgeStrategy(HedgeStrategy):
    """尾部风险对冲策略

    在市场极端波动时增加对冲, 平时保持低对冲比例。
    适合: 追求低回撤的防御型组合。
    """

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(name="tail_risk_hedge", config=config)
        self.hedge_code = self.config.get("hedge_code", "IF.CFFEX")
        self.put_code = self.config.get("put_code", "510050_PUT")
        self.vol_threshold = self.config.get("vol_threshold", 0.25)  # 波动率阈值

    def on_rebalance(self, ctx: HedgeContext) -> None:
        metrics = ctx.get_portfolio_metrics()

        # 简化的尾部风险判断: 净 Beta 过高时视为尾部风险
        if metrics.net_beta > 1.5 or metrics.hedge_ratio < self.min_hedge_ratio:
            # 极端情况: 最大化对冲
            target_ratio = self.max_hedge_ratio
        elif metrics.net_beta > 1.0:
            target_ratio = self.target_hedge_ratio * 1.5
        else:
            target_ratio = self.target_hedge_ratio

        target_ratio = min(target_ratio, self.max_hedge_ratio)
        result = ctx.adjust_hedge(target_ratio, self.hedge_code)
        self.logger.info(
            f"尾部风险对冲: net_beta={metrics.net_beta:.3f} "
            f"hedge_ratio={metrics.hedge_ratio:.2%} → {target_ratio:.2%}"
        )


class DynamicHedgeStrategy(HedgeStrategy):
    """动态对冲策略

    根据市场状态动态调整对冲比例:
    - 牛市: 降低对冲 (10-20%)
    - 震荡: 中等对冲 (30%)
    - 熊市: 增加对冲 (50-70%)
    """

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(name="dynamic_hedge", config=config)
        self.hedge_code = self.config.get("hedge_code", "IF.CFFEX")
        self.market_state = "neutral"  # bull/bear/neutral

    def set_market_state(self, state: str) -> None:
        """设置市场状态"""
        self.market_state = state

    def on_rebalance(self, ctx: HedgeContext) -> None:
        if self.market_state == "bull":
            target_ratio = 0.15
        elif self.market_state == "bear":
            target_ratio = 0.70
        else:  # neutral
            target_ratio = 0.30

        target_ratio = min(target_ratio, self.max_hedge_ratio)
        result = ctx.adjust_hedge(target_ratio, self.hedge_code)
        self.logger.info(
            f"动态对冲: market={self.market_state} target={target_ratio:.2%}"
        )


__all__ = [
    "HedgePosition", "PortfolioMetrics",
    "HedgeStrategy", "HedgeContext",
    "BetaHedgeStrategy", "TailRiskHedgeStrategy", "DynamicHedgeStrategy",
]
