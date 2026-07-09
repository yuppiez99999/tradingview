# -*- coding: utf-8 -*-
"""
v7.5 Institutional — 单元测试

运行:
    cd v7.5_institutional
    python -m pytest tests/test_v75.py -v
    # 或
    python tests/test_v75.py
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 添加 src 到 path
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))


class TestRiskManager(unittest.TestCase):
    """风险预算模块测试"""

    def setUp(self):
        from risk.risk_manager import RiskManager
        self.rm = RiskManager(total_capital=5_000_000)

    def test_normal_mode(self):
        """DD < 10% → NORMAL"""
        self.rm.update_drawdown(5_000_000 * 0.95)  # DD=5%
        self.assertEqual(self.rm.mode, "NORMAL")
        self.assertEqual(self.rm.position_size_factor, 1.0)

    def test_defense_mode(self):
        """10% ≤ DD < 14% → DEFENSE, 仓位减半"""
        self.rm.update_drawdown(5_000_000 * 0.88)  # DD=12%
        self.assertEqual(self.rm.mode, "DEFENSE")
        self.assertEqual(self.rm.position_size_factor, 0.5)

    def test_circuit_breaker(self):
        """DD ≥ 14% → CIRCUIT_BREAKER"""
        self.rm.update_drawdown(5_000_000 * 0.84)  # DD=16%
        self.assertEqual(self.rm.mode, "CIRCUIT_BREAKER")
        self.assertEqual(self.rm.position_size_factor, 0.0)
        self.assertIsNotNone(self.rm.circuit_break_until)

    def test_kelly_weight(self):
        """Kelly 仓位计算"""
        w = self.rm.kelly_weight("TEST", mu_hist=0.10, sigma=0.25,
                                  beta=1.1, n=252)
        self.assertGreater(w, 0)
        self.assertLessEqual(w, 0.20)  # 上限 20%

    def test_kelly_zero_sigma(self):
        """sigma=0 返回 0"""
        w = self.rm.kelly_weight("TEST", mu_hist=0.10, sigma=0,
                                  beta=1.0, n=252)
        self.assertEqual(w, 0.0)

    def test_risk_parity(self):
        """Risk Parity 权重"""
        np.random.seed(42)
        returns = pd.DataFrame(
            np.random.normal(0.001, 0.02, (100, 3)),
            columns=["A", "B", "C"])
        w = self.rm.risk_parity_weights(returns)
        self.assertEqual(len(w), 3)
        self.assertAlmostEqual(np.sum(w), 1.0, places=4)
        self.assertTrue(np.all(w > 0))


class TestCircuitBreaker(unittest.TestCase):
    """熔断引擎测试"""

    def setUp(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitLevel
        self.cb = CircuitBreaker()
        self.CircuitLevel = CircuitLevel

    def test_level_1(self):
        """跌 3% → LEVEL_1"""
        level = self.cb.check(portfolio_drop=0.04)
        self.assertEqual(level, self.CircuitLevel.LEVEL_1)

    def test_level_2(self):
        """跌 5% → LEVEL_2"""
        level = self.cb.check(portfolio_drop=0.06)
        self.assertEqual(level, self.CircuitLevel.LEVEL_2)

    def test_level_3(self):
        """跌 7% → LEVEL_3"""
        level = self.cb.check(portfolio_drop=0.08)
        self.assertEqual(level, self.CircuitLevel.LEVEL_3)

    def test_level_4_vix(self):
        """VIX > 80 → LEVEL_4"""
        level = self.cb.check(portfolio_drop=0.01, vix=85)
        self.assertEqual(level, self.CircuitLevel.LEVEL_4)

    def test_vix_escalate(self):
        """VIX>=60 且跌>5% → LEVEL_3"""
        level = self.cb.check(portfolio_drop=0.06, vix=65)
        self.assertEqual(level, self.CircuitLevel.LEVEL_3)

    def test_allowed_actions(self):
        """允许操作检查"""
        self.cb.check(portfolio_drop=0.08)  # LEVEL_3
        actions = self.cb.allowed_actions()
        self.assertFalse(actions["open_new"])
        self.assertTrue(actions["reduce"])
        self.assertTrue(actions["hedge"])
        self.assertGreater(actions["force_reduce_pct"], 0)


class TestHedging(unittest.TestCase):
    """对冲模块测试"""

    def setUp(self):
        from hedging.beta_hedger import BetaHedger
        from hedging.vol_hedger import VolHedger
        from hedging.correlation_hedger import CorrelationHedger
        self.BetaHedger = BetaHedger
        self.VolHedger = VolHedger
        self.CorrelationHedger = CorrelationHedger

    def test_beta_no_hedge(self):
        """Beta < 0.7 不对冲"""
        bh = self.BetaHedger()
        result = bh.compute_hedge(portfolio_beta=0.5, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "NO_HEDGE")

    def test_beta_hedge(self):
        """Beta > 0.7 触发对冲"""
        bh = self.BetaHedger()
        result = bh.compute_hedge(portfolio_beta=1.2, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "SHORT_FUTURES")
        self.assertGreater(result["contracts"], 0)
        self.assertEqual(result["instrument"], "IF")
        self.assertLess(result["target_beta"], 0.7)

    def test_vol_no_hedge(self):
        """VIX ≤ 30 不对冲"""
        vh = self.VolHedger()
        result = vh.compute_hedge(vix=20.0, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "NO_HEDGE")

    def test_vol_put_spread(self):
        """VIX 30-40 → Put Spread"""
        vh = self.VolHedger()
        result = vh.compute_hedge(vix=35.0, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "BUY_PUT_SPREAD")

    def test_vol_bare_put(self):
        """VIX 40-60 → Bare Put"""
        vh = self.VolHedger()
        result = vh.compute_hedge(vix=50.0, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "BUY_BARE_PUT")

    def test_vol_emergency(self):
        """VIX > 60 → Emergency Put"""
        vh = self.VolHedger()
        result = vh.compute_hedge(vix=70.0, portfolio_value=5_000_000)
        self.assertEqual(result["action"], "BUY_EMERGENCY_PUT")
        self.assertLess(result["actual_coverage"], 1.0)

    def test_corr_no_hedge(self):
        """低相关不对冲"""
        np.random.seed(42)
        returns = pd.DataFrame(
            np.random.normal(0, 0.02, (60, 3)), columns=["A", "B", "C"])
        ch = self.CorrelationHedger()
        result = ch.compute_hedge(returns, 5_000_000)
        self.assertEqual(result["action"], "NO_HEDGE")

    def test_corr_hedge(self):
        """高相关触发避险"""
        # 构造高相关数据
        np.random.seed(42)
        base = np.random.normal(0, 0.02, 60)
        returns = pd.DataFrame({
            "A": base,
            "B": base + np.random.normal(0, 0.001, 60),
            "C": base + np.random.normal(0, 0.001, 60),
        })
        ch = self.CorrelationHedger(corr_trigger=0.80, corr_jump_threshold=0.05)
        result = ch.compute_hedge(returns, 5_000_000)
        # 应触发 (相关系数接近 1)
        self.assertEqual(result["action"], "SAFE_HAVEN_ALLOC")


class TestExecution(unittest.TestCase):
    """执行模块测试"""

    def test_ntp_sync(self):
        """NTP 同步"""
        from execution.ntp_sync import NTPSync
        ntp = NTPSync()
        ts = ntp.server_ts()
        self.assertIsInstance(ts, datetime)

    def test_algo_iceberg(self):
        """Iceberg 拆单"""
        from execution.algo_engine import AlgoEngine, AlgoType
        algo = AlgoEngine()
        depth = {"bid1_vol": 10000, "ask1_vol": 10000,
                 "bid1": 10.0, "ask1": 10.02}
        slices = algo.split(5000, "BUY", AlgoType.ICEBERG, depth)
        self.assertGreater(len(slices), 0)
        self.assertEqual(sum(s.quantity for s in slices), 5000)

    def test_sor_execute(self):
        """SOR 执行"""
        from execution.smart_order_router import SmartOrderRouter, MockBroker
        from execution.algo_engine import AlgoType
        broker = MockBroker(price_dict={"TEST": 10.0})
        sor = SmartOrderRouter(broker)
        fills = sor.execute(symbol="TEST", target_qty=500,
                            side="BUY", decision_price=10.0,
                            algo=AlgoType.ICEBERG)
        self.assertIsInstance(fills, list)


class TestBacktest(unittest.TestCase):
    """回测模块测试"""

    def test_metrics(self):
        """指标计算"""
        from backtest.metrics import compute_all_metrics
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.001, 0.015, 252))
        m = compute_all_metrics(returns)
        self.assertIn("sortino", m)
        self.assertIn("calmar", m)
        self.assertIn("max_drawdown", m)
        self.assertGreater(m["sortino"], 0)

    def test_max_drawdown(self):
        """最大回撤"""
        from backtest.metrics import compute_max_drawdown
        equity = pd.Series([1.0, 1.1, 1.2, 1.05, 1.15, 1.0])
        dd, p, t = compute_max_drawdown(equity)
        self.assertGreater(dd, 0)
        self.assertEqual(p, 2)  # peak at index 2 (1.2)
        self.assertEqual(t, 5)  # trough at index 5 (1.0)

    def test_dsr(self):
        """Deflated Sharpe Ratio"""
        from backtest.metrics import compute_dsr
        dsr = compute_dsr(observed_sr=2.0, n_trials=10, t_obs=3)
        self.assertGreater(dsr, 0)
        self.assertLessEqual(dsr, 1.0)

    def test_walk_forward(self):
        """Walk-Forward"""
        from backtest.walk_forward import WalkForward
        data = pd.DataFrame({
            "date": pd.date_range("2022-07-01", periods=756, freq="D"),
            "returns": np.random.normal(0.0005, 0.015, 756),
        })

        def strategy_fn(train_df, test_df):
            return test_df["returns"]

        wf = WalkForward(train_months=24, test_months=3, step_months=3)
        result = wf.run(data, strategy_fn, date_col="date")
        self.assertEqual(result["status"], "OK")
        self.assertGreater(result.get("n_windows", 0), 0)

    def test_stress_scenarios(self):
        """压力测试场景"""
        from backtest.scenario_lib import StressScenarioLib, STRESS_SCENARIOS
        self.assertEqual(len(STRESS_SCENARIOS), 3)
        names = [s.name for s in STRESS_SCENARIOS]
        self.assertIn("COVID_CRASH", names)
        self.assertIn("LUNA_CRASH", names)
        self.assertIn("YEN_CARRY", names)

    def test_cost_model(self):
        """成本模型"""
        from backtest.cost_model import CostModel
        cm = CostModel()
        cost = cm.trade_cost("600000", 10000, 10.0, "BUY", "stock",
                              adv=1_000_000, volatility=0.25)
        self.assertGreater(cost["commission"], 0)
        self.assertGreater(cost["slippage"], 0)
        self.assertEqual(cost["stamp_duty"], 0)  # 买入无印花税

    def test_cost_model_sell(self):
        """卖出有印花税"""
        from backtest.cost_model import CostModel
        cm = CostModel()
        cost = cm.trade_cost("600000", 10000, 10.0, "SELL", "stock")
        self.assertGreater(cost["stamp_duty"], 0)


def run_all():
    """运行所有测试"""
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    for cls in [TestRiskManager, TestCircuitBreaker, TestHedging,
                TestExecution, TestBacktest]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
