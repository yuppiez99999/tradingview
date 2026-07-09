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

from utils.data_types import safe_float, safe_int

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
        total = safe_float(self.cash, default=0.0)
        if total is None:
            total = 0.0
        for code, pos in self.positions.items():
            shares = safe_int(pos.get('shares'), default=0)
            price = safe_float(prices.get(code), default=None)
            if shares is not None and shares > 0 and price is not None:
                total += shares * price
        return total if total is not None else 0.0

    def current_weights(self, prices: Dict[str, float]) -> Dict[str, float]:
        """计算当前权重"""
        tv = self.total_value(prices)
        if tv <= 0:
            return {code: 0.0 for code in self.positions}
        result = {}
        for code in self.positions:
            shares = safe_int(self.positions[code].get('shares'), default=0)
            price = safe_float(prices.get(code), default=None)
            if shares is not None and shares > 0 and price is not None:
                result[code] = shares * price / tv
            else:
                result[code] = 0.0
        return result

    def execute_trade(self, code: str, side: str, shares: int, price: float,
                      cost_rate: float = 0.0008) -> Tuple[bool, float]:
        """
        执行交易并计算成本。

        Args:
            cost_rate: 总成本率（佣金0.03% + 滑点0.03% + 冲击0.02%）

        Returns:
            (success, trade_cost)
        """
        safe_shares = safe_int(shares, default=0)
        safe_price = safe_float(price, default=0.0)
        if safe_shares is None or safe_shares <= 0 or safe_price is None or safe_price <= 0:
            return False, 0.0

        if side == 'buy':
            gross = safe_shares * safe_price
            cost = gross * cost_rate
            total = gross + cost

            if total <= self.cash:
                old_cost = safe_float(self.positions[code].get('avg_cost'), default=0.0) * safe_int(self.positions[code].get('shares'), default=0)
                new_shares = safe_int(self.positions[code].get('shares'), default=0) + safe_shares
                self.positions[code]['shares'] = new_shares
                self.positions[code]['avg_cost'] = (
                    (old_cost + gross) / new_shares if new_shares > 0 else 0
                )
                self.cash -= total
                return True, cost

        elif side == 'sell':
            existing_shares = safe_int(self.positions[code].get('shares'), default=0)
            if existing_shares is not None and existing_shares >= safe_shares:
                gross = safe_shares * safe_price
                cost = gross * cost_rate
                net = gross - cost
                self.positions[code]['shares'] = existing_shares - safe_shares
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

    def robustness_check(self, klines: Dict[str, pd.DataFrame],
                          n_bootstrap: int = 200,
                          random_seed: int = 42) -> Dict:
        """
        稳健性检验 — 借鉴 AERS 实证研究方法论

        从 5 个维度验证策略的稳健性：
        1. 子样本分析（牛/熊/震荡市）
        2. 参数敏感性（再平衡阈值/间隔）
        3. 成本敏感性（不同交易成本）
        4. Bootstrap 置信区间
        5. 单标的剔除稳定性

        Args:
            klines: K线数据字典
            n_bootstrap: Bootstrap 迭代次数
            random_seed: 随机种子

        Returns:
            包含 5 项检验结果的字典 + 综合稳健性评分
        """
        rng = np.random.RandomState(random_seed)
        base_result = self.run(klines)
        if 'error' in base_result:
            return {'error': f'基准回测失败: {base_result["error"]}'}

        logger.info("开始稳健性检验（5维度）...")

        results = {
            'baseline': {
                'total_return': base_result['total_return'],
                'annual_return': base_result['annual_return'],
                'sharpe_ratio': base_result['sharpe_ratio'],
                'max_drawdown': base_result['max_drawdown'],
            },
        }

        # 1. 子样本分析
        results['subsample'] = self._subsample_analysis(klines, rng)

        # 2. 参数敏感性
        results['param_sensitivity'] = self._param_sensitivity(klines)

        # 3. 成本敏感性
        results['cost_sensitivity'] = self._cost_sensitivity(klines)

        # 4. Bootstrap 置信区间
        results['bootstrap_ci'] = self._bootstrap_ci(klines, n_bootstrap, rng)

        # 5. 单标的剔除稳定性
        results['leave_one_out'] = self._leave_one_out(klines)

        # 综合稳健性评分
        score = self._compute_robustness_score(results)
        results['robustness_score'] = score
        results['report'] = self._format_robustness_report(results, score)

        logger.info(f"稳健性检验完成，综合评分: {score['overall']}/100")
        return results

    def _subsample_analysis(self, klines, rng) -> Dict:
        """子样本分析：牛/熊/震荡市分别回测"""
        dates = self._get_common_dates(klines)
        if len(dates) < 60:
            return {'error': '数据不足，无法进行子样本分析'}

        benchmark_code = self.assets[0] if self.assets else None
        if not benchmark_code or benchmark_code not in klines:
            return {'error': '无基准标的'}

        benchmark = klines[benchmark_code]['close'].reindex(dates).ffill()
        bench_returns = benchmark.pct_change().fillna(0)

        rolling_mean = bench_returns.rolling(20).mean().fillna(0)
        cumulative = (1 + bench_returns).cumprod()
        rolling_peak = cumulative.cummax()
        rolling_dd = cumulative / rolling_peak - 1

        bull_mask = (rolling_mean > 0.001) & (rolling_dd > -0.05)
        bear_mask = rolling_dd < -0.10
        sideways_mask = ~bull_mask & ~bear_mask

        sub_results = {}
        regimes = [
            ('牛市', bull_mask),
            ('熊市', bear_mask),
            ('震荡市', sideways_mask),
        ]

        for name, mask in regimes:
            mask_vals = mask.reindex(dates).fillna(False).values.astype(bool)
            regime_dates = dates[mask_vals]
            if len(regime_dates) < 20:
                sub_results[name] = {'note': f'样本不足({len(regime_dates)}天)'}
                continue

            sub_klines = {}
            for code, df in klines.items():
                if df is not None and not df.empty:
                    sub_klines[code] = df.loc[df.index.isin(regime_dates)].copy()

            if not sub_klines:
                sub_results[name] = {'note': '无有效K线数据'}
                continue

            temp_engine = EnhancedBacktestEngine(
                self.config, self.initial_capital,
                self.rebalance_interval, self.rebalance_threshold,
                self.use_risk_parity, self.use_dynamic_weights
            )
            res = temp_engine.run(sub_klines)
            if 'error' not in res:
                sub_results[name] = {
                    'total_return': res['total_return'],
                    'annual_return': res['annual_return'],
                    'sharpe_ratio': res['sharpe_ratio'],
                    'max_drawdown': res['max_drawdown'],
                    'num_days': res['num_days'],
                }
            else:
                sub_results[name] = {'note': res['error']}

        return sub_results

    def _param_sensitivity(self, klines) -> Dict:
        """参数敏感性：不同再平衡阈值/间隔下的表现"""
        param_configs = [
            ('基准', self.rebalance_interval, self.rebalance_threshold),
            ('间隔+50%', int(self.rebalance_interval * 1.5), self.rebalance_threshold),
            ('间隔-30%', max(5, int(self.rebalance_interval * 0.7)), self.rebalance_threshold),
            ('阈值+50%', self.rebalance_interval, self.rebalance_threshold * 1.5),
            ('阈值-50%', self.rebalance_interval, max(0.01, self.rebalance_threshold * 0.5)),
            ('双松', int(self.rebalance_interval * 1.5), self.rebalance_threshold * 1.5),
            ('双紧', max(5, int(self.rebalance_interval * 0.7)), max(0.01, self.rebalance_threshold * 0.5)),
        ]

        results = {}
        for name, interval, threshold in param_configs:
            temp = EnhancedBacktestEngine(
                self.config, self.initial_capital,
                interval, threshold,
                self.use_risk_parity, self.use_dynamic_weights
            )
            res = temp.run(klines)
            if 'error' not in res:
                results[name] = {
                    'total_return': res['total_return'],
                    'sharpe_ratio': res['sharpe_ratio'],
                    'max_drawdown': res['max_drawdown'],
                    'num_trades': res['num_trades'],
                    'interval': interval,
                    'threshold': threshold,
                }

        return results

    def _cost_sensitivity(self, klines) -> Dict:
        """成本敏感性：不同交易成本下的表现"""
        cost_rates = [0.0003, 0.0005, 0.0008, 0.0015, 0.0025]

        results = {}
        for cost in cost_rates:
            temp = EnhancedBacktestEngine(
                self.config, self.initial_capital,
                self.rebalance_interval, self.rebalance_threshold,
                self.use_risk_parity, self.use_dynamic_weights
            )
            temp._cost_rate = cost

            original_execute = temp.portfolio.execute_trade

            def patched_execute(code, side, shares, price, cost_rate=cost):
                return original_execute(code, side, shares, price, cost_rate)

            temp.portfolio.execute_trade = patched_execute

            res = temp.run(klines)
            if 'error' not in res:
                results[f'{cost*100:.2f}%'] = {
                    'total_return': res['total_return'],
                    'sharpe_ratio': res['sharpe_ratio'],
                    'max_drawdown': res['max_drawdown'],
                    'cost_rate': cost,
                }

        return results

    def _bootstrap_ci(self, klines, n_bootstrap, rng) -> Dict:
        """Bootstrap 置信区间：通过重采样估计指标的不确定性

        使用分块 Bootstrap（block bootstrap）保持时间序列结构，
        避免日期重复导致的索引冲突问题。
        """
        base_result = self.run(klines)
        if 'error' in base_result:
            return {'error': f'基准回测失败: {base_result["error"]}'}

        equity = np.array([e['value'] for e in base_result['equity_curve']])
        if len(equity) < 30:
            return {'error': '数据不足'}

        daily_rets = np.diff(equity) / equity[:-1]
        n = len(daily_rets)

        sharpe_list = []
        return_list = []
        dd_list = []

        block_size = max(5, int(np.sqrt(n)))
        n_blocks = n // block_size + 2

        for i in range(n_bootstrap):
            block_starts = rng.choice(n - block_size + 1, size=n_blocks, replace=True)
            boot_rets = []
            for start in block_starts:
                boot_rets.extend(daily_rets[start:start + block_size])
            boot_rets = np.array(boot_rets[:n])

            boot_equity = 1.0 * np.cumprod(1 + boot_rets)
            total_return = boot_equity[-1] - 1
            annual_return = (1 + total_return) ** (252 / max(n, 1)) - 1
            annual_vol = float(np.std(boot_rets) * np.sqrt(252))
            sharpe = (annual_return - 0.03) / max(annual_vol, 0.001)

            peak = np.maximum.accumulate(boot_equity)
            max_dd = float(((peak - boot_equity) / peak).max())

            sharpe_list.append(sharpe)
            return_list.append(total_return)
            dd_list.append(max_dd)

        if not sharpe_list:
            return {'error': 'Bootstrap 全部失败'}

        def ci(arr, level=0.95):
            arr = np.array(arr)
            low = (1 - level) / 2
            high = 1 - low
            return {
                'mean': float(np.mean(arr)),
                'std': float(np.std(arr)),
                'ci_lower': float(np.percentile(arr, low * 100)),
                'ci_upper': float(np.percentile(arr, high * 100)),
                'median': float(np.median(arr)),
            }

        return {
            'n_iterations': len(sharpe_list),
            'method': 'block_bootstrap',
            'block_size': block_size,
            'total_return': ci(return_list),
            'sharpe_ratio': ci(sharpe_list),
            'max_drawdown': ci(dd_list),
        }

    def _leave_one_out(self, klines) -> Dict:
        """单标的剔除：逐个剔除标的，看结论是否稳定"""
        if len(self.assets) < 3:
            return {'error': '标的数量不足（至少3只）'}

        baseline = self.run(klines)
        if 'error' in baseline:
            return {'error': f'基准回测失败: {baseline["error"]}'}

        baseline_sharpe = baseline['sharpe_ratio']
        baseline_return = baseline['total_return']

        results = {}
        for exclude_code in self.assets:
            remaining = [a for a in self.config['assets'] if a['code'] != exclude_code]
            if len(remaining) < 2:
                continue

            sub_config = {'assets': remaining}
            temp = EnhancedBacktestEngine(
                sub_config, self.initial_capital,
                self.rebalance_interval, self.rebalance_threshold,
                self.use_risk_parity, self.use_dynamic_weights
            )
            res = temp.run(klines)
            if 'error' not in res:
                sharpe_delta = res['sharpe_ratio'] - baseline_sharpe
                ret_delta = res['total_return'] - baseline_return
                results[exclude_code] = {
                    'sharpe_ratio': res['sharpe_ratio'],
                    'total_return': res['total_return'],
                    'max_drawdown': res['max_drawdown'],
                    'sharpe_delta': round(sharpe_delta, 4),
                    'return_delta': round(ret_delta, 4),
                }

        return results

    def _compute_robustness_score(self, results: Dict) -> Dict:
        """
        计算综合稳健性评分（0-100）

        5个维度各占 20 分：
        - 子样本一致性：各行情下收益方向是否一致
        - 参数敏感性：参数变化时收益波动程度
        - 成本敏感性：成本翻倍时收益衰减程度
        - Bootstrap 稳定性：置信区间宽度
        - 剔除稳定性：单标的剔除后结论稳定性
        """
        scores = {}
        total = 0

        # 1. 子样本一致性 (20分)
        sub = results.get('subsample', {})
        if 'error' not in sub and len(sub) >= 2:
            rets = [v['total_return'] for v in sub.values() if 'total_return' in v]
            if len(rets) >= 2:
                all_positive = all(r > 0 for r in rets)
                std_ret = np.std(rets) / max(abs(np.mean(rets)), 0.01)
                consistency = max(0, 1 - std_ret)
                scores['subsample'] = min(20, int(consistency * 20 + (10 if all_positive else 0)))
            else:
                scores['subsample'] = 5
        else:
            scores['subsample'] = 0
        total += scores['subsample']

        # 2. 参数敏感性 (20分)
        param = results.get('param_sensitivity', {})
        if param and len(param) >= 3:
            sharpes = [v['sharpe_ratio'] for v in param.values() if 'sharpe_ratio' in v]
            if sharpes:
                base_sharpe = results['baseline']['sharpe_ratio']
                deviations = [abs(s - base_sharpe) / max(abs(base_sharpe), 0.01) for s in sharpes]
                max_dev = min(max(deviations), 1.0)
                scores['param_sensitivity'] = int((1 - max_dev * 0.5) * 20)
            else:
                scores['param_sensitivity'] = 5
        else:
            scores['param_sensitivity'] = 0
        total += scores['param_sensitivity']

        # 3. 成本敏感性 (20分)
        cost = results.get('cost_sensitivity', {})
        if cost and len(cost) >= 3:
            rets = [v['total_return'] for v in cost.values() if 'total_return' in v]
            if rets and rets[0] > 0:
                decay = (rets[0] - rets[-1]) / max(rets[0], 0.0001)
                scores['cost_sensitivity'] = int(max(0, min(20, (1 - decay) * 20)))
            else:
                scores['cost_sensitivity'] = 5
        else:
            scores['cost_sensitivity'] = 0
        total += scores['cost_sensitivity']

        # 4. Bootstrap 稳定性 (20分)
        boot = results.get('bootstrap_ci', {})
        if 'error' not in boot and 'sharpe_ratio' in boot:
            sharpe_ci = boot['sharpe_ratio']
            ci_width = sharpe_ci['ci_upper'] - sharpe_ci['ci_lower']
            relative_width = ci_width / max(abs(sharpe_ci['mean']), 0.01)
            scores['bootstrap'] = int(max(0, min(20, (1 - relative_width * 0.3) * 20)))
        else:
            scores['bootstrap'] = 0
        total += scores['bootstrap']

        # 5. 剔除稳定性 (20分)
        loo = results.get('leave_one_out', {})
        if 'error' not in loo and len(loo) >= 2:
            deltas = [abs(v['sharpe_delta']) for v in loo.values() if 'sharpe_delta' in v]
            if deltas:
                avg_delta = np.mean(deltas)
                base_sharpe = results['baseline']['sharpe_ratio']
                relative_delta = avg_delta / max(abs(base_sharpe), 0.01)
                scores['leave_one_out'] = int(max(0, min(20, (1 - relative_delta * 0.5) * 20)))
            else:
                scores['leave_one_out'] = 5
        else:
            scores['leave_one_out'] = 0
        total += scores['leave_one_out']

        scores['overall'] = total

        if total >= 80:
            scores['grade'] = 'A+ 极稳健'
        elif total >= 65:
            scores['grade'] = 'A 稳健'
        elif total >= 50:
            scores['grade'] = 'B 一般'
        elif total >= 35:
            scores['grade'] = 'C 偏弱'
        else:
            scores['grade'] = 'D 脆弱'

        return scores

    def _format_robustness_report(self, results: Dict, score: Dict) -> str:
        """生成稳健性检验中文报告"""
        lines = [
            "=" * 60,
            "  回测稳健性检验报告",
            "=" * 60,
            "",
            f"综合稳健性评分: {score['overall']}/100  ({score.get('grade', '未知')})",
            f"  子样本一致性: {score.get('subsample', 0)}/20",
            f"  参数敏感性: {score.get('param_sensitivity', 0)}/20",
            f"  成本敏感性: {score.get('cost_sensitivity', 0)}/20",
            f"  Bootstrap 稳定性: {score.get('bootstrap', 0)}/20",
            f"  剔除稳定性: {score.get('leave_one_out', 0)}/20",
            "",
            "-" * 60,
            "【基准回测】",
            f"  总收益: {results['baseline']['total_return']:.2%}",
            f"  年化收益: {results['baseline']['annual_return']:.2%}",
            f"  Sharpe: {results['baseline']['sharpe_ratio']:.2f}",
            f"  最大回撤: {results['baseline']['max_drawdown']:.2%}",
            "",
            "-" * 60,
            "【子样本分析】",
        ]

        sub = results.get('subsample', {})
        if 'error' in sub:
            lines.append(f"  {sub['error']}")
        else:
            for name, data in sub.items():
                if 'total_return' in data:
                    lines.append(f"  {name}: 收益 {data['total_return']:.2%}, "
                                 f"Sharpe {data['sharpe_ratio']:.2f}, "
                                 f"回撤 {data['max_drawdown']:.2%}")
                else:
                    lines.append(f"  {name}: {data.get('note', '无数据')}")

        lines += ["", "-" * 60, "【参数敏感性】"]
        param = results.get('param_sensitivity', {})
        for name, data in param.items():
            lines.append(f"  {name}: 收益 {data['total_return']:.2%}, "
                         f"Sharpe {data['sharpe_ratio']:.2f}, "
                         f"交易次数 {data['num_trades']}")

        lines += ["", "-" * 60, "【成本敏感性】"]
        cost = results.get('cost_sensitivity', {})
        for name, data in cost.items():
            lines.append(f"  成本{name}: 收益 {data['total_return']:.2%}, "
                         f"Sharpe {data['sharpe_ratio']:.2f}")

        lines += ["", "-" * 60, "【Bootstrap 置信区间(95%)】"]
        boot = results.get('bootstrap_ci', {})
        if 'error' in boot:
            lines.append(f"  {boot['error']}")
        else:
            lines.append(f"  迭代次数: {boot['n_iterations']}")
            for metric in ['total_return', 'sharpe_ratio', 'max_drawdown']:
                if metric in boot:
                    d = boot[metric]
                    lines.append(f"  {metric}: 均值 {d['mean']:.4f}, "
                                 f"95% CI [{d['ci_lower']:.4f}, {d['ci_upper']:.4f}]")

        lines += ["", "-" * 60, "【单标的剔除检验】"]
        loo = results.get('leave_one_out', {})
        if 'error' in loo:
            lines.append(f"  {loo['error']}")
        else:
            for code, data in loo.items():
                delta = data.get('sharpe_delta', 0)
                sign = "+" if delta > 0 else ""
                lines.append(f"  剔除{code}: Sharpe {data['sharpe_ratio']:.2f} "
                             f"({sign}{delta:.2f}), "
                             f"收益 {data['total_return']:.2%}")

        lines += ["", "=" * 60]
        return "\n".join(lines)
