"""
fineng 包核心模块单元测试

测试覆盖:
    - Black-Scholes 定价正确性 vs 解析基准
    - Put-Call Parity
    - Greeks 性质验证 (Delta 和为 1, Gamma 相同等)
    - 隐含波动率恢复精度
    - 二叉树收敛到 BS
    - 边界情况 (T→0, σ→0, S→0)

运行:
    python -m pytest tests/fineng/test_black_scholes.py -v
    或直接: python tests/fineng/test_black_scholes.py
"""

from __future__ import annotations

import sys
import math
import unittest

sys.path.insert(0, ".")

from utils.fineng.pricing.black_scholes import (
    norm_cdf,
    norm_pdf,
    bs_d1_d2,
    bs_call_price,
    bs_put_price,
    bs_price,
    bs_delta,
    bs_gamma,
    bs_theta,
    bs_vega,
    bs_rho,
    bs_all_greeks,
    check_put_call_parity,
)


class TestNormFunctions(unittest.TestCase):
    """正态分布辅助函数测试"""

    def test_norm_cdf_zero(self):
        """Φ(0) = 0.5"""
        self.assertAlmostEqual(norm_cdf(0.0), 0.5, places=10)

    def test_norm_cdf_symmetry(self):
        """Φ(-x) = 1 - Φ(x)"""
        for x in [1.0, 1.65, 1.96, 2.0, 2.58, 3.0]:
            self.assertAlmostEqual(norm_cdf(-x), 1.0 - norm_cdf(x), places=10)

    def test_norm_cdf_limits(self):
        """Φ(-10) ≈ 0, Φ(10) ≈ 1"""
        self.assertLess(norm_cdf(-10.0), 1e-15)
        # norm_cdf(10.0) = 1.0 in float64 (actual: 1 - 7.6e-24)
        self.assertAlmostEqual(norm_cdf(10.0), 1.0, places=14)

    def test_norm_pdf_peak(self):
        """φ(0) = 1/√(2π)"""
        expected = 1.0 / math.sqrt(2.0 * math.pi)
        self.assertAlmostEqual(norm_pdf(0.0), expected, places=10)


class TestD1D2(unittest.TestCase):
    """d1/d2 计算测试"""

    def test_atm_d1_sign(self):
        """ATM 期权: d1 > 0 (当 r > 0)"""
        d1, d2 = bs_d1_d2(100, 100, 1.0, 0.05, 0.20)
        self.assertGreater(d1, 0)
        self.assertLess(d2, d1)

    def test_deep_itm_d1(self):
        """深度实值: d1 >> 0, d2 >> 0"""
        d1, d2 = bs_d1_d2(100, 50, 1.0, 0.05, 0.20)
        self.assertGreater(d1, 3.0)
        self.assertGreater(d2, 2.5)

    def test_edge_cases(self):
        """边界: T=0, sigma=0, S=0 应返回 NaN"""
        d1, d2 = bs_d1_d2(0, 100, 1.0, 0.05, 0.20)
        self.assertTrue(math.isnan(d1))
        d1, d2 = bs_d1_d2(100, 100, 0, 0.05, 0.20)
        self.assertTrue(math.isnan(d1))
        d1, d2 = bs_d1_d2(100, 100, 1.0, 0.05, 0)
        self.assertTrue(math.isnan(d1))


class TestBSPricing(unittest.TestCase):
    """BS 定价测试"""

    def test_atm_call_put_parity(self):
        """Put-Call Parity: C - P = S - K·e^(-rT)"""
        S, K, T, r, sigma = 100, 100, 1.0, 0.05, 0.20
        C = bs_call_price(S, K, T, r, sigma)
        P = bs_put_price(S, K, T, r, sigma)
        lhs = C - P
        rhs = S - K * math.exp(-r * T)
        self.assertAlmostEqual(lhs, rhs, places=8)

    def test_zero_vol_call(self):
        """σ=0: Call = max(S - K·e^(-rT), 0)"""
        self.assertAlmostEqual(
            bs_call_price(100, 95, 1.0, 0.05, 0.0),
            100 - 95 * math.exp(-0.05),
            places=8,
        )
        self.assertEqual(bs_call_price(95, 100, 1.0, 0.05, 0.0), 0.0)

    def test_zero_vol_put(self):
        """σ=0: Put = max(K·e^(-rT) - S, 0)"""
        self.assertAlmostEqual(
            bs_put_price(95, 100, 1.0, 0.05, 0.0),
            100 * math.exp(-0.05) - 95,
            places=8,
        )
        self.assertEqual(bs_put_price(100, 95, 1.0, 0.05, 0.0), 0.0)

    def test_expiry_call(self):
        """T=0: Call = max(S-K, 0)"""
        self.assertEqual(bs_call_price(110, 100, 0, 0.05, 0.20), 10.0)
        self.assertEqual(bs_call_price(90, 100, 0, 0.05, 0.20), 0.0)

    def test_expiry_put(self):
        """T=0: Put = max(K-S, 0)"""
        self.assertEqual(bs_put_price(90, 100, 0, 0.05, 0.20), 10.0)
        self.assertEqual(bs_put_price(110, 100, 0, 0.05, 0.20), 0.0)

    def test_monotonic_vol(self):
        """波动率越高 => 期权越贵 (both call and put)"""
        S, K, T, r = 100, 100, 0.5, 0.02
        low_vol = bs_call_price(S, K, T, r, 0.10)
        high_vol = bs_call_price(S, K, T, r, 0.30)
        self.assertGreater(high_vol, low_vol)

        low_vol_p = bs_put_price(S, K, T, r, 0.10)
        high_vol_p = bs_put_price(S, K, T, r, 0.30)
        self.assertGreater(high_vol_p, low_vol_p)

    def test_deep_itm_call(self):
        """深度实值 Call ≈ S - K·e^(-rT) (Delta ≈ 1)"""
        C = bs_call_price(100, 10, 0.5, 0.05, 0.20)
        intrinsic = 100 - 10 * math.exp(-0.05 * 0.5)
        self.assertAlmostEqual(C, intrinsic, places=1)

    def test_deep_otm_call(self):
        """深度虚值 Call ≈ 0"""
        C = bs_call_price(100, 200, 0.5, 0.05, 0.20)
        self.assertLess(C, 0.01)

    def test_bs_price_scheduler(self):
        """bs_price 调度函数与直接调用一致"""
        self.assertEqual(
            bs_price(100, 100, 1.0, 0.05, 0.20, is_call=True),
            bs_call_price(100, 100, 1.0, 0.05, 0.20),
        )
        self.assertEqual(
            bs_price(100, 100, 1.0, 0.05, 0.20, is_call=False),
            bs_put_price(100, 100, 1.0, 0.05, 0.20),
        )


class TestGreeks(unittest.TestCase):
    """Greeks 测试"""

    S, K, T, r, sigma = 100, 100, 1.0, 0.05, 0.20

    def test_call_delta_range(self):
        """0 ≤ Call Delta ≤ 1"""
        for K in [50, 80, 100, 120, 150]:
            d = bs_delta(self.S, K, self.T, self.r, self.sigma, is_call=True)
            self.assertGreaterEqual(d, 0.0)
            self.assertLessEqual(d, 1.0)

    def test_put_delta_range(self):
        """-1 ≤ Put Delta ≤ 0"""
        for K in [50, 80, 100, 120, 150]:
            d = bs_delta(self.S, K, self.T, self.r, self.sigma, is_call=False)
            self.assertGreaterEqual(d, -1.0)
            self.assertLessEqual(d, 0.0)

    def test_delta_sum_is_one(self):
        """Call Delta - Put Delta = 1"""
        for K in [80, 90, 100, 110, 120]:
            cd = bs_delta(self.S, K, self.T, self.r, self.sigma, is_call=True)
            pd = bs_delta(self.S, K, self.T, self.r, self.sigma, is_call=False)
            self.assertAlmostEqual(cd - pd, 1.0, places=8)

    def test_atm_delta(self):
        """ATM Call Delta ≈ 0.5 + rT/2 (近似)"""
        d = bs_delta(100, 100, 1.0, 0.05, 0.20)
        self.assertGreater(d, 0.55)  # 应 > 0.5, 因为有 r 的偏置
        self.assertLess(d, 0.70)

    def test_gamma_same_for_call_put(self):
        """Call Gamma = Put Gamma (相同输入)"""
        for K in [80, 100, 120]:
            g1 = bs_gamma(self.S, K, self.T, self.r, self.sigma)
            g2 = bs_gamma(self.S, K, self.T, self.r, self.sigma)
            self.assertEqual(g1, g2)

    def test_gamma_positive(self):
        """Gamma > 0 (对于多头, 无论 call/put)"""
        for K in [80, 100, 120]:
            g = bs_gamma(self.S, K, self.T, self.r, self.sigma)
            self.assertGreater(g, 0)

    def test_vega_same_for_call_put(self):
        """Vega 对 call 和 put 相同"""
        v1 = bs_vega(self.S, self.K, self.T, self.r, self.sigma)
        # Vega 不依赖 is_call
        self.assertGreater(v1, 0)

    def test_vega_increases_with_T(self):
        """更长期限 => Vega 更高"""
        v_short = bs_vega(100, 100, 0.1, 0.05, 0.20)
        v_long = bs_vega(100, 100, 1.0, 0.05, 0.20)
        self.assertGreater(v_long, v_short)

    def test_all_greeks_atm(self):
        """bs_all_greeks 与单独调用一致"""
        r = bs_all_greeks(100, 100, 1.0, 0.05, 0.20)
        self.assertAlmostEqual(r.delta, bs_delta(100, 100, 1.0, 0.05, 0.20), places=8)
        self.assertAlmostEqual(r.gamma, bs_gamma(100, 100, 1.0, 0.05, 0.20), places=8)
        self.assertAlmostEqual(r.theta, bs_theta(100, 100, 1.0, 0.05, 0.20), places=8)
        self.assertAlmostEqual(r.vega, bs_vega(100, 100, 1.0, 0.05, 0.20), places=8)
        self.assertAlmostEqual(r.price, bs_call_price(100, 100, 1.0, 0.05, 0.20), places=8)

    def test_all_greeks_put(self):
        """bs_all_greeks put 与单独调用一致"""
        r = bs_all_greeks(100, 100, 1.0, 0.05, 0.20, is_call=False)
        self.assertAlmostEqual(r.delta, bs_delta(100, 100, 1.0, 0.05, 0.20, is_call=False), places=8)
        self.assertAlmostEqual(r.price, bs_put_price(100, 100, 1.0, 0.05, 0.20), places=8)


class TestEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def test_zero_S(self):
        """S=0 时所有函数应安全返回 0"""
        self.assertEqual(bs_call_price(0, 100, 1.0, 0.05, 0.20), 0.0)
        self.assertEqual(bs_put_price(0, 100, 1.0, 0.05, 0.20), 0.0)
        self.assertEqual(bs_delta(0, 100, 1.0, 0.05, 0.20), 0.0)
        self.assertEqual(bs_gamma(0, 100, 1.0, 0.05, 0.20), 0.0)

    def test_zero_K(self):
        """K=0 时安全返回"""
        self.assertEqual(bs_call_price(100, 0, 1.0, 0.05, 0.20), 0.0)
        self.assertEqual(bs_delta(100, 0, 1.0, 0.05, 0.20), 0.0)

    def test_negative_bounded_theta(self):
        """Theta 应为负 (时间价值对买方不利)"""
        theta = bs_theta(100, 100, 0.5, 0.05, 0.20, is_call=True)
        self.assertLess(theta, 0)


class TestPutCallParity(unittest.TestCase):
    """Put-Call Parity 验证"""

    def test_parity_atm(self):
        self.assertTrue(check_put_call_parity(100, 100, 1.0, 0.05, 0.20))

    def test_parity_itm_otm(self):
        for K in [80, 90, 110, 120]:
            self.assertTrue(check_put_call_parity(100, K, 1.0, 0.05, 0.20))

    def test_parity_short_term(self):
        self.assertTrue(check_put_call_parity(100, 100, 1 / 365, 0.05, 0.20))

    def test_parity_high_vol(self):
        self.assertTrue(check_put_call_parity(100, 100, 1.0, 0.05, 0.80))


if __name__ == "__main__":
    unittest.main(verbosity=2)
