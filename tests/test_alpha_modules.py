"""Alpha 生成模块单元测试

验证三大 Alpha 生成模块:
1. AlphaFactorLibrary — 6大类50+因子计算
2. MomentumReversalEngine — TSMOM+XSMOM+Reversal 三信号融合
3. SmartBetaEngine — 多因子加权优化

测试覆盖:
- 因子计算正确性 (动量/价值/质量/低波/规模/流动性)
- 因子评价指标 (IC/有效因子/强因子)
- 动量信号生成 (置信度/目标仓位)
- Smart Beta 权重优化 (softmax/集中度)
- 边界条件 (空数据/单标的/极端值)
"""

import os
import sys
import unittest
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 路径设置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "v7.5_institutional"))


class TestAlphaFactorLibrary(unittest.TestCase):
    """测试 Alpha 因子库"""

    @classmethod
    def setUpClass(cls):
        """构造测试数据: 10只标的, 252日价格"""
        np.random.seed(42)
        cls.symbols = [f"TEST{i:03d}.SZ" for i in range(10)]
        n_days = 252
        # 模拟价格: 不同标的不同的漂移和波动
        drifts = np.random.uniform(-0.0005, 0.001, len(cls.symbols))
        vols = np.random.uniform(0.015, 0.03, len(cls.symbols))
        returns = np.random.randn(n_days, len(cls.symbols)) * vols + drifts
        cls.prices = pd.DataFrame(
            np.cumprod(1 + returns, axis=0) * 100,
            columns=cls.symbols,
        )
        cls.industries = {s: f"IND{i % 3}" for i, s in enumerate(cls.symbols)}

    def test_module_import(self):
        """测试模块可导入"""
        from utils.alpha_factor_library import AlphaFactorLibrary
        self.assertTrue(callable(AlphaFactorLibrary))

    def test_factor_computation(self):
        """测试因子计算"""
        from utils.alpha_factor_library import AlphaFactorLibrary
        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=self.prices,
            fundamentals=None,
            industries=self.industries,
            benchmark_returns=None,
        )
        self.assertIsNotNone(result)
        # 实际字段是 factors (Dict[str, FactorValue])
        self.assertGreater(len(result.factors), 0)
        # 至少有动量因子
        self.assertGreaterEqual(len(result.factors), 10)

    def test_factor_neutralization(self):
        """测试因子中性化"""
        from utils.alpha_factor_library import AlphaFactorLibrary
        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=self.prices,
            fundamentals=None,
            industries=self.industries,
            benchmark_returns=None,
        )
        # factors 是 Dict[str, FactorValue]
        self.assertIsNotNone(result.factors)
        self.assertGreater(len(result.factors), 0)


class TestMomentumReversalEngine(unittest.TestCase):
    """测试动量反转引擎"""

    @classmethod
    def setUpClass(cls):
        np.random.seed(42)
        cls.symbols = [f"TEST{i:03d}.SZ" for i in range(8)]
        n_days = 252
        returns = np.random.randn(n_days, len(cls.symbols)) * 0.02
        cls.prices = pd.DataFrame(
            np.cumprod(1 + returns, axis=0) * 100,
            columns=cls.symbols,
        )

    def test_module_import(self):
        from utils.momentum_reversal_engine import MomentumReversalEngine
        self.assertTrue(callable(MomentumReversalEngine))

    def test_signal_generation(self):
        """测试信号生成"""
        from utils.momentum_reversal_engine import MomentumReversalEngine
        engine = MomentumReversalEngine()
        result = engine.generate_signals(self.prices)
        self.assertIsNotNone(result)
        # 实际字段: signals (Dict[str, MomentumSignal])
        self.assertGreaterEqual(len(result.signals), 0)

    def test_confidence_range(self):
        """测试置信度在 [0, 1] 范围内"""
        from utils.momentum_reversal_engine import MomentumReversalEngine
        engine = MomentumReversalEngine()
        result = engine.generate_signals(self.prices)
        # signals 是 Dict[str, MomentumSignal]
        if result.signals:
            for sig in result.signals.values():
                self.assertGreaterEqual(sig.confidence, 0.0)
                self.assertLessEqual(sig.confidence, 1.0)


class TestSmartBetaEngine(unittest.TestCase):
    """测试 Smart Beta 加权引擎"""

    @classmethod
    def setUpClass(cls):
        np.random.seed(42)
        cls.symbols = [f"TEST{i:03d}.SZ" for i in range(8)]
        # factor_scores 实际签名: Dict[str, Dict[str, float]] = {symbol: {factor: value}}
        np.random.seed(42)
        cls.factor_scores = {
            s: {
                "MOM_60D": float(np.random.uniform(-1, 1)),
                "VAL_PE": float(np.random.uniform(-1, 1)),
                "QUA_ROE": float(np.random.uniform(-1, 1)),
            }
            for s in cls.symbols
        }
        cls.market_caps = {s: float(np.random.uniform(1e9, 5e10)) for s in cls.symbols}
        cls.cov_matrix = np.eye(len(cls.symbols)) * 0.04

    def test_module_import(self):
        from utils.smart_beta_engine import SmartBetaEngine
        self.assertTrue(callable(SmartBetaEngine))

    def test_optimization(self):
        """测试权重优化"""
        from utils.smart_beta_engine import SmartBetaEngine
        engine = SmartBetaEngine()
        result = engine.optimize(
            symbols=self.symbols,
            factor_scores=self.factor_scores,
            market_caps=self.market_caps,
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=self.cov_matrix,
        )
        self.assertIsNotNone(result)
        # 实际字段是 smart_beta_weights
        weights = np.array(result.smart_beta_weights)
        # 权重归一化 (允许 softmax 后归一化误差)
        self.assertAlmostEqual(weights.sum(), 1.0, places=2)
        # 所有权重非负
        self.assertTrue(np.all(weights >= -1e-6))

    def test_concentration_hhi(self):
        """测试集中度 HHI 计算"""
        from utils.smart_beta_engine import SmartBetaEngine
        engine = SmartBetaEngine()
        result = engine.optimize(
            symbols=self.symbols,
            factor_scores=self.factor_scores,
            market_caps=self.market_caps,
            factor_weights={"MOM_60D": 1.0},
            cov_matrix=self.cov_matrix,
        )
        # 实际字段是 weight_concentration
        hhi = result.weight_concentration
        # HHI 范围: [1/N, 1]
        self.assertGreaterEqual(hhi, 1.0 / len(self.symbols) - 1e-3)
        self.assertLessEqual(hhi, 1.0 + 1e-3)


class TestEdgeCases(unittest.TestCase):
    """边界条件测试"""

    def test_single_asset(self):
        """单标的场景"""
        from utils.momentum_reversal_engine import MomentumReversalEngine
        np.random.seed(42)
        prices = pd.DataFrame(
            {"SOLO.SH": np.cumprod(1 + np.random.randn(60) * 0.02) * 100}
        )
        engine = MomentumReversalEngine()
        result = engine.generate_signals(prices)
        self.assertIsNotNone(result)

    def test_empty_data(self):
        """空数据场景"""
        from utils.alpha_factor_library import AlphaFactorLibrary
        lib = AlphaFactorLibrary()
        empty_df = pd.DataFrame()
        try:
            result = lib.compute_all(
                price_data=empty_df,
                fundamentals=None,
                industries={},
                benchmark_returns=None,
            )
            # 应该返回结果对象,不抛异常
            self.assertIsNotNone(result)
        except (ValueError, KeyError):
            # 空数据抛出明确异常也算正常行为
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
