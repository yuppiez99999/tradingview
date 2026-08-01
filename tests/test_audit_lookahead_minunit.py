"""
量化审计回归测试 — 未来函数修复 + 最小交易单位修复

覆盖三项修复:
    AUDIT-1 (CRITICAL): qlib_signal_adapter._add_technical_features 的 bfill() 未来函数
    AUDIT-2 (HIGH):     qlib_signal_adapter._add_technical_features 缺少 OHLCV 列校验
    AUDIT-3 (MEDIUM):   trading_rules.get_trading_rule 的 min_unit 硬编码为 1

验证目标:
    - 年化收益率 > 8% 建立在无未来函数的真实回测上 (而非 bfill 虚高)
    - 回撤 < 15% 的风控在最小交易单位正确的前提下生效
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ms_strategy/src 在 sys.path
_MS_SRC = PROJECT_ROOT / "ms_strategy" / "src"
if str(_MS_SRC) not in sys.path:
    sys.path.insert(0, str(_MS_SRC))


class LookAheadBfillFixTest(unittest.TestCase):
    """AUDIT-1: 验证 bfill() 未来函数已修复"""

    def test_no_bfill_in_technical_features(self):
        """测试 1: _add_technical_features 不再调用 bfill (前视偏差已消除)

        检查方法调用 (.bfill() 形式), 而非注释中的文本.
        修复后的代码用 df.ffill() 替代 df.bfill().ffill().
        """
        import inspect
        import re

        from alpha import qlib_signal_adapter as qsa

        source = inspect.getsource(qsa._add_technical_features)
        # 检查 .bfill( 方法调用形式 (带点号和左括号), 排除注释中的描述性文本
        bfill_calls = re.findall(r"\.bfill\s*\(", source)
        self.assertEqual(
            len(bfill_calls), 0,
            f"_add_technical_features 仍包含 {len(bfill_calls)} 处 .bfill() 方法调用 "
            "— 前视偏差未修复! bfill 会用未来数据填充历史 NaN, 导致回测虚高."
        )
        # ffill 是允许的 (前向填充, 用过去数据)
        self.assertIn(".ffill()", source, "应使用 df.ffill() 替代 df.bfill().ffill()")

    def test_technical_features_no_future_data_leak(self):
        """测试 2: 特征计算不引入未来数据 (前视偏差功能性验证)

        构造一个已知序列, 验证 ffill (过去填充) 与 bfill (未来填充) 行为不同:
            - 在第 5 行注入 NaN, ffill 会用第 4 行的值填充 (过去)
            - bfill 会用第 6 行的值填充 (未来) — 这是被禁止的
        """
        from alpha.qlib_signal_adapter import _add_technical_features

        # 构造 OHLCV 数据, 在 close 列第 5 行注入 NaN
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        df = pd.DataFrame({
            "open": np.arange(30, dtype=float) + 10,
            "high": np.arange(30, dtype=float) + 11,
            "low": np.arange(30, dtype=float) + 9,
            "close": np.arange(30, dtype=float) + 10,
            "volume": np.arange(30, dtype=float) * 1000 + 500,
        }, index=dates)

        # 注入 NaN (模拟停牌/数据缺失)
        df.loc[dates[5], "close"] = np.nan

        result = _add_technical_features(df)

        # 验证: ffill 会用第 4 行的值 (14.0) 填充第 5 行
        # 如果是 bfill, 会用第 6 行的值 (16.0) 填充 — 这是错误的
        filled_value = result.loc[dates[5], "close"]
        self.assertEqual(
            filled_value, 14.0,
            f"close[5] 应被 ffill 为 14.0 (过去值), 实际为 {filled_value}. "
            "若为 16.0 则说明仍在用 bfill (未来数据泄漏)."
        )

    def test_missing_ohlcv_raises_keyerror(self):
        """AUDIT-2: 缺少 OHLCV 列时抛出清晰的 KeyError 而非隐晦的报错"""
        from alpha.qlib_signal_adapter import _add_technical_features

        # 缺少 high/low 列的 DataFrame
        df_bad = pd.DataFrame({
            "open": [10, 11, 12],
            "close": [10, 11, 12],
            "volume": [1000, 2000, 3000],
        })

        with self.assertRaises(KeyError) as ctx:
            _add_technical_features(df_bad)

        # 验证错误信息包含缺失列名 (便于排查)
        err_msg = str(ctx.exception)
        self.assertIn("high", err_msg)
        self.assertIn("low", err_msg)
        self.assertIn("OHLCV", err_msg)


class MinUnitFixTest(unittest.TestCase):
    """AUDIT-3: 验证最小交易单位修复"""

    @classmethod
    def setUpClass(cls):
        """用 importlib 直接从文件路径加载 trading_rules, 绕过 utils 包导入问题"""
        import importlib.util
        tr_path = PROJECT_ROOT / "utils" / "trading_rules.py"
        spec = importlib.util.spec_from_file_location("trading_rules_test", tr_path)
        cls.tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tr)

    def test_stock_min_unit_is_100(self):
        """测试: 股票最小交易单位为 100 股 (1手)"""
        # 主板股票
        rule = self.tr.get_trading_rule("600019", "STOCK")
        self.assertEqual(rule["min_unit"], 100, "主板股票 min_unit 应为 100")

        # 创业板
        rule = self.tr.get_trading_rule("300750", "STOCK")
        self.assertEqual(rule["min_unit"], 100, "创业板股票 min_unit 应为 100")

        # 科创板
        rule = self.tr.get_trading_rule("688981", "STOCK")
        self.assertEqual(rule["min_unit"], 100, "科创板股票 min_unit 应为 100")

    def test_etf_min_unit_is_100(self):
        """测试: ETF 最小交易单位为 100 份"""
        rule = self.tr.get_trading_rule("510300", "ETF")
        self.assertEqual(rule["min_unit"], 100, "ETF min_unit 应为 100")

    def test_future_min_unit_is_1(self):
        """测试: 期货最小交易单位为 1 手"""
        rule = self.tr.get_trading_rule("IF2406", "FUTURE")
        self.assertEqual(rule["min_unit"], 1, "期货 min_unit 应为 1 (按手)")

    def test_option_min_unit_is_1(self):
        """测试: 期权最小交易单位为 1 张"""
        rule = self.tr.get_trading_rule("10004001", "OPTION")
        self.assertEqual(rule["min_unit"], 1, "期权 min_unit 应为 1 (按张)")

    def test_min_unit_produces_valid_lot_size(self):
        """测试: min_unit 用于手数计算时产生 100 的整数倍 (A股合规性)"""
        rule = self.tr.get_trading_rule("600019", "STOCK")
        min_unit = rule["min_unit"]

        # 模拟下单: 资金 10000, 价格 5.0
        capital = 10000
        price = 5.0
        raw_shares = int(capital / price)  # 2000
        lots = (raw_shares // min_unit) * min_unit  # 2000 // 100 * 100 = 2000

        self.assertEqual(lots % 100, 0, "下单股数必须是 100 的整数倍 (A股最小交易单位)")
        self.assertEqual(lots, 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
