# -*- coding: utf-8 -*-
"""evaluator 单元测试 — Alpha 因子标准化评估器全覆盖.

被测模块: utils/alpha_factor/evaluator.py
覆盖目标: >=90%
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha_factor.evaluator import (  # noqa: E402
    DecayResult,
    FactorTearSheet,
    QuantileReturn,
    TurnoverResult,
    _rank_corr,
    _split_into_quantiles,
    build_factor_tear_sheet,
    compute_factor_decay,
    compute_quantile_returns,
    compute_turnover,
    evaluate_all_factors_tear_sheets,
    tear_sheet_to_dict,
)


# ============================================================
# _rank_corr
# ============================================================

class TestRankCorr:
    def test_perfect_positive(self):
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        corr, _ = _rank_corr(x, y)
        assert corr > 0.99

    def test_perfect_negative(self):
        x = [1, 2, 3, 4, 5]
        y = [10, 8, 6, 4, 2]
        corr, _ = _rank_corr(x, y)
        assert corr < -0.99

    def test_too_short(self):
        corr, pval = _rank_corr([1, 2], [3, 4])
        assert corr == 0.0

    def test_length_mismatch(self):
        corr, _ = _rank_corr([1, 2, 3, 4, 5], [1, 2, 3])
        assert corr == 0.0

    def test_constant_series(self):
        corr, _ = _rank_corr([1, 1, 1, 1, 1], [2, 3, 4, 5, 6])
        assert corr == 0.0


# ============================================================
# _split_into_quantiles
# ============================================================

class TestSplitQuantiles:
    def test_basic_5_groups(self):
        fv = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
        groups = _split_into_quantiles(fv, n_quantiles=5)
        assert len(groups) == 5
        assert groups[1] == ["A"]
        assert groups[5] == ["E"]

    def test_10_symbols_5_groups(self):
        fv = {f"S{i}": i for i in range(1, 11)}
        groups = _split_into_quantiles(fv, n_quantiles=5)
        assert len(groups) == 5
        total = sum(len(v) for v in groups.values())
        assert total == 10

    def test_empty(self):
        groups = _split_into_quantiles({}, n_quantiles=5)
        assert groups == {}

    def test_none_values_filtered(self):
        fv = {"A": 1, "B": None, "C": 3, "D": 4, "E": 5, "F": 6}
        groups = _split_into_quantiles(fv, n_quantiles=5)
        total = sum(len(v) for v in groups.values())
        assert total == 5

    def test_non_divisible(self):
        fv = {f"S{i}": i for i in range(1, 8)}
        groups = _split_into_quantiles(fv, n_quantiles=3)
        total = sum(len(v) for v in groups.values())
        assert total == 7


# ============================================================
# compute_quantile_returns
# ============================================================

class TestQuantileReturns:
    def test_monotonic_factor(self):
        fv = {f"S{i}": i for i in range(1, 21)}
        fr = {f"S{i}": i * 0.001 for i in range(1, 21)}
        result = compute_quantile_returns(fv, fr, n_quantiles=5, factor_name="test")
        assert isinstance(result, QuantileReturn)
        assert result.factor_name == "test"
        assert result.long_short_return > 0
        assert result.monotonicity > 0.5

    def test_reverse_factor(self):
        fv = {f"S{i}": i for i in range(1, 21)}
        fr = {f"S{i}": -i * 0.001 for i in range(1, 21)}
        result = compute_quantile_returns(fv, fr, n_quantiles=5)
        assert result.long_short_return < 0

    def test_empty(self):
        result = compute_quantile_returns({}, {}, n_quantiles=5)
        assert result.long_short_return == 0.0

    def test_missing_returns(self):
        fv = {f"S{i}": i for i in range(1, 11)}
        fr = {"S1": 0.01}
        result = compute_quantile_returns(fv, fr, n_quantiles=5)
        assert isinstance(result, QuantileReturn)

    def test_forward_window_metadata(self):
        result = compute_quantile_returns({"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
                                          {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04, "E": 0.05},
                                          forward_window=10)
        assert result.forward_window == 10


# ============================================================
# compute_turnover
# ============================================================

class TestTurnover:
    def test_stable_portfolio(self):
        hist = [{"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}] * 10
        result = compute_turnover(hist, top_pct=0.4, factor_name="test")
        assert isinstance(result, TurnoverResult)
        assert result.avg_daily_turnover == 0.0

    def test_changing_portfolio(self):
        hist = [
            {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
            {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1},
        ] * 5
        result = compute_turnover(hist, top_pct=0.4)
        assert result.avg_daily_turnover > 0

    def test_single_period(self):
        result = compute_turnover([{"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}])
        assert result.avg_daily_turnover == 0.0
        assert result.weekly_turnover is None

    def test_empty_history(self):
        result = compute_turnover([])
        assert result.avg_daily_turnover == 0.0

    def test_weekly_turnover(self):
        hist = [{"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}] * 6
        result = compute_turnover(hist)
        assert result.weekly_turnover is not None

    def test_none_values(self):
        hist = [{"A": None, "B": 2, "C": 3, "D": 4, "E": 5}]
        result = compute_turnover(hist)
        assert isinstance(result, TurnoverResult)


# ============================================================
# compute_factor_decay
# ============================================================

class TestFactorDecay:
    def test_basic_decay(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr_by_window = {
            1: [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30,
            5: [{f"S{i}": 0.002 * i for i in range(1, 21)}] * 30,
            10: [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30,
        }
        result = compute_factor_decay(hist, fr_by_window, windows=[1, 5, 10], factor_name="test")
        assert isinstance(result, DecayResult)
        assert 1 in result.ic_by_window
        assert 5 in result.ic_by_window

    def test_insufficient_data(self):
        hist = [{f"S{i}": float(i) for i in range(1, 21)}] * 5
        fr_by_window = {1: [{f"S{i}": 0.001 for i in range(1, 21)}] * 5}
        result = compute_factor_decay(hist, fr_by_window, windows=[1])
        assert result.ic_by_window[1] == 0.0

    def test_default_windows(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr_by_window = {w: [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30 for w in [1, 2, 3, 5, 10, 15, 20]}
        result = compute_factor_decay(hist, fr_by_window)
        assert len(result.ic_by_window) == 7

    def test_half_life_fitted(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr_by_window = {
            1: [{f"S{i}": 0.01 * i for i in range(1, 21)}] * 30,
            2: [{f"S{i}": 0.008 * i for i in range(1, 21)}] * 30,
            3: [{f"S{i}": 0.006 * i for i in range(1, 21)}] * 30,
            5: [{f"S{i}": 0.004 * i for i in range(1, 21)}] * 30,
            10: [{f"S{i}": 0.002 * i for i in range(1, 21)}] * 30,
        }
        result = compute_factor_decay(hist, fr_by_window, windows=[1, 2, 3, 5, 10])
        assert result.half_life_days is not None


# ============================================================
# build_factor_tear_sheet
# ============================================================

class TestTearSheet:
    def test_name_only(self):
        sheet = build_factor_tear_sheet(factor_name="momentum")
        assert isinstance(sheet, FactorTearSheet)
        assert sheet.factor_name == "momentum"
        assert sheet.ic_mean == 0.0

    def test_with_history(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr = [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30
        sheet = build_factor_tear_sheet(
            factor_name="test",
            factor_history=hist,
            forward_returns_history=fr,
        )
        assert sheet.factor_name == "test"
        assert len(sheet.ic_series) > 0

    def test_with_quantile(self):
        fv = {f"S{i}": float(i) for i in range(1, 21)}
        fr = {f"S{i}": 0.001 * i for i in range(1, 21)}
        sheet = build_factor_tear_sheet(
            factor_name="test",
            latest_factor_values=fv,
            latest_forward_returns=fr,
        )
        assert sheet.quantile is not None

    def test_with_turnover(self):
        hist = [{"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}] * 10
        sheet = build_factor_tear_sheet(
            factor_name="test",
            factor_history=hist,
        )
        assert sheet.turnover is not None

    def test_with_decay(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr_by_window = {5: [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30}
        sheet = build_factor_tear_sheet(
            factor_name="test",
            factor_history=hist,
            forward_returns_by_window=fr_by_window,
        )
        assert sheet.decay is not None

    def test_full_sheet(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr = [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30
        fv = {f"S{i}": float(i) for i in range(1, 21)}
        fr_latest = {f"S{i}": 0.001 * i for i in range(1, 21)}
        fr_by_window = {5: fr}
        sheet = build_factor_tear_sheet(
            factor_name="full",
            factor_history=hist,
            forward_returns_history=fr,
            latest_factor_values=fv,
            latest_forward_returns=fr_latest,
            forward_returns_by_window=fr_by_window,
        )
        assert sheet.quantile is not None
        assert sheet.turnover is not None
        assert sheet.decay is not None


# ============================================================
# evaluate_all_factors_tear_sheets
# ============================================================

class TestEvaluateAll:
    def test_batch(self):
        hist = [{f"S{i}": float(i + t) for i in range(1, 21)} for t in range(30)]
        fr = [{f"S{i}": 0.001 * i for i in range(1, 21)}] * 30
        factor_history_by_name = {"alpha1": hist, "alpha2": hist}
        latest_fv = {"alpha1": {f"S{i}": float(i) for i in range(1, 21)},
                     "alpha2": {f"S{i}": float(i) for i in range(1, 21)}}
        latest_fr = {f"S{i}": 0.001 * i for i in range(1, 21)}
        sheets = evaluate_all_factors_tear_sheets(
            factor_history_by_name=factor_history_by_name,
            forward_returns_history=fr,
            latest_factor_values_by_name=latest_fv,
            latest_forward_returns=latest_fr,
        )
        assert "alpha1" in sheets
        assert "alpha2" in sheets

    def test_empty(self):
        sheets = evaluate_all_factors_tear_sheets(
            factor_history_by_name={},
            forward_returns_history=[],
            latest_factor_values_by_name={},
            latest_forward_returns={},
        )
        assert sheets == {}


# ============================================================
# tear_sheet_to_dict
# ============================================================

class TestTearSheetToDict:
    def test_minimal(self):
        sheet = FactorTearSheet(factor_name="test")
        d = tear_sheet_to_dict(sheet)
        assert d["factor_name"] == "test"
        assert d["ic_mean"] == 0.0
        assert "quantile" not in d

    def test_with_quantile(self):
        sheet = FactorTearSheet(
            factor_name="test",
            quantile=QuantileReturn(
                factor_name="test",
                quantile_returns={1: 0.01, 2: 0.02},
                long_short_return=0.01,
                monotonicity=0.9,
                forward_window=5,
            ),
        )
        d = tear_sheet_to_dict(sheet)
        assert "quantile" in d
        assert d["quantile"]["long_short_return"] == 0.01

    def test_with_turnover(self):
        sheet = FactorTearSheet(
            factor_name="test",
            turnover=TurnoverResult(
                factor_name="test",
                daily_turnover=[0.1, 0.2],
                avg_daily_turnover=0.15,
            ),
        )
        d = tear_sheet_to_dict(sheet)
        assert "turnover" in d
        assert d["turnover"]["avg_daily_turnover"] == 0.15

    def test_with_decay(self):
        sheet = FactorTearSheet(
            factor_name="test",
            decay=DecayResult(
                factor_name="test",
                ic_by_window={1: 0.05, 5: 0.03},
                icir_by_window={1: 0.5, 5: 0.3},
                half_life_days=7.5,
            ),
        )
        d = tear_sheet_to_dict(sheet)
        assert "decay" in d
        assert d["decay"]["half_life_days"] == 7.5

    def test_full(self):
        sheet = FactorTearSheet(
            factor_name="full",
            ic_mean=0.05,
            ic_std=0.1,
            ic_ir=0.5,
            ic_win_rate=0.6,
            t_stat=2.0,
            quantile=QuantileReturn("full", {1: 0.01}, 0.02, 0.9, 5),
            turnover=TurnoverResult("full", [0.1], 0.1),
            decay=DecayResult("full", {1: 0.05}, {1: 0.5}, 5.0),
        )
        d = tear_sheet_to_dict(sheet)
        assert d["ic_mean"] == 0.05
        assert "quantile" in d
        assert "turnover" in d
        assert "decay" in d