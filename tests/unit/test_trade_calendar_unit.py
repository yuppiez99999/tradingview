"""test_trade_calendar_unit.py — A股交易日历单元测试

B1.2 验收测试: 验证 utils/trade_calendar.is_trading_day
    - 节假日识别 (国庆/春节/元旦/劳动节)
    - 多类型入参兼容 (str/date/datetime/None)
    - 回退模式 (无缓存时仅判周末)
    - 与 daily_trade_executor 调用方行为等价

P0 场景: 国庆 2026-10-01 (周四, 但属法定节假日, 应非交易日)
        春节 2026-02-16 (周一, 春节假期, 应非交易日)
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT))

from utils import trade_calendar  # noqa: E402
from utils.trade_calendar import is_trading_day, next_trading_day  # noqa: E402

# ============================================================
# 多类型入参兼容性 (B1.2 核心改动)
# ============================================================

class TestIsTradingDayMultiTypeArgs:
    """is_trading_day 应支持 str / date / datetime / None 入参"""

    def test_str_iso_format(self):
        """字符串 'YYYY-MM-DD' 格式"""
        assert is_trading_day("2026-07-13") is True  # 周一

    def test_str_compact_format(self):
        """字符串 'YYYYMMDD' 格式"""
        assert is_trading_day("20260713") is True

    def test_str_with_slashes(self):
        """字符串 'YYYY/MM/DD' 格式 (容错)"""
        assert is_trading_day("2026/07/13") is True

    def test_date_object(self):
        """datetime.date 对象 (daily_trade_executor 的原始入参类型)"""
        assert is_trading_day(date(2026, 7, 13)) is True
        assert is_trading_day(date(2026, 7, 11)) is False  # 周六

    def test_datetime_object(self):
        """datetime.datetime 对象"""
        assert is_trading_day(datetime(2026, 7, 13, 9, 30)) is True
        assert is_trading_day(datetime(2026, 7, 11, 15, 0)) is False

    def test_none_returns_today(self):
        """None 入参应返回今天"""
        # 不假设今天是否交易日, 仅验证不抛异常且返回 bool
        result = is_trading_day(None)
        assert isinstance(result, bool)


# ============================================================
# 节假日识别 (B1.2 核心修复点)
# ============================================================

class TestHolidayRecognition:
    """验证 is_trading_day 能正确识别 A 股法定节假日

    依赖 utils.trade_calendar._load_year_dates 的缓存或 akshare 拉取。
    若缓存不存在且 akshare 不可用, 会回退到周末判断 — 此情况下
    节假日测试将被标记为 skip (避免在无日历数据环境下误报)。
    """

    @pytest.fixture
    def ensure_2026_calendar(self):
        """确保 2026 年日历数据已加载, 否则 skip 节假日断言"""
        dates = trade_calendar._load_year_dates(2026, allow_fetch=False)
        if not dates:
            # 尝试一次拉取
            dates = trade_calendar._load_year_dates(2026, allow_fetch=True)
        if not dates:
            pytest.skip("2026 年交易日历不可用 (无缓存且 akshare 不可达), 跳过节假日断言")
        return dates

    def test_national_day_holiday(self, ensure_2026_calendar):
        """国庆节 2026-10-01 (周四) 应为非交易日"""
        # 国庆假期通常 10-01 ~ 10-07
        assert is_trading_day(date(2026, 10, 1)) is False, "国庆节 10-01 应为非交易日"

    def test_national_day_holiday_oct7(self, ensure_2026_calendar):
        """国庆假期尾声 2026-10-07 (周三) 应为非交易日"""
        assert is_trading_day(date(2026, 10, 7)) is False, "国庆假期 10-07 应为非交易日"

    def test_national_day_post_holiday(self, ensure_2026_calendar):
        """国庆假期后首个交易日 2026-10-08 (周四) 应为交易日"""
        assert is_trading_day(date(2026, 10, 8)) is True, "国庆后 10-08 应为交易日"

    def test_spring_festival_holiday(self, ensure_2026_calendar):
        """春节 2026-02-16 (周一, 除夕) 应为非交易日"""
        # 2026 春节假期: 2-15 (除夕) ~ 2-21 左右
        assert is_trading_day(date(2026, 2, 16)) is False, "春节期间 02-16 应为非交易日"

    def test_new_year_holiday(self, ensure_2026_calendar):
        """元旦 2026-01-01 (周四) 应为非交易日"""
        assert is_trading_day(date(2026, 1, 1)) is False, "元旦 01-01 应为非交易日"

    def test_labour_day_holiday(self, ensure_2026_calendar):
        """劳动节 2026-05-01 (周五) 应为非交易日"""
        assert is_trading_day(date(2026, 5, 1)) is False, "劳动节 05-01 应为非交易日"


# ============================================================
# 周末判断 (基础功能, 不依赖日历数据)
# ============================================================

class TestWeekendFallback:
    """即使无节假日数据, 周末也应判定为非交易日"""

    def test_saturday(self):
        """周六 2026-07-11 应为非交易日"""
        assert is_trading_day(date(2026, 7, 11)) is False

    def test_sunday(self):
        """周日 2026-07-12 应为非交易日"""
        assert is_trading_day(date(2026, 7, 12)) is False

    def test_monday(self):
        """周一 2026-07-13 (无节假日) 应为交易日"""
        # 7月13日无节假日, 周一应为交易日
        assert is_trading_day(date(2026, 7, 13)) is True

    def test_friday(self):
        """周五 2026-07-10 (无节假日) 应为交易日"""
        assert is_trading_day(date(2026, 7, 10)) is True


# ============================================================
# next_trading_day 基础验证
# ============================================================

class TestNextTradingDay:
    """next_trading_day 应跳过周末和节假日"""

    def test_friday_to_monday(self):
        """周五的下一交易日应为下周一"""
        # 2026-07-10 周五 → 2026-07-13 周一
        result = next_trading_day("2026-07-10")
        # 不依赖节假日数据时回退到周末判断, 仍应跳过周末
        assert result in ("2026-07-13", "2026-07-14")  # 容忍节假日调休

    def test_saturday_to_monday(self):
        """周六的下一交易日应为下周一"""
        result = next_trading_day("2026-07-11")
        assert result in ("2026-07-13", "2026-07-14")


# ============================================================
# 回退模式 (无缓存/无网络)
# ============================================================

class TestFallbackMode:
    """无日历缓存时, 回退到 '周一至周五即交易日' 模式"""

    def test_fallback_returns_weekday_only(self, tmp_path, monkeypatch):
        """模拟无缓存 + akshare 不可用, 应回退到周末判断"""
        # 重定向 CACHE_DIR 到临时空目录
        monkeypatch.setattr(trade_calendar, "CACHE_DIR", tmp_path)
        # mock akshare 拉取返回 None
        monkeypatch.setattr(trade_calendar, "_fetch_trade_dates_via_akshare", lambda y: None)

        # 周一应判定为交易日 (回退模式)
        assert is_trading_day(date(2026, 7, 13)) is True
        # 周六应判定为非交易日 (回退模式)
        assert is_trading_day(date(2026, 7, 11)) is False


# ============================================================
# daily_trade_executor 调用方等价性 (B1.2 验收核心)
# ============================================================

class TestDailyTradeExecutorIntegration:
    """验证 daily_trade_executor.py 删除本地 is_trading_day 后,
    使用 utils.trade_calendar.is_trading_day 行为等价"""

    def test_root_daily_trade_executor_imports(self):
        """根目录 daily_trade_executor 应使用 utils.trade_calendar.is_trading_day"""
        import daily_trade_executor as dte
        # 验证 is_trading_day 是从 utils.trade_calendar 导入的
        assert dte.is_trading_day is trade_calendar.is_trading_day

    def test_ms_strategy_daily_trade_executor_imports(self):
        """ms_strategy 副本也应使用 utils.trade_calendar.is_trading_day"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ms_dte",
            PROJECT_ROOT / "ms_strategy" / "scripts" / "daily_trade_executor.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.is_trading_day is trade_calendar.is_trading_day

    def test_weekend_skipped_in_accumulation_loop(self):
        """建仓期交易日计数循环应跳过周末 (原 daily_trade_executor:204 行为)"""
        from datetime import timedelta
        # 模拟一周的累计交易日计数 (与 daily_trade_executor 逻辑等价)
        start = date(2026, 7, 13)  # 周一
        end = date(2026, 7, 19)    # 周日
        current = start
        trading_days = 0
        while current <= end:
            if is_trading_day(current):
                trading_days += 1
            current += timedelta(days=1)
        # 周一到周日: 周一~周五 5 个交易日
        assert trading_days == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
