"""
v7.5 测试：三联对冲 — Beta / Vol / Correlation
"""
import sys
import os
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hedging.beta_hedger import BetaHedger
from hedging.vol_hedger import VolHedger
from hedging.correlation_hedger import CorrelationHedger
from hedging.hedge_coordinator import HedgeCoordinator


class TestBetaHedger(unittest.TestCase):
    """Beta 对冲"""

    def setUp(self):
        self.hedger = BetaHedger(capital=5_000_000)

    def test_ewma_beta_compute(self):
        np.random.seed(42)
        stock = np.random.randn(100) * 0.02
        market = stock * 0.8 + np.random.randn(100) * 0.01
        beta = self.hedger.compute_ewma_beta(pd.Series(stock), pd.Series(market))
        self.assertGreater(beta, 0)
        self.assertLess(beta, 2.0)

    def test_no_hedge_when_beta_low(self):
        actions = self.hedger.hedge({'600519.SH': 10000}, {}, portfolio_beta=0.4)
        self.assertEqual(actions, [])

    def test_hedge_when_beta_high(self):
        actions = self.hedger.hedge({'600519.SH': 10000}, {}, portfolio_beta=0.85)
        self.assertTrue(len(actions) > 0)
        self.assertIn('BETA_HEDGE', actions[0].get('type', ''))

    def test_hedge_lot_calculation(self):
        lots = self.hedger.calculate_hedge_lots(
            portfolio_value=5_000_000,
            portfolio_beta=0.85,
            target_beta=0.3,
            futures_beta=1.0,
            futures_price=4000,
            multiplier=300
        )
        self.assertGreater(lots, 0)
        # 手数应为整数
        self.assertIsInstance(lots, int)


class TestVolHedger(unittest.TestCase):
    """波动率对冲"""

    def setUp(self):
        self.hedger = VolHedger(capital=5_000_000)

    def test_no_hedge_vix_low(self):
        action = self.hedger.hedge(vix=20)
        self.assertIsNone(action)

    def test_put_spread_vix_35(self):
        action = self.hedger.hedge(vix=35)
        self.assertIsNotNone(action)
        self.assertEqual(action['type'], 'PUT_SPREAD')

    def test_bare_put_vix_50(self):
        action = self.hedger.hedge(vix=50)
        self.assertIsNotNone(action)
        self.assertEqual(action['type'], 'BARE_PUT')

    def test_emergency_put_vix_70(self):
        action = self.hedger.hedge(vix=70)
        self.assertIsNotNone(action)
        self.assertEqual(action['type'], 'EMERGENCY_PUT')
        self.assertIn('coverage', action)
        self.assertLess(action['coverage'], 1.0)


class TestCorrelationHedger(unittest.TestCase):
    """相关性对冲"""

    def setUp(self):
        self.hedger = CorrelationHedger(capital=5_000_000)

    def test_avg_correlation(self):
        np.random.seed(42)
        data = pd.DataFrame({
            'A': np.random.randn(100),
            'B': np.random.randn(100) * 0.5 + np.random.randn(100) * 0.5,
            'C': np.random.randn(100),
        })
        avg = self.hedger.compute_avg_correlation(data)
        self.assertGreaterEqual(avg, -1.0)
        self.assertLessEqual(avg, 1.0)

    def test_no_hedge_low_corr(self):
        action = self.hedger.hedge(avg_correlation=0.3)
        self.assertIsNone(action)

    def test_hedge_high_corr(self):
        action = self.hedger.hedge(avg_correlation=0.88)
        self.assertIsNotNone(action)
        self.assertEqual(action['type'], 'SAFE_HAVEN')
        self.assertIn('gold_etf', action)


class TestHedgeCoordinator(unittest.TestCase):
    """对冲协调器集成测试"""

    def setUp(self):
        self.coordinator = HedgeCoordinator(capital=5_000_000)

    def test_no_hedge_normal(self):
        orders = self.coordinator.coordinate(
            positions={'600519.SH': 10000},
            market_data=pd.DataFrame(),
            vix=18,
            portfolio_beta=0.4,
            avg_correlation=0.3,
        )
        self.assertEqual(orders, [])

    def test_all_triggers(self):
        """极端场景：三路同时触发"""
        orders = self.coordinator.coordinate(
            positions={'600519.SH': 10000},
            market_data=pd.DataFrame(),
            vix=45,
            portfolio_beta=0.85,
            avg_correlation=0.90,
        )
        self.assertTrue(len(orders) >= 3)

    def test_deduplication(self):
        """重复信号去重"""
        orders = self.coordinator.coordinate(
            positions={'600519.SH': 10000},
            market_data=pd.DataFrame(),
            vix=70,
            portfolio_beta=0.90,
            avg_correlation=0.95,
        )
        types = {o.get('type') for o in orders}
        self.assertEqual(len(types), len(orders))


if __name__ == '__main__':
    unittest.main()
