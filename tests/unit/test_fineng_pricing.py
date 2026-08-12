"""期权定价核心模块单测: black_scholes / binomial / implied_vol / monte_carlo.

聚焦工业级关键路径: BS 公式 + 希腊值、二叉树、隐含波动率反解、蒙特卡洛。
全部纯数学, 无副作用、无网络依赖。
"""
from __future__ import annotations

import math

import pytest

from utils.fineng.pricing import black_scholes as bs
from utils.fineng.pricing.binomial import (
    BinomialResult,
    BinomialTree,
    ExerciseStyle,
    binomial_price,
)
from utils.fineng.pricing.implied_vol import (
    ImpliedVolResult,
    _no_arbitrage_lower_bound,
    implied_vol,
    implied_vol_bisection,
)
from utils.fineng.pricing.monte_carlo import MCPricingResult, MonteCarloEngine


# ---------- black_scholes ----------
def test_norm_cdf_symmetry():
    assert bs.norm_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert bs.norm_cdf(-1.0) + bs.norm_cdf(1.0) == pytest.approx(1.0, abs=1e-9)


def test_norm_pdf_positive_and_peak():
    assert bs.norm_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), abs=1e-9)
    assert bs.norm_pdf(1.0) < bs.norm_pdf(0.0)


def test_bs_d1_d2_at_the_money():
    d1, d2 = bs.bs_d1_d2(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)
    assert d1 - d2 == pytest.approx(0.2 * math.sqrt(1.0), abs=1e-9)


@pytest.mark.parametrize("sigma,T", [(0.2, 1.0), (0.3, 0.5), (0.1, 2.0)])
def test_bs_call_price_positive_and_bound(sigma, T):
    price = bs.bs_call_price(S=100.0, K=100.0, T=T, r=0.05, sigma=sigma)
    assert price > 0.0
    assert price < 100.0


def test_bs_put_call_parity():
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.2
    c = bs.bs_call_price(S, K, T, r, sigma)
    p = bs.bs_put_price(S, K, T, r, sigma)
    assert (c - p) == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)


def test_bs_delta_call_in_range():
    delta = bs.bs_delta(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True)
    assert 0.0 < delta < 1.0


def test_bs_delta_put_negative():
    delta = bs.bs_delta(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=False)
    assert -1.0 < delta < 0.0


def test_bs_gamma_positive():
    gamma = bs.bs_gamma(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)
    assert gamma > 0.0


def test_bs_vega_positive():
    vega = bs.bs_vega(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)
    assert vega > 0.0


def test_bs_all_greeks_structure():
    g = bs.bs_all_greeks(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)
    assert isinstance(g, bs.GreeksResult)
    assert g.delta == pytest.approx(
        bs.bs_delta(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True)
    )


def test_check_put_call_parity_true():
    assert bs.check_put_call_parity(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)


# ---------- binomial ----------
def test_binomial_price_matches_bs_approx():
    res: BinomialResult = BinomialTree(n_steps=200).price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True, exercise=ExerciseStyle.EUROPEAN
    )
    bs_call = bs.bs_call_price(100.0, 100.0, 1.0, 0.05, 0.2)
    assert res.price == pytest.approx(bs_call, abs=0.5)


def test_binomial_american_put_premium_over_european():
    euro = BinomialTree(n_steps=100).price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.3, is_call=False, exercise=ExerciseStyle.EUROPEAN
    ).price
    amer = BinomialTree(n_steps=100).price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.3, is_call=False, exercise=ExerciseStyle.AMERICAN
    ).price
    assert amer >= euro


def test_binomial_tree_steps_attribute():
    tree = BinomialTree(n_steps=50)
    assert tree.n_steps == 50


def test_binomial_price_module_wrapper():
    price = binomial_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True, american=False, n_steps=100
    )
    bs_call = bs.bs_call_price(100.0, 100.0, 1.0, 0.05, 0.2)
    assert price == pytest.approx(bs_call, abs=0.5)


# ---------- implied_vol ----------
def test_implied_vol_recovers_input():
    target = 0.2
    price = bs.bs_call_price(100.0, 100.0, 1.0, 0.05, target)
    res: ImpliedVolResult = implied_vol(market_price=price, S=100.0, K=100.0, T=1.0, r=0.05, is_call=True)
    assert res.iv == pytest.approx(target, abs=1e-3)
    assert res.converged


def test_implied_vol_bisection_boundary():
    price = bs.bs_call_price(100.0, 90.0, 0.5, 0.05, 0.25)
    res = implied_vol_bisection(market_price=price, S=100.0, K=90.0, T=0.5, r=0.05, is_call=True)
    assert res.iv == pytest.approx(0.25, abs=1e-3)


def test_no_arbitrage_lower_bound_positive():
    bound = _no_arbitrage_lower_bound(S=100.0, K=100.0, T=1.0, r=0.05, is_call=True)
    assert bound >= 0.0


# ---------- monte_carlo ----------
def test_monte_carlo_call_positive():
    engine = MonteCarloEngine(n_paths=2000, n_steps=100, seed=42)
    res: MCPricingResult = engine.price_european(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True)
    assert res.price > 0.0
    assert res.price == pytest.approx(bs.bs_call_price(100, 100, 1, 0.05, 0.2), abs=2.0)


def test_monte_carlo_put_positive():
    engine = MonteCarloEngine(n_paths=2000, n_steps=100, seed=7)
    res = engine.price_european(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=False)
    assert res.price > 0.0


def test_monte_carlo_reproducible_with_seed():
    import random

    # 同 seed 应完全复现: 重置全局随机状态后重新定价
    e1 = MonteCarloEngine(n_paths=20000, n_steps=100, seed=123)
    p1 = e1.price_european(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True).price
    random.seed(123)
    e2 = MonteCarloEngine(n_paths=20000, n_steps=100, seed=123)
    p2 = e2.price_european(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=True).price
    assert p1 == pytest.approx(p2, abs=1e-9)
