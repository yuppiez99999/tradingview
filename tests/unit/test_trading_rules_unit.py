"""trading_rules 单元测试.

被测模块: utils/trading_rules.py
覆盖目标: >=90%

测试 T+0/T+1 交易制度判定、可卖判断、下一交易日、完整交易规则获取。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.trading_rules import (  # noqa: E402
    T0_EXACT_CODES,
    T0_ETF_SH_PREFIXES,
    T0_ETF_SZ_PREFIXES,
    _next_trade_day,
    can_sell_today,
    get_trading_rule,
    is_t0_eligible,
)


class TradingRulesTest:
    """trading_rules 单元测试."""

    # ------ is_t0_eligible: 期货/期权 ------
    def test_is_t0_future_always_true(self):
        assert is_t0_eligible("IF2406", "FUTURE") is True

    def test_is_t0_option_always_true(self):
        assert is_t0_eligible("10000006", "OPTION") is True

    def test_is_t0_future_with_t1_code_still_true(self):
        # 即使代码是普通股票代码, FUTURE 仍 T+0
        assert is_t0_eligible("600519", "FUTURE") is True

    # ------ is_t0_eligible: 精确匹配 ------
    def test_is_t0_exact_code_bond_etf(self):
        assert is_t0_eligible("511010", "ETF") is True

    def test_is_t0_exact_code_currency_etf(self):
        assert is_t0_eligible("511880", "ETF") is True

    def test_is_t0_exact_code_cross_border(self):
        assert is_t0_eligible("513100", "ETF") is True
        assert is_t0_eligible("159920", "ETF") is True

    def test_is_t0_exact_code_gold_etf(self):
        assert is_t0_eligible("518880", "ETF") is True
        assert is_t0_eligible("159934", "ETF") is True

    def test_is_t0_exact_code_lof(self):
        assert is_t0_eligible("160505", "ETF") is True
        assert is_t0_eligible("160706", "ETF") is True

    # ------ is_t0_eligible: 前缀匹配 SH ------
    def test_is_t0_prefix_511_bond(self):
        # 511 前缀但不在精确表
        assert is_t0_eligible("511888", "ETF") is True

    def test_is_t0_prefix_513_cross_border(self):
        assert is_t0_eligible("513600", "ETF") is True

    def test_is_t0_prefix_518_commodity(self):
        assert is_t0_eligible("518800", "ETF") is True

    def test_is_t0_prefix_510_not_t0(self):
        # 510 是普通 T+1 ETF, 不在 T0 前缀
        assert is_t0_eligible("510300", "ETF") is False

    # ------ is_t0_eligible: 前缀匹配 SZ ------
    def test_is_t0_prefix_159_cross_border(self):
        assert is_t0_eligible("159940", "ETF") is True

    def test_is_t0_prefix_16_lof(self):
        # 16 前缀 (LOF)
        assert is_t0_eligible("161725", "ETF") is True

    # ------ is_t0_eligible: 默认 T+1 ------
    def test_is_t0_main_board_stock_false(self):
        assert is_t0_eligible("600519", "STOCK") is False

    def test_is_t0_sz_main_board_false(self):
        assert is_t0_eligible("000001", "STOCK") is False

    def test_is_t0_chinext_false(self):
        assert is_t0_eligible("300750", "STOCK") is False

    def test_is_t0_star_market_false(self):
        assert is_t0_eligible("688981", "STOCK") is False

    def test_is_t0_bse_false(self):
        assert is_t0_eligible("830799", "STOCK") is False

    # ------ is_t0_eligible: 代码清洗 ------
    def test_is_t0_with_sh_suffix(self):
        assert is_t0_eligible("511010.SH", "ETF") is True

    def test_is_t0_with_sz_suffix(self):
        assert is_t0_eligible("159920.SZ", "ETF") is True

    def test_is_t0_short_code_zfilled(self):
        # 短代码 zfill(6) 后再判断
        assert is_t0_eligible("510300", "ETF") is False

    def test_is_t0_non_string_code(self):
        # 传入 int 也能工作 (str(code))
        assert is_t0_eligible(511010, "ETF") is True

    # ------ 模块常量完整性 ------
    def test_t0_exact_codes_nonempty(self):
        assert len(T0_EXACT_CODES) > 0
        assert all(isinstance(k, str) for k in T0_EXACT_CODES)

    def test_t0_sh_prefixes(self):
        assert set(T0_ETF_SH_PREFIXES.keys()) == {"511", "513", "518"}

    def test_t0_sz_prefixes(self):
        assert set(T0_ETF_SZ_PREFIXES.keys()) == {"159", "16"}

    # ------ can_sell_today: T+0 标的 ------
    def test_can_sell_today_t0_eligible(self):
        today = date.today()
        can, reason = can_sell_today("511010", today, "ETF")
        assert can is True
        assert reason == ""

    def test_can_sell_today_future(self):
        today = date.today()
        can, reason = can_sell_today("IF2406", today, "FUTURE")
        assert can is True
        assert reason == ""

    # ------ can_sell_today: T+1 标的 ------
    def test_can_sell_today_past_buy_date(self):
        # 昨日买入, 今日可卖
        past = date.today() - timedelta(days=1)
        can, reason = can_sell_today("600519", past, "STOCK")
        assert can is True
        assert reason == ""

    def test_can_sell_today_same_day_blocked(self):
        today = date.today()
        can, reason = can_sell_today("600519", today, "STOCK")
        assert can is False
        assert "T+1限制" in reason
        assert "600519" in reason

    def test_can_sell_today_future_buy_date_anomaly(self):
        future = date.today() + timedelta(days=1)
        can, reason = can_sell_today("600519", future, "STOCK")
        assert can is False
        assert "日期异常" in reason

    def test_can_sell_today_t1_etf_same_day(self):
        today = date.today()
        # 510300 是 T+1 ETF
        can, reason = can_sell_today("510300", today, "ETF")
        assert can is False
        assert "T+1限制" in reason

    # ------ _next_trade_day ------
    def test_next_trade_day_weekday(self):
        # 2024-05-15 是周三 → 周四
        d = date(2024, 5, 15)
        nxt = _next_trade_day(d)
        assert nxt == date(2024, 5, 16)

    def test_next_trade_day_friday(self):
        # 2024-05-17 是周五 → 下周一 5-20
        d = date(2024, 5, 17)
        nxt = _next_trade_day(d)
        assert nxt == date(2024, 5, 20)

    def test_next_trade_day_saturday(self):
        d = date(2024, 5, 18)
        nxt = _next_trade_day(d)
        assert nxt == date(2024, 5, 20)

    def test_next_trade_day_sunday(self):
        d = date(2024, 5, 19)
        nxt = _next_trade_day(d)
        assert nxt == date(2024, 5, 20)

    # ------ get_trading_rule: FUTURE ------
    def test_get_trading_rule_future(self):
        r = get_trading_rule("IF2406", "FUTURE")
        assert r["settlement"] == "T+0"
        assert r["can_short"] is True
        assert r["min_unit"] == 1
        assert r["price_limit_pct"] == 0.10
        assert r["margin_required"] is True

    # ------ get_trading_rule: OPTION ------
    def test_get_trading_rule_option(self):
        r = get_trading_rule("10000006", "OPTION")
        assert r["settlement"] == "T+0"
        assert r["can_short"] is False
        assert r["min_unit"] == 1
        assert r["price_limit_pct"] == 0.0
        assert r["margin_required"] is True

    # ------ get_trading_rule: 科创板 ------
    def test_get_trading_rule_star_market(self):
        r = get_trading_rule("688981", "STOCK")
        assert r["settlement"] == "T+1"
        assert r["can_short"] is False
        assert r["min_unit"] == 100
        assert r["price_limit_pct"] == 0.20
        assert r["margin_required"] is False

    # ------ get_trading_rule: 北交所 ------
    def test_get_trading_rule_bse(self):
        r = get_trading_rule("830799", "STOCK")
        assert r["settlement"] == "T+1"
        assert r["price_limit_pct"] == 0.30
        assert r["margin_required"] is False
        assert r["min_unit"] == 100

    # ------ get_trading_rule: 创业板 ------
    def test_get_trading_rule_chinext(self):
        r = get_trading_rule("300750", "STOCK")
        assert r["settlement"] == "T+1"
        assert r["price_limit_pct"] == 0.20
        assert r["margin_required"] is False
        assert r["min_unit"] == 100

    # ------ get_trading_rule: 主板 ------
    def test_get_trading_rule_main_board_sh(self):
        r = get_trading_rule("600519", "STOCK")
        assert r["settlement"] == "T+1"
        assert r["price_limit_pct"] == 0.10
        assert r["margin_required"] is False
        assert r["min_unit"] == 100

    def test_get_trading_rule_main_board_sz(self):
        r = get_trading_rule("000001", "STOCK")
        assert r["settlement"] == "T+1"
        assert r["price_limit_pct"] == 0.10
        assert r["margin_required"] is False

    # ------ get_trading_rule: T+0 ETF ------
    def test_get_trading_rule_t0_etf(self):
        r = get_trading_rule("511010", "ETF")
        assert r["settlement"] == "T+0"
        assert r["can_short"] is False
        assert r["min_unit"] == 100
        # 511010 走 else 分支 (主板 price_limit)
        assert r["price_limit_pct"] == 0.10
        assert r["margin_required"] is False

    def test_get_trading_rule_t1_etf(self):
        r = get_trading_rule("510300", "ETF")
        assert r["settlement"] == "T+1"
        assert r["min_unit"] == 100

    # ------ get_trading_rule: 代码清洗 ------
    def test_get_trading_rule_with_suffix(self):
        r1 = get_trading_rule("688981.SH", "STOCK")
        r2 = get_trading_rule("688981", "STOCK")
        assert r1 == r2

    def test_get_trading_rule_default_product_class(self):
        # 默认 product_class="STOCK"
        r = get_trading_rule("600519")
        assert r["settlement"] == "T+1"
        assert r["can_short"] is False