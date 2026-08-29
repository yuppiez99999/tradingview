"""MVSK 高阶矩优化单元测试 — P1: YAND 启发的偏度/峰度优化.

被测模块: utils/risk_budget_optimizer.py (MVSK 扩展)
覆盖目标:
- 不传 return_matrix 时退化为纯 MV (向后兼容)
- 传 return_matrix + γ>0 时启用 MVSK, 诊断字段被填充
- 偏度厌恶改善组合偏度 (MVSK skew >= MV skew)
- 峰度厌恶改善组合峰度 (MVSK exkurt <= MV exkurt)
- return_matrix 列数不匹配抛 ValueError
- γ_s=0 且 γ_k=0 时即使传 return_matrix 也不启用
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_budget_optimizer import RiskBudgetOptimizer  # noqa: E402

# ============================================================
# 测试数据构造
# ============================================================


def _make_skewed_returns(
    n_assets: int = 10, n_days: int = 252, seed: int = 7
) -> np.ndarray:
    """生成含负偏度+肥尾的收益矩阵 (模拟 A 股涨跌停截断).

    混合正态: 85% N(μ, σ²) + 15% N(μ-3σ, (2σ)²) → 负偏度 + 超额峰度.
    """
    rng = np.random.RandomState(seed)
    dt = 1.0 / 252
    vol_d = 0.30 * math.sqrt(dt)
    mu_d = 0.10 * dt

    R = np.zeros((n_days, n_assets))  # noqa: N806
    for t in range(n_days):
        regime = rng.rand(n_assets) < 0.15
        normal = mu_d + vol_d * rng.randn(n_assets)
        crash = mu_d - 3.0 * vol_d + 2.0 * vol_d * rng.randn(n_assets)
        R[t] = np.where(regime, crash, normal)
    return R


def _make_cov(n: int, seed: int = 0) -> np.ndarray:
    """正定协方差矩阵 (年化)."""
    rng = np.random.RandomState(seed)
    A = rng.randn(n, n) * 0.05 + np.eye(n) * 0.15  # noqa: N806
    return A @ A.T


def _portfolio_skew(returns: np.ndarray, w: np.ndarray) -> float:
    rp = returns @ w
    m1 = rp.mean()
    std = rp.std()
    return float(((rp - m1) ** 3).mean() / std**3) if std > 0 else 0.0


def _portfolio_exkurt(returns: np.ndarray, w: np.ndarray) -> float:
    rp = returns @ w
    m1 = rp.mean()
    std = rp.std()
    return float(((rp - m1) ** 4).mean() / std**4 - 3.0) if std > 0 else 0.0


# ============================================================
# 向后兼容测试
# ============================================================


class TestMVSKBackwardCompat:
    def test_no_return_matrix_degrades_to_mv(self):
        """不传 return_matrix 时 mvsk_enabled=False, 行为同原 MV."""
        n = 5
        cov = _make_cov(n, seed=1)
        mu = np.array([0.12, 0.10, 0.08, 0.15, 0.09])
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        result = opt.optimize(
            symbols=[f"a{i}" for i in range(n)],
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.05,
            max_weight=0.40,
        )

        assert result.mvsk_enabled is False
        assert result.portfolio_skewness == 0.0
        assert result.portfolio_excess_kurtosis == 0.0
        assert abs(result.optimal_weights.sum() - 1.0) < 1e-6

    def test_zero_aversion_disables_mvsk(self):
        """γ_s=0 且 γ_k=0 时即使传 return_matrix 也不启用."""
        n = 5
        R = _make_skewed_returns(n, 252)  # noqa: N806
        cov = np.cov(R, rowvar=False) * 252
        mu = R.mean(axis=0) * 252
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        result = opt.optimize(
            symbols=[f"a{i}" for i in range(n)],
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.05,
            max_weight=0.40,
            return_matrix=R,
            skew_aversion=0.0,
            kurtosis_aversion=0.0,
        )

        assert result.mvsk_enabled is False


# ============================================================
# MVSK 启用与诊断字段测试
# ============================================================


class TestMVSKEnabled:
    def test_mvsk_enabled_fills_diagnostics(self):
        """传 return_matrix + γ>0 时 mvsk_enabled=True, 偏度/峰度被计算."""
        n = 8
        R = _make_skewed_returns(n, 252, seed=11)  # noqa: N806
        cov = np.cov(R, rowvar=False) * 252
        mu = R.mean(axis=0) * 252
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        result = opt.optimize(
            symbols=[f"a{i}" for i in range(n)],
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.30,
            return_matrix=R,
            skew_aversion=1.0,
            kurtosis_aversion=0.3,
        )

        assert result.mvsk_enabled is True
        # 诊断字段应被填充为非零 (合成数据有偏度/峰度)
        assert result.portfolio_skewness != 0.0
        assert result.portfolio_excess_kurtosis != 0.0
        # 权重合法
        assert abs(result.optimal_weights.sum() - 1.0) < 1e-6
        assert np.all(result.optimal_weights >= -1e-6)

    def test_diagnostics_match_direct_computation(self):
        """诊断字段与外部直接计算一致."""
        n = 6
        R = _make_skewed_returns(n, 252, seed=23)  # noqa: N806
        cov = np.cov(R, rowvar=False) * 252
        mu = R.mean(axis=0) * 252
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        result = opt.optimize(
            symbols=[f"a{i}" for i in range(n)],
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.35,
            return_matrix=R,
            skew_aversion=0.8,
            kurtosis_aversion=0.2,
        )

        w = result.optimal_weights
        assert abs(result.portfolio_skewness - _portfolio_skew(R, w)) < 1e-8
        assert abs(result.portfolio_excess_kurtosis - _portfolio_exkurt(R, w)) < 1e-8


# ============================================================
# 高阶矩优化效果测试
# ============================================================


class TestMVSKEffect:
    def test_skew_aversion_improves_skew(self):
        """偏度厌恶应使 MVSK 组合偏度 >= MV 组合偏度 (鼓励正偏度/抑制负偏度)."""
        n = 12
        R = _make_skewed_returns(n, 504, seed=99)  # noqa: N806
        cov = np.cov(R, rowvar=False) * 252
        mu = R.mean(axis=0) * 252
        w_bench = np.ones(n) / n
        symbols = [f"a{i}" for i in range(n)]
        opt = RiskBudgetOptimizer(risk_aversion=2.5)

        result_mv = opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.25,
        )
        result_mvsk = opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.25,
            return_matrix=R,
            skew_aversion=2.0,
            kurtosis_aversion=0.0,
        )

        # 用外部函数算 MV 权重的实际偏度 (result_mv.portfolio_skewness=0 因未启用 MVSK)
        mv_skew = _portfolio_skew(R, result_mv.optimal_weights)
        mvsk_skew = result_mvsk.portfolio_skewness
        # MVSK 偏度应不差于 MV (允许数值噪声, 给 1e-3 容差)
        assert (
            mvsk_skew >= mv_skew - 1e-3
        ), f"MVSK 偏度 {mvsk_skew:.4f} < MV 偏度 {mv_skew:.4f}"

    def test_kurtosis_aversion_improves_kurtosis(self):
        """峰度厌恶应使 MVSK 组合超额峰度 <= MV 组合超额峰度."""
        n = 12
        R = _make_skewed_returns(n, 504, seed=99)  # noqa: N806
        cov = np.cov(R, rowvar=False) * 252
        mu = R.mean(axis=0) * 252
        w_bench = np.ones(n) / n
        symbols = [f"a{i}" for i in range(n)]
        opt = RiskBudgetOptimizer(risk_aversion=2.5)

        result_mv = opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.25,
        )
        result_mvsk = opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.06,
            max_weight=0.25,
            return_matrix=R,
            skew_aversion=0.0,
            kurtosis_aversion=1.0,
        )

        mv_exkurt = _portfolio_exkurt(R, result_mv.optimal_weights)
        mvsk_exkurt = result_mvsk.portfolio_excess_kurtosis
        # MVSK 峰度应不差于 MV
        assert (
            mvsk_exkurt <= mv_exkurt + 1e-3
        ), f"MVSK 峰度 {mvsk_exkurt:.4f} > MV 峰度 {mv_exkurt:.4f}"


# ============================================================
# 异常处理测试
# ============================================================


class TestMVSKErrors:
    def test_return_matrix_wrong_columns_raises(self):
        """return_matrix 列数 != 资产数时抛 ValueError."""
        n = 5
        R = _make_skewed_returns(n + 2, 252)  # noqa: N806  # 列数故意不匹配
        cov = _make_cov(n)
        mu = np.full(n, 0.10)
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        with pytest.raises(ValueError, match="return_matrix 列数"):
            opt.optimize(
                symbols=[f"a{i}" for i in range(n)],
                expected_returns=mu,
                cov_matrix=cov,
                benchmark_weights=w_bench,
                max_tracking_error=0.05,
                return_matrix=R,
                skew_aversion=1.0,
            )

    def test_mvsk_slsqp_failure_fails_open(self):
        """SLSQP 求解失败时 fail-open 回退到投影梯度/缩放法, 不抛异常."""
        n = 4
        R = _make_skewed_returns(n, 252, seed=3)  # noqa: N806
        cov = _make_cov(n)
        mu = np.array([0.20, 0.15, 0.10, 0.05])
        w_bench = np.ones(n) / n

        opt = RiskBudgetOptimizer(risk_aversion=2.5)
        result = opt.optimize(
            symbols=[f"a{i}" for i in range(n)],
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=w_bench,
            max_tracking_error=0.10,
            max_weight=0.50,
            return_matrix=R,
            skew_aversion=1.0,
            kurtosis_aversion=0.5,
        )

        # 不抛异常, 权重合法
        assert abs(result.optimal_weights.sum() - 1.0) < 1e-6
        assert result.mvsk_enabled is True
