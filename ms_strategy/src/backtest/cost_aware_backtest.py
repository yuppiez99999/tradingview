"""
成本感知回测包装器 v1.0 — P0 修复

将 v7.5 CostModel 集成到回测流程中:
1. 佣金 (万2.5)
2. 印花税 (卖出 0.1%)
3. 过户费 (万0.2)
4. Almgren-Chriss 市场冲击 (平方根模型)
5. 期货/期权成本

使用方式:
    from src.backtest.cost_aware_backtest import CostAwareBacktest
    bt = CostAwareBacktest(initial_capital=5_000_000)
    result = bt.run_strategy(prices, signals, target_weights)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger('cost_aware_backtest')

# 尝试导入 v7.5 CostModel
try:
    from ..backtest.cost_model import CostConfig, CostModel
    _COST_MODEL_AVAILABLE = True
except ImportError:
    try:
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        from src.backtest.cost_model import CostConfig, CostModel
        _COST_MODEL_AVAILABLE = True
    except ImportError:
        _COST_MODEL_AVAILABLE = False
        logger.warning("CostModel 不可用, 使用简化版成本计算")


@dataclass
class TradeRecord:
    """交易记录"""
    date: pd.Timestamp
    code: str
    side: str          # BUY / SELL
    qty: int
    price: float
    notional: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    market_impact: float
    total_cost: float


@dataclass
class BacktestResult:
    """回测结果"""
    # 收益
    total_return: float = 0.0
    annual_return: float = 0.0
    excess_return: float = 0.0
    # 风险
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    # 成本
    total_cost: float = 0.0
    total_commission: float = 0.0
    total_stamp_duty: float = 0.0
    total_market_impact: float = 0.0
    cost_as_return_pct: float = 0.0  # 总成本占初始资本比例
    n_trades: int = 0
    avg_cost_per_trade: float = 0.0
    # 换手率
    turnover: float = 0.0
    # 详细记录
    equity_curve: pd.Series = None
    trades: list[TradeRecord] = field(default_factory=list)
    daily_costs: pd.Series = None


class CostAwareBacktest:
    """成本感知回测引擎

    在传统回测基础上, 精确计算每笔交易的成本:
    - 佣金: 万2.5 (买卖都收)
    - 印花税: 千1 (仅卖出)
    - 过户费: 万0.2 (买卖都收)
    - 市场冲击: Almgren-Chriss 平方根模型
        Impact = σ × η × √(Q/V)
    """

    def __init__(self,
                 initial_capital: float = 5_000_000,
                 commission_rate: float = 0.00025,
                 stamp_duty_rate: float = 0.001,
                 transfer_fee_rate: float = 0.00002,
                 slippage_coef: float = 0.142,
                 min_cost_bps: float = 3.0):
        """
        Args:
            initial_capital: 初始资金
            commission_rate: 佣金费率 (万2.5 = 0.00025)
            stamp_duty_rate: 印花税率 (千1 = 0.001, 仅卖出)
            transfer_fee_rate: 过户费率 (万0.2 = 0.00002)
            slippage_coef: Almgren-Chriss 系数
            min_cost_bps: 最低成本 (基点), 兜底防止极低成本
        """
        self.capital = initial_capital
        self.commission_rate = commission_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.transfer_fee_rate = transfer_fee_rate
        self.slippage_coef = slippage_coef
        self.min_cost_bps = min_cost_bps / 10000

        # 使用 v7.5 CostModel 如果可用
        if _COST_MODEL_AVAILABLE:
            config = CostConfig(
                commission_stock=commission_rate,
                stamp_duty=stamp_duty_rate,
                transfer_fee=transfer_fee_rate,
                slippage_coef=slippage_coef,
            )
            self.cost_model = CostModel(config)
        else:
            self.cost_model = None

        logger.info(
            f"CostAwareBacktest 初始化: 资本={initial_capital:,.0f}, "
            f"佣金={commission_rate:.5f}, 印花税={stamp_duty_rate:.4f}, "
            f"冲击系数={slippage_coef}"
        )

    def compute_trade_cost(self,
                           notional: float,
                           side: str,
                           qty: int = 0,
                           daily_volume: int = 0,
                           volatility: float = 0.02,
                           price: float = 0.0) -> dict[str, float]:
        """计算单笔交易的全部成本

        Args:
            notional: 交易金额 (qty * price)
            side: 'BUY' 或 'SELL'
            qty: 委托数量
            daily_volume: 日成交量 (用于市场冲击)
            volatility: 日波动率 (用于市场冲击)
            price: 当前价格

        Returns:
            {commission, stamp_duty, transfer_fee, market_impact, total_cost}
        """
        # 佣金 (买卖都收)
        commission = notional * self.commission_rate

        # 印花税 (仅卖出)
        stamp_duty = notional * self.stamp_duty_rate if side == 'SELL' else 0.0

        # 过户费 (买卖都收)
        transfer_fee = notional * self.transfer_fee_rate

        # 市场冲击 (Almgren-Chriss)
        market_impact = 0.0
        if self.cost_model and qty > 0 and daily_volume > 0 and price > 0:
            market_impact = self.cost_model.market_impact(
                qty=qty, daily_volume=daily_volume,
                volatility=volatility, price=price
            )
        elif qty > 0 and daily_volume > 0:
            # 简化版: σ × η × √(Q/V) × price × qty
            # BT-2: participation 钳制到 (0,1], 防止大单冲击成本爆炸
            participation = min(qty / daily_volume, 1.0)
            impact_bps = self.slippage_coef * volatility * np.sqrt(participation)
            market_impact = impact_bps * notional

        # 最低成本兜底
        total = commission + stamp_duty + transfer_fee + market_impact
        min_cost = notional * self.min_cost_bps
        if total < min_cost:
            total = min_cost

        return {
            'commission': commission,
            'stamp_duty': stamp_duty,
            'transfer_fee': transfer_fee,
            'market_impact': market_impact,
            'total_cost': total,
        }

    def run_strategy(self,
                     prices: pd.DataFrame,
                     target_weights: pd.DataFrame,
                     rebalance_threshold: float = 0.05,
                     daily_volumes: pd.DataFrame | None = None) -> BacktestResult:
        """运行成本感知回测

        Args:
            prices: 日期 x 标的 的价格 DataFrame
            target_weights: 日期 x 标的 的目标权重 DataFrame
            rebalance_threshold: 权重偏离阈值, 超过则触发再平衡
            daily_volumes: 日期 x 标的 的日成交量 (用于市场冲击)

        Returns:
            BacktestResult
        """
        dates = prices.index
        codes = prices.columns
        n_days = len(dates)

        # 初始化
        current_weights = pd.Series(0.0, index=codes)
        current_cash = 1.0  # 现金比例
        equity = self.capital
        equity_curve = []
        trades = []
        daily_costs_list = []

        logger.info(f"开始回测: {n_days} 天, {len(codes)} 标的, "
                    f"初始资本 {self.capital:,.0f}")

        for i, date in enumerate(dates):
            day_prices = prices.loc[date]
            day_volumes = (daily_volumes.loc[date] if daily_volumes is not None
                          else pd.Series(1e6, index=codes))

            # 目标权重
            if date in target_weights.index:
                tgt = target_weights.loc[date]
            else:
                # 用最近的目标权重
                tgt = target_weights.loc[:date].iloc[-1] if len(target_weights.loc[:date]) > 0 else current_weights

            # 计算权重偏离
            if i > 0:
                # 价格变动导致的权重漂移
                prev_prices = prices.iloc[i-1]
                if current_weights.sum() > 0:
                    new_weights = current_weights * (day_prices / prev_prices)
                    total = new_weights.sum() + current_cash
                    current_weights = new_weights / total
                    current_cash = current_cash / total

            # 检查是否需要再平衡
            deviation = (tgt - current_weights).abs().max()
            need_rebalance = deviation > rebalance_threshold or i == 0

            daily_cost = 0.0

            if need_rebalance:
                for code in codes:
                    target_w = tgt.get(code, 0.0)
                    current_w = current_weights.get(code, 0.0)
                    delta_w = target_w - current_w

                    if abs(delta_w) < 0.001 or delta_w != delta_w:  # 忽略微小调整和 NaN
                        continue

                    side = 'BUY' if delta_w > 0 else 'SELL'
                    notional = abs(delta_w) * equity
                    if notional != notional or notional <= 0:  # NaN 检查
                        continue

                    price = float(day_prices.get(code, 0))
                    if not (price > 0 and price == price):  # NaN/零/负 价格
                        continue

                    try:
                        qty = int(notional / price / 100) * 100  # 整手
                    except (ValueError, ZeroDivisionError):
                        continue
                    if qty == 0:
                        continue

                    actual_notional = qty * price
                    vol = day_volumes.get(code, 1e6)
                    daily_ret = prices[code].pct_change()
                    vol_30d = daily_ret.iloc[max(0, i-30):i].std() if i > 30 else 0.02
                    vol_30d = vol_30d if not np.isnan(vol_30d) else 0.02

                    costs = self.compute_trade_cost(
                        notional=actual_notional,
                        side=side,
                        qty=qty,
                        daily_volume=int(vol),
                        volatility=vol_30d,
                        price=price,
                    )

                    daily_cost += costs['total_cost']

                    trades.append(TradeRecord(
                        date=date, code=code, side=side,
                        qty=qty, price=price, notional=actual_notional,
                        commission=costs['commission'],
                        stamp_duty=costs['stamp_duty'],
                        transfer_fee=costs['transfer_fee'],
                        market_impact=costs['market_impact'],
                        total_cost=costs['total_cost'],
                    ))

                    current_weights[code] = target_w

                current_cash = 1.0 - current_weights.sum()

            # 计算当日收益
            if i > 0:
                daily_ret = (day_prices / prices.iloc[i-1] - 1).fillna(0)
                portfolio_ret = (current_weights * daily_ret).sum()
                equity *= (1 + portfolio_ret)
                # 扣除成本
                equity -= daily_cost

            equity_curve.append(equity)
            daily_costs_list.append(daily_cost)

        # 计算回测指标
        equity_series = pd.Series(equity_curve, index=dates)
        daily_costs_series = pd.Series(daily_costs_list, index=dates)

        total_return = (equity_series.iloc[-1] / self.capital) - 1
        n_years = n_days / 252
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

        daily_returns = equity_series.pct_change().dropna()
        volatility = daily_returns.std() * np.sqrt(252)
        sharpe = (daily_returns.mean() * 252 - 0.02) / volatility if volatility > 0 else 0

        # 最大回撤
        rolling_max = equity_series.expanding().max()
        drawdown = (equity_series - rolling_max) / rolling_max
        max_dd = drawdown.min()

        # Sortino
        downside_returns = daily_returns[daily_returns < 0]
        downside_std = downside_returns.std() if len(downside_returns) > 0 else np.nan
        sortino = (daily_returns.mean() * 252 - 0.02) / (downside_std * np.sqrt(252)) if np.isfinite(downside_std) and downside_std > 1e-6 else 0.0  # noqa: E501

        # Calmar
        calmar = annual_return / abs(max_dd) if max_dd < 0 else 0

        # 成本统计
        total_cost = daily_costs_series.sum()
        total_commission = sum(t.commission for t in trades)
        total_stamp = sum(t.stamp_duty for t in trades)
        total_impact = sum(t.market_impact for t in trades)
        n_trades = len(trades)
        avg_cost = total_cost / n_trades if n_trades > 0 else 0

        # 换手率
        total_notional = sum(t.notional for t in trades)
        turnover = total_notional / (self.capital * n_years) if n_years > 0 else 0

        result = BacktestResult(
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_dd,
            volatility=volatility,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            total_cost=total_cost,
            total_commission=total_commission,
            total_stamp_duty=total_stamp,
            total_market_impact=total_impact,
            cost_as_return_pct=total_cost / self.capital,
            n_trades=n_trades,
            avg_cost_per_trade=avg_cost,
            turnover=turnover,
            equity_curve=equity_series,
            trades=trades,
            daily_costs=daily_costs_series,
        )

        logger.info(
            f"回测完成: 年化收益 {annual_return:.2%}, 夏普 {sharpe:.3f}, "
            f"最大回撤 {max_dd:.2%}, 总成本 {total_cost:,.0f} ({total_cost/self.capital:.2%}%), "
            f"交易 {n_trades} 笔, 换手 {turnover:.1f}x"
        )

        return result

    def compare_with_no_cost(self, result: BacktestResult) -> dict:
        """对比有无成本的差异"""
        return {
            'total_return_with_cost': result.total_return,
            'total_return_without_cost': result.total_return + result.cost_as_return_pct,
            'cost_drag': result.cost_as_return_pct,
            'cost_breakdown': {
                'commission': result.total_commission,
                'stamp_duty': result.total_stamp_duty,
                'market_impact': result.total_market_impact,
            },
            'sharpe_with_cost': result.sharpe_ratio,
            'turnover': result.turnover,
            'n_trades': result.n_trades,
            'avg_cost_bps': result.avg_cost_per_trade / (self.capital / result.n_trades) * 10000 if result.n_trades > 0 else 0,  # noqa: E501
        }
