"""
v7.5 测试：Walk-Forward Analysis + 回测指标
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from backtest.cost_model import AlmgrenChrissCost, CostConfig, CostModel
from backtest.metrics import DeflatedSharpeRatio, PerformanceMetrics
from backtest.walk_forward import WalkForward, WalkForwardResult


class TestWalkForward(unittest.TestCase):
    """Walk-Forward Analysis"""

    def setUp(self):
        self.wf = WalkForward(train_months=24, test_months=3, step_months=3)

    def test_generate_windows(self):
        windows = self.wf.generate_windows('2022-01-01', '2025-12-31')
        self.assertGreater(len(windows), 0)
        for tr_s, tr_e, te_s, te_e in windows:
            self.assertLess(tr_s, tr_e)
            self.assertLessEqual(tr_e, te_s)
            self.assertLess(te_s, te_e)

    def test_window_count(self):
        """窗口数合理：3年数据, 24m/3m/3m → 约12个窗口"""
        windows = self.wf.generate_windows('2022-07-01', '2025-12-31')
        self.assertGreater(len(windows), 5)

    def test_run_basic(self):
        """简化版运行测试"""
        np.random.seed(42)
        dates = pd.date_range('2022-01-01', '2025-12-31', freq='B')
        data = pd.DataFrame({
            'price': 100 * (1 + np.random.randn(len(dates)) * 0.01).cumprod(),
        }, index=dates)

        def simple_train(df):
            return {'lookback': 20}

        def simple_test(df, params):
            ret = df['price'].pct_change().dropna()
            return ret * 0.5  # 模拟策略收益

        results = self.wf.run(data, simple_train, simple_test, verbose=False)
        self.assertGreater(len(results), 0)
        self.assertIsInstance(results[0], WalkForwardResult)

    def test_aggregate_metrics(self):
        """综合指标计算"""
        np.random.seed(42)
        for i in range(5):
            self.wf.results.append(WalkForwardResult(
                window_id=i,
                train_start=f'2022-{i*3+1:02d}-01',
                train_end=f'2024-{i*3+1:02d}-01',
                test_start=f'2024-{i*3+1:02d}-01',
                test_end=f'2024-{i*3+3:02d}-01',
                sortino=1.0 + np.random.randn() * 0.2,
                calmar=0.6 + np.random.randn() * 0.1,
                max_dd=-0.10 - np.random.rand() * 0.05,
                test_returns=np.random.randn(60) * 0.01,
            ))
        metrics = self.wf.aggregate_metrics()
        self.assertIn('sortino', metrics)
        self.assertIn('calmar', metrics)
        self.assertIn('max_dd', metrics)
        self.assertGreater(metrics['n_windows'], 0)


class TestPerformanceMetrics(unittest.TestCase):
    """绩效指标"""

    def setUp(self):
        np.random.seed(42)
        self.good_ret = pd.Series(np.random.randn(252) * 0.01 + 0.0005)  # 正收益
        self.bad_ret = pd.Series(np.random.randn(252) * 0.03 - 0.001)    # 负收益

    def test_annual_return(self):
        pm = PerformanceMetrics(self.good_ret)
        self.assertGreater(pm.annual_return(), 0)

        pm_bad = PerformanceMetrics(self.bad_ret)
        self.assertLess(pm_bad.annual_return(), 0)

    def test_max_drawdown(self):
        pm = PerformanceMetrics(self.good_ret)
        dd = pm.max_drawdown()
        self.assertLessEqual(dd, 0)

    def test_sortino(self):
        pm = PerformanceMetrics(self.good_ret)
        self.assertGreater(pm.sortino_ratio(), 0)

    def test_calmar(self):
        pm = PerformanceMetrics(self.good_ret)
        calmar = pm.calmar_ratio()
        self.assertIsInstance(calmar, float)

    def test_objective(self):
        pm = PerformanceMetrics(self.good_ret)
        obj = pm.objective()
        self.assertIsInstance(obj, float)

    def test_var_cvar(self):
        pm = PerformanceMetrics(self.bad_ret)
        var = pm.value_at_risk(0.95)
        cvar = pm.conditional_var(0.95)
        self.assertLessEqual(cvar, var)

    def test_summary(self):
        pm = PerformanceMetrics(self.good_ret)
        s = pm.summary()
        self.assertIn('sharpe', s)
        self.assertIn('sortino', s)
        self.assertIn('calmar', s)


class TestDeflatedSharpeRatio(unittest.TestCase):
    """DSR 测试"""

    def test_dsr_high_sr(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=2.0, n_trials=5,
                                   n_observations=252)
        result = dsr.compute()
        self.assertGreater(result, 0.9)
        self.assertTrue(dsr.is_significant())

    def test_dsr_low_sr(self):
        dsr = DeflatedSharpeRatio(sharpe_ratio=0.2, n_trials=100,
                                   n_observations=252)
        result = dsr.compute()
        self.assertLess(result, 0.9)

    def test_dsr_edge_case(self):
        """边界：零 Sharpe"""
        dsr = DeflatedSharpeRatio(sharpe_ratio=0.0, n_trials=1,
                                   n_observations=100)
        result = dsr.compute()
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 1)


class TestCostModel(unittest.TestCase):
    """成本模型"""

    def setUp(self):
        self.cm = CostModel(CostConfig())

    def test_commission_stock(self):
        cost = self.cm.commission(100000, 'stock', 'BUY')
        self.assertGreater(cost, 0)

    def test_commission_sell(self):
        cost_buy = self.cm.commission(100000, 'stock', 'BUY')
        cost_sell = self.cm.commission(100000, 'stock', 'SELL')
        # 卖出含印花税，应更贵
        self.assertGreater(cost_sell, cost_buy)

    def test_market_impact(self):
        impact = self.cm.market_impact(10000, 1000000, 0.02, 100)
        self.assertGreater(impact, 0)

    def test_total_cost(self):
        result = self.cm.total_cost(10000, 100, 1000000, 0.02)
        self.assertIn('commission', result)
        self.assertIn('market_impact', result)
        self.assertIn('total_cost', result)

    def test_almgren_chriss(self):
        ac = AlmgrenChrissCost(CostConfig())
        result = ac.total_cost(10000, 100, 1000000, 0.02)
        self.assertIn('permanent_impact', result)
        self.assertIn('temporary_impact', result)


if __name__ == '__main__':
    unittest.main()
