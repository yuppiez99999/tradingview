# -*- coding: utf-8 -*-
"""
Alpha 因子库 (Alpha Factor Library)

世界顶级量化基金 Alpha 来源 (AQR / Renaissance / Two Sigma):
- 6 大类因子: 动量/价值/质量/低波/规模/流动性
- 50+ 单因子, 每类 8-10 个
- 因子预处理: 去极值/标准化/中性化
- 因子评价指标: IC / IC_IR / 因子收益率 / 换手率

参考:
- Fama-French 三因子/五因子模型
- Carhart 四因子 (加入动量)
- AQR "Style Investing in Fixed Income"
- Asness, A. et al. (2013) "Value and Momentum Everywhere"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorValue:
    """单个因子值"""

    name: str  # 因子名
    category: str  # 因子类别
    values: Dict[str, float]  # {symbol: factor_value}
    ic_1d: float = 0.0  # 1 日 IC
    ic_5d: float = 0.0  # 5 日 IC
    ic_20d: float = 0.0  # 20 日 IC
    ic_ir: float = 0.0  # IC 信息比率
    factor_return: float = 0.0  # 因子收益率 (年化)
    turnover: float = 0.0  # 因子换手率


@dataclass
class FactorLibraryResult:
    """因子库计算结果"""

    factors: Dict[str, FactorValue] = field(default_factory=dict)
    # 因子相关性矩阵
    factor_corr_matrix: Optional[pd.DataFrame] = None
    # 有效因子 (|IC| > 0.03)
    effective_factors: List[str] = field(default_factory=list)
    # 强因子 (|IC| > 0.05)
    strong_factors: List[str] = field(default_factory=list)


# ============================================================
# Alpha 因子库
# ============================================================


class AlphaFactorLibrary:
    """Alpha 因子库

    6 大类 50+ 因子:
    1. 动量类 (Momentum): 10 个
       - MOM_20D / MOM_60D / MOM_120D / MOM_252D
       - MOM_12_1M (12-1 月动量)
       - MOM_REVERSAL_5D (5 日反转)
       - MOM_REVERSAL_20D (20 日反转)
       - MOM_INDUSTRY_ADJ (行业调整动量)
       - MOM_VOLUME_ADJ (成交量调整动量)
       - MOM_UP_DOWN (上涨下跌日比)

    2. 价值类 (Value): 10 个
       - VAL_PE / VAL_PB / VAL_PS / VAL_PCF
       - VAL_EARNINGS_YIELD / VAL_BOOK_YIELD
       - VAL_DIVIDEND_YIELD / VAL_FCF_YIELD
       - VAL_EV_EBITDA / VAL_SALES_EV

    3. 质量类 (Quality): 8 个
       - QUA_ROE / QUA_ROA / QUA_ROIC
       - QUA_GROSS_MARGIN / QUA_NET_MARGIN
       - QUA_DEBT_TO_EQUITY / QUA_CURRENT_RATIO
       - QUA_ACCRUALS (应计利润)

    4. 低波类 (LowVolatility): 8 个
       - VOL_20D / VOL_60D / VOL_120D / VOL_252D
       - VOL_BETA (相对 Beta)
       - VOL_DOWNSIDE (下行波动)
       - VOL_IDIO (特质波动)
       - VOL_SKEW (偏度)

    5. 规模类 (Size): 7 个
       - SIZE_LOG_MCAP / SIZE_LOG_NS (流通市值)
       - SIZE_LOG_REV (营收规模)
       - SIZE_LOG_ASSETS
       - SIZE_SMALL_LARGE_RATIO (小盘/大盘比)
       - SIZE_NON_LINEAR (非线性市值)
       - SIZE_CUBIC (三次方市值)

    6. 流动性类 (Liquidity): 8 个
       - LIQ_TURNOVER_20D / LIQ_TURNOVER_60D
       - LIQ_AMIHUD (Amihud 非流动性)
       - LIQ_SPREAD (买卖价差)
       - LIQ_DEPT (深度)
       - LIQ_RSVP (周转率)
       - LIQ_ZERO_RET_DAYS (零收益天数)
       - LIQ_VOLUME_ZSCORE

    用法:
        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data={"600519": {"closes": [...], "volumes": [...]}},
            fundamentals={"600519": {"pe": 25.0, "pb": 5.0, "roe": 0.20, ...}},
        )
    """

    def __init__(self, neutralize_industry: bool = False, neutralize_size: bool = False):
        self.neutralize_industry = bool(neutralize_industry)
        self.neutralize_size = bool(neutralize_size)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def compute_all(
        self,
        price_data: Dict[str, Dict[str, List[float]]],
        fundamentals: Optional[Dict[str, Dict[str, float]]] = None,
        industries: Optional[Dict[str, str]] = None,
        benchmark_returns: Optional[List[float]] = None,
    ) -> FactorLibraryResult:
        """计算所有因子

        Args:
            price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
            fundamentals: {symbol: {"pe": ..., "pb": ..., "roe": ..., ...}}
            industries: {symbol: industry_name}
            benchmark_returns: 基准收益率序列 (计算 Beta)

        Returns:
            FactorLibraryResult
        """
        result = FactorLibraryResult()
        fundamentals = fundamentals or {}
        industries = industries or {}

        # 1. 动量因子
        momentum_factors = self._compute_momentum_factors(price_data)
        result.factors.update(momentum_factors)

        # 2. 价值因子
        value_factors = self._compute_value_factors(fundamentals)
        result.factors.update(value_factors)

        # 3. 质量因子
        quality_factors = self._compute_quality_factors(fundamentals)
        result.factors.update(quality_factors)

        # 4. 低波因子
        volatility_factors = self._compute_volatility_factors(price_data, benchmark_returns)
        result.factors.update(volatility_factors)

        # 5. 规模因子
        size_factors = self._compute_size_factors(fundamentals)
        result.factors.update(size_factors)

        # 6. 流动性因子
        liquidity_factors = self._compute_liquidity_factors(price_data)
        result.factors.update(liquidity_factors)

        # 中性化处理
        if self.neutralize_industry and industries:
            for fval in result.factors.values():
                fval.values = self._neutralize_by_industry(fval.values, industries)

        if self.neutralize_size and fundamentals:
            sizes = {s: float(f.get("market_cap", 0)) for s, f in fundamentals.items()}
            for fval in result.factors.values():
                fval.values = self._neutralize_by_size(fval.values, sizes)

        # 因子有效性评估
        self._evaluate_factors(result, price_data)

        # 因子相关性矩阵
        result.factor_corr_matrix = self._compute_factor_corr_matrix(result.factors)

        return result

    # ------------------------------------------------------------
    # 1. 动量因子
    # ------------------------------------------------------------

    def _compute_momentum_factors(
        self,
        price_data: Dict[str, Dict[str, List[float]]],
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        # MOM_20D / 60D / 120D / 252D
        for window, name in [(20, "MOM_20D"), (60, "MOM_60D"), (120, "MOM_120D"), (252, "MOM_252D")]:
            values = {}
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > window:
                    values[sym] = float(closes[-1] / closes[-window] - 1)
            factors[name] = FactorValue(name=name, category="Momentum", values=values)

        # MOM_12_1M (12-1 月动量)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 252:
                values[sym] = float(closes[-21] / closes[-252] - 1)
        factors["MOM_12_1M"] = FactorValue(name="MOM_12_1M", category="Momentum", values=values)

        # MOM_REVERSAL_5D / 20D
        for window, name in [(5, "MOM_REVERSAL_5D"), (20, "MOM_REVERSAL_20D")]:
            values = {}
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > window:
                    values[sym] = -float(closes[-1] / closes[-window] - 1)
            factors[name] = FactorValue(name=name, category="Momentum", values=values)

        # MOM_VOLUME_ADJ (成交量加权动量)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 60 and len(vols) > 60:
                ret_60 = closes[-1] / closes[-60] - 1
                vol_avg = np.mean(vols[-60:]) if vols else 1
                vol_ratio = vols[-1] / vol_avg if vol_avg > 0 else 1
                values[sym] = float(ret_60 * vol_ratio)
        factors["MOM_VOLUME_ADJ"] = FactorValue(name="MOM_VOLUME_ADJ", category="Momentum", values=values)

        # MOM_UP_DOWN (上涨下跌日比)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                rets = np.diff(closes[-21:])
                up_days = np.sum(rets > 0)
                down_days = np.sum(rets < 0)
                if down_days > 0:
                    values[sym] = float(up_days / down_days)
        factors["MOM_UP_DOWN"] = FactorValue(name="MOM_UP_DOWN", category="Momentum", values=values)

        # MOM_INDUSTRY_ADJ (后续中性化处理)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 60:
                values[sym] = float(closes[-1] / closes[-60] - 1)
        factors["MOM_INDUSTRY_ADJ"] = FactorValue(name="MOM_INDUSTRY_ADJ", category="Momentum", values=values)

        return factors

    # ------------------------------------------------------------
    # 2. 价值因子
    # ------------------------------------------------------------

    def _compute_value_factors(
        self,
        fundamentals: Dict[str, Dict[str, float]],
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        value_factors_spec = [
            ("VAL_PE", "pe", -1),  # 市盈率倒数
            ("VAL_PB", "pb", -1),  # 市净率倒数
            ("VAL_PS", "ps", -1),  # 市销率倒数
            ("VAL_PCF", "pcf", -1),  # 现金流收益率
            ("VAL_EARNINGS_YIELD", "earnings_yield", 1),
            ("VAL_BOOK_YIELD", "book_yield", 1),
            ("VAL_DIVIDEND_YIELD", "dividend_yield", 1),
            ("VAL_FCF_YIELD", "fcf_yield", 1),
            ("VAL_EV_EBITDA", "ev_ebitda", -1),
            ("VAL_SALES_EV", "sales_ev", -1),
        ]

        for name, fld, sign in value_factors_spec:
            values = {}
            for sym, fund in fundamentals.items():
                raw = fund.get(fld, 0)
                if raw and raw > 0:
                    values[sym] = float(sign * (1.0 / raw if sign < 0 else raw))
            factors[name] = FactorValue(name=name, category="Value", values=values)

        return factors

    # ------------------------------------------------------------
    # 3. 质量因子
    # ------------------------------------------------------------

    def _compute_quality_factors(
        self,
        fundamentals: Dict[str, Dict[str, float]],
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        quality_fields = [
            ("QUA_ROE", "roe"),
            ("QUA_ROA", "roa"),
            ("QUA_ROIC", "roic"),
            ("QUA_GROSS_MARGIN", "gross_margin"),
            ("QUA_NET_MARGIN", "net_margin"),
            ("QUA_DEBT_TO_EQUITY", "debt_to_equity"),  # 反向
            ("QUA_CURRENT_RATIO", "current_ratio"),
            ("QUA_ACCRUALS", "accruals"),  # 反向
        ]

        for name, fld in quality_fields:
            values = {}
            for sym, fund in fundamentals.items():
                raw = fund.get(fld, 0)
                if raw:
                    sign = -1 if "debt" in fld or "accrual" in fld else 1
                    values[sym] = float(sign * raw)
            factors[name] = FactorValue(name=name, category="Quality", values=values)

        return factors

    # ------------------------------------------------------------
    # 4. 低波因子
    # ------------------------------------------------------------

    def _compute_volatility_factors(
        self,
        price_data: Dict[str, Dict[str, List[float]]],
        benchmark_returns: Optional[List[float]] = None,
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        # VOL_20D / 60D / 120D / 252D
        for window, name in [(20, "VOL_20D"), (60, "VOL_60D"), (120, "VOL_120D"), (252, "VOL_252D")]:
            values = {}
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > window:
                    rets = np.diff(closes[-window - 1 :])
                    vol = float(np.std(rets) * np.sqrt(252))
                    if vol > 0:
                        values[sym] = -vol  # 反向: 低波 = 高分
            factors[name] = FactorValue(name=name, category="LowVolatility", values=values)

        # VOL_BETA
        values = {}
        if benchmark_returns and len(benchmark_returns) > 20:
            bench = np.array(benchmark_returns[-60:]) if len(benchmark_returns) >= 60 else np.array(benchmark_returns)
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > len(bench):
                    rets = np.diff(closes[-len(bench) - 1 :])
                    if len(rets) == len(bench):
                        cov = np.cov(rets, bench)[0, 1]
                        var_b = float(np.var(bench))
                        if var_b > 0:
                            values[sym] = -float(cov / var_b)  # 反向
        factors["VOL_BETA"] = FactorValue(name="VOL_BETA", category="LowVolatility", values=values)

        # VOL_DOWNSIDE (下行波动)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 60:
                rets = np.diff(closes[-61:])
                neg_rets = rets[rets < 0]
                if len(neg_rets) > 0:
                    values[sym] = -float(np.std(neg_rets) * np.sqrt(252))
        factors["VOL_DOWNSIDE"] = FactorValue(name="VOL_DOWNSIDE", category="LowVolatility", values=values)

        # VOL_IDIO (特质波动 = 残差波动)
        values = {}
        if benchmark_returns:
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > 60:
                    rets = np.diff(closes[-61:])
                    bench = (
                        np.array(benchmark_returns[-60:])
                        if len(benchmark_returns) >= 60
                        else np.array(benchmark_returns)
                    )
                    if len(rets) == len(bench):
                        beta = np.cov(rets, bench)[0, 1] / max(np.var(bench), 1e-10)
                        resid = rets - beta * bench
                        values[sym] = -float(np.std(resid) * np.sqrt(252))
        factors["VOL_IDIO"] = FactorValue(name="VOL_IDIO", category="LowVolatility", values=values)

        # VOL_SKEW (偏度)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 60:
                rets = np.diff(closes[-61:])
                if len(rets) > 2 and np.std(rets) > 0:
                    skew = float(((rets - np.mean(rets)) ** 3).mean() / np.std(rets) ** 3)
                    values[sym] = -skew  # 反向: 负偏更好
        factors["VOL_SKEW"] = FactorValue(name="VOL_SKEW", category="LowVolatility", values=values)

        return factors

    # ------------------------------------------------------------
    # 5. 规模因子
    # ------------------------------------------------------------

    def _compute_size_factors(
        self,
        fundamentals: Dict[str, Dict[str, float]],
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        size_fields = [
            ("SIZE_LOG_MCAP", "market_cap"),
            ("SIZE_LOG_NS", "negotiable_value"),
            ("SIZE_LOG_REV", "revenue"),
            ("SIZE_LOG_ASSETS", "total_assets"),
        ]

        for name, fld in size_fields:
            values = {}
            for sym, fund in fundamentals.items():
                raw = fund.get(fld, 0)
                if raw and raw > 0:
                    values[sym] = -float(np.log(raw))  # 反向: 小盘 = 高分
            factors[name] = FactorValue(name=name, category="Size", values=values)

        # SIZE_SMALL_LARGE_RATIO
        values = {}
        if fundamentals:
            mcaps = [float(f.get("market_cap", 0)) for f in fundamentals.values()]
            if mcaps:
                median_cap = np.median(mcaps)
                for sym, fund in fundamentals.items():
                    cap = float(fund.get("market_cap", 0))
                    if median_cap > 0 and cap > 0:
                        values[sym] = float(median_cap / cap)
        factors["SIZE_SMALL_LARGE_RATIO"] = FactorValue(name="SIZE_SMALL_LARGE_RATIO", category="Size", values=values)

        # SIZE_NON_LINEAR (非线性市值)
        values = {}
        for sym, fund in fundamentals.items():
            cap = float(fund.get("market_cap", 0))
            if cap > 0:
                log_cap = np.log(cap)
                # 非线性: 偏离中位数的立方
                values[sym] = -float(log_cap**3)
        factors["SIZE_NON_LINEAR"] = FactorValue(name="SIZE_NON_LINEAR", category="Size", values=values)

        # SIZE_CUBIC
        values = {}
        for sym, fund in fundamentals.items():
            cap = float(fund.get("market_cap", 0))
            if cap > 0:
                values[sym] = -float(cap ** (1 / 3))
        factors["SIZE_CUBIC"] = FactorValue(name="SIZE_CUBIC", category="Size", values=values)

        return factors

    # ------------------------------------------------------------
    # 6. 流动性因子
    # ------------------------------------------------------------

    def _compute_liquidity_factors(
        self,
        price_data: Dict[str, Dict[str, List[float]]],
    ) -> Dict[str, FactorValue]:
        factors: Dict[str, FactorValue] = {}

        # LIQ_TURNOVER_20D / 60D
        for window, name in [(20, "LIQ_TURNOVER_20D"), (60, "LIQ_TURNOVER_60D")]:
            values = {}
            for sym, data in price_data.items():
                vols = data.get("volumes", [])
                if len(vols) > window:
                    avg_vol = float(np.mean(vols[-window:]))
                    if avg_vol > 0:
                        values[sym] = -avg_vol  # 反向: 低换手 = 高分
            factors[name] = FactorValue(name=name, category="Liquidity", values=values)

        # LIQ_AMIHUD (Amihud 非流动性)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 20 and len(vols) > 20:
                rets = np.abs(np.diff(closes[-21:]))
                vols_20 = np.array(vols[-20:], dtype=float)
                vols_20[vols_20 == 0] = 1e-10
                illiq = float(np.mean(rets / vols_20))
                values[sym] = illiq  # 高 = 流动性差 = 低分
        factors["LIQ_AMIHUD"] = FactorValue(name="LIQ_AMIHUD", category="Liquidity", values=values)

        # LIQ_SPREAD (买卖价差近似: high-low / close)
        values = {}
        for sym, data in price_data.items():
            highs = data.get("highs", [])
            lows = data.get("lows", [])
            closes = data.get("closes", [])
            if len(highs) > 20 and len(lows) > 20 and len(closes) > 20:
                spreads = []
                for i in range(-20, 0):
                    if closes[i] > 0:
                        spreads.append((highs[i] - lows[i]) / closes[i])
                if spreads:
                    values[sym] = -float(np.mean(spreads))
        factors["LIQ_SPREAD"] = FactorValue(name="LIQ_SPREAD", category="Liquidity", values=values)

        # LIQ_DEPTH (深度: 平均成交量)
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) > 20:
                values[sym] = -float(np.mean(vols[-20:]))  # 反向
        factors["LIQ_DEPTH"] = FactorValue(name="LIQ_DEPTH", category="Liquidity", values=values)

        # LIQ_RSVP (周转率)
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            closes = data.get("closes", [])
            if len(vols) > 20 and len(closes) > 20:
                avg_amount = float(np.mean(np.array(vols[-20:]) * np.array(closes[-20:])))
                if avg_amount > 0:
                    values[sym] = -avg_amount  # 反向
        factors["LIQ_RSVP"] = FactorValue(name="LIQ_RSVP", category="Liquidity", values=values)

        # LIQ_ZERO_RET_DAYS (零收益天数)
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                rets = np.diff(closes[-21:])
                zero_days = np.sum(np.abs(rets) < 1e-6)
                values[sym] = float(zero_days)  # 高 = 流动性差
        factors["LIQ_ZERO_RET_DAYS"] = FactorValue(name="LIQ_ZERO_RET_DAYS", category="Liquidity", values=values)

        # LIQ_VOLUME_ZSCORE
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) > 60:
                vols_60 = np.array(vols[-60:])
                z = float((vols[-1] - np.mean(vols_60)) / max(np.std(vols_60), 1e-10))
                values[sym] = -z  # 反向
        factors["LIQ_VOLUME_ZSCORE"] = FactorValue(name="LIQ_VOLUME_ZSCORE", category="Liquidity", values=values)

        return factors

    # ------------------------------------------------------------
    # 因子预处理
    # ------------------------------------------------------------

    def _winsorize(self, values: Dict[str, float], n_sigma: float = 3.0) -> Dict[str, float]:
        """去极值 (MAD 法)"""
        if not values:
            return values
        arr = np.array(list(values.values()))
        median = np.median(arr)
        mad = np.median(np.abs(arr - median))
        if mad > 0:
            upper = median + n_sigma * 1.4826 * mad
            lower = median - n_sigma * 1.4826 * mad
            return {k: float(np.clip(v, lower, upper)) for k, v in values.items()}
        return values

    def _standardize(self, values: Dict[str, float]) -> Dict[str, float]:
        """Z-score 标准化"""
        if not values:
            return values
        arr = np.array(list(values.values()))
        if np.std(arr) > 0:
            mean, std = float(np.mean(arr)), float(np.std(arr))
            return {k: float((v - mean) / std) for k, v in values.items()}
        return values

    def _neutralize_by_industry(
        self,
        values: Dict[str, float],
        industries: Dict[str, str],
    ) -> Dict[str, float]:
        """行业中性化"""
        # 按行业分组, 各组内做去均值
        industry_groups: Dict[str, List[float]] = {}
        for sym, ind in industries.items():
            if sym in values:
                industry_groups.setdefault(ind, []).append(values[sym])

        industry_means = {ind: float(np.mean(vs)) for ind, vs in industry_groups.items() if vs}

        return {sym: float(values[sym] - industry_means.get(industries.get(sym, ""), 0)) for sym in values}

    def _neutralize_by_size(
        self,
        values: Dict[str, float],
        sizes: Dict[str, float],
    ) -> Dict[str, float]:
        """规模中性化 (回归残差)"""
        common_syms = set(values.keys()) & set(sizes.keys())
        if len(common_syms) < 3:
            return values

        x = np.array([sizes[s] for s in common_syms])
        y = np.array([values[s] for s in common_syms])

        if np.std(x) > 0:
            # 简单线性回归
            slope = np.cov(x, y)[0, 1] / max(np.var(x), 1e-10)
            intercept = float(np.mean(y) - slope * np.mean(x))
            residuals = y - (slope * x + intercept)
            return {s: float(r) for s, r in zip(common_syms, residuals)}

        return values

    # ------------------------------------------------------------
    # 因子评价
    # ------------------------------------------------------------

    def _evaluate_factors(
        self,
        result: FactorLibraryResult,
        price_data: Dict[str, Dict[str, List[float]]],
    ) -> None:
        """评估因子有效性"""
        for name, fval in result.factors.items():
            if not fval.values:
                continue
            # 计算 5 日 IC (Spearman 相关)
            ic_5d = self._calc_ic(fval.values, price_data, 5)
            fval.ic_5d = ic_5d

            if abs(ic_5d) > 0.05:
                result.strong_factors.append(name)
            elif abs(ic_5d) > 0.03:
                result.effective_factors.append(name)

    def _calc_ic(
        self,
        factor_values: Dict[str, float],
        price_data: Dict[str, Dict[str, List[float]]],
        forward_days: int,
    ) -> float:
        """计算 IC (Spearman rank correlation)"""
        try:
            from scipy.stats import spearmanr

            factor_list = []
            ret_list = []
            for sym, fv in factor_values.items():
                closes = price_data.get(sym, {}).get("closes", [])
                if len(closes) > forward_days:
                    fwd_ret = closes[-1] / closes[-forward_days] - 1
                    factor_list.append(fv)
                    ret_list.append(fwd_ret)

            if len(factor_list) < 5:
                return 0.0
            corr, _ = spearmanr(factor_list, ret_list)
            return float(corr) if not np.isnan(corr) else 0.0
        except (ImportError, Exception):
            return 0.0

    # ------------------------------------------------------------
    # 因子相关性矩阵
    # ------------------------------------------------------------

    def _compute_factor_corr_matrix(
        self,
        factors: Dict[str, FactorValue],
    ) -> Optional[pd.DataFrame]:
        """计算因子间相关性矩阵"""
        if not factors:
            return None
        try:
            df = pd.DataFrame({name: pd.Series(fv.values) for name, fv in factors.items()})
            return df.corr()
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            return None
