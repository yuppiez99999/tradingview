"""价量因子模块 — 动量/低波/规模/流动性 4 大类

国泰海通研报风格命名 (MOM_/VOL_/SIZE_/LIQ_ 前缀), 共 33 个因子。

参考:
- 国泰君安《权益配置因子研究系列05》: 市值因子为A股最强风格因子
- Carhart (1997) 四因子模型 (加入动量)
- Amihud (2002) 非流动性度量
"""

from __future__ import annotations

import numpy as np

from utils.alpha_factor.base import (
    FactorValue,
    neutralize_by_industry,
    orthogonalize,
    residualize,
)


def _residualize_against(
    target: dict[str, float], anchor: dict[str, float]
) -> dict[str, float]:
    """对 target 关于 anchor 做截面回归取残差 (解耦共线)

    已迁移到 base.residualize, 此处保留薄包装以兼容已有调用。
    """
    return residualize(target, anchor)


# ============================================================
# 1. 动量因子 (Momentum) — 10 个
# ============================================================


def compute_momentum_factors(
    price_data: dict[str, dict[str, list[float]]],
    industries: dict[str, str] | None = None,
) -> dict[str, FactorValue]:
    """动量类因子 10 个

    MOM_20D/60D/120D/252D: 多窗口动量
    MOM_12_1M: 12-1 月动量 (剔除最近一月, Jegadeesh-Titman 经典)
    MOM_REVERSAL_5D/20D: 短期反转
    MOM_VOLUME_ADJ: 成交量加权动量
    MOM_UP_DOWN: 上涨下跌日比
    MOM_INDUSTRY_ADJ: 行业调整动量 (个股 - 同行业均值)
    """
    factors: dict[str, FactorValue] = {}

    # MOM_20D / 60D / 120D / 252D
    for window, name in [(20, "MOM_20D"), (60, "MOM_60D"), (120, "MOM_120D"), (252, "MOM_252D")]:
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > window:
                values[sym] = float(closes[-1] / closes[-window] - 1)
        factors[name] = FactorValue(name=name, category="Momentum", values=values)

    # MOM_12_1M (12-1 月动量, 经典 Jegadeesh-Titman)
    # 对 MOM_252D 正交化: 剔除最近一月的长期动量, 保留 "动量延续性" 残差
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 252:
            values[sym] = float(closes[-21] / closes[-252] - 1)
    if factors.get("MOM_252D") and factors["MOM_252D"].values:
        values = _residualize_against(values, factors["MOM_252D"].values)
    factors["MOM_12_1M"] = FactorValue(name="MOM_12_1M", category="Momentum", values=values)

    # MOM_REVERSAL_5D: 5 日反转 (短窗口, 与长窗口动量相关性低)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 5:
            values[sym] = -float(closes[-1] / closes[-5] - 1)
    factors["MOM_REVERSAL_5D"] = FactorValue(name="MOM_REVERSAL_5D", category="Momentum", values=values)

    # MOM_REVERSAL_20D: 20 日收益率在 120 日窗口中的时序分位数 (与 MOM_20D 解耦)
    # 高值 = 当前 20 日涨幅处于历史高位 (反转下跌风险)
    # 低值 = 当前 20 日涨幅处于历史低位 (反转上涨机会)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 120:
            # 滚动 20 日收益率序列
            ret20_series = np.array([
                closes[i] / closes[i - 20] - 1.0
                for i in range(20, len(closes))
            ])
            if len(ret20_series) >= 20:
                current = ret20_series[-1]
                rank = float(np.sum(ret20_series <= current) / len(ret20_series))
                values[sym] = rank  # 0~1, 高=超买
    factors["MOM_REVERSAL_20D"] = FactorValue(name="MOM_REVERSAL_20D", category="Momentum", values=values)

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

    # MOM_UP_DOWN: 上涨下跌日收益差 (与 GTJA_006 上涨天数占比解耦)
    # GTJA_006 度量 "上涨天数占比" (0-1), MOM_UP_DOWN 度量 "上涨日收益 - 下跌日收益"
    # 高值 = 上涨日幅度大于下跌日 (趋势健康), 低值 = 下跌日幅度更大 (恐慌)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 21:
            rets = np.diff(closes[-21:])
            up_rets = rets[rets > 0]
            down_rets = rets[rets < 0]
            if len(up_rets) > 0 and len(down_rets) > 0:
                values[sym] = float(np.mean(up_rets) - np.mean(np.abs(down_rets)))
    factors["MOM_UP_DOWN"] = FactorValue(name="MOM_UP_DOWN", category="Momentum", values=values)

    # MOM_INDUSTRY_ADJ (行业调整动量: 个股 60 日动量 - 同行业均值)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 60:
            values[sym] = float(closes[-1] / closes[-60] - 1)
    if industries:
        values = neutralize_by_industry(values, industries)
    factors["MOM_INDUSTRY_ADJ"] = FactorValue(name="MOM_INDUSTRY_ADJ", category="Momentum", values=values)

    return factors


# ============================================================
# 2. 低波因子 (LowVolatility) — 8 个
# ============================================================


def compute_volatility_factors(
    price_data: dict[str, dict[str, list[float]]],
    benchmark_returns: list[float] | None = None,
) -> dict[str, FactorValue]:
    """低波类因子 8 个 (反向: 低波 = 高分)

    多窗口波动率高度共线 (VOL_20D/60D/120D/252D 之间 ρ>0.95), 故:
    - 保留 VOL_20D (短) / VOL_120D (中) 作为基础窗口
    - VOL_60D  → 对 VOL_20D 正交化残差 (中期额外波动)
    - VOL_252D → 改为期限结构 VOL_252D - VOL_120D (长-中, 可正可负)
    - VOL_DOWNSIDE → 改为下行波动占比 downside_vol/total_vol (与总波动解耦)
    - VOL_IDIO → 对 VOL_60D 正交化残差 (确保真正残差, demo 中 beta≈0 时不再等于 VOL)
    - VOL_BETA / VOL_SKEW 保留 (与波动率水平天然解耦)
    """
    factors: dict[str, FactorValue] = {}

    # VOL_20D (基础短期波动率)
    vol_20d: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 20:
            rets = np.diff(closes[-21:])
            vol = float(np.std(rets) * np.sqrt(252))
            if vol > 0:
                vol_20d[sym] = -vol  # 反向: 低波 = 高分
    factors["VOL_20D"] = FactorValue(name="VOL_20D", category="LowVolatility", values=vol_20d)

    # VOL_120D: 长期波动率, 对 VOL_20D 正交化 (保留长期波动中独立于短期的部分)
    vol_120d_raw: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 120:
            rets = np.diff(closes[-121:])
            vol = float(np.std(rets) * np.sqrt(252))
            if vol > 0:
                vol_120d_raw[sym] = -vol
    vol_120d = _residualize_against(vol_120d_raw, vol_20d)
    factors["VOL_120D"] = FactorValue(name="VOL_120D", category="LowVolatility", values=vol_120d)

    # VOL_60D: 对 VOL_20D 正交化残差 (保留中期波动中独立于短期的部分)
    vol_60d_raw: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 60:
            rets = np.diff(closes[-61:])
            vol = float(np.std(rets) * np.sqrt(252))
            if vol > 0:
                vol_60d_raw[sym] = -vol
    vol_60d_resid = _residualize_against(vol_60d_raw, vol_20d)
    factors["VOL_60D"] = FactorValue(name="VOL_60D", category="LowVolatility", values=vol_60d_resid)

    # VOL_252D: 期限结构 = 长期波动率 - 中期波动率 (可正可负, 与波动率水平解耦)
    # 高值 = 长期波动 > 中期 (波动率上行趋势), 低值 = 波动率下行趋势
    values: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 252:
            rets_252 = np.diff(closes[-253:])
            rets_120 = np.diff(closes[-121:])
            v252 = float(np.std(rets_252) * np.sqrt(252))
            v120 = float(np.std(rets_120) * np.sqrt(252))
            if v252 > 0 and v120 > 0:
                values[sym] = -(v252 - v120)  # 反向: 长期波动率上行 = 风险增加 = 低分
    factors["VOL_252D"] = FactorValue(name="VOL_252D", category="LowVolatility", values=values)

    # VOL_BETA (市场敏感度, 反向: 低 beta = 高分)
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
                        values[sym] = -float(cov / var_b)
    factors["VOL_BETA"] = FactorValue(name="VOL_BETA", category="LowVolatility", values=values)

    # VOL_DOWNSIDE: 下行波动占比 = downside_vol / total_vol (与总波动水平解耦)
    # 高值 = 下行波动主导 (左尾风险大), 低值 = 上行波动主导
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 60:
            rets = np.diff(closes[-61:])
            neg_rets = rets[rets < 0]
            total_std = float(np.std(rets))
            if len(neg_rets) > 0 and total_std > 0:
                downside_std = float(np.std(neg_rets))
                values[sym] = -float(downside_std / total_std)  # 反向: 低下行占比 = 高分
    factors["VOL_DOWNSIDE"] = FactorValue(name="VOL_DOWNSIDE", category="LowVolatility", values=values)

    # VOL_IDIO: 特质波动, 同时对 VOL_20D 和 VOL_120D_raw 正交化 (确保与所有波动率窗口解耦)
    # demo 中 benchmark 为随机噪声时 beta≈0, resid≈rets, 故必须做残差化
    idio_raw: dict[str, float] = {}
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
                    idio_raw[sym] = -float(np.std(resid) * np.sqrt(252))
    # 双重残差化: 对 VOL_20D 和 VOL_120D_raw (原始长期波动率)
    idio_resid = _residualize_against(idio_raw, vol_20d)
    idio_resid = _residualize_against(idio_resid, vol_120d_raw)
    factors["VOL_IDIO"] = FactorValue(name="VOL_IDIO", category="LowVolatility", values=idio_resid)

    # VOL_SKEW (偏度, 反向: 负偏更好)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 60:
            rets = np.diff(closes[-61:])
            if len(rets) > 2 and np.std(rets) > 0:
                skew = float(((rets - np.mean(rets)) ** 3).mean() / np.std(rets) ** 3)
                values[sym] = -skew
    factors["VOL_SKEW"] = FactorValue(name="VOL_SKEW", category="LowVolatility", values=values)

    return factors


# ============================================================
# 3. 规模因子 (Size) — 7 个
# ============================================================


def compute_size_factors(
    fundamentals: dict[str, dict[str, float]],
) -> dict[str, FactorValue]:
    """规模类因子 7 个 (反向: 小盘 = 高分)

    多个对数规模因子高度共线 (LOG_MCAP/LOG_NS/LOG_REV ρ>0.95), 故:
    - SIZE_LOG_MCAP: 总市值 (基础规模因子)
    - SIZE_LOG_NS  → 改为流通比率 log(negotiable_value/market_cap) (与规模解耦)
    - SIZE_LOG_REV → 对 SIZE_LOG_MCAP 正交化残差 (营收规模中独立于市值的部分)
    - SIZE_LOG_ASSETS → 保留 (资产规模, 概念不同)
    - SIZE_SMALL_LARGE_RATIO → 对 SIZE_LOG_MCAP 正交化残差 (中盘溢酬)
    - SIZE_NON_LINEAR: 中盘因子 (V型 + 正交化, 与 LOG_MCAP 解耦)
    - SIZE_CUBIC: 规模分布偏度 (log(mcap)^3 对 log(mcap) 正交化残差)
    """
    factors: dict[str, FactorValue] = {}

    # SIZE_LOG_MCAP (基础规模因子)
    log_mcap: dict[str, float] = {}
    for sym, fund in fundamentals.items():
        raw = fund.get("market_cap", 0)
        if raw and raw > 0:
            log_mcap[sym] = -float(np.log(raw))  # 反向: 小盘 = 高分
    factors["SIZE_LOG_MCAP"] = FactorValue(name="SIZE_LOG_MCAP", category="Size", values=log_mcap)

    # SIZE_LOG_NS → 流通比率 (与规模解耦)
    # 高值 = 低流通占比 (锁定筹码多, 易操纵); 低值 = 高流通 (筹码分散)
    values = {}
    for sym, fund in fundamentals.items():
        ns = fund.get("negotiable_value", 0)
        mc = fund.get("market_cap", 0)
        if ns and mc and ns > 0 and mc > 0:
            ratio = ns / mc
            if 0 < ratio <= 1.0:
                values[sym] = -float(np.log(ratio))  # 反向: 低流通 = 高分 (稀有溢酬)
    factors["SIZE_LOG_NS"] = FactorValue(name="SIZE_LOG_NS", category="Size", values=values)

    # SIZE_LOG_REV: 营收规模, 对 SIZE_LOG_MCAP 正交化 (营收中独立于市值的部分)
    rev_raw: dict[str, float] = {}
    for sym, fund in fundamentals.items():
        raw = fund.get("revenue", 0)
        if raw and raw > 0:
            rev_raw[sym] = -float(np.log(raw))
    rev_resid = _residualize_against(rev_raw, log_mcap)
    factors["SIZE_LOG_REV"] = FactorValue(name="SIZE_LOG_REV", category="Size", values=rev_resid)

    # SIZE_LOG_ASSETS (资产规模, 概念独立保留)
    values = {}
    for sym, fund in fundamentals.items():
        raw = fund.get("total_assets", 0)
        if raw and raw > 0:
            values[sym] = -float(np.log(raw))
    factors["SIZE_LOG_ASSETS"] = FactorValue(name="SIZE_LOG_ASSETS", category="Size", values=values)

    # SIZE_SMALL_LARGE_RATIO: 对 SIZE_LOG_MCAP 正交化 (中盘溢酬, 与规模解耦)
    slr_raw: dict[str, float] = {}
    if fundamentals:
        mcaps = [float(f.get("market_cap", 0)) for f in fundamentals.values()]
        if mcaps:
            median_cap = float(np.median(mcaps))
            for sym, fund in fundamentals.items():
                cap = float(fund.get("market_cap", 0))
                if median_cap > 0 and cap > 0:
                    slr_raw[sym] = float(median_cap / cap)
    slr_resid = _residualize_against(slr_raw, log_mcap)
    factors["SIZE_SMALL_LARGE_RATIO"] = FactorValue(name="SIZE_SMALL_LARGE_RATIO", category="Size", values=slr_resid)

    # SIZE_NON_LINEAR (中盘因子: V型 + 对 log(mcap) 正交化)
    values = {}
    log_caps = {
        sym: float(np.log(fund.get("market_cap", 0)))
        for sym, fund in fundamentals.items()
        if fund.get("market_cap", 0) and fund.get("market_cap", 0) > 0
    }
    if log_caps:
        median_log_cap = float(np.median(list(log_caps.values())))
        raw = {sym: -abs(lc - median_log_cap) for sym, lc in log_caps.items()}
        values = orthogonalize(raw, log_caps) if len(raw) >= 3 else raw
    factors["SIZE_NON_LINEAR"] = FactorValue(name="SIZE_NON_LINEAR", category="Size", values=values)

    # SIZE_CUBIC (规模分布偏度: log(mcap)^3 对 log(mcap) 正交化)
    # 设计意图: 捕获市值分布的非线性/偏度特征, 而非规模水平本身。
    # 旧设计 "市值对资产残差" 在 market_cap 与 total_assets 独立时退化为 log(mcap), 与 LOG_MCAP 共线。
    # 改为立方项残差: (log mcap)^3 对 log mcap 回归取残差, 保留高阶非线性信息。
    values = {}
    cubic_raw: dict[str, float] = {}
    for sym, lc in log_caps.items():
        cubic_raw[sym] = float(lc ** 3)
    if len(cubic_raw) >= 3:
        values = orthogonalize(cubic_raw, log_caps)
    factors["SIZE_CUBIC"] = FactorValue(name="SIZE_CUBIC", category="Size", values=values)

    return factors


# ============================================================
# 4. 流动性因子 (Liquidity) — 8 个
# ============================================================


def compute_liquidity_factors(
    price_data: dict[str, dict[str, list[float]]],
) -> dict[str, FactorValue]:
    """流动性类因子 8 个 (反向: 低流动性 = 高分)"""
    factors: dict[str, FactorValue] = {}

    # LIQ_TURNOVER_20D / 60D
    # 60D 对 20D 正交化: 保留长期换手率中独立于短期的部分 (长期流动性趋势)
    turnover_20d: dict[str, float] = {}
    turnover_60d_raw: dict[str, float] = {}
    for window, name, sink in [(20, "LIQ_TURNOVER_20D", turnover_20d), (60, "LIQ_TURNOVER_60D", turnover_60d_raw)]:
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) > window:
                avg_vol = float(np.mean(vols[-window:]))
                if avg_vol > 0:
                    values[sym] = -avg_vol
                    sink[sym] = -avg_vol
        if name == "LIQ_TURNOVER_20D":
            factors[name] = FactorValue(name=name, category="Liquidity", values=values)
        else:
            # 60D 对 20D 残差化, 消除窗口重叠导致的共线 (ρ>0.99 → 预期 <0.5)
            resid = residualize(turnover_60d_raw, turnover_20d) if len(turnover_20d) >= 3 else turnover_60d_raw
            factors[name] = FactorValue(name=name, category="Liquidity", values=resid)

    # LIQ_AMIHUD (Amihud 非流动性, 对 VOL_20D 正交化以解耦波动率影响)
    # 原始 Amihud = mean(|ret|/vol) 与波动率强相关 (ρ≈-0.95), 正交化后保留 "单位成交额价格冲击" 残差
    amihud_raw: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        vols = data.get("volumes", [])
        if len(closes) > 20 and len(vols) > 20:
            rets = np.abs(np.diff(closes[-21:]))
            vols_20 = np.array(vols[-20:], dtype=float)
            vols_20[vols_20 == 0] = 1e-10
            illiq = float(np.mean(rets / vols_20))
            amihud_raw[sym] = illiq
    # 需要从 volatility_factors 获取 VOL_20D, 但此处无法直接访问
    # 改为在 library.py 的后处理中统一正交化 (见 compute_all 末尾)
    factors["LIQ_AMIHUD"] = FactorValue(name="LIQ_AMIHUD", category="Liquidity", values=amihud_raw)

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

    # LIQ_DEPTH (深度稳定性: 60 日成交量变异系数 CV, 与绝对水平解耦)
    values = {}
    for sym, data in price_data.items():
        vols = data.get("volumes", [])
        if len(vols) > 20:
            window = np.array(vols[-60:] if len(vols) >= 60 else vols[-20:], dtype=float)
            mean_vol = float(np.mean(window))
            if mean_vol > 0:
                cv = float(np.std(window) / mean_vol)
                values[sym] = -cv
    factors["LIQ_DEPTH"] = FactorValue(name="LIQ_DEPTH", category="Liquidity", values=values)

    # LIQ_RSVP: 5 日均成交额 / 60 日均成交额 - 1 (中期成交额趋势, 与单点 z-score 解耦)
    # 与 LIQ_VOLUME_ZSCORE 区分: RSVP 用 5 日均值 (平滑), ZSCORE 用单点 (敏感)
    values = {}
    for sym, data in price_data.items():
        vols = data.get("volumes", [])
        closes = data.get("closes", [])
        if len(vols) > 60 and len(closes) > 60:
            amt_5 = float(np.mean(np.array(vols[-5:]) * np.array(closes[-5:])))
            amt_60 = float(np.mean(np.array(vols[-60:]) * np.array(closes[-60:])))
            if amt_60 > 0:
                values[sym] = float(amt_5 / amt_60 - 1.0)
    factors["LIQ_RSVP"] = FactorValue(name="LIQ_RSVP", category="Liquidity", values=values)

    # LIQ_ZERO_RET_DAYS (零收益天数)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 20:
            rets = np.diff(closes[-21:])
            zero_days = np.sum(np.abs(rets) < 1e-6)
            values[sym] = float(zero_days)
    factors["LIQ_ZERO_RET_DAYS"] = FactorValue(name="LIQ_ZERO_RET_DAYS", category="Liquidity", values=values)

    # LIQ_VOLUME_ZSCORE
    values = {}
    for sym, data in price_data.items():
        vols = data.get("volumes", [])
        if len(vols) > 60:
            vols_60 = np.array(vols[-60:])
            z = float((vols[-1] - np.mean(vols_60)) / max(np.std(vols_60), 1e-10))
            values[sym] = -z
    factors["LIQ_VOLUME_ZSCORE"] = FactorValue(name="LIQ_VOLUME_ZSCORE", category="Liquidity", values=values)

    return factors
