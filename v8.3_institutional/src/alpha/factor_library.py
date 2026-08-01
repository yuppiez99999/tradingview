"""
v7.5 FactorLibrary — 五维因子库：价值 / 质量 / 动量 / 增长 / 安全
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §6 (src/alpha/)
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FactorGroup:
    """单组因子"""

    name: str
    factors: Dict[str, pd.Series] = field(default_factory=dict)
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
        self.factor_groups: Dict[str, FactorGroup] = {
            "value": FactorGroup(name="value"),
            "quality": FactorGroup(name="quality"),
            "momentum": FactorGroup(name="momentum"),
            "growth": FactorGroup(name="growth"),
            "safety": FactorGroup(name="safety"),
        }
        self._all_factors: Dict[str, pd.Series] = {}

    # ---------- Value 因子 ----------
    def compute_pe(self, price: pd.Series, earnings_per_share: float) -> pd.Series:
        return price / max(earnings_per_share, 0.01)

    def compute_pb(self, price: pd.Series, book_value_per_share: float) -> pd.Series:
        return price / max(book_value_per_share, 0.01)

    def compute_ps(self, price: pd.Series, sales_per_share: float) -> pd.Series:
        return price / max(sales_per_share, 0.01)

    def compute_fcf_yield(self, price: pd.Series, fcf_per_share: float) -> pd.Series:
        last_price = price.iloc[-1] if len(price) > 0 else 0.0
        return fcf_per_share / max(float(last_price), 0.01)

    def compute_dividend_yield(self, price: pd.Series, dps: float) -> pd.Series:
        last_price = price.iloc[-1] if len(price) > 0 else 0.0
        return dps / max(float(last_price), 0.01)

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
        cov = np.cov(returns.loc[common].values[-window:], market_returns.loc[common].values[-window:])[0, 1]
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
        except Exception:
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

    # ---------- 批量因子计算 ----------
    def build_all_factors(
        self, price_data: Dict[str, pd.DataFrame], fundamentals: Optional[Dict[str, dict]] = None
    ) -> Dict[str, pd.Series]:
        """
        对全部标的计算五维因子

        Args:
            price_data: {symbol: DataFrame[OHLCV]}
            fundamentals: {symbol: {fundamental data}}

        Returns:
            {factor_name: pd.Series} 统一 indexed by date
        """
        fundamentals = fundamentals or {}
        all_factors = {}

        for symbol, df in price_data.items():
            price = df.get("close", df[df.columns[0]])
            price.pct_change().dropna()
            fund = fundamentals.get(symbol, {})

            # --- Value ---
            all_factors[f"{symbol}_momentum_1m"] = self.zscore(self.compute_momentum(price, 21))
            all_factors[f"{symbol}_momentum_3m"] = self.zscore(self.compute_momentum(price, 63))
            all_factors[f"{symbol}_momentum_6m"] = self.zscore(self.compute_momentum(price, 126))

            # --- Momentum Technical ---
            all_factors[f"{symbol}_rsi"] = self.zscore(self.compute_rsi(price, 14)) / 100.0

            # --- Safety ---
            all_factors[f"{symbol}_max_dd_60"] = pd.Series(self.compute_max_dd(price.tail(60)), index=price.index)

            # --- 基本面因子（低频）---
            if fund:
                pe = self.compute_pe(price, fund.get("eps", 0.01))
                all_factors[f"{symbol}_pe"] = self.zscore(pe)

                roe_val = self.compute_roe(fund.get("net_income", 0), fund.get("equity", 1))
                all_factors[f"{symbol}_roe"] = pd.Series(roe_val, index=price.index)

            logger.debug(f"完成 {symbol} 因子计算: {len(all_factors)} 个因子")

        self._all_factors = all_factors
        return all_factors

    def get_factor_matrix(self) -> Optional[pd.DataFrame]:
        """将所有因子合并为 DataFrame"""
        if not self._all_factors:
            return None
        return pd.DataFrame(self._all_factors).dropna(how="all")
