"""LedoitWolfCovariance 单元测试.

被测模块: utils/ledoit_wolf_covariance.py
覆盖目标: >=85%
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.ledoit_wolf_covariance import (
    LedoitWolfCovariance,
    ShrinkageResult,
)  # noqa: E402


class LedoitWolfCovarianceTest:
    """LedoitWolfCovariance 单元测试."""

    # ------ __init__ ------
    def test_init_defaults(self):
        est = LedoitWolfCovariance()
        assert est.assume_zero_mean is False
        assert est.annualize is False
        assert est.periods_per_year == 252

    def test_init_custom(self):
        est = LedoitWolfCovariance(
            assume_zero_mean=True, annualize=True, periods_per_year=365
        )
        assert est.assume_zero_mean is True
        assert est.annualize is True
        assert est.periods_per_year == 365

    # ------ fit 正常 ------
    def test_fit_normal_matrix(self):
        rng = np.random.default_rng(42)
        R = rng.normal(0, 0.01, size=(100, 3))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert isinstance(result, ShrinkageResult)
        assert result.cov_shrunk.shape == (3, 3)
        assert result.sample_cov.shape == (3, 3)
        assert result.target.shape == (3, 3)
        assert result.n_observations == 100
        assert result.n_assets == 3
        assert result.method == "ledoit-wolf-constant-correlation"
        # 对称
        assert np.allclose(result.cov_shrunk, result.cov_shrunk.T)
        # 收缩强度 [0,1]
        assert 0.0 <= result.shrinkage_intensity <= 1.0

    def test_fit_dataframe_input(self):
        rng = np.random.default_rng(7)
        R = rng.normal(0, 0.01, size=(50, 2))
        df = pd.DataFrame(R, columns=["A", "B"])
        est = LedoitWolfCovariance()
        result = est.fit(df)
        assert result.cov_shrunk.shape == (2, 2)
        assert result.n_assets == 2

    def test_fit_assume_zero_mean(self):
        rng = np.random.default_rng(11)
        R = rng.normal(0.05, 0.01, size=(80, 2))  # 非零均值
        est1 = LedoitWolfCovariance(assume_zero_mean=False)
        est2 = LedoitWolfCovariance(assume_zero_mean=True)
        r1 = est1.fit(R)
        r2 = est2.fit(R)
        # 均值不同则协方差不同
        assert not np.allclose(r1.sample_cov, r2.sample_cov)

    def test_fit_annualize(self):
        rng = np.random.default_rng(13)
        R = rng.normal(0, 0.01, size=(100, 2))
        est1 = LedoitWolfCovariance(annualize=False)
        est2 = LedoitWolfCovariance(annualize=True, periods_per_year=252)
        r1 = est1.fit(R)
        r2 = est2.fit(R)
        # 年化后协方差 ≈ 原始 × 252
        assert np.allclose(r2.cov_shrunk, r1.cov_shrunk * 252)
        assert np.allclose(r2.sample_cov, r1.sample_cov * 252)

    # ------ fit 单资产 ------
    def test_fit_single_asset(self):
        rng = np.random.default_rng(17)
        R = rng.normal(0, 0.01, size=(50, 1))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert result.n_assets == 1
        assert result.condition_number_before == 1.0
        assert result.condition_number_after == 1.0
        assert result.avg_correlation == 0.0
        assert result.cov_shrunk.shape == (1, 1)

    # ------ fit 异常 ------
    def test_fit_insufficient_observations_raises(self):
        R = np.array([[0.01, 0.02]])  # T=1
        est = LedoitWolfCovariance()
        with pytest.raises(ValueError, match="样本不足"):
            est.fit(R)

    def test_fit_zero_assets_raises(self):
        R = np.empty((10, 0))  # N=0
        est = LedoitWolfCovariance()
        with pytest.raises(ValueError, match="样本不足"):
            est.fit(R)

    def test_fit_non_2d_raises(self):
        R = np.array([0.01, 0.02, 0.03])  # 1D
        est = LedoitWolfCovariance()
        with pytest.raises(ValueError, match="2D"):
            est.fit(R)

    # ------ T <= N 分支 (corrcoef 用 eye) ------
    def test_fit_t_less_than_n(self):
        rng = np.random.default_rng(61)
        R = rng.normal(0, 0.01, size=(3, 5))  # T=3 < N=5
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert result.n_observations == 3
        assert result.n_assets == 5
        # T<=N 时用 eye(N) 计算 avg_corr → 0.0
        assert isinstance(result.avg_correlation, float)

    # ------ _build_constant_correlation_target ------
    def test_build_constant_correlation_target(self):
        est = LedoitWolfCovariance()
        S = np.array([[0.04, 0.01], [0.01, 0.09]])
        F = est._build_constant_correlation_target(S)
        # 对角线保持
        assert np.isclose(F[0, 0], 0.04)
        assert np.isclose(F[1, 1], 0.09)
        # off-diagonal = rho_bar * sqrt(s_ii * s_jj)
        std = np.sqrt([0.04, 0.09])
        corr = S / np.outer(std, std)
        rho_bar = corr[0, 1]  # 2x2 只有一个 off-diagonal
        expected_off = rho_bar * std[0] * std[1]
        assert np.isclose(F[0, 1], expected_off)
        assert np.isclose(F[1, 0], expected_off)

    def test_build_constant_correlation_target_single_asset(self):
        est = LedoitWolfCovariance()
        S = np.array([[0.04]])
        F = est._build_constant_correlation_target(S)
        assert F.shape == (1, 1)
        assert np.isclose(F[0, 0], 0.04)

    # ------ _estimate_shrinkage_intensity ------
    def test_shrinkage_intensity_in_bounds(self):
        rng = np.random.default_rng(23)
        R = rng.normal(0, 0.01, size=(30, 4))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert 0.0 <= result.shrinkage_intensity <= 1.0

    def test_estimate_shrinkage_intensity_small_t_returns_default(self):
        est = LedoitWolfCovariance()
        Rc = np.zeros((1, 2))
        S = np.eye(2)
        F = np.eye(2)
        # T=1 < 2 → 返回 0.5
        delta = est._estimate_shrinkage_intensity(Rc, S, F, 1)
        assert delta == 0.5

    # ------ fit_predict ------
    def test_fit_predict_returns_cov(self):
        rng = np.random.default_rng(29)
        R = rng.normal(0, 0.01, size=(60, 3))
        est = LedoitWolfCovariance()
        cov = est.fit_predict(R)
        result = est.fit(R)
        assert np.allclose(cov, result.cov_shrunk)

    def test_fit_predict_dataframe(self):
        rng = np.random.default_rng(31)
        R = rng.normal(0, 0.01, size=(40, 2))
        df = pd.DataFrame(R, columns=["A", "B"])
        est = LedoitWolfCovariance()
        cov = est.fit_predict(df)
        assert cov.shape == (2, 2)

    # ------ fit_with_uncertainty ------
    def test_fit_with_uncertainty_bootstrap(self):
        rng = np.random.default_rng(31)
        R = rng.normal(0, 0.01, size=(50, 2))
        est = LedoitWolfCovariance()
        cov, std = est.fit_with_uncertainty(R, n_bootstrap=20)
        assert cov.shape == (2, 2)
        assert std.shape == (2, 2)
        # 自助法后 std 应非负
        assert np.all(std >= 0)

    def test_fit_with_uncertainty_small_sample_returns_zeros(self):
        rng = np.random.default_rng(37)
        R = rng.normal(0, 0.01, size=(8, 2))  # T=8 <= 10
        est = LedoitWolfCovariance()
        cov, std = est.fit_with_uncertainty(R, n_bootstrap=10)
        assert cov.shape == (2, 2)
        assert np.all(std == 0.0)

    def test_fit_with_uncertainty_zero_bootstrap_returns_zeros(self):
        rng = np.random.default_rng(41)
        R = rng.normal(0, 0.01, size=(50, 2))
        est = LedoitWolfCovariance()
        cov, std = est.fit_with_uncertainty(R, n_bootstrap=0)
        assert np.all(std == 0.0)

    def test_fit_with_uncertainty_dataframe(self):
        rng = np.random.default_rng(43)
        R = rng.normal(0, 0.01, size=(40, 2))
        df = pd.DataFrame(R, columns=["A", "B"])
        est = LedoitWolfCovariance()
        cov, std = est.fit_with_uncertainty(df, n_bootstrap=10)
        assert cov.shape == (2, 2)

    # ------ 条件数改善 ------
    def test_condition_number_improvement(self):
        rng = np.random.default_rng(47)
        # 构造高度相关的近似奇异矩阵
        x = rng.normal(0, 0.01, size=80)
        R = np.column_stack([x, x + 0.001 * rng.normal(0, 1, size=80)])
        est = LedoitWolfCovariance()
        result = est.fit(R)
        # 收缩后条件数应改善（更小）
        assert result.condition_number_after <= result.condition_number_before
        assert result.condition_number_after > 0

    # ------ 常量相关性目标对称性 ------
    def test_target_symmetric(self):
        rng = np.random.default_rng(53)
        R = rng.normal(0, 0.01, size=(40, 4))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert np.allclose(result.target, result.target.T)

    # ------ avg_correlation ------
    def test_avg_correlation_uncorrelated(self):
        rng = np.random.default_rng(59)
        # 独立正态 → 相关性接近 0
        R = rng.normal(0, 0.01, size=(200, 3))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        assert abs(result.avg_correlation) < 0.3

    # ------ 正定性 ------
    def test_cov_shrunk_positive_definite(self):
        rng = np.random.default_rng(67)
        R = rng.normal(0, 0.01, size=(50, 3))
        est = LedoitWolfCovariance()
        result = est.fit(R)
        # 收缩后协方差应正定 → 特征值全正
        eigs = np.linalg.eigvalsh(result.cov_shrunk)
        assert np.all(eigs > 0)
