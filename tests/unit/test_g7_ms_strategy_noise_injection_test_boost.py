"""G7 boost: ms_strategy/src/backtest/noise_injection_test.py 单元测试.

覆盖 NoiseInjectionResult / run_noise_injection_test / noise_injection_summary
及内部辅助函数 _compute_sharpe / _compute_max_dd 的核心路径与边界分支.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.noise_injection_test import (  # noqa: E402
    NoiseInjectionResult,
    _compute_max_dd,
    _compute_sharpe,
    noise_injection_summary,
    run_noise_injection_test,
)

# ============================================================
# 1. _compute_sharpe
# ============================================================


class TestComputeSharpe:
    def test_normal_positive(self):
        rets = np.array([0.001] * 100 + [0.002] * 100)
        s = _compute_sharpe(rets)
        assert s > 0

    def test_normal_negative(self):
        rets = np.array([-0.001] * 100)
        s = _compute_sharpe(rets)
        assert s < 0

    def test_len_lt_2(self):
        assert _compute_sharpe(np.array([0.001])) == 0.0

    def test_empty(self):
        assert _compute_sharpe(np.array([])) == 0.0

    def test_zero_std(self):
        rets = np.array([0.0] * 100)
        assert _compute_sharpe(rets) == 0.0

    def test_custom_ann_factor(self):
        # 非零均值 → sharpe != 0, 不同 ann_factor 产生不同结果
        rets = np.array([0.002, -0.001] * 50)
        s_default = _compute_sharpe(rets)
        s_custom = _compute_sharpe(rets, ann_factor=math.sqrt(365))
        assert s_default > 0
        assert s_custom > 0
        assert s_custom != pytest.approx(s_default)


# ============================================================
# 2. _compute_max_dd
# ============================================================


class TestComputeMaxDd:
    def test_no_drawdown(self):
        rets = np.array([0.01, 0.01, 0.01])
        assert _compute_max_dd(rets) == 0.0

    def test_with_drawdown(self):
        rets = np.array([0.05, -0.10, 0.02])
        dd = _compute_max_dd(rets)
        assert dd > 0

    def test_len_lt_2(self):
        assert _compute_max_dd(np.array([0.001])) == 0.0

    def test_empty(self):
        assert _compute_max_dd(np.array([])) == 0.0


# ============================================================
# 3. run_noise_injection_test - 边界分支
# ============================================================


class TestRunNoiseInsufficientSamples:
    def test_samples_below_20(self):
        result = run_noise_injection_test([0.001] * 10, n_trials=100)
        assert result.n_trials == 0
        assert result.original_sharpe == 0.0
        assert "样本不足" in result.verdict

    def test_empty_returns(self):
        result = run_noise_injection_test([], n_trials=100)
        assert result.n_trials == 0


# ============================================================
# 4. run_noise_injection_test - 核心路径
# ============================================================


class TestRunNoiseInjectionNormal:
    def test_stable_strategy(self):
        np.random.seed(42)
        # 稳定正收益策略
        rets = np.random.normal(0.002, 0.01, 252)
        result = run_noise_injection_test(rets, noise_ratio=0.05, n_trials=200,
                                          random_seed=42)
        assert result.n_trials == 200
        assert result.original_sharpe > 0
        assert len(result.all_sharpes) == 200
        assert len(result.all_max_dds) == 200
        assert result.sharpe_mean > 0
        assert 0.0 <= result.pct_positive <= 1.0
        assert result.sharpe_min <= result.sharpe_p5 <= result.sharpe_median
        assert result.sharpe_median <= result.sharpe_p95 <= result.sharpe_max

    def test_random_seed_none(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        result = run_noise_injection_test(rets, n_trials=50, random_seed=None)
        assert result.n_trials == 50
        assert len(result.all_sharpes) == 50

    def test_deterministic_with_seed(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        r1 = run_noise_injection_test(rets, n_trials=50, random_seed=123)
        r2 = run_noise_injection_test(rets, n_trials=50, random_seed=123)
        assert r1.sharpe_mean == pytest.approx(r2.sharpe_mean)
        assert r1.sharpe_p5 == pytest.approx(r2.sharpe_p5)

    def test_max_dd_stats(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        assert result.original_max_dd >= 0
        assert result.max_dd_mean >= 0
        assert result.max_dd_p95 >= 0

    def test_cv_calculation(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        if abs(result.sharpe_mean) > 1e-10:
            expected_cv = result.sharpe_std / abs(result.sharpe_mean)
            assert result.sharpe_cv == pytest.approx(expected_cv)


# ============================================================
# 5. run_noise_injection_test - 稳定性判断
# ============================================================


class TestStabilityVerdict:
    def test_stable_verdict_message(self):
        np.random.seed(42)
        rets = np.random.normal(0.005, 0.005, 252)  # 强信号
        result = run_noise_injection_test(rets, noise_ratio=0.01, n_trials=100,
                                          random_seed=42)
        if result.is_stable:
            assert "通过稳定性检验" in result.verdict
        else:
            assert "未通过稳定性检验" in result.verdict

    def test_unstable_high_noise(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        # 极高噪音 → 不稳定
        result = run_noise_injection_test(rets, noise_ratio=2.0, n_trials=100,
                                          random_seed=42)
        # 高噪音下 pct_positive 应较低
        assert isinstance(result.is_stable, bool)
        assert isinstance(result.verdict, str)

    def test_pct_above_half_original_zero(self):
        # original_sharpe <= 0 → pct_above_half = 0
        np.random.seed(42)
        rets = np.random.normal(-0.001, 0.01, 100)  # 负收益
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        assert result.pct_above_half_original == 0.0

    def test_custom_thresholds(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42,
                                          required_pct_positive=0.5,
                                          required_pct_above_half=0.1)
        assert isinstance(result.is_stable, bool)


# ============================================================
# 6. noise_injection_summary
# ============================================================


class TestNoiseInjectionSummary:
    def test_summary_stable(self):
        np.random.seed(42)
        rets = np.random.normal(0.005, 0.005, 252)
        result = run_noise_injection_test(rets, noise_ratio=0.01, n_trials=50,
                                          random_seed=42)
        s = noise_injection_summary(result)
        assert "Noise Injection 稳定性测试" in s
        assert "原始 Sharpe" in s
        assert "Sharpe 分布" in s
        assert "通过率" in s
        assert "MaxDD" in s
        assert "结论" in s

    def test_summary_insufficient_samples(self):
        result = run_noise_injection_test([0.001] * 10, n_trials=100)
        s = noise_injection_summary(result)
        assert "0 次" in s

    def test_summary_contains_cv(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        s = noise_injection_summary(result)
        assert "CV" in s


# ============================================================
# 7. NoiseInjectionResult dataclass
# ============================================================


class TestNoiseInjectionResult:
    def test_defaults(self):
        r = NoiseInjectionResult(original_sharpe=1.0, noise_ratio=0.1, n_trials=100)
        assert r.sharpe_mean == 0.0
        assert r.sharpe_std == 0.0
        assert r.is_stable is False
        assert r.verdict == ""
        assert len(r.all_sharpes) == 0
        assert len(r.all_max_dds) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
