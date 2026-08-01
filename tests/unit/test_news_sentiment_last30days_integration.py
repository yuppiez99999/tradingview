"""news_sentiment_engine × last30days_adapter 集成测试.

测试目标:
    1. Flag 透传 (HC-1): USE_LAST30DAYS_SENTIMENT=False 时注入返回 0
    2. 信号转换: Last30DaysSignal → NewsItem 字段正确映射
    3. 标的关联: symbol_mapping / default_symbols 正确生效
    4. 情感聚合: 注入后 analyze() 能正确反映社交情感
    5. 失败安全: 单条异常信号不阻断批量注入
    6. 不可变性: 注入不影响原 signals 列表
    7. 端到端: adapter.search_topic → engine.ingest_last30days_signals → analyze

设计原则:
    - 使用真实 NewsSentimentEngine 实例 (不 mock 引擎本身)
    - mock Last30DaysAdapter 的 CLI 调用 (不调用真实 npx)
    - mock feature_flags.is_enabled 控制开关
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from utils.last30days_adapter import Last30DaysAdapter, Last30DaysSignal
from utils.news_sentiment_engine import NewsItem, NewsSentimentEngine


# ============================================================
# 测试数据
# ============================================================
def _make_signal(
    topic: str = "gold price",
    platform: str = "reddit",
    sentiment: float = 0.6,
    mentions: int = 50,
    engagement: float = 2000.0,
    last_seen: str = "",
) -> Last30DaysSignal:
    """构造测试信号."""
    if not last_seen:
        last_seen = datetime.now().isoformat()
    return Last30DaysSignal(
        topic=topic,
        platform=platform,
        sentiment_score=sentiment,
        mention_count=mentions,
        engagement_score=engagement,
        first_seen=last_seen,
        last_seen=last_seen,
        source_urls=[f"https://{platform}.com/test"],
        title=f"{platform} signal on {topic}",
        summary=f"Users on {platform} discuss {topic}",
    )


def _make_signals_mixed() -> list[Last30DaysSignal]:
    """构造混合情感的多平台信号."""
    return [
        _make_signal("gold price", "reddit", sentiment=0.7, mentions=100, engagement=3000.0),
        _make_signal("gold price", "x", sentiment=-0.4, mentions=60, engagement=1500.0),
        _make_signal("AI chip", "hn", sentiment=0.5, mentions=40, engagement=800.0),
    ]


# ============================================================
# 1. Flag 透传 (HC-1)
# ============================================================
class TestFlagGate:
    """Flag 关闭时必须返回 0 且不修改引擎状态."""

    def test_flag_disabled_returns_zero(self) -> None:
        engine = NewsSentimentEngine()
        signals = _make_signals_mixed()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            count = engine.ingest_last30days_signals(signals)
        assert count == 0

    def test_flag_disabled_engine_state_unchanged(self) -> None:
        engine = NewsSentimentEngine()
        initial_news_count = sum(len(v) for v in engine.news_store.values())
        signals = _make_signals_mixed()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            engine.ingest_last30days_signals(signals)
        after_count = sum(len(v) for v in engine.news_store.values())
        assert after_count == initial_news_count

    def test_empty_signals_returns_zero_even_if_flag_enabled(self) -> None:
        engine = NewsSentimentEngine()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            count = engine.ingest_last30days_signals([])
        assert count == 0


# ============================================================
# 2. 信号转换 (字段映射)
# ============================================================
class TestSignalConversion:
    """Last30DaysSignal → NewsItem 字段正确映射."""

    def test_basic_field_mapping(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(
            topic="gold price",
            platform="reddit",
            sentiment=0.7,
            mentions=50,
            engagement=2000.0,
        )
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            count = engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])

        assert count == 1
        # 检查 news_store 中的 NewsItem
        all_news = [n for v in engine.news_store.values() for n in v]
        assert len(all_news) == 1
        news = all_news[0]
        # 来源标记
        assert news.source == "last30days:reddit"
        # 标题
        assert "reddit" in news.title
        # 情感 (社交信号原生 sentiment 应被保留, 不被词典覆盖)
        assert news.sentiment_score == 0.7
        assert news.sentiment_label == "POSITIVE"
        # 事件类型
        assert news.event_type == "SOCIAL"
        # 影响时长 (社交信号 12 小时)
        assert news.impact_horizon_hours == 12

    def test_sentiment_label_negative(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(sentiment=-0.6)
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])
        all_news = [n for v in engine.news_store.values() for n in v]
        assert all_news[0].sentiment_label == "NEGATIVE"

    def test_sentiment_label_neutral(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(sentiment=0.1)
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])
        all_news = [n for v in engine.news_store.values() for n in v]
        assert all_news[0].sentiment_label == "NEUTRAL"

    def test_publish_time_parsed_from_last_seen(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(last_seen="2026-07-15T18:30:00Z")
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])
        all_news = [n for v in engine.news_store.values() for n in v]
        # publish_time 应解析为 2026-07-15 18:30:00
        assert all_news[0].publish_time is not None
        assert all_news[0].publish_time.year == 2026
        assert all_news[0].publish_time.month == 7
        assert all_news[0].publish_time.day == 15

    def test_invalid_time_falls_back_to_now(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(last_seen="not-a-date")
        before = datetime.now()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])
        all_news = [n for v in engine.news_store.values() for n in v]
        after = datetime.now()
        # 应降级为当前时间
        assert before <= all_news[0].publish_time <= after

    def test_content_includes_summary_and_urls(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal()
        sig.summary = "Bullish gold discussion"
        sig.source_urls = ["https://reddit.com/r/gold/abc", "https://reddit.com/r/gold/def"]
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig], default_symbols=["600916.SH"])
        all_news = [n for v in engine.news_store.values() for n in v]
        content = all_news[0].content
        assert "Bullish gold discussion" in content
        assert "reddit.com/r/gold/abc" in content

    def test_confidence_scales_with_engagement(self) -> None:
        engine = NewsSentimentEngine()
        low_engagement = _make_signal(mentions=2, engagement=10.0)
        high_engagement = _make_signal(mentions=100, engagement=5000.0)
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(
                [low_engagement, high_engagement], default_symbols=["600916.SH"]
            )
        all_news = [n for v in engine.news_store.values() for n in v]
        # 按 source 找回 (两个信号 platform 都是 reddit, 但 news_id 不同)
        # low_engagement 的置信度应低于 high_engagement
        confidences = sorted([n.confidence for n in all_news])
        assert confidences[0] < confidences[-1]
        # 高互动信号置信度应较高 (>0.5)
        assert confidences[-1] > 0.5


# ============================================================
# 3. 标的关联
# ============================================================
class TestSymbolAssociation:
    """symbol_mapping 和 default_symbols 行为."""

    def test_symbol_mapping_associates_correctly(self) -> None:
        engine = NewsSentimentEngine()
        sig_gold = _make_signal(topic="gold price")
        sig_ai = _make_signal(topic="AI chip")
        symbol_mapping = {
            "gold price": ["600916.SH"],  # 山金
            "AI chip": ["002475.SZ"],  # 立讯
        }
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig_gold, sig_ai], symbol_mapping=symbol_mapping)
        # 600916.SH 应有 1 条新闻
        assert len(engine.news_store.get("600916.SH", [])) == 1
        assert len(engine.news_store.get("002475.SZ", [])) == 1

    def test_default_symbols_used_when_topic_not_in_mapping(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(topic="unknown topic")
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(
                [sig], symbol_mapping={"gold": ["600916.SH"]}, default_symbols=["000001.SZ"]
            )
        # topic 不在 mapping 中, 应回退到 default_symbols
        assert len(engine.news_store.get("000001.SZ", [])) == 1

    def test_no_symbols_when_mapping_and_default_both_empty(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(topic="anything")
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            count = engine.ingest_last30days_signals([sig])
        # 仍应注入成功, 但 news.symbols 为空
        assert count == 1
        all_news = [n for v in engine.news_store.values() for n in v]
        # 由于 symbols 为空, news 不会被存入 news_store (add_news 遍历 symbols)
        # 但 ingested count 仍为 1 (add_news 调用成功)
        assert len(all_news) == 0


# ============================================================
# 4. 情感聚合 (端到端)
# ============================================================
class TestSentimentAggregation:
    """注入后 analyze() 应反映社交情感."""

    def test_analyze_reflects_injected_signals(self) -> None:
        engine = NewsSentimentEngine()
        # 注入 3 条正面 reddit 信号, 关联到 600916.SH
        signals = [
            _make_signal(topic="gold", platform="reddit", sentiment=0.7, mentions=80, engagement=2000.0),
            _make_signal(topic="gold", platform="x", sentiment=0.5, mentions=60, engagement=1500.0),
        ]
        symbol_mapping = {"gold": ["600916.SH"]}
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(signals, symbol_mapping=symbol_mapping)

        result = engine.analyze(["600916.SH"])
        sig = result.signals.get("600916.SH")
        assert sig is not None
        # 综合情感应为正
        assert sig.composite_sentiment > 0.3
        assert sig.news_count_24h == 2

    def test_market_sentiment_updated(self) -> None:
        engine = NewsSentimentEngine()
        signals = _make_signals_mixed()
        symbol_mapping = {
            "gold price": ["600916.SH"],
            "AI chip": ["002475.SZ"],
        }
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(signals, symbol_mapping=symbol_mapping)

        result = engine.analyze(["600916.SH", "002475.SZ"])
        # market_sentiment 应为所有标的 composite 的平均
        assert result.market_sentiment != 0.0
        # 3 条信号: reddit(0.7) + x(-0.4) + hn(0.5) → 平均 0.27
        assert 0.0 < result.market_sentiment < 0.6


# ============================================================
# 5. 失败安全
# ============================================================
class TestFailSafe:
    """单条异常信号不阻断批量注入."""

    def test_bad_signal_does_not_block_others(self) -> None:
        engine = NewsSentimentEngine()
        good_sig = _make_signal(topic="gold")
        # 构造一个坏信号 (属性会触发异常)
        bad_sig = _make_signal(topic="bad")
        # 模拟坏信号在 add_news 时抛异常
        original_add = engine.add_news
        call_count = {"n": 0}

        def flaky_add(news: NewsItem) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated failure")
            original_add(news)

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch.object(
            engine, "add_news", side_effect=flaky_add
        ):
            count = engine.ingest_last30days_signals([bad_sig, good_sig])
        # 一条失败, 一条成功
        assert count == 1

    def test_no_symbols_does_not_crash(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            # 无 symbol_mapping, 无 default_symbols → symbols 为空
            count = engine.ingest_last30days_signals([sig])
        assert count == 1  # 仍计入 ingested


# ============================================================
# 6. 不可变性
# ============================================================
class TestImmutability:
    """注入不应修改原 signals 列表."""

    def test_signals_list_not_modified(self) -> None:
        engine = NewsSentimentEngine()
        signals = _make_signals_mixed()
        original_len = len(signals)
        original_topics = [s.topic for s in signals]
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(signals)
        assert len(signals) == original_len
        assert [s.topic for s in signals] == original_topics

    def test_signal_objects_not_modified(self) -> None:
        engine = NewsSentimentEngine()
        sig = _make_signal(sentiment=0.5)
        original_sentiment = sig.sentiment_score
        original_mentions = sig.mention_count
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals([sig])
        assert sig.sentiment_score == original_sentiment
        assert sig.mention_count == original_mentions


# ============================================================
# 7. 端到端: adapter → engine
# ============================================================
class TestEndToEnd:
    """从 Last30DaysAdapter 到 NewsSentimentEngine 的完整链路."""

    def test_adapter_to_engine_full_flow(self) -> None:
        """模拟 adapter 返回信号, engine 注入并分析."""
        adapter = Last30DaysAdapter()
        engine = NewsSentimentEngine()

        # mock adapter 的 CLI 调用返回 2 条信号
        mock_signals = [
            _make_signal("gold price", "reddit", sentiment=0.6, mentions=80, engagement=2500.0),
            _make_signal("gold price", "x", sentiment=-0.2, mentions=40, engagement=500.0),
        ]

        # 同时 patch adapter 模块和 feature_flags 模块的 is_enabled 引用
        # (adapter 通过 `from utils.infra.feature_flags import is_enabled` 直接绑定)
        with patch("utils.last30days_adapter.is_enabled", return_value=True), patch(
            "utils.infra.feature_flags.is_enabled", return_value=True
        ), patch.object(
            adapter, "_query_cli", return_value=mock_signals
        ), patch.object(adapter, "_save_cache"), patch.object(adapter, "_check_cli", return_value=True):
            # 1) adapter 查询
            signals = adapter.search_topic("gold price", platforms=["reddit", "x"], days=7)
            assert len(signals) == 2

            # 2) engine 注入
            count = engine.ingest_last30days_signals(
                signals, symbol_mapping={"gold price": ["600916.SH"]}
            )
            assert count == 2

        # 3) analyze
        result = engine.analyze(["600916.SH"])
        sig = result.signals.get("600916.SH")
        assert sig is not None
        assert sig.news_count_24h == 2
        # 加权情感 (reddit 0.6 + x -0.2) / 2 = 0.2
        assert -0.1 < sig.composite_sentiment < 0.5

    def test_aggregate_sentiment_consistent_with_engine(self) -> None:
        """adapter.aggregate_sentiment 与 engine.analyze 应方向一致."""
        signals = _make_signals_mixed()
        adapter_agg = Last30DaysAdapter.aggregate_sentiment(signals)

        engine = NewsSentimentEngine()
        symbol_mapping = {"gold price": ["600916.SH"], "AI chip": ["002475.SZ"]}
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            engine.ingest_last30days_signals(signals, symbol_mapping=symbol_mapping)
        result = engine.analyze(["600916.SH", "002475.SZ"])

        # adapter 的 composite 应与 engine 的 market_sentiment 符号一致
        assert (adapter_agg["composite_sentiment"] > 0) == (result.market_sentiment > 0)
