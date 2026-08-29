"""P0 回归测试 — 交易日历缓存投毒防御 (2026-08-11)

背景
----
`config/trade_calendar_cache/trade_dates_2026.json` 曾被写入一份 261 天的
"全年工作日"假日历 (= 365 - 104 个周末), 包含元旦/春节/劳动节/国庆等全部
法定节假日。由于原实现读缓存时不做任何校验、缓存永不过期, 该污染数据被
永久固化, 导致:

    is_trading_day(date(2026, 10, 1)) -> True   # 国庆被判为交易日

危害路径 (均为生产 P0 入口):
    - daily_trade_executor.py:628  交易执行唯一日期守卫失效 -> 节假日生成交易指令
    - daily_trade_executor.py:249  剩余交易日多算 19 天 -> 建仓节奏被稀释 ~7.3%
    - daily_trade_executor.py:1222 次日计划指向节假日

修复 (utils/trade_calendar.py):
    L1 `_validate_year_dates` 读/写双向校验, 污染缓存自动隔离为 .json.invalid
    L2 降级模式兜底公历固定节假日 (元旦/劳动节/国庆)
    L3 `get_calendar_status()` 暴露精确/降级模式供调用方判断能否自动下单

本测试锁定上述三层防御, 防止回归。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from utils import trade_calendar
from utils.trade_calendar import (
    _is_fixed_holiday,
    _validate_year_dates,
)

pytestmark = [pytest.mark.regression, pytest.mark.p0]


def _weekday_only_calendar(year: int) -> set[str]:
    """构造污染数据: 仅排除周末的"全年工作日"假日历 (复现历史 bug)."""
    d = date(year, 1, 1)
    out: set[str] = set()
    while d.year == year:
        if d.weekday() < 5:
            out.add(d.isoformat())
        d += timedelta(days=1)
    return out


# ============================================================
# L1: 缓存合理性校验
# ============================================================


class TestValidateYearDates:
    """`_validate_year_dates` 必须拦截三类污染数据."""

    def test_rejects_weekday_only_calendar(self):
        """261 天的"全年工作日"假日历必须被拒 (历史 bug 原始形态)."""
        poisoned = _weekday_only_calendar(2026)
        assert len(poisoned) == 261, "2026 年工作日应为 261 天, 用于复现原始污染"
        ok, reason = _validate_year_dates(2026, poisoned)
        assert ok is False
        assert "超出合理区间" in reason

    def test_rejects_calendar_containing_weekend(self):
        """含周末的日历必须被拒."""
        dates = {d.isoformat() for d in _sample_valid_dates(2026)}
        dates.add("2026-07-11")  # 周六
        ok, reason = _validate_year_dates(2026, dates)
        assert ok is False
        assert "含周末" in reason

    def test_rejects_calendar_containing_fixed_holiday(self):
        """含元旦/劳动节/国庆的日历必须被拒."""
        dates = {d.isoformat() for d in _sample_valid_dates(2026)}
        dates.add("2026-10-01")  # 国庆
        ok, reason = _validate_year_dates(2026, dates)
        assert ok is False
        assert "法定节假日" in reason

    def test_rejects_cross_year_pollution(self):
        """混入其它年份的日期必须被拒."""
        dates = {d.isoformat() for d in _sample_valid_dates(2026)}
        dates.add("2025-03-03")
        ok, reason = _validate_year_dates(2026, dates)
        assert ok is False
        assert "不属于" in reason

    def test_rejects_empty(self):
        ok, reason = _validate_year_dates(2026, set())
        assert ok is False
        assert reason == "空集合"

    def test_accepts_realistic_calendar(self):
        """合规日历应通过校验."""
        dates = {d.isoformat() for d in _sample_valid_dates(2026)}
        ok, reason = _validate_year_dates(2026, dates)
        assert ok is True, f"合规日历不应被拒: {reason}"


def _sample_valid_dates(year: int) -> list[date]:
    """构造一份 242 天、无周末、无固定节假日的合规日历."""
    d = date(year, 1, 1)
    out: list[date] = []
    while d.year == year and len(out) < 242:
        if d.weekday() < 5 and not _is_fixed_holiday(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# ============================================================
# L1: 污染缓存自动隔离
# ============================================================


class TestPoisonedCacheQuarantine:
    """污染缓存应被隔离为 .json.invalid, 而非静默采信."""

    def test_poisoned_cache_is_quarantined_and_not_returned(
        self, tmp_path, monkeypatch
    ):
        year = 2026
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / f"trade_dates_{year}.json"
        cache_file.write_text(
            json.dumps(sorted(_weekday_only_calendar(year))), encoding="utf-8"
        )

        # allow_fetch=False 隔离网络依赖: 污染缓存被拒后应返回空集合
        result = trade_calendar._load_year_dates(year, allow_fetch=False)

        assert result == set(), "污染缓存绝不能被当作有效日历返回"
        assert not cache_file.exists(), "污染缓存应被改名隔离"
        assert (
            tmp_path / f"trade_dates_{year}.json.invalid"
        ).exists(), "应保留 .invalid 现场供排查"

    def test_valid_cache_is_returned_intact(self, tmp_path, monkeypatch):
        year = 2026
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        valid = sorted(d.isoformat() for d in _sample_valid_dates(year))
        (tmp_path / f"trade_dates_{year}.json").write_text(
            json.dumps(valid), encoding="utf-8"
        )
        result = trade_calendar._load_year_dates(year, allow_fetch=False)
        assert result == set(valid)


# ============================================================
# L2: 降级模式的固定节假日兜底
# ============================================================


class TestDegradedModeHolidayFallback:
    """即使日历不可用而降级到"仅判周末", 也不得把固定节假日当交易日."""

    @pytest.mark.parametrize(
        "day,label",
        [
            (date(2026, 1, 1), "元旦"),
            (date(2026, 5, 1), "劳动节"),
            (date(2026, 10, 1), "国庆"),
            (date(2026, 10, 2), "国庆"),
        ],
    )
    def test_fixed_holidays_rejected_in_degraded_mode(
        self, day, label, tmp_path, monkeypatch
    ):
        # 空缓存目录 + 禁用 akshare => 强制进入降级模式
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(
            trade_calendar, "_fetch_trade_dates_via_akshare", lambda year: None
        )
        assert day.weekday() < 5, f"{label} 用例须取工作日才有意义"
        assert (
            trade_calendar.is_trading_day(day) is False
        ), f"降级模式下 {label} {day} 不得判为交易日"

    def test_normal_weekday_still_trading_in_degraded_mode(self, tmp_path, monkeypatch):
        """降级不得矫枉过正: 普通工作日仍应是交易日."""
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(
            trade_calendar, "_fetch_trade_dates_via_akshare", lambda year: None
        )
        assert trade_calendar.is_trading_day(date(2026, 7, 13)) is True

    def test_next_trading_day_skips_fixed_holiday_in_degraded_mode(
        self, tmp_path, monkeypatch
    ):
        """降级模式下 next_trading_day 不得返回固定节假日."""
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(
            trade_calendar, "_fetch_trade_dates_via_akshare", lambda year: None
        )
        # 2026-09-30 周三 -> 次日 10-01 国庆, 应继续向后跳
        nxt = trade_calendar.next_trading_day("2026-09-30")
        assert nxt not in ("2026-10-01", "2026-10-02", "2026-10-03")


# ============================================================
# L3: 降级状态可观测
# ============================================================


class TestCalendarStatusObservability:
    """调用方需能判断当前处于精确模式还是降级模式."""

    def test_status_reports_degraded_when_no_calendar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        st = trade_calendar.get_calendar_status(2026)
        assert st["mode"] == "degraded"
        assert st["safe_for_trading"] is False, "降级模式必须显式标记为不适合自动交易"

    def test_status_reports_exact_with_valid_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        valid = sorted(d.isoformat() for d in _sample_valid_dates(2026))
        (tmp_path / "trade_dates_2026.json").write_text(
            json.dumps(valid), encoding="utf-8"
        )
        st = trade_calendar.get_calendar_status(2026)
        assert st["mode"] == "exact"
        assert st["safe_for_trading"] is True
        assert st["trading_days"] == len(valid)


# ============================================================
# 端到端: 原始 bug 断言
# ============================================================


class TestOriginalBugAssertions:
    """直接锁定历史 bug 的具体表现, 任何回归都会在此暴露."""

    @pytest.mark.parametrize(
        "day,label",
        [
            (date(2026, 1, 1), "元旦"),
            (date(2026, 2, 16), "春节"),
            (date(2026, 5, 1), "劳动节"),
            (date(2026, 10, 1), "国庆"),
            (date(2026, 10, 7), "国庆假期"),
        ],
    )
    def test_holidays_are_not_trading_days(self, day, label):
        """使用真实缓存: 法定节假日必须为非交易日."""
        if not trade_calendar._load_year_dates(2026, allow_fetch=True):
            pytest.skip("2026 年真实日历不可用 (无缓存且 akshare 不可达)")
        assert (
            trade_calendar.is_trading_day(day) is False
        ), f"{label} {day} 被误判为交易日 — P0 回归!"

    def test_trading_day_count_is_realistic(self):
        """2026 年交易日总数应在合理区间, 而非 261 天."""
        dates = trade_calendar._load_year_dates(2026, allow_fetch=True)
        if not dates:
            pytest.skip("2026 年真实日历不可用")
        assert (
            235 <= len(dates) <= 250
        ), f"2026 交易日数 {len(dates)} 异常 (261 = 仅排除周末的假日历)"
