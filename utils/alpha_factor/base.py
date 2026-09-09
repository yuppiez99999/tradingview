"""因子库基础结构 — 国泰海通因子体系对标本

数据结构与预处理工具, 供 fundamental / price_volume / technical / expectation 各模块复用。

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》
- 国泰海通《量化2025年度复盘系列》
- Asness et al. (2013) "Value and Momentum Everywhere"
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorValue:
    """单个因子值 (国泰海通因子库标准结构)

    字段 ic_1d / ic_5d / ic_20d / ic_ir: U1 升级后, 优先用时序 IC/ICIR 填充
    (提供 factor_history + forward_returns_history 时); 缺省时用单点 IC 近似
    (evaluate_factors 自动兼容双模式).
    """

    name: str  # 因子名 (研报式: EP/BP/ROE_TTM/SUE/GTJA_001 等)
    category: str  # 因子类别 (Value/Growth/Quality/Leverage/Operation/Momentum/...)
    values: dict[str, float]  # {symbol: factor_value}
    ic_1d: float = 0.0  # 最近 1 日 IC (时序模式=最新一天; 单点模式=单点 1 日未来收益)
    ic_5d: float = 0.0  # 最近 5 日 IC 均值 / 单点 5 日窗口 IC
    ic_20d: float = 0.0  # 最近 20 日 IC 均值 / 单点 20 日窗口 IC
    ic_ir: float = 0.0  # IC 信息比率 (时序模式=完整序列均值/std; 单点模式=近似退化值 0)
    ic_mean_raw: float = 0.0  # U1 新增: 时序 IC 原始均值 (未取 abs, 保留方向)
    ic_n_samples: int = 0  # U1 新增: 有效 IC 样本数 (诊断用, 最少 20 天)
    ic_mode: str = (
        "none"  # U1 新增: "timeseries" / "single_point" / "none", 便于下游区分
    )
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
    # 调试/审计信息 (Wave 6 新增: 装饰器因子清单、处理统计等可扩展)
    debug_info: dict[str, object] = field(default_factory=dict)


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

    industry_means = {
        ind: float(np.mean(vs)) for ind, vs in industry_groups.items() if vs
    }

    return {
        sym: float(values[sym] - industry_means.get(industries.get(sym, ""), 0))
        for sym in values
    }


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
        return {s: float(r) for s, r in zip(common_syms, residuals, strict=True)}

    return values


def orthogonalize(
    values: dict[str, float],
    control: dict[str, float],
) -> dict[str, float]:
    """正交化: 对 control 变量回归取残差 (用于因子间解耦)

    用于如 SIZE_NON_LINEAR 对 SIZE_LOG_MCAP 正交化, 消除线性共线。
    别名: residualize (语义相同, 保留两个名字以兼容已有调用)。
    """
    common = [
        s
        for s in values
        if s in control and values[s] is not None and control[s] is not None
    ]
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
    for sym in symbols or {}:
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
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):  # scipy 不可用时降级
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
        spearmanr = None  # 降级: 用 numpy 实现 Spearman (rank IC)

    ic_series: list[float] = []
    n = min(len(factor_history), len(forward_returns_history))
    for i in range(n):
        fv = factor_history[i]
        fr = forward_returns_history[i]
        common = [
            s
            for s in fv
            if s in fr
            if fv[s] is not None
            and fr[s] is not None
            and isinstance(fv[s], (int, float))
            and isinstance(fr[s], (int, float))
        ]
        if len(common) < min_samples:
            ic_series.append(0.0)
            continue
        x = [float(fv[s]) for s in common]
        y = [float(fr[s]) for s in common]
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            ic_series.append(0.0)
            continue
        if spearmanr is not None:
            corr, _ = spearmanr(x, y)
        else:
            corr = _spearman_numpy(x, y)
        ic_series.append(float(corr) if not np.isnan(corr) else 0.0)
    return ic_series


def _spearman_numpy(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation 的纯 numpy 实现 (scipy 缺失时降级)

    Spearman = Pearson of ranks. 处理 ties 用 average rank.
    """
    xa = np.array(x, dtype=float)
    ya = np.array(y, dtype=float)
    rx = _average_rank(xa)
    ry = _average_rank(ya)
    rx_c = rx - rx.mean()
    ry_c = ry - ry.mean()
    den = np.sqrt(np.sum(rx_c**2) * np.sum(ry_c**2))
    if den < 1e-12:
        return 0.0
    return float(np.sum(rx_c * ry_c) / den)


def _average_rank(a: np.ndarray) -> np.ndarray:
    """计算 average rank (处理 ties, scipy.stats.rankdata 的 default 方法)"""
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    sorted_a = a[order]
    i = 0
    n = len(a)
    while i < n:
        j = i
        while j < n and sorted_a[j] == sorted_a[i]:
            j += 1
        avg = (i + j - 1) / 2.0  # 0-based average rank
        ranks[order[i:j]] = avg
        i = j
    return ranks


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

    for name, fval in result.factors.items():
        if not fval.values:
            continue

        # 时序模式可用性直接内联, 让 mypy 能逐条件收窄 factor_history
        if (
            factor_history is not None
            and forward_returns_history is not None
            and len(forward_returns_history) >= 20
            and name in factor_history
        ):
            # U1: 时序 IC/ICIR 模式 (Spearman, 与 gate1_validation 一致)
            fh = factor_history[name]
            ic_series = calc_ic_series_from_history(fh, forward_returns_history)
            ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=20)

            fval.ic_ir = ic_ir
            fval.ic_mean_raw = ic_mean
            fval.ic_n_samples = len(
                [x for x in ic_series if x != 0.0 and np.isfinite(x)]
            )
            fval.ic_mode = "timeseries"
            # ic_5d: 最近 5 日 IC 均值; ic_1d: 最近 1 日 IC; ic_20d: 最近 20 日 IC 均值
            if ic_series:
                fval.ic_1d = float(ic_series[-1])
                fval.ic_5d = (
                    float(np.mean(ic_series[-5:]))
                    if len(ic_series) >= 5
                    else float(np.mean(ic_series))
                )
                fval.ic_20d = (
                    float(np.mean(ic_series[-20:]))
                    if len(ic_series) >= 20
                    else float(np.mean(ic_series))
                )
            else:
                fval.ic_5d = 0.0
                fval.ic_20d = 0.0
                fval.ic_1d = 0.0

            # 强因子/有效因子判定基于时序 IC 均值 (|ic_mean| 阈值不变)
            effective_ic = abs(ic_mean) if ic_series else 0.0
        else:
            # 降级: 单点 IC 模式 (P0-C2 修复的未来收益版, 向后兼容)
            # 对 1/5/20 三个窗口都计算单点 IC, 保证 fval 字段非空诊断可用;
            # 单点无序列 → ic_ir = 0 (退化值, 无意义), ic_n_samples=1, ic_mean_raw=ic_5d
            ic_1d = calc_ic(fval.values, price_data, 1)
            ic_5d = calc_ic(fval.values, price_data, 5)
            ic_20d = calc_ic(fval.values, price_data, 20)
            fval.ic_1d = ic_1d
            fval.ic_5d = ic_5d
            fval.ic_20d = ic_20d
            fval.ic_ir = 0.0
            fval.ic_mean_raw = ic_5d
            fval.ic_n_samples = 1
            fval.ic_mode = "single_point"
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
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):  # 数据为空或形状不一致时降级返回 None
        return None


# ============================================================
# 因子装饰器系统 (Wave 6 W6.1.3, EigenAlpha 风格)
#
# 设计要点:
#   1. 装饰器注册 + 自动参数注入 (EigenAlpha @register_factor 思路)
#   2. 双模式兼容: 装饰器注册的新因子 + 旧的 compute_xxx_factors 函数, 互不干扰
#   3. 零运行时开销: 注册时 inspect 签名, 执行时直接查表
# ============================================================


_FACTOR_REGISTRY: dict[str, dict] = {}


def register_factor(
    category: str,
    name: str | None = None,
    description: str = "",
    **defaults: Any,
) -> Callable[..., Any]:
    """因子函数装饰器 (EigenAlpha strategy.py 风格, Wave 6 W6.1.3)

    用法:
        @register_factor(category="Momentum", name="MY_MOM", window=20)
        def my_momentum(price_data, window=20):
            ...
            return FactorValue(name="MY_MOM", category="Momentum", values={...})

    设计:
        - 装饰时 inspect(fn) 保存签名, 以便执行时从上下文中按参数名注入
        - 返回 dict[str, FactorValue] 或单个 FactorValue 均可, 统一聚合为 dict
        - 旧的 compute_xxx_factors 函数无需修改 (向后兼容: 可以但不强制用装饰器注册)

    Args:
        category: 因子类别 (Momentum / LowVolatility / Size / Liquidity / Value / Growth /
                  Quality / Leverage / Operation / Technical / Expectation / Graph)
        name: 可选, 强制覆盖因子名前缀 (默认用函数名作为 id)
        description: 因子中文说明 (用于报告展示)
        **defaults: 传给装饰函数的默认参数 (会在注入前被 context 中同名字段覆盖)
    """
    import inspect

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn_id = name or fn.__name__
        sig = inspect.signature(fn)
        _FACTOR_REGISTRY[fn_id] = {
            "fn": fn,
            "category": category,
            "description": description or fn.__doc__ or "",
            "params": list(sig.parameters.keys()),
            "defaults": dict(defaults),
        }
        # 原函数透传, 保持直接调用可用
        return fn

    return deco


def list_registered_factors() -> list[dict]:
    """列出所有装饰器注册的因子 (元信息列表)"""
    return [
        {
            "name": fn_id,
            "category": meta["category"],
            "description": meta["description"],
            "params": meta["params"],
        }
        for fn_id, meta in _FACTOR_REGISTRY.items()
    ]


def compute_registered_factors(
    context: dict,
    select: list[str] | None = None,
) -> dict[str, FactorValue]:
    """根据注册中心 + 上下文参数自动计算所有装饰器注册的因子

    参数注入规则:
        - 遍历每个注册因子的 params 列表, 在 context 中按名称查找
        - context 中没有 → 使用装饰器默认值 (**defaults) → 再用函数签名默认值
        - 完全缺失的必填参数 (函数签名无默认值) → 跳过该因子, 不抛异常 (fail-open)

    Args:
        context: 参数字典, 常见键:
            - price_data: {symbol: {"closes": [...], ...}}
            - fundamentals: {symbol: {"pe": ...}}
            - fundamentals_prev: {symbol: {"pe": ...}} (上期)
            - industries: {symbol: industry_name}
            - benchmark_returns: list[float] (基准收益序列)
            - graph: SupplyChainGraph 实例 (可选)
            - plus: 任何被装饰因子函数参数名匹配的字段
        select: 白名单 (None 表示全部)

    Returns:
        {factor_name: FactorValue} — 所有成功计算的因子 (与 compute_xxx_factors 返回格式一致)
    """
    import inspect

    out: dict[str, FactorValue] = {}
    for fn_id, meta in _FACTOR_REGISTRY.items():
        if select is not None and fn_id not in select:
            continue
        fn = meta["fn"]
        sig_params = inspect.signature(fn).parameters
        kwargs: dict = {}
        skip = False
        for pname, param in sig_params.items():
            # 优先级: context > 装饰器 defaults > 函数签名默认值
            if pname in context and context[pname] is not None:
                kwargs[pname] = context[pname]
            elif pname in meta["defaults"]:
                kwargs[pname] = meta["defaults"][pname]
            elif param.default is not inspect.Parameter.empty:
                kwargs[pname] = param.default
            else:
                # 必填参数缺失 → 跳过该因子
                skip = True
                break
        if skip:
            continue
        try:
            result = fn(**kwargs)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            logging.getLogger("alpha_factor.registry").debug(
                "装饰器因子 %s 计算失败: %r; 已跳过", fn_id, exc
            )
            continue
        # 兼容返回值: FactorValue 或 dict[str, FactorValue]
        if isinstance(result, FactorValue):
            out[result.name or fn_id] = result
        elif isinstance(result, dict):
            # 纯 {symbol: value} dict → 包装成 FactorValue
            if result and all(
                isinstance(k, str) and isinstance(v, (int, float, type(None)))
                for k, v in result.items()
            ):
                out[fn_id] = FactorValue(
                    name=fn_id,
                    category=meta["category"],
                    values=result,
                )
            else:
                # 预期是 {factor_name: FactorValue}
                for k, v in result.items():
                    if isinstance(v, FactorValue):
                        out[k] = v
    return out


# ============================================================
# U1 升级 · 便捷构造器: 从 OHLCV 列表格式 price_data 直接生成
#   forward_returns_history + factor_history (时序 IC/ICIR 模式入参)
# 消除「单点 forward return 近似」, 让 evaluate_factors 走完整时序路径.
# ============================================================


def build_forward_returns_history(
    price_data: dict[str, dict[str, list[float]]],
    forward_window: int = 5,
    symbols: list[str] | None = None,
) -> list[dict[str, float]]:
    """从 OHLCV 列表格式 price_data 构造日频 forward_returns_history

    U1 升级新增: 替代单点未来收益近似, 输出长度为 T 的日频前瞻收益字典序列,
    其中 list[t] = {symbol: forward_return} 表示 t 时点横截面 → t→t+forward_window 的收益.

    语义: 第 t 天收盘价 closes[t] 作为因子基准价, 目标收益为 closes[t+forward_window]/closes[t] - 1.
    对于 t > len - forward_window - 1 的末尾几天, 未来价格不足 → 直接省略 (不产出伪样本).
    因此列表实际长度 = max(0, min_len - forward_window), 索引与 closes 的 0..min_len-forward_window-1
    一一对应, 便于后续与 factor_history 对齐.

    Args:
        price_data: {symbol: {"closes": list[float], ...}} (OHLCV 列表格式, 与 compute_all 一致)
        forward_window: 前瞻收益窗口天数 (默认 5, 与 Gate1/GTJA 惯例对齐; 常用 1/5/20)
        symbols: 可选白名单 (仅包含这些 symbol); None 则取 price_data 全量

    Returns:
        list[dict[str, float]]: 长度 = T = min_len - forward_window (不足则空)
    """
    if forward_window <= 0:
        raise ValueError(f"forward_window 必须为正整数, 得到 {forward_window}")

    active_syms: list[str] = []
    if symbols is None:
        active_syms = list(price_data.keys())
    else:
        active_syms = [s for s in symbols if s in price_data]

    if not active_syms:
        return []

    # 取所有标的 closes 最短长度作为公共时间轴 (对齐各标的数据长度差异)
    closes_map: dict[str, list[float]] = {}
    min_len: int | None = None
    for sym in active_syms:
        closes = price_data.get(sym, {}).get("closes", [])
        closes_map[sym] = closes
        L = len(closes)
        min_len = L if min_len is None else min(min_len, L)
    if min_len is None or min_len <= forward_window:
        return []

    T = min_len - forward_window  # 0..T-1 对应 t=0..T-1, t+forward_window < min_len
    history: list[dict[str, float]] = []
    for t in range(T):
        cross: dict[str, float] = {}
        for sym in active_syms:
            closes = closes_map[sym]
            base = closes[t]
            fwd = closes[t + forward_window]
            if (
                base
                and base > 0
                and fwd is not None
                and np.isfinite(base)
                and np.isfinite(fwd)
            ):
                cross[sym] = float(fwd / base - 1.0)
        history.append(cross)
    return history


def build_factor_history_from_prices(
    price_data: dict[str, dict[str, list[float]]],
    factor_fn: Callable[..., dict[str, Any]],
    symbols: list[str] | None = None,
    warmup_window: int = 20,
) -> dict[str, list[dict[str, float]]]:
    """用「滚动窗口 replay」方式从 OHLCV price_data 构造 factor_history

    U1 升级新增: 给定一个单横截面因子计算函数 factor_fn(price_data_slice)->{sym: value},
    对每个 t = warmup_window..min_len-1 切出 closes[:t+1] 的价格切片, 调用 factor_fn
    产出该时点因子值 → 组装为 {factor_name: [{sym: value}, ...]} 的 factor_history 格式
    (可直接传入 AlphaFactorLibrary.compute_all + evaluate_factors 走时序 IC/ICIR 模式).

    注意:
      - 本函数提供的是通用 replay 方案. 对 100+ 因子的 factor_history 全量构建建议使用
        专门的 factor_history_builder (如 `utils.evolution.auto_factor_factory` 中的管道),
        避免重复运行全因子库 N 次的 O(N) 开销.
      - 单因子 POC / 单点 IC 与时序 IC 的对比诊断场景用本函数足够.

    Args:
        price_data: {symbol: {"closes": list[float], "highs":..., "lows":..., "volumes":...}}
        factor_fn: callable(price_data_slice) -> {sym: value} 或 FactorValue
            price_data_slice 与 price_data 同构但 closes/volumes/highs/lows 长度=t+1
        symbols: 可选白名单
        warmup_window: 最小预热窗口 (默认 20, 因子通常需要 ≥20 根 bar 才能产出有效值)

    Returns:
        {factor_name: [{sym: value}, ...]} — 每个因子序列长度 = min_len - warmup_window
        (对应 t=warmup_window..min_len-1 的横截面, 与 build_forward_returns_history 对齐时
        需注意 forward_window 窗口; 推荐 forward_window=5 + warmup_window=25 得到同长度.)
    """

    active_syms: list[str] = (
        list(price_data.keys())
        if symbols is None
        else [s for s in symbols if s in price_data]
    )
    if not active_syms:
        return {}

    # 公共时间轴长度
    min_len: int | None = None
    for sym in active_syms:
        for key in ("closes", "volumes", "highs", "lows"):
            arr = price_data.get(sym, {}).get(key) or []
            min_len = len(arr) if min_len is None else min(min_len, len(arr))
    if min_len is None or min_len <= warmup_window:
        return {}

    # 准备切片模板 (不破坏原始 price_data)
    def _slice_price(t_end: int) -> dict[str, dict[str, list[float]]]:
        out: dict[str, dict[str, list[float]]] = {}
        for sym in active_syms:
            sym_entry: dict[str, list[float]] = {}
            for key in ("closes", "volumes", "highs", "lows"):
                arr = price_data.get(sym, {}).get(key) or []
                sym_entry[key] = list(arr[: t_end + 1]) if arr else []
            out[sym] = sym_entry
        return out

    # 收集每个 t 的因子值
    history_per_factor: dict[str, list[dict[str, float]]] = {}
    for t in range(warmup_window, min_len):
        slice_pd = _slice_price(t)
        try:
            raw = factor_fn(slice_pd)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            raw = {}
        # 规范化: FactorValue → {name: values dict}
        factor_map: dict[str, dict[str, float]] = {}
        if isinstance(raw, FactorValue):
            factor_map[raw.name] = raw.values
        elif isinstance(raw, dict):
            # {factor_name: FactorValue} / {sym: value}
            if raw and all(
                isinstance(k, str) and isinstance(v, FactorValue)
                for k, v in raw.items()
            ):
                for fname, fv in raw.items():
                    factor_map[fname] = fv.values
            elif raw and all(
                isinstance(k, str) and isinstance(v, (int, float, type(None)))
                for k, v in raw.items()
            ):
                factor_map["factor"] = {
                    k: float(v) for k, v in raw.items() if isinstance(v, (int, float))
                }
        # 追加到每因子序列
        for fname, fv_dict in factor_map.items():
            if fname not in history_per_factor:
                history_per_factor[fname] = []
            history_per_factor[fname].append({s: float(v) for s, v in fv_dict.items()})
    return history_per_factor
