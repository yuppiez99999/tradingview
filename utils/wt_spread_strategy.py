# -*- coding: utf-8 -*-
"""
WonderTrader 风格价差策略框架

参考 wtpy/SpreadStrategy.py + SpreadContext.py 设计。
新增 ETF 配对交易/跨期套利/价差回归策略能力。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple
from dataclasses import dataclass
import logging

from .wt_structs import TickData, BarData, TradeData
from .wt_contracts_manager import get_contracts_manager

logger = logging.getLogger(__name__)


@dataclass
class SpreadDefinition:
    """价差合约定义

    定义一个价差合约, 如:
    - ETF配对: 1×510300 - 1×510050 (沪深300 vs 上证50)
    - 跨期套利: 1×IF2509 - 1×IF2512
    - 指数对冲: 1×510300 - 0.7×IF (ETF多+期货空)
    """

    name: str  # 价差合约名,如 "SPD.300-50"
    legs: List[Dict[str, float]]  # 各腿: [{"code":"510300.SH","ratio":1.0,"direction":"BUY"}, ...]
    spread_type: str = "ratio"  # "ratio"/"diff"/"weighted"
    description: str = ""


class SpreadCalculator:
    """价差计算器"""

    @staticmethod
    def calc_spread_price(spread: SpreadDefinition, prices: Dict[str, float]) -> float:
        """计算价差价格"""
        result = 0.0
        for leg in spread.legs:
            code = leg["code"]
            if code not in prices:
                continue
            ratio = leg.get("ratio", 1.0)
            direction = leg.get("direction", "BUY")
            if direction == "BUY":
                result += prices[code] * ratio  # type: ignore
            else:  # SELL
                result -= prices[code] * ratio  # type: ignore
        return result

    @staticmethod
    def calc_spread_bars(spread: SpreadDefinition, bars: Dict[str, BarData]) -> Tuple[float, float, float, float]:
        """计算价差的 OHLC

        Returns: (spread_open, spread_high, spread_low, spread_close)
        """
        o = h = lo = c = 0.0
        for leg in spread.legs:
            code = leg["code"]
            if code not in bars:
                continue
            bar = bars[code]  # type: ignore
            ratio = leg.get("ratio", 1.0)
            direction = leg.get("direction", "BUY")
            sign = 1 if direction == "BUY" else -1
            o += sign * bar.open * ratio
            h += sign * bar.high * ratio
            lo += sign * bar.low * ratio
            c += sign * bar.close * ratio
        return o, h, lo, c


class SpreadStrategy(ABC):
    """价差策略基类

    用户继承此类实现价差交易逻辑:
        class MySpreadStrategy(SpreadStrategy):
            def on_spread_tick(self, ctx, spread_price, leg_prices):
                if spread_price > self.threshold:
                    ctx.enter_long_spread(...)
    """

    def __init__(self, name: str, spread: SpreadDefinition):
        self.name = name
        self.spread = spread
        self.position = 0.0  # 价差持仓(正数=多头价差)
        self.avg_price = 0.0  # 价差持仓均价
        self.logger = logging.getLogger(f"spread.{name}")

    @abstractmethod
    def on_spread_tick(self, ctx: "SpreadContext", spread_price: float, leg_prices: Dict[str, float]) -> None:
        """价差 Tick 回调"""
        pass

    @abstractmethod
    def on_spread_bar(
        self, ctx: "SpreadContext", spread_bar: Tuple[float, float, float, float], leg_bars: Dict[str, BarData]
    ) -> None:
        """价差 Bar 回调"""
        pass

    def on_trade(self, ctx: "SpreadContext", trade: TradeData) -> None:  # noqa: B027  接口占位, 子类按需覆写
        """成交回调"""
        pass

    def on_position(self, ctx: "SpreadContext", position: float) -> None:  # noqa: B027  接口占位, 子类按需覆写
        """持仓回调"""
        pass


class SpreadContext:
    """价差策略上下文

    提供价差交易 API:
    - enter_long_spread: 开多价差 (买入正腿, 卖出反腿)
    - exit_long_spread: 平多价差
    - enter_short_spread: 开空价差
    - exit_short_spread: 平空价差
    - get_spread_position: 获取价差持仓
    - get_leg_position: 获取单腿持仓
    """

    def __init__(self, strategy: SpreadStrategy, spread: SpreadDefinition):
        self.strategy = strategy
        self.spread = spread
        self.leg_positions: Dict[str, float] = {leg["code"]: 0.0 for leg in spread.legs}  # type: ignore
        self.leg_avg_cost: Dict[str, float] = {leg["code"]: 0.0 for leg in spread.legs}  # type: ignore
        self.trades: List[TradeData] = []
        self.cash = 1_000_000.0
        self.contracts = get_contracts_manager()

    def enter_long_spread(self, qty: float, leg_prices: Dict[str, float]) -> bool:
        """开多价差: 买入正腿, 卖出反腿"""
        for leg in self.spread.legs:
            code = leg["code"]
            direction = leg.get("direction", "BUY")
            ratio = leg.get("ratio", 1.0)
            leg_qty = qty * ratio
            price = leg_prices.get(code, 0)  # type: ignore
            if price <= 0:
                return False

            # 计算成本
            amount = price * leg_qty
            commission = self.contracts.calc_commission(code, amount, direction)  # type: ignore
            self.contracts.calc_margin(code, amount)  # type: ignore

            if direction == "BUY":
                self.cash -= amount + commission
            else:
                self.cash -= commission  # 卖出仅扣手续费

            # 更新持仓
            old_pos = self.leg_positions[code]  # type: ignore
            old_cost = self.leg_avg_cost[code]  # type: ignore
            new_pos = old_pos + leg_qty if direction == "BUY" else old_pos - leg_qty
            if new_pos != 0:
                self.leg_avg_cost[code] = (  # type: ignore
                    (old_pos * old_cost + leg_qty * price) / abs(new_pos) if abs(new_pos) > 0 else 0
                )
            self.leg_positions[code] = new_pos  # type: ignore

            self.trades.append(
                TradeData(
                    trade_id=f"SL_{len(self.trades)}",
                    order_id=f"SO_{len(self.trades)}",
                    code=code,
                    exchange="SSE",  # type: ignore
                    direction=direction,
                    offset="OPEN",  # type: ignore
                    price=price,
                    volume=leg_qty,
                    amount=amount,
                )
            )

        return True

    def exit_long_spread(self, qty: float, leg_prices: Dict[str, float]) -> bool:
        """平多价差: 卖出正腿, 买入反腿"""
        return self.enter_short_spread(qty, leg_prices)

    def enter_short_spread(self, qty: float, leg_prices: Dict[str, float]) -> bool:
        """开空价差: 卖出正腿, 买入反腿"""
        for leg in self.spread.legs:
            code = leg["code"]
            original_dir = leg.get("direction", "BUY")
            reverse_dir = "SELL" if original_dir == "BUY" else "BUY"
            ratio = leg.get("ratio", 1.0)
            leg_qty = qty * ratio
            price = leg_prices.get(code, 0)  # type: ignore
            if price <= 0:
                return False

            amount = price * leg_qty
            commission = self.contracts.calc_commission(code, amount, reverse_dir)  # type: ignore

            if reverse_dir == "BUY":
                self.cash -= amount + commission
            else:
                self.cash -= commission

            old_pos = self.leg_positions[code]  # type: ignore
            new_pos = old_pos - leg_qty if reverse_dir == "SELL" else old_pos + leg_qty
            self.leg_positions[code] = new_pos  # type: ignore

            self.trades.append(
                TradeData(
                    trade_id=f"SL_{len(self.trades)}",
                    order_id=f"SO_{len(self.trades)}",
                    code=code,
                    exchange="SSE",  # type: ignore
                    direction=reverse_dir,
                    offset="CLOSE",
                    price=price,
                    volume=leg_qty,
                    amount=amount,
                )
            )

        return True

    def exit_short_spread(self, qty: float, leg_prices: Dict[str, float]) -> bool:
        """平空价差"""
        return self.enter_long_spread(qty, leg_prices)

    def get_spread_position(self) -> float:
        """获取价差净持仓(以第一腿为准)"""
        if not self.spread.legs:
            return 0
        first_code = self.spread.legs[0]["code"]
        return self.leg_positions.get(first_code, 0)  # type: ignore

    def get_leg_position(self, code: str) -> float:
        return self.leg_positions.get(code, 0)

    def get_total_equity(self, leg_prices: Dict[str, float]) -> float:
        """计算总权益 = 现金 + 持仓市值"""
        equity = self.cash
        for code, pos in self.leg_positions.items():
            price = leg_prices.get(code, 0)
            equity += pos * price
        return equity


class SpreadBacktester:
    """价差策略回测器"""

    def __init__(self, strategy: SpreadStrategy, initial_capital: float = 1_000_000):
        self.strategy = strategy
        self.ctx = SpreadContext(strategy, strategy.spread)
        self.ctx.cash = initial_capital
        self.equity_curve: List[Dict] = []
        self.calc = SpreadCalculator()

    def run_on_ticks(self, tick_data_list: List[Dict[str, TickData]]) -> Dict:
        """对 Tick 数据序列进行回测

        Args:
            tick_data_list: 每个元素是 {code: TickData} 字典
        """
        for tick_dict in tick_data_list:
            prices = {code: t.price for code, t in tick_dict.items()}
            spread_price = self.calc.calc_spread_price(self.strategy.spread, prices)
            self.strategy.on_spread_tick(self.ctx, spread_price, prices)

            # 记录权益
            equity = self.ctx.get_total_equity(prices)
            ts = max((t.timestamp for t in tick_dict.values()), default=0)
            self.equity_curve.append(
                {
                    "timestamp": ts,
                    "spread_price": spread_price,
                    "equity": equity,
                }
            )

        return self._generate_report()

    def _generate_report(self) -> Dict:
        """生成回测报告"""
        if not self.equity_curve:
            return {}

        equities = [e["equity"] for e in self.equity_curve]
        spreads = [e["spread_price"] for e in self.equity_curve]

        self.ctx.cash if not self.equity_curve else (self.equity_curve[0]["equity"] - 0)  # 近似
        # 用实际初始资金
        from .wt_backtest_engine import BacktestEngine

        BacktestEngine(initial_capital=1_000_000)

        final_equity = equities[-1] if equities else 1_000_000
        total_return = (final_equity / 1_000_000) - 1

        # 计算最大回撤
        peak = equities[0]
        max_dd = 0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 计算夏普
        import numpy as np

        returns = np.diff(equities) / equities[:-1] if len(equities) > 1 else []
        sharpe = np.mean(returns) / np.std(returns) * (252**0.5) if len(returns) > 1 and np.std(returns) > 0 else 0

        return {
            "strategy": self.strategy.name,
            "spread": self.strategy.spread.name,
            "initial_capital": 1_000_000,
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_ratio": round(sharpe, 4),
            "n_ticks": len(self.equity_curve),
            "n_trades": len(self.ctx.trades),
            "spread_price_mean": round(sum(spreads) / len(spreads), 4) if spreads else 0,
            "spread_price_std": round(np.std(spreads), 4) if spreads else 0,
        }


# === 预定义价差合约 ===

ETF_PAIR_SPREADS = {
    # ETF 配对交易
    "SPD.300-50": SpreadDefinition(
        name="SPD.300-50",
        legs=[
            {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},  # type: ignore
            {"code": "510050.SH", "ratio": 1.0, "direction": "SELL"},  # type: ignore
        ],
        spread_type="diff",
        description="沪深300ETF - 上证50ETF (大盘风格价差)",
    ),
    "SPD.500-1000": SpreadDefinition(
        name="SPD.500-1000",
        legs=[
            {"code": "510500.SH", "ratio": 1.0, "direction": "BUY"},  # type: ignore
            {"code": "512100.SH", "ratio": 1.0, "direction": "SELL"},  # type: ignore
        ],
        spread_type="diff",
        description="中证500 - 中证1000 (中小盘价差)",
    ),
    "SPD.KECHUANG": SpreadDefinition(
        name="SPD.KECHUANG",
        legs=[
            {"code": "588080.SH", "ratio": 1.0, "direction": "BUY"},  # type: ignore
            {"code": "588000.SH", "ratio": 1.0, "direction": "SELL"},  # type: ignore
        ],
        spread_type="diff",
        description="科创50易方达 - 科创50华夏 (同标的ETF价差)",
    ),
    # 对冲价差: ETF多+期货空
    "SPD.300-IF": SpreadDefinition(
        name="SPD.300-IF",
        legs=[
            {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},  # type: ignore
            {"code": "IF.CFFEX", "ratio": 1.0, "direction": "SELL"},  # type: ignore
        ],
        spread_type="weighted",
        description="沪深300ETF多+IF期货空 (期现套利)",
    ),
}


__all__ = [
    "ETF_PAIR_SPREADS",
    "SpreadBacktester",
    "SpreadCalculator",
    "SpreadContext",
    "SpreadDefinition",
    "SpreadStrategy",
]
