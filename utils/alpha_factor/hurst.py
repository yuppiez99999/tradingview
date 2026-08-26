"""Hurst 指数因子模块 — R/S 分析判断序列长记忆性

Hurst 指数 H ∈ (0, 1) 通过重标极差 (R/S) 分析估计:
    H > 0.5  → 趋势性 (长记忆, 持续过程)
    H = 0.5  → 随机游走 (无记忆)
    H < 0.5  → 均值回归 (反持续过程)

A股应用:
- 趋势股 (H>0.6) 适合动量策略
- 均值回归股 (H<0.4) 适合反转策略
- 可作为 Regime 因子, 动态切换动量/反转策略权重

参考:
- Hurst (1951) "Long-term storage capacity of reservoirs"
- Mandelbrot & Ness (1968) "Fractional Brownian motions, fractional noises and applications"
- Peters (1994) "Fractal Market Analysis"
- 经典理论覆盖度审计 cairn/classic-theory-coverage-20260819.md Top 10 #2
"""

from __future__ import annotations

import numpy as np

from utils.alpha_factor.base import FactorValue


def _rs_statistic(series: np.ndarray) -> float:
    """计算单个序列的重标极差 R/S

    R = max(累积偏差) - min(累积偏差)
    S = std(series)
    R/S = R / S

    Args:
        series: 一段价格序列 (已转对数收益)

    Returns:
        R/S 值; 序列长度 < 2 或 S=0 时返回 0.0
    """
    n = len(series)
    if n < 2:
        return 0.0
    mean = float(np.mean(series))
    dev = np.cumsum(series - mean)
    r = float(np.max(dev) - np.min(dev))
    s = float(np.std(series, ddof=1))
    if s <= 1e-12:
        return 0.0
    return r / s


def estimate_hurst(
    closes: list[float] | np.ndarray,
    min_n: int = 10,
    max_n: int | None = None,
) -> float:
    """估计 Hurst 指数 (经典 R/S 分析 + log-log 回归)

    步骤:
    1. 价格转对数收益: r_t = log(p_t / p_{t-1})
    2. 对多个子序列长度 n, 计算 R/S 的均值
    3. log(R/S) = H * log(n) + c, 斜率 H 即 Hurst 指数

    Args:
        closes: 收盘价序列
        min_n: 最小子序列长度 (默认 10)
        max_n: 最大子序列长度 (默认 len/2)

    Returns:
        Hurst 指数 ∈ [0, 1]; 数据不足返回 0.5 (随机游走中性)
    """
    prices = np.asarray(closes, dtype=float)
    n_total = len(prices)
    if n_total < min_n * 2:
        return 0.5

    # 对数收益 (避免零/负价格)
    valid = prices[prices > 0]
    if len(valid) < min_n * 2:
        return 0.5
    rets = np.diff(np.log(valid))
    n_ret = len(rets)

    if max_n is None:
        max_n = n_ret // 2
    max_n = min(max_n, n_ret // 2)
    if max_n < min_n:
        return 0.5

    # 多尺度 R/S 计算
    ns: list[int] = []
    rs_vals: list[float] = []
    n = min_n
    while n <= max_n:
        n_chunks = n_ret // n
        if n_chunks < 1:
            break
        rs_sum = 0.0
        cnt = 0
        for k in range(n_chunks):
            chunk = rets[k * n : (k + 1) * n]
            rs = _rs_statistic(chunk)
            if rs > 0:
                rs_sum += rs
                cnt += 1
        if cnt > 0 and rs_sum > 0:
            ns.append(n)
            rs_vals.append(rs_sum / cnt)
        n = int(n * 1.5) if n < max_n else max_n + 1

    if len(ns) < 3:
        return 0.5

    # log-log 线性回归: log(R/S) = H * log(n) + c
    log_n = np.log(np.asarray(ns, dtype=float))
    log_rs = np.log(np.asarray(rs_vals, dtype=float))
    # 最小二乘斜率
    slope = float(np.polyfit(log_n, log_rs, 1)[0])
    # 截断到合理范围
    return float(np.clip(slope, 0.0, 1.0))


def compute_hurst_factors(
    price_data: dict[str, dict[str, list[float]]],
) -> dict[str, FactorValue]:
    """Hurst 指数类因子 4 个

    HURST_60D / HURST_120D / HURST_252D: 多窗口 Hurst 指数 (原始值, 0-1)
    HURST_TREND_SCORE: 趋势性得分 = (H - 0.5) * 2, ∈ [-1, 1]
        正值 = 趋势性, 适合动量
        负值 = 均值回归, 适合反转
        零   = 随机游走

    Returns:
        {factor_name: FactorValue}
    """
    factors: dict[str, FactorValue] = {}

    # 多窗口 Hurst 指数
    hurst_60d: dict[str, float] = {}
    hurst_120d: dict[str, float] = {}
    hurst_252d: dict[str, float] = {}

    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if not closes:
            continue
        # 60 日窗口 (短记忆)
        if len(closes) >= 60:
            h60 = estimate_hurst(closes[-60:], min_n=8, max_n=30)
            hurst_60d[sym] = h60
        # 120 日窗口 (中记忆)
        if len(closes) >= 120:
            h120 = estimate_hurst(closes[-120:], min_n=10, max_n=60)
            hurst_120d[sym] = h120
        # 252 日窗口 (长记忆)
        if len(closes) >= 252:
            h252 = estimate_hurst(closes[-252:], min_n=12, max_n=126)
            hurst_252d[sym] = h252

    factors["HURST_60D"] = FactorValue(
        name="HURST_60D", category="LongMemory", values=hurst_60d
    )
    factors["HURST_120D"] = FactorValue(
        name="HURST_120D", category="LongMemory", values=hurst_120d
    )
    factors["HURST_252D"] = FactorValue(
        name="HURST_252D", category="LongMemory", values=hurst_252d
    )

    # 趋势性得分: (H - 0.5) * 2, 用 120 日 Hurst (中窗口平衡稳定性与灵敏度)
    trend_score: dict[str, float] = {}
    for sym, h in hurst_120d.items():
        trend_score[sym] = (h - 0.5) * 2.0
    factors["HURST_TREND_SCORE"] = FactorValue(
        name="HURST_TREND_SCORE", category="LongMemory", values=trend_score
    )

    return factors


def classify_regime(hurst: float) -> str:
    """根据 Hurst 指数分类市场制度

    Args:
        hurst: Hurst 指数 ∈ [0, 1]

    Returns:
        "trending" (H>0.6) / "mean_reverting" (H<0.4) / "random_walk"
    """
    if hurst > 0.6:
        return "trending"
    if hurst < 0.4:
        return "mean_reverting"
    return "random_walk"
