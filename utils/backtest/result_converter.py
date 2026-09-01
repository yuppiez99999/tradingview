"""G15 事件驱动回测 — 结果转换器模块。

职责:
    将 EventDrivenEngine 的 EngineSummary 转换为与向量化回测同构的 BacktestResult,
    确保两个引擎的指标口径完全一致,便于偏差验证 (验收标准: 偏差 < 5%)。

指标公式与 hedge_rebalance_backtest.HedgeRebalanceBacktest._metrics 完全对齐:
    - daily_returns: [0.0] + 逐日 (eq[i] - eq[i-1]) / eq[i-1]
    - total_return: (eq[-1] - eq[0]) / eq[0]
    - annual_return: (1 + total_return) ** (1 / max(n_years, 0.5)) - 1
    - annual_volatility: std(daily_returns) * sqrt(252)
    - sharpe_ratio: (annual_return - RISK_FREE_RATE) / max(annual_volatility, 0.001)
    - max_drawdown: abs(min((eq - peak) / peak))
    - calmar_ratio: annual_return / max(max_drawdown, 0.001)
    - win_rate: sum(rets > 0) / max(len(rets), 1)
    - annual_turnover: sum(turnover_daily) * (252 / n_points)
    - window_turnover: 最近 WINDOW(20) 日换手率均值

设计原则 (AGENTS.md):
    - 单一职责: 只做转换,不重新计算引擎状态
    - DRY: 复用向量化回测的常量 (RISK_FREE_RATE, 252 交易日)
    - 不可变性: convert() 返回新 BacktestResult,不修改 EngineSummary
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from utils.backtest.event_driven_engine import EngineSummary
from utils.hedge_rebalance_backtest import BacktestResult

# 与向量化回测对齐的常量 (hedge_rebalance_backtest.py L118-L123)
RISK_FREE_RATE = 0.03
TRADING_DAYS_PER_YEAR = 252
TURNOVER_WINDOW = 20
DEFAULT_COMMISSION_RATE = 0.0003


@dataclass
class ConversionConfig:
    """转换器配置。

    Attributes:
        risk_free_rate: 无风险利率 (与向量化回测 RISK_FREE_RATE 对齐)
        trading_days_per_year: 年化交易日数 (252)
        turnover_window: 换手率统计窗口
        default_start_date: 未提供 dates 时的默认起始日期
        commission_rate: 手续费率 (与 EventDrivenEngine 默认对齐)
    """

    risk_free_rate: float = RISK_FREE_RATE
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR
    turnover_window: int = TURNOVER_WINDOW
    default_start_date: str = "2021-01-04"
    commission_rate: float = DEFAULT_COMMISSION_RATE


class ResultConverter:
    """EngineSummary → BacktestResult 转换器。

    使用示例:
        converter = ResultConverter()
        result = converter.convert(summary, name="event_driven_S1")
        # result.total_return, result.sharpe_ratio, result.annual_turnover 等
        # 可直接与向量化回测对比

    公式对齐:
        所有指标计算与 HedgeRebalanceBacktest._metrics 完全一致,
        确保两个引擎在相同输入下偏差 < 5% (验收标准)。
    """

    def __init__(self, config: ConversionConfig | None = None) -> None:
        self._config = config or ConversionConfig()

    def convert(
        self,
        summary: EngineSummary,
        dates: list[pd.Timestamp] | None = None,
        name: str = "event_driven",
        csi300_returns: list[float] | None = None,
    ) -> BacktestResult:
        """转换 EngineSummary 为 BacktestResult。

        Args:
            summary: 引擎产出的汇总结果
            dates: 日期列表 (长度需 = len(equity_curve)); None 时自动生成
            name: 策略名称
            csi300_returns: 每日 CSI300 收益率序列 (长度 = len(equity_curve)-1),
                用于 yearly_stats 计算市场类型; None 时跳过 CSI300 对比

        Returns:
            填充完整指标的 BacktestResult (含换手率/逐年统计)
        """
        equity_curve = list(summary.equity_curve)
        n_points = len(equity_curve)

        # 1. 日期处理
        if dates is None:
            dates = self._generate_dates(n_points)
        elif len(dates) != n_points:
            raise ValueError(
                f"dates 长度 ({len(dates)}) 与 equity_curve 长度 ({n_points}) 不匹配"
            )

        # 2. 日收益率
        daily_returns = self._calc_daily_returns(equity_curve)

        # 3. 成本拆分 (从 trade_records 提取, 按 event_index 分配到日)
        transaction_costs = self._extract_transaction_costs(summary, n_points)
        hedge_costs = self._extract_hedge_costs(summary, n_points)

        # 4. 换手率 (从 trade_records 提取, 按 event_index 分配到日)
        turnover_daily = self._extract_turnover_daily(summary, n_points, equity_curve)

        # 5. 构建 BacktestResult 骨架
        result = BacktestResult(
            name=name,
            equity_curve=equity_curve,
            dates=dates,
            daily_returns=daily_returns,
            trade_count=self._count_fills(summary),
            hedge_costs=hedge_costs,
            transaction_costs=transaction_costs,
            n_days=n_points - 1,
            turnover_window=self._config.turnover_window,
            turnover_daily=turnover_daily,
        )

        # 6. 计算性能指标 (与向量化回测 _metrics 公式完全一致)
        self._calc_metrics(result)

        # 7. 逐年统计 (可选 CSI300 对比)
        self._calc_yearly_stats(result, csi300_returns)

        return result

    # ============================================================
    # 内部: 指标计算 (与 HedgeRebalanceBacktest._metrics 对齐)
    # ============================================================

    def _calc_daily_returns(self, equity_curve: list[float]) -> list[float]:
        """计算日收益率 — 与 _rets() 公式一致。

        rets[0] = 0.0
        rets[i] = (eq[i] - eq[i-1]) / max(1, eq[i-1]) if eq[i-1] > 0 else 0
        """
        rets: list[float] = [0.0]
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            if prev > 0:
                rets.append((equity_curve[i] - prev) / max(1, prev))
            else:
                rets.append(0.0)
        return rets

    def _calc_metrics(self, result: BacktestResult) -> None:
        """填充性能指标 — 与 HedgeRebalanceBacktest._metrics 完全一致。

        计算:
            total_return, annual_return, annual_volatility, sharpe_ratio,
            max_drawdown, calmar_ratio, win_rate, total_transaction_cost,
            annual_turnover, window_turnover
        """
        eq = np.array(result.equity_curve)
        rets = np.array(result.daily_returns)

        if len(eq) < 2:
            # 退化为零
            result.total_return = 0.0
            result.annual_return = 0.0
            result.annual_volatility = 0.0
            result.sharpe_ratio = 0.0
            result.max_drawdown = 0.0
            result.calmar_ratio = 0.0
            result.win_rate = 0.0
            result.total_transaction_cost = sum(result.transaction_costs)
            result.total_hedge_cost = sum(result.hedge_costs)
            result.annual_turnover = 0.0
            result.window_turnover = 0.0
            return

        # 总收益率
        result.total_return = float((eq[-1] - eq[0]) / eq[0])

        # 年化收益率
        n_years = result.n_days / self._config.trading_days_per_year
        result.annual_return = float(
            (1 + result.total_return) ** (1 / max(n_years, 0.5)) - 1
        )

        # 年化波动率
        result.annual_volatility = float(np.std(rets) * np.sqrt(TRADING_DAYS_PER_YEAR))

        # 夏普比率
        result.sharpe_ratio = float(
            (result.annual_return - self._config.risk_free_rate)
            / max(result.annual_volatility, 0.001)
        )

        # 最大回撤
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        result.max_drawdown = float(abs(np.min(dd)))

        # Calmar 比率
        result.calmar_ratio = float(
            result.annual_return / max(result.max_drawdown, 0.001)
        )

        # 胜率
        result.win_rate = float(np.sum(rets > 0) / max(len(rets), 1))

        # 成本汇总
        result.total_transaction_cost = float(sum(result.transaction_costs))
        result.total_hedge_cost = float(sum(result.hedge_costs))

        # 换手率统计 (与 _metrics 对齐)
        if result.turnover_daily:
            td = np.array(result.turnover_daily)
            result.annual_turnover = float(
                np.sum(td) * (self._config.trading_days_per_year / max(len(td), 1))
            )
            w = self._config.turnover_window
            if len(td) > w:
                result.window_turnover = float(np.sum(td[-w:]) / max(1, len(td[-w:])))
            else:
                result.window_turnover = float(np.mean(td)) if len(td) > 0 else 0.0
        else:
            result.annual_turnover = 0.0
            result.window_turnover = 0.0

    def _calc_yearly_stats(
        self,
        result: BacktestResult,
        csi300_returns: list[float] | None = None,
    ) -> None:
        """逐年收益/波动/回撤/CSI300 对比/换手率统计。

        与 HedgeRebalanceBacktest._metrics yearly_stats 结构对齐,
        缺少 CSI300 数据时 csi300_return=0, market_type="震荡市"。

        Args:
            result: 已填充权益曲线/日期/日收益率的 BacktestResult
            csi300_returns: 可选 CSI300 日收益率序列
        """
        eq = np.array(result.equity_curve)
        rets = np.array(result.daily_returns)
        dates = result.dates

        # 按年份分组
        year_groups: dict[int, list[int]] = {}
        for idx, d in enumerate(dates):
            year = d.year
            if year not in year_groups:
                year_groups[year] = []
            year_groups[year].append(idx)

        result.yearly_stats = []
        for year in sorted(year_groups.keys()):
            idxs = year_groups[year]
            if len(idxs) < 10:
                continue

            # 年度收益率
            yr = float((eq[idxs[-1]] - eq[idxs[0]]) / eq[idxs[0]])

            # 年度波动率
            yv = float(np.std(rets[idxs]) * np.sqrt(TRADING_DAYS_PER_YEAR))

            # 年度最大回撤
            yeq = eq[idxs[0] : idxs[-1] + 1]
            ypeak = np.maximum.accumulate(yeq)
            ydd = float(abs(np.min((yeq - ypeak) / ypeak)))

            # CSI300 对比 (可选)
            csi_yr = 0.0
            mt = "震荡市"
            if csi300_returns is not None:
                csi_vals = np.array(
                    [csi300_returns[j] for j in idxs if j < len(csi300_returns)]
                )
                if len(csi_vals) > 0:
                    csi_yr = float(np.prod(1 + csi_vals) - 1)
                    mt = (
                        "牛市"
                        if csi_yr > 0.15
                        else ("熊市" if csi_yr < -0.05 else "震荡市")
                    )

            # 年度换手率 (该年所有日换手率的均值)
            year_turnover = 0.0
            if result.turnover_daily:
                year_td = np.array([result.turnover_daily[j] for j in idxs])
                year_turnover = float(np.mean(year_td))

            result.yearly_stats.append(
                {
                    "year": year,
                    "return": yr,
                    "volatility": yv,
                    "max_drawdown": ydd,
                    "csi300_return": csi_yr,
                    "market_type": mt,
                    "turnover": year_turnover,
                }
            )

    # ============================================================
    # 内部: 辅助方法
    # ============================================================

    def _generate_dates(self, n_points: int) -> list[pd.Timestamp]:
        """生成合成日期序列 (交易日,跳过周末)。

        Args:
            n_points: 需要的日期数 (与 equity_curve 长度一致)

        Returns:
            pd.Timestamp 列表, 从 default_start_date 开始的工作日
        """
        start = pd.Timestamp(self._config.default_start_date)
        # 用 BDay 生成工作日序列 (近似交易日, 不考虑节假日)
        return list(pd.bdate_range(start=start, periods=n_points))

    def _count_fills(self, summary: EngineSummary) -> int:
        """统计成交订单数 (ALL_TRADED 状态)。"""
        return sum(1 for r in summary.trade_records if r.get("status") == "ALL_TRADED")

    def _extract_transaction_costs(
        self, summary: EngineSummary, n_points: int
    ) -> list[float]:
        """从 trade_records 提取每日交易成本 (按 event_index 分配)。

        每笔 ALL_TRADED 成交的手续费 = price * volume * commission_rate,
        按 event_index 分配到对应日期; 无 event_index 的旧记录归入第 0 日。
        REJECTED 订单不计入。
        """
        costs = [0.0] * n_points
        rate = self._config.commission_rate
        for trade in summary.trade_records:
            if trade.get("status") != "ALL_TRADED":
                continue
            price = trade.get("price", 0.0)
            volume = trade.get("volume", 0.0)
            commission = price * volume * rate
            event_idx = trade.get("event_index", 0)
            if 0 <= event_idx < n_points:
                costs[event_idx] += commission
            else:
                costs[0] += commission
        return costs

    def _extract_hedge_costs(
        self, summary: EngineSummary, n_points: int
    ) -> list[float]:
        """提取每日对冲成本 (当前引擎未单独跟踪,返回零列表)。

        Day 6+ 扩展: 引擎区分 hedge/transaction 成本后填充。
        """
        return [0.0] * n_points

    def _extract_turnover_daily(
        self,
        summary: EngineSummary,
        n_points: int,
        equity_curve: list[float],
    ) -> list[float]:
        """从 trade_records 提取每日换手率 (按 event_index 分配)。

        换手率公式 (与向量化回测 _compute_turnover 对齐):
            turnover[i] = sum(price * volume) / equity[event_index]
            仅统计 ALL_TRADED 成交, 同日多笔聚合

        无 event_index 的旧记录归入第 0 日。
        REJECTED 订单不计入。
        """
        turnover = [0.0] * n_points
        for trade in summary.trade_records:
            if trade.get("status") != "ALL_TRADED":
                continue
            price = trade.get("price", 0.0)
            volume = trade.get("volume", 0.0)
            traded_value = price * volume
            if traded_value <= 0:
                continue
            event_idx = trade.get("event_index", 0)
            if 0 <= event_idx < n_points:
                eq_val = equity_curve[event_idx]
                if eq_val > 0:
                    turnover[event_idx] += traded_value / eq_val
            else:
                eq_val = equity_curve[0]
                if eq_val > 0:
                    turnover[0] += traded_value / eq_val
        return turnover


__all__ = [
    "ConversionConfig",
    "ResultConverter",
]
