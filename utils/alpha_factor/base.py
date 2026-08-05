"""因子库基础结构 — 国泰海通因子体系对标本

数据结构与预处理工具, 供 fundamental / price_volume / technical / expectation 各模块复用。

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》
- 国泰海通《量化2025年度复盘系列》
- Asness et al. (2013) "Value and Momentum Everywhere"
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorValue:
    """单个因子值 (国泰海通因子库标准结构)"""

    name: str  # 因子名 (研报式: EP/BP/ROE_TTM/SUE/GTJA_001 等)
    category: str  # 因子类别 (Value/Growth/Quality/Leverage/Operation/Momentum/...)
    values: dict[str, float]  # {symbol: factor_value}
    ic_1d: float = 0.0  # 1 日 IC
    ic_5d: float = 0.0  # 5 日 IC
    ic_20d: float = 0.0  # 20 日 IC
    ic_ir: float = 0.0  # IC 信息比率
    factor_return: float = 0.0  # 因子收益率 (年化)
    turnover: float = 0.0  # 因子换手率


@dataclass
class FactorLibraryResult:
    """因子库计算结果"""

    factors: dict[str, FactorValue] = field(default_factory=dict)
    # 因子相关性矩阵
    factor_corr_matrix: pd.DataFrame | None = None
    # 有效因子 (|IC| > 0.03)
    effective_factors: list[str] = field(default_factory=list)
    # 强因子 (|IC| > 0.05)
    strong_factors: list[str] = field(default_factory=list)


# ============================================================
# 因子预处理工具 (国泰君安标准流水线: 去极值 → 标准化 → 中性化)
# ============================================================


def winsorize(values: dict[str, float], n_sigma: float = 3.0) -> dict[str, float]:
    """去极值 (MAD 法, 国泰君安标准)

    MAD = median(|x - median(x)|), 1.4826 × MAD ≈ 标准差
    """
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


def standardize(values: dict[str, float]) -> dict[str, float]:
    """Z-score 标准化"""
    if not values:
        return values
    arr = np.array(list(values.values()))
    if np.std(arr) > 0:
        mean, std = float(np.mean(arr)), float(np.std(arr))
        return {k: float((v - mean) / std) for k, v in values.items()}
    return values


def neutralize_by_industry(
    values: dict[str, float],
    industries: dict[str, str],
) -> dict[str, float]:
    """行业中性化: 各行业组内去均值"""
    industry_groups: dict[str, list[float]] = {}
    for sym, ind in industries.items():
        if sym in values:
            industry_groups.setdefault(ind, []).append(values[sym])

    industry_means = {ind: float(np.mean(vs)) for ind, vs in industry_groups.items() if vs}

    return {sym: float(values[sym] - industry_means.get(industries.get(sym, ""), 0)) for sym in values}


def neutralize_by_size(
    values: dict[str, float],
    sizes: dict[str, float],
) -> dict[str, float]:
    """规模中性化 (回归残差)"""
    common_syms = set(values.keys()) & set(sizes.keys())
    if len(common_syms) < 3:
        return values

    x = np.array([sizes[s] for s in common_syms])
    y = np.array([values[s] for s in common_syms])

    if np.std(x) > 0:
        slope = np.cov(x, y)[0, 1] / max(np.var(x), 1e-10)
        intercept = float(np.mean(y) - slope * np.mean(x))
        residuals = y - (slope * x + intercept)
        return {s: float(r) for s, r in zip(common_syms, residuals)}

    return values


def orthogonalize(
    values: dict[str, float],
    control: dict[str, float],
) -> dict[str, float]:
    """正交化: 对 control 变量回归取残差 (用于因子间解耦)

    用于如 SIZE_NON_LINEAR 对 SIZE_LOG_MCAP 正交化, 消除线性共线。
    别名: residualize (语义相同, 保留两个名字以兼容已有调用)。
    """
    common = [s for s in values if s in control and values[s] is not None and control[s] is not None]
    if len(common) < 3:
        return dict(values)
    x = np.array([control[s] for s in common], dtype=float)
    y = np.array([values[s] for s in common], dtype=float)
    if np.std(x) < 1e-10:
        return {s: float(values[s]) for s in common}
    slope = float(np.cov(x, y)[0, 1] / max(np.var(x), 1e-10))
    intercept = float(np.mean(y) - slope * np.mean(x))
    return {s: float(y[i] - (slope * x[i] + intercept)) for i, s in enumerate(common)}


# 别名 (price_volume.py 与 library.py 跨类正交化使用)
residualize = orthogonalize


# ============================================================
# 因子评价工具
# ============================================================


def _forward_returns(
    price_data: dict[str, dict[str, list[float]]],
    forward_window: int,
    symbols: dict[str, float] | None = None,
) -> dict[str, float]:
    """构造横截面「未来收益」字典 (用于 IC 计算, 消除前视/自相关偏差)。

    语义: 因子值对应 t 时点, 目标收益为 t → t+forward_window 的未来收益。
    这里假设因子值基于截至「末时点-forward_window」的价格计算,
    故未来收益 = closes[-1]/closes[-1-forward] - 1 (真实未来窗口)。

    与旧实现 (closes[-1]/closes[-lookback]-1 用过去收益近似) 相比,
    目标收益改为**未来收益**, 消除「因子值(基于过去)与回看收益(基于同一过去)」
    的自相关伪 IC。
    """
    fwd: dict[str, float] = {}
    for sym in (symbols or {}):
        closes = price_data.get(sym, {}).get("closes", [])
        # 需要至少 forward_window+1 个点, 且参考点价格非 0
        if len(closes) > forward_window and closes[-1 - forward_window] > 0:
            fwd[sym] = closes[-1] / closes[-1 - forward_window] - 1.0
    return fwd


def calc_ic(
    factor_values: dict[str, float],
    price_data: dict[str, dict[str, list[float]]],
    forward_window: int,
) -> float:
    """计算 IC (Spearman rank correlation, 国泰君安标准) — 使用未来收益

    Args:
        factor_values: {symbol: 因子值} (横截面, 对应 t 时点)
        price_data: {symbol: {"closes": [...]}}
        forward_window: 前瞻收益窗口 (目标收益 = t → t+forward_window 未来收益)

    Note:
        真实 IC = corr(横截面因子值, 未来收益)。
        P0-C2 修复: 旧实现用「过去 N 日收益」(closes[-1]/closes[-N]-1) 近似,
        因子值(基于过去动量) 与 回看收益(同一段过去) 产生自相关伪 IC。
        现改用未来收益, 目标收益 = closes[-1]/closes[-1-forward]-1, 语义正确。
    """
    try:
        from scipy.stats import spearmanr

        fwd_returns = _forward_returns(price_data, forward_window, factor_values)
        factor_list: list[float] = []
        ret_list: list[float] = []
        for sym, fv in factor_values.items():
            ret = fwd_returns.get(sym)
            if ret is not None:
                factor_list.append(fv)
                ret_list.append(ret)

        if len(factor_list) < 5:
            return 0.0
        corr, _ = spearmanr(factor_list, ret_list)
        return float(corr) if not np.isnan(corr) else 0.0
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # scipy 不可用时降级
        return 0.0


def calc_ic_series_from_history(
    factor_history: list[dict[str, float]],
    forward_returns_history: list[dict[str, float]],
    min_samples: int = 5,
) -> list[float]:
    """计算时序 IC 序列 (每日一个截面 Spearman rank IC)

    U1 升级新增: 基于 factor_history_builder 产出的日频因子值序列,
    逐日计算 Spearman rank IC (国泰君安标准), 用于 IC_IR 稳定性评估.

    与 factor_history_builder.compute_rolling_ic_series 的区别:
        - 本函数用 Spearman (rank IC, 国泰君安标准), 与 calc_ic / gate1_validation 一致
        - compute_rolling_ic_series 用 Pearson (适合 PipelineOrchestrator 的组合优化)

    Args:
        factor_history: 单个因子的日频值序列 [{symbol: factor_value}, ...]
        forward_returns_history: 日频 forward returns [{symbol: forward_return}, ...]
        min_samples: 计算单日 IC 最少所需标的数

    Returns:
        ic_series: List[float], 每日的 Spearman rank IC (无效日返回 0.0)
    """
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return []

    ic_series: list[float] = []
    n = min(len(factor_history), len(forward_returns_history))
    for i in range(n):
        fv = factor_history[i]
        fr = forward_returns_history[i]
        common = [s for s in fv if s in fr
                  if fv[s] is not None and fr[s] is not None
                  and isinstance(fv[s], (int, float)) and isinstance(fr[s], (int, float))]
        if len(common) < min_samples:
            ic_series.append(0.0)
            continue
        x = [float(fv[s]) for s in common]
        y = [float(fr[s]) for s in common]
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            ic_series.append(0.0)
            continue
        corr, _ = spearmanr(x, y)
        ic_series.append(float(corr) if not np.isnan(corr) else 0.0)
    return ic_series


def calc_ic_ir(
    ic_series: list[float],
    min_periods: int = 20,
) -> tuple[float, float, float]:
    """计算 IC_IR = mean(IC) / std(IC)

    U1 升级新增: 从 IC 序列计算信息比率, 评估因子预测能力的稳定性.
    逻辑与 factor_history_builder.compute_ic_ir 对齐 (ddof=1 样本标准差).

    Args:
        ic_series: 日频 IC 序列 (来自 calc_ic_series_from_history)
        min_periods: 最少所需 IC 样本数 (默认 20, 与 Gate2 一致)

    Returns:
        (ic_ir, ic_mean, ic_std) — 样本不足时返回 (0.0, 0.0, 0.0)
    """
    if len(ic_series) < min_periods:
        return 0.0, 0.0, 0.0
    arr = np.array(ic_series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < min_periods:
        return 0.0, 0.0, 0.0
    ic_mean = float(np.mean(arr))
    ic_std = float(np.std(arr, ddof=1))
    if ic_std < 1e-12:
        return 0.0, ic_mean, 0.0
    ic_ir = ic_mean / ic_std
    if not np.isfinite(ic_ir):
        return 0.0, ic_mean, ic_std
    return float(ic_ir), ic_mean, ic_std


def evaluate_factors(
    result: FactorLibraryResult,
    price_data: dict[str, dict[str, list[float]]],
    factor_history: dict[str, list[dict[str, float]]] | None = None,
    forward_returns_history: list[dict[str, float]] | None = None,
) -> None:
    """评估因子有效性 (|IC|>0.05 强因子, >0.03 有效因子)

    U1 升级: 支持时序 IC/ICIR 模式. 当提供 factor_history + forward_returns_history 时,
    使用 Spearman rank IC 序列计算 IC_IR (稳定性), 替代单点 IC.
    未提供时降级为单点 IC (向后兼容, P0-C2 修复的未来收益版).

    Args:
        result: 因子库计算结果 (就地修改 fval.ic_1d/ic_5d/ic_20d/ic_ir)
        price_data: {symbol: {"closes": [...]}} — 单点模式用于 calc_ic
        factor_history: {factor_name: [{symbol: factor_value}, ...]} — 日频因子值序列 (可选)
        forward_returns_history: [{symbol: forward_return}, ...] — 日频 forward returns (可选)

    幂等: 重复调用会先清空 strong_factors/effective_factors 再重新评估, 避免重复 append。
    """
    # 清空旧结果 (幂等性)
    result.strong_factors.clear()
    result.effective_factors.clear()

    # 时序模式可用性检查
    use_timeseries = (
        factor_history is not None
        and forward_returns_history is not None
        and len(forward_returns_history) >= 20
    )

    for name, fval in result.factors.items():
        if not fval.values:
            continue

        if use_timeseries and name in factor_history:
            # U1: 时序 IC/ICIR 模式 (Spearman, 与 gate1_validation 一致)
            fh = factor_history[name]
            ic_series = calc_ic_series_from_history(fh, forward_returns_history)
            ic_ir, ic_mean, _ = calc_ic_ir(ic_series, min_periods=20)

            fval.ic_ir = ic_ir
            # ic_5d: 最近 5 日 IC 均值; ic_1d: 最近 1 日 IC; ic_20d: 最近 20 日 IC 均值
            if ic_series:
                fval.ic_1d = float(ic_series[-1])
                fval.ic_5d = float(np.mean(ic_series[-5:])) if len(ic_series) >= 5 else float(np.mean(ic_series))
                fval.ic_20d = float(np.mean(ic_series[-20:])) if len(ic_series) >= 20 else float(np.mean(ic_series))
            else:
                fval.ic_5d = 0.0

            # 强因子/有效因子判定基于时序 IC 均值 (|ic_mean| 阈值不变)
            effective_ic = abs(ic_mean) if ic_series else 0.0
        else:
            # 降级: 单点 IC 模式 (P0-C2 修复的未来收益版, 向后兼容)
            ic_5d = calc_ic(fval.values, price_data, 5)
            fval.ic_5d = ic_5d
            effective_ic = abs(ic_5d)

        if effective_ic > 0.05:
            result.strong_factors.append(name)
        elif effective_ic > 0.03:
            result.effective_factors.append(name)


def compute_factor_corr_matrix(
    factors: dict[str, FactorValue],
) -> pd.DataFrame | None:
    """计算因子间相关性矩阵 (用于检测共线)"""
    if not factors:
        return None
    try:
        df = pd.DataFrame({name: pd.Series(fv.values) for name, fv in factors.items()})
        return df.corr()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # 数据为空或形状不一致时降级返回 None
        return None
