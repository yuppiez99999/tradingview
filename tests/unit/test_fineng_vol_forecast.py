"""utils.fineng.vol_forecast 单元测试 — GARCH / EWMA / 对比 / half_life"""
from __future__ import annotations

import math
import random

import pytest

from utils.fineng.vol_forecast import (
    VolComparisonReport,
    ewma_vol,
    fit_garch,
    forecast_vol,
    generate_comparison,
)


def _synthetic_returns(n: int = 500, mu: float = 0.0, sigma: float = 0.02,
                       seed: int = 42) -> list:
    """日对数收益率序列 (已知年化波动 ≈ sigma*sqrt(252))"""
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def test_fit_garch_stationary():
    """fit_garch: 返回 GARCHResult, persistence<1 平稳, 年化波动合理"""
    r = _synthetic_returns()
    res = fit_garch(r)
    assert res.converged is True
    assert 0.0 <= res.alpha < 1.0
    assert 0.0 <= res.beta < 1.0
    assert res.persistence < 1.0              # 平稳性
    assert res.omega >= 0.0
    # 年化波动与真实日波动同量级 (sigma=0.02 → 年化≈0.317)
    assert res.forecast_vol == pytest.approx(0.317, abs=5e-2)


def test_garch_result_half_life():
    """GARCHResult.half_life_days = ln(0.5)/ln(persistence)"""
    res = fit_garch(_synthetic_returns())
    expected = math.log(0.5) / math.log(res.persistence)
    assert res.half_life_days == pytest.approx(expected, abs=1e-9)


def test_forecast_vol_positive():
    """forecast_vol: 多步预测返回正波动率"""
    r = _synthetic_returns()
    res = fit_garch(r)
    # 取最后一步的 eps^2 与 sigma^2 (用 conditional_vol 近似)
    prev_eps2 = (r[-1] - (sum(r[:-1]) / len(r[:-1]))) ** 2 if len(r) > 1 else r[-1] ** 2
    prev_sigma2 = (res.conditional_vol[-1] / math.sqrt(252)) ** 2
    fwd = forecast_vol(prev_eps2, prev_sigma2, res.omega,
                       res.alpha, res.beta, steps=10)
    assert fwd > 0.0


def test_ewma_vol_returns_tuple():
    """ewma_vol: 返回 (最新年化波动, 序列); 随机序列波动 > 0"""
    const = [0.02] * 100
    vol_const, series = ewma_vol(const, lambda_=0.94)
    assert isinstance(series, list)
    # 随机序列 EWMA 波动 > 0
    r = _synthetic_returns()
    vol_rand, _ = ewma_vol(r, lambda_=0.94)
    assert vol_rand > 0.0
    assert vol_const >= 0.0


def test_generate_comparison_keys():
    """generate_comparison: 返回 VolComparisonReport (garch_vol / ewma_vol 字段)"""
    r = _synthetic_returns()
    cmp = generate_comparison(r)
    assert isinstance(cmp, VolComparisonReport)
    assert cmp.garch_vol > 0.0
    assert cmp.ewma_vol > 0.0
    # 年化波动接近 (日波动 2% → 年化约 31.7%)
    assert cmp.garch_vol == pytest.approx(0.317, abs=5e-2)


def test_fit_garch_min_history_rejects_short():
    """样本量不足 min_history → converged=False (fail-closed)"""
    short = _synthetic_returns(n=50)
    res = fit_garch(short, min_history=250)
    assert res.converged is False
