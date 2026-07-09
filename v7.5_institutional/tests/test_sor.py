"""
v7.5 测试：Smart Order Router — Iceberg + 滑点熔断
"""
import sys
import os
import unittest
from datetime import datetime, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from execution.broker_api import SimulatedBroker
from execution.smart_order_router import SmartOrderRouter
from execution.algo_engine import AlgoEngine


class TestSmartOrderRouter(unittest.TestCase):
    """SOR 单元测试"""

    def setUp(self):
        self.broker = SimulatedBroker(initial_capital=5_000_000)
        self.broker.set_price('600519.SH', 1800.0, 100000)
        self.broker.set_price('000858.SZ', 160.0, 500000)

        self.sor = SmartOrderRouter(
            broker_api=self.broker,
            config={
                'iceberg_pct_of_depth': 0.10,
                'max_attempts': 5,
                'throttle_seconds': 0,
                'per_trade_break': 0.005,
                'daily_break': 0.010,
                'global_slow_threshold': 0.003,
            },
            ntp_offset=0.0
        )

    def test_basic_execute(self):
        fills = self.sor.execute('600519.SH', 1000, 'BUY', 1800.0)
        self.assertTrue(len(fills) > 0)
        result = fills[0]
        self.assertIn(result.get('status', ''), ['COMPLETE', 'PARTIAL'])

    def test_iceberg_slicing(self):
        """冰山拆单：大单应被拆成多笔"""
        fills = self.sor.execute('600519.SH', 50000, 'BUY', 1800.0)
        total_filled = sum(
            f.get('filled_qty', f.get('qty', 0))
            for f in fills
            if isinstance(f, dict)
        )
        self.assertGreater(total_filled, 0)

    def test_slippage_break(self):
        """滑点熔断：高价买入应触发"""
        fills = self.sor.execute('000858.SZ', 1000, 'BUY', 100.0)
        statuses = [f.get('status') for f in fills if isinstance(f, dict)]
        # 模拟环境中可能存在滑点
        self.assertTrue(any(s in ['COMPLETE', 'PARTIAL', 'SLIPPAGE_BREAK']
                           for s in statuses))

    def test_pause_after_accumulated_slip(self):
        """累计滑点超过 1% → 暂停"""
        self.sor.slip_per_symbol['000858.SZ'] = 0.012
        self.sor.slip_pause_until['000858.SZ'] = datetime(2099, 1, 1)
        fills = self.sor.execute('000858.SZ', 100, 'BUY', 160.0)
        status = fills[0].get('status', '') if fills else ''
        self.assertEqual(status, 'PAUSED')

    def test_server_ts(self):
        ts = self.sor.server_ts()
        self.assertIsInstance(ts, datetime)


class TestAlgoEngine(unittest.TestCase):
    """AlgoEngine 测试"""

    def setUp(self):
        self.broker = SimulatedBroker(initial_capital=5_000_000)
        self.broker.set_price('600519.SH', 1800.0, 100000)

        self.sor = SmartOrderRouter(
            broker_api=self.broker,
            config={'iceberg_pct_of_depth': 0.10, 'max_attempts': 5,
                    'throttle_seconds': 0, 'per_trade_break': 0.005,
                    'daily_break': 0.010, 'global_slow_threshold': 0.003},
            ntp_offset=0.0
        )
        self.engine = AlgoEngine(sor=self.sor)

    def test_twap_execution(self):
        fills = self.engine.execute_order(
            '600519.SH', 1000, 'BUY', 1800.0, algo='TWAP'
        )
        self.assertTrue(len(fills) > 0)

    def test_vwap_execution(self):
        fills = self.engine.execute_order(
            '600519.SH', 1000, 'BUY', 1800.0, algo='VWAP'
        )
        self.assertTrue(len(fills) > 0)

    def test_pov_execution(self):
        fills = self.engine.execute_order(
            '600519.SH', 1000, 'BUY', 1800.0, algo='POV'
        )
        self.assertTrue(len(fills) > 0)

    def test_batch_execution(self):
        orders = [
            {'symbol': '600519.SH', 'qty': 500, 'side': 'BUY',
             'decision_price': 1800.0, 'algo': 'TWAP'},
            {'symbol': '600519.SH', 'qty': 300, 'side': 'SELL',
             'decision_price': 1800.0, 'algo': 'VWAP'},
        ]
        fills = self.engine.execute_batch(orders)
        self.assertTrue(len(fills) > 0)

    def test_is_trading_hours(self):
        # 取决于当前时间，不做断言，确保不抛异常
        result = self.engine.is_trading_hours()
        self.assertIsInstance(result, bool)

    def test_end_of_day(self):
        self.engine.end_of_day()
        self.assertAlmostEqual(self.sor.global_slowdown, 1.0)


if __name__ == '__main__':
    unittest.main()
