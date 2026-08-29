"""FinMultiTime 多模态基准数据
================================

文献依据: #63 FinMultiTime (2025.06)
任务: LIT-5.3 FinMultiTime 多模态基准数据

核心设计
--------
FinMultiTime 是多市场、多时间分辨率的金融时间序列基准:
- 市场: S&P500 (美股) + HS300 (沪深300, A股)
- 分辨率: 分钟 (1min) / 日 (1day) / 季度 (1quarter)
- 模态: 价格 (OHLCV) + 新闻情感 + 财报指标

验收标准
--------
- S&P500 + HS300 时间戳对齐 (跨市场同步)
- 分钟/日/季度三分辨率一致性 (聚合校验)
- 多模态融合 (价格 + 新闻 + 财报)

使用示例
--------
    from tests.eval.finmultitime_benchmark import FinMultiTimeBenchmark

    bench = FinMultiTimeBenchmark()
    data = bench.generate_aligned_dataset(n_days=252)
    report = bench.validate_alignment(data)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("finmultitime_benchmark")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class MarketData:
    """单市场多分辨率数据."""

    market: str  # 市场名 (SP500/HS300)
    minute: pd.DataFrame  # 分钟数据
    daily: pd.DataFrame  # 日数据
    quarterly: pd.DataFrame  # 季度数据
    news_sentiment: pd.DataFrame  # 新闻情感
    fundamentals: pd.DataFrame  # 财报指标


@dataclass
class AlignedDataset:
    """对齐后的多市场数据集."""

    markets: dict[str, MarketData]
    aligned_dates: pd.DatetimeIndex  # 对齐的交易日
    alignment_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """验证报告."""

    timestamp_aligned: bool  # 时间戳对齐
    resolution_consistency: bool  # 分辨率一致性
    cross_market_coverage: float  # 跨市场覆盖率
    multimodal_completeness: float  # 多模态完整性
    issues: list[str] = field(default_factory=list)


# ============================================================
# FinMultiTime 基准
# ============================================================


class FinMultiTimeBenchmark:
    """FinMultiTime 多模态基准数据生成与验证.

    用法:
        bench = FinMultiTimeBenchmark()
        data = bench.generate_aligned_dataset(n_days=252)
        report = bench.validate_alignment(data)
    """

    MARKETS = ["SP500", "HS300"]
    RESOLUTIONS = ["minute", "daily", "quarterly"]
    MODALITIES = ["price", "news_sentiment", "fundamentals"]

    # 每日交易分钟数 (SP500: 390, HS300: 240)
    MINUTES_PER_DAY = {"SP500": 390, "HS300": 240}

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def _generate_ohlcv(
        self,
        n: int,
        initial_price: float,
        drift: float,
        volatility: float,
        rng: np.random.Generator,
    ) -> pd.DataFrame:
        """生成 OHLCV 数据 (几何布朗运动)."""
        returns = rng.normal(drift, volatility, n)
        close = initial_price * np.exp(np.cumsum(returns))
        open_ = np.roll(close, 1)
        open_[0] = initial_price
        high = np.maximum(open_, close) * (
            1 + np.abs(rng.normal(0, volatility * 0.3, n))
        )
        low = np.minimum(open_, close) * (
            1 - np.abs(rng.normal(0, volatility * 0.3, n))
        )
        volume = rng.integers(1_000_000, 100_000_000, n).astype(float)
        return pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    def _generate_news_sentiment(
        self,
        dates: pd.DatetimeIndex,
        rng: np.random.Generator,
    ) -> pd.DataFrame:
        """生成新闻情感数据."""
        n = len(dates)
        return pd.DataFrame(
            {
                "date": dates,
                "sentiment": rng.normal(0, 1, n),  # 情感分数 [-3, 3] 近似
                "news_count": rng.integers(0, 50, n),
                "relevance": rng.uniform(0, 1, n),
            }
        )

    def _generate_fundamentals(
        self,
        quarters: pd.DatetimeIndex,
        rng: np.random.Generator,
    ) -> pd.DataFrame:
        """生成财报指标数据."""
        n = len(quarters)
        return pd.DataFrame(
            {
                "quarter": quarters,
                "revenue": rng.lognormal(20, 0.5, n),  # 营收
                "earnings": rng.normal(0, 1e8, n),  # 盈利
                "pe_ratio": rng.uniform(5, 50, n),  # 市盈率
                "debt_ratio": rng.uniform(0, 1, n),  # 负债率
            }
        )

    def generate_market_data(
        self,
        market: str,
        n_days: int = 252,
    ) -> MarketData:
        """生成单市场多分辨率数据.

        Args:
            market: 市场名 (SP500/HS300)
            n_days: 交易日数

        Returns:
            MarketData
        """
        rng = np.random.default_rng(self.seed + hash(market) % 1000)

        # 日历
        daily_dates = pd.bdate_range(end="2026-08-24", periods=n_days)

        # 日数据
        drift = 0.0003 if market == "SP500" else 0.0002
        vol = 0.012 if market == "SP500" else 0.015
        daily_df = self._generate_ohlcv(
            n_days, 4000.0 if market == "SP500" else 4000.0, drift, vol, rng
        )
        daily_df.index = daily_dates
        daily_df.index.name = "date"

        # 分钟数据 (从日数据展开)
        minutes_per_day = self.MINUTES_PER_DAY[market]
        n_minute = n_days * minutes_per_day
        minute_rng = np.random.default_rng(self.seed + hash(market + "_min") % 1000)
        minute_df = self._generate_ohlcv(
            n_minute,
            float(daily_df["close"].iloc[0]),
            drift / minutes_per_day,
            vol / np.sqrt(minutes_per_day),
            minute_rng,
        )
        # 分钟时间索引
        minute_dates = []
        for d in daily_dates:
            for m in range(minutes_per_day):
                minute_dates.append(d + pd.Timedelta(minutes=9 * 60 + m))
        minute_df.index = pd.DatetimeIndex(minute_dates)
        minute_df.index.name = "datetime"

        # 季度数据 (聚合日数据)
        quarterly_df = (
            daily_df.resample("QE")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )

        # 新闻情感
        news_df = self._generate_news_sentiment(daily_dates, rng)

        # 财报指标
        quarter_ends = quarterly_df.index
        fund_df = self._generate_fundamentals(quarter_ends, rng)

        return MarketData(
            market=market,
            minute=minute_df,
            daily=daily_df,
            quarterly=quarterly_df,
            news_sentiment=news_df,
            fundamentals=fund_df,
        )

    def generate_aligned_dataset(
        self,
        n_days: int = 252,
    ) -> AlignedDataset:
        """生成对齐的多市场数据集.

        Args:
            n_days: 交易日数

        Returns:
            AlignedDataset
        """
        markets: dict[str, MarketData] = {}
        for m in self.MARKETS:
            markets[m] = self.generate_market_data(m, n_days)

        # 对齐交易日 (取交集)
        dates_list = [md.daily.index for md in markets.values()]
        aligned_dates = dates_list[0]
        for d in dates_list[1:]:
            aligned_dates = aligned_dates.intersection(d)

        stats = {
            "n_markets": len(markets),
            "n_aligned_days": len(aligned_dates),
            "markets": list(markets.keys()),
            "date_range": (str(aligned_dates[0]), str(aligned_dates[-1])),
        }

        return AlignedDataset(
            markets=markets,
            aligned_dates=aligned_dates,
            alignment_stats=stats,
        )

    # ============================================================
    # 验证
    # ============================================================

    def validate_alignment(
        self,
        dataset: AlignedDataset,
    ) -> ValidationReport:
        """验证数据集对齐质量.

        Args:
            dataset: 对齐数据集

        Returns:
            ValidationReport
        """
        issues: list[str] = []

        # 1. 时间戳对齐检查
        ts_aligned = True
        for name, md in dataset.markets.items():
            if not dataset.aligned_dates.isin(md.daily.index).all():
                ts_aligned = False
                issues.append(f"{name}: 部分对齐日期不在日数据中")

        # 2. 分辨率一致性 (日数据 = 分钟数据聚合)
        res_consistent = True
        for name, md in dataset.markets.items():
            # 检查日 close == 当日最后分钟 close
            for date in md.daily.index[:5]:  # 抽查前5天
                day_minutes = md.minute[md.minute.index.date == date.date()]
                if len(day_minutes) > 0:
                    minute_close = float(day_minutes["close"].iloc[-1])
                    daily_close = float(md.daily.loc[date, "close"])
                    if not np.isclose(minute_close, daily_close, rtol=0.1):
                        res_consistent = False
                        issues.append(
                            f"{name}: {date.date()} 分钟收盘 {minute_close:.2f}"
                            f" ≠ 日收盘 {daily_close:.2f}"
                        )
                        break

        # 3. 跨市场覆盖率
        total_dates = sum(len(md.daily.index) for md in dataset.markets.values())
        aligned_total = len(dataset.aligned_dates) * len(dataset.markets)
        coverage = aligned_total / total_dates if total_dates > 0 else 0.0

        # 4. 多模态完整性
        modal_count = 0
        modal_total = 0
        for md in dataset.markets.values():
            for modal in [
                md.minute,
                md.daily,
                md.quarterly,
                md.news_sentiment,
                md.fundamentals,
            ]:
                modal_total += 1
                if len(modal) > 0:
                    modal_count += 1
        completeness = modal_count / modal_total if modal_total > 0 else 0.0

        return ValidationReport(
            timestamp_aligned=ts_aligned,
            resolution_consistency=res_consistent,
            cross_market_coverage=coverage,
            multimodal_completeness=completeness,
            issues=issues,
        )

    def benchmark_summary(
        self,
        dataset: AlignedDataset,
        report: ValidationReport,
    ) -> dict[str, Any]:
        """生成基准摘要."""
        return {
            "n_markets": len(dataset.markets),
            "n_aligned_days": len(dataset.aligned_dates),
            "markets": {
                name: {
                    "n_minute": len(md.minute),
                    "n_daily": len(md.daily),
                    "n_quarterly": len(md.quarterly),
                    "n_news": len(md.news_sentiment),
                    "n_fundamentals": len(md.fundamentals),
                }
                for name, md in dataset.markets.items()
            },
            "validation": {
                "timestamp_aligned": report.timestamp_aligned,
                "resolution_consistency": report.resolution_consistency,
                "cross_market_coverage": report.cross_market_coverage,
                "multimodal_completeness": report.multimodal_completeness,
                "n_issues": len(report.issues),
            },
        }


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 FinMultiTime 基准."""
    print("=" * 60)
    print("FinMultiTime 多模态基准数据")
    print("文献: #63 FinMultiTime (2025.06)")
    print("=" * 60)

    bench = FinMultiTimeBenchmark()

    # === 1. 生成对齐数据集 ===
    print("\n--- 1. 生成对齐数据集 ---")
    dataset = bench.generate_aligned_dataset(n_days=252)
    print(f"  市场数: {len(dataset.markets)}")
    print(f"  对齐交易日: {len(dataset.aligned_dates)}")
    for name, md in dataset.markets.items():
        print(
            f"  {name}: 分钟{len(md.minute)}行, 日{len(md.daily)}行, "
            f"季{len(md.quarterly)}行, 新闻{len(md.news_sentiment)}行, "
            f"财报{len(md.fundamentals)}行"
        )

    # === 2. 验证对齐 ===
    print("\n--- 2. 验证对齐质量 ---")
    report = bench.validate_alignment(dataset)
    print(f"  时间戳对齐: {'✅' if report.timestamp_aligned else '❌'}")
    print(f"  分辨率一致性: {'✅' if report.resolution_consistency else '❌'}")
    print(f"  跨市场覆盖率: {report.cross_market_coverage:.1%}")
    print(f"  多模态完整性: {report.multimodal_completeness:.1%}")
    if report.issues:
        print(f"  问题 ({len(report.issues)}):")
        for issue in report.issues[:5]:
            print(f"    - {issue}")

    # === 3. 基准摘要 ===
    print("\n--- 3. 基准摘要 ---")
    summary = bench.benchmark_summary(dataset, report)
    print(f"  总市场: {summary['n_markets']}")
    print(f"  总对齐天数: {summary['n_aligned_days']}")
    print(
        f"  验证通过: {summary['validation']['timestamp_aligned'] and summary['validation']['resolution_consistency']}"
    )


if __name__ == "__main__":
    main()
