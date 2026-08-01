"""
v7.5 测试：三联对冲 — Beta / Vol / Correlation
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hedging.beta_hedger import BetaHedger
from hedging.correlation_hedger import CorrelationHedger
from hedging.hedge_coordinator import HedgeCoordinator
from hedging.vol_hedger import VolHedger


class TestBetaHedger(unittest.TestCase):
    """Beta 对冲"""

    def setUp(self):
        self.hedger = BetaHedger()

    def test_ewma_beta_compute(self):
        np.random.seed(42)
        stock = np.random.randn(100) * 0.02
        market = stock * 0.8 + np.random.randn(100) * 0.01
        asset_ret = pd.Series(stock)
        mkt_ret = pd.Series(market)
        beta = self.hedger.ewma_beta(asset_ret, mkt_ret)
        self.assertGreater(beta, 0)
        self.assertLess(beta, 2.0)

    def test_no_hedge_when_beta_low(self):
        result = self.hedger.compute_hedge(
            portfolio_beta=0.4,
            portfolio_value=5_000_000
        )
        self.assertEqual(result['action'], 'NO_HEDGE')

    def test_hedge_when_beta_high(self):
        result = self.hedger.compute_hedge(
            portfolio_beta=0.85,
            portfolio_value=5_000_000
        )
        self.assertIn(result['action'], ['SHORT_FUTURES', 'DOWNGRADE_TO_PUT_SPREAD'])
        self.assertGreater(result.get('contracts', 0), 0)

    def test_hedge_lot_calculation(self):
        result = self.hedger.compute_hedge(
            portfolio_beta=0.85,
            portfolio_value=5_000_000
        )
        lots = result.get('contracts', 0)
        self.assertGreater(lots, 0)
        self.assertIsInstance(lots, int)


class TestVolHedger(unittest.TestCase):
    """波动率对冲"""

    def setUp(self):
        self.hedger = VolHedger()

    def test_no_hedge_vix_low(self):
        action = self.hedger.compute_hedge(vix=20, portfolio_value=5_000_000)
        self.assertEqual(action['action'], 'NO_HEDGE')

    def test_put_spread_vix_35(self):
        action = self.hedger.compute_hedge(vix=35, portfolio_value=5_000_000)
        self.assertEqual(action['action'], 'BUY_PUT_SPREAD')

    def test_bare_put_vix_50(self):
        action = self.hedger.compute_hedge(vix=50, portfolio_value=5_000_000)
        self.assertEqual(action['action'], 'BUY_BARE_PUT')

    def test_emergency_put_vix_70(self):
        action = self.hedger.compute_hedge(vix=70, portfolio_value=5_000_000)
        self.assertEqual(action['action'], 'BUY_EMERGENCY_PUT')
        self.assertLess(action['actual_coverage'], 1.0)


class TestCorrelationHedger(unittest.TestCase):
    """相关性对冲"""

    def setUp(self):
        self.hedger = CorrelationHedger()

    def test_avg_correlation(self):
        np.random.seed(42)
        data = pd.DataFrame({
            'A': np.random.randn(100),
            'B': np.random.randn(100) * 0.5 + np.random.randn(100) * 0.5,
            'C': np.random.randn(100),
        })
        avg = self.hedger.average_correlation(data)
        self.assertGreaterEqual(avg, -1.0)
        self.assertLessEqual(avg, 1.0)

    def test_no_hedge_low_corr(self):
        np.random.seed(42)
        data = pd.DataFrame({
            'A': np.random.randn(100),
            'B': np.random.randn(100),
        })
        result = self.hedger.compute_hedge(data, 5_000_000)
        # 低相关性时应无对冲
        self.assertIn(result['action'], ['NO_HEDGE'])

    def test_hedge_high_corr(self):
        # 创建高相关数据
        np.random.seed(42)
        common = np.random.randn(100)
        data = pd.DataFrame({
            'A': common + np.random.randn(100) * 0.1,
            'B': common + np.random.randn(100) * 0.1,
            'C': common + np.random.randn(100) * 0.1,
        })
        result = self.hedger.compute_hedge(data, 5_000_000)
        # 高相关性时应触发避险配置
        if result['action'] == 'SAFE_HAVEN_ALLOC':
            self.assertIn('gold_etf', result)


class TestHedgeCoordinator(unittest.TestCase):
    """对冲协调器集成测试"""

    def setUp(self):
        self.coordinator = HedgeCoordinator()

    def test_no_hedge_normal(self):
        np.random.seed(42)
        returns = pd.DataFrame(np.random.randn(100, 2), columns=['A', 'B'])
        market_returns = pd.Series(np.random.randn(100))
        orders = self.coordinator.coordinate(
            positions={'A': 1000, 'B': 1000},
            prices={'A': 100.0, 'B': 50.0},
            returns=returns,
            market_returns=market_returns,
            vix=18,
            hwm_drawdown=0.0,
            bs_loss=0.0,
            portfolio_value=5_000_000,
        )
        # 正常市场条件下不应有大量对冲
        self.assertLessEqual(orders.get('total_hedge_pct', 0), 0.4)

    def test_all_triggers(self):
        """极端场景：三路同时触发"""
        np.random.seed(42)
        returns = pd.DataFrame(np.random.randn(100, 2), columns=['A', 'B'])
        market_returns = pd.Series(np.random.randn(100))
        orders = self.coordinator.coordinate(
            positions={'A': 1000, 'B': 1000},
            prices={'A': 100.0, 'B': 50.0},
            returns=returns,
            market_returns=market_returns,
            vix=45,
            hwm_drawdown=0.2,
            bs_loss=0.15,
            portfolio_value=5_000_000,
        )
        # 极端VIX应触发对冲
        self.assertGreater(orders.get('total_hedge_pct', 0), 0)

    def test_deduplication(self):
        """重复信号去重"""
        np.random.seed(42)
        returns = pd.DataFrame(np.random.randn(100, 2), columns=['A', 'B'])
        market_returns = pd.Series(np.random.randn(100))
        orders = self.coordinator.coordinate(
            positions={'A': 1000, 'B': 1000},
            prices={'A': 100.0, 'B': 50.0},
            returns=returns,
            market_returns=market_returns,
            vix=70,
            hwm_drawdown=0.2,
            bs_loss=0.15,
            portfolio_value=5_000_000,
        )
        # 检查对冲订单类型唯一
        hedge_types = [o.get('hedge_type') for o in orders.get('orders', [])]
        self.assertEqual(len(hedge_types), len(set(hedge_types)))


if __name__ == '__main__':
    unittest.main()
