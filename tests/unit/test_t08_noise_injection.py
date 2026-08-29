"""T08: Noise Injection 稳定性测试单元测试."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.noise_injection_test import (  # noqa: E402
    NoiseInjectionResult,
    noise_injection_summary,
    run_noise_injection_test,
)


class TestNoiseInjectionBasic:
    def test_returns_valid_result(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        result = run_noise_injection_test(rets, n_trials=100, random_seed=42)
        assert isinstance(result, NoiseInjectionResult)
        assert result.n_trials == 100
        assert result.noise_ratio == 0.1

    def test_original_sharpe_computed(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        result = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        assert result.original_sharpe != 0

    def test_insufficient_samples(self):
        result = run_noise_injection_test([0.01] * 10, n_trials=10)
        assert result.n_trials == 0
        assert "样本不足" in result.verdict


class TestStabilityDetection:
    def test_stable_strategy_passes(self):
        """稳定策略 (高 SR, 大样本) 应通过."""
        np.random.seed(42)
        # SR ≈ 2.0, 5年数据
        rets = np.random.normal(0.0015, 0.012, 1260).tolist()
        result = run_noise_injection_test(
            rets, noise_ratio=0.1, n_trials=200, random_seed=42
        )
        assert result.is_stable, f"稳定策略应通过: {result.verdict}"
        assert result.pct_positive > 0.95

    def test_fragile_strategy_fails(self):
        """脆弱策略 (低 SR, 小样本) 应失败."""
        np.random.seed(42)
        # SR ≈ 0.1, 半年数据
        rets = np.random.normal(0.0001, 0.02, 126).tolist()
        result = run_noise_injection_test(
            rets, noise_ratio=0.5, n_trials=200, random_seed=42
        )
        assert not result.is_stable

    def test_high_noise_ratio_reduces_stability(self):
        """噪音比例越高, 稳定性越差."""
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 504).tolist()
        r_low = run_noise_injection_test(
            rets, noise_ratio=0.05, n_trials=100, random_seed=42
        )
        r_high = run_noise_injection_test(
            rets, noise_ratio=0.5, n_trials=100, random_seed=42
        )
        # 高噪音下 P5 应更低
        assert r_high.sharpe_p5 < r_low.sharpe_p5

    def test_reproducible_with_seed(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        r1 = run_noise_injection_test(rets, n_trials=50, random_seed=123)
        r2 = run_noise_injection_test(rets, n_trials=50, random_seed=123)
        assert r1.sharpe_mean == r2.sharpe_mean


class TestStatistics:
    def test_quantile_ordering(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        r = run_noise_injection_test(rets, n_trials=100, random_seed=42)
        assert r.sharpe_p5 <= r.sharpe_median <= r.sharpe_p95
        assert r.sharpe_min <= r.sharpe_p5
        assert r.sharpe_p95 <= r.sharpe_max

    def test_pct_positive_range(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        r = run_noise_injection_test(rets, n_trials=100, random_seed=42)
        assert 0.0 <= r.pct_positive <= 1.0
        assert 0.0 <= r.pct_above_half_original <= 1.0

    def test_cv_non_negative(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        r = run_noise_injection_test(rets, n_trials=100, random_seed=42)
        assert r.sharpe_cv >= 0


class TestSummary:
    def test_summary_contains_key_info(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        r = run_noise_injection_test(rets, n_trials=50, random_seed=42)
        s = noise_injection_summary(r)
        assert "Noise Injection" in s
        assert "Sharpe" in s
        assert "通过" in s or "未通过" in s


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
