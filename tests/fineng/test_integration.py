"""
fineng 包集成测试

测试:
    - BS → IV → BS 往返精度
    - 二叉树 → BS 收敛性
    - MC → BS 偏差
    - Greeks 聚合器
"""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")

from utils.fineng.greeks.aggregator import PortfolioGreeksAggregator
from utils.fineng.pricing.binomial import BinomialTree, binomial_price
from utils.fineng.pricing.binomial import ExerciseStyle as BinomialExerciseStyle
from utils.fineng.pricing.black_scholes import bs_call_price, bs_put_price
from utils.fineng.pricing.implied_vol import implied_vol, implied_vol_bisection
from utils.fineng.pricing.monte_carlo import MonteCarloEngine


class TestIVRoundTrip(unittest.TestCase):
    """BS → IV → BS 往返测试"""

    def test_round_trip_call(self):
        """定价后恢复 IV 应在容差内"""
        for sigma_true in [0.10, 0.20, 0.35, 0.50]:
            price = bs_call_price(100, 100, 0.5, 0.02, sigma_true)
            result = implied_vol(price, 100, 100, 0.5, 0.02, is_call=True)
            self.assertTrue(result.converged, f"IV not converged for sigma={sigma_true}")
            self.assertAlmostEqual(result.iv, sigma_true, places=5,
                                   msg=f"sigma={sigma_true}")

    def test_round_trip_put(self):
        """看跌期权往返测试"""
        for sigma_true in [0.15, 0.25, 0.40]:
            price = bs_put_price(100, 95, 0.25, 0.02, sigma_true)
            result = implied_vol(price, 100, 95, 0.25, 0.02, is_call=False)
            self.assertTrue(result.converged)
            self.assertAlmostEqual(result.iv, sigma_true, places=5)

    def test_bisection_fallback(self):
        """Bisection 作为单独调用也能恢复 IV"""
        price = bs_call_price(100, 90, 0.75, 0.03, 0.30)
        result = implied_vol_bisection(price, 100, 90, 0.75, 0.03)
        self.assertTrue(result.converged)
        self.assertAlmostEqual(result.iv, 0.30, places=4)


class TestBinomialConvergence(unittest.TestCase):
    """二叉树 → BS 收敛测试"""

    def test_binomial_approaches_bs(self):
        """增加步数应收敛到 BS"""
        bs_c = bs_call_price(100, 100, 1.0, 0.05, 0.20)

        tree_50 = BinomialTree(n_steps=50)
        err_50 = abs(tree_50.price(100, 100, 1.0, 0.05, 0.20, is_call=True).price - bs_c)

        tree_500 = BinomialTree(n_steps=500)
        err_500 = abs(tree_500.price(100, 100, 1.0, 0.05, 0.20, is_call=True).price - bs_c)

        tree_2000 = BinomialTree(n_steps=2000)
        err_2000 = abs(tree_2000.price(100, 100, 1.0, 0.05, 0.20, is_call=True).price - bs_c)

        # 误差应递减
        self.assertGreater(err_50, err_500)
        self.assertGreater(err_500, err_2000)
        # 2000 步误差应 < 0.01
        self.assertLess(err_2000, 0.01)

    def test_american_premium(self):
        """美式期权 ≥ 欧式期权 (因为有提前行权溢价)"""
        tree = BinomialTree(n_steps=200)
        am = tree.price(100, 90, 0.5, 0.02, 0.25, is_call=False).price  # ITM Put
        eu = tree.price(100, 90, 0.5, 0.02, 0.25, is_call=False,
                        exercise=BinomialExerciseStyle.EUROPEAN).price
        self.assertGreaterEqual(am, eu)

    def test_convenience_function(self):
        """便捷函数 binomial_price 正常工作"""
        price = binomial_price(100, 100, 0.5, 0.02, 0.20)
        self.assertGreater(price, 0)
        self.assertLess(price, 20)


class TestMCvsBS(unittest.TestCase):
    """MC 与 BS 对照"""

    def test_mc_within_ci(self):
        """MC 定价 BS 误差应在 3*SE 内"""
        bs_c = bs_call_price(100, 100, 1.0, 0.05, 0.20)
        mc = MonteCarloEngine(n_paths=200000, n_steps=1, seed=42,
                              use_antithetic=True, use_control_variate=False)
        result = mc.price_european(100, 100, 1.0, 0.05, 0.20, is_call=True)

        error = abs(result.price - bs_c)
        self.assertLess(error, 3 * result.standard_error,
                        f"MC error {error:.4f} > 3*SE {3 * result.standard_error:.4f}")


class TestGreeksAggregator(unittest.TestCase):
    """Greeks 聚合器测试"""

    def test_single_option(self):
        """单期权持仓聚合"""
        agg = PortfolioGreeksAggregator()
        portfolio = agg.aggregate([{
            "code": "510050C2500M6.SH",
            "type": "OPTION",
            "qty": 10,
            "price": 0.15,
            "S": 2.75,
            "K": 2.50,
            "T": 0.5,
            "sigma": 0.22,
            "is_call": True,
            "multiplier": 10000,
        }], r=0.02)
        self.assertEqual(len(portfolio.positions), 1)
        self.assertGreater(portfolio.delta, 0)  # Call Delta > 0
        self.assertGreater(portfolio.gamma, 0)

    def test_stock_only(self):
        """纯股票持仓"""
        agg = PortfolioGreeksAggregator()
        portfolio = agg.aggregate([{
            "code": "510050.SH",
            "type": "STOCK",
            "qty": 10000,
            "price": 2.75,
        }])
        self.assertGreater(portfolio.delta, 0)
        self.assertEqual(portfolio.gamma, 0.0)
        self.assertEqual(portfolio.vega, 0.0)

    def test_mixed_portfolio(self):
        """混合持仓 (股票 + 期权)"""
        agg = PortfolioGreeksAggregator()
        portfolio = agg.aggregate([
            {"code": "510050.SH", "type": "STOCK", "qty": 10000, "price": 2.75},
            {"code": "510050P2500M6.SH", "type": "OPTION", "qty": -5,
             "price": 0.08, "S": 2.75, "K": 2.50, "T": 0.5, "sigma": 0.22,
             "is_call": False, "multiplier": 10000},
        ], r=0.02)
        # 检查有合理的 Delta 和 Vega
        self.assertIsNotNone(portfolio.delta)
        self.assertIsNotNone(portfolio.vega)
        self.assertGreater(portfolio.total_market_value, 0)

    def test_rebalance_signal(self):
        """再平衡信号生成"""
        agg = PortfolioGreeksAggregator()
        portfolio = agg.aggregate([
            {"code": "510050.SH", "type": "STOCK", "qty": 100000, "price": 2.75},
            {"code": "510050C2500M6.SH", "type": "OPTION", "qty": -100,
             "price": 0.50, "S": 2.75, "K": 2.50, "T": 0.5, "sigma": 0.22,
             "is_call": True, "multiplier": 10000},
        ], r=0.02)

        signal = agg.rebalance_signal(portfolio, delta_tolerance=0.01)
        self.assertIsInstance(signal, dict)
        self.assertIn("need_rebalance", signal)
        self.assertIn("alerts", signal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
