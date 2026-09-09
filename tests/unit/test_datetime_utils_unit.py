"""utils.datetime_utils 单元测试 — 时区语义单一入口."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from utils.datetime_utils import CN_TZ, now_bj, now_utc, now_utc_naive, today_bj, utc_iso


def test_cn_tz_is_plus8():
    """北京时区固定 UTC+8 (无夏令时)."""
    assert CN_TZ.utcoffset(None) == timedelta(hours=8)


def test_now_utc_is_aware_utc():
    """now_utc 返回 aware UTC (tzinfo 非空, 偏移为 0)."""
    t = now_utc()
    assert t.tzinfo is not None
    assert t.utcoffset() == timedelta(0)


def test_now_utc_naive_is_naive_utc():
    """now_utc_naive 返回 naive UTC (无 tzinfo, 与 aware UTC 时刻一致)."""
    t = now_utc_naive()
    assert t.tzinfo is None
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = t - utc_now
    # 允许分钟级误差
    assert timedelta(minutes=-10) <= delta <= timedelta(minutes=10)


def test_now_bj_is_naive_beijing():
    """now_bj 返回 naive 北京时间 (UTC+8 时刻, 无 tzinfo)."""
    t = now_bj()
    assert t.tzinfo is None
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = t - utc_now
    # 允许分钟级误差, 覆盖测试耗时
    assert timedelta(hours=7, minutes=50) <= delta <= timedelta(hours=8, minutes=10)


def test_utc_iso_ends_with_z():
    """utc_iso 带 Z 后缀, 可解析为 aware datetime."""
    s = utc_iso()
    assert s.endswith("Z")
    parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_today_bj_matches_now_bj_date():
    """today_bj 等于 now_bj 的日期部分."""
    assert today_bj() == now_bj().date()