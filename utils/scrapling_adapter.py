"""Scrapling 高性能爬虫适配器 — 28 系统集成层

核心功能:
    封装 Scrapling 框架的 StealthyFetcher / PlayWrightFetcher, 提供
    企业级反爬能力 (Cloudflare 绕过 / JS 渲染 / 自动选择器), 补充
    web_scraper.py 的基础 requests+bs4 抓取能力.

设计原则:
    1. 懒加载: 只在首次调用时初始化 Scrapling, 不影响系统启动
    2. 优雅降级: Scrapling 不可用时回退到 web_scraper.WebScraper
    3. 统一数据结构: 返回 web_scraper.NewsItem, 保持下游兼容
    4. 单例模式: 避免重复初始化浏览器引擎

降级链:
    Scrapling StealthyFetcher (反爬强, JS渲染)
        ↓ (不可用时降级)
    Scrapling Fetcher (基础模式)
        ↓ (不可用时降级)
    web_scraper.WebScraper (requests + bs4)
        ↓ (不可用时降级)
    空结果 + 警告日志

用法:
    from utils.scrapling_adapter import ScraplingAdapter

    adapter = ScraplingAdapter()
    items = adapter.fetch_news("算力")           # 关键词搜新闻
    items = adapter.fetch_url("https://...")     # 抓取指定URL
    items = adapter.fetch_announcements("600519") # 个股公告

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("scrapling_adapter")

# ============================================================
# Scrapling 可选导入 (懒加载)
# ============================================================

_scrapling_available: bool | None = None
_StealthyFetcher = None
_Fetcher = None
_PlayWrightFetcher = None


def _check_scrapling() -> bool:
    """检测 Scrapling 是否可用 (一次性检测, 缓存结果)."""
    global _scrapling_available, _StealthyFetcher, _Fetcher, _PlayWrightFetcher
    if _scrapling_available is not None:
        return _scrapling_available
    try:
        from scrapling import Fetcher, StealthyFetcher  # type: ignore
        _StealthyFetcher = StealthyFetcher
        _Fetcher = Fetcher
        # PlayWrightFetcher 是可选的 (需要 playwright 依赖)
        try:
            from scrapling import PlayWrightFetcher  # type: ignore
            _PlayWrightFetcher = PlayWrightFetcher
        except ImportError:
            pass
        _scrapling_available = True
        logger.info("Scrapling 加载成功 (StealthyFetcher + Fetcher)")
    except ImportError:
        _scrapling_available = False
        logger.info("Scrapling 不可用, 将降级到 web_scraper.WebScraper")
    return _scrapling_available


# ============================================================
# 适配器主类
# ============================================================


class ScraplingAdapter:
    """Scrapling 高性能爬虫适配器.

    封装 Scrapling 的反爬能力, 提供统一的新闻/公告/URL 抓取接口.
    Scrapling 不可用时自动降级到 web_scraper.WebScraper.

    Attributes:
        use_stealth: 是否优先使用 StealthyFetcher (反爬更强, 但更慢)
        timeout: 请求超时秒数
        _fetcher: Scrapling fetcher 实例 (懒加载)
        _fallback: WebScraper 降级实例 (懒加载)

    Usage:
        >>> adapter = ScraplingAdapter()
        >>> items = adapter.fetch_news("半导体")
        >>> items = adapter.fetch_url("https://finance.eastmoney.com/...")
    """

    def __init__(
        self,
        use_stealth: bool = True,
        timeout: int = 30,
    ) -> None:
        """初始化适配器.

        Args:
            use_stealth: 是否优先使用 StealthyFetcher (默认 True, 反爬更强)
            timeout: 请求超时秒数
        """
        self.use_stealth = use_stealth
        self.timeout = timeout
        self._fetcher: Any = None
        self._fallback: Any = None
        self._available: bool | None = None

    # ------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Scrapling 是否可用."""
        if self._available is None:
            self._available = _check_scrapling()
        return self._available

    def _get_fetcher(self) -> Any:
        """获取 Scrapling fetcher 实例 (懒加载, 单例)."""
        if self._fetcher is not None:
            return self._fetcher
        if not self.is_available:
            return None
        try:
            if self.use_stealth and _StealthyFetcher is not None:
                self._fetcher = _StealthyFetcher(auto_match=False)
                logger.debug("Scrapling StealthyFetcher 初始化完成")
            elif _Fetcher is not None:
                self._fetcher = _Fetcher(auto_match=False)
                logger.debug("Scrapling Fetcher 初始化完成")
            else:
                self._available = False
                return None
        except Exception as e:
            logger.warning(f"Scrapling fetcher 初始化失败: {e}, 降级到 WebScraper")
            self._available = False
            return None
        return self._fetcher

    def _get_fallback(self) -> Any:
        """获取 WebScraper 降级实例 (懒加载)."""
        if self._fallback is not None:
            return self._fallback
        try:
            from utils.web_scraper import WebScraper
            self._fallback = WebScraper()
            logger.debug("WebScraper 降级实例初始化完成")
        except ImportError as e:
            logger.warning(f"WebScraper 降级不可用: {e}")
            return None
        return self._fallback

    # ------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------

    def fetch_url(
        self,
        url: str,
        selector: str | None = None,
        render_js: bool = False,
    ) -> dict[str, Any]:
        """抓取指定 URL, 返回页面内容.

        Args:
            url: 目标 URL
            selector: CSS 选择器 (只提取匹配元素, None 提取全页)
            render_js: 是否渲染 JS (Scrapling PlayWrightFetcher)

        Returns:
            {"success": bool, "html": str, "text": str, "url": str, "source": str, "error": str}
        """
        if not url:
            return self._empty_result(url, "URL 为空")

        # 尝试 Scrapling
        if self.is_available:
            fetcher = self._get_fetcher()
            if fetcher is not None:
                try:
                    page = fetcher.get(url, timeout=self.timeout)
                    html = getattr(page, "html_content", "") or str(page)
                    text = self._extract_text(page, selector)
                    return {
                        "success": True,
                        "html": html,
                        "text": text,
                        "url": url,
                        "source": "scrapling",
                        "error": "",
                    }
                except Exception as e:
                    logger.debug(f"Scrapling 抓取 {url} 失败: {e}, 降级")

        # 降级: 直接 requests
        return self._fallback_fetch_url(url, selector)

    def fetch_news(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """按关键词抓取新闻.

        优先使用 WebScraper.fetch_news (已适配东方财富/新浪等中文财经网站),
        Scrapling 仅在 WebScraper 失败时作为增强抓取层.

        Args:
            keyword: 搜索关键词 (如 "算力", "半导体")
            limit: 最多返回条数

        Returns:
            NewsItem 字典列表 [{title, content, url, source, ...}]
        """
        if not keyword:
            return []

        # 优先用 WebScraper (已适配中文财经网站)
        scraper = self._get_fallback()
        if scraper is not None:
            try:
                result = scraper.fetch_news(keyword)
                if result and result.success and result.items:
                    items = result.items[:limit]
                    return [item.to_dict() for item in items]
            except Exception as e:
                logger.debug(f"WebScraper.fetch_news 失败: {e}")

        # Scrapling 兜底: 直接抓东方财富搜索页
        if self.is_available:
            return self._scrapling_fetch_news(keyword, limit)

        logger.warning(f"新闻抓取全部失败: keyword={keyword}")
        return []

    def fetch_announcements(self, stock_code: str, limit: int = 20) -> list[dict[str, Any]]:
        """抓取个股公告.

        Args:
            stock_code: 股票代码 (如 "600519")
            limit: 最多返回条数

        Returns:
            NewsItem 字典列表
        """
        if not stock_code:
            return []

        scraper = self._get_fallback()
        if scraper is not None:
            try:
                result = scraper.fetch_announcements(stock_code)
                if result and result.success and result.items:
                    items = result.items[:limit]
                    return [item.to_dict() for item in items]
            except Exception as e:
                logger.debug(f"WebScraper.fetch_announcements 失败: {e}")

        logger.warning(f"公告抓取失败: stock={stock_code}")
        return []

    def fetch_research_reports(self, stock_code: str, limit: int = 10) -> list[dict[str, Any]]:
        """抓取个股研报.

        Args:
            stock_code: 股票代码
            limit: 最多返回条数

        Returns:
            NewsItem 字典列表
        """
        if not stock_code:
            return []

        scraper = self._get_fallback()
        if scraper is not None:
            try:
                result = scraper.fetch_research_reports(stock_code)
                if result and result.success and result.items:
                    items = result.items[:limit]
                    return [item.to_dict() for item in items]
            except Exception as e:
                logger.debug(f"WebScraper.fetch_research_reports 失败: {e}")

        logger.warning(f"研报抓取失败: stock={stock_code}")
        return []

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _extract_text(self, page: Any, selector: str | None) -> str:
        """从 Scrapling 页面对象提取文本.

        Args:
            page: Scrapling 页面对象
            selector: CSS 选择器

        Returns:
            提取的纯文本
        """
        try:
            if selector:
                elements = page.css(selector) if hasattr(page, "css") else []
                return " ".join(getattr(el, "text", "") for el in elements)
            # 全页文本
            if hasattr(page, "get_all_text"):
                return page.get_all_text()
            if hasattr(page, "text"):
                return page.text
            return str(page)
        except Exception as e:
            logger.debug(f"文本提取失败: {e}")
            return ""

    def _fallback_fetch_url(self, url: str, selector: str | None) -> dict[str, Any]:
        """降级: 使用 requests 直接抓取 URL.

        Args:
            url: 目标 URL
            selector: CSS 选择器

        Returns:
            抓取结果字典
        """
        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(
                url,
                timeout=self.timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    )
                },
                verify=True,  # 保留 SSL 证书验证 (安全合规)
            )
            resp.encoding = resp.apparent_encoding or "utf-8"
            html = resp.text

            text = html
            if selector:
                soup = BeautifulSoup(html, "html.parser")
                elements = soup.select(selector)
                text = " ".join(el.get_text(strip=True) for el in elements)

            return {
                "success": True,
                "html": html,
                "text": text,
                "url": url,
                "source": "requests",
                "error": "",
            }
        except Exception as e:
            return self._empty_result(url, f"requests 降级失败: {e}")

    def _scrapling_fetch_news(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """Scrapling 兜底: 抓取东方财富搜索页.

        Args:
            keyword: 搜索关键词
            limit: 最多返回条数

        Returns:
            NewsItem 字典列表
        """
        fetcher = self._get_fetcher()
        if fetcher is None:
            return []

        try:
            # 东方财富新闻搜索
            url = f"https://so.eastmoney.com/news/s?keyword={keyword}"
            page = fetcher.get(url, timeout=self.timeout)
            items: list[dict[str, Any]] = []

            # 尝试解析搜索结果
            if hasattr(page, "css"):
                results = page.css(".news_item") or page.css(".result") or []
                for item in results[:limit]:
                    title_el = item.css("a") or item.css(".title")
                    title = getattr(title_el[0], "text", "") if title_el else ""
                    link = getattr(title_el[0], "href", "") if title_el else ""
                    content = getattr(item, "text", "")
                    items.append({
                        "title": title.strip(),
                        "content": content.strip()[:500],
                        "url": link,
                        "source": "eastmoney",
                        "category": "news",
                        "published_at": "",
                        "symbol": "",
                        "sentiment_score": 0.0,
                        "keywords": [keyword],
                        "raw": {},
                    })
            if items:
                logger.info(f"Scrapling 抓取 {keyword}: {len(items)} 条")
                return items
        except Exception as e:
            logger.debug(f"Scrapling 新闻抓取失败: {e}")

        return []

    def _empty_result(self, url: str, error: str) -> dict[str, Any]:
        """构造空结果.

        Args:
            url: URL
            error: 错误信息

        Returns:
            空结果字典
        """
        return {
            "success": False,
            "html": "",
            "text": "",
            "url": url,
            "source": "none",
            "error": error,
        }


# ============================================================
# 单例便捷函数
# ============================================================

_default_adapter: ScraplingAdapter | None = None


def get_adapter(use_stealth: bool = True) -> ScraplingAdapter:
    """获取默认适配器单例.

    Args:
        use_stealth: 是否优先使用 StealthyFetcher

    Returns:
        ScraplingAdapter 实例
    """
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = ScraplingAdapter(use_stealth=use_stealth)
    return _default_adapter


def fetch_news(keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """便捷函数: 按关键词抓取新闻.

    Args:
        keyword: 搜索关键词
        limit: 最多返回条数

    Returns:
        NewsItem 字典列表
    """
    return get_adapter().fetch_news(keyword, limit)


def fetch_url(url: str, selector: str | None = None) -> dict[str, Any]:
    """便捷函数: 抓取指定 URL.

    Args:
        url: 目标 URL
        selector: CSS 选择器

    Returns:
        抓取结果字典
    """
    return get_adapter().fetch_url(url, selector)


def is_available() -> bool:
    """便捷函数: 检测 Scrapling 是否可用.

    Returns:
        Scrapling 是否可用
    """
    return _check_scrapling()
