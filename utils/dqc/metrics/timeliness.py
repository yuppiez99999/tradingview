"""时效性指标 (Timeliness) — T-01 ~ T-05.

检查点: P1 (源头)
设计原则: 数据延迟超阈值即告警, EOD 到位时间影响后续流程
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd

from utils.datetime_utils import now_bj
from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event

logger = logging.getLogger("dqc.timeliness")

# 日频数据延迟阈值 (分钟)
DAILY_LATENCY_THRESHOLD_MIN = 30  # 15:30 收盘后 30 分钟内应到位

# EOD 到位时间阈值 (分钟)
EOD_ARRIVAL_THRESHOLD_MIN = 60  # 15:30 后 60 分钟内


def check_timeliness(
    df: pd.DataFrame,
    target_date: date,
    checkpoint: DQCCheckpoint = DQCCheckpoint.P1_SOURCE,
    data_arrival_time: datetime | None = None,
) -> list[DQCEvent]:
    """执行所有时效性检查 (T-01 ~ T-05).

    Args:
        df: 待检查的 DataFrame
        target_date: 目标交易日
        checkpoint: 检查点
        data_arrival_time: 数据实际到达时间 (None 则用 now())

    Returns:
        DQC 事件列表
    """
    events: list[DQCEvent] = []

    # T-01: 数据延迟
    events.extend(
        _check_t01_data_latency(df, target_date, checkpoint, data_arrival_time)
    )

    # T-02: 最新数据日期
    events.extend(_check_t02_latest_date(df, target_date, checkpoint))

    # T-03: EOD 到位时间
    events.extend(_check_t03_eod_arrival(target_date, checkpoint, data_arrival_time))

    # T-04 / T-05: 因子计算耗时 / 训练数据就绪 (需在对应阶段调用, 此处跳过)
    # 由 checkpoints 层注入

    return events


# ============================================================
# T-01: 数据延迟
# ============================================================
def _check_t01_data_latency(
    df: pd.DataFrame,
    target_date: date,
    checkpoint: DQCCheckpoint,
    arrival_time: datetime | None,
) -> list[DQCEvent]:
    """T-01: 数据延迟 = now() - 数据时间戳.

    日频场景: 15:30 收盘后 30 分钟内应到位.
    """
    events: list[DQCEvent] = []
    if arrival_time is None:
        arrival_time = now_bj()

    # 期望到位时间: 收盘后 30 分钟
    expected_arrival = (
        datetime.combine(target_date, datetime.min.time())
        + timedelta(hours=15, minutes=30)
        + timedelta(minutes=DAILY_LATENCY_THRESHOLD_MIN)
    )

    if arrival_time > expected_arrival:
        delay_min = (arrival_time - expected_arrival).total_seconds() / 60
        level = DQCLevel.ERROR if delay_min > 30 else DQCLevel.WARN
        events.append(
            make_event(
                metric_id="T-01",
                level=level,
                checkpoint=checkpoint,
                value=float(delay_min),
                threshold=float(DAILY_LATENCY_THRESHOLD_MIN),
                message=f"数据延迟 {delay_min:.0f} 分钟 (期望 {expected_arrival:%H:%M} 到位)",
                arrival_time=arrival_time.isoformat(),
                expected_time=expected_arrival.isoformat(),
            )
        )

    return events


# ============================================================
# T-02: 最新数据日期
# ============================================================
def _check_t02_latest_date(
    df: pd.DataFrame,
    target_date: date,
    checkpoint: DQCCheckpoint,
) -> list[DQCEvent]:
    """T-02: 最新数据日期 = max(日期字段), 应等于目标日.

    落后一天即 ERROR.
    """
    events: list[DQCEvent] = []
    if df.empty or "date" not in df.columns:
        return events

    try:
        df_dates = pd.to_datetime(df["date"]).dt.date
        latest = df_dates.max()
        target = target_date
        if latest < target:
            gap_days = (target - latest).days
            events.append(
                make_event(
                    metric_id="T-02",
                    level=DQCLevel.ERROR,
                    checkpoint=checkpoint,
                    value=float(gap_days),
                    threshold=0.0,
                    message=f"最新数据日期 {latest} 落后目标日 {target} {gap_days} 天",
                    latest_date=str(latest),
                    target_date=str(target),
                )
            )
    except (ValueError, TypeError) as e:
        logger.warning("T-02 日期解析失败: %s", e)

    return events


# ============================================================
# T-03: EOD 到位时间
# ============================================================
def _check_t03_eod_arrival(
    target_date: date,
    checkpoint: DQCCheckpoint,
    arrival_time: datetime | None,
) -> list[DQCEvent]:
    """T-03: EOD 到位时间 — 15:30 收盘后 60 分钟内应完成全部数据落盘."""
    events: list[DQCEvent] = []
    if arrival_time is None:
        return events  # 无实际到达时间时不告警

    expected_deadline = (
        datetime.combine(target_date, datetime.min.time())
        + timedelta(hours=15, minutes=30)
        + timedelta(minutes=EOD_ARRIVAL_THRESHOLD_MIN)
    )

    if arrival_time > expected_deadline:
        delay_min = (arrival_time - expected_deadline).total_seconds() / 60
        events.append(
            make_event(
                metric_id="T-03",
                level=DQCLevel.WARN,
                checkpoint=checkpoint,
                value=float(delay_min),
                threshold=float(EOD_ARRIVAL_THRESHOLD_MIN),
                message=f"EOD 到位超时 {delay_min:.0f} 分钟 (期望 {expected_deadline:%H:%M} 完成)",
                arrival_time=arrival_time.isoformat(),
                deadline=expected_deadline.isoformat(),
            )
        )

    return events
