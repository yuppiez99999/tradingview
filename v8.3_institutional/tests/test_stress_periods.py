"""
v7.5 测试：三段极端行情压力测试 — 合规必过
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §4.3, §7.2
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from backtest.scenario_lib import (
    ScenarioLibrary, StressScenario, STRESS_SCENARIOS
)


class TestStressScenarios(unittest.TestCase):
    """三段必过压力测试"""

    def setUp(self):
        self.lib = ScenarioLibrary()
        # 模拟持仓：500万组合
        self.prices = {
            '600519.SH': 1800,   # 贵州茅台
            '000858.SZ': 160,    # 五粮液
            '300750.SZ': 240,    # 宁德时代
            '601318.SH': 60,     # 中国平安
            '600036.SH': 42,     # 招商银行
            '510300.SH': 4.0,    # 沪深300ETF
            '518880.SH': 5.0,    # 黄金ETF
        }
        self.positions = {
            '600519.SH': 500,
            '000858.SZ': 3000,
            '300750.SZ': 2000,
            '601318.SH': 5000,
            '600036.SH': 10000,
            '510300.SH': 20000,
            '518880.SH': 30000,
        }

    def test_all_scenarios_exist(self):
        """三段场景全部存在"""
        names = self.lib.list_scenarios()
        for required in ['COVID_CRASH', 'LUNA_CRASH', 'YEN_CARRY']:
            self.assertIn(required, names)

    def test_covid_crash(self):
        result = self.lib.what_if(self.positions, 'COVID_CRASH', self.prices)
        self.assertEqual(result['scenario'], 'COVID_CRASH')
        self.assertIn('pnl_pct', result)
        self.assertIsInstance(result['pnl_pct'], float)

    def test_luna_crash(self):
        result = self.lib.what_if(self.positions, 'LUNA_CRASH', self.prices)
        self.assertEqual(result['scenario'], 'LUNA_CRASH')

    def test_yen_carry(self):
        result = self.lib.what_if(self.positions, 'YEN_CARRY', self.prices)
        self.assertEqual(result['scenario'], 'YEN_CARRY')

    def test_compliance_tests(self):
        """三段合规测试"""
        results = self.lib.run_compliance_tests(self.positions, self.prices)
        self.assertEqual(len(results), 3)
        for name in ['COVID_CRASH', 'LUNA_CRASH', 'YEN_CARRY']:
            self.assertIn(name, results)
            self.assertIn('passed', results[name])
            self.assertIn('max_dd', results[name])

    def test_pass_criteria(self):
        """通过判据检查"""
        results = self.lib.run_compliance_tests(self.positions, self.prices)
        all_pass, needs_cro, details = self.lib.check_pass_criteria(results)
        self.assertIsInstance(all_pass, bool)
        self.assertIsInstance(needs_cro, bool)
        self.assertIn('details', details)

    def test_monte_carlo_tail(self):
        """蒙特卡洛尾部模拟"""
        result = self.lib.monte_carlo_tail(
            self.positions, self.prices, n_simulations=5000
        )
        self.assertIn('var_95', result)
        self.assertIn('var_99', result)
        self.assertIn('expected_shortfall_95', result)
        self.assertIn('expected_shortfall_99', result)
        self.assertLessEqual(result['var_99'], result['var_95'])

    def test_custom_scenario(self):
        """自定义场景添加"""
        custom = StressScenario(
            name='CUSTOM_CRASH',
            start='2025-01-01',
            end='2025-01-10',
            description='自定义极端场景',
            asset_shocks={'CSI300': -0.20},
        )
        self.lib.add_scenario(custom)
        self.assertIn('CUSTOM_CRASH', self.lib.list_scenarios())

        result = self.lib.what_if(self.positions, 'CUSTOM_CRASH', self.prices)
        self.assertEqual(result['scenario'], 'CUSTOM_CRASH')

    def test_scenario_not_found(self):
        result = self.lib.what_if(self.positions, 'NONEXISTENT', self.prices)
        self.assertIn('error', result)


class TestStressScenarioConfig(unittest.TestCase):
    """场景配置验证"""

    def test_covid_scenario(self):
        # 从列表中查找COVID_CRASH场景
        covid = next((s for s in STRESS_SCENARIOS if s.name == 'COVID_CRASH'), None)
        self.assertIsNotNone(covid)
        self.assertGreater(abs(covid.asset_shocks['SPX']), 0.1)
        self.assertIn('vix_peak', covid.key_metrics)
        self.assertGreater(covid.key_metrics['vix_peak'], 50)

    def test_luna_scenario(self):
        luna = next((s for s in STRESS_SCENARIOS if s.name == 'LUNA_CRASH'), None)
        self.assertIsNotNone(luna)
        self.assertAlmostEqual(luna.key_metrics['luna_loss'], -0.999, delta=0.01)

    def test_yen_scenario(self):
        yen = next((s for s in STRESS_SCENARIOS if s.name == 'YEN_CARRY'), None)
        self.assertIsNotNone(yen)
        self.assertGreater(yen.key_metrics['vix_peak'], 50)
        self.assertLess(yen.asset_shocks['NIKKEI'], -0.10)


if __name__ == '__main__':
    unittest.main()
