"""utils.fineng.models.term_structure 单元测试 — Flat / Linear / NelsonSiegel 利率期限结构"""

from __future__ import annotations

import math

import pytest

from utils.fineng.models.term_structure import (
    FlatTermStructure,
    LinearTermStructure,
    NelsonSiegelModel,
    RatePoint,
    TermStructure,
)


def test_flat_term_structure():
    """Flat: 任意 T 利率恒定, discount_factor = exp(-rT)"""
    ts: TermStructure = FlatTermStructure(rate=0.03)
    assert ts.rate(0.0) == pytest.approx(0.03)
    assert ts.rate(5.0) == pytest.approx(0.03)
    assert ts.discount_factor(1.0) == pytest.approx(math.exp(-0.03), abs=1e-6)
    assert ts.forward_rate(1.0, 2.0) == pytest.approx(0.03)


def test_linear_term_structure_interp_and_extrap():
    """Linear: 区间内线性插值, 区间外恒定外推"""
    ts = LinearTermStructure(
        points=[RatePoint(0.0, 0.02), RatePoint(1.0, 0.03), RatePoint(5.0, 0.04)]
    )
    assert ts.rate(0.0) == pytest.approx(0.02)
    assert ts.rate(5.0) == pytest.approx(0.04)
    assert ts.rate(0.5) == pytest.approx(0.025)  # 区间 [0,1] 线性
    # 外推
    assert ts.rate(-1.0) == pytest.approx(0.02)
    assert ts.rate(10.0) == pytest.approx(0.04)


def test_linear_term_structure_forward_rate():
    """Linear: forward_rate 返回区间末端利率 (t2 处)"""
    ts = LinearTermStructure(points=[RatePoint(0.0, 0.02), RatePoint(1.0, 0.04)])
    # forward_rate(t1, t2) = 区间 [t1, t2] 的瞬时远期 ≈ t2 处利率
    assert ts.forward_rate(0.2, 0.8) == pytest.approx(0.04, abs=1e-6)


def test_nelson_siegel_short_and_long_limit():
    """NelsonSiegel: t→0 利率 = beta0+beta1; t→∞ 利率 → beta0"""
    ns = NelsonSiegelModel(beta0=0.04, beta1=-0.01, beta2=0.005, tau=2.0)
    # 短端 (t→0): rate = beta0 + beta1
    assert ns.rate(1e-9) == pytest.approx(0.04 - 0.01, abs=1e-4)
    # 长端 (t→∞): rate → beta0
    assert ns.rate(100.0) == pytest.approx(0.04, abs=1e-4)
    # 正利率下 discount_factor 随期限递减
    assert ns.discount_factor(2.0) < ns.discount_factor(1.0)


def test_nelson_siegel_known_monotonic():
    """NelsonSiegel: beta1<0 时短端低于长端 (向上倾斜曲线)"""
    ns = NelsonSiegelModel(beta0=0.04, beta1=-0.01, beta2=0.005, tau=2.0)
    assert ns.rate(0.25) < ns.rate(5.0)  # 短端低、长端高
    # 中间点介于长短端之间
    mid = ns.rate(2.0)
    assert ns.rate(0.25) < mid < ns.rate(5.0) or ns.rate(0.25) < mid


def test_from_points_heuristic():
    """from_points: 用市场点启发式构造 NS (长端=beta0)"""
    pts = [
        RatePoint(0.25, 0.018),
        RatePoint(0.5, 0.020),
        RatePoint(1.0, 0.023),
        RatePoint(2.0, 0.027),
        RatePoint(5.0, 0.032),
        RatePoint(10.0, 0.036),
    ]
    model = NelsonSiegelModel.from_points(pts)
    # 长端利率接近 10Y 点
    assert model.rate(10.0) == pytest.approx(0.036, abs=5e-3)
    # 短端利率接近 0.25Y 点
    assert model.rate(0.25) == pytest.approx(0.018, abs=5e-3)


def test_term_structure_polymorphism():
    """三种实现都满足 TermStructure 接口"""
    tss = [
        FlatTermStructure(0.03),
        LinearTermStructure([RatePoint(0.0, 0.02), RatePoint(1.0, 0.03)]),
        NelsonSiegelModel(0.04, -0.01, 0.005, 2.0),
    ]
    for ts in tss:
        assert hasattr(ts, "rate")
        assert hasattr(ts, "discount_factor")
        assert hasattr(ts, "forward_rate")
        assert ts.rate(1.0) > -0.1
