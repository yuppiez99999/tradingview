"""test_price_limit_calculator_unit.py — A股涨跌停价+停牌标记计算器单元测试

覆盖要点:
    - normalize_code 代码归一化
    - get_board_type 板块识别 (主板/创业板/科创板/北交所/ETF/可转债)
    - get_limit_pct 涨跌停比例 (含 ST ±5%)
    - calc_limit_prices 涨跌停价 (四舍五入到分 + 边界)
    - is_at_limit_up / is_at_limit_down
    - detect_suspended_from_row 停牌检测
    - enrich_day_data_list 富化 (首日/多日/ST/停牌)
    - build_backtest_data_from_ohlcv 从 DataFrame 构建
    - fetch_st_codes akshare 失败容错
"""
from __future__ import annotations

import pandas as pd
import pytest

from utils.price_limit_calculator import (
    BoardType,
    _is_positive_float,
    _round_to_cents,
    build_backtest_data_from_ohlcv,
    calc_limit_prices,
    detect_suspended_from_row,
    enrich_day_data_list,
    fetch_st_codes,
    get_board_type,
    get_limit_pct,
    is_at_limit_down,
    is_at_limit_up,
    normalize_code,
)

# ============================================================
# normalize_code
# ============================================================


class TestNormalizeCode:
    @pytest.mark.unit
    def test_with_sz_suffix(self):
        assert normalize_code("300750.SZ") == "300750"

    @pytest.mark.unit
    def test_with_sh_suffix(self):
        assert normalize_code("600519.SH") == "600519"

    @pytest.mark.unit
    def test_with_sh_prefix(self):
        assert normalize_code("sh600519") == "600519"

    @pytest.mark.unit
    def test_plain_digits(self):
        assert normalize_code("688981") == "688981"

    @pytest.mark.unit
    def test_none(self):
        assert normalize_code(None) == ""

    @pytest.mark.unit
    def test_empty(self):
        assert normalize_code("") == ""

    @pytest.mark.unit
    def test_string_none(self):
        assert normalize_code("none") == ""

    @pytest.mark.unit
    def test_with_bj_suffix(self):
        assert normalize_code("430047.BJ") == "430047"


# ============================================================
# get_board_type
# ============================================================


class TestGetBoardType:
    @pytest.mark.unit
    def test_main_sh(self):
        assert get_board_type("600519.SH") == BoardType.MAIN_SH

    @pytest.mark.unit
    def test_main_sz(self):
        assert get_board_type("000001.SZ") == BoardType.MAIN_SZ

    @pytest.mark.unit
    def test_gem_300(self):
        assert get_board_type("300750.SZ") == BoardType.GEM

    @pytest.mark.unit
    def test_gem_301(self):
        assert get_board_type("301088") == BoardType.GEM

    @pytest.mark.unit
    def test_star_688(self):
        assert get_board_type("688981.SH") == BoardType.STAR

    @pytest.mark.unit
    def test_star_689(self):
        assert get_board_type("689009") == BoardType.STAR

    @pytest.mark.unit
    def test_bse_8(self):
        assert get_board_type("830799") == BoardType.BSE

    @pytest.mark.unit
    def test_bse_4(self):
        assert get_board_type("430047") == BoardType.BSE

    @pytest.mark.unit
    def test_etf_51(self):
        assert get_board_type("510300.SH") == BoardType.ETF

    @pytest.mark.unit
    def test_etf_58(self):
        assert get_board_type("588080") == BoardType.ETF

    @pytest.mark.unit
    def test_etf_15(self):
        assert get_board_type("159915") == BoardType.ETF

    @pytest.mark.unit
    def test_bond_11(self):
        assert get_board_type("113001") == BoardType.BOND

    @pytest.mark.unit
    def test_bond_12(self):
        assert get_board_type("123001") == BoardType.BOND

    @pytest.mark.unit
    def test_unknown_code(self):
        assert get_board_type("ABC") == BoardType.UNKNOWN

    @pytest.mark.unit
    def test_empty(self):
        assert get_board_type("") == BoardType.UNKNOWN


# ============================================================
# get_limit_pct
# ============================================================


class TestGetLimitPct:
    @pytest.mark.unit
    def test_main_board(self):
        assert get_limit_pct("600519.SH") == 0.10

    @pytest.mark.unit
    def test_gem(self):
        assert get_limit_pct("300750.SZ") == 0.20

    @pytest.mark.unit
    def test_star(self):
        assert get_limit_pct("688981") == 0.20

    @pytest.mark.unit
    def test_bse(self):
        assert get_limit_pct("830799") == 0.30

    @pytest.mark.unit
    def test_etf(self):
        assert get_limit_pct("510300") == 0.10

    @pytest.mark.unit
    def test_bond_no_limit(self):
        assert get_limit_pct("113001") == 0.0

    @pytest.mark.unit
    def test_st_always_5pct(self):
        assert get_limit_pct("600519.SH", is_st=True) == 0.05
        assert get_limit_pct("300750.SZ", is_st=True) == 0.05
        assert get_limit_pct("688981", is_st=True) == 0.05

    @pytest.mark.unit
    def test_unknown_defaults_10pct(self):
        assert get_limit_pct("ABC") == 0.10


# ============================================================
# calc_limit_prices
# ============================================================


class TestCalcLimitPrices:
    @pytest.mark.unit
    def test_main_board_10pct(self):
        lu, ld = calc_limit_prices(10.00, "600519.SH")
        assert lu == 11.00
        assert ld == 9.00

    @pytest.mark.unit
    def test_gem_20pct(self):
        lu, ld = calc_limit_prices(10.00, "300750.SZ")
        assert lu == 12.00
        assert ld == 8.00

    @pytest.mark.unit
    def test_star_20pct(self):
        lu, ld = calc_limit_prices(50.00, "688981")
        assert lu == 60.00
        assert ld == 40.00

    @pytest.mark.unit
    def test_bse_30pct(self):
        lu, ld = calc_limit_prices(10.00, "830799")
        assert lu == 13.00
        assert ld == 7.00

    @pytest.mark.unit
    def test_st_5pct(self):
        lu, ld = calc_limit_prices(10.00, "600519.SH", is_st=True)
        assert lu == 10.50
        assert ld == 9.50

    @pytest.mark.unit
    def test_bond_no_limit(self):
        lu, ld = calc_limit_prices(100.00, "113001")
        assert lu == 0.0
        assert ld == 0.0

    @pytest.mark.unit
    def test_zero_prev_close(self):
        lu, ld = calc_limit_prices(0.0, "600519.SH")
        assert lu == 0.0
        assert ld == 0.0

    @pytest.mark.unit
    def test_negative_prev_close(self):
        lu, ld = calc_limit_prices(-1.0, "600519.SH")
        assert lu == 0.0
        assert ld == 0.0

    @pytest.mark.unit
    def test_none_prev_close(self):
        lu, ld = calc_limit_prices(None, "600519.SH")  # type: ignore[arg-type]
        assert lu == 0.0
        assert ld == 0.0

    @pytest.mark.unit
    def test_rounding_half_up(self):
        """四舍五入到分 (ROUND_HALF_UP, 非银行家舍入)"""
        # 10.005 × 1.1 = 11.0055 → 11.01 (ROUND_HALF_UP)
        lu, ld = calc_limit_prices(10.005, "600519.SH")
        assert lu == 11.01  # 11.0055 → 11.01


# ============================================================
# is_at_limit_up / is_at_limit_down
# ============================================================


class TestLimitCheck:
    @pytest.mark.unit
    def test_at_limit_up(self):
        assert is_at_limit_up(11.00, 11.00) is True

    @pytest.mark.unit
    def test_above_limit_up(self):
        assert is_at_limit_up(11.01, 11.00) is True

    @pytest.mark.unit
    def test_below_limit_up(self):
        assert is_at_limit_up(10.99, 11.00) is False

    @pytest.mark.unit
    def test_limit_up_with_tolerance(self):
        """容差 1e-3: 10.9995 >= 11.00 - 0.001"""
        assert is_at_limit_up(10.9995, 11.00) is True

    @pytest.mark.unit
    def test_limit_up_zero_returns_false(self):
        assert is_at_limit_up(10.0, 0.0) is False

    @pytest.mark.unit
    def test_at_limit_down(self):
        assert is_at_limit_down(9.00, 9.00) is True

    @pytest.mark.unit
    def test_below_limit_down(self):
        assert is_at_limit_down(8.99, 9.00) is True

    @pytest.mark.unit
    def test_above_limit_down(self):
        assert is_at_limit_down(9.01, 9.00) is False

    @pytest.mark.unit
    def test_limit_down_zero_returns_false(self):
        assert is_at_limit_down(10.0, 0.0) is False


# ============================================================
# detect_suspended_from_row
# ============================================================


class TestDetectSuspended:
    @pytest.mark.unit
    def test_normal_trading(self):
        assert detect_suspended_from_row(close=10.0, volume=1000, open_price=10.0) is False

    @pytest.mark.unit
    def test_zero_close(self):
        assert detect_suspended_from_row(close=0.0, volume=0, open_price=0) is True

    @pytest.mark.unit
    def test_negative_close(self):
        assert detect_suspended_from_row(close=-1.0) is True

    @pytest.mark.unit
    def test_none_close(self):
        assert detect_suspended_from_row(close=None) is True  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_zero_volume_zero_open(self):
        """volume=0 + open=0 → 停牌"""
        assert detect_suspended_from_row(close=10.0, volume=0, open_price=0) is True

    @pytest.mark.unit
    def test_zero_volume_with_open(self):
        """一字涨停: volume=0 但 open>0 → 不停牌"""
        assert detect_suspended_from_row(close=10.0, volume=0, open_price=10.0) is False

    @pytest.mark.unit
    def test_none_volume_none_open(self):
        assert detect_suspended_from_row(close=10.0, volume=None, open_price=None) is True  # type: ignore[arg-type]


# ============================================================
# enrich_day_data_list
# ============================================================


class TestEnrichDayDataList:
    @pytest.mark.unit
    def test_empty_list(self):
        assert enrich_day_data_list([]) == []

    @pytest.mark.unit
    def test_first_day_no_limit(self):
        """首日无前收盘价 → limit 字段为空"""
        data = [{"date": "2024-01-01", "prices": {"600519": 10.0}}]
        result = enrich_day_data_list(data)
        assert result[0]["limit_up_prices"] == {}
        assert result[0]["limit_down_prices"] == {}
        assert result[0]["suspended"] == {"600519": False}

    @pytest.mark.unit
    def test_second_day_with_limit(self):
        data = [
            {"date": "2024-01-01", "prices": {"600519": 10.0}},
            {"date": "2024-01-02", "prices": {"600519": 11.0}},
        ]
        result = enrich_day_data_list(data)
        assert result[1]["limit_up_prices"]["600519"] == 11.00
        assert result[1]["limit_down_prices"]["600519"] == 9.00

    @pytest.mark.unit
    def test_st_codes(self):
        data = [
            {"date": "2024-01-01", "prices": {"600519": 10.0}},
            {"date": "2024-01-02", "prices": {"600519": 10.0}},
        ]
        result = enrich_day_data_list(data, st_codes={"600519"})
        assert result[1]["limit_up_prices"]["600519"] == 10.50
        assert result[1]["limit_down_prices"]["600519"] == 9.50

    @pytest.mark.unit
    def test_suspended_zero_price(self):
        data = [{"date": "2024-01-01", "prices": {"600519": 0.0}}]
        result = enrich_day_data_list(data)
        assert result[0]["suspended"]["600519"] is True

    @pytest.mark.unit
    def test_gem_20pct(self):
        data = [
            {"date": "2024-01-01", "prices": {"300750": 10.0}},
            {"date": "2024-01-02", "prices": {"300750": 12.0}},
        ]
        result = enrich_day_data_list(data)
        assert result[1]["limit_up_prices"]["300750"] == 12.00
        assert result[1]["limit_down_prices"]["300750"] == 8.00

    @pytest.mark.unit
    def test_bond_no_limit(self):
        """可转债 → limit 字段为空 (pct=0)"""
        data = [
            {"date": "2024-01-01", "prices": {"113001": 100.0}},
            {"date": "2024-01-02", "prices": {"113001": 110.0}},
        ]
        result = enrich_day_data_list(data)
        assert result[1]["limit_up_prices"] == {}
        assert result[1]["limit_down_prices"] == {}

    @pytest.mark.unit
    def test_preserve_existing_limit_fields(self):
        """setdefault 不覆盖已有值"""
        data = [
            {"date": "2024-01-01", "prices": {"600519": 10.0}},
            {
                "date": "2024-01-02",
                "prices": {"600519": 11.0},
                "limit_up_prices": {"600519": 99.99},  # 预设
            },
        ]
        result = enrich_day_data_list(data)
        assert result[1]["limit_up_prices"]["600519"] == 99.99  # 不覆盖


# ============================================================
# build_backtest_data_from_ohlcv
# ============================================================


class TestBuildBacktestData:
    @pytest.mark.unit
    def test_empty_input(self):
        assert build_backtest_data_from_ohlcv({}) == []

    @pytest.mark.unit
    def test_single_symbol_two_days(self):
        df = pd.DataFrame(
            {
                "open": [10.0, 11.0],
                "close": [10.0, 11.0],
                "high": [10.5, 11.5],
                "low": [9.8, 10.5],
                "volume": [1000, 2000],
            },
            index=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")],
        )
        data = build_backtest_data_from_ohlcv({"600519": df})
        assert len(data) == 2
        assert data[0]["date"] == "2024-01-01"
        assert data[1]["limit_up_prices"]["600519"] == 11.00
        assert data[1]["limit_down_prices"]["600519"] == 9.00

    @pytest.mark.unit
    def test_suspended_via_zero_volume(self):
        df = pd.DataFrame(
            {
                "close": [10.0, 10.0],
                "open": [10.0, 0.0],
                "volume": [1000, 0],
            },
            index=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")],
        )
        data = build_backtest_data_from_ohlcv({"600519": df})
        assert data[1]["suspended"]["600519"] is True

    @pytest.mark.unit
    def test_missing_date_marks_suspended(self):
        """标的在某日无数据 → 标记停牌, 用前一日 close 估值"""
        df1 = pd.DataFrame(
            {"close": [10.0, 10.0], "open": [10.0, 10.0], "volume": [1000, 1000]},
            index=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")],
        )
        df2 = pd.DataFrame(
            {"close": [20.0], "open": [20.0], "volume": [500]},
            index=[pd.Timestamp("2024-01-01")],  # 1-02 缺失
        )
        data = build_backtest_data_from_ohlcv({"600519": df1, "300750": df2})
        assert data[1]["suspended"]["300750"] is True
        assert data[1]["prices"]["300750"] == 20.0  # 冻结估值


# ============================================================
# _round_to_cents / _is_positive_float
# ============================================================


class TestHelpers:
    @pytest.mark.unit
    def test_round_normal(self):
        assert _round_to_cents(10.005) == 10.01  # ROUND_HALF_UP

    @pytest.mark.unit
    def test_round_down(self):
        assert _round_to_cents(10.004) == 10.00

    @pytest.mark.unit
    def test_nan_returns_zero(self):
        assert _round_to_cents(float("nan")) == 0.0

    @pytest.mark.unit
    def test_inf_returns_zero(self):
        assert _round_to_cents(float("inf")) == 0.0

    @pytest.mark.unit
    def test_is_positive_float_true(self):
        assert _is_positive_float(1.0) is True

    @pytest.mark.unit
    def test_is_positive_float_zero(self):
        assert _is_positive_float(0.0) is False

    @pytest.mark.unit
    def test_is_positive_float_negative(self):
        assert _is_positive_float(-1.0) is False

    @pytest.mark.unit
    def test_is_positive_float_none(self):
        assert _is_positive_float(None) is False

    @pytest.mark.unit
    def test_is_positive_float_string(self):
        assert _is_positive_float("abc") is False


# ============================================================
# fetch_st_codes (akshare 失败容错)
# ============================================================


class TestFetchStCodes:
    @pytest.mark.unit
    def test_akshare_unavailable(self, monkeypatch):
        """akshare 未安装 → 返回空集合"""
        # 确保 akshare import 失败
        monkeypatch.setitem(__import__("sys").modules, "akshare", None)
        result = fetch_st_codes()
        assert result == set()
