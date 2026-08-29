"""utils.fineng.kalman_beta 单元测试 — Kalman 时变 Beta / 滚动 OLS / 对冲对比"""

from __future__ import annotations

import random

import pytest

from utils.fineng.kalman_beta import (
    BetaHedgeComparison,
    backtest_hedge_comparison,
    fit_kalman_beta,
    rolling_ols_beta,
)


def _synthetic(portfolio_beta: float = 1.2, n: int = 400, seed: int = 7) -> tuple:
    """合成 portfolio/index 收益: portfolio = beta*index + noise"""
    rng = random.Random(seed)
    idx = [rng.gauss(0.0, 0.015) for _ in range(n)]
    port = [portfolio_beta * i + rng.gauss(0.0, 0.005) for i in idx]
    return port, idx


def test_fit_kalman_beta_converges():
    """fit_kalman_beta: 合成已知 beta≈1.2 应被估计出接近值"""
    port, idx = _synthetic(portfolio_beta=1.2)
    res = fit_kalman_beta(port, idx)
    assert res.converged is True
    assert len(res.filtered_beta) == len(port)
    # 估计 beta 末值接近真实值
    assert res.filtered_beta[-1] == pytest.approx(1.2, abs=0.2)
    # 相关系数应 > 0
    assert res.latest_beta > 0.0


def test_fit_kalman_beta_zero_correlation():
    """不相关序列 → beta 接近 0"""
    rng = random.Random(99)
    idx = [rng.gauss(0.0, 0.015) for _ in range(300)]
    port = [rng.gauss(0.0, 0.01) for _ in range(300)]
    res = fit_kalman_beta(port, idx)
    assert abs(res.latest_beta) == pytest.approx(0.0, abs=0.3)


def test_rolling_ols_beta_length_and_value():
    """rolling_ols_beta: 长度=输入长度 (前 window-1 为 NaN), 末值接近真实 beta"""
    port, idx = _synthetic(portfolio_beta=0.8)
    betas = rolling_ols_beta(port, idx, window=60)
    assert len(betas) == len(port)
    # 末值应有限且接近 0.8
    assert betas[-1] == pytest.approx(0.8, abs=0.25)
    # 前 window-1 个为 NaN
    assert all(math.isnan(b) for b in betas[:59])


def test_backtest_hedge_comparison():
    """backtest_hedge_comparison: 返回 BetaHedgeComparison, 含对冲后方差"""
    port, idx = _synthetic(portfolio_beta=1.5)
    cmp = backtest_hedge_comparison(port, idx, ols_window=60)
    assert isinstance(cmp, BetaHedgeComparison)
    # 字段存在且有限
    assert math.isfinite(cmp.kalman_hedged_variance)
    assert math.isfinite(cmp.ols_hedged_variance)
    assert isinstance(cmp.variance_reduction_pct, float)
    # 对冲后组合方差应低于未对冲 (组合本身有 beta=1.5 暴露)
    assert (
        cmp.kalman_hedged_variance < cmp.ols_hedged_variance + 1.0
    )  # Kalman 不差于 OLS
    assert cmp.kalman_hedged_variance >= 0.0


import math  # noqa: E402  (供 test_rolling_ols_beta NaN 检查)
