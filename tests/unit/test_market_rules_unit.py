"""test_market_rules_unit.py — 市场规则常量单一事实源单元测试

覆盖要点:
    - normalize_symbol_code 多格式归一化
    - is_20cm_symbol (白名单 + 正则)
    - get_abnormal_threshold 差异化阈值
    - classify_board 板别分类
    - batch_classify 批量
    - register_20cm_etf 运行时注册
    - is_20cm_etf_explicit 白名单审计
"""
from __future__ import annotations

import pytest

from utils.market_rules import (
    ABNORMAL_RETURN_THRESHOLD_20CM,
    ABNORMAL_RETURN_THRESHOLD_INTERNAL,
    CROSS_VALIDATION_THRESHOLD,
    COVERAGE_THRESHOLD,
    MIN_SIGNIFICANT_WEIGHT,
    MIN_POSITIVE_PRICE,
    batch_classify,
    classify_board,
    get_abnormal_threshold,
    is_20cm_etf_explicit,
    is_20cm_symbol,
    normalize_symbol_code,
    register_20cm_etf,
)


# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_thresholds(self):
        assert ABNORMAL_RETURN_THRESHOLD_INTERNAL == 0.20
        assert ABNORMAL_RETURN_THRESHOLD_20CM == 0.30
        assert CROSS_VALIDATION_THRESHOLD == 0.10
        assert COVERAGE_THRESHOLD == 0.80
        assert MIN_SIGNIFICANT_WEIGHT == 0.001
        assert MIN_POSITIVE_PRICE == 0.001


# ============================================================
# normalize_symbol_code
# ============================================================


class TestNormalizeSymbolCode:
    @pytest.mark.unit
    def test_plain_6_digits(self):
        assert normalize_symbol_code("600276") == "600276"

    @pytest.mark.unit
    def test_with_sh_suffix(self):
        assert normalize_symbol_code("600276.SH") == "600276"

    @pytest.mark.unit
    def test_with_sz_suffix(self):
        assert normalize_symbol_code("300750.SZ") == "300750"

    @pytest.mark.unit
    def test_with_sh_prefix(self):
        assert normalize_symbol_code("sh600276") == "600276"

    @pytest.mark.unit
    def test_with_sh_dot_prefix(self):
        assert normalize_symbol_code("SH.600276") == "600276"

    @pytest.mark.unit
    def test_lowercase_suffix(self):
        assert normalize_symbol_code("300750.sz") == "300750"

    @pytest.mark.unit
    def test_empty_string(self):
        assert normalize_symbol_code("") == ""

    @pytest.mark.unit
    def test_none(self):
        assert normalize_symbol_code(None) == ""

    @pytest.mark.unit
    def test_no_digits(self):
        assert normalize_symbol_code("ABCDEF") == ""

    @pytest.mark.unit
    def test_extra_chars_with_digits(self):
        assert normalize_symbol_code("prefix_600276_extra") == "600276"


# ============================================================
# is_20cm_symbol
# ============================================================


class TestIs20cmSymbol:
    @pytest.mark.unit
    def test_star_board_688(self):
        assert is_20cm_symbol("688981.SH") is True

    @pytest.mark.unit
    def test_star_board_689(self):
        assert is_20cm_symbol("689009") is True

    @pytest.mark.unit
    def test_gem_300(self):
        assert is_20cm_symbol("300750.SZ") is True

    @pytest.mark.unit
    def test_gem_301(self):
        assert is_20cm_symbol("301088") is True

    @pytest.mark.unit
    def test_star_etf_588(self):
        assert is_20cm_symbol("588080.SH") is True

    @pytest.mark.unit
    def test_star_etf_562(self):
        assert is_20cm_symbol("562500") is True

    @pytest.mark.unit
    def test_explicit_whitelist_159915(self):
        assert is_20cm_symbol("159915.SZ") is True

    @pytest.mark.unit
    def test_explicit_whitelist_159952(self):
        assert is_20cm_symbol("159952") is True

    @pytest.mark.unit
    def test_main_board_600(self):
        assert is_20cm_symbol("600519.SH") is False

    @pytest.mark.unit
    def test_main_board_000(self):
        assert is_20cm_symbol("000001.SZ") is False

    @pytest.mark.unit
    def test_main_etf_510(self):
        assert is_20cm_symbol("510300.SH") is False

    @pytest.mark.unit
    def test_non_20cm_159_etf(self):
        """159919 沪深300 ETF → 10cm (不在白名单)"""
        assert is_20cm_symbol("159919") is False

    @pytest.mark.unit
    def test_empty_code(self):
        assert is_20cm_symbol("") is False

    @pytest.mark.unit
    def test_invalid_code(self):
        assert is_20cm_symbol("ABC") is False


# ============================================================
# get_abnormal_threshold
# ============================================================


class TestGetAbnormalThreshold:
    @pytest.mark.unit
    def test_20cm_threshold(self):
        assert get_abnormal_threshold("300750.SZ") == 0.30

    @pytest.mark.unit
    def test_10cm_threshold(self):
        assert get_abnormal_threshold("600519.SH") == 0.20

    @pytest.mark.unit
    def test_star_board_threshold(self):
        assert get_abnormal_threshold("688981") == 0.30

    @pytest.mark.unit
    def test_etf_20cm_whitelist(self):
        assert get_abnormal_threshold("159915") == 0.30

    @pytest.mark.unit
    def test_etf_10cm(self):
        assert get_abnormal_threshold("510300") == 0.20


# ============================================================
# classify_board
# ============================================================


class TestClassifyBoard:
    @pytest.mark.unit
    def test_classify_20cm(self):
        assert classify_board("300750.SZ") == "20cm"

    @pytest.mark.unit
    def test_classify_10cm_main(self):
        assert classify_board("600519.SH") == "10cm"

    @pytest.mark.unit
    def test_classify_10cm_etf(self):
        assert classify_board("510300.SH") == "10cm"

    @pytest.mark.unit
    def test_classify_unknown(self):
        assert classify_board("ABC") == "unknown"

    @pytest.mark.unit
    def test_classify_empty(self):
        assert classify_board("") == "unknown"

    @pytest.mark.unit
    def test_classify_159_20cm_whitelist(self):
        assert classify_board("159915") == "20cm"

    @pytest.mark.unit
    def test_classify_159_10cm_normal(self):
        assert classify_board("159919") == "10cm"


# ============================================================
# batch_classify
# ============================================================


class TestBatchClassify:
    @pytest.mark.unit
    def test_batch_mixed(self):
        symbols = ["300750.SZ", "600519.SH", "688981", "510300", "ABC"]
        result = batch_classify(symbols)
        assert result["300750.SZ"] == "20cm"
        assert result["600519.SH"] == "10cm"
        assert result["688981"] == "20cm"
        assert result["510300"] == "10cm"
        assert result["ABC"] == "unknown"

    @pytest.mark.unit
    def test_batch_empty(self):
        assert batch_classify([]) == {}


# ============================================================
# register_20cm_etf + is_20cm_etf_explicit
# ============================================================


class TestRegister20cmEtf:
    @pytest.mark.unit
    def test_register_new_code(self):
        """注册新代码后, is_20cm_symbol 返回 True"""
        register_20cm_etf("159999")
        assert is_20cm_symbol("159999") is True
        assert is_20cm_etf_explicit("159999") is True

    @pytest.mark.unit
    def test_register_with_suffix(self):
        register_20cm_etf("159998.SZ")
        assert is_20cm_etf_explicit("159998") is True

    @pytest.mark.unit
    def test_register_invalid_code(self):
        """无效代码不注册"""
        register_20cm_etf("ABC")
        assert is_20cm_etf_explicit("ABC") is False

    @pytest.mark.unit
    def test_register_empty(self):
        register_20cm_etf("")
        # 不影响已有白名单
        assert is_20cm_etf_explicit("159915") is True

    @pytest.mark.unit
    def test_existing_whitelist(self):
        assert is_20cm_etf_explicit("159915") is True
        assert is_20cm_etf_explicit("159952") is True

    @pytest.mark.unit
    def test_not_in_whitelist(self):
        assert is_20cm_etf_explicit("159919") is False