"""
MediaCrawler 适配器 — 整合自媒体平台舆情数据源
====================================================

功能:
  - 封装 MediaCrawler REST API 调用
  - 统一数据格式转换为 NewsItem 结构
  - 支持 7 大平台: 小红书/抖音/快手/B站/微博/贴吧/知乎
  - 关键词搜索 + 评论抓取
  - 本地 TTL 缓存避免重复请求
  - 优雅降级: API 不可用时返回空结果, 不崩溃

依赖:
  - requests (必需)
  - MediaCrawler 服务需独立启动 (uv run uvicorn api.main:app --port 8080)

使用:
  from utils.media_crawler_adapter import MediaCrawlerAdapter, MediaCrawlerNewsItem

  adapter = MediaCrawlerAdapter(base_url="http://localhost:8080")
  result = adapter.search(keyword="半导体", platform="xhs", max_items=20)
  for item in result.items:
      logger.info(item.title, item.sentiment_score)
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import requests
import urllib3  # noqa: F401  (保留以备显式 verify=certifi.where())

# 安全加固: 不抑制 InsecureRequest 警告 (TLS 验证保持默认启用, 避免掩盖 MITM 回归)

logger = logging.getLogger(__name__)

# ============================================================
# 数据结构
# ============================================================


@dataclass
class MediaCrawlerNewsItem:
    """自媒体舆情条目 - 对齐 web_scraper.NewsItem 结构"""

    title: str
    content: str = ""
    url: str = ""
    source: str = ""  # 来源平台 (xhs/dy/ks/bili/wb/tieba/zhihu)
    category: str = "social"  # social/news/comment
    published_at: str = ""  # ISO 格式时间
    symbol: str = ""  # 关联股票代码 (如有)
    sentiment_score: float = 0.0  # [-1, 1] 情感分 (需 NLP 模型填充)
    keywords: list[str] = field(default_factory=list)
    # MediaCrawler 特有字段
    platform: str = ""  # 原始平台标识
    item_id: str = ""  # 帖子/视频 ID
    creator_nickname: str = ""  # 创作者昵称 (已脱敏)
    like_count: int = 0  # 点赞数
    comment_count: int = 0  # 评论数
    share_count: int = 0  # 分享数
    view_count: int = 0  # 观看/阅读数
    comments: list[dict[str, Any]] = field(default_factory=list)  # 评论列表
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MediaCrawlerResult:
    """抓取结果"""

    success: bool
    items: list[MediaCrawlerNewsItem] = field(default_factory=list)
    source: str = ""
    platform: str = ""
    error: str = ""
    elapsed_ms: float = 0.0
    total_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "platform": self.platform,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "total_count": self.total_count,
            "items_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }


# ============================================================
# 平台映射
# ============================================================

PLATFORM_MAP: dict[str, str] = {
    "xhs": "小红书",
    "xiaohongshu": "小红书",
    "dy": "抖音",
    "douyin": "抖音",
    "ks": "快手",
    "kuaishou": "快手",
    "bili": "B站",
    "bilibili": "B站",
    "wb": "微博",
    "weibo": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}

# MediaCrawler API 平台枚举值
API_PLATFORM_VALUES = {"xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"}


def normalize_platform(platform: str) -> str:
    """标准化平台名称为 API 可接受值"""
    p = platform.lower().strip()
    if p in API_PLATFORM_VALUES:
        return p
    # 别名映射
    alias_map = {
        "xiaohongshu": "xhs",
        "hongshu": "xhs",
        "douyin": "dy",
        "kuaishou": "ks",
        "bilibili": "bili",
        "weibo": "wb",
    }
    return alias_map.get(p, "xhs")


def get_platform_display(platform: str) -> str:
    """获取平台中文显示名称"""
    return PLATFORM_MAP.get(platform.lower(), platform)


# ============================================================
# TTL 缓存
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

    def set(self, key: str, value: Any, ttl: int | None = None):
        actual_ttl = ttl if ttl is not None else self.ttl
        self._store[key] = (value, time.time() + actual_ttl)

    def clear(self):
        self._store.clear()

    def info(self) -> dict[str, Any]:
        now = time.time()
        valid = sum(1 for _, exp in self._store.values() if now <= exp)
        return {"total_entries": len(self._store), "valid_entries": valid}


# ============================================================
# 适配器主类
# ============================================================


class MediaCrawlerAdapter:
    """MediaCrawler 适配器 - 封装 REST API 调用"""

    # 请求超时 (秒)
    DEFAULT_TIMEOUT = 30
    # 启动爬虫后等待结果的轮询间隔 (秒)
    POLL_INTERVAL = 2
    # 最大等待时间 (秒)
    MAX_WAIT_TIME = 120

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        cache_ttl: int = 1800,
        enabled: bool = True,
    ):
        """
        初始化 MediaCrawler 适配器

        Args:
            base_url: MediaCrawler API 服务地址, 默认读环境变量 MEDIACRAWLER_BASE_URL
            api_key: API Key (预留, 当前版本可能不需要)
            timeout: 请求超时秒数
            cache_ttl: 缓存 TTL (秒), 默认 30 分钟
            enabled: 是否启用, False 时所有方法返回空结果
        """
        self.base_url = (
            base_url or os.environ.get("MEDIACRAWLER_BASE_URL", "http://localhost:8080")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("MEDIACRAWLER_API_KEY", "")
        self.timeout = timeout
        self.enabled = enabled
        self._cache = _TTLCache(ttl_seconds=cache_ttl)

        # 请求 Session (绕过系统代理)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.proxies = {"http": None, "https": None}

        logger.info(
            "MediaCrawlerAdapter 初始化: base_url=%s, enabled=%s",
            self.base_url,
            self.enabled,
        )

    # ------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------

    def check_health(self) -> dict[str, Any]:
        """检查 MediaCrawler 服务是否可用"""
        if not self.enabled:
            return {"available": False, "reason": "feature_flag_disabled"}

        try:
            resp = self._session.get(
                f"{self.base_url}/api/health", timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"available": True, "data": data}
            return {"available": False, "reason": f"HTTP_{resp.status_code}"}
        except requests.RequestException as e:
            logger.warning("MediaCrawler 健康检查失败: %s", e)
            return {"available": False, "reason": str(e)}

    # ------------------------------------------------------------
    # 关键词搜索 (核心方法)
    # ------------------------------------------------------------

    def search(
        self,
        keyword: str,
        platform: str = "xhs",
        max_items: int = 20,
        enable_comments: bool = False,
        start_page: int = 1,
        use_cache: bool = True,
    ) -> MediaCrawlerResult:
        """
        按关键词搜索自媒体内容

        Args:
            keyword: 搜索关键词, 如 "半导体"、"AI"、"恒瑞医药"
            platform: 平台, 支持 xhs/dy/ks/bili/wb/tieba/zhihu
            max_items: 最大抓取数量
            enable_comments: 是否抓取评论
            start_page: 起始页码
            use_cache: 是否使用缓存

        Returns:
            MediaCrawlerResult 抓取结果
        """
        start_time = time.perf_counter()
        api_platform = normalize_platform(platform)
        display_platform = get_platform_display(api_platform)

        if not self.enabled:
            return MediaCrawlerResult(
                success=False,
                source="MediaCrawler",
                platform=display_platform,
                error="feature_flag_disabled",
                elapsed_ms=(time.perf_counter() - start_time) * 1000,
            )

        if not keyword or not keyword.strip():
            return MediaCrawlerResult(
                success=False,
                source="MediaCrawler",
                platform=display_platform,
                error="empty_keyword",
                elapsed_ms=(time.perf_counter() - start_time) * 1000,
            )

        # 缓存 key
        cache_key = hashlib.sha256(
            f"search:{api_platform}:{keyword}:{max_items}:{enable_comments}:{start_page}".encode()
        ).hexdigest()

        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(
                    "MediaCrawler 命中缓存: %s @ %s", keyword, display_platform
                )
                cached.elapsed_ms = (time.perf_counter() - start_time) * 1000
                return cached

        try:
            # 1. 启动爬虫任务
            start_resp = self._session.post(
                f"{self.base_url}/api/crawler/start",
                json={
                    "platform": api_platform,
                    "login_type": "cookie",
                    "crawler_type": "search",
                    "keywords": keyword,
                    "start_page": start_page,
                    "enable_comments": enable_comments,
                    "enable_sub_comments": False,
                    "save_option": "jsonl",
                    "headless": True,
                    "max_notes_count": max_items,
                    "max_comments_count": 50 if enable_comments else None,
                },
                timeout=self.timeout,
            )

            if start_resp.status_code not in (200, 400):
                logger.warning(
                    "MediaCrawler 启动失败: HTTP_%s - %s",
                    start_resp.status_code,
                    start_resp.text[:200],
                )
                return MediaCrawlerResult(
                    success=False,
                    source="MediaCrawler",
                    platform=display_platform,
                    error=f"start_failed_HTTP_{start_resp.status_code}",
                    elapsed_ms=(time.perf_counter() - start_time) * 1000,
                )

            # 400 表示已有任务在运行, 直接等待
            if start_resp.status_code == 400:
                logger.debug("MediaCrawler 已有任务运行, 等待结果...")

            # 2. 轮询状态直到完成或超时
            items: list[MediaCrawlerNewsItem] = []
            deadline = time.time() + self.MAX_WAIT_TIME

            while time.time() < deadline:
                status_resp = self._session.get(
                    f"{self.base_url}/api/crawler/status",
                    timeout=self.timeout,
                )
                if status_resp.status_code != 200:
                    time.sleep(self.POLL_INTERVAL)
                    continue

                status_data = status_resp.json()
                status = status_data.get("status", "idle")

                if status == "idle" or status == "stopping":
                    # 任务结束, 获取数据文件
                    break
                if status == "error":
                    logger.warning(
                        "MediaCrawler 任务错误: %s",
                        status_data.get("error_message", ""),
                    )
                    break

                time.sleep(self.POLL_INTERVAL)

            # 3. 获取数据文件列表并读取
            items = self._fetch_latest_data(api_platform, max_items)

            result = MediaCrawlerResult(
                success=True,
                items=items,
                source="MediaCrawler",
                platform=display_platform,
                elapsed_ms=(time.perf_counter() - start_time) * 1000,
                total_count=len(items),
            )

            # 写入缓存
            if use_cache and result.success:
                self._cache.set(cache_key, result)

            logger.info(
                "MediaCrawler 搜索完成: keyword=%s, platform=%s, items=%d, 耗时=%.1fs",
                keyword,
                display_platform,
                len(items),
                (time.perf_counter() - start_time),
            )
            return result

        except requests.RequestException as e:
            logger.warning("MediaCrawler 搜索异常: %s", e)
            return MediaCrawlerResult(
                success=False,
                source="MediaCrawler",
                platform=display_platform,
                error=str(e),
                elapsed_ms=(time.perf_counter() - start_time) * 1000,
            )

    # ------------------------------------------------------------
    # 多平台批量搜索
    # ------------------------------------------------------------

    def search_multi_platform(
        self,
        keyword: str,
        platforms: list[str] | None = None,
        max_items_per_platform: int = 15,
        enable_comments: bool = False,
    ) -> dict[str, MediaCrawlerResult]:
        """
        多平台批量搜索

        Args:
            keyword: 搜索关键词
            platforms: 平台列表, 默认 ["xhs", "bili", "wb", "zhihu"]
            max_items_per_platform: 每个平台最大数量
            enable_comments: 是否抓取评论

        Returns:
            Dict[platform_display, MediaCrawlerResult]
        """
        if platforms is None:
            platforms = ["xhs", "bili", "wb", "zhihu"]

        results: dict[str, MediaCrawlerResult] = {}
        for platform in platforms:
            display = get_platform_display(normalize_platform(platform))
            results[display] = self.search(
                keyword=keyword,
                platform=platform,
                max_items=max_items_per_platform,
                enable_comments=enable_comments,
            )
        return results

    # ------------------------------------------------------------
    # 读取最新数据文件
    # ------------------------------------------------------------

    def _fetch_latest_data(
        self, platform: str, max_items: int
    ) -> list[MediaCrawlerNewsItem]:
        """从 MediaCrawler 数据目录读取最新抓取的数据"""
        items: list[MediaCrawlerNewsItem] = []

        try:
            # 获取数据文件列表
            files_resp = self._session.get(
                f"{self.base_url}/api/data/files",
                params={"platform": platform, "file_type": "json"},
                timeout=self.timeout,
            )
            if files_resp.status_code != 200:
                return items

            files_data = files_resp.json()
            files = files_data.get("files", [])

            if not files:
                return items

            # 读取最新文件
            latest_file = files[0]
            file_path = latest_file.get("path", "")

            content_resp = self._session.get(
                f"{self.base_url}/api/data/files/{file_path}",
                params={"preview": "false", "limit": max_items},
                timeout=self.timeout,
            )
            if content_resp.status_code != 200:
                return items

            raw_data = content_resp.json()
            content = raw_data.get("content", [])

            # 解析并转换
            for raw_item in content[:max_items]:
                parsed = self._parse_raw_item(raw_item, platform)
                if parsed:
                    items.append(parsed)

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug("MediaCrawler 读取数据文件异常: %s", e)

        return items

    # ------------------------------------------------------------
    # 原始数据解析
    # ------------------------------------------------------------

    def _parse_raw_item(
        self, raw: dict[str, Any], platform: str
    ) -> MediaCrawlerNewsItem | None:
        """解析 MediaCrawler 原始数据为统一格式"""
        try:
            title = (
                raw.get("title")
                or raw.get("note_title")
                or raw.get("aweme_title")
                or raw.get("video_title")
                or raw.get("content_title")
                or raw.get("question_title")
                or ""
            )
            content = (
                raw.get("content")
                or raw.get("desc")
                or raw.get("note_content")
                or raw.get("video_desc")
                or raw.get("answer_content")
                or raw.get("text")
                or ""
            )
            url = (
                raw.get("url")
                or raw.get("note_url")
                or raw.get("aweme_url")
                or raw.get("video_url")
                or raw.get("share_url")
                or ""
            )
            item_id = (
                raw.get("id")
                or raw.get("note_id")
                or raw.get("aweme_id")
                or raw.get("video_id")
                or raw.get("post_id")
                or raw.get("answer_id")
                or ""
            )
            # 发布时间
            pub_ts = (
                raw.get("create_time")
                or raw.get("publish_time")
                or raw.get("pub_ts")
                or raw.get("time")
                or 0
            )
            published_at = ""
            if pub_ts:
                try:
                    if isinstance(pub_ts, (int, float)) and pub_ts > 0:
                        # 处理秒级和毫秒级时间戳
                        if pub_ts > 1e12:
                            pub_ts = pub_ts / 1000
                        published_at = datetime.fromtimestamp(int(pub_ts)).isoformat()
                except (ValueError, OSError):
                    pass

            # 互动数据
            like_count = int(raw.get("liked_count") or raw.get("like_count") or 0)
            comment_count = int(
                raw.get("comment_count") or raw.get("video_comment") or 0
            )
            share_count = int(
                raw.get("share_count") or raw.get("video_share_count") or 0
            )
            view_count = int(
                raw.get("viewed_count")
                or raw.get("video_play_count")
                or raw.get("read_count")
                or 0
            )

            # 创作者信息
            creator_nickname = (
                raw.get("nickname") or raw.get("user_name") or raw.get("author") or ""
            )

            # 评论列表 (如有)
            comments = (
                raw.get("comments", []) if isinstance(raw.get("comments"), list) else []
            )

            return MediaCrawlerNewsItem(
                title=str(title).strip(),
                content=str(content).strip(),
                url=str(url),
                source=get_platform_display(platform),
                category="social",
                published_at=published_at,
                platform=platform,
                item_id=str(item_id),
                creator_nickname=str(creator_nickname),
                like_count=like_count,
                comment_count=comment_count,
                share_count=share_count,
                view_count=view_count,
                comments=comments,
                raw=raw,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug("MediaCrawler 解析条目失败: %s", e)
            return None

    # ------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
        logger.info("MediaCrawler 缓存已清空")

    def cache_info(self) -> dict[str, Any]:
        """获取缓存状态"""
        return self._cache.info()

    # ------------------------------------------------------------
    # 便捷方法: 对齐 web_scraper 接口
    # ------------------------------------------------------------

    def fetch_social_news(
        self,
        keyword: str,
        platforms: list[str] | None = None,
        max_items: int = 50,
    ) -> list[MediaCrawlerNewsItem]:
        """
        获取自媒体舆情新闻 (对齐 web_scraper 风格接口)

        Args:
            keyword: 关键词 (股票名/行业/题材)
            platforms: 指定平台列表, None 表示默认 4 个主流平台
            max_items: 最大返回数量

        Returns:
            新闻条目列表 (按热度排序)
        """
        results = self.search_multi_platform(
            keyword=keyword,
            platforms=platforms,
            max_items_per_platform=max(max_items // 4, 10),
            enable_comments=False,
        )

        all_items: list[MediaCrawlerNewsItem] = []
        for result in results.values():
            if result.success:
                all_items.extend(result.items)

        # 按互动热度排序
        all_items.sort(
            key=lambda x: x.like_count * 1 + x.comment_count * 3 + x.share_count * 5,
            reverse=True,
        )

        return all_items[:max_items]
