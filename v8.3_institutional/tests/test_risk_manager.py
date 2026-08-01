"""
v7.5 测试：RiskManager — 三级回撤 + Kelly + Risk Parity
"""
import os
import sys
import unittest
from datetime import datetime

import numpy as np
import pandas as pd

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from risk.risk_budgeter import RiskBudgeter
from risk.risk_manager import RiskManager


class TestRiskBudgeter(unittest.TestCase):
    """RiskBudgeter 单元测试"""

    def setUp(self):
        self.budgeter = RiskBudgeter(total_capital=5_000_000)

    def test_initial_state(self):
        self.assertEqual(self.budgeter.mode, "NORMAL")
        self.assertEqual(self.budgeter.position_multiplier, 1.0)

    def test_normal_drawdown(self):
        result = self.budgeter.update_drawdown(5_000_000, pd.Timestamp('2026-07-05'))
        self.assertEqual(result, "NORMAL")
        self.assertEqual(self.budgeter.mode, "NORMAL")

    def test_defense_trigger(self):
        # 8% 回撤 → DEFENSE
        result = self.budgeter.update_drawdown(4_600_000, pd.Timestamp('2026-07-05'))
        self.assertEqual(result, "HALVE_POSITION")
        self.assertEqual(self.budgeter.mode, "DEFENSE")
        self.assertEqual(self.budgeter.position_multiplier, 0.5)

    def test_defense_hold(self):
        # 先进入 DEFENSE，再来一次信号
        self.budgeter.update_drawdown(4_600_000, pd.Timestamp('2026-07-05'))
        result = self.budgeter.update_drawdown(4_500_000, pd.Timestamp('2026-07-06'))
        self.assertEqual(result, "DEFENSE_HOLD")

    def test_circuit_breaker(self):
        result = self.budgeter.update_drawdown(4_500_000, pd.Timestamp('2026-07-05'))
        self.assertEqual(result, "CIRCUIT_BREAKER")
        self.assertEqual(self.budgeter.mode, "CIRCUIT_BREAKER")
        self.assertAlmostEqual(self.budgeter.position_multiplier, 0.0)

    def test_kelly_weight(self):
        w = self.budgeter.kelly_weight(
            mu_hist=0.12, sigma=0.25, beta=1.0, n=252
        )
        # 应在 (0, 0.20] 范围内
        self.assertGreater(w, 0)
        self.assertLessEqual(w, 0.20)

    def test_kelly_single_trade_cap(self):
        """单笔风险硬约束：w 不可超过 1.5% 风险限制"""
        w = self.budgeter.kelly_weight(
            mu_hist=0.50, sigma=0.80, beta=1.5, n=252
        )
        # 高波动 + 高 Beta → 权重应被硬约束压低
        self.assertLessEqual(w, 0.20)

    def test_risk_parity_weights(self):
        """Risk Parity 权重：等风险贡献"""
        np.random.seed(42)
        returns = np.random.randn(252, 5) * 0.02
        cov = np.cov(returns.T)
        weights = self.budgeter.risk_parity_weights(pd.DataFrame(cov))
        self.assertEqual(len(weights), 5)
        self.assertAlmostEqual(weights.sum(), 1.0, delta=0.01)
        self.assertTrue(np.all(weights > 0))

    def test_hwm_window_length(self):
        """HWM 窗口不超过 60"""
        for i in range(100):
            self.budgeter.update_drawdown(5_000_000 + i * 1000, pd.Timestamp('2026-07-05'))
        self.assertLessEqual(len(self.budgeter.hwm_window), 60)


class TestRiskManager(unittest.TestCase):
    """RiskManager 集成测试"""

    def setUp(self):
        self.rm = RiskManager(total_capital=5_000_000)

    def test_normal_cycle(self):
        """正常风控周期"""
        result = self.rm.run_risk_cycle(
            equity=5_000_000,
            positions={'600519.SH': 10000},
            market_data=pd.DataFrame(),
            vix=20,
            ts=datetime.now()
        )
        self.assertEqual(result.get('mode'), 'NORMAL')
        self.assertIn('budget_report', result)

    def test_hedge_when_high_beta(self):
        """高 Beta → 触发对冲"""
        actions = self.rm.auto_hedge(
            portfolio_beta=0.85,
            vix_level=20,
            avg_correlation=0.4
        )
        self.assertTrue(any(a['type'] == 'BETA_HEDGE' for a in actions))

    def test_vol_hedge_trigger(self):
        """VIX > 30 → 触发波动率对冲"""
        actions = self.rm.auto_hedge(
            portfolio_beta=0.3,
            vix_level=35,
            avg_correlation=0.4
        )
        self.assertTrue(any(a['type'] == 'PUT_SPREAD' for a in actions))

    def test_emergency_put_trigger(self):
        """VIX > 60 → 紧急 Put"""
        actions = self.rm.auto_hedge(
            portfolio_beta=0.3,
            vix_level=70,
            avg_correlation=0.4
        )
        self.assertTrue(any(a['type'] == 'EMERGENCY_PUT' for a in actions))

    def test_correlation_hedge_trigger(self):
        """高相关性 → 避险资产配置"""
        actions = self.rm.auto_hedge(
            portfolio_beta=0.3,
            vix_level=20,
            avg_correlation=0.90
        )
        self.assertTrue(any(a['type'] == 'SAFE_HAVEN' for a in actions))


if __name__ == '__main__':
    unittest.main()
