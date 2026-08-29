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

import json
import math
import os
from collections.abc import Callable
from datetime import datetime, timedelta


class BacktestEngine:
    """轻量级回测引擎"""

    def __init__(self, initial_capital: float = 1000000.0, commission_rate: float = 0.0003,
                 slippage_rate: float = 0.001, min_commission: float = 5.0):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.min_commission = min_commission

        self.cash = initial_capital
        self.positions = {}
        self.trades = []
        self.daily_pnl = []
        self.equity_curve = []
        self.current_date = None

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
        return price - slippage

    def buy(self, code: str, price: float, qty: int) -> bool:
        """买入"""
        execution_price = self.calculate_slippage(price, qty, "BUY")
        total_cost = execution_price * qty
        commission = self.calculate_commission(total_cost)

        if total_cost + commission > self.cash:
            return False

        self.cash -= (total_cost + commission)

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

        self.trades.append({
            "date": self.current_date,
            "code": code,
            "action": "BUY",
            "qty": qty,
            "price": price,
            "execution_price": execution_price,
            "commission": commission,
            "total_cost": total_cost + commission,
        })

        return True

    def sell(self, code: str, price: float, qty: int) -> bool:
        """卖出"""
        if code not in self.positions or self.positions[code]["qty"] < qty:
            return False

        execution_price = self.calculate_slippage(price, qty, "SELL")
        total_revenue = execution_price * qty
        commission = self.calculate_commission(total_revenue)

        self.cash += (total_revenue - commission)

        pos = self.positions[code]
        pos["qty"] -= qty
        pos["current_price"] = price

        if pos["qty"] == 0:
            del self.positions[code]

        self.trades.append({
            "date": self.current_date,
            "code": code,
            "action": "SELL",
            "qty": qty,
            "price": price,
            "execution_price": execution_price,
            "commission": commission,
            "total_revenue": total_revenue - commission,
        })

        return True

    def update_prices(self, prices: dict[str, float]):
        """更新持仓价格"""
        for code, price in prices.items():
            if code in self.positions:
                self.positions[code]["current_price"] = price

    def get_total_equity(self) -> float:
        """计算总权益"""
        position_value = sum(
            pos["qty"] * pos["current_price"]
            for pos in self.positions.values()
        )
        return self.cash + position_value

    def record_daily_pnl(self, date: str):
        """记录每日盈亏"""
        equity = self.get_total_equity()
        self.equity_curve.append({"date": date, "equity": equity})

        if len(self.equity_curve) > 1:
            prev_equity = self.equity_curve[-2]["equity"]
            daily_return = (equity - prev_equity) / prev_equity
            daily_pnl = equity - prev_equity
        else:
            daily_return = 0.0
            daily_pnl = 0.0

        self.daily_pnl.append({
            "date": date,
            "equity": equity,
            "cash": self.cash,
            "position_value": equity - self.cash,
            "daily_return": daily_return,
            "daily_pnl": daily_pnl,
            "positions": {k: v.copy() for k, v in self.positions.items()},
        })

    def run(self, data: list[dict], strategy_func: Callable[[dict, dict], list[dict]],
            verbose: bool = False) -> dict:
        """运行回测

        Args:
            data: 历史数据列表，每个元素包含 date 和 prices
            strategy_func: 策略函数，接收 (current_data, positions) 返回交易信号列表
            verbose: 是否输出详细日志

        Returns:
            回测结果摘要
        """
        self.reset()

        for _i, day_data in enumerate(data):
            self.current_date = day_data["date"]
            prices = day_data.get("prices", {})

            self.update_prices(prices)

            signals = strategy_func(day_data, self.positions)

            for signal in signals:
                code = signal["code"]
                action = signal["action"]
                qty = signal["qty"]
                price = prices.get(code, signal.get("price", 0))

                if price <= 0:
                    continue

                if action == "BUY":
                    success = self.buy(code, price, qty)
                elif action == "SELL":
                    success = self.sell(code, price, qty)
                else:
                    success = False

                if verbose and success:
                    print(f"  {self.current_date} {action} {code} {qty} @ {price:.4f}")

            self.record_daily_pnl(self.current_date)

        return self.generate_report()

    def generate_report(self) -> dict:
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
        avg_trade_amount = sum(t.get("total_cost", t.get("total_revenue", 0)) for t in self.trades) / max(total_trades, 1)

        return {
            "status": "success",
            "initial_capital": self.initial_capital,
            "final_equity": self.equity_curve[-1]["equity"],
            "total_return": total_return,
            "annualized_return": (1 + total_return) ** (252 / len(self.daily_pnl)) - 1 if len(self.daily_pnl) > 0 else 0,
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
    """

    def __init__(self, signal_thresholds: dict = None, max_position_pct: float = 0.3):
        self.signal_thresholds = signal_thresholds or {
            "strong_buy": "强加仓",
            "medium_buy": "加仓",
            "strong_sell": "强减仓",
            "medium_sell": "减仓",
        }
        self.max_position_pct = max_position_pct

    def generate_signals(self, day_data: dict, positions: dict) -> list[dict]:
        """生成交易信号"""
        signals = []
        etf_signals = day_data.get("etf_signals", {})
        prices = day_data.get("prices", {})
        equity = day_data.get("equity", 1000000.0)

        for code, signal_info in etf_signals.items():
            signal = signal_info.get("signal", "")
            signal_info.get("inflow", 0)
            price = prices.get(code, 0)

            if price <= 0:
                continue

            if signal == self.signal_thresholds["strong_buy"]:
                max_amount = equity * self.max_position_pct
                current_pos_value = positions.get(code, {}).get("qty", 0) * price
                available_amount = max_amount - current_pos_value

                if available_amount > 1000:
                    qty = int(available_amount / price / 100) * 100
                    if qty >= 100:
                        signals.append({"code": code, "action": "BUY", "qty": qty, "price": price})

            elif signal == self.signal_thresholds["medium_buy"]:
                max_amount = equity * self.max_position_pct * 0.5
                current_pos_value = positions.get(code, {}).get("qty", 0) * price
                available_amount = max_amount - current_pos_value

                if available_amount > 1000:
                    qty = int(available_amount / price / 100) * 100
                    if qty >= 100:
                        signals.append({"code": code, "action": "BUY", "qty": qty, "price": price})

            elif signal in [self.signal_thresholds["strong_sell"], self.signal_thresholds["medium_sell"]]:
                if code in positions and positions[code]["qty"] > 0:
                    qty = positions[code]["qty"]
                    if signal == self.signal_thresholds["medium_sell"]:
                        qty = qty // 2

                    if qty >= 100:
                        signals.append({"code": code, "action": "SELL", "qty": qty, "price": price})

        return signals


class BacktestDataLoader:
    """回测数据加载器"""

    @staticmethod
    def load_from_positions_history(positions_history_dir: str, tickers: list[str] = None) -> list[dict]:
        """从positions.json历史记录加载数据"""
        data = []
        files = sorted(os.listdir(positions_history_dir))

        for filename in files:
            if not filename.startswith("positions_") or not filename.endswith(".json"):
                continue

            date_str = filename.replace("positions_", "").replace(".json", "")
            file_path = os.path.join(positions_history_dir, filename)

            try:
                with open(file_path, encoding="utf-8") as f:
                    pos_data = json.load(f)

                positions = pos_data.get("positions", {})
                etf_signals = {}
                prices = {}

                for code, pos in positions.items():
                    if tickers and code not in tickers:
                        continue

                    etf_signals[code] = {
                        "signal": pos.get("etf_flow_signal", ""),
                        "inflow": pos.get("etf_inflow", 0),
                    }
                    prices[code] = pos.get("est_price", 0) or pos.get("avg_cost", 0)

                if prices:
                    data.append({
                        "date": date_str,
                        "prices": prices,
                        "etf_signals": etf_signals,
                        "raw_data": pos_data,
                    })

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):

                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue

        return data

    @staticmethod
    def generate_synthetic_data(start_date: str, end_date: str, tickers: list[str],
                                base_price: float = 2.0, volatility: float = 0.02) -> list[dict]:
        """生成合成回测数据"""
        data = []
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        current = start
        prices = dict.fromkeys(tickers, base_price)

        import random
        random.seed(42)

        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            date_str = current.strftime("%Y-%m-%d")

            for ticker in tickers:
                change = (random.random() - 0.48) * 2 * volatility
                prices[ticker] *= (1 + change)
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

            data.append({
                "date": date_str,
                "prices": prices.copy(),
                "etf_signals": etf_signals,
            })

            current += timedelta(days=1)

        return data


def run_etf_signal_backtest(data: list[dict], initial_capital: float = 1000000.0,
                            **kwargs) -> dict:
    """便捷函数：运行ETF信号策略回测"""
    strategy = ETFSignalStrategy(**kwargs)
    engine = BacktestEngine(initial_capital=initial_capital)

    def strategy_func(day_data, positions):
        return strategy.generate_signals(day_data, positions)

    return engine.run(data, strategy_func)


def compare_strategies(data: list[dict], strategies: dict[str, Callable],
                       initial_capital: float = 1000000.0) -> dict:
    """比较多个策略"""
    results = {}

    for name, strategy_func in strategies.items():
        engine = BacktestEngine(initial_capital=initial_capital)
        result = engine.run(data, strategy_func)
        results[name] = result

    return results


if __name__ == "__main__":
    tickers = ["588080.SH", "512880.SH", "510050.SH", "512760.SH"]

    print("===== 生成合成回测数据 =====")
    data = BacktestDataLoader.generate_synthetic_data("2024-01-01", "2025-12-31", tickers)
    print(f"生成 {len(data)} 个交易日数据")
    print()

    print("===== 运行ETF信号策略回测 =====")
    result = run_etf_signal_backtest(data, initial_capital=1000000.0)

    print(f"初始资金: ¥{result['initial_capital']:,.0f}")
    print(f"最终权益: ¥{result['final_equity']:,.0f}")
    print(f"总收益率: {result['total_return']:.2%}")
    print(f"年化收益率: {result['annualized_return']:.2%}")
    print(f"夏普比率: {result['sharpe_ratio']:.2f}")
    print(f"最大回撤: {result['max_drawdown']:.2%}")
    print(f"胜率: {result['win_rate']:.2%}")
    print(f"总交易次数: {result['total_trades']}")
    print(f"总手续费: ¥{result['total_commission']:,.2f}")
