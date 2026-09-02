"""P3.3 评估脚本单测: 分位数 / 滚动窗口年化 / 四项验收判定."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_eval():
    spec = importlib.util.spec_from_file_location(
        "run_p33_evaluation", _PROJECT_ROOT / "scripts" / "run_p33_evaluation.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_p33_evaluation"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load_eval()


class TestPercentile:
    def test_basic(self, ev):
        vals = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
        assert ev.percentile(vals, 0.0) == 1.0
        assert ev.percentile(vals, 0.5) == 3.0
        assert ev.percentile(vals, 1.0) == 5.0

    def test_interpolation(self, ev):
        vals = sorted([1.0, 2.0, 3.0, 4.0])
        assert ev.percentile(vals, 0.25) == pytest.approx(1.75)
        assert ev.percentile(vals, 0.5) == pytest.approx(2.5)

    def test_empty(self, ev):
        import math
        assert math.isnan(ev.percentile([], 0.5))


class TestRollingAnnualized:
    def test_flat(self, ev):
        """全平 NAV → 年化 0."""
        eq = [1.0] * 40
        wins = ev.rolling_window_annualized(eq, 30)
        assert len(wins) == 11
        assert all(w == pytest.approx(0.0) for w in wins)

    def test_steady_growth(self, ev):
        """日涨 0.1% → 每窗口年化 ≈ 0.001*252 = 25.2% (复利略低)."""
        eq = [1.0]
        for _ in range(35):
            eq.append(eq[-1] * 1.001)
        wins = ev.rolling_window_annualized(eq, 30)
        assert len(wins) == 7  # 36 个 NAV 点 → 7 个窗口
        # 窗口含 30 点 = 29 次增长: 年化 = 1.001^29^(252/30) - 1 ≈ 27.6%
        assert all(0.24 < w < 0.28 for w in wins)

    def test_window_count(self, ev):
        eq = [float(i) + 1.0 for i in range(50)]
        assert len(ev.rolling_window_annualized(eq, 30)) == 21


class TestEvaluateAcceptance:
    def test_all_pass(self, ev):
        checks, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert ok
        assert all(c["pass"] for c in checks.values())

    def test_negative_return_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=-0.01, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_drawdown_limit(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.16,  # > 15%
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_excess_rebalance_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=3,  # > 2
            max_single_turnover=0.2,
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_extreme_single_turnover_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.8,  # > 50%
            shadow_ann=0.08, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_outside_dist_band_fails(self, ev):
        _, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=0.50,  # 远超 P95=25%
            dist_low=-0.05, dist_high=0.25,
        )
        assert not ok

    def test_none_ann_fails_dist_check(self, ev):
        checks, ok = ev.evaluate_acceptance(
            total_return=0.02, max_drawdown=0.03,
            rebal_count=1, max_single_turnover=0.2,
            shadow_ann=None, dist_low=-0.05, dist_high=0.25,
        )
        assert not ok
        assert not checks["backtest_consistent"]["pass"]
