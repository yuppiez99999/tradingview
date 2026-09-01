"""
新闻智能引擎 — LLM 驱动的深度新闻分析 + 多源采集

灵感来源: TradingAgents/news_analyst.py (langchain ChatPromptTemplate + tools)
适配 A 股: 中文 prompt + A 股代码→关键词 + 复用主系统 WebScraper/GLM5Client

区别于 NewsSentimentEngine (词典打分):
- 用 LLM (GLM-5/豆包) 做深度新闻解读, 输出结构化交易建议
- 多源采集 (新浪财经搜索 + 东方财富, 复用 WebScraper.fetch_news)
- LLM 不可用时降级到 NewsSentimentEngine 词典打分 (fail-safe)

接入链路:
    WebScraper.fetch_news(code→keyword) → NewsArticle 列表
        → GLM5Client.chat(中文 prompt + 新闻摘要) → NewsIntelligenceReport
        → NewsIntelligenceSignalSource → SignalFusionEngine.register_source("news_intel")

关键约束:
- score ∈ [0, 1] (SignalFusionEngine 约定, 越高越看多)
- LLM 返回的 composite_sentiment ∈ [-1, 1] → score = (sentiment + 1) / 2
- 采集失败/LLM 不可用时返回中性报告 (score=0.5, confidence=0)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

try:
    from .logging_manager import get_logger

    logger = get_logger("news_intelligence")
except ImportError:
    logger = logging.getLogger("news_intelligence")


# ============================================================
# 数据结构
# ============================================================


@dataclass(frozen=True)
class NewsArticle:
    """新闻条目 (采集结果, 不可变)"""

    title: str
    content: str = ""
    url: str = ""
    source: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class NewsIntelligenceReport:
    """新闻智能分析报告 (LLM 输出, 不可变)

    score ∈ [0, 1]: 0=极度看空, 0.5=中性, 1=极度看多
    action: BUY/SELL/HOLD
    confidence ∈ [0, 1]: LLM 置信度
    """

    code: str
    score: float = 0.5
    action: str = "HOLD"
    confidence: float = 0.0
    summary: str = ""
    key_points: tuple[str, ...] = field(default_factory=tuple)
    risk_factors: tuple[str, ...] = field(default_factory=tuple)
    article_count: int = 0
    source: str = "news_intelligence"
    timestamp: str = ""
    fallback_used: bool = False


# ============================================================
# 默认配置
# ============================================================

DEFAULT_LOOKBACK_DAYS: int = 7
DEFAULT_ARTICLE_LIMIT: int = 20
DEFAULT_MIN_CONFIDENCE: float = 0.3
DEFAULT_LLM_TIMEOUT_SECONDS: int = 30
MAX_NEWS_CHARS_IN_PROMPT: int = 4000


# ============================================================
# A 股代码 → 搜索关键词
# ============================================================


def resolve_keyword(code: str) -> str:
    """A 股代码 → 搜索关键词

    "600519.SH" → "600519"
    "000001.SZ" → "000001"
    "贵州茅台" → "贵州茅台"
    """
    if not code:
        return ""
    if "." in code:
        return code.split(".")[0]
    return code


# ============================================================
# 新闻智能引擎
# ============================================================


class NewsIntelligenceEngine:
    """新闻智能引擎 — LLM 驱动深度新闻分析 + 多源采集

    使用方式:
        engine = NewsIntelligenceEngine()
        report = engine.get_report("600519.SH")
        print(report.score, report.action, report.summary)

    或注入自定义采集器/LLM (测试用):
        engine = NewsIntelligenceEngine(
            web_scraper=mock_scraper,
            llm_client=mock_llm,
        )
    """

    def __init__(
        self,
        web_scraper: Any | None = None,
        llm_client: Any | None = None,
        sentiment_engine: Any | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        article_limit: int = DEFAULT_ARTICLE_LIMIT,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        self.lookback_days = lookback_days
        self.article_limit = article_limit
        self.min_confidence = min_confidence

        self._web_scraper = web_scraper
        self._llm_client = llm_client
        self._sentiment_engine = sentiment_engine
        self._owns_web_scraper = web_scraper is None
        self._owns_llm_client = llm_client is None
        self._owns_sentiment_engine = sentiment_engine is None

    # ------------------------------------------------------------
    # 懒加载依赖
    # ------------------------------------------------------------

    def _get_web_scraper(self) -> Any:
        if self._web_scraper is not None:
            return self._web_scraper
        try:
            from .web_scraper import WebScraper

            self._web_scraper = WebScraper()
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("WebScraper 初始化失败: %s", e)
            raise
        return self._web_scraper

    def _get_llm_client(self) -> Any:
        if self._llm_client is not None:
            return self._llm_client
        try:
            from .glm5_client import get_glm5_client

            self._llm_client = get_glm5_client()
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("GLM5Client 初始化失败: %s", e)
            raise
        return self._llm_client

    def _get_sentiment_engine(self) -> Any:
        if self._sentiment_engine is not None:
            return self._sentiment_engine
        try:
            from .news_sentiment_engine import NewsSentimentEngine

            self._sentiment_engine = NewsSentimentEngine()
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("NewsSentimentEngine 初始化失败: %s", e)
            raise
        return self._sentiment_engine

    # ------------------------------------------------------------
    # 核心接口: get_report(code) -> NewsIntelligenceReport
    # ------------------------------------------------------------

    def get_report(self, code: str) -> NewsIntelligenceReport:
        """采集 + 分析, 返回新闻智能报告

        Args:
            code: A 股代码, 如 "600519.SH" / "000001.SZ"

        Returns:
            NewsIntelligenceReport (score ∈ [0,1], action, confidence)
            采集失败/LLM 不可用时返回中性报告 (score=0.5, confidence=0)
        """
        if not code:
            return self._neutral_report(code, "空代码")

        try:
            keyword = resolve_keyword(code)
            if not keyword:
                return self._neutral_report(code, "关键词解析为空")

            articles = self._fetch_articles(keyword)
            if not articles:
                return self._neutral_report(code, f"无新闻数据 (keyword={keyword})")

            report = self._analyze_with_llm(code, articles)
            if report is not None:
                return report

            logger.info("LLM 分析失败, 降级到词典打分 code=%s", code)
            return self._fallback_to_sentiment(code, articles)

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("新闻智能分析异常 code=%s: %s", code, e)
            return self._neutral_report(code, f"分析异常: {e}")

    # ------------------------------------------------------------
    # 新闻采集
    # ------------------------------------------------------------

    def _fetch_articles(self, keyword: str) -> list[NewsArticle]:
        """从 WebScraper 采集新闻, 转为 NewsArticle"""
        try:
            scraper = self._get_web_scraper()
            raw_items = scraper.fetch_news(keyword, limit=self.article_limit)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("新闻采集失败 keyword=%s: %s", keyword, e)
            return []

        articles: list[NewsArticle] = []
        for item in raw_items:
            converted = self._convert_to_article(item)
            if converted is not None:
                articles.append(converted)
        return articles

    @staticmethod
    def _convert_to_article(item: Any) -> NewsArticle | None:
        """WebScraper.NewsItem / dict → NewsArticle"""
        try:
            if isinstance(item, dict):
                return NewsArticle(
                    title=str(item.get("title", "")),
                    content=str(item.get("content", ""))[:500],
                    url=str(item.get("url", "")),
                    source=str(item.get("source", "")),
                    published_at=str(item.get("published_at", "")),
                )
            return NewsArticle(
                title=getattr(item, "title", "") or "",
                content=(getattr(item, "content", "") or "")[:500],
                url=getattr(item, "url", "") or "",
                source=getattr(item, "source", "") or "",
                published_at=getattr(item, "published_at", "") or "",
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug("NewsArticle 转换失败: %s", e)
            return None

    # ------------------------------------------------------------
    # LLM 驱动深度分析
    # ------------------------------------------------------------

    def _analyze_with_llm(
        self, code: str, articles: list[NewsArticle]
    ) -> NewsIntelligenceReport | None:
        """用 LLM 分析新闻, 返回结构化报告"""
        try:
            client = self._get_llm_client()
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("LLM 客户端不可用: %s", e)
            return None

        prompt = self._build_prompt(code, articles)
        system_prompt = self._build_system_prompt()

        try:
            resp = client.chat(prompt, system_prompt=system_prompt)
            content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            if not content:
                return None
            return self._parse_llm_response(code, content, len(articles))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("LLM 调用失败 code=%s: %s", code, e)
            return None

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "你是 A 股新闻分析师。分析新闻对标的的影响, "
            "输出严格 JSON (不要 markdown 代码块). "
            "字段: score(0-1), action(BUY/SELL/HOLD), "
            "confidence(0-1), summary(一句话), "
            "key_points(数组), risk_factors(数组)."
        )

    @staticmethod
    def _build_prompt(code: str, articles: list[NewsArticle]) -> str:
        news_text = ""
        total_chars = 0
        for i, art in enumerate(articles, 1):
            entry = f"[{i}] {art.title}"
            if art.source:
                entry += f" (来源: {art.source})"
            entry += f"\n{art.content}\n"
            if total_chars + len(entry) > MAX_NEWS_CHARS_IN_PROMPT:
                break
            news_text += entry
            total_chars += len(entry)

        return (
            f"标的代码: {code}\n\n"
            f"近期新闻 (共 {len(articles)} 条):\n{news_text}\n\n"
            "请分析上述新闻对该标的的影响, 输出 JSON:"
        )

    def _parse_llm_response(
        self, code: str, content: str, article_count: int
    ) -> NewsIntelligenceReport | None:
        """解析 LLM 返回的 JSON, 构造 NewsIntelligenceReport"""
        json_str = self._extract_json(content)
        if not json_str:
            return None

        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("LLM JSON 解析失败: %s", e)
            return None

        try:
            score = float(data.get("score", 0.5))
            score = max(0.0, min(1.0, score))

            action = str(data.get("action", "HOLD")).upper().strip()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"

            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))

            summary = str(data.get("summary", ""))[:200]

            key_points = self._to_str_tuple(data.get("key_points", []))
            risk_factors = self._to_str_tuple(data.get("risk_factors", []))

            if confidence < self.min_confidence:
                confidence = 0.0
                action = "HOLD"

            return NewsIntelligenceReport(
                code=code,
                score=score,
                action=action,
                confidence=confidence,
                summary=summary,
                key_points=key_points,
                risk_factors=risk_factors,
                article_count=article_count,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("LLM 报告构造失败: %s", e)
            return None

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从 LLM 输出中提取 JSON (支持 markdown 代码块包裹)"""
        if not text:
            return None
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1)
        brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0)
        return None

    @staticmethod
    def _to_str_tuple(items: Any) -> tuple[str, ...]:
        """list/any → tuple[str, ...] (不可变, 截断防过长)"""
        if not isinstance(items, (list, tuple)):
            return ()
        result: list[str] = []
        for item in items[:10]:
            try:
                s = str(item)[:100]
                if s:
                    result.append(s)
            except (ValueError, TypeError):
                continue
        return tuple(result)

    # ------------------------------------------------------------
    # 降级: 词典打分
    # ------------------------------------------------------------

    def _fallback_to_sentiment(
        self, code: str, articles: list[NewsArticle]
    ) -> NewsIntelligenceReport:
        """LLM 不可用时降级到 NewsSentimentEngine 词典打分"""
        try:
            engine = self._get_sentiment_engine()
            from .news_sentiment_engine import NewsItem

            news_items: list[Any] = []
            for art in articles:
                ni = NewsItem(
                    news_id=f"ni_{id(art)}",
                    title=art.title,
                    content=art.content,
                    source=art.source,
                    symbols=[code],
                )
                news_items.append(ni)

            engine.add_news_batch(news_items)
            result = engine.analyze([code])
            signals = getattr(result, "signals", {})
            sig = signals.get(code)
            if sig is None:
                return self._neutral_report(code, "词典打分无信号")

            composite = float(getattr(sig, "composite_sentiment", 0.0))
            composite = max(-1.0, min(1.0, composite))
            score = (composite + 1.0) / 2.0
            confidence = float(getattr(sig, "confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))

            action = "BUY" if score > 0.6 else ("SELL" if score < 0.4 else "HOLD")

            return NewsIntelligenceReport(
                code=code,
                score=score,
                action=action,
                confidence=confidence,
                summary=f"词典打分降级: composite={composite:.3f}",
                article_count=len(articles),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                fallback_used=True,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            ImportError,
        ) as e:
            logger.debug("词典打分降级失败 code=%s: %s", code, e)
            return self._neutral_report(code, f"降级失败: {e}")

    # ------------------------------------------------------------
    # 中性报告 (降级)
    # ------------------------------------------------------------

    @staticmethod
    def _neutral_report(code: str, reason: str) -> NewsIntelligenceReport:
        return NewsIntelligenceReport(
            code=code,
            score=0.5,
            action="HOLD",
            confidence=0.0,
            summary=f"中性降级: {reason}",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            fallback_used=True,
        )

    # ------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------

    def close(self) -> None:
        if self._owns_web_scraper and self._web_scraper is not None:
            close_fn = getattr(self._web_scraper, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except (ValueError, TypeError, RuntimeError, OSError):
                    pass
        if self._owns_llm_client and self._llm_client is not None:
            close_fn = getattr(self._llm_client, "close", None)
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
