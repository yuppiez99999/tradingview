"""W6.3.3 Step 0: utils.contracts.symbols 单元测试。

覆盖:
    - 股票 SH/SZ/BSE 裸码 + Wind 码
    - ETF / 可转债
    - 期货: 股指 (CFFEX) / 商品 (SHFE/INE/DCE/CZCE) / 旧写法规范化 (SHF→SHFE, ZCE→CZCE)
    - 东财 secid 输入
    - to_eastmoney_secid / to_wind_code 便捷函数
    - normalize_exchange
    - strict 模式抛错
    - NewType 运行时向后兼容 (isinstance str)
    - SymbolInfo 不可变性 (frozen=True)
"""
from __future__ import annotations

import pytest

from utils.contracts.symbols import (
    EXCHANGES,
    FUTURES_CODE_PATTERN,
    AShareCode6,
    EastMoneySecId,
    SymbolParseError,
    WindCode,
    normalize_exchange,
    parse_symbol,
    to_eastmoney_secid,
    to_wind_code,
)

# ============================================================
# 1. 股票: SH 主板 / 科创板 / B 股
# ============================================================


class TestStockSH:
    def test_wind_code_600519(self) -> None:
        info = parse_symbol("600519.SH")
        assert info.code6 == "600519"
        assert info.exchange == "SSE"
        assert info.asset_type == "STOCK"
        assert info.wind_code == "600519.SH"
        assert info.eastmoney_secid == "1.600519"
        assert info.product == "600519"
        assert info.futures_year is None
        assert info.futures_month is None

    def test_bare_code_600519(self) -> None:
        info = parse_symbol("600519")
        assert info.exchange == "SSE"
        assert info.asset_type == "STOCK"
        assert info.wind_code == "600519.SH"
        assert info.eastmoney_secid == "1.600519"

    def test_star_688981(self) -> None:
        info = parse_symbol("688981.SH")
        assert info.exchange == "SSE"
        assert info.asset_type == "STOCK"
        assert info.eastmoney_secid == "1.688981"

    def test_b_stock_900957(self) -> None:
        info = parse_symbol("900957.SH")
        assert info.exchange == "SSE"
        assert info.asset_type == "B_STOCK"
        assert info.eastmoney_secid == "1.900957"


# ============================================================
# 2. 股票: SZ 主板 / 创业板
# ============================================================


class TestStockSZ:
    def test_wind_code_000001(self) -> None:
        info = parse_symbol("000001.SZ")
        assert info.code6 == "000001"
        assert info.exchange == "SZSE"
        assert info.asset_type == "STOCK"
        assert info.wind_code == "000001.SZ"
        assert info.eastmoney_secid == "0.000001"

    def test_bare_code_000001(self) -> None:
        info = parse_symbol("000001")
        assert info.exchange == "SZSE"
        assert info.asset_type == "STOCK"
        assert info.eastmoney_secid == "0.000001"

    def test_chinext_300750(self) -> None:
        info = parse_symbol("300750.SZ")
        assert info.exchange == "SZSE"
        assert info.asset_type == "STOCK"
        assert info.eastmoney_secid == "0.300750"


# ============================================================
# 3. 北交所
# ============================================================


class TestBSE:
    def test_830799_wind(self) -> None:
        info = parse_symbol("830799.BJ")
        assert info.exchange == "BSE"
        assert info.asset_type == "BSE_STOCK"
        assert info.wind_code == "830799.BJ"

    def test_830799_bare(self) -> None:
        info = parse_symbol("830799")
        assert info.exchange == "BSE"
        assert info.asset_type == "BSE_STOCK"

    def test_430047_bare(self) -> None:
        info = parse_symbol("430047")
        assert info.exchange == "BSE"
        assert info.asset_type == "BSE_STOCK"


# ============================================================
# 4. ETF
# ============================================================


class TestETF:
    def test_sh_etf_510300(self) -> None:
        info = parse_symbol("510300.SH")
        assert info.exchange == "SSE"
        assert info.asset_type == "ETF"
        assert info.eastmoney_secid == "1.510300"

    def test_sz_etf_159915(self) -> None:
        info = parse_symbol("159915.SZ")
        assert info.exchange == "SZSE"
        assert info.asset_type == "ETF"
        assert info.eastmoney_secid == "0.159915"

    def test_bare_etf_510300(self) -> None:
        info = parse_symbol("510300")
        assert info.exchange == "SSE"
        assert info.asset_type == "ETF"


# ============================================================
# 5. 可转债
# ============================================================


class TestConvertibleBond:
    def test_sh_cb_113050(self) -> None:
        info = parse_symbol("113050.SH")
        assert info.exchange == "SSE"
        assert info.asset_type == "CONVERTIBLE_BOND"
        assert info.eastmoney_secid == "1.113050"

    def test_sz_cb_127015(self) -> None:
        info = parse_symbol("127015.SZ")
        assert info.exchange == "SZSE"
        assert info.asset_type == "CONVERTIBLE_BOND"
        assert info.eastmoney_secid == "0.127015"


# ============================================================
# 6. 期货: 股指 / 商品 / 旧写法规范化
# ============================================================


class TestFutures:
    def test_index_future_if(self) -> None:
        info = parse_symbol("IF2507.CFFEX")
        assert info.code6 == "IF2507"
        assert info.exchange == "CFFEX"
        assert info.asset_type == "INDEX_FUTURE"
        assert info.product == "IF"
        assert info.futures_year == 2025
        assert info.futures_month == 7
        assert info.wind_code == "IF2507.CFFEX"

    def test_index_future_ic(self) -> None:
        info = parse_symbol("IC2506.CFFEX")
        assert info.asset_type == "INDEX_FUTURE"
        assert info.product == "IC"
        assert info.futures_month == 6

    def test_commodity_cu_shfe(self) -> None:
        info = parse_symbol("CU2508.SHFE")
        assert info.exchange == "SHFE"
        assert info.asset_type == "COMMODITY_FUTURE"
        assert info.product == "CU"
        assert info.futures_year == 2025
        assert info.futures_month == 8

    def test_commodity_sc_ine(self) -> None:
        """W6.3.3 难点 §2.1: INE 原油 — 原系统遗漏, 新正则必须支持。"""
        info = parse_symbol("SC2509.INE")
        assert info.exchange == "INE"
        assert info.asset_type == "COMMODITY_FUTURE"
        assert info.product == "SC"

    def test_commodity_m_dce(self) -> None:
        info = parse_symbol("M2509.DCE")
        assert info.exchange == "DCE"
        assert info.asset_type == "COMMODITY_FUTURE"
        assert info.product == "M"

    def test_commodity_cf_czce(self) -> None:
        info = parse_symbol("CF2509.CZCE")
        assert info.exchange == "CZCE"
        assert info.asset_type == "COMMODITY_FUTURE"
        assert info.product == "CF"

    # --- 旧写法规范化 (SHF→SHFE, ZCE→CZCE) ---

    def test_old_suffix_shf_normalized(self) -> None:
        """难点 §2.1: 旧正则只认 SHF, 新版接受并规范化为 SHFE。"""
        info = parse_symbol("CU2508.SHF")
        assert info.exchange == "SHFE"
        assert info.wind_code == "CU2508.SHFE"

    def test_old_suffix_zce_normalized(self) -> None:
        info = parse_symbol("CF2509.ZCE")
        assert info.exchange == "CZCE"
        assert info.wind_code == "CF2509.CZCE"

    def test_hint_future_not_matched_warns(self) -> None:
        """hint_asset='future' 但代码不匹配期货正则 → 非 strict 时 warning。"""
        info = parse_symbol("600519.SH", hint_asset="future")
        assert len(info.warnings) > 0
        assert "future" in info.warnings[0].lower()


# ============================================================
# 7. 东财 secid 输入
# ============================================================


class TestEastMoneySecIdInput:
    def test_secid_sh(self) -> None:
        info = parse_symbol("1.600519")
        assert info.code6 == "600519"
        assert info.exchange == "SSE"
        assert info.asset_type == "STOCK"
        assert info.eastmoney_secid == "1.600519"

    def test_secid_sz(self) -> None:
        info = parse_symbol("0.300750")
        assert info.code6 == "300750"
        assert info.exchange == "SZSE"
        assert info.eastmoney_secid == "0.300750"

    def test_secid_etf(self) -> None:
        info = parse_symbol("1.510300")
        assert info.exchange == "SSE"
        assert info.asset_type == "ETF"


# ============================================================
# 8. 便捷函数
# ============================================================


class TestConvenienceFunctions:
    def test_to_eastmoney_secid_wind_code(self) -> None:
        assert to_eastmoney_secid("600519.SH") == "1.600519"
        assert to_eastmoney_secid("300750.SZ") == "0.300750"
        assert to_eastmoney_secid("510300.SH") == "1.510300"

    def test_to_eastmoney_secid_bare_code(self) -> None:
        assert to_eastmoney_secid("600519") == "1.600519"
        assert to_eastmoney_secid("000001") == "0.000001"

    def test_to_eastmoney_secid_secid_input(self) -> None:
        assert to_eastmoney_secid("1.600519") == "1.600519"

    def test_to_wind_code_bare(self) -> None:
        assert to_wind_code("600519") == "600519.SH"
        assert to_wind_code("000001") == "000001.SZ"
        assert to_wind_code("510300") == "510300.SH"

    def test_to_wind_code_normalizes_old_suffix(self) -> None:
        assert to_wind_code("CU2508.SHF") == "CU2508.SHFE"
        assert to_wind_code("CF2509.ZCE") == "CF2509.CZCE"

    def test_normalize_exchange(self) -> None:
        assert normalize_exchange("SH") == "SSE"
        assert normalize_exchange("SZ") == "SZSE"
        assert normalize_exchange("BJ") == "BSE"
        assert normalize_exchange("SHF") == "SHFE"
        assert normalize_exchange("ZCE") == "CZCE"
        assert normalize_exchange("SHFE") == "SHFE"
        assert normalize_exchange("CFFEX") == "CFFEX"
        assert normalize_exchange("INE") == "INE"


# ============================================================
# 9. strict 模式
# ============================================================


class TestStrictMode:
    def test_strict_unknown_prefix_raises(self) -> None:
        with pytest.raises(SymbolParseError, match="无法识别的代码前缀"):
            parse_symbol("778888", strict=True)

    def test_strict_bad_suffix_raises(self) -> None:
        with pytest.raises(SymbolParseError, match="未知交易所后缀"):
            parse_symbol("600519.XXX", strict=True)

    def test_strict_hint_future_mismatch_raises(self) -> None:
        with pytest.raises(SymbolParseError, match="hint_asset='future'"):
            parse_symbol("600519.SH", hint_asset="future", strict=True)

    def test_non_strict_unknown_prefix_warns(self) -> None:
        """非 strict 模式: 未知前缀 → RuntimeWarning + UNKNOWN 降级。"""
        with pytest.warns(RuntimeWarning, match="无法识别裸码前缀"):
            info = parse_symbol("778888")
        assert info.exchange == "UNKNOWN"
        assert info.asset_type == "UNKNOWN"
        assert len(info.warnings) > 0

    def test_non_strict_bad_suffix_warns(self) -> None:
        """非 strict 模式: 未知后缀 → warning + best-effort。"""
        info = parse_symbol("600519.XXX")
        # 前缀规则兜底: 60 → SSE
        assert info.exchange == "SSE"
        assert len(info.warnings) > 0

    def test_symbol_parse_error_attributes(self) -> None:
        try:
            parse_symbol("778888", strict=True)
        except SymbolParseError as e:
            assert e.raw == "778888"
            assert "77" in e.reason


# ============================================================
# 10. NewType 向后兼容 + SymbolInfo 不可变
# ============================================================


class TestBackwardCompat:
    def test_newtype_is_str_at_runtime(self) -> None:
        """NewType 运行时退化为 str, 传入旧函数零修改。"""
        code: AShareCode6 = AShareCode6("600519")
        assert isinstance(code, str)
        assert code == "600519"

        wc: WindCode = WindCode("600519.SH")
        assert isinstance(wc, str)

        secid: EastMoneySecId = EastMoneySecId("1.600519")
        assert isinstance(secid, str)

    def test_newtype_passes_to_str_function(self) -> None:
        """NewType 值可以直接传入期望 str 的函数。"""
        code = AShareCode6("600519")
        info = parse_symbol(code)
        assert info.code6 == "600519"

    def test_symbol_info_frozen(self) -> None:
        """SymbolInfo 是 frozen dataclass, 不可变。"""
        info = parse_symbol("600519.SH")
        with pytest.raises((AttributeError, Exception)):
            info.exchange = "SZSE"  # type: ignore[misc]

    def test_symbol_info_warnings_empty_on_success(self) -> None:
        info = parse_symbol("600519.SH")
        assert info.warnings == []

    def test_exchanges_contains_all(self) -> None:
        assert "SSE" in EXCHANGES
        assert "SZSE" in EXCHANGES
        assert "BSE" in EXCHANGES
        assert "CFFEX" in EXCHANGES
        assert "SHFE" in EXCHANGES
        assert "INE" in EXCHANGES
        assert "DCE" in EXCHANGES
        assert "CZCE" in EXCHANGES
        assert "GFEX" in EXCHANGES

    def test_futures_pattern_matches_all_exchanges(self) -> None:
        """FUTURES_CODE_PATTERN 覆盖所有期货交易所 + 旧写法。"""
        test_cases = [
            ("IF2507.CFFEX", True),
            ("CU2508.SHFE", True),
            ("CU2508.SHF", True),   # 旧写法
            ("SC2509.INE", True),
            ("M2509.DCE", True),
            ("CF2509.CZCE", True),
            ("CF2509.ZCE", True),   # 旧写法
            ("SI2509.GFEX", True),
            ("600519.SH", False),    # 非期货
            ("IF2507", False),       # 无交易所
        ]
        for code, expected in test_cases:
            assert FUTURES_CODE_PATTERN.match(code) is not None if expected \
                else FUTURES_CODE_PATTERN.match(code) is None, \
                f"FUTURES_CODE_PATTERN.match({code!r}) 应为 {'匹配' if expected else '不匹配'}"
