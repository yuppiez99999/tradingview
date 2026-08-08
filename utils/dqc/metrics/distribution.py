"""分布稳定性指标 (Distribution Drift) — F-01 ~ F-04.

检查点: P3 (因子) / P4 (样本) / P5 (预测)
设计原则: 复用 DriftMonitor.compute_psi() 工业级实现, 不重复造轮子

工业级 PSI 阈值 (与 drift_monitor.py 一致):
    - PSI < 0.1:    无显著变化 (INFO)
    - 0.1 <= PSI < 0.25: 需观察 (WARN)
    - 0.25 <= PSI < 0.5: 需告警 (ERROR)
    - PSI >= 0.5:    需回滚 (CRITICAL)
"""

from __future__ import annotations

import logging

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.distribution")

# 极值频率的 σ 倍数 (3σ = 99.7% 置信区间)
EXTREME_SIGMA = 3.0


def check_distribution_drift(
    baseline_df: pd.DataFrame,
    current_df: pd.DataFrame,
    factor_cols: list[str],
    checkpoint: DQCCheckpoint = DQCCheckpoint.P3_FACTOR,
) -> list[DQCEvent]:
    """F 维度检查入口: F-01 ~ F-04.

    Args:
        baseline_df: 基线分布 DataFrame (如 T-30 ~ T-1 的因子值)
        current_df: 当前分布 DataFrame (如 T 日的因子值)
        factor_cols: 待检查的因子列名列表
        checkpoint: 检查点 (默认 P3 因子质量)

    Returns:
        DQC 事件列表 (空列表表示全部通过)
    """
    events: list[DQCEvent] = []

    if not factor_cols:
        return events

    if baseline_df.empty or current_df.empty:
        logger.warning("F 维度检查跳过: baseline=%d rows, current=%d rows",
                       len(baseline_df), len(current_df))
        return events

    for col in factor_cols:
        if col not in baseline_df.columns or col not in current_df.columns:
            events.append(make_event(
                metric_id="F-01",
                level=DQCLevel.WARN,
                checkpoint=checkpoint,
                value=0.0,
                threshold=0.1,
                message=f"因子列 {col} 在 baseline 或 current 中不存在, 跳过漂移检查",
                factor=col,
            ))
            continue

        baseline_series = baseline_df[col].dropna()
        current_series = current_df[col].dropna()

        if len(baseline_series) < 2 or len(current_series) < 2:
            continue

        # F-01: 因子 PSI
        events.extend(_check_f01_psi(baseline_series, current_series, col, checkpoint))

        # F-02: 均值漂移
        events.extend(_check_f02_mean_drift(baseline_series, current_series, col, checkpoint))

        # F-03: 方差漂移
        events.extend(_check_f03_variance_drift(baseline_series, current_series, col, checkpoint))

        # F-04: 极值频率
        events.extend(_check_f04_extreme_freq(baseline_series, current_series, col, checkpoint))

    return events


# ============================================================
# F-01: 因子 PSI (Population Stability Index)
# ============================================================
def _check_f01_psi(
    baseline: pd.Series,
    current: pd.Series,
    factor: str,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """F-01: 因子 PSI — 调用 DriftMonitor.compute_psi().

    阈值 (工业级):
        PSI < 0.1:       INFO (无显著变化)
        0.1 <= PSI < 0.25: WARN (需观察)
        0.25 <= PSI < 0.5: ERROR (需告警)
        PSI >= 0.5:       CRITICAL (需回滚)
    """
    events: list[DQCEvent] = []
    try:
        from utils.alpha.drift_monitor import compute_psi
        psi = float(compute_psi(baseline, current, n_bins=10))
    except (ImportError, RuntimeError, ValueError, TypeError) as e:
        logger.warning("F-01 PSI 计算失败 (factor=%s): %s", factor, e)
        events.append(make_event(
            metric_id="F-01",
            level=DQCLevel.WARN,
            checkpoint=checkpoint,
            value=0.0,
            threshold=0.1,
            message=f"因子 {factor} PSI 计算异常: {e}",
            factor=factor,
            error=str(e),
        ))
        return events

    # 级别判定
    if psi >= 0.5:
        level = DQCLevel.CRITICAL
    elif psi >= 0.25:
        level = DQCLevel.ERROR
    elif psi >= 0.1:
        level = DQCLevel.WARN
    else:
        # PSI < 0.1, 无显著变化, 不产生事件
        return events

    events.append(make_event(
        metric_id="F-01",
        level=level,
        checkpoint=checkpoint,
        value=psi,
        threshold=0.1,
        message=f"因子 {factor} PSI={psi:.4f} ({level.name})",
        factor=factor,
        psi=psi,
        baseline_size=int(len(baseline)),
        current_size=int(len(current)),
    ))
    return events


# ============================================================
# F-02: 均值漂移
# ============================================================
def _check_f02_mean_drift(
    baseline: pd.Series,
    current: pd.Series,
    factor: str,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """F-02: 均值漂移 — |mean_cur - mean_base| / std_base.

    阈值:
        > 0.5:  WARN
        > 1.0:  ERROR
        > 2.0:  CRITICAL
    """
    events: list[DQCEvent] = []
    std_base = float(baseline.std())
    if std_base < 1e-10:
        # 基线方差为 0, 无法计算漂移率, 跳过
        return events

    mean_base = float(baseline.mean())
    mean_cur = float(current.mean())
    drift_ratio = abs(mean_cur - mean_base) / std_base

    if drift_ratio > 2.0:
        level = DQCLevel.CRITICAL
    elif drift_ratio > 1.0:
        level = DQCLevel.ERROR
    elif drift_ratio > 0.5:
        level = DQCLevel.WARN
    else:
        return events

    events.append(make_event(
        metric_id="F-02",
        level=level,
        checkpoint=checkpoint,
        value=drift_ratio,
        threshold=0.5,
        message=f"因子 {factor} 均值漂移 {drift_ratio:.3f}σ (baseline mean={mean_base:.4f}, current mean={mean_cur:.4f})",
        factor=factor,
        drift_ratio=drift_ratio,
        baseline_mean=mean_base,
        current_mean=mean_cur,
        baseline_std=std_base,
    ))
    return events


# ============================================================
# F-03: 方差漂移
# ============================================================
def _check_f03_variance_drift(
    baseline: pd.Series,
    current: pd.Series,
    factor: str,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """F-03: 方差漂移 — std_cur / std_base 比率.

    阈值 (双侧):
        < 0.25 或 > 4.0: ERROR
        < 0.5  或 > 2.0: WARN
        其他: 通过
    """
    events: list[DQCEvent] = []
    std_base = float(baseline.std())
    std_cur = float(current.std())
    if std_base < 1e-10:
        return events

    ratio = std_cur / std_base

    # 双侧判定
    if ratio < 0.25 or ratio > 4.0:
        level = DQCLevel.ERROR
    elif ratio < 0.5 or ratio > 2.0:
        level = DQCLevel.WARN
    else:
        return events

    direction = "收缩" if ratio < 1.0 else "放大"
    events.append(make_event(
        metric_id="F-03",
        level=level,
        checkpoint=checkpoint,
        value=ratio,
        threshold=2.0,
        message=f"因子 {factor} 方差{direction} {ratio:.3f}x (baseline std={std_base:.4f}, current std={std_cur:.4f})",
        factor=factor,
        variance_ratio=ratio,
        baseline_std=std_base,
        current_std=std_cur,
        direction=direction,
    ))
    return events


# ============================================================
# F-04: 极值频率
# ============================================================
def _check_f04_extreme_freq(
    baseline: pd.Series,
    current: pd.Series,
    factor: str,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """F-04: 极值频率 — 当前分布超出基线 3σ 的比例.

    阈值:
        > 10%: ERROR
        > 5%:  WARN
        其他: 通过
    """
    events: list[DQCEvent] = []
    mean_base = float(baseline.mean())
    std_base = float(baseline.std())
    if std_base < 1e-10:
        return events

    lower_bound = mean_base - EXTREME_SIGMA * std_base
    upper_bound = mean_base + EXTREME_SIGMA * std_base

    # 计算当前分布中超出基线 3σ 的比例
    extreme_mask = (current < lower_bound) | (current > upper_bound)
    extreme_count = int(extreme_mask.sum())
    extreme_freq = extreme_count / len(current)

    if extreme_freq > 0.10:
        level = DQCLevel.ERROR
    elif extreme_freq > 0.05:
        level = DQCLevel.WARN
    else:
        return events

    events.append(make_event(
        metric_id="F-04",
        level=level,
        checkpoint=checkpoint,
        value=extreme_freq,
        threshold=0.05,
        message=f"因子 {factor} 极值频率 {extreme_freq:.1%} ({extreme_count}/{len(current)} 超出基线 {EXTREME_SIGMA}σ)",
        factor=factor,
        extreme_freq=extreme_freq,
        extreme_count=extreme_count,
        total_count=int(len(current)),
        sigma=EXTREME_SIGMA,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    ))
    return events
