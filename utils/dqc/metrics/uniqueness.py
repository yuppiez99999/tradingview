"""唯一性指标 (Uniqueness) — U-01 ~ U-03.

检查点: P2 (缓存) / P3 (因子)
设计原则: 主键重复即阻断, 因子重复计算会污染样本
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.uniqueness")

# 标的代码正则 (6 位数字, 可选 .SH/.SZ 后缀)
SYMBOL_CODE_PATTERN = re.compile(r"^\d{6}(\.(SH|SZ|BJ))?$")


def check_uniqueness(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint = DQCCheckpoint.P2_CACHE,
) -> list[DQCEvent]:
    """执行所有唯一性检查 (U-01 ~ U-03).

    Args:
        df: 待检查的 DataFrame
        checkpoint: 检查点

    Returns:
        DQC 事件列表
    """
    events: list[DQCEvent] = []

    # U-01: 主键去重
    events.extend(_check_u01_primary_key_dedup(df, checkpoint))

    # U-02: 因子重复计算 (在 P3 检查点用)
    # (由 P3 checkpoint 注入检查)

    # U-03: 标的代码规范
    events.extend(_check_u03_symbol_code_format(df, checkpoint))

    return events


# ============================================================
# U-01: 主键去重
# ============================================================
def _check_u01_primary_key_dedup(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """U-01: 主键去重 — (标的, 日期) 组合必须唯一.

    阈值: 0 重复
    """
    events: list[DQCEvent] = []
    if df.empty:
        return events

    # 识别主键列
    symbol_col = "symbol" if "symbol" in df.columns else ("code" if "code" in df.columns else None)
    date_col = "date" if "date" in df.columns else ("datetime" if "datetime" in df.columns else None)
    if symbol_col is None or date_col is None:
        return events

    # 检查重复
    duplicates = df.duplicated(subset=[symbol_col, date_col], keep=False)
    dup_count = int(duplicates.sum())

    if dup_count > 0:
        dup_df = df[duplicates]
        # 统计每个标的的重复数
        symbol_dup_counts = dup_df.groupby(symbol_col).size().to_dict()
        events.append(make_event(
            metric_id="U-01",
            level=DQCLevel.ERROR,
            checkpoint=checkpoint,
            value=float(dup_count),
            threshold=0.0,
            message=f"主键 (symbol, date) 重复 {dup_count} 行 (涉及 {len(symbol_dup_counts)} 标的)",
            violation_count=dup_count,
            symbol_dup_counts={str(k): int(v) for k, v in symbol_dup_counts.items()},
        ))

    return events


# ============================================================
# U-03: 标的代码规范
# ============================================================
def _check_u03_symbol_code_format(
    df: pd.DataFrame,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    r"""U-03: 标的代码规范 — code 应匹配 ^\d{6}(\.(SH|SZ|BJ))?$.

    阈值: 100% 符合
    """
    events: list[DQCEvent] = []
    if df.empty:
        return events

    symbol_col = "symbol" if "symbol" in df.columns else ("code" if "code" in df.columns else None)
    if symbol_col is None:
        return events

    codes = df[symbol_col].astype(str)
    invalid_mask = ~codes.apply(lambda c: bool(SYMBOL_CODE_PATTERN.match(c)))
    invalid_count = int(invalid_mask.sum())

    if invalid_count > 0:
        invalid_codes = codes[invalid_mask].unique().tolist()[:10]
        events.append(make_event(
            metric_id="U-03",
            level=DQCLevel.WARN,
            checkpoint=checkpoint,
            value=float(invalid_count),
            threshold=0.0,
            message=f"标的代码不规范 ({invalid_count} 行, 样例: {invalid_codes})",
            violation_count=invalid_count,
            invalid_codes=invalid_codes,
        ))

    return events
