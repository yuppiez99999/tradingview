"""FinMultiTime 多模态基准数据单元测试.

被测模块: tests/eval/finmultitime_benchmark.py
文献: #63 FinMultiTime (2025.06)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.finmultitime_benchmark import (  # noqa: E402
    FinMultiTimeBenchmark,
    ValidationReport,
)

# ============================================================
# 数据生成测试
# ============================================================

class TestGenerateMarketData:
    def test_sp500_generation(self):
        """SP500 数据生成."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=100)
        assert md.market == "SP500"
        assert len(md.daily) == 100
        assert len(md.minute) == 100 * 390  # 390 分钟/天
        assert len(md.quarterly) > 0

    def test_hs300_generation(self):
        """HS300 数据生成."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("HS300", n_days=100)
        assert md.market == "HS300"
        assert len(md.daily) == 100
        assert len(md.minute) == 100 * 240  # 240 分钟/天

    def test_ohlcv_columns(self):
        """OHLCV 列完整."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=50)
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in md.daily.columns
            assert col in md.minute.columns

    def test_price_positive(self):
        """价格为正."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=50)
        assert (md.daily["close"] > 0).all()
        assert (md.daily["high"] >= md.daily["low"]).all()

    def test_news_sentiment(self):
        """新闻情感数据."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=50)
        assert len(md.news_sentiment) == 50
        assert "sentiment" in md.news_sentiment.columns

    def test_fundamentals(self):
        """财报指标数据."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=252)
        assert len(md.fundamentals) > 0
        assert "revenue" in md.fundamentals.columns
        assert "pe_ratio" in md.fundamentals.columns

    def test_quarterly_aggregation(self):
        """季度数据是日数据聚合."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=252)
        # 季度数 ≈ 252/63 ≈ 4
        assert 3 <= len(md.quarterly) <= 5


# ============================================================
# 对齐数据集测试
# ============================================================

class TestAlignedDataset:
    def test_generate_aligned(self):
        """生成对齐数据集."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        assert len(dataset.markets) == 2
        assert "SP500" in dataset.markets
        assert "HS300" in dataset.markets

    def test_aligned_dates(self):
        """对齐日期."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        assert len(dataset.aligned_dates) > 0
        assert isinstance(dataset.aligned_dates, pd.DatetimeIndex)

    def test_alignment_stats(self):
        """对齐统计."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        assert "n_markets" in dataset.alignment_stats
        assert "n_aligned_days" in dataset.alignment_stats
        assert dataset.alignment_stats["n_markets"] == 2


# ============================================================
# 验证测试
# ============================================================

class TestValidation:
    def test_validation_report(self):
        """验证报告."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        assert isinstance(report, ValidationReport)
        assert isinstance(report.timestamp_aligned, bool)
        assert isinstance(report.resolution_consistency, bool)

    def test_timestamp_alignment(self):
        """时间戳对齐 (验收)."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        assert report.timestamp_aligned  # 对齐日期应在所有市场中存在

    def test_multimodal_completeness(self):
        """多模态完整性 (验收)."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        assert report.multimodal_completeness == 1.0  # 所有模态都有数据

    def test_cross_market_coverage(self):
        """跨市场覆盖率 (验收)."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        assert report.cross_market_coverage > 0.99  # 几乎完全覆盖


# ============================================================
# 基准摘要测试
# ============================================================

class TestBenchmarkSummary:
    def test_summary(self):
        """基准摘要."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        summary = bench.benchmark_summary(dataset, report)
        assert summary["n_markets"] == 2
        assert "SP500" in summary["markets"]
        assert "HS300" in summary["markets"]
        assert "validation" in summary

    def test_summary_market_details(self):
        """摘要市场详情."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=100)
        report = bench.validate_alignment(dataset)
        summary = bench.benchmark_summary(dataset, report)
        for market in ["SP500", "HS300"]:
            assert summary["markets"][market]["n_daily"] == 100
            assert summary["markets"][market]["n_minute"] > 0
            assert summary["markets"][market]["n_quarterly"] > 0


# ============================================================
# 验收标准测试
# ============================================================

class TestAcceptanceCriteria:
    """LIT-5.3 验收: S&P500+HS300 对齐 + 三分辨率 + 多模态."""

    def test_sp500_hs300_aligned(self):
        """验收: S&P500 + HS300 时间戳对齐."""
        bench = FinMultiTimeBenchmark()
        dataset = bench.generate_aligned_dataset(n_days=252)
        report = bench.validate_alignment(dataset)
        assert report.timestamp_aligned
        assert len(dataset.aligned_dates) == 252  # 完全对齐

    def test_three_resolutions(self):
        """验收: 分钟/日/季度三分辨率."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=252)
        assert len(md.minute) > len(md.daily)  # 分钟 > 日
        assert len(md.daily) > len(md.quarterly)  # 日 > 季度

    def test_multimodal_fusion(self):
        """验收: 多模态融合 (价格+新闻+财报)."""
        bench = FinMultiTimeBenchmark()
        md = bench.generate_market_data("SP500", n_days=252)
        assert len(md.daily) > 0  # 价格
        assert len(md.news_sentiment) > 0  # 新闻
        assert len(md.fundamentals) > 0  # 财报
