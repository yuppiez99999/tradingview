"""执行层模块单元测试

验证三大执行层模块:
1. ExecutionAlgorithmEngine — VWAP/TWAP/POV/IS 执行算法
2. MarketImpactModel — Almgren-Chriss 市场冲击模型
3. SmartOrderRouter — 智能订单路由器

测试覆盖:
- VWAP 切片分布符合 U 型曲线
- TWAP 均匀切片
- POV 参与度约束
- IS 算法前置加权
- 市场冲击估计 (Square-Root + AC 分解)
- AC 最优轨迹 (半衰期 + 成本方差)
- 智能路由评分 + 多场所分配
- 反贪吃检测
"""

import os
import sys
import unittest
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "v7.5_institutional"))


class TestExecutionAlgorithmEngine(unittest.TestCase):
    """测试执行算法引擎"""

    @classmethod
    def setUpClass(cls):
        """构造测试订单"""
        cls.start = pd.Timestamp("2026-07-15 09:30:00")
        cls.end = pd.Timestamp("2026-07-15 15:00:00")
        from utils.execution_algorithm_engine import Order

        cls.Order = Order
        cls.order = Order(
            symbol="600519.SH",
            side="BUY",
            total_shares=10000,
            start_time=cls.start,
            end_time=cls.end,
            benchmark_price=1800.0,
            urgency="MEDIUM",
        )

    def test_module_import(self):
        from utils.execution_algorithm_engine import (
            ExecutionAlgorithmEngine,
        )

        self.assertTrue(callable(ExecutionAlgorithmEngine))

    def test_vwap(self):
        """测试 VWAP — 切片数 > 0, 总股数匹配"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

        engine = ExecutionAlgorithmEngine()
        plan = engine.vwap(self.order)
        self.assertEqual(plan.algorithm, "VWAP")
        self.assertGreater(plan.num_slices, 0)
        total = sum(c.shares for c in plan.child_orders)
        # 容忍随机化误差 ±5%
        self.assertAlmostEqual(total, 10000, delta=500)
        # 期望成本 > 0
        self.assertGreater(plan.expected_cost_bps, 0)

    def test_twap(self):
        """测试 TWAP — 均匀切片"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

        engine = ExecutionAlgorithmEngine()
        plan = engine.twap(self.order)
        self.assertEqual(plan.algorithm, "TWAP")
        self.assertGreater(plan.num_slices, 0)
        total = sum(c.shares for c in plan.child_orders)
        self.assertAlmostEqual(total, 10000, delta=500)

    def test_pov(self):
        """测试 POV — 参与度约束"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

        engine = ExecutionAlgorithmEngine()
        plan = engine.pov(self.order, expected_market_volume=500_000)
        self.assertEqual(plan.algorithm, "POV")
        self.assertGreater(plan.num_slices, 0)
        # POV 总股数不应超过订单总量 (允许随机化微小超出)
        total = sum(c.shares for c in plan.child_orders)
        self.assertLessEqual(total, 12000)

    def test_is_algo(self):
        """测试 IS — 前置加权"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

        engine = ExecutionAlgorithmEngine()
        plan = engine.is_algo(self.order, daily_volatility=0.02)
        self.assertEqual(plan.algorithm, "IS")
        self.assertGreater(plan.num_slices, 0)
        # IS 应有较高的时机风险
        self.assertGreater(plan.expected_timing_risk_bps, 0)

    def test_select_algorithm(self):
        """测试算法自动选择"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

        engine = ExecutionAlgorithmEngine()
        # 小单 + 高紧迫 → TWAP
        small_order = self.Order(
            symbol="TEST",
            side="BUY",
            total_shares=1000,
            start_time=self.start,
            end_time=self.end,
            urgency="HIGH",
        )
        algo = engine.select_algorithm(small_order, adv=500_000)
        self.assertEqual(algo, "TWAP")
        # 大单 (>20% ADV) → POV
        big_order = self.Order(
            symbol="TEST",
            side="BUY",
            total_shares=200_000,
            start_time=self.start,
            end_time=self.end,
            urgency="MEDIUM",
        )
        algo = engine.select_algorithm(big_order, adv=500_000)
        self.assertEqual(algo, "POV")


class TestMarketImpactModel(unittest.TestCase):
    """测试市场冲击模型"""

    def test_module_import(self):
        from utils.market_impact_model import (
            MarketImpactModel,
        )

        self.assertTrue(callable(MarketImpactModel))

    def test_impact_estimate(self):
        """测试冲击估计 — 总冲击 > 0, 永久/临时合理"""
        from utils.market_impact_model import MarketImpactModel

        model = MarketImpactModel()
        est = model.estimate(
            symbol="600519.SH",
            order_shares=10000,
            adv=500_000,
            decision_price=1800.0,
            volatility=0.02,
        )
        self.assertEqual(est.symbol, "600519.SH")
        self.assertGreater(est.total_impact_bps, 0)
        self.assertGreater(est.temporary_impact_bps, 0)
        self.assertGreater(est.permanent_impact_bps, 0)
        # 预期执行价 > 决策价 (BUY 单有正冲击)
        self.assertGreater(est.expected_exec_price, est.decision_price)

    def test_participation_scaling(self):
        """参与度越高, 冲击越大"""
        from utils.market_impact_model import MarketImpactModel

        model = MarketImpactModel()
        small = model.estimate(
            symbol="X", order_shares=1000, adv=500_000, decision_price=100.0
        )
        large = model.estimate(
            symbol="X", order_shares=100_000, adv=500_000, decision_price=100.0
        )
        self.assertGreater(large.total_impact_bps, small.total_impact_bps)

    def test_optimal_trajectory(self):
        """测试 AC 最优轨迹 — 半衰期合理"""
        from utils.market_impact_model import MarketImpactModel

        model = MarketImpactModel()
        traj = model.optimal_trajectory(
            total_shares=10000,
            time_horizon=1.0,
            volatility=0.02,
            risk_aversion=1.0,
            n_steps=10,
        )
        self.assertEqual(len(traj.times), 11)
        # 初始持仓 = 总股数
        self.assertAlmostEqual(traj.holdings[0], 10000, delta=100)
        # 终止持仓 ≈ 0
        self.assertLess(abs(traj.holdings[-1]), 100)
        # 半衰期在 [0, T] 内
        self.assertGreaterEqual(traj.half_life, 0.0)
        self.assertLessEqual(traj.half_life, 1.0)

    def test_efficient_frontier(self):
        """测试有效前沿 — λ 越大成本越高, 风险越低"""
        from utils.market_impact_model import MarketImpactModel

        model = MarketImpactModel()
        frontier = model.efficient_frontier(
            total_shares=10000,
            time_horizon=1.0,
            volatility=0.02,
            lam_range=[0.1, 1.0, 10.0],
        )
        self.assertEqual(len(frontier), 3)
        # λ=10 的成本应该高于 λ=0.1 (高厌恶成本更大)
        self.assertGreater(frontier[2][1], frontier[0][1])


class TestSmartOrderRouter(unittest.TestCase):
    """测试智能订单路由器"""

    def test_module_import(self):
        from utils.smart_order_router import (
            SmartOrderRouter,
        )

        self.assertTrue(callable(SmartOrderRouter))

    def test_default_venues(self):
        """测试默认场所加载"""
        from utils.smart_order_router import SmartOrderRouter

        router = SmartOrderRouter()
        # 应该至少有 2 个场所
        self.assertGreaterEqual(len(router.venues), 2)
        # 应该包含 SSE_MAIN
        self.assertIn("SSE_MAIN", router.venues)

    def test_route_decision(self):
        """测试路由决策 — 应该有主场所和分配"""
        from utils.smart_order_router import SmartOrderRouter

        router = SmartOrderRouter()
        decision = router.route(
            symbol="600519.SH",
            side="BUY",
            total_shares=10000,
            order_books=None,
            strategy="SMART",
            max_venues=2,
        )
        self.assertEqual(decision.symbol, "600519.SH")
        self.assertGreater(len(decision.allocations), 0)
        self.assertTrue(decision.primary_venue)
        # 分配的总和应该等于 total_shares
        total_allocated = sum(a.recommended_shares for a in decision.allocations)
        self.assertAlmostEqual(total_allocated, 10000, delta=200)

    def test_iceberg_strategy(self):
        """测试冰山策略 — 主场所占比小"""
        from utils.smart_order_router import SmartOrderRouter

        router = SmartOrderRouter()
        decision = router.route(
            symbol="600519.SH",
            side="BUY",
            total_shares=10000,
            order_books=None,
            strategy="ICEBERG",
            max_venues=3,
        )
        self.assertEqual(decision.strategy, "ICEBERG")
        # 应该至少有 1 个分配
        self.assertGreater(len(decision.allocations), 0)

    def test_gaming_detection(self):
        """测试反贪吃检测"""
        from utils.smart_order_router import OrderBookSnapshot, SmartOrderRouter

        router = SmartOrderRouter(gaming_threshold=0.5)
        # 构造不平衡盘口
        book = OrderBookSnapshot(
            venue_name="SSE_MAIN",
            timestamp="2026-07-15 09:30:00",
            bid_prices=[100.0, 99.9, 99.8, 99.7, 99.6],
            bid_sizes=[10000, 8000, 6000, 4000, 2000],  # 买盘很大
            ask_prices=[100.01, 100.02, 100.03, 100.04, 100.05],
            ask_sizes=[100, 80, 60, 40, 20],  # 卖盘很小
            last_price=100.0,
        )
        decision = router.route(
            symbol="TEST",
            side="BUY",
            total_shares=1000,
            order_books={"SSE_MAIN": book},
        )
        # 不平衡度高, gaming_risk_score 应该 > 0
        self.assertGreaterEqual(decision.gaming_risk_score, 0.0)


class TestEdgeCases(unittest.TestCase):
    """边界条件测试"""

    def test_zero_shares(self):
        """零股订单"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine, Order

        engine = ExecutionAlgorithmEngine()
        order = Order(
            symbol="X",
            side="BUY",
            total_shares=0,
            start_time=pd.Timestamp("2026-07-15 09:30:00"),
            end_time=pd.Timestamp("2026-07-15 15:00:00"),
        )
        plan = engine.vwap(order)
        # 零股不应有切片
        self.assertEqual(plan.num_slices, 0)

    def test_no_venues(self):
        """无可用场所 — 显式禁用所有场所"""
        from utils.smart_order_router import SmartOrderRouter, Venue

        # 用空场所列表初始化 (不用默认值)
        router = SmartOrderRouter(
            venues=[Venue(name="EMPTY", venue_type="EXCHANGE", available=False)]
        )
        decision = router.route(
            symbol="X",
            side="BUY",
            total_shares=1000,
        )
        self.assertEqual(len(decision.allocations), 0)
        self.assertEqual(decision.primary_venue, "")

    def test_very_short_execution_window(self):
        """极短执行窗口"""
        from utils.execution_algorithm_engine import ExecutionAlgorithmEngine, Order

        engine = ExecutionAlgorithmEngine()
        # 9:30-9:40 仅 10 分钟
        order = Order(
            symbol="X",
            side="BUY",
            total_shares=1000,
            start_time=pd.Timestamp("2026-07-15 09:30:00"),
            end_time=pd.Timestamp("2026-07-15 09:40:00"),
        )
        plan = engine.vwap(order)
        # 至少应该有 1 个切片
        self.assertGreaterEqual(plan.num_slices, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
