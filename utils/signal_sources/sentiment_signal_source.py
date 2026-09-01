"""
舆情情感信号源 — 把自媒体舆情采集 + 情感打分适配为 SignalFusionEngine 的第 6 信号源

接入链路:
    MediaCrawlerAdapter (7 平台采集: 小红书/B站/微博/知乎/抖音/快手/贴吧)
        → NewsSentimentEngine (中文金融情感词典打分)
        → SentimentSignalSource.get_signal(code) -> SignalResult
        → SignalFusionEngine.register_source("sentiment", source.get_signal)

设计依据: docs/1设计计划集成到系统内并能完整运行_20260817.md W.A.1
上游: docs/1 (OpenBiliClaw 接入建议) + utils/media_crawler_adapter.py + utils/news_sentiment_engine.py
下游: utils/signal_fusion.py SignalFusionEngine

关键约束:
- score ∈ [0, 1] (SignalFusionEngine 约定, 越高越看多)
- composite_sentiment ∈ [-1, 1] (NewsSentimentEngine 约定) → score = (sentiment + 1) / 2
- confidence < min_confidence 时自动降权 (返回 confidence=0, action=HOLD)
- 采集失败/无数据时返回中性 SignalResult (score=0.5, confidence=0)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

try:
    from ..logging_manager import get_logger
except (ImportError, ValueError):

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger("sentiment_signal_source")

try:
    from ..signal_fusion import SignalResult
except (ImportError, ValueError):
    SignalResult = None  # type: ignore[assignment,misc]

try:
    from ..media_crawler_adapter import MediaCrawlerAdapter, MediaCrawlerNewsItem
except (ImportError, ValueError):
    MediaCrawlerAdapter = None  # type: ignore[assignment,misc]
    MediaCrawlerNewsItem = None  # type: ignore[assignment,misc]

try:
    from ..news_sentiment_engine import NewsItem, NewsSentimentEngine
except (ImportError, ValueError):
    NewsItem = None  # type: ignore[assignment,misc]
    NewsSentimentEngine = None  # type: ignore[assignment,misc]


# ============================================================
# 默认配置
# ============================================================

DEFAULT_PLATFORMS: list[str] = ["xhs", "bili", "wb", "zhihu"]
DEFAULT_MAX_ITEMS_PER_PLATFORM: int = 15
DEFAULT_MIN_CONFIDENCE: float = 0.3
DEFAULT_NEWS_LOOKBACK_DAYS: int = 7


@dataclass
class SentimentSourceConfig:
    """舆情信号源配置"""

    platforms: list[str] = field(default_factory=lambda: list(DEFAULT_PLATFORMS))
    max_items_per_platform: int = DEFAULT_MAX_ITEMS_PER_PLATFORM
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    news_lookback_days: int = DEFAULT_NEWS_LOOKBACK_DAYS
    enable_comments: bool = False
    keyword_resolver: Callable[[str], str] | None = None


# ============================================================
# 股票代码 → 搜索关键词 的默认映射
# ============================================================


def _default_keyword_resolver(code: str) -> str:
    """默认关键词解析: 去掉 .SH/.SZ 后缀, 用纯数字代码搜索

    后续可接入 config/positions.json 的 name 字段做 代码→名称 映射,
    但首版用纯代码避免引入额外依赖。
    """
    if not code:
        return ""
    return code.split(".")[0]


# ============================================================
# 舆情情感信号源
# ============================================================


class SentimentSignalSource:
    """舆情情感信号源 — 适配 SignalFusionEngine.register_source 的 getter 签名

    使用方式:
        source = SentimentSignalSource()
        engine = SignalFusionEngine()
        engine.register_source("sentiment", source.get_signal, initial_weight=0.05)

    或带自定义采集器/打分引擎:
        source = SentimentSignalSource(
            media_crawler=my_adapter,
            sentiment_engine=my_engine,
            config=SentimentSourceConfig(platforms=["bili", "wb"]),
        )
    """

    SOURCE_NAME: str = "sentiment"

    def __init__(
        self,
        media_crawler: Any | None = None,
        sentiment_engine: Any | None = None,
        config: SentimentSourceConfig | None = None,
    ) -> None:
        self.config = config or SentimentSourceConfig()
        self._keyword_resolver = (
            self.config.keyword_resolver or _default_keyword_resolver
        )

        self._media_crawler = media_crawler
        self._sentiment_engine = sentiment_engine
        self._owns_media_crawler = media_crawler is None
        self._owns_sentiment_engine = sentiment_engine is None

    # ------------------------------------------------------------
    # 懒加载采集器 / 打分引擎
    # ------------------------------------------------------------

    def _get_media_crawler(self) -> Any:
        if self._media_crawler is not None:
            return self._media_crawler
        if MediaCrawlerAdapter is None:
            raise RuntimeError(
                "MediaCrawlerAdapter 未导入, 检查 utils.media_crawler_adapter"
            )
        try:
            self._media_crawler = MediaCrawlerAdapter()
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.warning("MediaCrawlerAdapter 初始化失败: %s", e)
            raise
        return self._media_crawler

    def _get_sentiment_engine(self) -> Any:
        if self._sentiment_engine is not None:
            return self._sentiment_engine
        if NewsSentimentEngine is None:
            raise RuntimeError(
                "NewsSentimentEngine 未导入, 检查 utils.news_sentiment_engine"
            )
        try:
            self._sentiment_engine = NewsSentimentEngine()
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.warning("NewsSentimentEngine 初始化失败: %s", e)
            raise
        return self._sentiment_engine

    # ------------------------------------------------------------
    # 核心接口: get_signal(code) -> SignalResult
    # ------------------------------------------------------------

    def get_signal(self, code: str) -> Any:
        """获取某股票的舆情情感信号

        Args:
            code: 股票代码, 如 "600519.SH" / "000001.SZ"

        Returns:
            SignalResult(source="sentiment", score ∈ [0,1], confidence ∈ [0,1])
            采集失败/无数据时返回中性信号 (score=0.5, confidence=0)
        """
        if not code:
            return self._neutral_signal(code, "空代码")

        if SignalResult is None:
            logger.debug("SignalResult 未导入, 跳过 sentiment 信号")
            return None

        try:
            keyword = self._keyword_resolver(code)
            if not keyword:
                return self._neutral_signal(code, "关键词解析为空")

            items = self._fetch_sentiment_items(keyword)
            if not items:
                return self._neutral_signal(code, f"无舆情数据 (keyword={keyword})")

            sentiment_signal = self._analyze_sentiment(code, items)
            if sentiment_signal is None:
                return self._neutral_signal(code, "情感分析返回空")

            return self._to_signal_result(code, sentiment_signal, items)

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("sentiment 信号采集异常 code=%s: %s", code, e)
            return self._neutral_signal(code, f"采集异常: {e}")

    # ------------------------------------------------------------
    # 采集 + 打分
    # ------------------------------------------------------------

    def _fetch_sentiment_items(self, keyword: str) -> list[Any]:
        """从多平台采集舆情条目"""
        try:
            crawler = self._get_media_crawler()
            results = crawler.search_multi_platform(
                keyword=keyword,
                platforms=self.config.platforms,
                max_items_per_platform=self.config.max_items_per_platform,
                enable_comments=self.config.enable_comments,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("MediaCrawler 采集失败 keyword=%s: %s", keyword, e)
            return []

        items: list[Any] = []
        for _platform_display, result in results.items():
            if not getattr(result, "success", False):
                continue
            for item in getattr(result, "items", []):
                items.append(item)
        return items

    def _analyze_sentiment(self, code: str, items: list[Any]) -> Any:
        """把舆情条目喂入 NewsSentimentEngine 打分"""
        try:
            engine = self._get_sentiment_engine()
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("NewsSentimentEngine 获取失败: %s", e)
            return None

        news_items = [self._convert_to_news_item(it, code) for it in items]
        news_items = [ni for ni in news_items if ni is not None]
        if not news_items:
            return None

        try:
            engine.add_news_batch(news_items)
            result = engine.analyze([code])
            signals = getattr(result, "signals", {})
            return signals.get(code)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("NewsSentimentEngine 分析失败 code=%s: %s", code, e)
            return None

    def _convert_to_news_item(self, item: Any, code: str) -> Any:
        """MediaCrawlerNewsItem → NewsItem"""
        if NewsItem is None:
            return None
        try:
            news_id = (
                f"{getattr(item, 'platform', 'unknown')}_{getattr(item, 'item_id', '')}"
            )
            if not news_id or news_id == "_":
                news_id = f"sent_{id(item)}"

            publish_time = self._parse_time(getattr(item, "published_at", ""))
            symbols = [code] if code else []
            if getattr(item, "symbol", ""):
                symbols.append(item.symbol)

            return NewsItem(
                news_id=news_id,
                title=getattr(item, "title", "") or "",
                content=getattr(item, "content", "") or "",
                source=getattr(item, "source", "") or getattr(item, "platform", ""),
                publish_time=publish_time,
                symbols=symbols,
                sentiment_score=float(getattr(item, "sentiment_score", 0.0)),
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug("NewsItem 转换失败: %s", e)
            return None

    @staticmethod
    def _parse_time(time_str: str) -> datetime | None:
        if not time_str:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(time_str[:19], fmt)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------
    # SentimentSignal → SignalResult
    # ------------------------------------------------------------

    def _to_signal_result(
        self, code: str, sentiment_signal: Any, items: list[Any]
    ) -> Any:
        """把 SentimentSignal 转为 SignalResult"""
        composite = float(getattr(sentiment_signal, "composite_sentiment", 0.0))
        composite = max(-1.0, min(1.0, composite))

        confidence = float(getattr(sentiment_signal, "confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        news_count_24h = int(getattr(sentiment_signal, "news_count_24h", 0))

        score = (composite + 1.0) / 2.0
        action = self._score_to_action(score)

        if confidence < self.config.min_confidence:
            confidence = 0.0
            action = "HOLD"

        platform_counts = self._count_platforms(items)
        reason = (
            f"舆情情感: composite={composite:.3f}, news_24h={news_count_24h}, "
            f"platforms={platform_counts}"
        )

        return SignalResult(
            code=code,
            source=self.SOURCE_NAME,
            score=score,
            action=action,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    @staticmethod
    def _score_to_action(score: float) -> str:
        if score > 0.6:
            return "BUY"
        if score < 0.4:
            return "SELL"
        return "HOLD"

    @staticmethod
    def _count_platforms(items: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in items:
            p = getattr(it, "platform", "") or getattr(it, "source", "unknown")
            counts[p] = counts.get(p, 0) + 1
        return counts

    # ------------------------------------------------------------
    # 中性信号 (降级)
    # ------------------------------------------------------------

    def _neutral_signal(self, code: str, reason: str) -> Any:
        if SignalResult is None:
            return None
        return SignalResult(
            code=code,
            source=self.SOURCE_NAME,
            score=0.5,
            action="HOLD",
            confidence=0.0,
            reason=f"中性降级: {reason}",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # ------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------

    def close(self) -> None:
        if self._owns_media_crawler and self._media_crawler is not None:
            close_fn = getattr(self._media_crawler, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except (ValueError, TypeError, RuntimeError, OSError):
                    pass
        if self._owns_sentiment_engine and self._sentiment_engine is not None:
            close_fn = getattr(self._sentiment_engine, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except (ValueError, TypeError, RuntimeError, OSError):
                    pass
