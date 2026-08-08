"""完整性指标 (Completeness) — C-01 ~ C-06.

检查点: P1 (源头) / P2 (缓存)
设计原则: 数据缺失即阻断, 因子计算无法容忍 NaN 输入
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.completeness")

# 持仓池标的数 (默认 23, 实际从 positions.json 读取)
DEFAULT_SYMBOL_POOL_SIZE = 23

# OHLCV 必需字段
OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def check_completeness(
    df: pd.DataFrame,
    target_date: date,
    expected_symbols: list[str],
    checkpoint: DQCCheckpoint = DQCCheckpoint.P2_CACHE,
) -> list[DQCEvent]:
    """执行所有完整性检查 (C-01 ~ C-06).

    Args:
        df: 待检查的 DataFrame (要求含 symbol/date/OHLCV 字段)
        target_date: 目标交易日
        expected_symbols: 预期标的池
        checkpoint: 检查点 (P1 或 P2)

    Returns:
        DQC 事件列表 (空列表表示全部通过)
    """
    events: list[DQCEvent] = []

    # C-01: 标的覆盖率
    events.extend(_check_c01_symbol_coverage(df, expected_symbols, checkpoint))

    # C-02: 交易日覆盖率
    events.extend(_check_c02_trading_day_coverage(df, target_date, checkpoint))

    # C-03: 字段缺失率
    events.extend(_check_c03_field_missing_rate(df, checkpoint))

    # C-04: 时间戳连续性
    events.extend(_check_c04_timestamp_continuity(df, target_date, checkpoint))

    # C-05: OHLCV 完整性
    events.extend(_check_c05_ohlcv_completeness(df, checkpoint))

    # C-06: 复权因子完整性 (若存在 adj_factor 字段)
    events.extend(_check_c06_adjfactor_completeness(df, checkpoint))

    return events


# ============================================================
# C-01: 标的覆盖率
# ============================================================
def _check_c01_symbol_coverage(
    df: pd.DataFrame,
    expected_symbols: list[str],
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """C-01: 标的覆盖率 = 存在数据的标的数 / 持仓池标的数.

    阈值:
        ≥ 95%  通过
        90-95% WARN
        < 90%  ERROR
    """
    events: list[DQCEvent] = []
    if not expected_symbols:
        return events

    # 标准化 df 中的标的代码 (去 .SH/.SZ 后缀)
    if "symbol" in df.columns:
        actual_symbols = set(df["symbol"].astype(str).str.split(".").str[0].unique())
    elif "code" in df.columns:
        actual_symbols = set(df["code"].astype(str).str.split(".").str[0].unique())
    else:
        events.append(make_event(
            metric_id="C-01",
            level=DQCLevel.ERROR,
            checkpoint=checkpoint,
            value=0.0,
            threshold=0.95,
            message="缺少 symbol/code 字段, 无法检查标的覆盖率",
        ))
        return events

    expected_set = {str(s).split(".")[0] for s in expected_symbols}
    missing = expected_set - actual_symbols
    coverage = len(expected_set & actual_symbols) / len(expected_set)

    if coverage < 0.90:
        level = DQCLevel.ERROR
    elif coverage < 0.95:
        level = DQCLevel.WARN
    else:
        level = DQCLevel.INFO

    if level != DQCLevel.INFO:
        events.append(make_event(
            metric_id="C-01",
            level=level,
            checkpoint=checkpoint,
            value=coverage,
            threshold=0.95,
            message=f"标的覆盖率 {coverage:.1%} (缺失 {len(missing)} 个: {list(missing)[:5]}...)",
            missing_count=len(missing),
            missing_symbols=list(missing)[:10],
        ))

    return events


# ============================================================
# C-02: 交易日覆盖率
# ============================================================
def _check_c02_trading_day_coverage(
    df: pd.DataFrame,
    target_date: date,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """C-02: 交易日覆盖率 — 目标日数据是否全部到位.

    阈值: 100% (缺一天即 ERROR)
    """
    events: list[DQCEvent] = []
    if "date" not in df.columns:
        return events

    target_str = target_date.strftime("%Y-%m-%d")
    df_dates = df["date"].astype(str)
    has_target = (df_dates == target_str).any()

    if not has_target:
        events.append(make_event(
            metric_id="C-02",
            level=DQCLevel.ERROR,
            checkpoint=checkpoint,
            value=0.0,
            threshold=1.0,
            message=f"目标交易日 {target_str} 数据完全缺失",
            target_date=target_str,
        ))

    return events


# ============================================================
# C-03: 字段缺失率
# ============================================================
def _check_c03_field_missing_rate(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
    threshold: float = 0.05,
) -> list[DQCEvent]:
    """C-03: 字段缺失率 = NaN 计数 / 总单元格.

    阈值: 单字段 < 5%
    """
    events: list[DQCEvent] = []
    if df.empty:
        return events

    total_cells = len(df)
    if total_cells == 0:
        return events

    for col in df.columns:
        if col in ("symbol", "code", "date", "timestamp"):
            continue
        nan_count = int(df[col].isna().sum())
        if nan_count == 0:
            continue
        missing_rate = nan_count / total_cells
        if missing_rate > threshold:
            events.append(make_event(
                metric_id="C-03",
                level=DQCLevel.WARN,
                checkpoint=checkpoint,
                value=missing_rate,
                threshold=threshold,
                message=f"字段 {col} 缺失率 {missing_rate:.1%} (超阈值 {threshold:.1%})",
                field=col,
                nan_count=nan_count,
            ))

    return events


# ============================================================
# C-04: 时间戳连续性
# ============================================================
def _check_c04_timestamp_continuity(
    df: pd.DataFrame,
    target_date: date,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """C-04: 时间戳连续性 — 检查是否存在缺失的日期序列.

    简化版: 仅检查目标日前后是否有断档 (完整版需交易日历).
    阈值: 0 连续缺失
    """
    events: list[DQCEvent] = []
    if "date" not in df.columns or df.empty:
        return events

    # 检查目标日的前一交易日是否存在 (简化: 假设前一自然日)
    target_str = target_date.strftime("%Y-%m-%d")
    df_dates = sorted(df["date"].astype(str).unique())
    if target_str not in df_dates:
        # C-02 已处理, 这里不重复
        return events

    # 简化: 检查 df_dates 中是否存在日期断档 (相差 > 3 天且非周末)
    from datetime import datetime
    try:
        date_objs = [datetime.strptime(d[:10], "%Y-%m-%d").date() for d in df_dates]
        date_objs.sort()
        for i in range(1, len(date_objs)):
            gap = (date_objs[i] - date_objs[i - 1]).days
            # 周末 gap 允许 3 天 (周五→周一)
            if gap > 3:
                events.append(make_event(
                    metric_id="C-04",
                    level=DQCLevel.ERROR,
                    checkpoint=checkpoint,
                    value=float(gap),
                    threshold=3.0,
                    message=f"日期断档 {gap} 天 ({date_objs[i-1]} → {date_objs[i]})",
                    gap_days=gap,
                    from_date=str(date_objs[i - 1]),
                    to_date=str(date_objs[i]),
                ))
    except (ValueError, TypeError) as e:
        logger.warning("C-04 日期解析失败: %s", e)

    return events


# ============================================================
# C-05: OHLCV 完整性
# ============================================================
def _check_c05_ohlcv_completeness(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """C-05: OHLCV 完整性 — 任一字段缺失即阻断该标的.

    阈值: 0% (任一缺失即 ERROR)
    """
    events: list[DQCEvent] = []
    if df.empty:
        return events

    missing_fields = [f for f in OHLCV_FIELDS if f not in df.columns]
    if missing_fields:
        events.append(make_event(
            metric_id="C-05",
            level=DQCLevel.ERROR,
            checkpoint=checkpoint,
            value=float(len(missing_fields)),
            threshold=0.0,
            message=f"OHLCV 字段缺失: {missing_fields}",
            missing_fields=missing_fields,
        ))
        return events

    # 检查每个标的的 OHLCV 是否有 NaN
    symbol_col = "symbol" if "symbol" in df.columns else ("code" if "code" in df.columns else None)
    if symbol_col is None:
        return events

    for symbol, group in df.groupby(symbol_col):
        nan_in_ohlcv = group[list(OHLCV_FIELDS)].isna().any(axis=1)
        nan_count = int(nan_in_ohlcv.sum())
        if nan_count > 0:
            events.append(make_event(
                metric_id="C-05",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(nan_count),
                threshold=0.0,
                message=f"标的 {symbol} OHLCV 缺失 {nan_count} 行",
                symbol=str(symbol),
                nan_count=nan_count,
            ))

    return events


# ============================================================
# C-06: 复权因子完整性
# ============================================================
def _check_c06_adjfactor_completeness(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """C-06: 复权因子完整性 — 若存在 adj_factor 字段则必须无 NaN.

    阈值: 0% (任一缺失即 ERROR)
    """
    events: list[DQCEvent] = []
    if df.empty or "adj_factor" not in df.columns:
        return events

    nan_count = int(df["adj_factor"].isna().sum())
    if nan_count > 0:
        events.append(make_event(
            metric_id="C-06",
            level=DQCLevel.ERROR,
            checkpoint=checkpoint,
            value=float(nan_count),
            threshold=0.0,
            message=f"复权因子 adj_factor 缺失 {nan_count} 行",
            nan_count=nan_count,
        ))

    return events
