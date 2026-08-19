"""
WonderTrader风格回测引擎模块

实现核心回测功能：
- 历史数据回放（支持日频/分钟频）
- ETF资金流信号策略回测
- 绩效指标计算（收益率、胜率、最大回撤、夏普比率等）
- 支持滑点和手续费模拟
- 支持多策略对比

适用于验证ETF资金流信号策略的历史有效性。
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta
from typing import Any, Callable, ClassVar, Optional, TypedDict

# E1 加固: 补全 pandas 导入。wt_backtest_engine.py:543 的 `price_data: Dict[str, "pd.DataFrame"]`
# 仅作字符串类型注解, 普通运行不求值; 但为消除 F821 未定义名隐患(审查报告 B1 降 P1 项),
# 显式导入 pandas, 确保运行时求值时不再 NameError。
import pandas as pd  # noqa: F401

logger = logging.getLogger(__name__)


# ============================================================
# TypedDicts: 为 BacktestEngine 状态容器提供严格类型
# 替代 8 处裸 {} / [] + # type: ignore[assignment]
# ============================================================

class PositionDict(TypedDict):
    """BacktestEngine.positions[code] 的结构。"""

    qty: int
    avg_cost: float
    current_price: float


class BaseTradeDict(TypedDict, total=False):
    """BUY 与 SELL trade 公共字段 (两方向字段对称但不同时出现)。"""

    date: Optional[str]
    code: str
    action: str  # "BUY" | "SELL"
    qty: int
    price: float
    execution_price: float
    commission: float
    total_cost: float
    total_revenue: float


class EquityPointDict(TypedDict):
    date: str
    equity: float


class DailyPnlDict(TypedDict):
    date: str
    equity: float
    cash: float
    position_value: float
    daily_return: float
    daily_pnl: float
    positions: dict[str, PositionDict]


class BacktestDayData(TypedDict, total=False):
    """run() 方法 data 参数 / data_loader 返回项的典型结构。"""

    date: str
    prices: dict[str, float]
    etf_signals: dict[str, dict[str, Any]]
    limit_up_prices: dict[str, Any]
    limit_down_prices: dict[str, Any]
    suspended: dict[str, bool]
    raw_data: Any


class BacktestSignalDict(TypedDict):
    """strategy_func 返回信号。"""

    code: str
    action: str  # "BUY" | "SELL"
    qty: int
    price: float


class SignalThresholds(TypedDict, total=False):
    """ETFSignalStrategy.signal_thresholds。"""

    strong_buy: str
    medium_buy: str
    strong_sell: str
    medium_sell: str


class BacktestEngine:
    """轻量级回测引擎"""

    def __init__(
        self,
        initial_capital: float = 1000000.0,
        commission_rate: float = 0.0003,
        slippage_rate: float = 0.001,
        min_commission: float = 5.0,
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.min_commission = min_commission

        self.cash: float = initial_capital
        self.positions: dict[str, PositionDict] = {}
        self.trades: list[BaseTradeDict] = []
        self.daily_pnl: list[DailyPnlDict] = []
        self.equity_curve: list[EquityPointDict] = []
        self.current_date: Optional[str] = None

    def reset(self):
        """重置回测状态"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.daily_pnl = []
        self.equity_curve = []
        self.current_date = None

    def calculate_commission(self, amount: float) -> float:
        """计算手续费"""
        commission = amount * self.commission_rate
        return max(commission, self.min_commission)

    def calculate_slippage(self, price: float, qty: int, direction: str) -> float:
        """计算滑点"""
        slippage = price * self.slippage_rate
        if direction == "BUY":
            return price + slippage
        else:
            return price - slippage

    def buy(self, code: str, price: float, qty: int) -> bool:
        """买入"""
        execution_price = self.calculate_slippage(price, qty, "BUY")
        total_cost = execution_price * qty
        commission = self.calculate_commission(total_cost)

        if total_cost + commission > self.cash:
            return False

        self.cash -= total_cost + commission

        if code not in self.positions:
            self.positions[code] = {
                "qty": 0,
                "avg_cost": 0.0,
                "current_price": 0.0,
            }

        pos = self.positions[code]
        total_qty = pos["qty"] + qty
        pos["avg_cost"] = (pos["qty"] * pos["avg_cost"] + qty * execution_price) / total_qty
        pos["qty"] = total_qty
        pos["current_price"] = price

        self.trades.append(
            {
                "date": self.current_date,
                "code": code,
                "action": "BUY",
                "qty": qty,
                "price": price,
                "execution_price": execution_price,
                "commission": commission,
                "total_cost": total_cost + commission,
            }
        )

        return True

    def sell(self, code: str, price: float, qty: int) -> bool:
        """卖出"""
        if code not in self.positions or self.positions[code]["qty"] < qty:
            return False

        execution_price = self.calculate_slippage(price, qty, "SELL")
        total_revenue = execution_price * qty
        commission = self.calculate_commission(total_revenue)

        self.cash += total_revenue - commission

        pos = self.positions[code]
        pos["qty"] -= qty
        pos["current_price"] = price

        if pos["qty"] == 0:
            del self.positions[code]

        self.trades.append(
            {
                "date": self.current_date,
                "code": code,
                "action": "SELL",
                "qty": qty,
                "price": price,
                "execution_price": execution_price,
                "commission": commission,
                "total_revenue": total_revenue - commission,
            }
        )

        return True

    def update_prices(self, prices: dict[str, float]) -> None:
        """更新持仓价格"""
        for code, price in prices.items():
            if code in self.positions:
                self.positions[code]["current_price"] = price

    def get_total_equity(self) -> float:
        """计算总权益"""
        position_value = sum(pos["qty"] * pos["current_price"] for pos in self.positions.values())
        return float(self.cash + position_value)

    def record_daily_pnl(self, date: Optional[str]) -> None:
        """记录每日盈亏。date 允许 None (启动期首条记录), 用 "" 占位以避免类型漂移。"""
        equity = self.get_total_equity()
        record_date = "" if date is None else date
        self.equity_curve.append({"date": record_date, "equity": float(equity)})

        if len(self.equity_curve) > 1:
            prev_equity = self.equity_curve[-2]["equity"]
            daily_return = (equity - prev_equity) / prev_equity if prev_equity else 0.0
            daily_pnl = equity - prev_equity
        else:
            daily_return = 0.0
            daily_pnl = 0.0

        point: DailyPnlDict = {
            "date": record_date,
            "equity": float(equity),
            "cash": float(self.cash),
            "position_value": float(equity - self.cash),
            "daily_return": float(daily_return),
            "daily_pnl": float(daily_pnl),
            "positions": {k: v.copy() for k, v in self.positions.items()},
        }
        self.daily_pnl.append(point)

    def _is_suspended(self, day_data: dict[str, Any], code: str) -> bool:
        """P2-2: 判断标的当日是否停牌。

        支持两种来源：
          1. day_data["suspended"] = {code: bool}
          2. 价格缺失或为 0（停牌日无行情）
        停牌标的不可交易，且持仓估值冻结（用上一收盘价）。
        """
        suspended = day_data.get("suspended", {})
        if isinstance(suspended, dict) and code in suspended:
            return bool(suspended[code])
        prices = day_data.get("prices", {})
        p = prices.get(code, 0)
        return p <= 0

    def run(self, data: list[BacktestDayData], strategy_func: Callable[[dict[str, Any], dict[str, PositionDict]], list[BacktestSignalDict]], verbose: bool = False) -> dict[str, Any]:
        """运行回测

        Args:
            data: 历史数据列表，每个元素包含 date 和 prices
            strategy_func: 策略函数，接收 (current_data, positions) 返回交易信号列表
            verbose: 是否输出详细日志

        P2-2 涨跌停/停牌约束:
            若 day_data 提供 limit_up_prices / limit_down_prices / suspended 字段，则启用 A股约束:
              - 涨停 (price >= limit_up)  不可买入
              - 跌停 (price <= limit_down) 不可卖出
              - 停牌 (suspended) 不可交易, 持仓冻结
            未提供这些字段时向后兼容 (不约束), 与原有行为一致。

        Returns:
            回测结果摘要
        """
        self.reset()

        for _i, day_data in enumerate(data):
            self.current_date = day_data["date"]
            prices = day_data.get("prices", {})

            self.update_prices(prices)

            # P2-2: 涨跌停价/停牌信息 (可选)
            limit_up_prices = day_data.get("limit_up_prices", {}) or {}
            limit_down_prices = day_data.get("limit_down_prices", {}) or {}

            signals = strategy_func(day_data, self.positions)

            for signal in signals:
                code = signal["code"]
                action = signal["action"]
                qty = signal["qty"]
                price = prices.get(code, signal.get("price", 0))

                if price <= 0:
                    continue

                # P2-2: 停牌约束——停牌不可交易
                if self._is_suspended(day_data, code):
                    if verbose:
                        logger.info(f"  {self.current_date} {code} 停牌, 跳过 {action} {qty}")
                    continue

                # P2-2: 涨跌停约束——涨停不可买, 跌停不可卖
                if action == "BUY":
                    lu = limit_up_prices.get(code)
                    if lu and price >= float(lu):
                        if verbose:
                            logger.info(f"  {self.current_date} {code} 涨停, 无法买入 {qty}")
                        continue
                elif action == "SELL":
                    ld = limit_down_prices.get(code)
                    if ld and price <= float(ld):
                        if verbose:
                            logger.info(f"  {self.current_date} {code} 跌停, 无法卖出 {qty}")
                        continue

                if action == "BUY":
                    success = self.buy(code, price, qty)
                elif action == "SELL":
                    success = self.sell(code, price, qty)
                else:
                    success = False

                if verbose and success:
                    logger.info(f"  {self.current_date} {action} {code} {qty} @ {price:.4f}")

            self.record_daily_pnl(self.current_date)

        return self.generate_report()

    def generate_report(self) -> dict[str, Any]:
        """生成回测报告"""
        if not self.daily_pnl:
            return {"status": "error", "message": "无回测数据"}

        total_return = (self.equity_curve[-1]["equity"] - self.initial_capital) / self.initial_capital
        daily_returns = [d["daily_return"] for d in self.daily_pnl if d["daily_return"] != 0]

        if daily_returns:
            avg_daily_return = sum(daily_returns) / len(daily_returns)
            std_daily_return = math.sqrt(sum((r - avg_daily_return) ** 2 for r in daily_returns) / len(daily_returns))
            sharpe_ratio = avg_daily_return / std_daily_return * math.sqrt(252) if std_daily_return > 0 else 0
        else:
            avg_daily_return = 0
            std_daily_return = 0
            sharpe_ratio = 0

        max_equity = self.initial_capital
        max_drawdown = 0
        for point in self.equity_curve:
            max_equity = max(max_equity, point["equity"])
            drawdown = (max_equity - point["equity"]) / max_equity
            max_drawdown = max(max_drawdown, drawdown)

        winning_trades = [t for t in self.trades if t["action"] == "SELL"]
        if winning_trades:
            win_count = sum(1 for t in winning_trades if t["total_revenue"] > 0)
            win_rate = win_count / len(winning_trades)
        else:
            win_rate = 0

        total_commission = sum(t["commission"] for t in self.trades)
        total_trades = len(self.trades)
        avg_trade_amount = sum(t.get("total_cost", t.get("total_revenue", 0)) for t in self.trades) / max(
            total_trades, 1
        )

        return {
            "status": "success",
            "initial_capital": self.initial_capital,
            "final_equity": self.equity_curve[-1]["equity"],
            "total_return": total_return,
            "annualized_return": (max(1 + total_return, 1e-9) ** (252 / len(self.daily_pnl)) - 1)
            if len(self.daily_pnl) > 0
            else 0,
            "avg_daily_return": avg_daily_return,
            "std_daily_return": std_daily_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "buy_trades": len([t for t in self.trades if t["action"] == "BUY"]),
            "sell_trades": len([t for t in self.trades if t["action"] == "SELL"]),
            "total_commission": total_commission,
            "avg_trade_amount": avg_trade_amount,
            "equity_curve": self.equity_curve,
            "daily_pnl": self.daily_pnl,
            "trades": self.trades,
            "positions": self.positions,
            "backtest_days": len(self.daily_pnl),
        }


class ETFSignalStrategy:
    """ETF资金流信号策略

    基于ETF资金流信号进行交易：
    - 强加仓信号：买入
    - 强减仓信号：卖出
    - 中信号：半仓操作

    警告: 回测时需确保 etf_signals 仅使用当日及之前的数据计算，
    不得包含未来信息。建议在回测循环中实时计算信号而非预计算。
    """

    def __init__(
        self,
        signal_thresholds: Optional[SignalThresholds] = None,
        max_position_pct: float = 0.3,
        validate_no_lookahead: bool = True,
    ):
        self.signal_thresholds: SignalThresholds = (
            signal_thresholds
            if signal_thresholds is not None
            else SignalThresholds(
                strong_buy="强加仓",
                medium_buy="加仓",
                strong_sell="强减仓",
                medium_sell="减仓",
            )
        )
        self.max_position_pct = max_position_pct
        self.validate_no_lookahead = validate_no_lookahead
        self._validated_dates: set[str] = set()

    def generate_signals(self, day_data: dict[str, Any], positions: dict[str, PositionDict]) -> list[BacktestSignalDict]:
        """生成交易信号"""
        signals: list[BacktestSignalDict] = []
        etf_signals: dict[str, dict[str, Any]] = day_data.get("etf_signals", {})
        prices: dict[str, float] = day_data.get("prices", {})
        equity = float(day_data.get("equity", 1000000.0))

        strong_buy = self.signal_thresholds.get("strong_buy")
        medium_buy = self.signal_thresholds.get("medium_buy")
        strong_sell = self.signal_thresholds.get("strong_sell")
        medium_sell = self.signal_thresholds.get("medium_sell")

        for code, signal_info in etf_signals.items():
            signal = str(signal_info.get("signal", ""))
            signal_info.get("inflow", 0)
            price = float(prices.get(code, 0.0))

            if price <= 0:
                continue

            current_qty = 0
            pos = positions.get(code)
            if pos is not None:
                current_qty = int(pos["qty"])

            signal_is_strong_buy = strong_buy is not None and signal == strong_buy
            signal_is_medium_buy = medium_buy is not None and signal == medium_buy
            signal_is_strong_sell = strong_sell is not None and signal == strong_sell
            signal_is_medium_sell = medium_sell is not None and signal == medium_sell

            if signal_is_strong_buy or signal_is_medium_buy:
                ratio = 0.5 if signal_is_medium_buy else 1.0
                max_amount = equity * self.max_position_pct * ratio
                current_pos_value = current_qty * price
                available_amount = max_amount - current_pos_value

                if available_amount > 1000:
                    qty = int(available_amount / price / 100) * 100
                    if qty >= 100:
                        signals.append({"code": code, "action": "BUY", "qty": qty, "price": price})

            elif signal_is_strong_sell or signal_is_medium_sell:
                if current_qty > 0:
                    qty = current_qty
                    if signal_is_medium_sell:
                        qty = qty // 2

                    if qty >= 100:
                        signals.append({"code": code, "action": "SELL", "qty": qty, "price": price})

        return signals


class BacktestDataLoader:
    """回测数据加载器

    警告: load_from_positions_history 使用 positions.json 中的 est_price/avg_cost，
    这些价格可能包含事后信息（若文件在盘后写入）。
    回测结果可能因此虚高，建议使用独立的 OHLC 历史数据源。
    """

    # 类级去重标记: L455 原 # type: ignore[assignment] 根因是未声明类属性
    _warned_est_price: ClassVar[bool] = False

    @staticmethod
    def load_from_positions_history(positions_history_dir: str, tickers: Optional[list[str]] = None) -> list[BacktestDayData]:
        """从positions.json历史记录加载数据"""
        data: list[BacktestDayData] = []
        files = sorted(os.listdir(positions_history_dir))

        for filename in files:
            if not filename.startswith("positions_") or not filename.endswith(".json"):
                continue

            date_str = filename.replace("positions_", "").replace(".json", "")
            file_path = os.path.join(positions_history_dir, filename)

            try:
                with open(file_path, encoding="utf-8") as f:
                    pos_data = json.load(f)

                positions: dict[str, Any] = pos_data.get("positions", {})
                etf_signals: dict[str, dict[str, Any]] = {}
                prices: dict[str, float] = {}

                for code, pos in positions.items():
                    if tickers and code not in tickers:
                        continue

                    etf_signals[code] = {
                        "signal": pos.get("etf_flow_signal", ""),
                        "inflow": pos.get("etf_inflow", 0),
                    }
                    # 使用 est_price 或 avg_cost 作为价格 — 若这些值来自盘后文件，存在数据泄露风险
                    price = float(pos.get("est_price", 0) or pos.get("avg_cost", 0))
                    if pos.get("est_price", 0) and not BacktestDataLoader._warned_est_price:
                        import logging

                        logging.getLogger("backtest").warning(
                            "⚠️ 回测使用 est_price（估算价格），可能包含事后信息。"
                            "建议使用独立的历史 OHLC 数据源以获得无偏回测结果。"
                        )
                        BacktestDataLoader._warned_est_price = True
                    prices[code] = price

                if prices:
                    day: BacktestDayData = {
                        "date": date_str,
                        "prices": prices,
                        "etf_signals": etf_signals,
                        "raw_data": pos_data,
                    }
                    data.append(day)

            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                continue

        return data

    @staticmethod
    def generate_synthetic_data(
        start_date: str, end_date: str, tickers: list[str], base_price: float = 2.0, volatility: float = 0.02,
        with_limit_constraints: bool = False,
    ) -> list[BacktestDayData]:
        """生成合成回测数据

        Args:
            with_limit_constraints: U2 新增, 是否注入 limit_up_prices/limit_down_prices/suspended 字段.
                True 时调用 price_limit_calculator.enrich_day_data_list 富化, 默认 False (向后兼容).
        """
        data = []
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        current = start
        prices = {t: base_price for t in tickers}

        import random

        random.seed(42)

        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            date_str = current.strftime("%Y-%m-%d")

            for ticker in tickers:
                change = (random.random() - 0.48) * 2 * volatility
                prices[ticker] *= 1 + change
                prices[ticker] = round(prices[ticker], 4)

            inflow_values = [0, 5, 10, 15, 20, 25, 30, 50, 70, 100]
            etf_signals = {}

            for ticker in tickers:
                inflow = inflow_values[random.randint(0, len(inflow_values) - 1)] * (1 if random.random() > 0.3 else -1)

                if inflow >= 50:
                    signal = "强加仓"
                elif inflow >= 10:
                    signal = "加仓"
                elif inflow <= -50:
                    signal = "强减仓"
                elif inflow <= -10:
                    signal = "减仓"
                elif inflow >= 2:
                    signal = "关注"
                else:
                    signal = "中性"

                etf_signals[ticker] = {"signal": signal, "inflow": inflow}

            data.append(
                {
                    "date": date_str,
                    "prices": prices.copy(),
                    "etf_signals": etf_signals,
                }
            )

            current += timedelta(days=1)

        # U2: 可选注入涨跌停/停牌约束字段
        if with_limit_constraints:
            from utils.price_limit_calculator import enrich_day_data_list

            enrich_day_data_list(data)

        return data

    @staticmethod
    def load_from_ohlcv(
        price_data: dict[str, "pd.DataFrame"],
        st_codes: Optional[set[str]] = None,
        etf_signals_by_date: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[BacktestDayData]:
        """U2: 从 OHLCV DataFrame 构建带涨跌停/停牌字段的回测数据.

        使用 price_limit_calculator.build_backtest_data_from_ohlcv,
        生成的 day_data 含 limit_up_prices/limit_down_prices/suspended 字段,
        BacktestEngine.run() 会自动启用 A股涨跌停/停牌约束 (P2-2 已实现).

        Args:
            price_data: {symbol: DataFrame(index=date, columns=[open,high,low,close,volume])}
            st_codes: ST 股票代码集合 (可选, 用于 ±5% 涨跌停)
            etf_signals_by_date: 可选 ETF 信号 {date_str: {code: {signal, inflow}}}

        Returns:
            day_data 列表, 可直接传入 BacktestEngine.run()
        """
        from utils.price_limit_calculator import build_backtest_data_from_ohlcv

        return build_backtest_data_from_ohlcv(
            price_data=price_data,
            st_codes=st_codes,
            etf_signals_by_date=etf_signals_by_date,
        )


def run_etf_signal_backtest(data: list[dict], initial_capital: float = 1000000.0, **kwargs) -> dict:
    """便捷函数：运行ETF信号策略回测"""
    strategy = ETFSignalStrategy(**kwargs)
    engine = BacktestEngine(initial_capital=initial_capital)

    def strategy_func(day_data, positions):
        return strategy.generate_signals(day_data, positions)

    return engine.run(data, strategy_func)


def compare_strategies(data: list[dict], strategies: dict[str, Callable], initial_capital: float = 1000000.0) -> dict:
    """比较多个策略"""
    results = {}

    for name, strategy_func in strategies.items():
        engine = BacktestEngine(initial_capital=initial_capital)
        result = engine.run(data, strategy_func)
        results[name] = result

    return results


if __name__ == "__main__":
    tickers = ["588080.SH", "512880.SH", "510050.SH", "512760.SH"]

    logger.info("===== 生成合成回测数据 =====")
    data = BacktestDataLoader.generate_synthetic_data("2024-01-01", "2025-12-31", tickers)
    logger.info(f"生成 {len(data)} 个交易日数据")

    logger.info("===== 运行ETF信号策略回测 =====")
    result = run_etf_signal_backtest(data, initial_capital=1000000.0)

    logger.info(f"初始资金: ¥{result['initial_capital']:,.0f}")
    logger.info(f"最终权益: ¥{result['final_equity']:,.0f}")
    logger.info(f"总收益率: {result['total_return']:.2%}")
    logger.info(f"年化收益率: {result['annualized_return']:.2%}")
    logger.info(f"夏普比率: {result['sharpe_ratio']:.2f}")
    logger.info(f"最大回撤: {result['max_drawdown']:.2%}")
    logger.info(f"胜率: {result['win_rate']:.2%}")
    logger.info(f"总交易次数: {result['total_trades']}")
    logger.info(f"总手续费: ¥{result['total_commission']:,.2f}")
