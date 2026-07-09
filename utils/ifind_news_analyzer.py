# -*- coding: utf-8 -*-
"""
iFinD 资讯读取 + 标的研判模块
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger("ifind_news_analyzer")


@dataclass
class NewsItem:
    """单条资讯"""
    title: str
    snippet: str
    source: str
    publish_time: str
    url: Optional[str] = None
    sentiment: str = "neutral"
    relevance: float = 0.0
    entities: List[str] = field(default_factory=list)


@dataclass
class StockInsight:
    """单个标的研究结论"""
    symbol: str
    name: str
    direction: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    news_count: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class IFinDNewsAnalyzer:
    """基于 iFinD 新闻公告的资讯读取与标的研判"""

    def __init__(self, skill_dir: Optional[str] = None) -> None:
        self.skill_dir = skill_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills",
            "ifind-finance-data",
        )
        sys.path.insert(0, self.skill_dir)
        try:
            from call import call as _call  # type: ignore
            self._call = _call
        except Exception as exc:  # pragma: no cover
            logger.error("iFinD call 模块导入失败: %s", exc)
            self._call = None

    def available(self) -> bool:
        return self._call is not None

    def search_news(self, query: str, size: int = 5, days: int = 3) -> List[NewsItem]:
        """检索新闻资讯"""
        return self._call_news("search_news", query, size=size, days=days)

    def search_notice(self, query: str, size: int = 5, days: int = 3) -> List[NewsItem]:
        """检索公告"""
        return self._call_news("search_notice", query, size=size, days=days)

    def search_trending(self, keyword: str, industry_name: str = "", time_scope: str = "24小时", size: int = 5) -> List[NewsItem]:
        """热点事件"""
        items: List[NewsItem] = []
        if self._call is None:
            return items
        try:
            params: Dict[str, Any] = {"keyword": keyword, "size": size}
            if industry_name:
                params["industry_name"] = industry_name
            if time_scope:
                params["time_scope"] = time_scope
            result = self._call("news", "search_trending_news", params)
            if result.get("ok"):
                items = self._parse_news_result(result.get("data", {}))
        except Exception:
            logger.error("热点事件查询失败:\n%s", traceback.format_exc())
        return items

    def analyze_symbol(self, symbol: str, name: str = "", size: int = 5, days: int = 3) -> StockInsight:
        """对单个标的做新闻+公告研判"""
        if not symbol:
            raise ValueError("symbol 不能为空")

        news_query = f"{name} {symbol}".strip() if name else symbol
        news_items = self.search_news(news_query, size=size, days=days)
        notice_items = self.search_notice(news_query, size=min(3, size), days=days)
        all_items = news_items + notice_items

        direction, confidence, reasons = self._derive_insight(all_items, symbol)
        return StockInsight(
            symbol=symbol,
            name=name or symbol,
            direction=direction,
            confidence=confidence,
            reasons=reasons,
            news_count=len(all_items),
        )

    def batch_analyze(self, symbols: List[str], name_map: Optional[Dict[str, str]] = None, size: int = 4, days: int = 3) -> List[StockInsight]:
        """批量研判"""
        name_map = name_map or {}
        results: List[StockInsight] = []
        for symbol in symbols:
            try:
                results.append(self.analyze_symbol(symbol, name=name_map.get(symbol, ""), size=size, days=days))
            except Exception:
                logger.error("研判失败: %s", symbol, exc_info=True)
        return results

    def _call_news(self, tool_name: str, query: str, size: int = 5, days: int = 3) -> List[NewsItem]:
        items: List[NewsItem] = []
        if self._call is None:
            return items
        try:
            end = datetime.now()
            start = datetime(end.year, end.month, end.day) if False else end
            time_start = (datetime.now()).strftime("%Y-%m-%d")
            time_end = (datetime.now()).strftime("%Y-%m-%d")
            if days > 0:
                from datetime import timedelta
                time_start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            result = self._call(
                "news",
                tool_name,
                {"query": query, "time_start": time_start, "time_end": time_end, "size": size},
            )
            if result.get("ok"):
                items = self._parse_news_result(result.get("data", {}))
        except Exception:
            logger.error("%s 查询失败:\n%s", tool_name, traceback.format_exc())
        return items

    def _parse_news_result(self, data: Any) -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            parsed = data if isinstance(data, dict) else json.loads(str(data))
            # MCP 文本包装：result.content[0].text 内层又是业务 JSON
            result_block = (((parsed or {}).get("result") or {}).get("content") or [])
            if result_block and isinstance(result_block[0], dict):
                text_content = result_block[0].get("text")
                if isinstance(text_content, str):
                    try:
                        parsed = json.loads(text_content)
                    except Exception:
                        parsed = {}
            results = self._extract_results(parsed)
            for item in results:
                if not isinstance(item, dict):
                    continue
                items.append(
                    NewsItem(
                        title=str(
                            item.get("资讯标题")
                            or item.get("title")
                            or item.get("news_title")
                            or item.get("标题")
                            or item.get("新闻标题")
                            or ""
                        ),
                        snippet=str(
                            item.get("资讯内容")
                            or item.get("snippet")
                            or item.get("content")
                            or item.get("news_content")
                            or item.get("内容")
                            or item.get("正文")
                            or ""
                        ),
                        source=str(
                            item.get("来源")
                            or item.get("source")
                            or item.get("news_source")
                            or item.get("媒体")
                            or ""
                        ),
                        publish_time=str(
                            item.get("日期")
                            or item.get("publish_time")
                            or item.get("time")
                            or item.get("发布时间")
                            or item.get("时间")
                            or ""
                        ),
                        url=item.get("URL") or item.get("url"),
                        entities=self._extract_entities(
                            str(
                                item.get("资讯标题")
                                or item.get("title")
                                or item.get("news_title")
                                or item.get("标题")
                                or item.get("新闻标题")
                                or ""
                            )
                        ),
                    )
                )
        except Exception:
            logger.error("资讯解析失败:\n%s", traceback.format_exc())
        return items

    def _extract_results(self, parsed: Any) -> List[dict]:
        if isinstance(parsed, list):
            return parsed
        if not isinstance(parsed, dict):
            return []
        inner = parsed.get("data")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                inner = None
        if isinstance(inner, dict):
            return self._extract_results(inner)
        if isinstance(inner, list):
            return inner
        result_block = parsed.get("result")
        if isinstance(result_block, dict):
            results = result_block.get("results")
            if isinstance(results, list):
                return results
        return []

    def _derive_insight(self, items: List[NewsItem], symbol: str) -> tuple[str, float, List[str]]:
        if not items:
            return "neutral", 0.0, ["未检索到相关资讯"]

        positive_hits = 0
        negative_hits = 0
        reasons: List[str] = []
        keywords_positive = ["预增", "增长", "中标", "订单", "扩产", "出海", "份额提升", "超预期", "盈利", "放量", "景气"]
        keywords_negative = ["预减", "下滑", "亏损", "处罚", "减持", "质押", "暴雷", "下调", "断供", "降价", "过剩"]

        for item in items:
            text = f"{item.title} {item.snippet}".lower()
            if any(k in text for k in keywords_positive):
                positive_hits += 1
                reasons.append(f"利好：{item.title}")
            if any(k in text for k in keywords_negative):
                negative_hits += 1
                reasons.append(f"利空：{item.title}")
            if not reasons and item.snippet:
                reasons.append(item.snippet[:60])

        total = positive_hits + negative_hits
        if total == 0:
            return "neutral", 0.4, ["资讯未显示明显多空信号"]

        score = (positive_hits - negative_hits) / total
        if score >= 0.5:
            return "positive", min(1.0, 0.5 + 0.15 * positive_hits), reasons[:5]
        if score <= -0.5:
            return "negative", min(1.0, 0.5 + 0.15 * negative_hits), reasons[:5]
        return "neutral", 0.5, reasons[:5]

    def _extract_entities(self, text: str) -> List[str]:
        entities: List[str] = []
        try:
            import re
            for pattern in [r"\d{6}\.[A-Za-z]{2}", r"[A-Za-z]{2,4}\d{5,6}", r"\d{6}"]:
                entities.extend(re.findall(pattern, text))
        except Exception:
            pass
        return entities[:10]
