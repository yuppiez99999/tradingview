# -*- coding: utf-8 -*-
"""
WonderTrader 风格 Tick 级事件驱动回测引擎

参考 wtpy/WtBtEngine.py 设计。
支持 Tick/Bar 双模式事件驱动回测,替代现有日频批处理回测。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .wt_contracts_manager import get_contracts_manager
from .wt_structs import BarData, OrderData, PositionData, TickData, TradeData

logger = logging.getLogger(__name__)


class TickMatcher:
    """Tick 撮合器

    模拟 wtpy 的撮合逻辑:
    - 限价单: 检查价格是否触及
    - 市价单: 按 Tick 价格成交
    - 滑点: 按配置添加滑点
    """

    def __init__(
        self,
        slippage_rate: float = 0.001,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        stamp_duty: float = 0.001,
    ):
        self.slippage_rate = slippage_rate
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_duty = stamp_duty  # 印花税(仅卖出)
        self.contracts = get_contracts_manager()

    def match_order(self, order: OrderData, tick: TickData) -> Optional[TradeData]:
        """撮合订单

        Returns: TradeData 或 None(未成交)
        """
        if order.code != tick.code:
            return None

        # 限价单检查
        if order.order_type == "LIMIT":
            if order.direction == "BUY" and tick.price > order.price:
                return None  # 买价低于市价, 不成交
            if order.direction == "SELL" and tick.price < order.price:
                return None  # 卖价高于市价, 不成交

        # 计算成交价(含滑点)
        if order.direction == "BUY":
            exec_price = tick.price * (1 + self.slippage_rate)
        else:
            exec_price = tick.price * (1 - self.slippage_rate)

        # 量化到最小变动价位
        contract = self.contracts.get_contract(order.code)
        tick_size = contract.price_tick
        exec_price = round(exec_price / tick_size) * tick_size

        # 成交量
        trade_volume = order.volume - order.traded_volume
        # 限制不超过当前 Tick 成交量
        if tick.volume > 0:
            trade_volume = min(trade_volume, tick.volume * 0.1)  # 不超过 Tick 成交量 10%

        if trade_volume <= 0:
            return None

        amount = exec_price * trade_volume

        # 手续费
        commission = max(amount * self.commission_rate, self.min_commission)
        if order.direction == "SELL":
            commission += amount * self.stamp_duty

        return TradeData(
            trade_id=f"T_{int(tick.timestamp * 1000)}_{order.order_id}",
            order_id=order.order_id,
            code=order.code,
            exchange=order.exchange,
            direction=order.direction,
            offset=order.offset,
            price=exec_price,
            volume=trade_volume,
            amount=amount,
            timestamp=tick.timestamp,
            datetime_str=tick.datetime_str,
        )


class TickBacktestEngine:
    """Tick 级事件驱动回测引擎

    特性:
    1. 支持 Tick-by-Tick 事件驱动回测
    2. 支持多标的组合回测
    3. 限价/市价单撮合
    4. 滑点+手续费+印花税模拟
    5. 持仓+资金管理
    6. 绩效指标计算

    用法:
        engine = TickBacktestEngine(initial_capital=1_000_000)
        engine.set_strategy(my_strategy)
        engine.load_tick_data(ticks)
        result = engine.run()
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        slippage_rate: float = 0.001,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        stamp_duty: float = 0.001,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.matcher = TickMatcher(slippage_rate, commission_rate, min_commission, stamp_duty)
        self.contracts = get_contracts_manager()

        # 状态
        self.positions: Dict[str, PositionData] = {}
        self.pending_orders: List[OrderData] = []
        self.trades: List[TradeData] = []
        self.equity_curve: List[Dict] = []
        self.daily_pnl: List[Dict] = []
        self.current_tick: Optional[TickData] = None

        # 策略回调
        self.strategy: Optional[Any] = None  # 用户提供, 需实现 on_tick/on_bar

        # Tick 数据
        self.tick_data: Dict[str, List[TickData]] = {}  # code -> list of ticks
        self.bar_data: Dict[str, List[BarData]] = {}  # code -> list of bars

    def reset(self) -> None:
        """重置回测状态"""
        self.cash = self.initial_capital
        self.positions = {}
        self.pending_orders = []
        self.trades = []
        self.equity_curve = []
        self.daily_pnl = []
        self.current_tick = None

    def set_strategy(self, strategy: Any) -> None:
        """设置策略对象

        策略需实现以下方法(至少一个):
        - on_tick(ctx, tick: TickData)
        - on_bar(ctx, bar: BarData)
        - on_trade(ctx, trade: TradeData)
        - on_position(ctx, position: PositionData)
        """
        self.strategy = strategy

    def load_tick_data(self, ticks: Dict[str, List[TickData]]) -> None:
        """加载 Tick 数据

        Args:
            ticks: {code: [TickData, ...]}
        """
        self.tick_data = ticks

    def load_bar_data(self, bars: Dict[str, List[BarData]]) -> None:
        """加载 Bar 数据"""
        self.bar_data = bars

    def send_order(
        self,
        code: str,
        direction: str,
        volume: float,
        price: float = 0,
        order_type: str = "LIMIT",
        offset: str = "OPEN",
    ) -> str:
        """发送委托"""
        order_id = f"O_{len(self.pending_orders) + 1}"
        contract = self.contracts.get_contract(code)

        order = OrderData(
            order_id=order_id,
            code=code,
            exchange=contract.exchange,
            direction=direction,
            offset=offset,
            order_type=order_type,
            price=price,
            volume=volume,
            status="NOT_REPORTED",
            timestamp=self.current_tick.timestamp if self.current_tick else 0,
        )
        self.pending_orders.append(order)
        return order_id

    def buy(self, code: str, volume: float, price: float = 0, order_type: str = "LIMIT") -> str:
        """买入"""
        return self.send_order(code, "BUY", volume, price, order_type, "OPEN")

    def sell(self, code: str, volume: float, price: float = 0, order_type: str = "LIMIT") -> str:
        """卖出"""
        return self.send_order(code, "SELL", volume, price, order_type, "CLOSE")

    def _match_pending_orders(self, tick: TickData) -> None:
        """撮合待成交订单"""
        remaining = []
        for order in self.pending_orders:
            trade = self.matcher.match_order(order, tick)
            if trade:
                self._process_trade(trade)
                order.traded_volume += trade.volume
                if order.traded_volume >= order.volume:
                    order.status = "ALL_TRADED"
                else:
                    order.status = "PART_TRADED"
                    remaining.append(order)
            else:
                remaining.append(order)
        self.pending_orders = remaining

    def _process_trade(self, trade: TradeData) -> None:
        """处理成交"""
        self.trades.append(trade)

        # 更新持仓
        if trade.code not in self.positions:
            self.positions[trade.code] = PositionData(
                code=trade.code,
                exchange=trade.exchange,
            )
        pos = self.positions[trade.code]

        contract = self.contracts.get_contract(trade.code)
        commission = max(trade.amount * contract.commission_rate, contract.min_commission)
        if trade.direction == "SELL":
            commission += trade.amount * contract.stamp_duty

        if trade.direction == "BUY":
            # 更新多头持仓
            new_vol = pos.volume + trade.volume
            pos.avg_price = (pos.avg_price * pos.volume + trade.price * trade.volume) / new_vol if new_vol > 0 else 0
            pos.volume = new_vol
            self.cash -= trade.amount + commission
        else:
            # 卖出
            pos.volume -= trade.volume
            self.cash += trade.amount - commission
            if pos.volume <= 0:
                pos.volume = 0
                pos.avg_price = 0

        pos.last_price = trade.price

    def _update_position_prices(self, prices: Dict[str, float]) -> None:
        """更新持仓最新价"""
        for code, price in prices.items():
            if code in self.positions:
                self.positions[code].last_price = price

    def get_position(self, code: str) -> Optional[PositionData]:
        return self.positions.get(code)

    def get_position_profit(self) -> float:
        """获取总持仓盈亏"""
        return sum((pos.last_price - pos.avg_price) * pos.volume for pos in self.positions.values() if pos.volume > 0)

    def get_total_equity(self) -> float:
        """获取总权益"""
        pos_value = sum(pos.volume * pos.last_price for pos in self.positions.values() if pos.volume > 0)
        return self.cash + pos_value

    def run(self) -> Dict:
        """执行回测

        Returns: 回测报告
        """
        if not self.tick_data:
            logger.warning("无 Tick 数据, 跳过回测")
            return {}

        # 合并所有 code 的 Tick, 按时间排序
        all_ticks: List[TickData] = []
        for _code, ticks in self.tick_data.items():
            all_ticks.extend(ticks)
        all_ticks.sort(key=lambda t: t.timestamp)

        logger.info(f"开始 Tick 回测: {len(all_ticks)} 条 Tick, {len(self.tick_data)} 个标的")

        # 按日期分组用于日终结算
        daily_ticks: Dict[int, List[TickData]] = defaultdict(list)
        for tick in all_ticks:
            daily_ticks[tick.date].append(tick)

        prev_equity = self.initial_capital

        for tick in all_ticks:
            self.current_tick = tick

            # 1. 撮合待成交订单
            self._match_pending_orders(tick)

            # 2. 更新持仓价格
            self._update_position_prices({tick.code: tick.price})

            # 3. 调用策略回调
            if self.strategy and hasattr(self.strategy, "on_tick"):
                self.strategy.on_tick(self, tick)

            # 4. 记录权益
            equity = self.get_total_equity()
            self.equity_curve.append(
                {
                    "timestamp": tick.timestamp,
                    "date": tick.date,
                    "time": tick.time,
                    "code": tick.code,
                    "price": tick.price,
                    "equity": equity,
                    "cash": self.cash,
                }
            )

        # 日终结算
        for d, ticks in daily_ticks.items():
            ticks[-1]
            equity = self.get_total_equity()
            pnl = equity - prev_equity
            self.daily_pnl.append(
                {
                    "date": d,
                    "equity": equity,
                    "pnl": pnl,
                    "return": pnl / prev_equity if prev_equity > 0 else 0,
                }
            )
            prev_equity = equity

        return self._generate_report()

    def _generate_report(self) -> Dict:
        """生成回测报告"""
        if not self.equity_curve:
            return {}

        equities = [e["equity"] for e in self.equity_curve]
        final_equity = equities[-1]
        total_return = (final_equity / self.initial_capital) - 1

        # 最大回撤
        peak = equities[0]
        max_dd = 0
        max_dd_date = 0
        for e in self.equity_curve:
            eq = e["equity"]
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_date = e.get("date", 0)

        # 夏普比率
        if len(equities) > 1:
            returns = np.diff(equities) / equities[:-1]
            if np.std(returns) > 0:
                # Tick 频率转年化: 假设 252 交易日, 每天 240 个 Tick (分钟级)
                n_ticks_per_year = 252 * 240
                sharpe = (np.mean(returns) / np.std(returns)) * (n_ticks_per_year**0.5)
            else:
                sharpe = 0
        else:
            sharpe = 0

        # 日级收益
        daily_returns = [d["return"] for d in self.daily_pnl] if self.daily_pnl else []
        daily_sharpe = 0
        if len(daily_returns) > 1 and np.std(daily_returns) > 0:
            daily_sharpe = (np.mean(daily_returns) / np.std(daily_returns)) * (252**0.5)

        # 胜率
        win_days = sum(1 for d in self.daily_pnl if d["pnl"] > 0)
        total_days = len(self.daily_pnl)
        win_rate = win_days / total_days if total_days > 0 else 0

        return {
            "engine": "TickBacktestEngine",
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 4),
            "annual_return": round(total_return / max(len(self.daily_pnl) / 252, 0.01), 4),
            "max_drawdown": round(max_dd, 4),
            "max_drawdown_date": max_dd_date,
            "sharpe_ratio": round(sharpe, 4),
            "daily_sharpe": round(daily_sharpe, 4),
            "win_rate": round(win_rate, 4),
            "n_ticks": len(self.equity_curve),
            "n_trading_days": total_days,
            "n_trades": len(self.trades),
            "n_pending_orders": len(self.pending_orders),
            "total_commission": round(
                sum(t.amount * self.contracts.get_contract(t.code).commission_rate for t in self.trades), 2
            ),
        }


# === 工具函数 ===


def ticks_from_csv(csv_path: str, code: str, exchange: str = "SSE") -> List[TickData]:
    """从 CSV 加载 Tick 数据

    CSV 格式: timestamp,price,open,high,low,pre_close,volume,amount
    """
    import csv

    ticks = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = float(row.get("timestamp", 0))
            dt = datetime.fromtimestamp(ts) if ts > 0 else datetime.now()
            ticks.append(
                TickData(
                    code=code,
                    exchange=exchange,
                    price=float(row["price"]),
                    open=float(row.get("open", row["price"])),
                    high=float(row.get("high", row["price"])),
                    low=float(row.get("low", row["price"])),
                    pre_close=float(row.get("pre_close", row["price"])),
                    volume=float(row.get("volume", 0)),
                    amount=float(row.get("amount", 0)),
                    timestamp=ts,
                    datetime_str=dt.strftime("%Y-%m-%d %H:%M:%S"),
                    date=int(dt.strftime("%Y%m%d")),
                    time=int(dt.strftime("%H%M%S")),
                )
            )
    return ticks


def bars_from_csv(csv_path: str, code: str, exchange: str = "SSE", period: str = "1d") -> List[BarData]:
    """从 CSV 加载 Bar 数据"""
    import csv

    bars = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", "")
            try:
                d = int(date_str.replace("-", ""))
            except (ValueError, TypeError):
                d = 0
            bars.append(
                BarData(
                    code=code,
                    exchange=exchange,
                    period=period,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                    amount=float(row.get("amount", 0)),
                    date=d,
                )
            )
    return bars


def run_tick_backtest(
    strategy: Any, tick_data: Dict[str, List[TickData]], initial_capital: float = 1_000_000, slippage: float = 0.001
) -> Dict:
    """便捷函数: 一行运行 Tick 回测"""
    engine = TickBacktestEngine(
        initial_capital=initial_capital,
        slippage_rate=slippage,
    )
    engine.set_strategy(strategy)
    engine.load_tick_data(tick_data)
    return engine.run()


__all__ = [
    "TickBacktestEngine",
    "TickMatcher",
    "bars_from_csv",
    "run_tick_backtest",
    "ticks_from_csv",
]
