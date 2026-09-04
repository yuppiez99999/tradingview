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
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, ClassVar, TypedDict

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

    date: str | None
    signal_date: str | None  # 信号产生日 (延迟执行时与 date 不同)
    code: str
    action: str  # "BUY" | "SELL"
    qty: int
    price: float
    execution_price: float
    commission: float
    stamp_tax: float  # 卖出印花税 (仅股票)
    transfer_fee: float  # 过户费
    total_cost: float
    total_revenue: float
    realized_pnl: float  # SELL 已实现盈亏 (净回款 - 持仓成本)


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
    opens: dict[str, float]  # 可选: 执行日开盘价, 缺失时 _execution_price 回退当日收盘
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
        stamp_tax_rate: float = 0.0005,
        transfer_fee_rate: float = 0.00001,
        risk_free_rate: float = 0.02,
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.min_commission = min_commission
        # P1-3 费用修复: 与真实 A 股口径一致。
        #   印花税 = 卖出金额 * stamp_tax_rate (仅股票, ETF/基金免征)
        #   过户费 = 成交金额 * transfer_fee_rate (仅股票, 双边)
        self.stamp_tax_rate = stamp_tax_rate
        self.transfer_fee_rate = transfer_fee_rate
        # P0-2 Sharpe 修复: 无风险利率(年化), 计算超额收益的日化基准
        self.risk_free_rate = risk_free_rate

        self.cash: float = initial_capital
        self.positions: dict[str, PositionDict] = {}
        self.trades: list[BaseTradeDict] = []
        self.daily_pnl: list[DailyPnlDict] = []
        self.equity_curve: list[EquityPointDict] = []
        self.current_date: str | None = None
        # T+1 执行状态: 同一执行日内已买入的 code 集合, 禁止当日卖出
        self._executed_buy_codes: set[str] = set()

    def reset(self) -> None:
        """重置回测状态"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.daily_pnl = []
        self.equity_curve = []
        self.current_date = None
        self._executed_buy_codes = set()

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

    @staticmethod
    def _is_stock_code(code: str) -> bool:
        """P1-3: 判断 code 是否为 A 股股票(需缴印花税/过户费)。

        规则(基于代码首位):
          - A 股股票: 6(沪主板/科创板688)/0(深主板)/3(创业板)/4(老三板)/8(北交所)/9(北交所)
          - 基金/ETF: 5 或 1 开头(51/58/56 沪基金, 15/16/18 深基金), 免征印花税与过户费
          - 债券: 1 开头(11/12/13), 免征
        无法识别(如单测伪代码 "A")视为非股票, 避免凭空加税。
        """
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        if not digits:
            return False
        return digits[0] in ("6", "0", "3", "4", "8", "9")

    def _calculate_taxes(
        self, amount: float, direction: str, is_stock: bool
    ) -> dict[str, float]:
        """P1-3: 计算交易税费(印花税 + 过户费)。direction: BUY/SELL。"""
        if not is_stock:
            return {"stamp_tax": 0.0, "transfer_fee": 0.0}
        transfer_fee = amount * self.transfer_fee_rate
        stamp_tax = amount * self.stamp_tax_rate if direction == "SELL" else 0.0
        return {"stamp_tax": stamp_tax, "transfer_fee": transfer_fee}

    def buy(self, code: str, price: float, qty: int) -> bool:
        """买入

        P1-3: 成本 = 滑点成交额 + 佣金 + 过户费(股票); avg_cost 计入全部费用,
        使后续 SELL 的 realized_pnl 口径为"净回款 - 含费成本", 不系统性虚增胜率。
        """
        execution_price = self.calculate_slippage(price, qty, "BUY")
        gross = execution_price * qty
        commission = self.calculate_commission(gross)
        taxes = self._calculate_taxes(
            gross, "BUY", self._is_stock_code(code)
        )
        total_cost = gross + commission + taxes["stamp_tax"] + taxes["transfer_fee"]

        if total_cost > self.cash:
            return False

        self.cash -= total_cost

        if code not in self.positions:
            self.positions[code] = {
                "qty": 0,
                "avg_cost": 0.0,
                "current_price": 0.0,
            }

        pos = self.positions[code]
        total_qty = pos["qty"] + qty
        # avg_cost 含佣金与过户费, 保证 realized_pnl 使用全口径成本
        pos["avg_cost"] = (
            pos["qty"] * pos["avg_cost"] + total_cost
        ) / total_qty
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
                "stamp_tax": taxes["stamp_tax"],
                "transfer_fee": taxes["transfer_fee"],
                "total_cost": total_cost,
            }
        )

        return True

    def sell(self, code: str, price: float, qty: int) -> bool:
        """卖出

        P0-1: 记录 realized_pnl = 净回款(滑点成交额-佣金-印花税-过户费)
              - 持仓含费成本, 为胜率统计提供真实盈亏判据。
        """
        if code not in self.positions or self.positions[code]["qty"] < qty:
            return False

        execution_price = self.calculate_slippage(price, qty, "SELL")
        gross = execution_price * qty
        commission = self.calculate_commission(gross)
        taxes = self._calculate_taxes(
            gross, "SELL", self._is_stock_code(code)
        )
        net_revenue = gross - commission - taxes["stamp_tax"] - taxes["transfer_fee"]

        pos = self.positions[code]
        cost_basis = pos["avg_cost"] * qty
        realized_pnl = net_revenue - cost_basis

        self.cash += net_revenue

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
                "stamp_tax": taxes["stamp_tax"],
                "transfer_fee": taxes["transfer_fee"],
                "total_revenue": net_revenue,
                "realized_pnl": realized_pnl,
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
        position_value = sum(
            pos["qty"] * pos["current_price"] for pos in self.positions.values()
        )
        return float(self.cash + position_value)

    def record_daily_pnl(self, date: str | None) -> None:
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

    def _is_suspended(self, day_data: BacktestDayData, code: str) -> bool:
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
        return bool(p <= 0)

    @staticmethod
    def _execution_price(day_data: BacktestDayData, code: str) -> float | None:
        """解析执行日成交价: 优先开盘价(opens), 无则回退当日收盘价。

        诚实假设: 缺失 opens 时以当日收盘近似成交, 不再用信号日价格。
        返回 None 表示执行日无该标的行情。
        """
        opens = day_data.get("opens", {}) or {}
        prices = day_data.get("prices", {}) or {}
        price = opens.get(code) if opens.get(code) else prices.get(code)
        if price is None or float(price) <= 0:
            return None
        return float(price)

    def _trade_reject_reason(
        self,
        signal: BacktestSignalDict,
        day_data: BacktestDayData,
        exec_price: float,
    ) -> str | None:
        """返回执行日撮合前的拒单原因(涨跌停 / T+1 / 非法方向), 无则 None。"""
        code = signal["code"]
        action = signal["action"]
        qty = int(signal["qty"])

        limit_up_prices = day_data.get("limit_up_prices", {}) or {}
        limit_down_prices = day_data.get("limit_down_prices", {}) or {}

        if action == "BUY":
            lu = limit_up_prices.get(code)
            if lu and exec_price >= float(lu):
                return f"{code} 涨停, 无法买入 {qty}"
        elif action == "SELL":
            ld = limit_down_prices.get(code)
            if ld and exec_price <= float(ld):
                return f"{code} 跌停, 无法卖出 {qty}"
            # T+1: 当日买入的持仓当日不可卖出
            if code in self._executed_buy_codes:
                return f"{code} T+1: 当日买入不可当日卖出 {qty}"
        else:
            return f"{code} 非法信号方向 {action}"
        return None

    def _execute_signal(
        self,
        signal: BacktestSignalDict,
        day_data: BacktestDayData,
        verbose: bool,
        signal_date: str | None = None,
    ) -> bool:
        """在"执行日"撮合一条交易信号(约束在撮合时校验)。

        P0-3: 信号在 T 收盘生成, 默认 T+1 以开盘价成交——撮合发生在执行日,
              因此涨跌停/停牌等约束以执行日行情为准, 而非信号日行情。
        P1-1: T+1 约束——同一执行日内先 BUY 后不可 SELL 同一 code。

        Returns: 是否成交。
        """
        code = signal["code"]
        action = signal["action"]
        qty = int(signal["qty"])

        # 停牌/无行情等临时状态由 run() 顺延, 此处兜底拒单
        exec_price = self._execution_price(day_data, code)
        if exec_price is None:
            if verbose:
                logger.info(
                    f"  {self.current_date} {code} 执行日无行情, 无法成交 {action}"
                )
            return False
        if self._is_suspended(day_data, code):
            if verbose:
                logger.info(
                    f"  {self.current_date} {code} 停牌, 跳过 {action} {qty}"
                )
            return False

        reason = self._trade_reject_reason(signal, day_data, exec_price)
        if reason:
            if verbose:
                logger.info(f"  {self.current_date} {reason}")
            return False

        trade_count_before = len(self.trades)
        if action == "BUY":
            success = self.buy(code, exec_price, qty)
            if success:
                self._executed_buy_codes.add(code)
        else:
            success = self.sell(code, exec_price, qty)

        if success:
            if len(self.trades) > trade_count_before:
                self.trades[-1]["signal_date"] = signal_date
            if verbose:
                logger.info(
                    f"  {self.current_date} {action} {code} {qty} @ {exec_price:.4f}"
                )
        return success

    def run(
        self,
        data: list[BacktestDayData],
        strategy_func: Callable[
            [dict[str, Any], dict[str, PositionDict]], list[BacktestSignalDict]
        ],
        verbose: bool = False,
        execution_delay: bool = True,
    ) -> dict[str, Any]:
        """运行回测

        Args:
            data: 历史数据列表, 每个元素包含 date 和 prices
                  (可选 opens: 次日执行用开盘价; 缺省回退当日收盘价)
            strategy_func: 策略函数, 接收 (current_data, positions) 返回交易信号列表
            verbose: 是否输出详细日志
            execution_delay: P0-3 成交延迟。True(默认) = 信号在 T 收盘生成,
                  T+1 以开盘价撮合, 消除"当日信号当日成交"的前视/可实现性偏差;
                  False = 旧版当日立即成交(仅用于兼容退路)。

        P2-2 涨跌停/停牌约束(在执行日撮合时校验):
            - 涨停 (price >= limit_up)  不可买入
            - 跌停 (price <= limit_down) 不可卖出
            - 停牌 (suspended) 不可交易, 持仓冻结

        P1-8 equity 注入: 策略调用前向 day_data 注入引擎实际总权益,
              使 ETF 策略的仓位计算与引擎 equity 曲线同步(而非固定兜底值)。

        Returns:
            回测结果摘要
        """
        self.reset()

        # P0-3: 信号队列, 元素为 (signal_date, signal)
        pending: list[tuple[str, BacktestSignalDict]] = []

        for _i, day_data in enumerate(data):
            self.current_date = day_data["date"]
            prices = day_data.get("prices", {}) or {}

            # 以执行日行情刷新持仓估值
            self.update_prices(prices)
            # 新执行日开始: 重置 T+1 当日已买集合
            self._executed_buy_codes = set()

            # 1) 撮合上一交易日收盘生成的挂起信号(本日开盘价成交)。
            #    停牌/无行情 = 临时不可成交 → 顺延至复牌后的执行日;
            #    涨跌停/T+1 拒单 = 当日不可成交 → 丢弃(信号已失效)。
            next_pending: list[tuple[str, BacktestSignalDict]] = []
            for signal_date, signal in pending:
                code = signal["code"]
                exec_price = self._execution_price(day_data, code)
                if exec_price is None or self._is_suspended(day_data, code):
                    next_pending.append((signal_date, signal))
                    if verbose:
                        logger.info(
                            f"  {self.current_date} {code} 停牌/无行情, "
                            f"挂单顺延至下一执行日"
                        )
                    continue
                self._execute_signal(
                    signal, day_data, verbose, signal_date=signal_date
                )
            pending = next_pending

            # 2) 策略基于当日收盘数据决策
            ctx = dict(day_data)
            # P1-8: 注入引擎当前总权益(按本日收盘估值), 取代策略侧固定兜底 100 万
            ctx["equity"] = self.get_total_equity()
            signals = strategy_func(ctx, self.positions)  # type: ignore[arg-type]

            for signal in signals:
                if execution_delay:
                    # 信号 T 收盘挂起, 下个交易日撮合
                    pending.append((self.current_date or "", signal))
                else:
                    # 旧版语义: 当日立即撮合
                    self._execute_signal(
                        signal, day_data, verbose, signal_date=self.current_date
                    )

            self.record_daily_pnl(self.current_date)

        # 回测窗口末尾残留的信号无下一交易日可撮合——按真实执行丢弃
        if pending and verbose:
            logger.info(
                f"  {self.current_date}: {len(pending)} 个信号在最后交易日生成, "
                "无下一交易日可撮合, 按未成交处理"
            )

        return self.generate_report()

    def generate_report(self) -> dict[str, Any]:
        """生成回测报告

        P0-2 Sharpe 口径修复:
          - 日收益序列由 equity_curve 相邻点计算, 保留零收益日(空仓/停牌/无波动
            都是真实风险承担样本), 不再过滤 daily_return == 0;
          - 扣减无风险利率(risk_free_rate 年化 → 日化)得到超额收益;
          - 标准差用样本标准差(N-1), 并加回测天数下限保护(≥2 天)。
        P0-1 胜率口径修复: 以 SELL 成交的 realized_pnl(净回款-含费成本) > 0 判盈,
            取代"净回款是否为正"(恒为真 → 胜率恒 100%)。
        P1-6 年化下限保护: 短窗(< 63 交易日 ≈ 1/4 年)不做年化外推,
            避免 ~10 日样本被放大 25 倍, 直接返回累计收益。
        """
        if not self.daily_pnl:
            return {"status": "error", "message": "无回测数据"}

        total_return = (
            self.equity_curve[-1]["equity"] - self.initial_capital
        ) / self.initial_capital

        # P0-2: 用 equity_curve 相邻点差分构造日收益(首点含相对初始资金的当日收益)
        points = [float(self.initial_capital)] + [
            float(p["equity"]) for p in self.equity_curve
        ]
        raw_returns = [
            points[i] / points[i - 1] - 1.0 for i in range(1, len(points))
        ]
        n_obs = len(raw_returns)

        rf_daily = (1.0 + self.risk_free_rate) ** (1.0 / 252.0) - 1.0
        excess_returns = [r - rf_daily for r in raw_returns]

        avg_daily_return = (
            sum(excess_returns) / n_obs if n_obs > 0 else 0.0
        )
        if n_obs > 1:
            variance = sum(
                (x - avg_daily_return) ** 2 for x in excess_returns
            ) / (n_obs - 1)
            std_daily_return = math.sqrt(variance)
        else:
            std_daily_return = 0.0
        sharpe_ratio = (
            avg_daily_return / std_daily_return * math.sqrt(252)
            if std_daily_return > 0
            else 0.0
        )

        max_equity = self.initial_capital
        max_drawdown = 0.0
        for point in self.equity_curve:
            max_equity = max(max_equity, point["equity"])
            drawdown = (max_equity - point["equity"]) / max_equity
            max_drawdown = max(max_drawdown, drawdown)

        # P0-1: 胜率基于 SELL 已实现盈亏
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]
        if sell_trades:
            win_count = sum(1 for t in sell_trades if t.get("realized_pnl", 0.0) > 0)
            win_rate = win_count / len(sell_trades)
        else:
            win_rate = 0.0

        total_commission = sum(
            float(t.get("commission", 0.0)) for t in self.trades
        )
        total_stamp_tax = sum(
            float(t.get("stamp_tax", 0.0)) for t in self.trades
        )
        total_transfer_fee = sum(
            float(t.get("transfer_fee", 0.0)) for t in self.trades
        )
        total_realized_pnl = sum(
            float(t.get("realized_pnl", 0.0)) for t in self.trades
        )
        total_trades = len(self.trades)
        avg_trade_amount = sum(
            float(t.get("total_cost", t.get("total_revenue", 0.0)))
            for t in self.trades
        ) / max(total_trades, 1)

        # P1-6: 短窗年化下限保护 (不足 63 交易日 ≈ 1/4 年不做年化外推)
        n_days = len(self.daily_pnl)
        min_annualize_days = 63
        annualized_note: str | None = None
        if n_days >= min_annualize_days and total_return > -1.0:
            annualized_return = (1.0 + total_return) ** (252.0 / n_days) - 1.0
        else:
            annualized_return = total_return
            annualized_note = (
                None
                if n_days >= min_annualize_days
                else (
                    f"样本仅 {n_days} 个交易日(<{min_annualize_days}), "
                    "年化外推失真, 直接返回累计收益"
                )
            )

        # 换手率(买入单边成交额 / 平均权益)
        buy_amount = sum(
            float(t["execution_price"]) * float(t["qty"])
            for t in self.trades
            if t["action"] == "BUY"
        )
        avg_equity = (
            self.initial_capital + self.equity_curve[-1]["equity"]
        ) / 2.0
        turnover = buy_amount / avg_equity if avg_equity > 0 else 0.0

        return {
            "status": "success",
            "initial_capital": self.initial_capital,
            "final_equity": self.equity_curve[-1]["equity"],
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_note": annualized_note,
            "avg_daily_return": avg_daily_return,
            "std_daily_return": std_daily_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "buy_trades": len([t for t in self.trades if t["action"] == "BUY"]),
            "sell_trades": len(sell_trades),
            "total_commission": total_commission,
            "total_stamp_tax": total_stamp_tax,
            "total_transfer_fee": total_transfer_fee,
            "total_realized_pnl": total_realized_pnl,
            "turnover": turnover,
            "avg_trade_amount": avg_trade_amount,
            "equity_curve": self.equity_curve,
            "daily_pnl": self.daily_pnl,
            "trades": self.trades,
            "positions": self.positions,
            "backtest_days": n_days,
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
        signal_thresholds: SignalThresholds | None = None,
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

    def generate_signals(
        self, day_data: dict[str, Any], positions: dict[str, PositionDict]
    ) -> list[BacktestSignalDict]:
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
                        signals.append(
                            {"code": code, "action": "BUY", "qty": qty, "price": price}
                        )

            elif signal_is_strong_sell or signal_is_medium_sell:
                if current_qty > 0:
                    qty = current_qty
                    if signal_is_medium_sell:
                        qty = qty // 2

                    if qty >= 100:
                        signals.append(
                            {"code": code, "action": "SELL", "qty": qty, "price": price}
                        )

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
    def load_from_positions_history(
        positions_history_dir: str, tickers: list[str] | None = None
    ) -> list[BacktestDayData]:
        """从positions.json历史记录加载数据"""
        data: list[BacktestDayData] = []
        files = sorted(os.listdir(positions_history_dir))
        skipped = 0
        last_error: str | None = None

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
                    if (
                        pos.get("est_price", 0)
                        and not BacktestDataLoader._warned_est_price
                    ):
                        logger.warning(
                            "回测使用 est_price(估算价格), 可能包含事后信息。"
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

            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as exc:  # P1-9: 不再静默吞掉——逐文件累计并最终告警
                skipped += 1
                last_error = f"{filename}: {exc!r}"

        if skipped:
            logger.warning(
                f"load_from_positions_history 跳过 {skipped}/{len(files)} 个损坏/不可读文件, "
                f"最近一次错误: {last_error}"
            )
        return data

    @staticmethod
    def generate_synthetic_data(
        start_date: str,
        end_date: str,
        tickers: list[str],
        base_price: float = 2.0,
        volatility: float = 0.02,
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
        prices = dict.fromkeys(tickers, base_price)

        import random

        # P2: 用局部 RNG 实例, 避免 random.seed(42) 污染进程全局随机状态
        rng = random.Random(42)

        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            date_str = current.strftime("%Y-%m-%d")

            for ticker in tickers:
                change = (rng.random() - 0.48) * 2 * volatility
                prices[ticker] *= 1 + change
                prices[ticker] = round(prices[ticker], 4)

            inflow_values = [0, 5, 10, 15, 20, 25, 30, 50, 70, 100]
            etf_signals = {}

            for ticker in tickers:
                inflow = inflow_values[rng.randint(0, len(inflow_values) - 1)] * (
                    1 if rng.random() > 0.3 else -1
                )

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

        return data  # type: ignore[return-value]

    @staticmethod
    def load_from_ohlcv(
        price_data: dict[str, pd.DataFrame],
        st_codes: set[str] | None = None,
        etf_signals_by_date: dict[str, dict[str, Any]] | None = None,
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

        return build_backtest_data_from_ohlcv(  # type: ignore[no-any-return]
            price_data=price_data,
            st_codes=st_codes,
            etf_signals_by_date=etf_signals_by_date,
        )


def run_etf_signal_backtest(
    data: list[dict], initial_capital: float = 1000000.0, **kwargs: Any
) -> dict:
    """便捷函数：运行ETF信号策略回测"""
    strategy = ETFSignalStrategy(**kwargs)
    engine = BacktestEngine(initial_capital=initial_capital)

    def strategy_func(day_data: Any, positions: Any) -> Any:
        return strategy.generate_signals(day_data, positions)

    return engine.run(data, strategy_func)  # type: ignore[arg-type]


def compare_strategies(
    data: list[dict],
    strategies: dict[str, Callable],
    initial_capital: float = 1000000.0,
) -> dict:
    """比较多个策略"""
    results = {}

    for name, strategy_func in strategies.items():
        engine = BacktestEngine(initial_capital=initial_capital)
        result = engine.run(data, strategy_func)  # type: ignore[arg-type]
        results[name] = result

    return results


if __name__ == "__main__":
    tickers = ["588080.SH", "512880.SH", "510050.SH", "512760.SH"]

    logger.info("===== 生成合成回测数据 =====")
    data = BacktestDataLoader.generate_synthetic_data(
        "2024-01-01", "2025-12-31", tickers
    )
    logger.info(f"生成 {len(data)} 个交易日数据")

    logger.info("===== 运行ETF信号策略回测 =====")
    result = run_etf_signal_backtest(data, initial_capital=1000000.0)  # type: ignore[arg-type]

    logger.info(f"初始资金: ¥{result['initial_capital']:,.0f}")
    logger.info(f"最终权益: ¥{result['final_equity']:,.0f}")
    logger.info(f"总收益率: {result['total_return']:.2%}")
    logger.info(f"年化收益率: {result['annualized_return']:.2%}")
    logger.info(f"夏普比率: {result['sharpe_ratio']:.2f}")
    logger.info(f"最大回撤: {result['max_drawdown']:.2%}")
    logger.info(f"胜率: {result['win_rate']:.2%}")
    logger.info(f"总交易次数: {result['total_trades']}")
    logger.info(f"总手续费: ¥{result['total_commission']:,.2f}")
