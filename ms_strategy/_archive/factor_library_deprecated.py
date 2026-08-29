"""
v7.5 FactorLibrary — 五维因子库：价值 / 质量 / 动量 / 增长 / 安全
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §6 (src/alpha/)
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FactorGroup:
    """单组因子"""
    name: str
    factors: dict[str, pd.Series] = field(default_factory=dict)
    weight: float = 1.0


class FactorLibrary:
    """
    v7.5 五维因子库

    五大维度：
    1. Value (价值) — PE, PB, PS, FCF Yield, Dividend Yield
    2. Quality (质量) — ROE, ROIC, Gross Margin, Accruals, Debt/EBITDA
    3. Momentum (动量) — 1M/3M/6M/12M Return, RSI, MACD
    4. Growth (增长) — Rev Growth, EPS Growth, Earnings Revision
    5. Safety (安全) — Beta, IVOL, Max DD, VaR
    """

    # 因子标准化参数（Z-Score 裁剪）
    ZSCORE_CLIP = 3.0

    def __init__(self):
        self.factor_groups: dict[str, FactorGroup] = {
            'value': FactorGroup(name='value'),
            'quality': FactorGroup(name='quality'),
            'momentum': FactorGroup(name='momentum'),
            'growth': FactorGroup(name='growth'),
            'safety': FactorGroup(name='safety'),
        }
        self._all_factors: dict[str, pd.Series] = {}

    # ---------- Value 因子 ----------
    def compute_pe(self, price: pd.Series, earnings_per_share: float) -> pd.Series:
        return price / max(earnings_per_share, 0.01)

    def compute_pb(self, price: pd.Series, book_value_per_share: float) -> pd.Series:
        return price / max(book_value_per_share, 0.01)

    def compute_ps(self, price: pd.Series, sales_per_share: float) -> pd.Series:
        return price / max(sales_per_share, 0.01)

    def compute_fcf_yield(self, price: pd.Series, fcf_per_share: float) -> pd.Series:
        return fcf_per_share / max(price.iloc[-1], 0.01)

    def compute_dividend_yield(self, price: pd.Series, dps: float) -> pd.Series:
        return dps / max(price.iloc[-1], 0.01)

    # ---------- Quality 因子 ----------
    def compute_roe(self, net_income: float, equity: float) -> float:
        return net_income / max(equity, 0.01)

    def compute_roic(self, nopat: float, invested_capital: float) -> float:
        return nopat / max(invested_capital, 0.01)

    def compute_gross_margin(self, revenue: float, cogs: float) -> float:
        return (revenue - cogs) / max(revenue, 0.01)

    def compute_accruals(self, net_income: float, operating_cf: float, total_assets: float) -> float:
        if total_assets <= 0:
            return 0.0
        return (net_income - operating_cf) / total_assets

    def compute_debt_ebitda(self, total_debt: float, ebitda: float) -> float:
        return total_debt / max(ebitda, 0.01)

    # ---------- Momentum 因子 ----------
    def compute_momentum(self, price: pd.Series, window: int) -> pd.Series:
        return price.pct_change(window)

    def compute_rsi(self, price: pd.Series, window: int = 14) -> pd.Series:
        delta = price.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window, min_periods=1).mean()
        avg_loss = loss.rolling(window, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-8)
        return 100.0 - (100.0 / (1.0 + rs))

    def compute_macd(self, price: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        ema_fast = price.ewm(span=fast, adjust=False).mean()
        ema_slow = price.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line - signal_line

    # ---------- 技术形态因子 (v8.5 补充) ----------
    def compute_bollinger_zscore(self, price: pd.Series, window: int = 20,
                                 num_std: float = 2.0) -> pd.Series:
        """布林带 z-score 因子: 价格相对中轨的标准化偏离

        计算方式:
            middle = SMA(close, window)
            std    = STD(close, window)
            z      = (close - middle) / (num_std * std)

        解读:
            z > 1   价格突破上轨, 超买(均值回归视角为负 alpha)
            z < -1  价格突破下轨, 超卖(均值回归视角为正 alpha)
            |z| < 0.5 价格在中轨附近震荡

        作为因子直接使用 z 值, 后续由 LASSO/Ridge 决定方向。
        若需要显式均值回归信号, 取 -z 即可。

        Args:
            price: 收盘价序列
            window: 滚动窗口, 默认 20 日
            num_std: 标准差倍数, 默认 2.0

        Returns:
            pd.Series: 布林带 z-score
        """
        middle = price.rolling(window=window, min_periods=1).mean()
        std = price.rolling(window=window, min_periods=1).std(ddof=0)
        # 防止除零
        std = std.replace(0, np.nan)
        z = (price - middle) / (num_std * std)
        return z.fillna(0.0)

    def compute_awesome_oscillator(self, high: pd.Series, low: pd.Series,
                                   fast: int = 5, slow: int = 34) -> pd.Series:
        """Awesome Oscillator 因子: 5/34 中价动量差

        计算方式:
            median = (high + low) / 2
            AO     = SMA(median, fast) - SMA(median, slow)

        解读:
            AO > 0 且上升   短期动量强于中期, 上升趋势
            AO < 0 且下降   短期动量弱于中期, 下降趋势
            AO 穿越零线     趋势转换信号

        Args:
            high: 最高价序列
            low:  最低价序列
            fast: 快线窗口, 默认 5
            slow: 慢线窗口, 默认 34

        Returns:
            pd.Series: AO 振荡值
        """
        median = (high + low) / 2.0
        fast_ma = median.rolling(window=fast, min_periods=1).mean()
        slow_ma = median.rolling(window=slow, min_periods=1).mean()
        return fast_ma - slow_ma

    def compute_dual_thrust_position(self, high: pd.Series, low: pd.Series,
                                     close: pd.Series, window: int = 5,
                                     k_up: float = 0.5, k_down: float = 0.5) -> pd.Series:
        """Dual Thrust 位置因子: 价格在 N 日突破区间内的相对位置

        计算方式:
            HH = max(high, window)         LL = min(low, window)
            HC = max(close, window)        LC = min(close, window)
            range_up   = max(HH - LC, HC - LL)
            range_down = min(HH - LC, HC - LL)
            upper = close_prev + k_up * range_up
            lower = close_prev - k_down * range_down
            position = (close - lower) / (upper - lower) - 0.5

        解读:
            position > 0  接近上轨, 突破在即/已突破, 多头强势
            position < 0  接近下轨, 突破在即/已突破, 空头强势
            position ~ 0  区间中段

        Args:
            high:  最高价序列
            low:   最低价序列
            close: 收盘价序列
            window: 回看窗口, 默认 5 日
            k_up:   上轨系数
            k_down: 下轨系数

        Returns:
            pd.Series: 位置因子, 通常落在 [-0.5, 0.5]
        """
        # N 日内的极值
        hh = high.rolling(window=window, min_periods=1).max()
        ll = low.rolling(window=window, min_periods=1).min()
        hc = close.rolling(window=window, min_periods=1).max()
        lc = close.rolling(window=window, min_periods=1).min()

        range_up = (hh - lc).combine(hc - ll, max)
        range_down = (hh - lc).combine(hc - ll, min)

        # 前一日收盘作为基准
        close_prev = close.shift(1).fillna(close.iloc[0] if len(close) > 0 else 0.0)
        upper = close_prev + k_up * range_up
        lower = close_prev - k_down * range_down

        # 防止除零: 上下轨相等时填 0
        denom = (upper - lower).replace(0, np.nan)
        position = (close - lower) / denom - 0.5
        return position.fillna(0.0)

    # ---------- Growth 因子 ----------
    def compute_rev_growth(self, revenue: pd.Series) -> float:
        if len(revenue) < 2:
            return 0.0
        return (revenue.iloc[-1] / max(revenue.iloc[-2], 0.01)) - 1.0

    def compute_eps_growth(self, eps: pd.Series) -> float:
        if len(eps) < 2:
            return 0.0
        return (eps.iloc[-1] / max(eps.iloc[-2], 0.01)) - 1.0

    def compute_earnings_revision(self, est_current: float, est_prior: float) -> float:
        if est_prior == 0:
            return 0.0
        return (est_current - est_prior) / abs(est_prior)

    # ---------- Safety 因子 ----------
    def compute_beta(self, returns: pd.Series, market_returns: pd.Series, window: int = 60) -> float:
        common = returns.dropna().index.intersection(market_returns.dropna().index)
        if len(common) < window:
            return 1.0
        cov = np.cov(returns.loc[common].values[-window:],
                     market_returns.loc[common].values[-window:])[0, 1]
        var_m = market_returns.loc[common].iloc[-window:].var()
        return cov / max(var_m, 1e-8)

    def compute_ivol(self, returns: pd.Series, market_returns: pd.Series, window: int = 60) -> float:
        common = returns.dropna().index.intersection(market_returns.dropna().index)
        if len(common) < window:
            std = returns.std()
            return 0.0 if pd.isna(std) else float(std)
        import statsmodels.api as sm
        y = returns.loc[common].iloc[-window:].dropna()
        x = market_returns.loc[y.index]
        if len(y) < 10:
            std = y.std()
            return 0.0 if pd.isna(std) else float(std)
        try:
            model = sm.OLS(y, sm.add_constant(x)).fit()
            resid_std = np.std(model.resid)
            return 0.0 if pd.isna(resid_std) else float(resid_std)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            std = y.std()
            return 0.0 if pd.isna(std) else float(std)

    def compute_max_dd(self, price: pd.Series) -> float:
        if len(price) == 0 or price.iloc[0] <= 0:
            return 0.0
        rolling_max = price.expanding().max()
        # 防止除以 0
        denom = rolling_max.replace(0, np.nan)
        dd = (price - rolling_max) / denom
        dd = dd.replace([np.inf, -np.inf], np.nan).fillna(0)
        return float(dd.min())

    def compute_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        return float(returns.quantile(1 - confidence))

    # ---------- Z-Score 标准化 ----------
    def zscore(self, series: pd.Series) -> pd.Series:
        mean = series.mean()
        std = series.std()
        if std == 0 or np.isnan(std):
            return pd.Series(0.0, index=series.index)
        return np.clip((series - mean) / std, -self.ZSCORE_CLIP, self.ZSCORE_CLIP)

    def _build_market_returns_proxy(self, price_data: dict[str, pd.DataFrame]) -> Optional[pd.Series]:
        """构建等权市场收益代理（仅用同一日截面均值，避免泄漏）"""
        if len(price_data) <= 1:
            return None
        ret_frames = []
        for symbol, df in price_data.items():
            price = df.get('close', df[df.columns[0]] if len(df.columns) else df.iloc[:, 0])
            r = price.pct_change()
            if isinstance(r, pd.Series):
                ret_frames.append(r.rename(symbol))
        if ret_frames:
            ret_df = pd.concat(ret_frames, axis=1)
            return ret_df.mean(axis=1)
        return None

    def _compute_momentum_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                                  price: pd.Series) -> None:
        """计算动量维度因子（价格类，始终计算）"""
        all_factors[f'{symbol}_momentum_1m'] = self.zscore(self.compute_momentum(price, 21))
        all_factors[f'{symbol}_momentum_3m'] = self.zscore(self.compute_momentum(price, 63))
        all_factors[f'{symbol}_momentum_6m'] = self.zscore(self.compute_momentum(price, 126))
        all_factors[f'{symbol}_rsi'] = self.zscore(self.compute_rsi(price, 14)) / 100.0
        try:
            all_factors[f'{symbol}_macd'] = self.zscore(self.compute_macd(price))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    def _compute_technical_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                                   df: pd.DataFrame, price: pd.Series) -> None:
        """计算技术形态因子（v8.5 补充，价格类）"""
        try:
            all_factors[f'{symbol}_bollinger_z'] = self.zscore(
                self.compute_bollinger_zscore(price))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        if 'high' in df.columns and 'low' in df.columns:
            try:
                all_factors[f'{symbol}_awesome_osc'] = self.zscore(
                    self.compute_awesome_oscillator(df['high'], df['low']))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass
            try:
                all_factors[f'{symbol}_dual_thrust'] = self.compute_dual_thrust_position(
                    df['high'], df['low'], price)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass

    def _compute_safety_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                                price: pd.Series, returns: pd.Series,
                                market_returns: Optional[pd.Series]) -> None:
        """计算安全维度因子（价格类，始终计算）"""
        all_factors[f'{symbol}_max_dd_60'] = pd.Series(
            self.compute_max_dd(price.tail(60)), index=price.index
        )
        try:
            all_factors[f'{symbol}_var_95'] = pd.Series(
                self.compute_var(returns, 0.95), index=price.index
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            beta_val = self.compute_beta(returns, market_returns) if market_returns is not None else 1.0
            all_factors[f'{symbol}_beta'] = pd.Series(beta_val, index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            ivol_val = self.compute_ivol(returns, market_returns) if market_returns is not None else float(returns.std())
            all_factors[f'{symbol}_ivol'] = pd.Series(ivol_val, index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    def _compute_value_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                               price: pd.Series, fund: dict) -> None:
        """计算价值维度因子（基本面类）"""
        try:
            all_factors[f'{symbol}_pe'] = self.zscore(
                self.compute_pe(price, fund.get('eps', 0.01)))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_pb'] = self.zscore(
                self.compute_pb(price, fund.get('book_value_per_share', 0.01)))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_ps'] = self.zscore(
                self.compute_ps(price, fund.get('sales_per_share', 0.01)))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_fcf_yield'] = pd.Series(
                self.compute_fcf_yield(price, fund.get('fcf_per_share', 0.0)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_dividend_yield'] = pd.Series(
                self.compute_dividend_yield(price, fund.get('dps', 0.0)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    def _compute_quality_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                                 price: pd.Series, fund: dict) -> None:
        """计算质量维度因子（基本面类）"""
        try:
            all_factors[f'{symbol}_roe'] = pd.Series(
                self.compute_roe(fund.get('net_income', 0), fund.get('equity', 1)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_roic'] = pd.Series(
                self.compute_roic(fund.get('nopat', 0), fund.get('invested_capital', 1)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_gross_margin'] = pd.Series(
                self.compute_gross_margin(fund.get('revenue', 0), fund.get('cogs', 0)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_accruals'] = pd.Series(
                self.compute_accruals(fund.get('net_income', 0), fund.get('operating_cf', 0),
                                      fund.get('total_assets', 1)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_debt_ebitda'] = pd.Series(
                self.compute_debt_ebitda(fund.get('total_debt', 0), fund.get('ebitda', 0.01)), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    def _compute_growth_factors(self, all_factors: dict[str, pd.Series], symbol: str,
                                price: pd.Series, fund: dict) -> None:
        """计算增长维度因子（基本面类）"""
        try:
            rev_series = fund.get('revenue_series')
            if rev_series is not None:
                all_factors[f'{symbol}_rev_growth'] = pd.Series(
                    self.compute_rev_growth(rev_series), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            eps_series = fund.get('eps_series')
            if eps_series is not None:
                all_factors[f'{symbol}_eps_growth'] = pd.Series(
                    self.compute_eps_growth(eps_series), index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        try:
            all_factors[f'{symbol}_earnings_revision'] = pd.Series(
                self.compute_earnings_revision(fund.get('est_current', 0), fund.get('est_prior', 0)),
                index=price.index)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    # ---------- 批量因子计算 ----------
    def build_all_factors(self, price_data: dict[str, pd.DataFrame],
                          fundamentals: dict[str, dict] = None,
                          market_returns: Optional[pd.Series] = None) -> dict[str, pd.Series]:
        """对全部标的计算五维全部 18 因子（已激活）

        覆盖五大维度全部因子：
          - Value(5):     pe, pb, ps, fcf_yield, dividend_yield
          - Quality(5):   roe, roic, gross_margin, accruals, debt_ebitda
          - Momentum(4):  momentum_1m/3m/6m, rsi, macd
          - Growth(3):    rev_growth, eps_growth, earnings_revision
          - Safety(4):    beta, ivol, max_dd, var_95

        价格类因子(9个)始终计算；基本面因子(9个)在 fundamentals 提供时计算。
        market_returns 未提供时，用 price_data 等权指数作为市场代理（避免泄漏：
        仅用同一日截面均值，不含未来信息）。

        Args:
            price_data: {symbol: DataFrame[OHLCV]}
            fundamentals: {symbol: {fundamental data}}
            market_returns: 市场收益序列，未提供则构建等权代理

        Returns:
            {factor_name: pd.Series} 统一 indexed by date
        """
        fundamentals = fundamentals or {}
        all_factors = {}

        # ---- 第一遍: 构建等权市场收益代理（若未提供 market_returns）----
        if market_returns is None:
            market_returns = self._build_market_returns_proxy(price_data)

        # ---- 第二遍: 逐标的计算全部因子 ----
        for symbol, df in price_data.items():
            price = df.get('close', df[df.columns[0]] if len(df.columns) else df.iloc[:, 0])
            returns = price.pct_change().dropna()
            fund = fundamentals.get(symbol, {})

            # === Momentum 维度 (价格类, 始终计算) ===
            self._compute_momentum_factors(all_factors, symbol, price)

            # === 技术形态因子 (v8.5 补充, 价格类) ===
            self._compute_technical_factors(all_factors, symbol, df, price)

            # === Safety 维度 (价格类, 始终计算) ===
            self._compute_safety_factors(all_factors, symbol, price, returns, market_returns)

            # === Value/Quality/Growth 维度 (基本面类, fund 提供时计算) ===
            if fund:
                self._compute_value_factors(all_factors, symbol, price, fund)
                self._compute_quality_factors(all_factors, symbol, price, fund)
                self._compute_growth_factors(all_factors, symbol, price, fund)

            logger.debug(f"完成 {symbol} 因子计算: 累计 {len(all_factors)} 个因子")

        self._all_factors = all_factors
        return all_factors

    def get_factor_matrix(self) -> Optional[pd.DataFrame]:
        """将所有因子合并为 DataFrame"""
        if not self._all_factors:
            return None
        return pd.DataFrame(self._all_factors).dropna(how='all')
