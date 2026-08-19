"""
舆情情感信号源 (SentimentSignalSource) 单元测试
================================================

测试 W.A.1 新增的舆情情感信号源:
- 中性降级 (空代码 / 无数据 / 采集失败)
- 采集 + 打分 → SignalResult 转换正确
- composite_sentiment [-1,1] → score [0,1] 映射
- confidence < min_confidence 时自动降权
- 多平台采集聚合
- 异常容错 (采集器/打分引擎初始化失败)

设计依据: docs/1设计计划集成到系统内并能完整运行_20260817.md W.A.1
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from utils.signal_sources.sentiment_signal_source import (  # noqa: E402
    SentimentSignalSource,
    SentimentSourceConfig,
)

# ============================================================
# Mock 数据结构
# ============================================================

@dataclass
class MockNewsItem:
    news_id: str
    title: str
    content: str = ""
    source: str = ""
    publish_time: Any = None
    symbols: list = field(default_factory=list)
    sentiment_score: float = 0.0
    sentiment_label: str = "NEUTRAL"
    confidence: float = 0.0
    event_type: str = ""
    event_entities: list = field(default_factory=list)
    impact_score: float = 0.0
    impact_horizon_hours: int = 24


@dataclass
class MockSentimentSignal:
    symbol: str
    composite_sentiment: float
    news_count_24h: int
    news_count_7d: int
    weighted_sentiment: float
    breaking_news: bool
    event_distribution: dict
    propagated_sentiment: float
    signal_strength: float
    confidence: float
    top_news: list = field(default_factory=list)


@dataclass
class MockSentimentResult:
    signals: dict
    market_sentiment: float = 0.0
    anomalies: list = field(default_factory=list)
    hot_events: list = field(default_factory=list)
    total_news_processed: int = 0
    processing_time_ms: float = 0.0


@dataclass
class MockMediaCrawlerNewsItem:
    title: str
    content: str = ""
    url: str = ""
    source: str = "xhs"
    category: str = "social"
    published_at: str = "2026-08-17T10:00:00"
    symbol: str = ""
    sentiment_score: float = 0.0
    keywords: list = field(default_factory=list)
    platform: str = "xhs"
    item_id: str = "1"
    creator_nickname: str = ""
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    view_count: int = 0
    comments: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class MockMediaCrawlerResult:
    success: bool
    items: list = field(default_factory=list)
    source: str = ""
    platform: str = ""
    error: str = ""
    elapsed_ms: float = 0.0
    total_count: int = 0


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_media_crawler():
    """模拟 MediaCrawlerAdapter, 返回 3 平台 4 条舆情"""
    crawler = MagicMock()
    items = [
        MockMediaCrawlerNewsItem(title="茅台业绩大增", content="超预期", platform="xhs", item_id="1", sentiment_score=0.8),
        MockMediaCrawlerNewsItem(title="茅台突破新高", content="大涨", platform="bili", item_id="2", sentiment_score=0.6),
        MockMediaCrawlerNewsItem(title="茅台风险提示", content="估值偏高", platform="wb", item_id="3", sentiment_score=-0.3),
        MockMediaCrawlerNewsItem(title="茅台讨论", content="", platform="zhihu", item_id="4", sentiment_score=0.1),
    ]
    crawler.search_multi_platform.return_value = {
        "小红书": MockMediaCrawlerResult(success=True, items=[items[0]], total_count=1),
        "B站": MockMediaCrawlerResult(success=True, items=[items[1]], total_count=1),
        "微博": MockMediaCrawlerResult(success=True, items=[items[2]], total_count=1),
        "知乎": MockMediaCrawlerResult(success=True, items=[items[3]], total_count=1),
    }
    return crawler


@pytest.fixture
def mock_sentiment_engine():
    """模拟 NewsSentimentEngine, analyze 返回正向情感"""
    engine = MagicMock()
    engine.add_news_batch.return_value = 4
    engine.analyze.return_value = MockSentimentResult(
        signals={
            "600519.SH": MockSentimentSignal(
                symbol="600519.SH",
                composite_sentiment=0.4,
                news_count_24h=4,
                news_count_7d=4,
                weighted_sentiment=0.35,
                breaking_news=False,
                event_distribution={},
                propagated_sentiment=0.0,
                signal_strength=0.4,
                confidence=0.8,
            )
        }
    )
    return engine


@pytest.fixture
def source(mock_media_crawler, mock_sentiment_engine):
    """带 mock 采集器/打分引擎的 SentimentSignalSource"""
    return SentimentSignalSource(
        media_crawler=mock_media_crawler,
        sentiment_engine=mock_sentiment_engine,
    )


# ============================================================
# 测试组 1: 中性降级
# ============================================================

class TestNeutralDegradation:
    """采集失败/无数据时返回中性信号"""

    def test_empty_code(self, source):
        result = source.get_signal("")
        assert result is not None
        assert result.source == "sentiment"
        assert result.score == 0.5
        assert result.action == "HOLD"
        assert result.confidence == 0.0

    def test_no_data(self, mock_media_crawler, mock_sentiment_engine):
        crawler = MagicMock()
        crawler.search_multi_platform.return_value = {}
        src = SentimentSignalSource(media_crawler=crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == 0.5
        assert result.confidence == 0.0
        assert "无舆情数据" in result.reason

    def test_all_platforms_failed(self, mock_media_crawler, mock_sentiment_engine):
        crawler = MagicMock()
        crawler.search_multi_platform.return_value = {
            "小红书": MockMediaCrawlerResult(success=False, error="timeout"),
            "B站": MockMediaCrawlerResult(success=False, error="503"),
        }
        src = SentimentSignalSource(media_crawler=crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == 0.5
        assert result.confidence == 0.0

    def test_crawler_exception(self, mock_sentiment_engine):
        crawler = MagicMock()
        crawler.search_multi_platform.side_effect = RuntimeError("connection refused")
        src = SentimentSignalSource(media_crawler=crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == 0.5
        assert result.confidence == 0.0


# ============================================================
# 测试组 2: 采集 + 打分 → SignalResult 转换
# ============================================================

class TestSignalConversion:
    """SentimentSignal → SignalResult 转换正确性"""

    def test_basic_signal(self, source):
        result = source.get_signal("600519.SH")
        assert result is not None
        assert result.code == "600519.SH"
        assert result.source == "sentiment"
        assert 0.0 <= result.score <= 1.0
        assert result.action in ("BUY", "SELL", "HOLD")
        assert 0.0 <= result.confidence <= 1.0
        assert result.timestamp != ""

    def test_positive_sentiment_to_buy(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=0.5, news_count_24h=5,
                    news_count_7d=5, weighted_sentiment=0.5, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=0.5, confidence=0.9,
                )
            }
        )
        src = SentimentSignalSource(media_crawler=mock_media_crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == pytest.approx(0.75, abs=0.01)
        assert result.action == "BUY"
        assert result.confidence == pytest.approx(0.9, abs=0.01)

    def test_negative_sentiment_to_sell(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=-0.5, news_count_24h=5,
                    news_count_7d=5, weighted_sentiment=-0.5, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=-0.5, confidence=0.9,
                )
            }
        )
        src = SentimentSignalSource(media_crawler=mock_media_crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == pytest.approx(0.25, abs=0.01)
        assert result.action == "SELL"

    def test_neutral_sentiment_to_hold(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=0.0, news_count_24h=5,
                    news_count_7d=5, weighted_sentiment=0.0, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=0.0, confidence=0.9,
                )
            }
        )
        src = SentimentSignalSource(media_crawler=mock_media_crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == pytest.approx(0.5, abs=0.01)
        assert result.action == "HOLD"

    def test_composite_clamped_to_range(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=2.0, news_count_24h=5,
                    news_count_7d=5, weighted_sentiment=2.0, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=2.0, confidence=0.9,
                )
            }
        )
        src = SentimentSignalSource(media_crawler=mock_media_crawler, sentiment_engine=mock_sentiment_engine)
        result = src.get_signal("600519.SH")
        assert result.score == pytest.approx(1.0, abs=0.01)


# ============================================================
# 测试组 3: confidence 降权
# ============================================================

class TestConfidenceGating:
    """confidence < min_confidence 时自动降权"""

    def test_low_confidence_degraded(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=0.8, news_count_24h=1,
                    news_count_7d=1, weighted_sentiment=0.8, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=0.8, confidence=0.2,
                )
            }
        )
        src = SentimentSignalSource(
            media_crawler=mock_media_crawler,
            sentiment_engine=mock_sentiment_engine,
            config=SentimentSourceConfig(min_confidence=0.3),
        )
        result = src.get_signal("600519.SH")
        assert result.confidence == 0.0
        assert result.action == "HOLD"

    def test_high_confidence_passes(self, mock_media_crawler, mock_sentiment_engine):
        mock_sentiment_engine.analyze.return_value = MockSentimentResult(
            signals={
                "600519.SH": MockSentimentSignal(
                    symbol="600519.SH", composite_sentiment=0.8, news_count_24h=5,
                    news_count_7d=5, weighted_sentiment=0.8, breaking_news=False,
                    event_distribution={}, propagated_sentiment=0.0,
                    signal_strength=0.8, confidence=0.9,
                )
            }
        )
        src = SentimentSignalSource(
            media_crawler=mock_media_crawler,
            sentiment_engine=mock_sentiment_engine,
            config=SentimentSourceConfig(min_confidence=0.3),
        )
        result = src.get_signal("600519.SH")
        assert result.confidence == pytest.approx(0.9, abs=0.01)
        assert result.action == "BUY"


# ============================================================
# 测试组 4: 多平台聚合
# ============================================================

class TestMultiPlatformAggregation:
    """多平台采集聚合"""

    def test_platforms_in_reason(self, source):
        result = source.get_signal("600519.SH")
        assert "platforms" in result.reason
        assert "xhs" in result.reason or "小红书" in result.reason or "bili" in result.reason

    def test_custom_platforms(self, mock_media_crawler, mock_sentiment_engine):
        src = SentimentSignalSource(
            media_crawler=mock_media_crawler,
            sentiment_engine=mock_sentiment_engine,
            config=SentimentSourceConfig(platforms=["bili", "wb"]),
        )
        src.get_signal("600519.SH")
        call_args = mock_media_crawler.search_multi_platform.call_args
        assert call_args.kwargs["platforms"] == ["bili", "wb"]


# ============================================================
# 测试组 5: 关键词解析
# ============================================================

class TestKeywordResolver:
    """股票代码 → 搜索关键词"""

    def test_default_resolver_strips_suffix(self, source):
        result = source.get_signal("600519.SH")
        assert result is not None
        call_args = source._media_crawler.search_multi_platform.call_args
        assert call_args.kwargs["keyword"] == "600519"

    def test_custom_resolver(self, mock_media_crawler, mock_sentiment_engine):
        src = SentimentSignalSource(
            media_crawler=mock_media_crawler,
            sentiment_engine=mock_sentiment_engine,
            config=SentimentSourceConfig(keyword_resolver=lambda c: "贵州茅台"),
        )
        src.get_signal("600519.SH")
        call_args = mock_media_crawler.search_multi_platform.call_args
        assert call_args.kwargs["keyword"] == "贵州茅台"


# ============================================================
# 测试组 6: SignalFusionEngine 注册集成
# ============================================================

class TestFusionEngineRegistration:
    """注册到 SignalFusionEngine"""

    def test_register_sentiment_source(self, source):
        from utils.signal_fusion import SignalFusionEngine
        engine = SignalFusionEngine()
        engine.register_source("sentiment", source.get_signal, initial_weight=0.05)
        assert "sentiment" in engine._sources
        assert engine._source_weights["sentiment"] == pytest.approx(0.05, abs=0.01)

    def test_register_then_get_signal(self, source):
        from utils.signal_fusion import SignalFusionEngine
        engine = SignalFusionEngine()
        engine.register_source("sentiment", source.get_signal, initial_weight=0.05)
        getter = engine._sources["sentiment"]
        result = getter("600519.SH")
        assert result is not None
        assert result.source == "sentiment"
