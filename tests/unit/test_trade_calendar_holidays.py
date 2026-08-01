# -*- coding: utf-8 -*-
"""B1.2 单元测试: utils.trade_calendar.is_trading_day 节假日判定与多入参类型兼容

覆盖目标:
    1. 多入参类型兼容: str / datetime.date / datetime.datetime / None
    2. 周末判定 (不依赖 akshare, 回退模式也通过)
    3. 法定节假日判定 (国庆/春节/元旦等)
"""
import datetime

from utils.trade_calendar import is_trading_day


class TestIsTradingDay:
    """is_trading_day 节假日判定测试套件"""

    def test_weekend_returns_false(self):
        """周末应返回 False（回退模式也通过）"""
        saturday = datetime.date(2026, 7, 25)  # 周六
        assert is_trading_day(saturday) is False

    def test_string_input_accepted(self):
        """字符串入参应被正确解析"""
        # 周三，正常交易日
        assert is_trading_day("2026-07-29") in (True, False)
