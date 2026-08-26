"""fineng/pricing/black_scholes.py + implied_vol.py 边界分支补充测试.

目标: black_scholes.py (branch 0.48→高) + implied_vol.py (branch 0.58→高)
专注边界分支: T<=0 / sigma<=0 / S<=0 / K<=0 / is_call True/False
"""
from __future__ import annotations

import math

import pytest

from utils.fineng.pricing.black_scholes import (
    GreeksResult,
    bs_all_greeks,
    bs_call_price,
    bs_d1_d2,
    bs_delta,
    bs_gamma,
    bs_price,
    bs_put_price,
    bs_rho,
    bs_theta,
    bs_vega,
    check_put_call_parity,
    norm_cdf,
    norm_pdf,
)
from utils.fineng.pricing.implied_vol import (
    ImpliedVolResult,
    _no_arbitrage_lower_bound,
    implied_vol,
    implied_vol_bisection,
)


# ============================================================
# NormFunctionsTest — 正态分布辅助函数
# ============================================================

class NormFunctionsTest:

    def test_norm_cdf_zero(self):
        assert norm_cdf(0.0) == pytest.approx(0.5)

    def test_norm_cdf_positive(self):
        assert norm_cdf(1.0) > 0.5
        assert norm_cdf(1.0) == pytest.approx(0.8413, abs=1e-4)

    def test_norm_cdf_negative(self):
        assert norm_cdf(-1.0) < 0.5
        assert norm_cdf(-1.0) == pytest.approx(0.1587, abs=1e-4)

    def test_norm_cdf_symmetry(self):
        assert norm_cdf(2.0) + norm_cdf(-2.0) == pytest.approx(1.0)

    def test_norm_pdf_zero(self):
        assert norm_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi))

    def test_norm_pdf_positive(self):
        assert norm_pdf(1.0) > 0
        assert norm_pdf(1.0) == pytest.approx(0.2420, abs=1e-4)


# ============================================================
# BsD1D2Test — d1/d2 计算
# ============================================================

class BsD1D2Test:

    def test_normal_case(self):
        d1, d2 = bs_d1_d2(100, 100, 1.0, 0.05, 0.20)
        assert not math.isnan(d1)
        assert not math.isnan(d2)
        assert d2 == pytest.approx(d1 - 0.20)

    def test_T_zero_returns_nan(self):
        d1, d2 = bs_d1_d2(100, 100, 0.0, 0.05, 0.20)
        assert math.isnan(d1)
        assert math.isnan(d2)

    def test_sigma_zero_returns_nan(self):
        d1, d2 = bs_d1_d2(100, 100, 1.0, 0.05, 0.0)
        assert math.isnan(d1)

    def test_S_zero_returns_nan(self):
        d1, d2 = bs_d1_d2(0, 100, 1.0, 0.05, 0.20)
        assert math.isnan(d1)

    def test_K_zero_returns_nan(self):
        d1, d2 = bs_d1_d2(100, 0, 1.0, 0.05, 0.20)
        assert math.isnan(d1)


# ============================================================
# BsCallPriceTest — 看涨期权定价边界
# ============================================================

class BsCallPriceTest:

    def test_atm(self):
        price = bs_call_price(100, 100, 1.0, 0.05, 0.20)
        assert price == pytest.approx(10.45, abs=0.1)

    def test_S_zero(self):
        assert bs_call_price(0, 100, 1.0) == 0.0

    def test_K_zero(self):
        assert bs_call_price(100, 0, 1.0) == 0.0

    def test_T_zero_itm(self):
        assert bs_call_price(110, 100, 0.0) == 10.0

    def test_T_zero_otm(self):
        assert bs_call_price(90, 100, 0.0) == 0.0

    def test_sigma_zero_itm(self):
        result = bs_call_price(110, 100, 1.0, 0.05, 0.0)
        assert result == pytest.approx(110 - 100 * math.exp(-0.05))

    def test_sigma_zero_otm(self):
        result = bs_call_price(90, 100, 1.0, 0.05, 0.0)
        assert result == 0.0


# ============================================================
# BsPutPriceTest — 看跌期权定价边界
# ============================================================

class BsPutPriceTest:

    def test_atm(self):
        price = bs_put_price(100, 100, 1.0, 0.05, 0.20)
        assert price > 0

    def test_S_zero(self):
        assert bs_put_price(0, 100, 1.0) == 0.0

    def test_K_zero(self):
        assert bs_put_price(100, 0, 1.0) == 0.0

    def test_T_zero_itm(self):
        assert bs_put_price(90, 100, 0.0) == 10.0

    def test_T_zero_otm(self):
        assert bs_put_price(110, 100, 0.0) == 0.0

    def test_sigma_zero_itm(self):
        result = bs_put_price(90, 100, 1.0, 0.05, 0.0)
        assert result == pytest.approx(100 * math.exp(-0.05) - 90)

    def test_sigma_zero_otm(self):
        result = bs_put_price(110, 100, 1.0, 0.05, 0.0)
        assert result == 0.0


# ============================================================
# BsPriceDispatchTest — 调度函数
# ============================================================

class BsPriceDispatchTest:

    def test_call_dispatch(self):
        assert bs_price(100, 100, 1.0, 0.05, 0.20, is_call=True) == bs_call_price(100, 100, 1.0, 0.05, 0.20)

    def test_put_dispatch(self):
        assert bs_price(100, 100, 1.0, 0.05, 0.20, is_call=False) == bs_put_price(100, 100, 1.0, 0.05, 0.20)


# ============================================================
# BsDeltaTest — Delta 边界
# ============================================================

class BsDeltaTest:

    def test_call_normal(self):
        d = bs_delta(100, 100, 1.0, 0.05, 0.20, is_call=True)
        assert 0 < d < 1

    def test_put_normal(self):
        d = bs_delta(100, 100, 1.0, 0.05, 0.20, is_call=False)
        assert -1 < d < 0

    def test_S_zero(self):
        assert bs_delta(0, 100, 1.0) == 0.0

    def test_T_zero_call_itm(self):
        assert bs_delta(110, 100, 0.0, is_call=True) == 1.0

    def test_T_zero_call_otm(self):
        assert bs_delta(90, 100, 0.0, is_call=True) == 0.0

    def test_T_zero_put_itm(self):
        assert bs_delta(90, 100, 0.0, is_call=False) == -1.0

    def test_T_zero_put_otm(self):
        assert bs_delta(110, 100, 0.0, is_call=False) == 0.0

    def test_sigma_zero_call_itm(self):
        assert bs_delta(110, 100, 1.0, 0.05, 0.0, is_call=True) == 1.0

    def test_sigma_zero_call_otm(self):
        assert bs_delta(90, 100, 1.0, 0.05, 0.0, is_call=True) == 0.0

    def test_sigma_zero_put_itm(self):
        assert bs_delta(90, 100, 1.0, 0.05, 0.0, is_call=False) == -1.0

    def test_sigma_zero_put_otm(self):
        assert bs_delta(110, 100, 1.0, 0.05, 0.0, is_call=False) == 0.0


# ============================================================
# BsGammaTest — Gamma 边界
# ============================================================

class BsGammaTest:

    def test_normal(self):
        g = bs_gamma(100, 100, 1.0, 0.05, 0.20)
        assert g > 0

    def test_T_zero(self):
        assert bs_gamma(100, 100, 0.0) == 0.0

    def test_sigma_zero(self):
        assert bs_gamma(100, 100, 1.0, 0.05, 0.0) == 0.0

    def test_S_zero(self):
        assert bs_gamma(0, 100, 1.0) == 0.0


# ============================================================
# BsThetaTest — Theta 边界
# ============================================================

class BsThetaTest:

    def test_call_normal(self):
        t = bs_theta(100, 100, 1.0, 0.05, 0.20, is_call=True)
        assert t < 0  # Theta 通常为负

    def test_put_normal(self):
        t = bs_theta(100, 100, 1.0, 0.05, 0.20, is_call=False)
        assert isinstance(t, float)

    def test_T_zero(self):
        assert bs_theta(100, 100, 0.0) == 0.0

    def test_sigma_zero(self):
        assert bs_theta(100, 100, 1.0, 0.05, 0.0) == 0.0


# ============================================================
# BsVegaTest — Vega 边界
# ============================================================

class BsVegaTest:

    def test_normal(self):
        v = bs_vega(100, 100, 1.0, 0.05, 0.20)
        assert v > 0

    def test_T_zero(self):
        assert bs_vega(100, 100, 0.0) == 0.0

    def test_S_zero(self):
        assert bs_vega(0, 100, 1.0) == 0.0


# ============================================================
# BsRhoTest — Rho 边界
# ============================================================

class BsRhoTest:

    def test_call_normal(self):
        r = bs_rho(100, 100, 1.0, 0.05, 0.20, is_call=True)
        assert r > 0

    def test_put_normal(self):
        r = bs_rho(100, 100, 1.0, 0.05, 0.20, is_call=False)
        assert r < 0

    def test_T_zero(self):
        assert bs_rho(100, 100, 0.0) == 0.0


# ============================================================
# BsAllGreeksTest — 批量 Greeks
# ============================================================

class BsAllGreeksTest:

    def test_call_normal(self):
        g = bs_all_greeks(100, 100, 1.0, 0.05, 0.20, is_call=True)
        assert isinstance(g, GreeksResult)
        assert 0 < g.delta < 1
        assert g.gamma > 0
        assert g.price > 0

    def test_put_normal(self):
        g = bs_all_greeks(100, 100, 1.0, 0.05, 0.20, is_call=False)
        assert -1 < g.delta < 0
        assert g.price > 0

    def test_boundary_T_zero(self):
        g = bs_all_greeks(110, 100, 0.0, 0.05, 0.20, is_call=True)
        assert g.delta == 1.0
        assert g.gamma == 0.0

    def test_boundary_S_zero(self):
        g = bs_all_greeks(0, 100, 1.0, 0.05, 0.20)
        assert g.delta == 0.0
        assert g.price == 0.0

    def test_boundary_sigma_zero(self):
        g = bs_all_greeks(110, 100, 1.0, 0.05, 0.0, is_call=True)
        assert g.delta == 1.0


# ============================================================
# PutCallParityTest — 平价验证
# ============================================================

class PutCallParityTest:

    def test_parity_holds(self):
        assert check_put_call_parity(100, 100, 1.0, 0.05, 0.20) is True

    def test_parity_different_K(self):
        assert check_put_call_parity(100, 120, 0.5, 0.02, 0.30) is True


# ============================================================
# ImpliedVolTest — 隐含波动率求解
# ============================================================

class ImpliedVolTest:

    def test_solve_call(self):
        """从 BS 价格反推隐含波动率."""
        market_price = bs_call_price(100, 100, 1.0, 0.05, 0.20)
        result = implied_vol(market_price, 100, 100, 1.0, 0.05, is_call=True)
        assert result.converged
        assert result.iv == pytest.approx(0.20, abs=1e-4)

    def test_solve_put(self):
        market_price = bs_put_price(100, 100, 1.0, 0.05, 0.30)
        result = implied_vol(market_price, 100, 100, 1.0, 0.05, is_call=False)
        assert result.converged
        assert result.iv == pytest.approx(0.30, abs=1e-4)

    def test_below_lower_bound(self):
        """市场价格低于无套利下界 → 未收敛."""
        result = implied_vol(0.01, 100, 100, 1.0, 0.05, is_call=True)
        assert result.converged is False
        assert result.iv == 0.0

    def test_bisection_fallback(self):
        """bisection 法直接调用."""
        market_price = bs_call_price(100, 100, 1.0, 0.05, 0.20)
        result = implied_vol_bisection(market_price, 100, 100, 1.0, 0.05, is_call=True)
        assert result.converged
        assert result.iv == pytest.approx(0.20, abs=1e-4)

    def test_bisection_out_of_range(self):
        """根不在区间内 → 扩大边界或返回未收敛."""
        result = implied_vol_bisection(0.001, 100, 100, 1.0, 0.05, is_call=True)
        assert isinstance(result, ImpliedVolResult)

    def test_no_arbitrage_lower_bound_call(self):
        lb = _no_arbitrage_lower_bound(110, 100, 1.0, 0.05, is_call=True)
        assert lb == pytest.approx(110 - 100 * math.exp(-0.05))

    def test_no_arbitrage_lower_bound_put(self):
        lb = _no_arbitrage_lower_bound(90, 100, 1.0, 0.05, is_call=False)
        assert lb == pytest.approx(100 * math.exp(-0.05) - 90)

    def test_no_arbitrage_lower_bound_zero(self):
        lb = _no_arbitrage_lower_bound(90, 100, 1.0, 0.05, is_call=True)
        assert lb == 0.0

    def test_implied_vol_deep_otm(self):
        """深度虚值期权."""
        market_price = bs_call_price(100, 200, 1.0, 0.05, 0.50)
        result = implied_vol(market_price, 100, 200, 1.0, 0.05, is_call=True)
        assert result.converged
        assert result.iv == pytest.approx(0.50, abs=1e-3)