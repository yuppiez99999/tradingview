"""
FinnewsHunter 信号源 — 金融新闻事件驱动 alpha 信号

灵感: github.com/DemonDamon/FinnewsHunter (多智能体金融情报 + 情绪融合 + alpha 因子挖掘)
本模块提炼其"事件类型 → alpha 强度"核心思路, 做轻量适配, 不引入 AgenticX/langgraph 重依赖.

接入链路:
    新闻采集 (wind_search_news / WebScraper / 可注入 fetcher)
        → 事件分类 (LLM 优先, 关键词词典回退)
        → 事件类型 → alpha 强度映射
        → FinnewsHunterSignalSource.get_signal(code) -> SignalResult
        → SignalFusionEngine.register_source("finnhunter", source.get_signal)

区别于 news_intel (LLM 深度解读单股新闻):
- finnhunter 聚焦"事件类型 → alpha 方向", 输出事件驱动的方向性信号
- 事件类型: 并购/业绩/政策/高管/产品/监管 等 9 类

关键约束:
- score ∈ [0, 1] (SignalFusionEngine 约定, 越高越看多)
- confidence < min_confidence 时自动降权 (返回 confidence=0, action=HOLD)
- 采集失败/LLM 不可用时返回中性 SignalResult (score=0.5, confidence=0)
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

try:
    from ..logging_manager import get_logger

    logger = get_logger("finnhunter_signal_source")
except ImportError:
    logger = logging.getLogger("finnhunter_signal_source")

try:
    from ..signal_fusion import SignalResult
except ImportError:
    SignalResult = None  # type: ignore[assignment,misc]


# ============================================================
# 默认配置
# ============================================================

DEFAULT_INITIAL_WEIGHT: float = 0.06
DEFAULT_MIN_CONFIDENCE: float = 0.25
DEFAULT_NEWS_LIMIT: int = 15
DEFAULT_RECENCY_DAYS: int = 7


# ============================================================
# 事件类型 → alpha 强度映射 (FinnewsHunter 核心思路)
# ============================================================

EVENT_ALPHA: dict[str, float] = {
    "并购重组": 0.30,
    "业绩超预期": 0.25,
    "政策利好": 0.20,
    "产品订单": 0.15,
    "高管增持": 0.10,
    "融资利好": 0.08,
    "业绩不及预期": -0.25,
    "政策利空": -0.20,
    "高管减持": -0.30,
}

EVENT_KEYWORDS: dict[str, list[str]] = {
    "并购重组": ["并购", "重组", "收购", "合并", "借壳", "注入"],
    "业绩超预期": ["超预期", "大增", "预增", "扭亏", "业绩大增", "创历史新高"],
    "业绩不及预期": ["不及预期", "预亏", "预减", "下滑", "亏损扩大", "腰斩"],
    "政策利好": ["政策利好", "补贴", "减税", "支持", "扶持", "纳入", "示范"],
    "政策利空": ["监管处罚", "立案调查", "退市", "警示", "约谈", "限制"],
    "高管增持": ["增持", "回购", "员工持股"],
    "高管减持": ["减持", "套现", "辞职"],
    "产品订单": ["订单", "中标", "签约", "量产", "发布", "投产", "突破"],
    "融资利好": ["定增", "融资", "发债", "授信"],
}


# ============================================================
# FinnewsHunter 信号源
# ============================================================


class FinnewsHunterSignalSource:
    """FinnewsHunter 事件驱动 alpha 信号源 — 适配 SignalFusionEngine.register_source

    使用方式:
        source = FinnewsHunterSignalSource()
        engine = SignalFusionEngine()
        engine.register_source("finnhunter", source.get_signal, initial_weight=0.06)

    可注入 llm_callable / news_fetcher 用于测试或自定义数据源:
        source = FinnewsHunterSignalSource(
            llm_callable=lambda prompt: "...",
            news_fetcher=lambda code, n: [{"title": "...", "date": "..."}],
        )
    """

    SOURCE_NAME: str = "finnhunter"

    def __init__(
        self,
        llm_callable: Callable[[str], str] | None = None,
        news_fetcher: Callable[[str, int], list[dict]] | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        news_limit: int = DEFAULT_NEWS_LIMIT,
        recency_days: int = DEFAULT_RECENCY_DAYS,
    ) -> None:
        self._llm_callable = llm_callable
        self._news_fetcher = news_fetcher
        self.min_confidence = min_confidence
        self.news_limit = news_limit
        self.recency_days = recency_days

    # ------------------------------------------------------------
    # 核心接口: get_signal(code) -> SignalResult
    # ------------------------------------------------------------

    def get_signal(self, code: str) -> Any:
        """获取某股票的事件驱动 alpha 信号

        Args:
            code: 股票代码, 如 "600519.SH" / "000001.SZ"

        Returns:
            SignalResult(source="finnhunter", score ∈ [0,1], confidence ∈ [0,1])
            采集失败/无数据时返回中性信号 (score=0.5, confidence=0)
        """
        if not code:
            return self._neutral_signal(code, "空代码")

        if SignalResult is None:
            logger.debug("SignalResult 未导入, 跳过 finnhunter 信号")
            return None

        try:
            news = self._fetch_news(code, self.news_limit)
            if not news:
                return self._neutral_signal(code, "无新闻数据")

            events = self._classify_events(news)
            raw_alpha, used_count = self._aggregate_alpha(events, news)

            score = 0.5 + max(-0.5, min(0.5, raw_alpha))
            score = max(0.0, min(1.0, score))

            llm_used = self._llm_callable is not None
            confidence = min(1.0, used_count / 8.0) * (0.8 if llm_used else 0.5)

            action = "BUY" if score > 0.55 else ("SELL" if score < 0.45 else "HOLD")

            if confidence < self.min_confidence:
                confidence = 0.0
                action = "HOLD"

            method = "LLM" if llm_used else "关键词"
            reason = (
                f"FinnewsHunter({method}): score={score:.3f}, action={action}, "
                f"events={used_count}/{len(news)}, raw_alpha={raw_alpha:+.3f}"
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
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.debug("finnhunter 信号获取异常 code=%s: %s", code, e)
            return self._neutral_signal(code, f"获取异常: {e}")

    # ------------------------------------------------------------
    # 新闻采集 (默认走 wind_search_news, 可注入)
    # ------------------------------------------------------------

    def _fetch_news(self, code: str, limit: int) -> list[dict]:
        if self._news_fetcher is not None:
            return list(self._news_fetcher(code, limit) or [])

        try:
            from tools.wind_mcp_fetcher import wind_search_news

            raw = wind_search_news(keyword=code, top_k=limit)
            return self._normalize_news(raw)
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
            logger.debug("wind_search_news 不可用: %s", e)
            return []

    @staticmethod
    def _normalize_news(raw: Any) -> list[dict]:
        items: list[dict] = []
        if isinstance(raw, list):
            for it in raw:
                if isinstance(it, dict):
                    items.append(
                        {
                            "title": str(it.get("title") or it.get("Title") or ""),
                            "date": str(
                                it.get("date") or it.get("Date") or it.get("time") or ""
                            ),
                            "content": str(
                                it.get("content") or it.get("summary") or ""
                            ),
                        }
                    )
        return [x for x in items if x["title"]]

    # ------------------------------------------------------------
    # 事件分类 (LLM 优先, 关键词词典回退)
    # ------------------------------------------------------------

    def _classify_events(self, news: list[dict]) -> list[tuple[str, float]]:
        """返回 [(event_type, recency_weight), ...] 对应每条新闻"""
        if self._llm_callable is not None:
            try:
                return self._classify_events_llm(news)
            except (ValueError, TypeError, RuntimeError, OSError) as e:
                logger.debug("LLM 事件分类失败, 回退关键词: %s", e)
        return self._classify_events_keywords(news)

    def _classify_events_llm(self, news: list[dict]) -> list[tuple[str, float]]:
        titles = [n["title"] for n in news]
        prompt = (
            "你是金融事件分类器。对以下每条新闻标题, 输出最匹配的事件类型与情绪方向。\n"
            "事件类型候选: " + "、".join(EVENT_ALPHA.keys()) + "、中性\n"
            '输出 JSON 数组, 每项 {"idx": 0, "event": "事件类型"}, 仅输出 JSON:\n'
            + "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
        )
        text = self._llm_callable(prompt) or ""
        import json

        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("LLM 输出无 JSON 数组")
        arr = json.loads(text[start : end + 1])
        result: list[tuple[str, float]] = []
        for item in arr:
            ev = str(item.get("event", "中性"))
            if ev not in EVENT_ALPHA:
                ev = "中性"
            idx = int(item.get("idx", -1))
            weight = (
                self._recency_weight(news[idx]["date"]) if 0 <= idx < len(news) else 0.5
            )
            result.append((ev, weight))
        return result or self._classify_events_keywords(news)

    def _classify_events_keywords(self, news: list[dict]) -> list[tuple[str, float]]:
        result: list[tuple[str, float]] = []
        for n in news:
            text = (n["title"] + n.get("content", ""))[:200]
            ev = "中性"
            for event_type, keywords in EVENT_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    ev = event_type
                    break
            result.append((ev, self._recency_weight(n["date"])))
        return result

    # ------------------------------------------------------------
    # alpha 聚合 + 时间衰减
    # ------------------------------------------------------------

    def _aggregate_alpha(
        self, events: list[tuple[str, float]], news: list[dict]
    ) -> tuple[float, int]:
        total = 0.0
        used = 0
        for event_type, weight in events:
            alpha = EVENT_ALPHA.get(event_type, 0.0)
            if alpha == 0.0:
                continue
            total += alpha * weight
            used += 1
        if not news:
            return 0.0, 0
        return total / math.sqrt(len(news)), used

    def _recency_weight(self, date_str: str) -> float:
        if not date_str:
            return 0.5
        try:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(
                        date_str[: len(fmt) + 3] if " " in date_str else date_str[:10],
                        fmt,
                    )
                    age_days = max(0, (datetime.now() - d).days)
                    return max(0.1, math.exp(-age_days / self.recency_days))
                except ValueError:
                    continue
        except (ValueError, TypeError, OSError):
            pass
        return 0.5

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
