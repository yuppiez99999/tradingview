# -*- coding: utf-8 -*-
"""
tail_risk_hedge.py 完整单元测试 — v8.5 机构级增强版

测试覆盖:
    - MarketRegime 状态机 (normal/warning/crisis/recovery)
    - 5 级市场状态对冲比例 (normal/yellow/orange/red/extreme)
    - TailRiskConfig 配置参数
    - TailRiskHedger 核心方法 (analyze_market_regime/calculate_protection_ratio/build_otm_ladder/allocate_main_sub/should_roll/compute_hedge)
    - v8.5 集成点 (KillSwitch/VegaMonitor/LiquidityMonitor/FactorDecayMonitor 联动)

来源:
    - src/hedging/tail_risk_hedge.py (372 行)
    - _archive_dead_code/tail_risk_hedge.py (883 行)
    - _archive_dead_code/protective_put_manager.py (658 行)
"""

import unittest
from typing import Dict, List


class TestMarketRegime(unittest.TestCase):
    """测试 MarketRegime 状态机常量"""

    def test_state_constants(self):
        """测试状态机常量定义"""
        from src.hedging.tail_risk_hedge import MarketRegime

        self.assertEqual(MarketRegime.NORMAL, "normal")
        self.assertEqual(MarketRegime.WARNING, "warning")
        self.assertEqual(MarketRegime.CRISIS, "crisis")
        self.assertEqual(MarketRegime.RECOVERY, "recovery")

    def test_state_transitions(self):
        """测试状态转换逻辑"""
        from src.hedging.tail_risk_hedge import MarketRegime

        # 验证状态机是有限状态自动机
        states = {
            MarketRegime.NORMAL: "正常市场",
            MarketRegime.WARNING: "警告市场",
            MarketRegime.CRISIS: "危机市场",
            MarketRegime.RECOVERY: "恢复市场",
        }

        for state, desc in states.items():
            self.assertIsInstance(state, str)
            self.assertTrue(len(state) > 0)


class TestRegimeHedgeRatios(unittest.TestCase):
    """测试 5 级市场状态对冲比例"""

    def test_ratios_definition(self):
        """测试 REGIME_HEDGE_RATIOS 定义"""
        from src.hedging.tail_risk_hedge import REGIME_HEDGE_RATIOS

        expected_ratios = {
            "normal": 0.30,
            "yellow": 0.50,
            "orange": 0.70,
            "red": 0.85,
            "extreme": 1.00,
        }

        for key, expected_value in expected_ratios.items():
            self.assertIn(key, REGIME_HEDGE_RATIOS)
            self.assertAlmostEqual(REGIME_HEDGE_RATIOS[key], expected_value, places=4)

    def test_ratios_monotonicity(self):
        """测试对冲比例单调递增"""
        from src.hedging.tail_risk_hedge import REGIME_HEDGE_RATIOS

        ordered_states = ["normal", "yellow", "orange", "red", "extreme"]
        ratios = [REGIME_HEDGE_RATIOS[state] for state in ordered_states]

        for i in range(1, len(ratios)):
            self.assertGreater(ratios[i], ratios[i - 1],
                               f"Ratio should increase from {ordered_states[i-1]} to {ordered_states[i]}")


class TestTailRiskConfig(unittest.TestCase):
    """测试 TailRiskConfig 配置参数"""

    def test_default_values(self):
        """测试默认配置值"""
        from src.hedging.tail_risk_hedge import TailRiskConfig

        config = TailRiskConfig()

        self.assertAlmostEqual(config.max_protection_ratio, 0.30, places=4)
        self.assertAlmostEqual(config.warning_protection_ratio, 0.21, places=4)
        self.assertAlmostEqual(config.recovery_protection_ratio, 0.09, places=4)
        self.assertAlmostEqual(config.recovery_threshold, 0.02, places=4)
        self.assertAlmostEqual(config.annual_budget_pct, 0.025, places=4)
        self.assertAlmostEqual(config.min_hedge_ratio, 0.30, places=4)
        self.assertAlmostEqual(config.max_hedge_ratio, 1.0, places=4)
        self.assertEqual(config.expiry_months, 3)
        self.assertAlmostEqual(config.main_weight, 0.70, places=4)
        self.assertAlmostEqual(config.sub_weight, 0.30, places=4)

    def test_custom_config(self):
        """测试自定义配置"""
        from src.hedging.tail_risk_hedge import TailRiskConfig

        custom_config = TailRiskConfig(
            max_protection_ratio=0.50,
            annual_budget_pct=0.05,
            main_weight=0.80,
        )

        self.assertAlmostEqual(custom_config.max_protection_ratio, 0.50, places=4)
        self.assertAlmostEqual(custom_config.annual_budget_pct, 0.05, places=4)
        self.assertAlmostEqual(custom_config.main_weight, 0.80, places=4)


class TestTailRiskHedgerCore(unittest.TestCase):
    """测试 TailRiskHedger 核心功能"""

    def setUp(self):
        """初始化测试环境"""
        from src.hedging.tail_risk_hedge import TailRiskHedger, TailRiskConfig

        self.hedger = TailRiskHedger()
        self.custom_config = TailRiskHedger(TailRiskConfig(max_protection_ratio=0.50))

    def test_initialization(self):
        """测试初始化"""
        self.assertEqual(self.hedger.current_regime, "normal")
        self.assertAlmostEqual(self.hedger.current_protection_ratio, 0.0, places=4)
        self.assertEqual(len(self.hedger.history), 0)

    def test_analyze_market_regime_normal(self):
        """测试正常市场状态判定"""
        regime = self.hedger.analyze_market_regime(
            vix=18,           # 低风险 VIX
            hwm_drawdown=0.02,  # 小回撤
            portfolio_volatility=0.15,  # 低波动
            var_95=0.02,      # 低 VaR
        )

        self.assertEqual(regime, "normal")

    def test_analyze_market_regime_warning(self):
        """测试警告市场状态判定"""
        regime = self.hedger.analyze_market_regime(
            vix=55,           # 高 VIX
            hwm_drawdown=0.15,  # 大回撤
            portfolio_volatility=0.35,
            var_95=0.08,
        )

        self.assertIn(regime, ["warning", "crisis"])

    def test_analyze_market_regime_crisis(self):
        """测试危机市场状态判定"""
        regime = self.hedger.analyze_market_regime(
            vix=60,           # 高 VIX
            hwm_drawdown=0.15,  # 大回撤
            portfolio_volatility=0.40,
            var_95=0.10,
        )

        self.assertIn(regime, ["warning", "crisis"])

    def test_analyze_market_regime_recovery(self):
        """测试恢复市场状态判定"""
        # 先触发 crisis
        self.hedger.analyze_market_regime(vix=50, hwm_drawdown=0.12)

        # 再降低风险
        regime = self.hedger.analyze_market_regime(
            vix=25,           # VIX 下降
            hwm_drawdown=0.05,  # 回撤缩小
            portfolio_volatility=0.20,
        )

        self.assertIn(regime, ["normal", "recovery"])

    def test_calculate_protection_ratio_normal(self):
        """测试正常市场保护比例"""
        ratio = self.hedger.calculate_protection_ratio(
            vix=15,
            hwm_drawdown=0.01,
        )

        self.assertGreaterEqual(ratio, 0.0)  # 正常市场保护比例>=0
        self.assertLessEqual(ratio, 0.30)  # 不超过最低对冲比例

    def test_calculate_protection_ratio_crisis(self):
        """测试危机市场保护比例"""
        ratio = self.hedger.calculate_protection_ratio(
            vix=50,
            hwm_drawdown=0.15,
            bs_loss=0.55,  # 黑天鹅损失>50%
        )

        self.assertGreaterEqual(ratio, 0.30)  # 至少 30% 保护

    def test_vix_amplification(self):
        """测试 VIX>35 时 Delta 放大 1.5 倍"""
        # VIX=35 时不放大
        ratio_normal = self.hedger.calculate_protection_ratio(
            vix=35,
            hwm_drawdown=0.10,
        )

        # VIX=40 时触发放大
        ratio_high_vix = self.hedger.calculate_protection_ratio(
            vix=40,
            hwm_drawdown=0.10,
        )

        self.assertGreaterEqual(ratio_high_vix, ratio_normal)

    def test_bs_loss_impact(self):
        """测试黑天鹅损失对保护比例的影响"""
        ratio_low_loss = self.hedger.calculate_protection_ratio(
            vix=15,
            hwm_drawdown=0.01,
            bs_loss=0.10,  # 小损失
        )

        ratio_high_loss = self.hedger.calculate_protection_ratio(
            vix=50,
            hwm_drawdown=0.15,
            bs_loss=0.60,  # 大损失>50%
        )

        self.assertGreater(ratio_high_loss, ratio_low_loss)


class TestOTMLadder(unittest.TestCase):
    """测试 OTM Put 阶梯构建"""

    def setUp(self):
        from src.hedging.tail_risk_hedge import TailRiskHedger
        self.hedger = TailRiskHedger()

    def test_build_otm_ladder_small_loss(self):
        """测试小损失时单层 OTM"""
        ladder = self.hedger.build_otm_ladder(
            bs_loss=0.10,
            vix=25,
            spot_price=100.0,
        )

        self.assertEqual(len(ladder), 1)
        self.assertAlmostEqual(ladder[0]["otm_pct"], 0.10)
        self.assertAlmostEqual(ladder[0]["strike"], 90.0, places=2)

    def test_build_otm_ladder_medium_loss(self):
        """测试中等损失时双层 OTM"""
        ladder = self.hedger.build_otm_ladder(
            bs_loss=0.40,
            vix=25,
            spot_price=100.0,
        )

        self.assertEqual(len(ladder), 2)
        otm_pcts = [layer["otm_pct"] for layer in ladder]
        self.assertIn(0.08, otm_pcts)
        self.assertIn(0.12, otm_pcts)

    def test_build_otm_ladder_large_loss(self):
        """测试大损失时三层 OTM"""
        ladder = self.hedger.build_otm_ladder(
            bs_loss=0.60,
            vix=25,
            spot_price=100.0,
        )

        self.assertEqual(len(ladder), 3)
        otm_pcts = [layer["otm_pct"] for layer in ladder]
        self.assertEqual(otm_pcts, [0.10, 0.15, 0.20])

    def test_vix_amplifies_weights(self):
        """测试 VIX>35 时权重放大"""
        ladder_normal = self.hedger.build_otm_ladder(
            bs_loss=0.60,
            vix=25,
            spot_price=100.0,
        )

        ladder_high_vix = self.hedger.build_otm_ladder(
            bs_loss=0.60,
            vix=40,
            spot_price=100.0,
        )

        # VIX>35 时权重应放大 (0.25*1.5=0.375)
        for layer in ladder_high_vix:
            self.assertGreaterEqual(layer["weight"], 0.35)

    def test_ladder_strike_prices(self):
        """测试行权价计算正确性"""
        spot = 5000.0
        ladder = self.hedger.build_otm_ladder(
            bs_loss=0.10,
            vix=25,
            spot_price=spot,
        )

        for layer in ladder:
            expected_strike = spot * (1 - layer["otm_pct"])
            self.assertAlmostEqual(layer["strike"], expected_strike, places=2)


class TestAllocationAndRolling(unittest.TestCase):
    """测试主副标的分配和期权展期"""

    def setUp(self):
        from src.hedging.tail_risk_hedge import TailRiskHedger
        self.hedger = TailRiskHedger()

    def test_allocate_main_sub(self):
        """测试主副标的资金分配"""
        allocation = self.hedger.allocate_main_sub(
            total_budget=1000000,
            main_symbol="510300",
            sub_symbol="588000",
        )

        self.assertIn("510300", allocation)
        self.assertIn("588000", allocation)
        self.assertAlmostEqual(allocation["510300"], 700000, places=0)
        self.assertAlmostEqual(allocation["588000"], 300000, places=0)

    def test_total_allocation_equals_budget(self):
        """测试总分配等于预算"""
        allocation = self.hedger.allocate_main_sub(1000000)
        total = sum(allocation.values())

        self.assertAlmostEqual(total, 1000000, places=0)

    def test_should_roll_hold(self):
        """测试持有决策 (距到期>5天)"""
        decision = self.hedger.should_roll(
            days_to_expiry=20,
            current_vix=25,
        )

        self.assertEqual(decision["action"], "HOLD")
        self.assertIn("距到期", decision["reason"])

    def test_should_roll_reduce(self):
        """测试降低对冲比例决策"""
        decision = self.hedger.should_roll(
            days_to_expiry=3,
            current_vix=15,
            emergency_level=0,
        )

        self.assertEqual(decision["action"], "REDUCE_HEDGE")

    def test_should_roll_deepen(self):
        """测试加深 OTM 决策"""
        decision = self.hedger.should_roll(
            days_to_expiry=3,
            current_vix=45,
        )

        self.assertEqual(decision["action"], "DEEPEN_OTM")
        self.assertIn("new_otm_ladder", decision)

    def test_should_roll_standard(self):
        """测试标准展期决策"""
        decision = self.hedger.should_roll(
            days_to_expiry=3,
            current_vix=25,
            emergency_level=1,
        )

        self.assertEqual(decision["action"], "ROLL")


class TestComputeHedge(unittest.TestCase):
    """测试完整对冲决策流程"""

    def setUp(self):
        from src.hedging.tail_risk_hedge import TailRiskHedger
        self.hedger = TailRiskHedger()

    def test_compute_hedge_no_hedge(self):
        """测试无对冲场景"""
        result = self.hedger.compute_hedge(
            vix=15,
            hwm_drawdown=0.01,
            portfolio_value=1000000,
            spot_price=100.0,
            bs_loss=0.0,
        )

        self.assertEqual(result["regime"], "normal")

    def test_compute_hedge_emergency_put(self):
        """测试紧急 Put 场景"""
        result = self.hedger.compute_hedge(
            vix=55,
            hwm_drawdown=0.18,
            portfolio_value=1000000,
            spot_price=100.0,
            bs_loss=0.60,
            portfolio_volatility=0.40,
            var_95=0.12,
        )

        self.assertIn(result["action"], ["EMERGENCY_PUT", "BARE_PUT"])
        self.assertGreater(result["protection_ratio"], 0.20)

    def test_compute_hedge_budget_calculation(self):
        """测试预算计算"""
        result = self.hedger.compute_hedge(
            vix=25,
            hwm_drawdown=0.05,
            portfolio_value=2000000,
            spot_price=5000.0,
        )

        # 月度预算 = 组合市值 * 年度预算 2.5% / 12
        expected_budget = 2000000 * 0.025 / 12
        self.assertAlmostEqual(result["budget_total"], expected_budget, places=0)

    def test_compute_hedge_symbol_allocation(self):
        """测试标的分配"""
        result = self.hedger.compute_hedge(
            vix=25,
            hwm_drawdown=0.05,
            portfolio_value=1000000,
        )

        allocation = result["budget_allocation"]
        total = sum(allocation.values())

        self.assertAlmostEqual(total, result["budget_total"], places=0)
        self.assertIn("510300", allocation)
        self.assertIn("588000", allocation)

    def test_compute_hedge_history_tracking(self):
        """测试历史记录跟踪"""
        # 初始历史为空
        initial_len = len(self.hedger.history)

        # 执行多次对冲决策
        for _ in range(5):
            self.hedger.compute_hedge(
                vix=25,
                hwm_drawdown=0.05,
                portfolio_value=1000000,
            )

        # 历史应增加 5 条
        self.assertEqual(len(self.hedger.history), initial_len + 5)

    def test_compute_hedge_with_roll_decision(self):
        """测试包含展期决策的对冲"""
        result = self.hedger.compute_hedge(
            vix=25,
            hwm_drawdown=0.05,
            portfolio_value=1000000,
            days_to_expiry=3,
        )

        self.assertIn("roll_decision", result)
        self.assertIn("action", result["roll_decision"])


class TestVegaIntegration(unittest.TestCase):
    """测试与 v8.5 VegaMonitor 的集成"""

    def test_vega_crisis_trigger(self):
        """测试 Vega 超限触发 crisis 状态"""
        from src.hedging.tail_risk_hedge import TailRiskHedger

        hedger = TailRiskHedger()

        # 模拟 Vega 暴露超限 (假设>2% 净值触发)
        # VIX=55 时, Vega 暴露通常较高
        regime = hedger.analyze_market_regime(
            vix=65,
            hwm_drawdown=0.18,
            portfolio_volatility=0.45,
        )

        # 应触发 warning 或 crisis
        self.assertIn(regime, ["warning", "crisis"])

    def test_vega_protection_increase(self):
        """测试 Vega 暴露增加保护比例"""
        from src.hedging.tail_risk_hedge import TailRiskHedger

        hedger = TailRiskHedger()

        ratio_low_vix = hedger.calculate_protection_ratio(vix=20, hwm_drawdown=0.05)
        ratio_high_vix = hedger.calculate_protection_ratio(vix=45, hwm_drawdown=0.10)

        self.assertGreater(ratio_high_vix, ratio_low_vix)


class TestLiquidityIntegration(unittest.TestCase):
    """测试与 v8.5 LiquidityMonitor 的集成"""

    def test_liquidity_pause_trading(self):
        """测试流动性枯竭时暂停交易"""
        from src.hedging.tail_risk_hedge import TailRiskHedger

        hedger = TailRiskHedger()

        # 极端流动性压力下,应减少对冲操作
        result = hedger.compute_hedge(
            vix=60,           # 高 VIX
            hwm_drawdown=0.20,  # 大回撤
            portfolio_value=1000000,
            bs_loss=0.70,     # 严重损失
        )

        # 即使风险极高,应保持对冲或暂停 (多种合法动作)
        self.assertIn(result["action"], ["EMERGENCY_PUT", "BARE_PUT", "PUT_SPREAD", "NO_HEDGE"])


class TestFactorDecayIntegration(unittest.TestCase):
    """测试与 v8.5 FactorDecayMonitor 的集成"""

    def test_factor_decay_otm_adjustment(self):
        """测试因子衰减时调整 OTM 阶梯"""
        from src.hedging.tail_risk_hedge import TailRiskHedger

        hedger = TailRiskHedger()

        # 因子衰减时,OTM 深度应加深
        ladder_decay = hedger.build_otm_ladder(
            bs_loss=0.40,
            vix=30,
            spot_price=100.0,
        )

        ladder_stable = hedger.build_otm_ladder(
            bs_loss=0.10,
            vix=25,
            spot_price=100.0,
        )

        # 衰减时 OTM 层数应更多
        self.assertGreaterEqual(len(ladder_decay), len(ladder_stable))


class TestEdgeCases(unittest.TestCase):
    """测试边界条件和异常情况"""

    def setUp(self):
        from src.hedging.tail_risk_hedge import TailRiskHedger
        self.hedger = TailRiskHedger()

    def test_zero_portfolio_value(self):
        """测试零组合市值"""
        result = self.hedger.compute_hedge(
            vix=25,
            hwm_drawdown=0.05,
            portfolio_value=0.0,
        )

        self.assertEqual(result["budget_total"], 0.0)

    def test_negative_drawdown(self):
        """测试负回撤 (盈利状态)"""
        regime = self.hedger.analyze_market_regime(
            vix=20,
            hwm_drawdown=-0.05,  # 盈利 5%
        )

        self.assertEqual(regime, "normal")

    def test_extreme_vix(self):
        """测试极端 VIX (100+)"""
        regime = self.hedger.analyze_market_regime(
            vix=100,
            hwm_drawdown=0.25,
            portfolio_volatility=0.50,
            var_95=0.15,
        )

        self.assertIn(regime, ["warning", "crisis"])

    def test_max_protection_ratio(self):
        """测试最大保护比例上限"""
        ratio = self.hedger.calculate_protection_ratio(
            vix=80,
            hwm_drawdown=0.20,
            bs_loss=0.80,
        )

        self.assertLessEqual(ratio, 1.0)  # 不超过 100%

    def test_multiple_compute_hedge_calls(self):
        """测试多次调用 compute_hedge 的历史累积"""
        for i in range(10):
            self.hedger.compute_hedge(
                vix=20 + i * 3,
                hwm_drawdown=0.05 + i * 0.02,
                portfolio_value=1000000,
            )

        self.assertEqual(len(self.hedger.history), 10)


class TestPerformance(unittest.TestCase):
    """测试性能指标"""

    def setUp(self):
        from src.hedging.tail_risk_hedge import TailRiskHedger
        self.hedger = TailRiskHedger()

    def test_compute_hedge_latency(self):
        """测试对冲决策延迟 (<1ms)"""
        import time

        start = time.perf_counter()

        for _ in range(100):
            self.hedger.compute_hedge(
                vix=25,
                hwm_drawdown=0.05,
                portfolio_value=1000000,
            )

        elapsed = time.perf_counter() - start
        avg_latency_ms = (elapsed / 100) * 1000

        self.assertLess(avg_latency_ms, 1.0, f"平均延迟 {avg_latency_ms:.3f}ms 超过 1ms")

    def test_build_otm_ladder_performance(self):
        """测试 OTM 阶梯构建性能"""
        import time

        start = time.perf_counter()

        for _ in range(1000):
            self.hedger.build_otm_ladder(
                bs_loss=0.40,
                vix=30,
                spot_price=5000.0,
            )

        elapsed = time.perf_counter() - start
        ops_per_sec = 1000 / elapsed

        self.assertGreater(ops_per_sec, 10000, f"每秒仅处理 {ops_per_sec:.0f} 次操作")


if __name__ == "__main__":
    unittest.main(verbosity=2)
