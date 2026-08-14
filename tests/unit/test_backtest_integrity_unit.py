# -*- coding: utf-8 -*-
"""backtest_integrity 单元测试 — 前视偏差/alpha 来源/综合校验全分支覆盖"""
from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.backtest_integrity import (  # noqa: E402
    check_no_future_leakage,
    evaluate_alpha_provenance,
    validate_backtest,
)


class TestCheckNoFutureLeakageEmpty:
    def test_none_prices(self):
        ok, reason = check_no_future_leakage(None, "2026-07-21")
        assert ok is True
        assert reason == "无价格数据，跳过泄漏检测"

    def test_empty_series(self):
        s = pd.Series([], dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-21")
        assert ok is True
        assert reason == "无价格数据，跳过泄漏检测"

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        ok, reason = check_no_future_leakage(df, "2026-07-21")
        assert ok is True
        assert reason == "无价格数据，跳过泄漏检测"


class TestCheckNoFutureLeakageNoLeakage:
    def test_all_dates_before_cutoff(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D")
        s = pd.Series(range(10), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-15")
        assert ok is True
        assert reason == "无未来数据泄漏"

    def test_cutoff_equal_to_last_date(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-05")
        assert ok is True
        assert reason == "无未来数据泄漏"

    def test_string_dates_index(self):
        idx = pd.Index(["2026-07-01", "2026-07-02", "2026-07-03"])
        s = pd.Series([1.0, 2.0, 3.0], index=idx)
        ok, reason = check_no_future_leakage(s, "2026-07-10")
        assert ok is True
        assert reason == "无未来数据泄漏"


class TestCheckNoFutureLeakageWithLeakage:
    def test_future_date_detected(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D")
        s = pd.Series(range(10), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-05")
        assert ok is False
        assert "前视偏差" in reason
        assert "5" in reason

    def test_one_day_after_cutoff(self):
        idx = pd.DatetimeIndex(["2026-07-20", "2026-07-22"])
        s = pd.Series([1.0, 2.0], index=idx)
        ok, reason = check_no_future_leakage(s, "2026-07-21")
        assert ok is False
        assert "1" in reason

    def test_string_dates_with_leakage(self):
        idx = pd.Index(["2026-07-01", "2026-07-25"])
        s = pd.Series([1.0, 2.0], index=idx)
        ok, reason = check_no_future_leakage(s, "2026-07-10")
        assert ok is False
        assert "1" in reason


class TestCheckNoFutureLeakageTzAware:
    def test_tz_aware_index_no_leakage(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D", tz="Asia/Shanghai")
        s = pd.Series(range(5), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-10")
        assert ok is True
        assert reason == "无未来数据泄漏"

    def test_tz_aware_index_with_leakage(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D", tz="Asia/Shanghai")
        s = pd.Series(range(10), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "2026-07-05")
        assert ok is False
        assert "前视偏差" in reason

    def test_tz_aware_cutoff_naive_index(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        cutoff = pd.Timestamp("2026-07-10", tz="Asia/Shanghai")
        ok, reason = check_no_future_leakage(s, cutoff)
        assert ok is True
        assert reason == "无未来数据泄漏"

    def test_tz_aware_both_with_leakage(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D", tz="Asia/Shanghai")
        s = pd.Series(range(10), index=idx, dtype=float)
        cutoff = pd.Timestamp("2026-07-05", tz="Asia/Shanghai")
        ok, reason = check_no_future_leakage(s, cutoff)
        assert ok is False

    def test_tz_aware_elements_in_plain_index_no_leakage(self):
        ts_list = [
            pd.Timestamp("2026-07-01", tz="Asia/Shanghai"),
            pd.Timestamp("2026-07-02", tz="Asia/Shanghai"),
            pd.Timestamp("2026-07-03", tz="Asia/Shanghai"),
        ]
        idx = pd.Index(ts_list, dtype=object)
        s = pd.Series([1.0, 2.0, 3.0], index=idx)
        ok, reason = check_no_future_leakage(s, "2026-07-10")
        assert ok is True
        assert reason == "无未来数据泄漏"

    def test_tz_aware_elements_in_plain_index_with_leakage(self):
        ts_list = [
            pd.Timestamp("2026-07-01", tz="Asia/Shanghai"),
            pd.Timestamp("2026-07-15", tz="Asia/Shanghai"),
        ]
        idx = pd.Index(ts_list, dtype=object)
        s = pd.Series([1.0, 2.0], index=idx)
        ok, reason = check_no_future_leakage(s, "2026-07-10")
        assert ok is False
        assert "前视偏差" in reason


class TestCheckNoFutureLeakageException:
    def test_invalid_as_of_date_fail_closed(self):
        idx = pd.date_range("2026-07-01", periods=3, freq="D")
        s = pd.Series(range(3), index=idx, dtype=float)
        ok, reason = check_no_future_leakage(s, "not_a_valid_date")
        assert ok is False
        assert "fail-closed" in reason

    def test_unhashable_index_element_fail_closed(self):
        class BadIndex:
            def __iter__(self):
                return iter([{"complex": "object"}])

            @property
            def tz(self):
                return None

        class BadSeries:
            def __len__(self):
                return 1

            index = BadIndex()

        ok, reason = check_no_future_leakage(BadSeries(), "2026-07-10")
        assert ok is False
        assert "fail-closed" in reason


class TestEvaluateAlphaProvenanceNone:
    def test_none_returns_unknown(self):
        assert evaluate_alpha_provenance(None) == "unknown"


class TestEvaluateAlphaProvenanceDict:
    def test_dict_category_mock(self):
        assert evaluate_alpha_provenance({"category": "mock"}) == "mock"

    def test_dict_category_real(self):
        assert evaluate_alpha_provenance({"category": "real"}) == "real"

    def test_dict_active_factors_zero(self):
        assert evaluate_alpha_provenance({"active_factors": 0}) == "mock"

    def test_dict_active_factors_zero_with_evals(self):
        report = {
            "active_factors": 0,
            "evaluations": [{"category": "mock"}, {"category": "real"}],
        }
        assert evaluate_alpha_provenance(report) == "mock"

    def test_dict_real_evals_with_active_factors(self):
        report = {
            "active_factors": 3,
            "evaluations": [{"category": "real"}, {"category": "mock"}],
        }
        assert evaluate_alpha_provenance(report) == "real"

    def test_dict_all_mock_evals(self):
        report = {
            "active_factors": 2,
            "evaluations": [{"category": "mock"}, {"category": "mock"}],
        }
        assert evaluate_alpha_provenance(report) == "mock"

    def test_dict_empty_evals_unknown(self):
        report = {"active_factors": 2, "evaluations": []}
        assert evaluate_alpha_provenance(report) == "unknown"

    def test_dict_no_category_no_active_returns_mock(self):
        assert evaluate_alpha_provenance({}) == "mock"

    def test_dict_evals_none_treated_as_empty(self):
        report = {"active_factors": 2, "evaluations": None}
        assert evaluate_alpha_provenance(report) == "unknown"

    def test_dict_only_real_evals_no_active_returns_mock(self):
        report = {"evaluations": [{"category": "real"}]}
        assert evaluate_alpha_provenance(report) == "mock"

    def test_dict_active_factors_positive_no_evals(self):
        report = {"active_factors": 5}
        assert evaluate_alpha_provenance(report) == "unknown"

    def test_dict_evals_with_non_dict_items(self):
        report = {
            "active_factors": 2,
            "evaluations": ["not_a_dict", {"category": "real"}],
        }
        assert evaluate_alpha_provenance(report) == "real"


class TestEvaluateAlphaProvenanceObject:
    def test_object_category_mock(self):
        obj = MagicMock()
        obj.category = "mock"
        obj.active_factors = 5
        assert evaluate_alpha_provenance(obj) == "mock"

    def test_object_category_real(self):
        obj = MagicMock()
        obj.category = "real"
        obj.active_factors = 0
        assert evaluate_alpha_provenance(obj) == "real"

    def test_object_active_factors_zero(self):
        obj = MagicMock()
        del obj.category
        obj.active_factors = 0
        assert evaluate_alpha_provenance(obj) == "mock"

    def test_object_no_category_no_active_unknown(self):
        obj = MagicMock()
        del obj.category
        del obj.active_factors
        assert evaluate_alpha_provenance(obj) == "unknown"

    def test_object_category_other_unknown(self):
        obj = MagicMock()
        obj.category = "other"
        obj.active_factors = 5
        assert evaluate_alpha_provenance(obj) == "unknown"

    def test_object_active_factors_positive_unknown(self):
        obj = MagicMock()
        del obj.category
        obj.active_factors = 5
        assert evaluate_alpha_provenance(obj) == "unknown"

    def test_plain_object_with_attributes(self):
        class Report:
            category = "real"
            active_factors = 3

        assert evaluate_alpha_provenance(Report()) == "real"

    def test_plain_object_mock(self):
        class Report:
            category = "mock"
            active_factors = 3

        assert evaluate_alpha_provenance(Report()) == "mock"

    def test_plain_object_zero_active(self):
        class Report:
            active_factors = 0

        assert evaluate_alpha_provenance(Report()) == "mock"


class TestValidateBacktestValid:
    def test_valid_backtest(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "real"}
        is_valid, issues = validate_backtest("2026-07-10", alpha, prices=prices)
        assert is_valid is True
        assert issues == []

    def test_valid_with_real_alpha_object(self):
        idx = pd.date_range("2026-07-01", periods=3, freq="D")
        s = pd.Series(range(3), index=idx, dtype=float)
        prices = {"SYM1": s}

        class RealAlpha:
            category = "real"
            active_factors = 2

        is_valid, issues = validate_backtest("2026-07-05", RealAlpha(), prices=prices)
        assert is_valid is True
        assert issues == []


class TestValidateBacktestInvalidLeakage:
    def test_invalid_due_to_leakage(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D")
        s = pd.Series(range(10), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "real"}
        is_valid, issues = validate_backtest("2026-07-05", alpha, prices=prices)
        assert is_valid is False
        assert any("SYM1" in i for i in issues)
        assert any("前视偏差" in i for i in issues)

    def test_invalid_multiple_symbols_leakage(self):
        idx_bad = pd.date_range("2026-07-01", periods=10, freq="D")
        idx_ok = pd.date_range("2026-07-01", periods=3, freq="D")
        prices = {
            "BAD": pd.Series(range(10), index=idx_bad, dtype=float),
            "OK": pd.Series(range(3), index=idx_ok, dtype=float),
        }
        alpha = {"category": "real"}
        is_valid, issues = validate_backtest("2026-07-05", alpha, prices=prices)
        assert is_valid is False
        assert any("BAD" in i for i in issues)
        assert not any("OK" in i for i in issues)


class TestValidateBacktestInvalidAlpha:
    def test_invalid_due_to_mock_alpha(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "mock"}
        is_valid, issues = validate_backtest("2026-07-10", alpha, prices=prices)
        assert is_valid is False
        assert any("alpha 来源为 'mock'" in i for i in issues)

    def test_invalid_due_to_unknown_alpha(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        unknown_alpha = {"active_factors": 2, "evaluations": []}
        is_valid, issues = validate_backtest("2026-07-10", unknown_alpha, prices=prices)
        assert is_valid is False
        assert any("alpha 来源为 'unknown'" in i for i in issues)

    def test_invalid_due_to_none_alpha(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        is_valid, issues = validate_backtest("2026-07-10", None, prices=prices)
        assert is_valid is False
        assert any("alpha 来源为 'unknown'" in i for i in issues)


class TestValidateBacktestNoPrices:
    def test_no_prices_adds_issue(self):
        alpha = {"category": "real"}
        is_valid, issues = validate_backtest("2026-07-10", alpha, prices=None)
        assert is_valid is False
        assert any("未提供价格数据" in i for i in issues)

    def test_empty_prices_dict_adds_issue(self):
        alpha = {"category": "real"}
        is_valid, issues = validate_backtest("2026-07-10", alpha, prices={})
        assert is_valid is False
        assert any("未提供价格数据" in i for i in issues)


class TestValidateBacktestRequireRealAlphaFalse:
    def test_mock_alpha_allowed_when_not_required(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "mock"}
        is_valid, issues = validate_backtest(
            "2026-07-10", alpha, prices=prices, require_real_alpha=False
        )
        assert is_valid is True
        assert issues == []

    def test_unknown_alpha_allowed_when_not_required(self):
        idx = pd.date_range("2026-07-01", periods=5, freq="D")
        s = pd.Series(range(5), index=idx, dtype=float)
        prices = {"SYM1": s}
        is_valid, issues = validate_backtest(
            "2026-07-10", {}, prices=prices, require_real_alpha=False
        )
        assert is_valid is True
        assert issues == []

    def test_leakage_still_invalidates_without_alpha_requirement(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D")
        s = pd.Series(range(10), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "mock"}
        is_valid, issues = validate_backtest(
            "2026-07-05", alpha, prices=prices, require_real_alpha=False
        )
        assert is_valid is False
        assert any("前视偏差" in i for i in issues)


class TestValidateBacktestCombined:
    def test_both_leakage_and_mock_alpha_issues(self):
        idx = pd.date_range("2026-07-01", periods=10, freq="D")
        s = pd.Series(range(10), index=idx, dtype=float)
        prices = {"SYM1": s}
        alpha = {"category": "mock"}
        is_valid, issues = validate_backtest("2026-07-05", alpha, prices=prices)
        assert is_valid is False
        assert len(issues) == 2
        assert any("SYM1" in i for i in issues)
        assert any("alpha 来源为 'mock'" in i for i in issues)

    def test_no_prices_and_mock_alpha_two_issues(self):
        alpha = {"category": "mock"}
        is_valid, issues = validate_backtest("2026-07-10", alpha, prices=None)
        assert is_valid is False
        assert len(issues) == 2
        assert any("未提供价格数据" in i for i in issues)
        assert any("alpha 来源为 'mock'" in i for i in issues)