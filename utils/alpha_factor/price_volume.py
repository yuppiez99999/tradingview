"""价量因子模块 — 动量/低波/规模/流动性 4 大类

国泰海通研报风格命名 (MOM_/VOL_/SIZE_/LIQ_ 前缀), 共 33 个因子。

参考:
- 国泰君安《权益配置因子研究系列05》: 市值因子为A股最强风格因子
- Carhart (1997) 四因子模型 (加入动量)
- Amihud (2002) 非流动性度量
"""

from __future__ import annotations

from typing import Any

import numpy as np

from utils.alpha_factor.base import (
    FactorValue,
    neutralize_by_industry,
    orthogonalize,
    register_factor,  # Wave 6 W6.1.3: EigenAlpha 风格装饰器
    residualize,
)


def _residualize_against(target: dict[str, float], anchor: dict[str, float]) -> dict[str, float]:
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
    for window, name in [
        (20, "MOM_20D"),
        (60, "MOM_60D"),
        (120, "MOM_120D"),
        (252, "MOM_252D"),
    ]:
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
            ret20_series = np.array([closes[i] / closes[i - 20] - 1.0 for i in range(20, len(closes))])
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
                    np.array(benchmark_returns[-60:]) if len(benchmark_returns) >= 60 else np.array(benchmark_returns)
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
        # raw 在该大函数前部已被绑定为 float (market_cap 值), 此处独立命名避免类型污染
        log_cap_raw = {
            sym: -abs(lc - median_log_cap) for sym, lc in log_caps.items()
        }
        values = (
            orthogonalize(log_cap_raw, log_caps)
            if len(log_cap_raw) >= 3
            else log_cap_raw
        )
    factors["SIZE_NON_LINEAR"] = FactorValue(name="SIZE_NON_LINEAR", category="Size", values=values)

    # SIZE_CUBIC (规模分布偏度: log(mcap)^3 对 log(mcap) 正交化)
    # 设计意图: 捕获市值分布的非线性/偏度特征, 而非规模水平本身。
    # 旧设计 "市值对资产残差" 在 market_cap 与 total_assets 独立时退化为 log(mcap), 与 LOG_MCAP 共线。
    # 改为立方项残差: (log mcap)^3 对 log mcap 回归取残差, 保留高阶非线性信息。
    values = {}
    cubic_raw: dict[str, float] = {}
    for sym, lc in log_caps.items():
        cubic_raw[sym] = float(lc**3)
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
    for window, name, sink in [
        (20, "LIQ_TURNOVER_20D", turnover_20d),
        (60, "LIQ_TURNOVER_60D", turnover_60d_raw),
    ]:
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
            # window 在前部因子被绑定为 int (窗口天数), 此处独立命名避免类型污染
            vol_window = np.array(
                vols[-60:] if len(vols) >= 60 else vols[-20:], dtype=float
            )
            mean_vol = float(np.mean(vol_window))
            if mean_vol > 0:
                cv = float(np.std(vol_window) / mean_vol)
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


# ============================================================
# 5. factor-mining 移植因子 (Wave 6 W6.1.1 新增)
#    FM_ 前缀, 与国泰海通体系的 MOM/VOL/SIZE/LIQ 明确区分，便于 A/B 测试
#    参考: wzx11223344/factor-mining — A股 14 因子实现
# ============================================================


def compute_factor_mining_factors(
    price_data: dict[str, dict[str, list[float]]],
    fundamentals: dict[str, dict[str, float]] | None = None,
    benchmark_returns: list[float] | None = None,
) -> dict[str, FactorValue]:
    """factor-mining 移植因子 (14 因子中与现有体系有差异的部分, 共 5 个)

    与现有体系的区别 (为什么不能直接复用):
    - FM_RET_1D / FM_MOM_5D / FM_MOM_20D: 纯收益率, 不做反转取反或正交化
    - FM_IDIO_VOL: 对全市场等权收益回归取残差波动 (非基准回归 VOL_IDIO)
    - FM_AMIHUD_AMT: 成交额 (amount) 版 Amihud = mean(|ret|/成交额), 非成交量版 LIQ_AMIHUD
    - FM_CIRC_MCAP: 流通市值对数 (fundamentals.negotiable_value), 非总市值 SIZE_LOG_MCAP

    其余 9 个因子与现有体系等价 (直接 alias 不重复计算):
    - momentum_12m_1m  ≈ MOM_12_1M   (12-1月剔除最近一月)
    - reversal_5d      ≈ MOM_REVERSAL_5D
    - reversal_20d     ≈ MOM_REVERSAL_20D
    - volatility_20d   ≈ VOL_20D
    - turnover_rate    ≈ LIQ_TURNOVER_20D
    - log_market_cap   ≈ SIZE_LOG_MCAP
    - amihud_illiq     ≈ LIQ_AMIHUD
    - ep               ≈ fundamental.EP
    - bp               ≈ fundamental.BP
    """
    factors: dict[str, FactorValue] = {}
    fundamentals = fundamentals or {}

    # ---------- 1. 纯动量因子 (factor-mining 风格: 正向, 不反转取反) ----------

    # FM_RET_1D: 纯 1 日收益率 (正向: 高收益=高分)
    # 区别于 MOM_REVERSAL_5D (反向表示反转信号)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) >= 2:
            values[sym] = float(closes[-1] / closes[-2] - 1.0)
    factors["FM_RET_1D"] = FactorValue(name="FM_RET_1D", category="Momentum", values=values)

    # FM_MOM_5D: 5 日纯动量 (正向: 涨=高分)
    # 区别于现有 MOM_20D 体系: 5 日窗口更短, 用于短期动量/反转区分
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 5:
            values[sym] = float(closes[-1] / closes[-5] - 1.0)
    factors["FM_MOM_5D"] = FactorValue(name="FM_MOM_5D", category="Momentum", values=values)

    # FM_MOM_20D: 20 日纯动量 (与 MOM_20D 等价但不参与后续正交化, 独立 A/B)
    values = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 20:
            values[sym] = float(closes[-1] / closes[-20] - 1.0)
    factors["FM_MOM_20D"] = FactorValue(name="FM_MOM_20D", category="Momentum", values=values)

    # ---------- 2. 特质波动率 (对市场均值回归, factor-mining 原版实现) ----------

    # FM_IDIO_VOL: 对全市场等权收益率做回归取残差波动, 再年化
    # 区别于 VOL_IDIO (对 benchmark_returns 做回归 + 双重正交化 VOL_20D/VOL_120D)
    idio_raw: dict[str, float] = {}
    syms_with_returns = []
    returns_by_sym: dict[str, list[float]] = {}
    max_len = 0
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) > 60:
            # numpy 差分元素为 np.float64, 显式转 float 使 returns_by_sym 值类型为 list[float]
            rets = [float(x) for x in np.diff(closes[-61:])]
            returns_by_sym[sym] = rets
            syms_with_returns.append(sym)
            max_len = max(max_len, len(rets))

    if syms_with_returns and max_len > 0:
        # 构造对齐的收益矩阵: [N_syms x T]
        aligned = np.full((len(syms_with_returns), max_len), np.nan, dtype=float)
        for i, sym in enumerate(syms_with_returns):
            rets = returns_by_sym[sym]
            aligned[i, : len(rets)] = rets

        # 市场等权收益 = 每期横截面上非 NaN 收益的均值
        market_ret = np.nanmean(aligned, axis=0)  # shape [T]

        for i, sym in enumerate(syms_with_returns):
            sym_rets = aligned[i]
            valid = ~np.isnan(sym_rets) & ~np.isnan(market_ret)
            if np.sum(valid) >= 10:
                y = sym_rets[valid]
                x = market_ret[valid]
                std_x = np.std(x)
                if std_x > 1e-10:
                    beta = np.cov(x, y)[0, 1] / max(np.var(x), 1e-10)
                    intercept = float(np.mean(y) - beta * np.mean(x))
                    resid = y - (beta * x + intercept)
                    # 反向: 低特质波动 = 高分 (与 VOL_IDIO 惯例一致)
                    idio_raw[sym] = -float(np.std(resid) * np.sqrt(252))

    factors["FM_IDIO_VOL"] = FactorValue(name="FM_IDIO_VOL", category="LowVolatility", values=idio_raw)

    # ---------- 3. 成交额版 Amihud (factor-mining 原版) ----------

    # FM_AMIHUD_AMT: Amihud 非流动性 = mean(|ret_t| / amount_t)
    # 区别于 LIQ_AMIHUD = mean(|ret_t| / volume_t) (成交量作分母)
    # 成交额版更贴近"单位成交额导致的价格冲击"这一 Amihud 原始定义
    amihud_amt: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        vols = data.get("volumes", [])
        amounts = data.get("amounts")
        n = len(closes)
        if n > 20 and len(vols) >= n:
            # 优先用真实 amounts, 缺失则用 closes*volumes 近似 (同 technical.py _to_ohlcv_df 惯例)
            if amounts is None or len(amounts) < n:
                amounts = [
                    c * v for c, v in zip(closes, vols, strict=False)
                ]  # noqa: B905 - vols 允许长于 closes, 按较短截断为设计语义
            window = min(20, n - 1)
            illiq_list = []
            for t in range(n - window, n):
                if closes[t - 1] > 0 and amounts[t] > 0:
                    ret = abs(closes[t] / closes[t - 1] - 1.0)
                    illiq_list.append(ret / amounts[t])
            if illiq_list:
                amihud_amt[sym] = float(np.mean(illiq_list))

    factors["FM_AMIHUD_AMT"] = FactorValue(name="FM_AMIHUD_AMT", category="Liquidity", values=amihud_amt)

    # ---------- 4. 流通市值对数 (factor-mining: circulating_market_cap) ----------

    # FM_CIRC_MCAP: log(流通市值) — 用 fundamentals.negotiable_value
    # 区别于 SIZE_LOG_MCAP (总市值 market_cap)
    # 用途: 度量"可交易盘"规模, 与全市场规模解耦 (限售股/国有股不参与交易)
    circ_mcap: dict[str, float] = {}
    for sym, fund in fundamentals.items():
        ns = fund.get("negotiable_value", 0)
        if ns and ns > 0:
            # 反向: 小盘 = 高分 (与 SIZE_LOG_MCAP 惯例一致)
            circ_mcap[sym] = -float(np.log(ns))

    factors["FM_CIRC_MCAP"] = FactorValue(name="FM_CIRC_MCAP", category="Size", values=circ_mcap)

    return factors


# ============================================================
# 6. EigenAlpha 装饰器注册示例 (Wave 6 W6.1.3 新增)
#    通过 @register_factor(category=..., name=...) 声明,
#    由 compute_registered_factors(context) 按参数名自动注入并调用。
#    与上方 compute_xxx_factors 函数完全解耦, 可单独 enable/disable。
# ============================================================


@register_factor(
    category="Momentum",
    name="FM_DEMO_VOL_WEIGHTED_MOM",
    description="成交额加权动量 (装饰器示例): 最近5日收益率按成交额加权, 区分放量上涨 vs 缩量上涨",
    window=5,
)
def _fm_demo_volume_weighted_momentum(price_data: dict[str, Any], window: int = 5) -> dict[str, float]:
    """成交额加权动量 (演示 @register_factor 参数注入 + 默认值覆盖)

    计算: sum_i(amount_i * ret_i) / sum_i(amount_i)
    放量日权重更高, 识别"资金推动型"动量
    """
    out: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes") or []
        amounts = data.get("amounts") or data.get("volumes") or []
        if len(closes) <= window or len(amounts) < len(closes) - 1:
            continue
        closes_arr = np.asarray(closes, dtype=float)
        # 最近 window 天的日收益率 (closes[t]/closes[t-1] - 1) 对应窗口 [T-window, T-1]
        rets = closes_arr[-window:] / closes_arr[-window - 1 : -1] - 1.0
        amt_window = np.asarray(amounts[-window:], dtype=float)
        if amt_window.sum() <= 0 or np.isnan(amt_window).any() or np.isnan(rets).any():
            continue
        w = amt_window / amt_window.sum()
        out[sym] = float(np.dot(w, rets))
    return out


@register_factor(
    category="Liquidity",
    name="FM_DEMO_ZERO_TRADE_DAYS",
    description="零成交天数 (装饰器示例): 最近20日成交量为0的天数, 识别停牌/僵尸股风险",
    window=20,
)
def _fm_demo_zero_trade_days(price_data: dict[str, Any], window: int = 20) -> dict[str, float]:
    """最近 window 日零成交量天数占比 (0~1)。

    值越高表示停牌/无流动性越严重; 回测时可作为持仓准入过滤信号。
    """
    out: dict[str, float] = {}
    for sym, data in price_data.items():
        volumes = data.get("volumes") or []
        if len(volumes) < window:
            continue
        vol = np.asarray(volumes[-window:], dtype=float)
        zero_ratio = float((vol <= 1e-9).sum()) / float(window)
        # 反向: 流动性越差 → 分数越低 (与 LIQ 类保持一致)
        out[sym] = -zero_ratio
    return out


@register_factor(
    category="Quality",
    name="FM_DEMO_ROE_SMOOTHED",
    description="ROE 行业内平滑 (装饰器示例): 截面 winsorize + zscore, 展示 fundamentals 参数注入",
)
def _fm_demo_roe_smoothed(
    fundamentals: dict[str, Any] | None = None, industries: dict[str, str] | None = None
) -> dict[str, float]:
    """ROE 行业内中性化 + 3σ winsorize

    演示 fundamentals / industries 参数按名注入 (在 library.py context 中已提供)。
    """
    if not fundamentals:
        return {}
    raw: dict[str, float] = {}
    for sym, fund in fundamentals.items():
        roe = fund.get("roe")
        if roe is None:
            continue
        try:
            raw[sym] = float(roe)
        except (TypeError, ValueError):
            continue
    if not raw:
        return {}
    # 行业中性化 (与 MOM_INDUSTRY_ADJ 同等处理)
    if industries:
        # 先 winsorize
        vals = np.asarray(list(raw.values()), dtype=float)
        med = float(np.nanmedian(vals))
        mad = float(np.nanmedian(np.abs(vals - med))) or 1e-9
        upper = med + 3 * 1.4826 * mad
        lower = med - 3 * 1.4826 * mad
        clipped = {s: min(max(v, lower), upper) for s, v in raw.items()}
        values = neutralize_by_industry(clipped, industries)
    else:
        vals = np.asarray(list(raw.values()), dtype=float)
        med = float(np.nanmedian(vals))
        mad = float(np.nanmedian(np.abs(vals - med))) or 1e-9
        upper = med + 3 * 1.4826 * mad
        lower = med - 3 * 1.4826 * mad
        values = {s: min(max(v, lower), upper) for s, v in raw.items()}
    # zscore 标准化 (无基类依赖, 简单实现)
    arr = np.asarray(list(values.values()), dtype=float)
    std = float(np.nanstd(arr)) or 1.0
    mean = float(np.nanmean(arr))
    return {s: (v - mean) / std for s, v in values.items()}
