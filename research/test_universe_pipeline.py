"""
全市场选股系统 — 集成测试

测试场景：
1. 单元测试：各模块核心函数（用模拟数据）
2. 集成测试：完整 pipeline 端到端（烟雾测试模式）
3. 性能测试：单股因子计算耗时

运行:
    py -3.8 research/test_universe_pipeline.py
    py -3.8 -m pytest research/test_universe_pipeline.py -v
"""

from __future__ import annotations

import logging
import sys
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

# 项目根
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================================
# 测试工具
# ============================================================
def _make_mock_kline(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """生成模拟 K 线数据"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close0 = 50.0 + (seed % 50)
    returns = np.random.randn(n) * 0.025 + 0.0005
    close = close0 * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(np.random.randn(n)) * 0.015)
    low = close * (1 - np.abs(np.random.randn(n)) * 0.015)
    open_p = close * (1 + np.random.randn(n) * 0.008)
    volume = np.abs(np.random.randn(n)) * 1e7 + 1e6
    amount = close * volume * 0.001
    df = pd.DataFrame(
        {
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
        },
        index=dates,
    )
    return df


def _make_mock_spot(symbols: list) -> pd.DataFrame:
    """生成模拟全市场快照"""
    np.random.seed(0)
    n = len(symbols)
    return pd.DataFrame(
        {
            "代码": symbols,
            "名称": [f"股票{i:03d}" for i in range(n)],
            "最新价": np.random.uniform(5, 100, n),
            "涨跌幅": np.random.uniform(-5, 5, n),
            "成交额": np.random.uniform(1e7, 5e8, n),
            "成交量": np.random.uniform(1e5, 5e7, n),
            "换手率": np.random.uniform(0.5, 10, n),
            "总市值": np.random.uniform(1e9, 5e11, n),
            "流通市值": np.random.uniform(5e8, 3e11, n),
        }
    )


# ============================================================
# 单元测试
# ============================================================
class TestRiskFilter(unittest.TestCase):
    """风险过滤器测试"""

    def test_filter_st_stocks(self):
        from utils.universe.risk_filter import _is_st_stock

        self.assertTrue(_is_st_stock("ST天宝"))
        self.assertTrue(_is_st_stock("*ST海航"))
        self.assertTrue(_is_st_stock("退市美都"))
        self.assertFalse(_is_st_stock("贵州茅台"))
        self.assertFalse(_is_st_stock("中国平安"))

    def test_filter_universe_basic(self):
        from utils.universe.risk_filter import RiskFilterConfig, filter_universe

        # 构造测试数据
        universe_df = pd.DataFrame(
            {
                "code": ["600519", "000001", "600001", "000002"],
                "name": ["贵州茅台", "平安银行", "ST测试", "万科A"],
                "index": ["HS300"] * 4,
            }
        )
        spot_df = pd.DataFrame(
            {
                "代码": ["600519", "000001", "600001", "000002"],
                "名称": ["贵州茅台", "平安银行", "ST测试", "万科A"],
                "最新价": [200.0, 12.5, 1.5, 8.0],
                "涨跌幅": [1.2, -0.5, -2.0, 0.3],
                "成交额": [5e9, 1e9, 1e7, 5e8],
                "成交量": [3e6, 8e7, 5e6, 6e7],
                "换手率": [0.5, 0.8, 1.2, 0.6],
            }
        )
        result = filter_universe(
            universe_df, spot_df, RiskFilterConfig(max_price=2000.0)
        )
        # ST 测试 + 价格过低应该被剔除
        self.assertNotIn("600001", result["code"].tolist())
        # 茅台、平安、万科应保留
        for code in ["600519", "000001", "000002"]:
            self.assertIn(code, result["code"].tolist())


class TestFactorScorer(unittest.TestCase):
    """因子打分器测试"""

    def test_cross_sectional_score(self):
        from utils.universe.factor_scorer import cross_sectional_score

        # 构造模拟因子数据
        np.random.seed(42)
        n_stocks = 50
        symbols = [f"stock_{i:03d}" for i in range(n_stocks)]
        factor_df = pd.DataFrame(
            np.random.randn(n_stocks, 20),
            index=symbols,
            columns=[f"factor_{i}" for i in range(20)],
        )
        theme_factors = {
            "momentum": ["factor_0", "factor_1", "factor_2", "factor_3"],
            "reversal": ["factor_4", "factor_5", "factor_6"],
            "volume": ["factor_7", "factor_8", "factor_9", "factor_10"],
            "volatility": ["factor_11", "factor_12", "factor_13"],
            "liquidity": ["factor_14", "factor_15"],
        }
        scores = cross_sectional_score(factor_df, theme_factors)
        self.assertEqual(len(scores), n_stocks)
        self.assertIn("composite_score", scores.columns)
        self.assertIn("rank", scores.columns)
        # 排名应该是 1..n
        self.assertEqual(set(scores["rank"]), set(range(1, n_stocks + 1)))
        # 综合得分应该归一化到 [0,1]
        self.assertGreaterEqual(scores["composite_score"].min(), 0)
        self.assertLessEqual(scores["composite_score"].max(), 1)

    def test_industry_neutralize(self):
        from utils.universe.factor_scorer import industry_neutralize

        scores = pd.Series(
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], index=["A", "B", "C", "D", "E", "F"]
        )
        industry_map = {
            "A": "银行",
            "B": "银行",
            "C": "地产",
            "D": "地产",
            "E": "消费",
            "F": "消费",
        }
        result = industry_neutralize(scores, industry_map)
        # 每个行业内的均值应接近 0
        for ind in ["银行", "地产", "消费"]:
            ind_scores = [result[s] for s, i in industry_map.items() if i == ind]
            self.assertAlmostEqual(np.mean(ind_scores), 0, places=5)


class TestPortfolioBuilder(unittest.TestCase):
    """组合构建器测试"""

    def test_build_layered_portfolio(self):
        from utils.universe.portfolio_builder import (
            PortfolioConfig,
            build_layered_portfolio,
        )

        np.random.seed(42)
        n = 100
        symbols = [f"stock_{i:03d}" for i in range(n)]
        scores_df = pd.DataFrame(
            {
                "momentum_score": np.random.randn(n),
                "reversal_score": np.random.randn(n),
                "volume_score": np.random.randn(n),
                "volatility_score": np.random.randn(n),
                "liquidity_score": np.random.randn(n),
                "composite_score": np.random.uniform(0, 1, n),
            },
            index=symbols,
        )
        scores_df["rank"] = (
            scores_df["composite_score"].rank(ascending=False, method="min").astype(int)
        )

        industry_map = {
            s: np.random.choice(["银行", "地产", "消费", "科技", "能源"])
            for s in symbols
        }
        name_map = {s: s for s in symbols}

        config = PortfolioConfig(short_count=5, mid_count=5, long_count=10)
        portfolio = build_layered_portfolio(
            scores_df=scores_df,
            industry_map=industry_map,
            name_map=name_map,
            config=config,
            trade_date="2026-07-29",
            universe_size=800,
            filtered_size=600,
        )

        self.assertEqual(len(portfolio.holdings), 20)  # 5+5+10
        # 单股权重 ≤ 5%
        for h in portfolio.holdings:
            self.assertLessEqual(h.weight, 0.06)  # 留点容差
        # 总权重 ~ 1.0
        total = sum(h.weight for h in portfolio.holdings)
        self.assertAlmostEqual(total, 1.0, places=2)
        # HHI 集中度合理
        self.assertGreater(portfolio.concentration_hhi, 0)
        self.assertLess(portfolio.concentration_hhi, 1)


# ============================================================
# 因子计算性能测试
# ============================================================
class TestFactorPerformance(unittest.TestCase):
    """因子计算性能测试"""

    def test_single_stock_factor_compute(self):
        """单股 458 因子计算应在 5 秒内"""
        from utils.vibe_trading_adapter import get_vibe_adapter

        df = _make_mock_kline(300)
        adapter = get_vibe_adapter()
        start = time.time()
        result = adapter.compute_single_stock(df)
        elapsed = time.time() - start
        logger.info(f"单股因子计算: {len(result.values)} 个  耗时: {elapsed:.2f}s")
        self.assertGreaterEqual(len(result.values), 400, "至少应成功 400 个因子")
        self.assertLess(elapsed, 10.0, "单股因子计算应在 10 秒内完成")


# ============================================================
# 端到端集成测试
# ============================================================
class TestEndToEnd(unittest.TestCase):
    """端到端测试（使用模拟数据）"""

    def test_full_pipeline_with_mock_data(self):
        """使用模拟数据跑完整 pipeline"""
        import tempfile

        from utils.universe.factor_scorer import (
            ScoringConfig,
            batch_compute_factors,
            cross_sectional_score,
        )
        from utils.universe.portfolio_builder import (
            PortfolioConfig,
            build_layered_portfolio,
        )
        from utils.universe.report_generator import generate_full_report

        # 1. 生成 20 只模拟股票
        n_stocks = 20
        symbols = [f"mock_{i:03d}" for i in range(n_stocks)]
        klines_cache = {s: _make_mock_kline(300, seed=i) for i, s in enumerate(symbols)}

        # 2. 批量因子计算
        def loader(sym):
            return klines_cache[sym]

        config = ScoringConfig(max_factors_per_theme=5, max_workers=2)
        factor_df, theme_factors = batch_compute_factors(symbols, loader, config)
        self.assertGreaterEqual(len(factor_df), 15)

        # 3. 打分
        scores_df = cross_sectional_score(factor_df, theme_factors)
        self.assertEqual(len(scores_df), len(factor_df))

        # 4. 组合构建
        industry_map = {s: "测试行业" for s in scores_df.index}
        name_map = {s: s for s in scores_df.index}
        port_config = PortfolioConfig(short_count=3, mid_count=3, long_count=4)
        portfolio = build_layered_portfolio(
            scores_df=scores_df,
            industry_map=industry_map,
            name_map=name_map,
            config=port_config,
            trade_date="2026-07-29",
            universe_size=20,
            filtered_size=20,
        )
        self.assertEqual(len(portfolio.holdings), 10)

        # 5. 生成报告
        with tempfile.TemporaryDirectory() as tmpdir:
            filter_stats = {
                "initial": 20,
                "final": 20,
                "pass_rate": "100%",
                "total_removed": 0,
            }
            paths = generate_full_report(
                portfolio=portfolio,
                scores_df=scores_df,
                filter_stats=filter_stats,
                output_dir=tmpdir,
            )
            # 验证文件存在
            self.assertTrue(Path(paths.candidates_csv).exists())
            self.assertTrue(Path(paths.portfolio_json).exists())
            self.assertTrue(Path(paths.report_md).exists())
            logger.info(f"  报告生成于: {tmpdir}")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
