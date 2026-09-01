"""Alpha 因子标准化评估器 — alphalens 风格 (Wave 6 W6.1.2 新增)

在 base.py 现有单点/时序 IC 计算基础上, 新增 alphalens 四大标准分析输出:
    1. 分层收益分析 (Quantile Returns) — 5 分位组合收益 + 单调性
    2. 换手率分析 (Turnover) — 因子组合的日/周换手率
    3. 因子衰减曲线 (Factor Decay) — 1~20 日不同前瞻窗口的 IC 衰减
    4. 评估 Tear Sheet — 结构化汇总结果 (兼容 HTML/JSON 报告)

设计原则 (与现有系统对齐):
    - 零新增第三方依赖: 不直接 import alphalens (可能未装), 全部用 numpy/pandas
    - 失败安全: scipy.stats.spearmanr 缺失时降级 Pearson; 所有异常降级为 warning 返回空结果
    - 与 base.evaluate_factors 协作: 不覆盖, 提供「更完整评估」的额外能力
    - 与 cairn/backtest-standards.md §十 回测报告质量检查清单对齐

参考:
    - cloudQuant/alphalens: IC/IR + 分层收益 + 换手率 + Tear Sheet
    - wzx11223344/factor-mining: ic_analysis.py + decay.py + report.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("alpha_factor.evaluator")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class QuantileReturn:
    """单因子的分层收益结果"""

    factor_name: str
    quantile_returns: dict[int, float]  # {分位编号 1..5: 该组平均未来收益}
    long_short_return: float  # Q5 - Q1 多空收益 (做多高分, 做空低分)
    monotonicity: (
        float  # 单调性得分 (Spearman ρ of Q1..Q5 returns vs quantile rank, 越大越好)
    )
    forward_window: int  # 对应前瞻窗口


@dataclass
class TurnoverResult:
    """换手率结果"""

    factor_name: str
    daily_turnover: list[float]  # 每日换手率序列 (0~1)
    avg_daily_turnover: float  # 日均换手率
    weekly_turnover: float | None = None  # 5 日平均换手率 (可选)


@dataclass
class DecayResult:
    """因子衰减结果"""

    factor_name: str
    ic_by_window: dict[int, float]  # {前瞻窗口天数: IC 均值}
    icir_by_window: dict[int, float]  # {前瞻窗口天数: ICIR}
    half_life_days: float | None  # IC 半衰期 (天数, 指数拟合, 拟合失败返回 None)


@dataclass
class FactorTearSheet:
    """单因子评估 Tear Sheet (alphalens 风格汇总)"""

    factor_name: str
    # IC/ICIR
    ic_series: list[float] = field(default_factory=list)
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_win_rate: float = 0.0  # IC > 0 的比例
    t_stat: float = 0.0  # IC 的 t 统计量 (显著性)
    # 分层收益
    quantile: QuantileReturn | None = None
    # 换手率
    turnover: TurnoverResult | None = None
    # 衰减曲线
    decay: DecayResult | None = None


# ============================================================
# 内部辅助: Spearman / Pearson 容错计算
# ============================================================


def _rank_corr(x: list[float], y: list[float]) -> tuple[float, float]:
    """秩相关系数 (Spearman 优先, scipy 缺失时降级 Pearson)

    Returns:
        (corr, p_value) — p_value 在 Pearson 降级模式下恒为 np.nan
    """
    if len(x) < 5 or len(x) != len(y):
        return 0.0, np.nan
    try:
        from scipy.stats import spearmanr

        corr, pval = spearmanr(x, y)
        if np.isnan(corr):
            return 0.0, float(pval) if not np.isnan(pval) else np.nan
        return float(corr), float(pval)
    except (ImportError, ValueError, TypeError, RuntimeError):
        # 降级 Pearson
        xa, ya = np.array(x, dtype=float), np.array(y, dtype=float)
        if np.std(xa) < 1e-12 or np.std(ya) < 1e-12:
            return 0.0, np.nan
        corr = float(np.corrcoef(xa, ya)[0, 1])
        return (corr if np.isfinite(corr) else 0.0), np.nan


def _split_into_quantiles(
    factor_values: dict[str, float],
    n_quantiles: int = 5,
) -> dict[int, list[str]]:
    """把横截面 {symbol: value} 按因子值从小到大分为 n 组

    Returns:
        {quantile_rank(1..n): [symbol_1, symbol_2, ...]}
        rank=1 对应最低因子值组, rank=n 对应最高组
    """
    valid = [
        (sym, val)
        for sym, val in factor_values.items()
        if val is not None and isinstance(val, (int, float)) and np.isfinite(val)
    ]
    if not valid:
        return {}
    # 按因子值升序排列
    valid_sorted = sorted(valid, key=lambda kv: kv[1])
    n = len(valid_sorted)
    groups: dict[int, list[str]] = {}
    for q in range(1, n_quantiles + 1):
        lo = int((q - 1) * n / n_quantiles)
        hi = int(q * n / n_quantiles)
        groups[q] = [sym for sym, _ in valid_sorted[lo:hi]]
        # 确保边界不会漏 (当 n 不能整除时, 把余数分给最后一组)
        if q == n_quantiles and hi < n:
            groups[q].extend([sym for sym, _ in valid_sorted[hi:]])
    return groups


# ============================================================
# 1. 分层收益分析 (alphalens 风格)
# ============================================================


def compute_quantile_returns(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    n_quantiles: int = 5,
    factor_name: str = "",
    forward_window: int = 5,
) -> QuantileReturn:
    """计算横截面分层收益 (factor-mining ic_analysis.py 5分位收益分析)

    Args:
        factor_values: 横截面因子值 {symbol: value}
        forward_returns: 同横截面的前瞻收益 {symbol: forward_return} (t→t+W)
        n_quantiles: 分位数组数 (默认 5, 与 alphalens Q1..Q5 对齐)
        factor_name: 因子名
        forward_window: 前瞻窗口天数 (仅用于元数据)

    Returns:
        QuantileReturn — 含各组收益 + 多空收益 + 单调性得分
    """
    groups = _split_into_quantiles(factor_values, n_quantiles)
    q_returns: dict[int, float] = {}
    for q in range(1, n_quantiles + 1):
        syms = groups.get(q, [])
        rets = [
            forward_returns[s]
            for s in syms
            if s in forward_returns
            and forward_returns[s] is not None
            and isinstance(forward_returns[s], (int, float))
            and np.isfinite(forward_returns[s])
        ]
        q_returns[q] = float(np.mean(rets)) if rets else 0.0

    q5_q1 = q_returns.get(n_quantiles, 0.0) - q_returns.get(1, 0.0)

    # 单调性 = quantile rank 与该组收益的秩相关系数
    ranks = list(range(1, n_quantiles + 1))
    ret_seq = [q_returns.get(q, 0.0) for q in ranks]
    mono, _ = _rank_corr(ranks, ret_seq)

    return QuantileReturn(
        factor_name=factor_name,
        quantile_returns=q_returns,
        long_short_return=float(q5_q1),
        monotonicity=float(mono) if np.isfinite(mono) else 0.0,
        forward_window=forward_window,
    )


# ============================================================
# 2. 换手率分析
# ============================================================


def compute_turnover(
    factor_history: list[dict[str, float]],
    top_pct: float = 0.2,
    factor_name: str = "",
) -> TurnoverResult:
    """计算因子组合换手率 (factor-mining decay.py 思路 + alphalens turnover standard)

    定义: 每期选取 top_pct 高分股票作为"做多组合", 相邻两期组合的变动比例
    turnover(t) = 1 - |holdings(t) ∩ holdings(t-1)| / |holdings(t)|

    Args:
        factor_history: 单个因子的日频值序列 [{symbol: value}, ...]
        top_pct: 每组选取 top X% 作为组合 (0.2 = 前 20%)
        factor_name: 因子名

    Returns:
        TurnoverResult — 日均换手率 + 完整序列
    """
    daily: list[float] = []
    prev_set: set[str] | None = None

    for fv in factor_history:
        valid = [
            (s, v)
            for s, v in fv.items()
            if v is not None and isinstance(v, (int, float)) and np.isfinite(v)
        ]
        if not valid:
            prev_set = None
            daily.append(0.0)
            continue
        n_pick = max(1, int(len(valid) * top_pct))
        top = sorted(valid, key=lambda kv: kv[1], reverse=True)[:n_pick]
        curr_set = {s for s, _ in top}
        if prev_set is not None and len(curr_set) > 0:
            overlap = len(curr_set & prev_set)
            turn = 1.0 - float(overlap) / float(len(curr_set))
            daily.append(float(max(0.0, min(1.0, turn))))
        else:
            daily.append(0.0)
        prev_set = curr_set

    avg = float(np.mean(daily)) if daily else 0.0
    weekly = float(np.mean(daily[-5:])) if len(daily) >= 5 else None

    return TurnoverResult(
        factor_name=factor_name,
        daily_turnover=daily,
        avg_daily_turnover=avg,
        weekly_turnover=weekly,
    )


# ============================================================
# 3. 因子衰减曲线
# ============================================================


def compute_factor_decay(
    factor_history: list[dict[str, float]],
    forward_returns_by_window: dict[int, list[dict[str, float]]],
    windows: list[int] | None = None,
    factor_name: str = "",
) -> DecayResult:
    """计算因子衰减曲线 (factor-mining decay.py 思路)

    对 1,2,3,5,10,15,20 日等多个前瞻窗口分别计算 IC/ICIR,
    并拟合 IC 半衰期 (多少天后 IC 降到初始的 50%).

    Args:
        factor_history: 单个因子日频值序列 [T]
        forward_returns_by_window: {window_days: [T 日频 forward_returns 字典序列]}
            key 为窗口天数, value 必须与 factor_history 同长度对齐
        windows: 要评估的窗口列表 (None 时使用默认 [1,2,3,5,10,15,20])
        factor_name: 因子名

    Returns:
        DecayResult
    """
    if windows is None:
        windows = [1, 2, 3, 5, 10, 15, 20]

    ic_mean_map: dict[int, float] = {}
    icir_map: dict[int, float] = {}

    # 内联避免循环 import
    from utils.alpha_factor.base import calc_ic_ir, calc_ic_series_from_history

    for w in windows:
        fr = forward_returns_by_window.get(w)
        if fr is None or len(fr) < 20:
            ic_mean_map[w] = 0.0
            icir_map[w] = 0.0
            continue
        ic_series = calc_ic_series_from_history(factor_history, fr)
        ic_ir, ic_mean, _ = calc_ic_ir(ic_series, min_periods=10)
        ic_mean_map[w] = ic_mean
        icir_map[w] = ic_ir

    # 半衰期拟合: 指数衰减模型 IC(w) = A * exp(-λ * w)
    # 用 1,2,3,5,10 窗口 (短期) 拟合, 长窗口噪声大不参与
    half_life: float | None = None
    try:
        fit_windows = [w for w in windows if w <= 10 and ic_mean_map.get(w, 0) != 0.0]
        if len(fit_windows) >= 3:
            ws = np.array(fit_windows, dtype=float)
            ics = np.array([abs(ic_mean_map[w]) for w in fit_windows], dtype=float)
            if np.all(ics > 0):
                log_ics = np.log(ics)
                # 线性拟合 log(IC) = log(A) - λ * w
                coeffs = np.polyfit(ws, log_ics, 1)  # coeffs[0] = -λ
                lam = -coeffs[0]
                if lam > 1e-5:
                    half_life = float(np.log(2) / lam)
                else:
                    half_life = float("inf")  # 几乎不衰减
    except (ValueError, TypeError, RuntimeError) as e:
        logger.debug(f"[{factor_name}] 半衰期拟合失败: {e}")
        half_life = None

    return DecayResult(
        factor_name=factor_name,
        ic_by_window=ic_mean_map,
        icir_by_window=icir_map,
        half_life_days=half_life,
    )


# ============================================================
# 4. 汇总 Tear Sheet
# ============================================================


def build_factor_tear_sheet(
    factor_name: str,
    factor_history: list[dict[str, float]] | None = None,
    forward_returns_history: list[dict[str, float]] | None = None,
    latest_factor_values: dict[str, float] | None = None,
    latest_forward_returns: dict[str, float] | None = None,
    forward_window: int = 5,
    forward_returns_by_window: dict[int, list[dict[str, float]]] | None = None,
    top_pct_for_turnover: float = 0.2,
) -> FactorTearSheet:
    """构造单因子完整 Tear Sheet (alphalens 风格)

    输入可以为 "全部提供" 或 "部分提供"; 缺失项对应结果字段置 None。
    所有输入的最小可用性检查在子函数内部完成, 本函数无硬阻塞。

    Args:
        factor_name: 因子名
        factor_history: 日频因子值序列 [T] (用于 IC 时序 + 换手率 + 衰减)
        forward_returns_history: 日频 forward returns [T] (窗口 = forward_window)
        latest_factor_values: 最新横截面因子值 (用于分层收益)
        latest_forward_returns: 最新横截面前瞻收益 (用于分层收益)
        forward_window: 默认前瞻窗口天数 (默认 5)
        forward_returns_by_window: {W 天: [T 日频 W 天 forward returns]} (用于衰减)
        top_pct_for_turnover: 换手率组合的 top% 阈值 (默认 0.2)

    Returns:
        FactorTearSheet — 结构化评估结果 (可序列化为 JSON 报告)
    """
    sheet = FactorTearSheet(factor_name=factor_name)
    try:
        # ---- IC/ICIR 时序 ----
        if factor_history is not None and forward_returns_history is not None:
            from utils.alpha_factor.base import calc_ic_ir, calc_ic_series_from_history

            ic_series = calc_ic_series_from_history(
                factor_history, forward_returns_history
            )
            ic_ir, ic_mean, ic_std = calc_ic_ir(ic_series, min_periods=10)
            sheet.ic_series = [float(x) for x in ic_series]
            sheet.ic_mean = ic_mean
            sheet.ic_std = ic_std
            sheet.ic_ir = ic_ir
            sheet.ic_win_rate = (
                float(np.mean(np.array(ic_series) > 0)) if ic_series else 0.0
            )
            n = len(ic_series)
            if ic_std > 1e-12 and n >= 2:
                sheet.t_stat = float(ic_mean / (ic_std / max(np.sqrt(n), 1e-10)))

        # ---- 分层收益 (alphalens quantile analysis) ----
        if latest_factor_values is not None and latest_forward_returns is not None:
            sheet.quantile = compute_quantile_returns(
                latest_factor_values,
                latest_forward_returns,
                n_quantiles=5,
                factor_name=factor_name,
                forward_window=forward_window,
            )

        # ---- 换手率 ----
        if factor_history is not None:
            sheet.turnover = compute_turnover(
                factor_history,
                top_pct=top_pct_for_turnover,
                factor_name=factor_name,
            )

        # ---- 衰减曲线 ----
        if factor_history is not None and forward_returns_by_window is not None:
            sheet.decay = compute_factor_decay(
                factor_history,
                forward_returns_by_window,
                factor_name=factor_name,
            )

    except (ImportError, AttributeError) as exc:
        logger.warning(f"[{factor_name}] Tear Sheet 构建失败: {exc!r}; 返回部分结果")

    return sheet


# ============================================================
# 5. 批量评估: 对 FactorLibraryResult 中所有因子统一构建 Tear Sheet
# ============================================================


def evaluate_all_factors_tear_sheets(
    factor_history_by_name: dict[str, list[dict[str, float]]],
    forward_returns_history: list[dict[str, float]],
    latest_factor_values_by_name: dict[str, dict[str, float]],
    latest_forward_returns: dict[str, float],
    forward_window: int = 5,
    forward_returns_by_window: dict[int, list[dict[str, float]]] | None = None,
) -> dict[str, FactorTearSheet]:
    """批量构建所有因子的 Tear Sheet

    Args:
        factor_history_by_name: {factor_name: [T 日频因子值序列]}
        forward_returns_history: [T 日频 forward returns (W=forward_window)]
        latest_factor_values_by_name: {factor_name: 最新横截面 {symbol: value}}
        latest_forward_returns: 最新横截面 {symbol: forward_return (W=forward_window)}
        forward_window: 默认前瞻窗口
        forward_returns_by_window: {W 天: [T 日频 W 天 forward returns]}

    Returns:
        {factor_name: FactorTearSheet} — 所有因子的结构化评估结果
    """
    sheets: dict[str, FactorTearSheet] = {}
    names = set(factor_history_by_name.keys()) | set(
        latest_factor_values_by_name.keys()
    )
    for name in names:
        sheets[name] = build_factor_tear_sheet(
            factor_name=name,
            factor_history=factor_history_by_name.get(name),
            forward_returns_history=forward_returns_history,
            latest_factor_values=latest_factor_values_by_name.get(name),
            latest_forward_returns=latest_forward_returns,
            forward_window=forward_window,
            forward_returns_by_window=forward_returns_by_window,
        )
    return sheets


# ============================================================
# 6. 报告输出 (与 factor-mining report.py 思路对齐)
# ============================================================


def tear_sheet_to_dict(sheet: FactorTearSheet) -> dict:
    """把 FactorTearSheet 序列化为 JSON 友好的 dict (用于报告/落盘)"""
    out = {
        "factor_name": sheet.factor_name,
        "ic_mean": sheet.ic_mean,
        "ic_std": sheet.ic_std,
        "ic_ir": sheet.ic_ir,
        "ic_win_rate": sheet.ic_win_rate,
        "t_stat": sheet.t_stat,
        "ic_series_len": len(sheet.ic_series),
    }
    if sheet.quantile is not None:
        out["quantile"] = {
            "forward_window": sheet.quantile.forward_window,
            "quantile_returns": sheet.quantile.quantile_returns,
            "long_short_return": sheet.quantile.long_short_return,
            "monotonicity": sheet.quantile.monotonicity,
        }
    if sheet.turnover is not None:
        out["turnover"] = {
            "avg_daily_turnover": sheet.turnover.avg_daily_turnover,
            "weekly_turnover": sheet.turnover.weekly_turnover,
            "daily_turnover_len": len(sheet.turnover.daily_turnover),
        }
    if sheet.decay is not None:
        out["decay"] = {
            "ic_by_window": sheet.decay.ic_by_window,
            "icir_by_window": sheet.decay.icir_by_window,
            "half_life_days": sheet.decay.half_life_days,
        }
    return out
