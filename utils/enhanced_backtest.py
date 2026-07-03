# -*- coding: utf-8 -*-
"""
增强版回测引擎 — 波动率自适应权重 + 回撤分级控制 + 风险平价

来源：整合自 E:\各种PY程序\backtest_engine.py + 11_量化策略\enhanced_backtest_engine.py

核心改进（相对基础回测）：
1. 波动率倒数加权（低波动资产获得更高权重）
2. 回撤分级控制（6档：2%/4%/6%/8%/10%+）
3. 自适应再平衡（时间间隔 + 权重偏差双触发）
4. 交易成本建模（佣金+滑点）
5. 风险平价权重（等风险贡献）

使用方式：
  from utils.enhanced_backtest import EnhancedBacktestEngine
  
  engine = EnhancedBacktestEngine(portfolio_config, initial_capital=1_000_000)
  result = engine.run(klines_data)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger('backtest')


class PortfolioState:
    """组合状态管理"""

    def __init__(self, assets: List[str], initial_capital: float):
        self.cash = initial_capital
        self.initial_capital = initial_capital
        self.positions = {code: {'shares': 0, 'avg_cost': 0.0} for code in assets}
        self.equity_curve = []
        self.trades = []

    def total_value(self, prices: Dict[str, float]) -> float:
        """计算组合总市值"""
        total = self.cash
        for code, pos in self.positions.items():
            if pos['shares'] > 0 and code in prices:
                total += pos['shares'] * prices[code]
        return total

    def current_weights(self, prices: Dict[str, float]) -> Dict[str, float]:
        """计算当前权重"""
        tv = self.total_value(prices)
        if tv <= 0:
            return {code: 0.0 for code in self.positions}
        return {
            code: (self.positions[code]['shares'] * prices[code] / tv
                   if self.positions[code]['shares'] > 0 and code in prices else 0.0)
            for code in self.positions
        }

    def execute_trade(self, code: str, side: str, shares: int, price: float,
                      cost_rate: float = 0.0008) -> Tuple[bool, float]:
        """
        执行交易并计算成本。

        Args:
            cost_rate: 总成本率（佣金0.03% + 滑点0.03% + 冲击0.02%）

        Returns:
            (success, trade_cost)
        """
        if shares <= 0:
            return False, 0.0

        if side == 'buy':
            gross = shares * price
            cost = gross * cost_rate
            total = gross + cost

            if total <= self.cash:
                old_cost = self.positions[code]['shares'] * self.positions[code]['avg_cost']
                new_shares = self.positions[code]['shares'] + shares
                self.positions[code]['shares'] = new_shares
                self.positions[code]['avg_cost'] = (
                    (old_cost + gross) / new_shares if new_shares > 0 else 0
                )
                self.cash -= total
                return True, cost

        elif side == 'sell':
            if self.positions[code]['shares'] >= shares:
                gross = shares * price
                cost = gross * cost_rate
                net = gross - cost
                self.positions[code]['shares'] -= shares
                self.cash += net
                return True, cost

        return False, 0.0


class EnhancedBacktestEngine:
    """增强版回测引擎"""

    def __init__(self,
                 portfolio_config: Dict,
                 initial_capital: float = 1_000_000,
                 rebalance_interval: int = 15,
                 rebalance_threshold: float = 0.05,
                 use_risk_parity: bool = True,
                 use_dynamic_weights: bool = True):
        """
        Args:
            portfolio_config: 组合配置 {'assets': [{'code': str, 'target_weight': float, ...}]}
            initial_capital: 初始资金
            rebalance_interval: 最小再平衡间隔（交易日）
            rebalance_threshold: 权重偏差阈值（触发再平衡）
            use_risk_parity: 是否使用风险平价
            use_dynamic_weights: 是否使用波动率自适应权重
        """
        self.config = portfolio_config
        self.assets = [a['code'] for a in portfolio_config.get('assets', [])]
        self.initial_capital = initial_capital
        self.rebalance_interval = rebalance_interval
        self.rebalance_threshold = rebalance_threshold
        self.use_risk_parity = use_risk_parity
        self.use_dynamic_weights = use_dynamic_weights

        self.portfolio = PortfolioState(self.assets, initial_capital)
        self.peak_value = initial_capital

    def _get_common_dates(self, klines: Dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
        """获取公共交易日（至少50%资产有数据）"""
        date_counts = {}
        for df in klines.values():
            if df is not None and not df.empty:
                for d in df.index:
                    date_counts[d] = date_counts.get(d, 0) + 1

        min_assets = max(1, int(len(klines) * 0.5))
        valid = sorted([d for d, cnt in date_counts.items() if cnt >= min_assets])
        return pd.DatetimeIndex(valid)

    def _get_day_prices(self, klines: Dict[str, pd.DataFrame],
                         date) -> Dict[str, float]:
        """获取某日价格（含前值填充）"""
        prices = {}
        for code, df in klines.items():
            if df is None or df.empty:
                continue
            if date in df.index:
                prices[code] = float(df.loc[date, 'close'])
            else:
                before = df.index[df.index < date]
                if len(before) > 0:
                    prices[code] = float(df.loc[before[-1], 'close'])
        return prices

    def _check_drawdown(self, total_value: float) -> float:
        """
        回撤分级控制。

        Returns:
            position_scale: 头寸缩放因子 [0.4, 1.0]
        """
        if total_value > self.peak_value:
            self.peak_value = total_value
            return 1.0

        dd = (self.peak_value - total_value) / self.peak_value

        if dd >= 0.10:
            return 0.40   # 严重回撤：持仓缩减60%
        elif dd >= 0.08:
            return 0.55
        elif dd >= 0.06:
            return 0.70
        elif dd >= 0.04:
            return 0.85
        elif dd >= 0.02:
            return 0.93
        return 1.0

    def _calculate_volatility(self, klines: Dict[str, pd.DataFrame],
                               code: str, lookback: int = 20) -> float:
        """计算最近N日年化波动率"""
        if code not in klines or klines[code].empty:
            return 1.0

        df = klines[code]
        n = min(lookback, len(df))
        prices = df['close'].tail(n).values
        if len(prices) < 2:
            return 1.0

        returns = np.diff(prices) / prices[:-1]
        return max(0.001, float(np.std(returns) * np.sqrt(252)))

    def _get_base_weights(self) -> Dict[str, float]:
        """获取基础目标权重"""
        return {a['code']: a.get('target_weight', 0) for a in self.config.get('assets', [])}

    def _get_risk_parity_weights(self, klines: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """
        风险平价权重：每个资产贡献等量风险。

        简化版：权重 = 1/波动率 / sum(1/波动率)
        """
        vols = {}
        for code in self.assets:
            if code in klines and not klines[code].empty:
                vols[code] = self._calculate_volatility(klines, code, lookback=60)

        if not vols:
            return self._get_base_weights()

        inv_vols = {c: 1.0 / max(v, 0.001) for c, v in vols.items()}
        total = sum(inv_vols.values())

        weights = {}
        for code in self.assets:
            if code in inv_vols:
                weights[code] = inv_vols[code] / total
            else:
                weights[code] = 0.0

        return weights

    def _get_dynamic_weights(self, klines: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """
        动态权重：60%基础权重 + 40%波动率倒数加权。

        低波动资产获得更高权重，实现波动率目标管理。
        """
        base = self._get_base_weights()
        vols = {}
        for code in base:
            if base[code] > 0 and code in klines and not klines[code].empty:
                vols[code] = self._calculate_volatility(klines, code, lookback=20)

        if not vols:
            return base

        inv_vols = {c: 1.0 / (v + 0.0001) for c, v in vols.items()}
        inv_sum = sum(inv_vols.values())

        dynamic = {}
        total = 0.0
        for code in base:
            if base[code] > 0:
                if code in vols:
                    dynamic[code] = base[code] * 0.6 + (inv_vols[code] / inv_sum) * 0.4
                else:
                    dynamic[code] = base[code] * 0.3  # 无数据资产权重降低
            else:
                dynamic[code] = 0.0
            total += dynamic[code]

        if total > 0:
            dynamic = {c: w / total for c, w in dynamic.items()}

        return dynamic

    def _rebalance(self, target_weights: Dict[str, float],
                   prices: Dict[str, float], date, scale: float = 1.0):
        """执行再平衡交易"""
        current = self.portfolio.current_weights(prices)
        tv = self.portfolio.total_value(prices)

        for code, target_w in target_weights.items():
            if target_w <= 0 or code not in prices:
                continue

            adjusted = target_w * scale
            diff = adjusted - current.get(code, 0)

            if abs(diff) <= self.rebalance_threshold * 0.5:
                continue

            price = prices[code]
            amount = abs(diff) * tv
            shares = int(amount / price / 100) * 100

            if shares > 0:
                side = 'buy' if diff > 0 else 'sell'
                success, cost = self.portfolio.execute_trade(code, side, shares, price)
                if success:
                    self.portfolio.trades.append({
                        'date': str(date), 'code': code, 'side': side,
                        'shares': shares, 'price': price,
                        'amount': shares * price, 'cost': cost,
                    })

    def run(self, klines: Dict[str, pd.DataFrame]) -> Dict:
        """
        运行回测。

        Returns:
            {
                'initial_capital': float,
                'final_capital': float,
                'total_return': float,
                'annual_return': float,
                'annual_volatility': float,
                'max_drawdown': float,
                'sharpe_ratio': float,
                'num_trades': int,
                'equity_curve': [{date, value}, ...],
                'trades': [...],
            }
        """
        dates = self._get_common_dates(klines)
        if len(dates) < 20:
            return {'error': '数据不足（需要至少20个交易日）'}

        logger.info(f"开始回测：{len(dates)} 个交易日，初始资金 {self.initial_capital:,.0f}")

        last_rebalance = None

        for i, date in enumerate(dates):
            prices = self._get_day_prices(klines, date)
            if not prices:
                continue

            tv = self.portfolio.total_value(prices)
            scale = self._check_drawdown(tv)

            # 判断是否需要再平衡
            should_rebalance = False
            if last_rebalance is None:
                should_rebalance = True
            elif isinstance(last_rebalance, pd.Timestamp) and (date - last_rebalance).days >= self.rebalance_interval:
                should_rebalance = True
            elif self.use_dynamic_weights:
                current = self.portfolio.current_weights(prices)
                dynamic = self._get_dynamic_weights(klines)
                max_drift = max(
                    abs(current.get(c, 0) - dynamic.get(c, 0))
                    for c in dynamic
                )
                if max_drift > self.rebalance_threshold:
                    should_rebalance = True

            if should_rebalance:
                if self.use_risk_parity:
                    target = self._get_risk_parity_weights(klines)
                elif self.use_dynamic_weights:
                    target = self._get_dynamic_weights(klines)
                else:
                    target = self._get_base_weights()

                self._rebalance(target, prices, date, scale)
                last_rebalance = date

            self.portfolio.equity_curve.append({
                'date': str(date),
                'value': tv,
            })

        return self._compute_results(dates)

    def _compute_results(self, dates) -> Dict:
        """计算回测结果指标"""
        if len(self.portfolio.equity_curve) < 2:
            return {'error': '回测数据不足'}

        values = np.array([e['value'] for e in self.portfolio.equity_curve])
        initial = self.initial_capital
        final = values[-1]
        total_return = (final - initial) / initial

        years = len(dates) / 252
        annual_return = (1 + total_return) ** (1 / max(years, 0.01)) - 1

        # 最大回撤
        peak = np.maximum.accumulate(values)
        drawdowns = (peak - values) / peak
        max_dd = float(drawdowns.max())

        # Sharpe
        daily_rets = np.diff(values) / values[:-1]
        annual_vol = float(np.std(daily_rets) * np.sqrt(252))
        sharpe = (annual_return - 0.03) / max(annual_vol, 0.001)

        # 胜率
        win_rate = float(np.sum(daily_rets > 0) / len(daily_rets))

        logger.info(f"回测完成: 总收益 {total_return:.2%}, 年化 {annual_return:.2%}, "
                    f"最大回撤 {max_dd:.2%}, Sharpe {sharpe:.2f}")

        return {
            'initial_capital': initial,
            'final_capital': round(float(final), 2),
            'total_return': round(float(total_return), 4),
            'annual_return': round(float(annual_return), 4),
            'annual_volatility': round(float(annual_vol), 4),
            'max_drawdown': round(float(max_dd), 4),
            'sharpe_ratio': round(float(sharpe), 4),
            'win_rate': round(float(win_rate), 4),
            'num_trades': len(self.portfolio.trades),
            'num_days': len(values),
            'equity_curve': self.portfolio.equity_curve,
            'trades': self.portfolio.trades,
        }
