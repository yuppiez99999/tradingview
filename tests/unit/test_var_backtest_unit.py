"""var_backtest 单元测试 — VaR 回测框架 (Kupiec POF + Christoffersen + Basel 交通灯) 全分支覆盖.

被测模块: utils/var_backtest.py
覆盖目标: >=95%
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.var_backtest import (  # noqa: E402
    TRAFFIC_LIGHT_GREEN,
    TRAFFIC_LIGHT_RED,
    TRAFFIC_LIGHT_YELLOW,
    VaRBacktester,
    VarBacktestResult,
    _chi2_sf,
    backtest,
)

# ============================================================
# _chi2_sf
# ============================================================


class TestChi2Sf:
    def test_zero(self):
        assert _chi2_sf(0.0) == 1.0

    def test_negative(self):
        assert _chi2_sf(-1.0) == 1.0

    def test_large_x(self):
        assert _chi2_sf(100.0) < 1e-10

    def test_df1(self):
        result = _chi2_sf(3.84, df=1)
        assert 0.04 < result < 0.06

    def test_df_other(self):
        result = _chi2_sf(5.99, df=2)
        assert 0.04 < result < 0.06


# ============================================================
# VarBacktestResult
# ============================================================


class TestVarBacktestResult:
    def test_construction(self):
        r = VarBacktestResult(
            exceptions_count=3,
            total_observations=250,
            expected_exceptions=2.5,
            exception_rate=0.012,
            expected_rate=0.01,
            kupiec_pof_statistic=0.1,
            kupiec_p_value=0.75,
            christoffersen_statistic=0.05,
            christoffersen_p_value=0.82,
            traffic_light="GREEN",
            is_model_valid=True,
            confidence=0.99,
            window=250,
        )
        assert r.exceptions_count == 3
        assert r.summary_report == ""
        assert r.transition_matrix is None

    def test_to_dict(self):
        r = VarBacktestResult(
            exceptions_count=5,
            total_observations=250,
            expected_exceptions=2.5,
            exception_rate=0.02,
            expected_rate=0.01,
            kupiec_pof_statistic=1.5,
            kupiec_p_value=0.22,
            christoffersen_statistic=0.8,
            christoffersen_p_value=0.37,
            traffic_light="YELLOW",
            is_model_valid=True,
            confidence=0.99,
            window=250,
            transition_matrix=[[240, 5], [5, 0]],
        )
        d = r.to_dict()
        assert d["exceptions_count"] == 5
        assert d["traffic_light"] == "YELLOW"
        assert d["transition_matrix"] == [[240, 5], [5, 0]]


# ============================================================
# VaRBacktester.backtest
# ============================================================


class TestBacktest:
    def test_green_zone(self):
        np.random.seed(42)
        var_est = np.full(250, 0.0233)
        ret = np.random.randn(250) * 0.01
        bt = VaRBacktester()
        result = bt.backtest(var_est, ret, confidence=0.99)
        assert result.traffic_light == TRAFFIC_LIGHT_GREEN
        assert result.total_observations == 250

    def test_red_zone(self):
        var_est = np.full(250, 0.001)
        ret = np.full(250, -0.01)
        bt = VaRBacktester()
        result = bt.backtest(var_est, ret, confidence=0.99)
        assert result.traffic_light == TRAFFIC_LIGHT_RED
        assert result.is_model_valid is False

    def test_yellow_zone(self):
        var_est = np.full(250, 0.015)
        ret = np.zeros(250)
        for i in [50, 100, 150, 200, 240]:
            ret[i] = -0.02
        bt = VaRBacktester()
        result = bt.backtest(var_est, ret, confidence=0.99)
        assert result.traffic_light == TRAFFIC_LIGHT_YELLOW

    def test_length_mismatch(self):
        bt = VaRBacktester()
        with pytest.raises(ValueError, match="不一致"):
            bt.backtest(np.array([0.01, 0.02]), np.array([0.01]))

    def test_empty_input(self):
        bt = VaRBacktester()
        with pytest.raises(ValueError, match="为空"):
            bt.backtest(np.array([]), np.array([]))

    def test_window_truncation(self):
        var_est = np.full(300, 0.02)
        ret = np.zeros(300)
        bt = VaRBacktester()
        result = bt.backtest(var_est, ret, confidence=0.99, window=250)
        assert result.total_observations == 250

    def test_negative_var_abs(self):
        var_est = np.full(100, -0.02)
        ret = np.full(100, -0.01)
        bt = VaRBacktester()
        result = bt.backtest(var_est, ret, confidence=0.99)
        assert result.exceptions_count == 0

    def test_module_level_backtest(self):
        var_est = np.full(250, 0.0233)
        ret = np.random.randn(250) * 0.01
        result = backtest(var_est, ret, confidence=0.99)
        assert isinstance(result, VarBacktestResult)


# ============================================================
# VaRBacktester._kupiec_pof_test
# ============================================================


class TestKupiecPof:
    def test_zero_exceptions(self):
        bt = VaRBacktester()
        stat, pval = bt._kupiec_pof_test(n=250, x=0, p=0.01)
        assert stat > 0
        assert 0 < pval <= 1

    def test_all_exceptions(self):
        bt = VaRBacktester()
        stat, pval = bt._kupiec_pof_test(n=250, x=250, p=0.01)
        assert stat > 0
        assert pval < 0.05

    def test_normal_case(self):
        bt = VaRBacktester()
        stat, pval = bt._kupiec_pof_test(n=250, x=2, p=0.01)
        assert stat >= 0
        assert 0 < pval <= 1

    def test_n_zero(self):
        bt = VaRBacktester()
        stat, pval = bt._kupiec_pof_test(n=0, x=0, p=0.01)
        assert stat == 0.0 and pval == 1.0


# ============================================================
# VaRBacktester._christoffersen_test
# ============================================================


class TestChristoffersen:
    def test_no_exceptions(self):
        bt = VaRBacktester()
        exc = np.array([False] * 100)
        stat, pval, matrix = bt._christoffersen_test(exc)
        assert stat == 0.0
        assert pval == 1.0

    def test_with_clustering(self):
        bt = VaRBacktester()
        exc = np.array([False] * 50 + [True, True, True] + [False] * 47)
        stat, pval, matrix = bt._christoffersen_test(exc)
        assert matrix is not None
        assert matrix[1][1] > 0

    def test_short_array(self):
        bt = VaRBacktester()
        stat, pval, matrix = bt._christoffersen_test(np.array([True]))
        assert stat == 0.0 and pval == 1.0

    def test_independent_exceptions(self):
        bt = VaRBacktester()
        np.random.seed(42)
        exc = np.random.random(250) < 0.01
        stat, pval, matrix = bt._christoffersen_test(exc)
        assert stat >= 0
        assert 0 <= pval <= 1


# ============================================================
# VaRBacktester._traffic_light
# ============================================================


class TestTrafficLight:
    def test_green(self):
        bt = VaRBacktester()
        for x in range(5):
            assert bt._traffic_light(x) == TRAFFIC_LIGHT_GREEN

    def test_yellow(self):
        bt = VaRBacktester()
        for x in range(5, 10):
            assert bt._traffic_light(x) == TRAFFIC_LIGHT_YELLOW

    def test_red(self):
        bt = VaRBacktester()
        for x in [10, 15, 20, 50]:
            assert bt._traffic_light(x) == TRAFFIC_LIGHT_RED


# ============================================================
# VaRBacktester._generate_report
# ============================================================


class TestGenerateReport:
    def test_report_content(self):
        bt = VaRBacktester()
        report = bt._generate_report(
            n=250,
            x=3,
            p=0.01,
            expected=2.5,
            confidence=0.99,
            kupiec_stat=0.1,
            kupiec_pval=0.75,
            christ_stat=0.05,
            christ_pval=0.82,
            traffic=TRAFFIC_LIGHT_GREEN,
            is_valid=True,
            trans_matrix=[[245, 3], [2, 0]],
        )
        assert "VaR" in report
        assert "GREEN" in report
        assert "Kupiec" in report

    def test_red_report(self):
        bt = VaRBacktester()
        report = bt._generate_report(
            n=250,
            x=15,
            p=0.01,
            expected=2.5,
            confidence=0.99,
            kupiec_stat=50.0,
            kupiec_pval=0.001,
            christ_stat=5.0,
            christ_pval=0.02,
            traffic=TRAFFIC_LIGHT_RED,
            is_valid=False,
            trans_matrix=[[230, 10], [10, 5]],
        )
        assert "RED" in report
        assert "无效" in report
