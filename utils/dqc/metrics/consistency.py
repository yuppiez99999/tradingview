"""一致性指标 (Consistency) — X-01 ~ X-05.

检查点: P2 (缓存) — 跨源校验与历史不变性
设计原则:
    1. 跨源校验需提供 cross_source_df (None 时跳过, 不算违规)
    2. 历史值不变性是硬约束 (HC-DQC3): 任何历史值变更即 ERROR
    3. 指数成分股一致性检查标的池变化

硬约束:
    - HC-DQC3: 历史数据只标记不修改 (X-03 检测变更但不修改)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.consistency")

# 跨源校验的字段名映射 (主源 → 跨源)
DEFAULT_PRICE_FIELDS = ("open", "high", "low", "close")
DEFAULT_VOLUME_FIELD = "volume"


def check_consistency(
    df: pd.DataFrame,
    cross_source_df: pd.DataFrame | None = None,
    history_cache: pd.DataFrame | None = None,
    expected_symbols: list[str] | None = None,
    checkpoint: DQCCheckpoint = DQCCheckpoint.P2_CACHE,
) -> list[DQCEvent]:
    """X 维度检查入口: X-01, X-02, X-03, X-05.

    Args:
        df: 主源 DataFrame (待检查)
        cross_source_df: 跨源 DataFrame (None 时跳过 X-01/X-02 跨源校验)
        history_cache: 历史缓存 DataFrame (None 时跳过 X-03)
        expected_symbols: 预期标的池 (None 时跳过 X-05)
        checkpoint: 检查点

    Returns:
        DQC 事件列表
    """
    events: list[DQCEvent] = []

    if df.empty:
        return events

    # X-01: 跨源价格偏差 (cross_source_df 为 None 时跳过)
    if cross_source_df is not None and not cross_source_df.empty:
        events.extend(_check_x01_cross_source_price(df, cross_source_df, checkpoint))

    # X-02: 跨源成交量偏差 (cross_source_df 为 None 时跳过)
    if cross_source_df is not None and not cross_source_df.empty:
        events.extend(_check_x02_cross_source_volume(df, cross_source_df, checkpoint))

    # X-03: 历史值不变性 (history_cache 为 None 时跳过)
    if history_cache is not None and not history_cache.empty:
        events.extend(_check_x03_history_invariance(df, history_cache, checkpoint))

    # X-05: 指数成分股一致 (expected_symbols 为 None 时跳过)
    if expected_symbols is not None:
        events.extend(_check_x05_index_consistency(df, expected_symbols, checkpoint))

    return events


# ============================================================
# X-01: 跨源价格偏差
# ============================================================
def _check_x01_cross_source_price(
    df: pd.DataFrame,
    cross_df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
    threshold_warn: float = 0.001,  # 0.1%
    threshold_error: float = 0.01,  # 1%
) -> list[DQCEvent]:
    """X-01: 跨源价格偏差 = |price_A - price_B| / price_B.

    阈值:
        < 0.1%: INFO (通过)
        0.1% - 1%: WARN
        > 1%: ERROR

    对比方式: 按 (symbol, date) join 后逐字段比对
    """
    events: list[DQCEvent] = []

    # 识别主键
    symbol_col = (
        "symbol"
        if "symbol" in df.columns
        else ("code" if "code" in df.columns else None)
    )
    date_col = (
        "date"
        if "date" in df.columns
        else ("datetime" if "datetime" in df.columns else None)
    )
    if symbol_col is None or date_col is None:
        return events

    # 跨源 df 也需要相同主键
    cross_symbol_col = (
        "symbol"
        if "symbol" in cross_df.columns
        else ("code" if "code" in cross_df.columns else None)
    )
    cross_date_col = (
        "date"
        if "date" in cross_df.columns
        else ("datetime" if "datetime" in cross_df.columns else None)
    )
    if cross_symbol_col is None or cross_date_col is None:
        return events

    # 找共同的 price 字段
    price_fields = [
        f for f in DEFAULT_PRICE_FIELDS if f in df.columns and f in cross_df.columns
    ]
    if not price_fields:
        return events

    # 重命名跨源列以避免冲突
    cross_renamed = cross_df[[cross_symbol_col, cross_date_col] + price_fields].rename(
        columns={
            cross_symbol_col: "_symbol",
            cross_date_col: "_date",
            **{f: f"_cross_{f}" for f in price_fields},
        }
    )
    main_renamed = df[[symbol_col, date_col] + price_fields].rename(
        columns={symbol_col: "_symbol", date_col: "_date"}
    )

    # inner join
    merged = main_renamed.merge(cross_renamed, on=["_symbol", "_date"], how="inner")
    if merged.empty:
        return events

    for field in price_fields:
        cross_field = f"_cross_{field}"
        # 排除 0 值 (除零保护)
        valid = merged[(merged[field] != 0) & (merged[cross_field] != 0)]
        if valid.empty:
            continue
        diff = (valid[field] - valid[cross_field]).abs() / valid[cross_field]
        max_diff = float(diff.max())
        violation_count = int((diff > threshold_warn).sum())

        if max_diff > threshold_error:
            level = DQCLevel.ERROR
        elif max_diff > threshold_warn:
            level = DQCLevel.WARN
        else:
            continue

        # 取最严重的样例
        worst_idx = diff.idxmax()
        worst_symbol = str(merged.loc[worst_idx, "_symbol"])
        worst_date = str(merged.loc[worst_idx, "_date"])

        events.append(
            make_event(
                metric_id="X-01",
                level=level,
                checkpoint=checkpoint,
                value=max_diff,
                threshold=threshold_warn,
                message=f"跨源价格偏差 {field} max={max_diff:.4%} ({level.name}), 最严重: {worst_symbol}@{worst_date}",
                symbol=worst_symbol,
                field=field,
                max_diff=max_diff,
                violation_count=violation_count,
                total_count=int(len(valid)),
                worst_symbol=worst_symbol,
                worst_date=worst_date,
            )
        )

    return events


# ============================================================
# X-02: 跨源成交量偏差
# ============================================================
def _check_x02_cross_source_volume(
    df: pd.DataFrame,
    cross_df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
    threshold_warn: float = 0.01,  # 1%
    threshold_error: float = 0.05,  # 5%
) -> list[DQCEvent]:
    """X-02: 跨源成交量偏差 = |vol_A - vol_B| / vol_B.

    阈值 (成交量比价格波动更大):
        < 1%: INFO
        1% - 5%: WARN
        > 5%: ERROR
    """
    events: list[DQCEvent] = []

    symbol_col = (
        "symbol"
        if "symbol" in df.columns
        else ("code" if "code" in df.columns else None)
    )
    date_col = (
        "date"
        if "date" in df.columns
        else ("datetime" if "datetime" in df.columns else None)
    )
    if symbol_col is None or date_col is None:
        return events

    if (
        DEFAULT_VOLUME_FIELD not in df.columns
        or DEFAULT_VOLUME_FIELD not in cross_df.columns
    ):
        return events

    cross_symbol_col = (
        "symbol"
        if "symbol" in cross_df.columns
        else ("code" if "code" in cross_df.columns else None)
    )
    cross_date_col = (
        "date"
        if "date" in cross_df.columns
        else ("datetime" if "datetime" in cross_df.columns else None)
    )
    if cross_symbol_col is None or cross_date_col is None:
        return events

    # 重命名
    cross_renamed = cross_df[
        [cross_symbol_col, cross_date_col, DEFAULT_VOLUME_FIELD]
    ].rename(
        columns={
            cross_symbol_col: "_symbol",
            cross_date_col: "_date",
            DEFAULT_VOLUME_FIELD: "_cross_vol",
        }
    )
    main_renamed = df[[symbol_col, date_col, DEFAULT_VOLUME_FIELD]].rename(
        columns={symbol_col: "_symbol", date_col: "_date", DEFAULT_VOLUME_FIELD: "vol"}
    )

    merged = main_renamed.merge(cross_renamed, on=["_symbol", "_date"], how="inner")
    if merged.empty:
        return events

    # 排除 0 值
    valid = merged[(merged["vol"] != 0) & (merged["_cross_vol"] != 0)]
    if valid.empty:
        return events

    diff = (valid["vol"] - valid["_cross_vol"]).abs() / valid["_cross_vol"]
    max_diff = float(diff.max())
    violation_count = int((diff > threshold_warn).sum())

    if max_diff > threshold_error:
        level = DQCLevel.ERROR
    elif max_diff > threshold_warn:
        level = DQCLevel.WARN
    else:
        return events

    worst_idx = diff.idxmax()
    worst_symbol = str(merged.loc[worst_idx, "_symbol"])
    worst_date = str(merged.loc[worst_idx, "_date"])

    events.append(
        make_event(
            metric_id="X-02",
            level=level,
            checkpoint=checkpoint,
            value=max_diff,
            threshold=threshold_warn,
            message=f"跨源成交量偏差 max={max_diff:.4%} ({level.name}), 最严重: {worst_symbol}@{worst_date}",
            symbol=worst_symbol,
            max_diff=max_diff,
            violation_count=violation_count,
            total_count=int(len(valid)),
            worst_symbol=worst_symbol,
            worst_date=worst_date,
        )
    )

    return events


# ============================================================
# X-03: 历史值不变性 (HC-DQC3 硬约束)
# ============================================================
def _check_x03_history_invariance(
    df: pd.DataFrame,
    history_cache: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """X-03: 历史值不变性 — 对比今日读取的历史数据与昨日缓存.

    硬约束 (HC-DQC3): 历史数据只标记不修改, 任何变更即 ERROR.

    检查方式: 找共同的历史日期 (history_cache 中存在, df 中也存在的日期),
    对比相同 (symbol, date, field) 的值是否一致.
    """
    events: list[DQCEvent] = []

    symbol_col = (
        "symbol"
        if "symbol" in df.columns
        else ("code" if "code" in df.columns else None)
    )
    date_col = (
        "date"
        if "date" in df.columns
        else ("datetime" if "datetime" in df.columns else None)
    )
    if symbol_col is None or date_col is None:
        return events

    # 获取 history_cache 的主键列名
    h_symbol_col = (
        "symbol"
        if "symbol" in history_cache.columns
        else ("code" if "code" in history_cache.columns else None)
    )
    h_date_col = (
        "date"
        if "date" in history_cache.columns
        else ("datetime" if "datetime" in history_cache.columns else None)
    )
    if h_symbol_col is None or h_date_col is None:
        return events

    # 共同的可比字段 (排除主键, 聚焦 OHLCV + adj_factor)
    comparable_fields = [
        f
        for f in ("open", "high", "low", "close", "volume", "adj_factor")
        if f in df.columns and f in history_cache.columns
    ]
    if not comparable_fields:
        return events

    # 标准化主键为字符串
    df_keys = df[[symbol_col, date_col]].astype(str)
    df_norm = df.copy()
    df_norm["_key"] = df_keys[symbol_col] + "|" + df_keys[date_col]

    h_keys = history_cache[[h_symbol_col, h_date_col]].astype(str)
    h_norm = history_cache.copy()
    h_norm["_key"] = h_keys[h_symbol_col] + "|" + h_keys[h_date_col]

    # inner join (只比对共同的历史日期)
    merged = df_norm[["_key"] + comparable_fields].merge(
        h_norm[["_key"] + comparable_fields].rename(
            columns={f: f"_h_{f}" for f in comparable_fields}
        ),
        on="_key",
        how="inner",
    )
    if merged.empty:
        return events

    # 逐字段比对
    total_violations = 0
    field_violations: dict[str, int] = {}
    for field in comparable_fields:
        h_field = f"_h_{field}"
        # 数值字段用 np.isclose 容忍浮点误差
        try:
            diff_mask = ~np.isclose(
                merged[field].astype(float),
                merged[h_field].astype(float),
                rtol=1e-6,
                atol=1e-8,
            )
        except (ValueError, TypeError):
            # 非数值字段用严格相等
            diff_mask = merged[field] != merged[h_field]

        n_violations = int(diff_mask.sum())
        if n_violations > 0:
            field_violations[field] = n_violations
            total_violations += n_violations

    if total_violations > 0:
        # 取前 3 个违规样例
        sample_keys = []
        for field in comparable_fields:
            h_field = f"_h_{field}"
            try:
                diff_mask = ~np.isclose(
                    merged[field].astype(float),
                    merged[h_field].astype(float),
                    rtol=1e-6,
                    atol=1e-8,
                )
            except (ValueError, TypeError):
                diff_mask = merged[field] != merged[h_field]
            if diff_mask.any():
                for idx in merged[diff_mask]["_key"].head(3):
                    sample_keys.append(f"{idx} ({field})")

        events.append(
            make_event(
                metric_id="X-03",
                level=DQCLevel.ERROR,
                checkpoint=checkpoint,
                value=float(total_violations),
                threshold=0.0,
                message=f"历史值不变性违反 (HC-DQC3): {total_violations} 处历史数据被修改, 字段违规数={field_violations}",  # noqa: E501
                violation_count=total_violations,
                field_violations=field_violations,
                sample_keys=sample_keys[:5],
                compared_rows=int(len(merged)),
            )
        )

    return events


# ============================================================
# X-05: 指数成分股一致
# ============================================================
def _check_x05_index_consistency(
    df: pd.DataFrame,
    expected_symbols: list[str],
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """X-05: 指数成分股一致 — 当前 df 的标的池与预期标的池对比.

    阈值:
        完全一致: 通过
        缺失/新增 ≤ 2: WARN (允许小幅调整)
        缺失/新增 > 2: ERROR
    """
    events: list[DQCEvent] = []
    if not expected_symbols:
        return events

    symbol_col = (
        "symbol"
        if "symbol" in df.columns
        else ("code" if "code" in df.columns else None)
    )
    if symbol_col is None:
        return events

    # 标准化标的代码 (去后缀)
    actual_symbols = set(df[symbol_col].astype(str).str.split(".").str[0].unique())
    expected_set = {str(s).split(".")[0] for s in expected_symbols}

    missing = expected_set - actual_symbols
    extra = actual_symbols - expected_set

    if not missing and not extra:
        return events

    total_diff = len(missing) + len(extra)
    if total_diff > 2:
        level = DQCLevel.ERROR
    else:
        level = DQCLevel.WARN

    events.append(
        make_event(
            metric_id="X-05",
            level=level,
            checkpoint=checkpoint,
            value=float(total_diff),
            threshold=2.0,
            message=f"标的池不一致: 缺失 {len(missing)} 个, 新增 {len(extra)} 个",
            missing_count=len(missing),
            extra_count=len(extra),
            missing_symbols=sorted(missing)[:10],
            extra_symbols=sorted(extra)[:10],
        )
    )

    return events
