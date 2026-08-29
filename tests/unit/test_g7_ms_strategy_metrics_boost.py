"""G7 boost: ms_strategy/src/backtest/metrics.py 单元测试.

覆盖 PerformanceMetrics / DeflatedSharpeRatio 及模块级便捷函数的全部公开接口,
包括 Sharpe/Sortino/Calmar/MaxDD/VaR/CVaR/Omega/ProfitFactor 的核心路径与边界分支.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.metrics import (  # noqa: E402
    DeflatedSharpeRatio,
    PerformanceMetrics,
    compute_all_metrics,
    compute_calmar,
    compute_dsr,
    compute_max_drawdown,
    compute_sharpe,
    compute_sortino,
)

# ============================================================
# 1. PerformanceMetrics 基础指标
# ============================================================


class TestAnnualReturn:
    def test_normal(self):
        rets = pd.Series([0.001] * 252)
        pm = PerformanceMetrics(rets)
        assert pm.annual_return() == pytest.approx(0.001 * 252)

    def test_insufficient_samples(self):
        pm = PerformanceMetrics(pd.Series([0.001]))
        assert pm.annual_return() == 0.0

    def test_empty(self):
        pm = PerformanceMetrics(pd.Series([], dtype=float))
        assert pm.annual_return() == 0.0


class TestAnnualVol:
    def test_normal(self):
        rets = pd.Series([0.01, -0.01, 0.02, -0.02] * 10)
        pm = PerformanceMetrics(rets)
        vol = pm.annual_vol()
        assert vol > 0

    def test_insufficient_samples(self):
        pm = PerformanceMetrics(pd.Series([0.001]))
        assert pm.annual_vol() == 0.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        rets = pd.Series([0.01, 0.01, 0.01])
        pm = PerformanceMetrics(rets)
        assert pm.max_drawdown() == 0.0

    def test_with_drawdown(self):
        rets = pd.Series([0.05, -0.10, 0.02])
        pm = PerformanceMetrics(rets)
        assert pm.max_drawdown() < 0

    def test_empty(self):
        pm = PerformanceMetrics(pd.Series([], dtype=float))
        assert pm.max_drawdown() == 0.0

    def test_cumulative_le_zero(self):
        # 收益导致累计净值 <= 0
        rets = pd.Series([0.05, -1.5])
        pm = PerformanceMetrics(rets)
        assert pm.max_drawdown() == 0.0

    def test_non_finite(self):
        rets = pd.Series([float("inf"), 0.01])
        pm = PerformanceMetrics(rets)
        # inf 被 dropna 不处理, 但 cumprod 会 inf; 应返回 0
        result = pm.max_drawdown()
        assert math.isfinite(result) or result == 0.0


class TestSharpeRatio:
    def test_positive(self):
        rets = pd.Series([0.002] * 100 + [0.001] * 100)
        pm = PerformanceMetrics(rets, rf=0.0)
        assert pm.sharpe_ratio() > 0

    def test_zero_vol(self):
        rets = pd.Series([0.0] * 100)
        pm = PerformanceMetrics(rets)
        assert pm.sharpe_ratio() == 0.0

    def test_negative(self):
        rets = pd.Series([-0.002] * 100)
        pm = PerformanceMetrics(rets, rf=0.0)
        assert pm.sharpe_ratio() < 0


# ============================================================
# 2. Sortino / Calmar / Objective
# ============================================================


class TestSortinoRatio:
    def test_normal(self):
        rets = pd.Series([0.002, -0.001, 0.003, -0.002] * 50)
        pm = PerformanceMetrics(rets, rf=0.0)
        s = pm.sortino_ratio()
        assert math.isfinite(s)

    def test_no_downside_positive_annual(self):
        rets = pd.Series([0.001] * 100)
        pm = PerformanceMetrics(rets, rf=0.0)
        # 全正收益, 无下行 → inf
        assert pm.sortino_ratio() == float("inf")

    def test_no_downside_equal_rf(self):
        # rf=0, returns 全 0 → excess 全 0 → downside 空, annual == rf == 0
        rets = pd.Series([0.0] * 100)
        pm = PerformanceMetrics(rets, rf=0.0)
        # annual_return == 0 == rf → 不满足 > rf → 返回 0.0
        assert pm.sortino_ratio() == 0.0

    def test_downside_std_zero(self):
        # downside 全部相等 → std=0
        rets = pd.Series([-0.001, -0.001, 0.01, 0.01])
        pm = PerformanceMetrics(rets, rf=0.0)
        # downside = [-0.001, -0.001], std=0 → 返回 0.0
        assert pm.sortino_ratio() == 0.0


class TestCalmarRatio:
    def test_no_drawdown(self):
        rets = pd.Series([0.01] * 100)
        pm = PerformanceMetrics(rets)
        assert pm.calmar_ratio() == 0.0

    def test_with_drawdown(self):
        rets = pd.Series([0.05, -0.10, 0.02] * 50)
        pm = PerformanceMetrics(rets, rf=0.0)
        c = pm.calmar_ratio()
        assert math.isfinite(c)


class TestObjective:
    def test_no_weights(self):
        rets = pd.Series([0.002, -0.001] * 50)
        pm = PerformanceMetrics(rets, rf=0.0)
        obj = pm.objective()
        expected = pm.sortino_ratio() + 0.5 * pm.calmar_ratio()
        assert obj == pytest.approx(expected)

    def test_with_weights(self):
        rets = pd.Series([0.002, -0.001] * 50)
        pm = PerformanceMetrics(rets, rf=0.0, l2_lambda=0.1)
        w = np.array([0.5, 0.5])
        obj = pm.objective(w)
        expected = pm.sortino_ratio() + 0.5 * pm.calmar_ratio() - 0.1 * np.sum(w**2)
        assert obj == pytest.approx(expected)


# ============================================================
# 3. VaR / CVaR / WinRate / ProfitFactor / Omega
# ============================================================


class TestValueAtRisk:
    def test_normal(self):
        np.random.seed(42)
        rets = pd.Series(np.random.normal(0, 0.01, 1000))
        pm = PerformanceMetrics(rets)
        var = pm.value_at_risk(0.95)
        assert var < 0

    def test_confidence_level(self):
        rets = pd.Series(np.linspace(-0.05, 0.05, 100))
        pm = PerformanceMetrics(rets)
        var95 = pm.value_at_risk(0.95)
        var99 = pm.value_at_risk(0.99)
        assert var99 <= var95


class TestConditionalVar:
    def test_normal(self):
        np.random.seed(42)
        rets = pd.Series(np.random.normal(0, 0.01, 1000))
        pm = PerformanceMetrics(rets)
        cvar = pm.conditional_var(0.95)
        assert cvar < 0

    def test_empty_tail_returns_var(self):
        rets = pd.Series([0.01] * 100)
        pm = PerformanceMetrics(rets)
        cvar = pm.conditional_var(0.95)

        # 全正收益, tail 可能为空 → 返回 var
        assert isinstance(cvar, float)


class TestWinRate:
    def test_mixed(self):
        rets = pd.Series([0.01, -0.01, 0.02, -0.02])
        pm = PerformanceMetrics(rets)
        assert pm.win_rate() == pytest.approx(0.5)

    def test_all_positive(self):
        rets = pd.Series([0.01, 0.02, 0.03])
        pm = PerformanceMetrics(rets)
        assert pm.win_rate() == 1.0


class TestProfitFactor:
    def test_normal(self):
        rets = pd.Series([0.02, -0.01, 0.03, -0.02])
        pm = PerformanceMetrics(rets)
        gains = 0.02 + 0.03
        losses = 0.01 + 0.02
        assert pm.profit_factor() == pytest.approx(gains / losses)

    def test_no_losses_returns_inf(self):
        rets = pd.Series([0.01, 0.02, 0.03])
        pm = PerformanceMetrics(rets)
        assert pm.profit_factor() == float("inf")


class TestOmegaRatio:
    def test_normal(self):
        rets = pd.Series([0.02, -0.01, 0.03, -0.02])
        pm = PerformanceMetrics(rets)
        assert pm.omega_ratio() > 0

    def test_no_losses_returns_inf(self):
        rets = pd.Series([0.01, 0.02])
        pm = PerformanceMetrics(rets)
        assert pm.omega_ratio() == float("inf")


# ============================================================
# 4. summary / summary_str
# ============================================================


class TestSummary:
    def test_summary_keys(self):
        rets = pd.Series([0.002, -0.001] * 50)
        pm = PerformanceMetrics(rets)
        s = pm.summary()
        for key in (
            "annual_return",
            "annual_vol",
            "max_drawdown",
            "sharpe",
            "sortino",
            "calmar",
            "objective",
            "var_95",
            "cvar_95",
            "win_rate",
            "profit_factor",
            "omega",
            "n_days",
        ):
            assert key in s
        assert s["n_days"] == 100

    def test_summary_str(self):
        rets = pd.Series([0.002, -0.001] * 50)
        pm = PerformanceMetrics(rets)
        s = pm.summary_str()
        assert "Annual Return" in s
        assert "Sharpe" in s
        assert "N Days" in s


# ============================================================
# 5. DeflatedSharpeRatio
# ============================================================


class TestDeflatedSharpeRatio:
    def test_expected_max_sr_n_trials_le_one(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=1.0, n_trials=1, n_observations=100)
        assert dsr.expected_max_sr() == 0.0

    def test_expected_max_sr_normal(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=1.0, n_trials=10, n_observations=100)
        em = dsr.expected_max_sr()
        assert em > 0

    def test_compute_in_range(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=2.0, n_trials=5, n_observations=500)
        val = dsr.compute()
        assert 0.0 <= val <= 1.0

    def test_is_significant(self):
        dsr_good = DeflatedSharpeRatio(
            sharpe_ratio=5.0, n_trials=2, n_observations=1000
        )
        dsr_bad = DeflatedSharpeRatio(
            sharpe_ratio=0.01, n_trials=100, n_observations=100
        )
        assert dsr_good.is_significant(threshold=0.5) is True
        assert dsr_bad.is_significant(threshold=0.99) is False

    def test_summary_keys(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=1.5, n_trials=10, n_observations=500)
        s = dsr.summary()
        for key in (
            "sharpe_ratio",
            "expected_max_sr",
            "dsr",
            "significant",
            "n_trials",
            "n_observations",
        ):
            assert key in s

    def test_skewness_kurtosis_correction(self):
        dsr = DeflatedSharpeRatio(
            sharpe_ratio=1.0,
            n_trials=10,
            n_observations=100,
            skewness=1.0,
            kurtosis=5.0,
        )
        em = dsr.expected_max_sr()
        assert em > 0


# ============================================================
# 6. 模块级便捷函数
# ============================================================


class TestComputeSharpe:
    def test_normal(self):
        rets = pd.Series([0.002, -0.001] * 50)
        s = compute_sharpe(rets, rf=0.0)
        assert math.isfinite(s)

    def test_matches_class(self):
        rets = pd.Series([0.002, -0.001] * 50)
        assert compute_sharpe(rets, rf=0.02) == pytest.approx(
            PerformanceMetrics(rets, rf=0.02).sharpe_ratio()
        )


class TestComputeSortino:
    def test_normal(self):
        rets = pd.Series([0.002, -0.001] * 50)
        s = compute_sortino(rets, rf=0.0, target=0.0)
        assert math.isfinite(s)


class TestComputeCalmar:
    def test_normal(self):
        rets = pd.Series([0.05, -0.10, 0.02] * 50)
        c = compute_calmar(rets, rf=0.0)
        assert math.isfinite(c)


class TestComputeMaxDrawdown:
    def test_empty(self):
        assert compute_max_drawdown(pd.Series([], dtype=float)) == (0.0, 0, 0)

    def test_none(self):
        assert compute_max_drawdown(None) == (0.0, 0, 0)

    def test_single_element(self):
        assert compute_max_drawdown(pd.Series([1.0])) == (0.0, 0, 0)

    def test_with_drawdown(self):
        equity = pd.Series([1.0, 1.1, 0.9, 1.2])
        max_dd, peak, trough = compute_max_drawdown(equity)
        assert max_dd > 0
        assert peak <= trough

    def test_no_drawdown(self):
        equity = pd.Series([1.0, 1.1, 1.2, 1.3])
        max_dd, _peak, _trough = compute_max_drawdown(equity)
        assert max_dd == 0.0


class TestComputeDsr:
    def test_normal(self):
        val = compute_dsr(observed_sr=2.0, n_trials=5, t_obs=500)
        assert 0.0 <= val <= 1.0

    def test_matches_class(self):
        val = compute_dsr(
            observed_sr=1.5, n_trials=10, t_obs=300, skewness=0.5, kurtosis=4.0
        )
        expected = DeflatedSharpeRatio(1.5, 10, 300, 0.5, 4.0).compute()
        assert val == pytest.approx(expected)


class TestComputeAllMetrics:
    def test_keys(self):
        rets = pd.Series([0.002, -0.001] * 50)
        m = compute_all_metrics(rets, rf=0.02)
        for key in (
            "annual_return",
            "annual_vol",
            "sharpe",
            "sortino",
            "calmar",
            "max_drawdown",
            "var_95",
            "cvar_95",
            "win_rate",
            "n_days",
        ):
            assert key in m

    def test_max_drawdown_is_positive(self):
        rets = pd.Series([0.05, -0.10, 0.02] * 50)
        m = compute_all_metrics(rets, rf=0.0)
        assert m["max_drawdown"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
