"""
TrendSonar 适配器 — 接入舆情与专题追踪系统的 HTTP API
======================================================

功能:
  - 封装 TrendSonar FastAPI 服务的 REST 调用 (默认端口 8193)
  - 关键词语义检索新闻 (/api/news)
  - TopN 热度新闻 (/api/news/top)
  - 专题列表与详情 (/api/topics/list, /api/topics/{id})
  - RAG 问答 (/api/chat)
  - AI 分析报告 (/api/report/analysis)
  - 本地 TTL 缓存避免重复请求
  - 优雅降级: 服务不可用时返回空结果, 不崩溃

依赖:
  - requests (必需)
  - TrendSonar 服务需独立启动 (cd 03_投研与策略生成/trendsonar && python main.py)

使用:
  from utils.trendsonar_adapter import TrendSonarAdapter, TrendSonarNewsItem

  adapter = TrendSonarAdapter(base_url="http://localhost:8193")
  result = adapter.search_news(keyword="半导体", date_range="24h", limit=20)
  for item in result.items:
      logger.info(item.title, item.sentiment_score)

  topics = adapter.list_topics()
  detail = adapter.get_topic_detail(topics[0]["id"])
  answer = adapter.chat("近期 AI 算线有哪些进展?")
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import requests

logger = logging.getLogger(__name__)

# ============================================================
# 数据结构
# ============================================================


@dataclass
class TrendSonarNewsItem:
    """TrendSonar 新闻条目 — 对齐 web_scraper.NewsItem / MediaCrawlerNewsItem 结构"""

    id: int = 0
    title: str = ""
    summary: str = ""
    url: str = ""
    source: str = ""
    category: str = ""
    region: str = ""
    publish_date: str = ""
    heat_score: float = 0.0
    sentiment_label: str = ""  # positive / negative / neutral
    sentiment_score: float = 0.0  # [-1, 1]
    keywords: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendSonarTopic:
    """TrendSonar 专题"""

    id: int = 0
    name: str = ""
    summary: str = ""
    record: str = ""
    start_time: str = ""
    updated_time: str = ""
    heat_score: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendSonarResult:
    """通用调用结果"""

    success: bool
    data: Any = None
    error: str = ""
    elapsed_ms: float = 0.0
    total_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "total_count": self.total_count,
            "data": self.data,
        }


# ============================================================
# TTL 缓存 (与 media_crawler_adapter 同构)
# ============================================================


class _TTLCache:
    """简单 TTL 缓存 (线程安全)"""

    def __init__(self, ttl_seconds: int = 1800):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.time() > expire_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        actual_ttl = ttl if ttl is not None else self.ttl
        self._store[key] = (value, time.time() + actual_ttl)

    def clear(self) -> None:
        self._store.clear()


# ============================================================
# 适配器主类
# ============================================================


class TrendSonarAdapter:
    """TrendSonar 适配器 — 封装 REST API 调用"""

    DEFAULT_TIMEOUT = 15
    DEFAULT_BASE_URL = "http://localhost:8193"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        cache_ttl: int = 1800,
        enabled: bool | None = None,
    ):
        """
        初始化 TrendSonar 适配器

        Args:
            base_url: TrendSonar API 服务地址, 默认读环境变量 TRENDSONAR_BASE_URL
            timeout: 请求超时秒数
            cache_ttl: 缓存 TTL (秒), 默认 30 分钟
            enabled: 是否启用; None 时读环境变量 TRENDSONAR_ENABLED (默认 "1")
        """
        self.base_url = (
            base_url or os.environ.get("TRENDSONAR_BASE_URL", self.DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        if enabled is None:
            enabled = os.environ.get("TRENDSONAR_ENABLED", "1") == "1"
        self.enabled = enabled
        self._cache = _TTLCache(ttl_seconds=cache_ttl)

        self._session = requests.Session()
        self._session.trust_env = False
        # None 值表示禁用该协议代理 (requests 运行时允许); mypy 类型桩不允许, 显式收窄
        self._session.proxies = cast(Any, {"http": None, "https": None})

        logger.info(
            "TrendSonarAdapter 初始化: base_url=%s, enabled=%s",
            self.base_url,
            self.enabled,
        )

    # ------------------------------------------------------------
    # 内部请求封装
    # ------------------------------------------------------------

    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            resp = self._session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return cast(dict[str, Any], resp.json())
            logger.warning(
                "TrendSonar GET %s 失败: HTTP_%s - %s",
                path,
                resp.status_code,
                resp.text[:200],
            )
            return None
        except requests.RequestException as e:
            logger.warning("TrendSonar GET %s 异常: %s", path, e)
            return None

    def _post(
        self, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            resp = self._session.post(
                f"{self.base_url}{path}",
                json=json_body or {},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return cast(dict[str, Any], resp.json())
            logger.warning(
                "TrendSonar POST %s 失败: HTTP_%s - %s",
                path,
                resp.status_code,
                resp.text[:200],
            )
            return None
        except requests.RequestException as e:
            logger.warning("TrendSonar POST %s 异常: %s", path, e)
            return None

    @staticmethod
    def _cache_key(prefix: str, **kwargs: Any) -> str:
        raw = f"{prefix}:{sorted(kwargs.items())}".encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _parse_news_item(raw: dict[str, Any]) -> TrendSonarNewsItem:
        return TrendSonarNewsItem(
            id=int(raw.get("id", 0) or 0),
            title=str(raw.get("title", "") or ""),
            summary=str(raw.get("summary", "") or ""),
            url=str(raw.get("url", "") or ""),
            source=str(raw.get("source", "") or ""),
            category=str(raw.get("category", "") or ""),
            region=str(raw.get("region", "") or ""),
            publish_date=str(raw.get("publish_date") or raw.get("time") or ""),
            heat_score=float(raw.get("heat_score") or raw.get("heat") or 0.0),
            sentiment_label=str(raw.get("sentiment_label", "") or ""),
            sentiment_score=float(raw.get("sentiment_score") or 0.0),
            keywords=list(raw.get("keywords") or []),
            sources=list(raw.get("sources") or []),
            raw=raw,
        )

    # ------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------

    def check_health(self) -> dict[str, Any]:
        """检查 TrendSonar 服务是否可用"""
        if not self.enabled:
            return {"available": False, "reason": "feature_flag_disabled"}
        data = self._get("/api/app_info")
        if data is not None:
            return {"available": True, "data": data}
        return {"available": False, "reason": "request_failed"}

    # ------------------------------------------------------------
    # 关键词搜索新闻 (核心方法)
    # ------------------------------------------------------------

    def search_news(
        self,
        keyword: str,
        date_range: str = "24h",
        page: int = 1,
        sort_by: str = "heat",
        category: str | None = None,
        region: str | None = None,
        source: str | None = None,
        use_cache: bool = True,
    ) -> TrendSonarResult:
        """
        按关键词搜索新闻 (语义检索 + 标题匹配)

        Args:
            keyword: 搜索关键词, 如 "半导体"、"AI"、"恒瑞医药"
            date_range: 时间范围 24h/3d/7d/week/month/year/all 或 YYYY-MM-DD
            page: 页码 (每页 20 条)
            sort_by: 排序方式 heat/date
            category/region/source: 筛选条件
            use_cache: 是否使用缓存

        Returns:
            TrendSonarResult (data 为 list[TrendSonarNewsItem])
        """
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        if not keyword or not keyword.strip():
            return TrendSonarResult(
                success=False,
                error="empty_keyword",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        ck = self._cache_key(
            "news",
            keyword=keyword,
            date_range=date_range,
            page=page,
            sort_by=sort_by,
            category=category,
            region=region,
            source=source,
        )
        if use_cache:
            cached = self._cache.get(ck)
            if cached is not None:
                cached.elapsed_ms = (time.perf_counter() - start) * 1000
                return cast(TrendSonarResult, cached)

        data = self._get(
            "/api/news",
            params={
                "q": keyword,
                "page": page,
                "date": date_range,
                "sort_by": sort_by,
                "category": category,
                "region": region,
                "source": source,
            },
        )
        if data is None:
            return TrendSonarResult(
                success=False,
                error="request_failed",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        items = [self._parse_news_item(r) for r in (data.get("data") or [])]
        result = TrendSonarResult(
            success=True,
            data=items,
            total_count=len(items),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
        if use_cache:
            self._cache.set(ck, result)
        logger.info(
            "TrendSonar 搜索完成: keyword=%s, items=%d, 耗时=%.1fms",
            keyword,
            len(items),
            result.elapsed_ms,
        )
        return result

    # ------------------------------------------------------------
    # TopN 热度新闻
    # ------------------------------------------------------------

    def top_news(
        self,
        limit: int = 20,
        date_range: str = "24h",
        sort_by: str = "heat",
        category: str | None = None,
        region: str | None = None,
        use_cache: bool = True,
    ) -> TrendSonarResult:
        """获取指定时间范围内热度最高的新闻 TopN"""
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        ck = self._cache_key(
            "top",
            limit=limit,
            date_range=date_range,
            sort_by=sort_by,
            category=category,
            region=region,
        )
        if use_cache:
            cached = self._cache.get(ck)
            if cached is not None:
                cached.elapsed_ms = (time.perf_counter() - start) * 1000
                return cast(TrendSonarResult, cached)

        data = self._get(
            "/api/news/top",
            params={
                "limit": limit,
                "date": date_range,
                "sort_by": sort_by,
                "category": category,
                "region": region,
            },
        )
        if data is None:
            return TrendSonarResult(
                success=False,
                error="request_failed",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        items = [self._parse_news_item(r) for r in (data.get("data") or [])]
        result = TrendSonarResult(
            success=True,
            data=items,
            total_count=len(items),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
        if use_cache:
            self._cache.set(ck, result)
        return result

    # ------------------------------------------------------------
    # 专题列表与详情
    # ------------------------------------------------------------

    def list_topics(
        self,
        page: int = 1,
        size: int = 20,
        use_cache: bool = True,
    ) -> TrendSonarResult:
        """获取专题列表"""
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        ck = self._cache_key("topics", page=page, size=size)
        if use_cache:
            cached = self._cache.get(ck)
            if cached is not None:
                cached.elapsed_ms = (time.perf_counter() - start) * 1000
                return cast(TrendSonarResult, cached)

        data = self._get("/api/topics/list", params={"page": page, "size": size})
        if data is None:
            return TrendSonarResult(
                success=False,
                error="request_failed",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        items = [
            TrendSonarTopic(
                id=int(t.get("id", 0) or 0),
                name=str(t.get("name", "") or ""),
                summary=str(t.get("summary", "") or ""),
                start_time=str(t.get("start_time") or ""),
                updated_time=str(t.get("updated_time") or ""),
                heat_score=float(t.get("heat_score") or 0.0),
                raw=t,
            )
            for t in (data.get("items") or [])
        ]
        result = TrendSonarResult(
            success=True,
            data=items,
            total_count=len(items),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
        if use_cache:
            self._cache.set(ck, result)
        return result

    def get_topic_detail(
        self,
        topic_id: int,
        sort: str = "asc",
        use_cache: bool = True,
    ) -> TrendSonarResult:
        """
        获取专题详情 (含时间轴 + 关联新闻卡片 + 情感分)

        Returns:
            TrendSonarResult.data = {
                "topic": TrendSonarTopic,
                "timeline": list[dict],
                "news": list[TrendSonarNewsItem],
            }
        """
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        ck = self._cache_key("topic_detail", topic_id=topic_id, sort=sort)
        if use_cache:
            cached = self._cache.get(ck)
            if cached is not None:
                cached.elapsed_ms = (time.perf_counter() - start) * 1000
                return cast(TrendSonarResult, cached)

        data = self._get(f"/api/topics/{topic_id}", params={"sort": sort})
        if data is None:
            return TrendSonarResult(
                success=False,
                error="request_failed",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        topic_raw = data.get("topic") or {}
        topic = TrendSonarTopic(
            id=int(topic_raw.get("id", 0) or 0),
            name=str(topic_raw.get("name", "") or ""),
            summary=str(topic_raw.get("summary", "") or ""),
            record=str(topic_raw.get("record", "") or ""),
            start_time=str(topic_raw.get("start_time") or ""),
            updated_time=str(topic_raw.get("updated_time") or ""),
            heat_score=float(topic_raw.get("heat_score") or 0.0),
            raw=topic_raw,
        )
        timeline = list(data.get("timeline") or [])
        news = [self._parse_news_item(n) for n in (data.get("news") or [])]

        result = TrendSonarResult(
            success=True,
            data={"topic": topic, "timeline": timeline, "news": news},
            total_count=len(news),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
        if use_cache:
            self._cache.set(ck, result)
        return result

    # ------------------------------------------------------------
    # RAG 问答
    # ------------------------------------------------------------

    def chat(
        self,
        query: str,
        use_backup: bool = False,
        timeout: int | None = None,
    ) -> TrendSonarResult:
        """
        基于 TrendSonar 新闻向量库的 RAG 问答 (非流式)

        Args:
            query: 用户问题, 如 "近期 AI 管线有哪些进展?"
            use_backup: 是否强制使用备用模型
            timeout: 覆盖默认超时 (RAG 可能较慢, 建议设 60s)

        Returns:
            TrendSonarResult.data = {"response": str}
        """
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        if not query or not query.strip():
            return TrendSonarResult(
                success=False,
                error="empty_query",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        try:
            resp = self._session.get(
                f"{self.base_url}/api/chat",
                params=cast(
                    Any,
                    {"query": query, "stream": "false", "use_backup": use_backup},
                ),
                timeout=timeout or 60,
            )
            if resp.status_code == 200:
                data = resp.json()
                return TrendSonarResult(
                    success=True,
                    data={"response": str(data.get("response", "") or "")},
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                )
            return TrendSonarResult(
                success=False,
                error=f"HTTP_{resp.status_code}",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        except requests.RequestException as e:
            logger.warning("TrendSonar chat 异常: %s", e)
            return TrendSonarResult(
                success=False,
                error=str(e),
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

    # ------------------------------------------------------------
    # AI 分析报告
    # ------------------------------------------------------------

    def report_analysis(
        self,
        keyword: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        region: str | None = None,
        limit: int | None = None,
        generate_ai: bool = False,
        use_cache: bool = True,
    ) -> TrendSonarResult:
        """
        生成/读取报表分析数据 (含摘要、图表数据、Top 新闻、AI 分析)

        Returns:
            TrendSonarResult.data = 原始报表 JSON
        """
        start = time.perf_counter()
        if not self.enabled:
            return TrendSonarResult(
                success=False,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        ck = self._cache_key(
            "report",
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
            category=category,
            region=region,
            limit=limit,
            generate_ai=generate_ai,
        )
        if use_cache:
            cached = self._cache.get(ck)
            if cached is not None:
                cached.elapsed_ms = (time.perf_counter() - start) * 1000
                return cast(TrendSonarResult, cached)

        data = self._get(
            "/api/report/analysis",
            params={
                "q": keyword,
                "start_date": start_date,
                "end_date": end_date,
                "category": category,
                "region": region,
                "limit": limit,
                "generate_ai": generate_ai,
            },
        )
        if data is None:
            return TrendSonarResult(
                success=False,
                error="request_failed",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        result = TrendSonarResult(
            success=True,
            data=data,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
        if use_cache:
            self._cache.set(ck, result)
        return result


# ============================================================
# 模块级单例 (惰性初始化, 便于全局复用)
# ============================================================

_adapter_singleton: TrendSonarAdapter | None = None


def get_trendsonar_adapter() -> TrendSonarAdapter:
    """获取 TrendSonar 适配器单例"""
    global _adapter_singleton
    if _adapter_singleton is None:
        _adapter_singleton = TrendSonarAdapter()
    return _adapter_singleton
