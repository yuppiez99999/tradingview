"""
新闻智能信号源 — 把 LLM 驱动的新闻深度分析适配为 SignalFusionEngine 的第 7 信号源

接入链路:
    NewsIntelligenceEngine (WebScraper 采集 + GLM5Client LLM 分析)
        → NewsIntelligenceSignalSource.get_signal(code) -> SignalResult
        → SignalFusionEngine.register_source("news_intel", source.get_signal)

区别于 sentiment 信号源 (MediaCrawler 自媒体 + 词典打分):
- 新闻智能: 财经新闻 (新浪/东方财富) + LLM 深度解读
- sentiment: 自媒体舆情 (小红书/B站/微博) + 词典打分
两者互补, 可同时注册.

设计依据: docs/集成记录/S1/集成设计_20260821.md
上游: TradingAgents/news_analyst.py (灵感) + utils/news_intelligence.py
下游: utils/signal_fusion.py SignalFusionEngine

关键约束:
- score ∈ [0, 1] (SignalFusionEngine 约定, 越高越看多)
- NewsIntelligenceReport.score 已经是 [0, 1], 直接透传
- confidence < min_confidence 时自动降权 (返回 confidence=0, action=HOLD)
- 采集失败/LLM 不可用时返回中性 SignalResult (score=0.5, confidence=0)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

try:
    from ..logging_manager import get_logger

    logger = get_logger("news_intelligence_signal_source")
except ImportError:
    logger = logging.getLogger("news_intelligence_signal_source")

try:
    from ..signal_fusion import SignalResult
except ImportError:
    SignalResult = None  # type: ignore[assignment,misc]

try:
    from ..news_intelligence import NewsIntelligenceEngine, NewsIntelligenceReport
except ImportError:
    NewsIntelligenceEngine = None  # type: ignore[assignment,misc]
    NewsIntelligenceReport = None  # type: ignore[assignment,misc]


# ============================================================
# 默认配置
# ============================================================

DEFAULT_INITIAL_WEIGHT: float = 0.08
DEFAULT_MIN_CONFIDENCE: float = 0.3
DEFAULT_LOOKBACK_DAYS: int = 7
DEFAULT_ARTICLE_LIMIT: int = 20


# ============================================================
# 新闻智能信号源
# ============================================================


class NewsIntelligenceSignalSource:
    """新闻智能信号源 — 适配 SignalFusionEngine.register_source 的 getter 签名

    使用方式:
        source = NewsIntelligenceSignalSource()
        engine = SignalFusionEngine()
        engine.register_source("news_intel", source.get_signal, initial_weight=0.08)

    或带自定义引擎 (测试用):
        source = NewsIntelligenceSignalSource(
            engine=my_engine,
            min_confidence=0.4,
        )
    """

    SOURCE_NAME: str = "news_intel"

    def __init__(
        self,
        engine: Optional[Any] = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        article_limit: int = DEFAULT_ARTICLE_LIMIT,
    ) -> None:
        self.min_confidence = min_confidence
        self.lookback_days = lookback_days
        self.article_limit = article_limit

        self._engine = engine
        self._owns_engine = engine is None

    # ------------------------------------------------------------
    # 懒加载引擎
    # ------------------------------------------------------------

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if NewsIntelligenceEngine is None:
            raise RuntimeError(
                "NewsIntelligenceEngine 未导入, 检查 utils.news_intelligence"
            )
        try:
            self._engine = NewsIntelligenceEngine(
                lookback_days=self.lookback_days,
                article_limit=self.article_limit,
                min_confidence=self.min_confidence,
            )
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.warning("NewsIntelligenceEngine 初始化失败: %s", e)
            raise
        return self._engine

    # ------------------------------------------------------------
    # 核心接口: get_signal(code) -> SignalResult
    # ------------------------------------------------------------

    def get_signal(self, code: str) -> Any:
        """获取某股票的新闻智能信号

        Args:
            code: 股票代码, 如 "600519.SH" / "000001.SZ"

        Returns:
            SignalResult(source="news_intel", score ∈ [0,1], confidence ∈ [0,1])
            采集失败/无数据时返回中性信号 (score=0.5, confidence=0)
        """
        if not code:
            return self._neutral_signal(code, "空代码")

        if SignalResult is None:
            logger.debug("SignalResult 未导入, 跳过 news_intel 信号")
            return None

        try:
            engine = self._get_engine()
            report = engine.get_report(code)
            if report is None:
                return self._neutral_signal(code, "引擎返回空报告")
            return self._to_signal_result(report)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("news_intel 信号获取异常 code=%s: %s", code, e)
            return self._neutral_signal(code, f"获取异常: {e}")

    # ------------------------------------------------------------
    # NewsIntelligenceReport → SignalResult
    # ------------------------------------------------------------

    def _to_signal_result(self, report: Any) -> Any:
        """把 NewsIntelligenceReport 转为 SignalResult"""
        score = float(getattr(report, "score", 0.5))
        score = max(0.0, min(1.0, score))

        confidence = float(getattr(report, "confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        action = str(getattr(report, "action", "HOLD")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"

        if confidence < self.min_confidence:
            confidence = 0.0
            action = "HOLD"

        summary = getattr(report, "summary", "")
        article_count = int(getattr(report, "article_count", 0))
        fallback = bool(getattr(report, "fallback_used", False))
        source_tag = "词典降级" if fallback else "LLM"

        reason = (
            f"新闻智能({source_tag}): score={score:.3f}, "
            f"action={action}, articles={article_count}, "
            f"summary={summary[:80]}"
        )

        return SignalResult(
            code=getattr(report, "code", ""),
            source=self.SOURCE_NAME,
            score=score,
            action=action,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

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
        if self._owns_engine and self._engine is not None:
            close_fn = getattr(self._engine, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except (ValueError, TypeError, RuntimeError, OSError):
                    pass
