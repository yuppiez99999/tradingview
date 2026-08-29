"""信息论因子筛选模块 — 熵 / KL 散度 / 互信息

量化因子与未来收益的信息含量, 用于:
1. 因子筛选: 互信息高的因子优先入库
2. 冗余检测: 两因子互信息高则冗余, 保留 IC 高者
3. Drift 监控: KL(历史 || 当前) 度量分布漂移, 与 PSI 互补

与现有 utils/alpha/drift_monitor.py:compute_psi 互补:
- PSI 度量分箱分布差异 (一阶)
- KL 散度度量信息论差异 (更敏感于尾部)

参考:
- Shannon (1948) "A Mathematical Theory of Communication"
- Kullback & Leibler (1951) "On Information and Sufficiency"
- 经典理论覆盖度审计 cairn/classic-theory-coverage-20260819.md Top 10 #3
"""

from __future__ import annotations

import numpy as np

from utils.alpha_factor.base import FactorValue

# ============================================================
# 核心信息论度量
# ============================================================


def shannon_entropy(values: np.ndarray | list[float], n_bins: int = 10) -> float:
    """香农熵 H(X) = -Σ p(x) * log2(p(x))

    通过分箱直方图估计概率分布, 再计算熵。
    熵高 = 分布均匀 (信息量大, 不确定性高)
    熵低 = 分布集中 (信息量小, 确定性高)

    Args:
        values: 一维数值序列
        n_bins: 分箱数 (默认 10)

    Returns:
        香农熵 (bits); 空序列/常数序列返回 0.0
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    hist, _ = np.histogram(arr, bins=n_bins, density=False)
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist / total
    p = p[p > 0]
    return float(-np.sum(p * np.log2(p)))


def kl_divergence(
    p_values: np.ndarray | list[float],
    q_values: np.ndarray | list[float],
    n_bins: int = 10,
) -> float:
    """KL 散度 D_KL(P || Q) = Σ p(x) * log(p(x) / q(x))

    度量分布 P 相对 Q 的信息差异 (非对称)。
    用于 drift 监控: P=当前窗口, Q=历史窗口, KL 大 → 分布漂移。

    Args:
        p_values: 分布 P 的样本
        q_values: 分布 Q 的样本
        n_bins: 分箱数

    Returns:
        KL 散度 (nats); P 中有但 Q 中无的箱按 ε 平滑
    """
    p_arr = np.asarray(p_values, dtype=float)
    q_arr = np.asarray(q_values, dtype=float)
    p_arr = p_arr[np.isfinite(p_arr)]
    q_arr = q_arr[np.isfinite(q_arr)]
    if len(p_arr) < 2 or len(q_arr) < 2:
        return 0.0

    # 用 Q 的范围统一分箱 (确保两分布同支撑)
    lo = min(float(np.min(p_arr)), float(np.min(q_arr)))
    hi = max(float(np.max(p_arr)), float(np.max(q_arr)))
    if hi - lo < 1e-12:
        return 0.0
    edges = np.linspace(lo, hi, n_bins + 1)
    p_hist, _ = np.histogram(p_arr, bins=edges, density=False)
    q_hist, _ = np.histogram(q_arr, bins=edges, density=False)

    p_total = p_hist.sum()
    q_total = q_hist.sum()
    if p_total <= 0 or q_total <= 0:
        return 0.0
    p = p_hist / p_total
    q = q_hist / q_total
    # ε 平滑避免除零 / log0
    eps = 1e-10
    p_safe = p[p > 0]
    q_safe = q[p > 0] + eps
    return float(np.sum(p_safe * np.log(p_safe / q_safe)))


def mutual_information(
    x: np.ndarray | list[float],
    y: np.ndarray | list[float],
    n_bins: int = 10,
) -> float:
    """互信息 I(X; Y) = H(X) + H(Y) - H(X, Y)

    度量两变量的信息共享量 (非线性相关, 优于 Pearson)。
    用于:
    - 因子-收益互信息: 高 → 因子有预测力
    - 因子-因子互信息: 高 → 冗余, 保留 IC 高者

    Args:
        x: 变量 X 序列
        y: 变量 Y 序列
        n_bins: 每维分箱数

    Returns:
        互信息 (bits); 长度不一致/空返回 0.0
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if len(x_arr) != len(y_arr) or len(x_arr) < 2:
        return 0.0
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if len(x_arr) < 2:
        return 0.0

    # 2D 联合直方图
    x_edges = np.linspace(
        float(np.min(x_arr)), float(np.max(x_arr)) + 1e-12, n_bins + 1
    )
    y_edges = np.linspace(
        float(np.min(y_arr)), float(np.max(y_arr)) + 1e-12, n_bins + 1
    )
    joint, _, _ = np.histogram2d(x_arr, y_arr, bins=[x_edges, y_edges])
    total = joint.sum()
    if total <= 0:
        return 0.0
    pxy = joint / total
    px = pxy.sum(axis=1)
    py = pxy.sum(axis=0)

    mi = 0.0
    for i in range(n_bins):
        for j in range(n_bins):
            if pxy[i, j] > 0 and px[i] > 0 and py[j] > 0:
                mi += pxy[i, j] * np.log2(pxy[i, j] / (px[i] * py[j]))
    return float(max(mi, 0.0))


# ============================================================
# 因子级度量
# ============================================================


def factor_information_content(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    n_bins: int = 10,
) -> float:
    """因子信息含量 = 互信息(因子值, 未来收益)

    Args:
        factor_values: {symbol: factor_value}
        forward_returns: {symbol: forward_return}
        n_bins: 分箱数

    Returns:
        互信息 (bits); 越高因子预测力越强
    """
    common = set(factor_values.keys()) & set(forward_returns.keys())
    if len(common) < 5:
        return 0.0
    x = [factor_values[s] for s in common]
    y = [forward_returns[s] for s in common]
    return mutual_information(x, y, n_bins)


def factor_redundancy(
    factor_a: dict[str, float],
    factor_b: dict[str, float],
    n_bins: int = 10,
) -> float:
    """因子冗余度 = 互信息(因子A, 因子B)

    用于增量筛选: 新因子相对已有因子的冗余度。
    冗余高 → 信息重叠多 → 增量价值低。

    Args:
        factor_a: 因子 A 值
        factor_b: 因子 B 值

    Returns:
        互信息 (bits); 越高越冗余
    """
    common = set(factor_a.keys()) & set(factor_b.keys())
    if len(common) < 5:
        return 0.0
    x = [factor_a[s] for s in common]
    y = [factor_b[s] for s in common]
    return mutual_information(x, y, n_bins)


def distribution_drift(
    current_values: dict[str, float],
    historical_values: dict[str, float],
    n_bins: int = 10,
) -> float:
    """分布漂移 = KL(当前 || 历史)

    与 PSI 互补: KL 散度对尾部漂移更敏感。
    用于因子衰减监控: KL 持续上升 → 因子失效。

    Args:
        current_values: 当前窗口因子值
        historical_values: 历史窗口因子值

    Returns:
        KL 散度 (nats); 越高漂移越大
    """
    cur = [v for v in current_values.values() if np.isfinite(v)]
    hist = [v for v in historical_values.values() if np.isfinite(v)]
    if len(cur) < 5 or len(hist) < 5:
        return 0.0
    return kl_divergence(cur, hist, n_bins)


# ============================================================
# 因子筛选接口 (与 AlphaFactorLibrary 集成)
# ============================================================


def compute_information_factors(
    price_data: dict[str, dict[str, list[float]]],
) -> dict[str, FactorValue]:
    """信息论因子 3 个 (基于价量数据)

    INFO_ENTROPY_60D: 60 日收益率香农熵 (高=波动结构复杂, 低=单边趋势)
    INFO_ENTROPY_120D: 120 日收益率香农熵
    INFO_DRIFT_60D: 60 日 vs 120 日 KL 散度 (近期分布漂移)

    Returns:
        {factor_name: FactorValue}
    """
    factors: dict[str, FactorValue] = {}

    entropy_60d: dict[str, float] = {}
    entropy_120d: dict[str, float] = {}
    drift_60d: dict[str, float] = {}

    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) < 120:
            continue
        rets = np.diff(np.log(np.asarray(closes[-120:], dtype=float)))

        # 60 日熵
        if len(rets) >= 60:
            entropy_60d[sym] = shannon_entropy(rets[-60:], n_bins=10)
        # 120 日熵
        entropy_120d[sym] = shannon_entropy(rets, n_bins=10)
        # 60 日 vs 前 60 日 KL 漂移
        if len(rets) >= 120:
            drift_60d[sym] = kl_divergence(rets[-60:], rets[:60], n_bins=10)

    factors["INFO_ENTROPY_60D"] = FactorValue(
        name="INFO_ENTROPY_60D", category="InformationTheory", values=entropy_60d
    )
    factors["INFO_ENTROPY_120D"] = FactorValue(
        name="INFO_ENTROPY_120D", category="InformationTheory", values=entropy_120d
    )
    factors["INFO_DRIFT_60D"] = FactorValue(
        name="INFO_DRIFT_60D", category="InformationTheory", values=drift_60d
    )

    return factors


def select_factors_by_information(
    factors: dict[str, dict[str, float]],
    forward_returns: dict[str, float],
    min_ic: float = 0.02,
    max_redundancy: float = 0.10,
    n_bins: int = 10,
) -> list[str]:
    """基于信息含量的因子增量筛选

    贪心算法:
    1. 计算每个因子与未来收益的互信息
    2. 按互信息降序排列
    3. 依次选入: 新因子相对已选因子的最大冗余 < max_redundancy 才入选

    Args:
        factors: {factor_name: {symbol: value}}
        forward_returns: {symbol: forward_return}
        min_ic: 最小互信息阈值 (bits)
        max_redundancy: 最大允许冗余 (bits)
        n_bins: 分箱数

    Returns:
        入选因子名列表 (按互信息降序)
    """
    # 1. 计算各因子信息含量
    ic_scores: dict[str, float] = {}
    for name, vals in factors.items():
        ic = factor_information_content(vals, forward_returns, n_bins)
        if ic >= min_ic:
            ic_scores[name] = ic

    # 2. 按互信息降序
    ranked = sorted(ic_scores.items(), key=lambda kv: kv[1], reverse=True)

    # 3. 增量筛选
    selected: list[str] = []
    for name, _ic in ranked:
        redundant = False
        for sel in selected:
            if factor_redundancy(factors[name], factors[sel], n_bins) > max_redundancy:
                redundant = True
                break
        if not redundant:
            selected.append(name)

    return selected
