"""
协整配对交易模块 (v8.5 补充)

实现 Engle-Granger 协整检验 + 价差 z-score 信号生成。
作为 alpha 策略的补充, 适合统计套利场景。

NOTE (2026-08-10 高价值资产集成专项):
    本模块原位于 ms_strategy/src/alpha/pairs_trading.py (全仓库零消费),
    迁移至 utils/strategy_lib/ 作为候补高价值资产沉淀, 使其真正可复用。
    详见 docs/ASSET_INTEGRATION_PLAN_20260810.md。

使用方式:
    from utils.strategy_lib.pairs_trading import PairsTrading

    pt = PairsTrading(significance=0.05, zscore_window=20,
                      entry_z=2.0, exit_z=0.5)
    pairs = pt.find_cointegrated_pairs(price_data)
    signals = pt.generate_signals(price_data, pairs)

依赖:
    statsmodels (coint, OLS)  # 缺失时 fail-open 降级
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

try:
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant
    from statsmodels.tsa.stattools import coint
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False

logger = logging.getLogger(__name__)


class PairSignal:
    """单只协整对的信号结果"""

    def __init__(self, code_a: str, code_b: str,
                 hedge_ratio: float, intercept: float,
                 half_life: float | None, zscore: float,
                 signal: int, pvalue: float):
        """初始化配对信号

        Args:
            code_a: 标的 A 代码
            code_b: 标的 B 代码
            hedge_ratio: 对冲比价 (beta)
            intercept: 价差回归截距
            half_life: 价差均值回复半衰期 (天), None 表示无法估计
            zscore: 当前价差 z-score
            signal: 信号 +1 做多价差 / -1 做空价差 / 0 无信号
            pvalue: 协整检验 p-value
        """
        self.code_a = code_a
        self.code_b = code_b
        self.hedge_ratio = hedge_ratio
        self.intercept = intercept
        self.half_life = half_life
        self.zscore = zscore
        self.signal = signal
        self.pvalue = pvalue

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'code_a': self.code_a,
            'code_b': self.code_b,
            'hedge_ratio': round(self.hedge_ratio, 4),
            'intercept': round(self.intercept, 4),
            'half_life': round(self.half_life, 2) if self.half_life else None,
            'zscore': round(self.zscore, 4),
            'signal': self.signal,
            'pvalue': round(self.pvalue, 4),
        }


class PairsTrading:
    """协整配对交易信号生成器

    流程:
    1. find_cointegrated_pairs: 对所有标的两两做 Engle-Granger 协整检验
    2. generate_signals: 对每个协整对计算价差 z-score 与交易信号

    信号规则:
        z > +entry_z  做空价差 (卖 A 买 B), signal = -1
        z < -entry_z  做多价差 (买 A 卖 B), signal = +1
        |z| < exit_z  平仓信号, signal = 0 (由持仓方向决定)
        其他          持有, signal = 0
    """

    def __init__(self, significance: float = 0.05,
                 zscore_window: int = 20,
                 entry_z: float = 2.0,
                 exit_z: float = 0.5,
                 min_half_life: int = 1,
                 max_half_life: int = 60):
        """初始化配对交易参数

        Args:
            significance: 协整检验显著性水平, 默认 0.05
            zscore_window: 价差 z-score 滚动窗口, 默认 20 日
            entry_z: 开仓 z-score 阈值, 默认 2.0
            exit_z: 平仓 z-score 阈值, 默认 0.5
            min_half_life: 最小半衰期, 低于此值视为均值回复过快(噪声)
            max_half_life: 最大半衰期, 高于此值视为均值回复过慢(不套利)
        """
        if not _HAS_STATSMODELS:
            logger.warning('statsmodels 未安装, 协整检验不可用, 请 pip install statsmodels')

        self.significance = significance
        self.zscore_window = zscore_window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.min_half_life = min_half_life
        self.max_half_life = max_half_life

    # ---------- 协整检验 ----------
    def _coint_test(self, y: pd.Series, x: pd.Series) -> tuple[float, float, float]:
        """对两只标的做 Engle-Granger 协整检验

        回归: y = alpha + beta * x + epsilon
        检验: epsilon 是否平稳 (ADF)

        Args:
            y: 因变量价格序列
            x: 自变量价格序列

        Returns:
            (pvalue, beta, intercept)
            pvalue < significance 表示协整
        """
        common = y.dropna().index.intersection(x.dropna().index)
        if len(common) < 60:
            return 1.0, 0.0, 0.0

        y_aligned = y.loc[common]
        x_aligned = x.loc[common]

        try:
            # OLS 回归得到 hedge ratio
            x_with_const = add_constant(x_aligned)
            model = OLS(y_aligned, x_with_const).fit()
            intercept = float(model.params.iloc[0])
            beta = float(model.params.iloc[1])

            # Engle-Granger 协整检验
            _score, pvalue, _ = coint(y_aligned, x_aligned)
            return float(pvalue), beta, intercept
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug(f'协整检验失败: {exc}')
            return 1.0, 0.0, 0.0

    # ---------- 半衰期估计 ----------
    def _estimate_half_life(self, spread: pd.Series) -> float | None:
        """估计价差均值回复半衰期 (Ornstein-Uhlenbeck)

        回归: delta_spread = -kappa * spread_lag1 + epsilon
        半衰期 = ln(2) / kappa

        Args:
            spread: 价差序列

        Returns:
            半衰期 (天), 若 kappa <= 0 或拟合失败返回 None
        """
        spread = spread.dropna()
        if len(spread) < 30:
            return None

        try:
            spread_lag = spread.shift(1).dropna()
            delta = spread.diff().dropna()
            common = spread_lag.index.intersection(delta.index)
            if len(common) < 20:
                return None

            x = add_constant(spread_lag.loc[common])
            model = OLS(delta.loc[common], x).fit()
            kappa = -float(model.params.iloc[1])

            if kappa <= 0:
                return None

            return float(np.log(2) / kappa)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return None

    # ---------- 寻找协整对 ----------
    def find_cointegrated_pairs(self, price_data: dict[str, pd.DataFrame],
                                max_pairs: int = 20) -> list[dict]:
        """在全部标的中寻找协整对

        Args:
            price_data: {symbol: DataFrame[OHLCV]}
            max_pairs: 最多返回的协整对数量 (按 p-value 升序)

        Returns:
            [{code_a, code_b, pvalue, beta, intercept, half_life}, ...]
        """
        if not _HAS_STATSMODELS:
            logger.error('statsmodels 未安装, 无法执行协整检验')
            return []

        symbols = list(price_data.keys())
        if len(symbols) < 2:
            return []

        # 提取收盘价
        close_dict = {}
        for sym, df in price_data.items():
            if 'close' in df.columns:
                close_dict[sym] = df['close']
            elif len(df.columns) > 0:
                close_dict[sym] = df.iloc[:, 0]

        candidates = []
        n = len(symbols)
        # 两两组合
        for i in range(n):
            for j in range(i + 1, n):
                sym_a = symbols[i]
                sym_b = symbols[j]
                price_a = close_dict.get(sym_a)
                price_b = close_dict.get(sym_b)
                if price_a is None or price_b is None:
                    continue

                pvalue, beta, intercept = self._coint_test(price_a, price_b)
                if pvalue >= self.significance or beta == 0:
                    continue

                # 计算价差用于半衰期估计
                common = price_a.dropna().index.intersection(price_b.dropna().index)
                if len(common) < 60:
                    continue
                spread = price_a.loc[common] - beta * price_b.loc[common] - intercept
                half_life = self._estimate_half_life(spread)

                # 半衰期过滤
                if half_life is not None:
                    if half_life < self.min_half_life or half_life > self.max_half_life:
                        continue

                candidates.append({
                    'code_a': sym_a,
                    'code_b': sym_b,
                    'pvalue': pvalue,
                    'beta': beta,
                    'intercept': intercept,
                    'half_life': half_life,
                })

        # 按 p-value 升序, 取前 max_pairs
        candidates.sort(key=lambda d: d['pvalue'])
        return candidates[:max_pairs]

    # ---------- 信号生成 ----------
    def generate_signals(self, price_data: dict[str, pd.DataFrame],
                         pairs: list[dict] | None = None) -> list[PairSignal]:
        """对所有协整对生成交易信号

        Args:
            price_data: {symbol: DataFrame[OHLCV]}
            pairs: 协整对列表 (来自 find_cointegrated_pairs), None 则自动寻找

        Returns:
            List[PairSignal]
        """
        if pairs is None:
            pairs = self.find_cointegrated_pairs(price_data)

        if not pairs:
            logger.info('无协整对, 跳过信号生成')
            return []

        # 收盘价字典
        close_dict = {}
        for sym, df in price_data.items():
            if 'close' in df.columns:
                close_dict[sym] = df['close']
            elif len(df.columns) > 0:
                close_dict[sym] = df.iloc[:, 0]

        signals = []
        for pair in pairs:
            code_a = pair['code_a']
            code_b = pair['code_b']
            beta = pair['beta']
            intercept = pair['intercept']

            price_a = close_dict.get(code_a)
            price_b = close_dict.get(code_b)
            if price_a is None or price_b is None:
                continue

            common = price_a.dropna().index.intersection(price_b.dropna().index)
            if len(common) < self.zscore_window + 5:
                continue

            # 价差 = A - beta * B - intercept
            spread = price_a.loc[common] - beta * price_b.loc[common] - intercept

            # 滚动 z-score
            rolling_mean = spread.rolling(window=self.zscore_window, min_periods=1).mean()
            rolling_std = spread.rolling(window=self.zscore_window, min_periods=1).std(ddof=0)
            rolling_std = rolling_std.replace(0, np.nan)
            z = (spread - rolling_mean) / rolling_std
            z = z.fillna(0.0)

            # 最新 z-score
            latest_z = float(z.iloc[-1]) if len(z) > 0 else 0.0

            # 信号规则
            if latest_z > self.entry_z:
                signal = -1  # 做空价差: 卖 A 买 B
            elif latest_z < -self.entry_z:
                signal = +1  # 做多价差: 买 A 卖 B
            elif abs(latest_z) < self.exit_z:
                signal = 0   # 平仓区
            else:
                signal = 0   # 持有/无信号

            signals.append(PairSignal(
                code_a=code_a,
                code_b=code_b,
                hedge_ratio=beta,
                intercept=intercept,
                half_life=pair.get('half_life'),
                zscore=latest_z,
                signal=signal,
                pvalue=pair['pvalue'],
            ))

        return signals

    # ---------- 简单回测 ----------
    def backtest_pair(self, price_a: pd.Series, price_b: pd.Series,
                      beta: float, intercept: float,
                      cost_bps: float = 5.0) -> dict:
        """单对配对的简单向量回测

        信号规则:
            z > +entry_z  做空价差 (短 A, 长 B)
            z < -entry_z  做多价差 (长 A, 短 B)
            |z| < exit_z  平仓

        Args:
            price_a: 标的 A 收盘价
            price_b: 标的 B 收盘价
            beta: 对冲比
            intercept: 截距
            cost_bps: 单边交易成本 (bp)

        Returns:
            {equity_curve, trades, sharpe, max_dd, total_return}
        """
        common = price_a.dropna().index.intersection(price_b.dropna().index)
        if len(common) < self.zscore_window + 5:
            return {'equity_curve': pd.Series(), 'trades': 0,
                    'sharpe': 0.0, 'max_dd': 0.0, 'total_return': 0.0}

        pa = price_a.loc[common]
        pb = price_b.loc[common]
        spread = pa - beta * pb - intercept

        rolling_mean = spread.rolling(window=self.zscore_window, min_periods=1).mean()
        rolling_std = spread.rolling(window=self.zscore_window, min_periods=1).std(ddof=0)
        rolling_std = rolling_std.replace(0, np.nan)
        z = (spread - rolling_mean) / rolling_std
        z = z.fillna(0.0)

        # 仓位: +1 多价差, -1 空价差, 0 空仓
        position = pd.Series(0, index=common)
        prev_pos = 0
        for i in range(len(z)):
            zi = z.iloc[i]
            if zi > self.entry_z:
                target = -1
            elif zi < -self.entry_z:
                target = +1
            elif abs(zi) < self.exit_z:
                target = 0
            else:
                target = prev_pos
            position.iloc[i] = target
            prev_pos = target

        # 价差收益
        spread_ret = spread.diff().fillna(0.0)
        strategy_ret = position.shift(1).fillna(0) * spread_ret

        # 交易成本
        turnover = position.diff().abs().fillna(0)
        cost = turnover * (cost_bps / 10000.0) * abs(spread)
        net_ret = strategy_ret - cost

        # 权益曲线
        equity = (1.0 + net_ret).cumprod()

        # 指标
        total_return = float(equity.iloc[-1] - 1.0) if len(equity) > 0 else 0.0
        ann_ret = float(net_ret.mean() * 252) if len(net_ret) > 0 else 0.0
        ann_vol = float(net_ret.std() * np.sqrt(252)) if len(net_ret) > 0 else 0.001
        sharpe = ann_ret / max(ann_vol, 1e-8)

        rolling_max = equity.expanding().max()
        dd = (equity - rolling_max) / rolling_max.replace(0, np.nan)
        max_dd = float(dd.min()) if len(dd) > 0 else 0.0

        trades = int((turnover > 0).sum())

        return {
            'equity_curve': equity,
            'trades': trades,
            'sharpe': round(sharpe, 3),
            'max_dd': round(max_dd, 4),
            'total_return': round(total_return, 4),
            'ann_return': round(ann_ret, 4),
            'ann_vol': round(ann_vol, 4),
        }
