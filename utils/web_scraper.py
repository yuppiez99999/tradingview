"""
网页抓取模块 (Web Scraper)
==========================

功能:
  - 抓取A股公告/研报/新闻舆情
  - 多源降级: Scrapling (反爬强) → requests+BeautifulSoup (基础兜底)
  - 中文财经网站适配 (东方财富/巨潮/新浪/同花顺)
  - TTL缓存避免重复请求
  - 统一 NewsItem 数据结构, 供 signal_fusion / ai_report_agent 使用

数据源:
  - 东方财富 (公告/研报/新闻)
  - 巨潮资讯 (证监会指定披露平台)
  - 新浪财经 (新闻舆情)
  - 同花顺财经 (新闻/研报)

依赖:
  - requests (必需)
  - bs4 (必需, BeautifulSoup HTML解析)
  - scrapling (可选, 增强 Cloudflare 等反爬绕过能力)

使用:
  from utils.web_scraper import WebScraper, NewsItem
  scraper = WebScraper()
  announcements = scraper.fetch_announcements("002371")  # 北方华创公告
  research_reports = scraper.fetch_research_reports("688041")  # 海光信息研报
  news = scraper.fetch_news("半导体")  # 半导体行业新闻
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, cast

import requests

try:
    import certifi

    _SSL_VERIFY = certifi.where()
except ImportError:
    _SSL_VERIFY = True

logger = logging.getLogger(__name__)

# 安全加固: TLS 验证保持 certifi.where() 启用, 不抑制 InsecureRequest 警告 (避免掩盖 MITM 回归)

# BeautifulSoup 前向声明(模块级) — 根除 ImportError fallback 时 =None 触发 [assignment]
# 此声明与 try/except 的成功/失败分支独立，保证 mypy 看到的类型永远是 Optional[type]
BeautifulSoup: Optional[type]

try:
    from bs4 import BeautifulSoup  # noqa: F811

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    BeautifulSoup = None

# Scrapling (可选, 增强 Cloudflare 等反爬绕过)
try:
    from scrapling import Fetcher, StealthyFetcher

    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False
    StealthyFetcher = None
    Fetcher = None
# Python 3 标准库始终包含 urllib.parse，移除无意义的 ImportError fallback
from urllib.parse import urlparse

from utils.logger import get_logger  # noqa: E402

logger = get_logger("web_scraper")

# ============================================================
# P3: 域名白名单 (防止 SSRF / 内网访问)
# ============================================================

# 允许的金融数据源域名 (含子域通配)
ALLOWED_DOMAINS = {
    "eastmoney.com",
    "np-anotice-stock.eastmoney.com",
    "np-cnotice-stock.eastmoney.com",
    "reportapi.eastmoney.com",
    "data.eastmoney.com",
    "search-api-web.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "sina.com.cn",
    "search.sina.com.cn",
    "sinajs.cn",
    "cninfo.com.cn",
    "www.cninfo.com.cn",
    "static.cninfo.com.cn",
    "10jqka.com.cn",
    "fund.10jqka.com.cn",
    "10jqka.com",
    "cls.cn",
    "finance.sina.com.cn",
    "stockstar.com",
}

# 内网/保留地址段 (禁止访问)
_BLOCKED_NETS = (
    "127.",
    "10.",
    "192.168.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.2",
    "172.3",
    "0.",
    "169.254.",
    "::1",
    "localhost",
)


def is_allowed_domain(url: str) -> bool:
    """校验 URL 是否在白名单域名且非内网地址 (P3 加固)。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # 拒绝内网/保留地址
        if host in ("localhost", "::1") or host.startswith(_BLOCKED_NETS):
            logger.warning(f"拒绝内网/保留地址: {url}")
            return False
        # 域名后缀匹配白名单
        for allowed in ALLOWED_DOMAINS:
            if host == allowed or host.endswith("." + allowed):
                return True
        logger.warning(f"拒绝非白名单域名: {host}")
        return False
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
        return False


# ============================================================
# 数据结构
# ============================================================


@dataclass
class NewsItem:
    """新闻/公告/研报统一数据结构"""

    title: str
    content: str = ""
    url: str = ""
    source: str = ""  # 来源 (eastmoney/cninfo/sina/10jqka)
    category: str = "news"  # news/announcement/research
    published_at: str = ""  # ISO 格式时间
    symbol: str = ""  # 关联股票代码
    sentiment_score: float = 0.0  # [-1, 1] 情感分 (需 NLP 模型填充)
    keywords: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScrapeResult:
    """抓取结果"""

    success: bool
    items: list[NewsItem] = field(default_factory=list)
    source: str = ""
    error: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "items_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }


# ============================================================
# TTL 缓存
# ============================================================


class _TTLCache:
    """简单 TTL 缓存 (线程安全)"""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple] = {}  # key -> (value, expire_at)

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.time() > expire_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        actual_ttl = ttl if ttl is not None else self.ttl
        self._store[key] = (value, time.time() + actual_ttl)

    def clear(self):
        self._store.clear()

    def info(self) -> dict[str, Any]:
        now = time.time()
        valid = sum(1 for _, exp in self._store.values() if now <= exp)
        return {"total_entries": len(self._store), "valid_entries": valid}


# ============================================================
# HTTP 会话
# ============================================================


def _create_session() -> requests.Session:
    """创建 HTTP 会话 (绕过系统代理)"""
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
    )
    return s


# ============================================================
# 主抓取器
# ============================================================


class WebScraper:
    """网页抓取器 (多源降级)

    优先级:
      1. Scrapling (Cloudflare/反爬绕过) - 若已安装
      2. requests + BeautifulSoup (基础 HTTP + HTML 解析)
    """

    # 缓存 TTL (秒)
    CACHE_TTL = {
        "announcement": 1800,  # 公告 30 分钟
        "research": 3600,  # 研报 1 小时
        "news": 600,  # 新闻 10 分钟
    }

    def __init__(self, cache_dir: Optional[Path] = None, timeout: int = 15):
        if not HAS_BS4:
            raise ImportError(
                "BeautifulSoup (bs4) 未安装, 请运行: pip install beautifulsoup4"
            )
        self.session = _create_session()
        self.timeout = timeout
        self.cache = _TTLCache(ttl_seconds=600)
        self.cache_dir = cache_dir or Path("data/scrape_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if HAS_SCRAPLING:
            logger.info("WebScraper 初始化: Scrapling 可用 (增强反爬能力)")
        else:
            logger.info("WebScraper 初始化: 使用 requests+bs4 (Scrapling 未安装)")

    # ----------------------------------------------------------
    # HTTP 请求 (降级链)
    # ----------------------------------------------------------

    def _fetch_html(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """获取 HTML 内容 (降级链: Scrapling → requests)"""
        # P3: 域名白名单校验 (防止 SSRF)
        if not is_allowed_domain(url):
            logger.warning(f"拒绝抓取非白名单 URL: {url}")
            return None
        # P1: Scrapling (若安装, 用于 Cloudflare/反爬强的网站)
        if HAS_SCRAPLING:
            try:
                page = Fetcher.get(url, stealthy=True, timeout=self.timeout)
                if page and page.status == 200:
                    return cast(str, page.body)
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.debug(f"Scrapling 抓取失败 ({url}): {e}, 回退到 requests")

        # P2: requests + bs4
        try:
            resp = self.session.get(
                url, params=params, timeout=self.timeout, verify=_SSL_VERIFY
            )
            if resp.status_code == 200:
                # 自动检测编码 (中文网站常用 gbk/utf-8)
                if resp.encoding and resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding
                return resp.text
            logger.warning(f"HTTP {resp.status_code}: {url}")
        except requests.exceptions.Timeout:
            logger.warning(f"请求超时 ({self.timeout}s): {url}")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"请求失败 ({url}): {e}")
        return None

    def _fetch_json(
        self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None
    ) -> Optional[Any]:
        """获取 JSON API 响应"""
        # P3: 域名白名单校验 (防止 SSRF)
        if not is_allowed_domain(url):
            logger.warning(f"拒绝抓取非白名单 URL: {url}")
            return None
        try:
            req_headers = dict(self.session.headers)
            if headers:
                req_headers.update(headers)
            resp = self.session.get(
                url,
                params=params,
                headers=req_headers,
                timeout=self.timeout,
                verify=_SSL_VERIFY,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"HTTP {resp.status_code}: {url}")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"JSON 请求失败 ({url}): {e}")
        return None

    # ----------------------------------------------------------
    # HTML 解析
    # ----------------------------------------------------------

    def _parse_html(self, html: str) -> Optional[BeautifulSoup]:
        if not html:
            return None
        try:
            return BeautifulSoup(html, "html.parser")
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning(f"HTML 解析失败: {e}")
            return None

    def _extract_text(self, soup: Optional[BeautifulSoup], selector: str) -> str:
        if soup is None:
            return ""
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else ""

    def _extract_items(self, soup: Optional[BeautifulSoup], selector: str) -> list:
        if soup is None:
            return []
        return soup.select(selector)

    # ----------------------------------------------------------
    # 公告抓取
    # ----------------------------------------------------------

    def fetch_announcements(self, symbol: str, limit: int = 20) -> list[NewsItem]:
        """抓取指定股票的公告

        Args:
            symbol: 股票代码 (6位数字, 如 "002371")
            limit: 最多返回条数

        Returns:
            公告列表
        """
        cache_key = f"ann_{symbol}_{limit}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug(f"公告缓存命中: {symbol}")
            return cast(list[NewsItem], cached)

        # 东方财富公告 API (JSON 接口, 无需 HTML 解析)
        result = self._fetch_eastmoney_announcements(symbol, limit)

        if not result:
            # 回退: 巨潮资讯
            result = self._fetch_cninfo_announcements(symbol, limit)

        self.cache.set(cache_key, result, ttl=self.CACHE_TTL["announcement"])
        return result

    def _fetch_eastmoney_announcements(self, symbol: str, limit: int) -> list[NewsItem]:
        """东方财富公告 API"""
        # 判断市场 (沪/深)
        if symbol.startswith("6") or symbol.startswith("9"):
            market = "sse"  # 沪市
        else:
            market = "szse"  # 深市

        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        params = {
            "cb": "jQuery123",
            "sr": "-1",
            "page_size": str(min(limit, 50)),
            "page_index": "1",
            "ann_type": "A",
            "client_source": "web",
            "stock_list": symbol,
            "f_node": "0",
            "s_node": "0",
        }
        data = self._fetch_json(url, params=params)
        if not data or not isinstance(data, dict):
            return []

        items = []
        for row in data.get("data", {}).get("list", [])[:limit]:
            title = row.get("title", "")
            art_code = row.get("art_code", "")
            published = row.get("notice_date", "") or row.get("eiTime", "")
            content_url = (
                f"https://np-cnotice-stock.eastmoney.com/api/content/notice?art_code={art_code}"
                if art_code
                else ""
            )
            items.append(
                NewsItem(
                    title=title,
                    content="",
                    url=content_url,
                    source="eastmoney",
                    category="announcement",
                    published_at=published,
                    symbol=symbol,
                    raw={"art_code": art_code, "market": market},
                )
            )
        logger.info(f"东方财富公告 ({symbol}): 获取 {len(items)} 条")
        return items

    def _fetch_cninfo_announcements(self, symbol: str, limit: int) -> list[NewsItem]:
        """巨潮资讯公告 (证监会指定披露平台)"""
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        # 判断板块
        if symbol.startswith("6") or symbol.startswith("9"):
            plate = "shmb"
        elif symbol.startswith("3"):
            plate = "szcn"
        elif symbol.startswith("0") or symbol.startswith("2"):
            plate = "szmb"
        else:
            plate = "shmb"

        data = {
            "stock": f"{symbol},9900022216",  # 内部 orgId 可省略
            "tabName": "fulltext",
            "pageSize": str(min(limit, 30)),
            "pageNum": "1",
            "column": "szse",
            "category": "",
            "plate": plate,
            "seDate": "",
            "searchkey": "",
        }
        try:
            resp = self.session.post(
                url, data=data, timeout=self.timeout, verify=_SSL_VERIFY
            )
            if resp.status_code != 200:
                return []
            json_data = resp.json()
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"巨潮公告请求失败 ({symbol}): {e}")
            return []

        items = []
        for row in json_data.get("announcements", [])[:limit]:
            title = row.get("announcementTitle", "")
            adjunct_url = row.get("adjunctUrl", "")
            content_url = (
                f"https://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else ""
            )
            published = row.get("announcementTime", "")
            # 时间戳转日期
            if isinstance(published, int):
                published = datetime.fromtimestamp(published / 1000).isoformat()
            items.append(
                NewsItem(
                    title=title,
                    content="",
                    url=content_url,
                    source="cninfo",
                    category="announcement",
                    published_at=str(published),
                    symbol=symbol,
                    raw={
                        "sec_code": row.get("secCode", ""),
                        "org_id": row.get("orgId", ""),
                    },
                )
            )
        logger.info(f"巨潮公告 ({symbol}): 获取 {len(items)} 条")
        return items

    # ----------------------------------------------------------
    # 研报抓取
    # ----------------------------------------------------------

    def fetch_research_reports(self, symbol: str, limit: int = 15) -> list[NewsItem]:
        """抓取研报

        Args:
            symbol: 股票代码
            limit: 最多返回条数
        """
        cache_key = f"res_{symbol}_{limit}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cast(list[NewsItem], cached)

        result = self._fetch_eastmoney_research(symbol, limit)
        self.cache.set(cache_key, result, ttl=self.CACHE_TTL["research"])
        return result

    def _fetch_eastmoney_research(self, symbol: str, limit: int) -> list[NewsItem]:
        """东方财富研报 API"""
        # 判断沪市/深市前缀
        if symbol.startswith("6") or symbol.startswith("9"):
            pass
        else:
            pass

        url = "https://reportapi.eastmoney.com/report/list"
        params = {
            "cb": "jQuery456",
            "industryCode": "*",
            "pageSize": str(min(limit, 30)),
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            "endTime": datetime.now().strftime("%Y-%m-%d"),
            "pageNo": "1",
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": symbol,
            "rcode": "",
            "p": "1",
            "pageNum": "1",
        }
        data = self._fetch_json(url, params=params)
        if not data or not isinstance(data, dict):
            return []

        items = []
        for row in data.get("data", [])[:limit]:
            title = row.get("title", "")
            org = row.get("orgSName", "")
            rating = row.get("emRatingName", "")
            rating_change = row.get("emRatingChangeName", "")
            published = row.get("publishDate", "") or row.get("publishTime", "")
            info_code = row.get("infoCode", "")
            content_url = (
                f"https://data.eastmoney.com/report/zw_stock.jshtml?infoCode={info_code}"
                if info_code
                else ""
            )
            # 研报内容摘要
            summary = row.get("content", "")[:500] if row.get("content") else ""

            keywords = []
            if rating:
                keywords.append(rating)
            if rating_change:
                keywords.append(rating_change)
            if org:
                keywords.append(org)

            items.append(
                NewsItem(
                    title=title,
                    content=summary,
                    url=content_url,
                    source="eastmoney_research",
                    category="research",
                    published_at=str(published),
                    symbol=symbol,
                    keywords=keywords,
                    raw={
                        "org": org,
                        "rating": rating,
                        "rating_change": rating_change,
                        "info_code": info_code,
                    },
                )
            )
        logger.info(f"东方财富研报 ({symbol}): 获取 {len(items)} 条")
        return items

    # ----------------------------------------------------------
    # 新闻舆情
    # ----------------------------------------------------------

    def fetch_news(self, keyword: str, limit: int = 20) -> list[NewsItem]:
        """抓取新闻舆情 (按关键词)

        Args:
            keyword: 搜索关键词 (如 "半导体", "AI算力", "002371")
            limit: 最多返回条数
        """
        cache_key = f"news_{keyword}_{limit}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cast(list[NewsItem], cached)

        # 优先: 新浪财经搜索 API
        result = self._fetch_sina_news(keyword, limit)
        if not result:
            result = self._fetch_eastmoney_news(keyword, limit)

        self.cache.set(cache_key, result, ttl=self.CACHE_TTL["news"])
        return result

    def _fetch_sina_news(self, keyword: str, limit: int) -> list[NewsItem]:
        """新浪财经新闻搜索"""
        url = "https://search.sina.com.cn/news"
        params = {
            "q": keyword,
            "c": "news",
            "sort": "time",  # 按时间排序
            "range": "all",
            "num": str(min(limit, 50)),
            "ie": "utf-8",
        }
        html = self._fetch_html(url, params=params)
        if html is None:
            return []
        soup = self._parse_html(html)
        if soup is None:
            return []

        items = []
        for box in soup.select(".box-result")[:limit]:
            title_el = box.select_one("h2 a")
            content_el = box.select_one(".content")
            time_el = box.select_one(".content .gray")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            content = content_el.get_text(strip=True) if content_el else ""
            link = title_el.get("href", "")
            published = time_el.get_text(strip=True) if time_el else ""
            items.append(
                NewsItem(
                    title=title,
                    content=content[:500],
                    url=cast(str, link),
                    source="sina",
                    category="news",
                    published_at=published,
                    keywords=[keyword],
                )
            )
        logger.info(f"新浪新闻 ({keyword}): 获取 {len(items)} 条")
        return items

    def _fetch_eastmoney_news(self, keyword: str, limit: int) -> list[NewsItem]:
        """东方财富新闻搜索 API"""
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        params = {
            "cb": "jQuery789",
            "param": json.dumps(
                {
                    "uid": "",
                    "keyword": keyword,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": 1,
                            "pageSize": min(limit, 20),
                            "preTag": "",
                            "postTag": "",
                        }
                    },
                }
            ),
        }
        data = self._fetch_json(url, params=params)
        if not data or not isinstance(data, dict):
            return []

        items = []
        articles = data.get("result", {}).get("cmsArticleWebOld", {}).get("list", [])
        for row in articles[:limit]:
            title = row.get("title", "").replace("<em>", "").replace("</em>", "")
            content = row.get("content", "")[:500]
            published = row.get("date", "")
            url_link = row.get("url", "")
            items.append(
                NewsItem(
                    title=title,
                    content=content,
                    url=url_link,
                    source="eastmoney_news",
                    category="news",
                    published_at=published,
                    keywords=[keyword],
                )
            )
        logger.info(f"东方财富新闻 ({keyword}): 获取 {len(items)} 条")
        return items

    # ----------------------------------------------------------
    # 行业/板块舆情
    # ----------------------------------------------------------

    def fetch_industry_sentiment(
        self, industry: str, limit: int = 30
    ) -> dict[str, Any]:
        """抓取行业舆情汇总

        Args:
            industry: 行业名称 (如 "半导体", "新能源", "医药")
            limit: 每个数据源的最大条数

        Returns:
            {
                "industry": "半导体",
                "news_count": 25,
                "latest_news": [...],
                "hot_keywords": ["国产替代", "AI芯片", ...],
                "fetched_at": "2026-07-10T..."
            }
        """
        news_items = self.fetch_news(industry, limit=limit)

        # 提取热门关键词 (简单词频统计)
        word_count: dict[str, int] = {}
        stop_words = {
            "的",
            "了",
            "在",
            "是",
            "和",
            "与",
            "及",
            "或",
            "为",
            "对",
            "由",
            "从",
        }
        for item in news_items:
            # 从标题和内容中提取关键词
            text = item.title + " " + item.content
            # 简单分词: 提取2-4字中文词
            words = re.findall(r"[\u4e00-\u9fa5]{2,4}", text)
            for w in words:
                if w in stop_words or len(w) < 2:
                    continue
                word_count[w] = word_count.get(w, 0) + 1

        # 排序取 Top 10
        hot_keywords = sorted(word_count.items(), key=lambda x: -x[1])[:10]
        hot_keywords_list = [w for w, _ in hot_keywords]

        return {
            "industry": industry,
            "news_count": len(news_items),
            "latest_news": [item.to_dict() for item in news_items[:5]],
            "hot_keywords": hot_keywords_list,
            "keyword_frequencies": dict(hot_keywords),
            "fetched_at": datetime.now().isoformat(),
        }

    # ----------------------------------------------------------
    # 批量抓取 (供组合分析用)
    # ----------------------------------------------------------

    def fetch_portfolio_news(
        self, symbols: list[str], limit_per_symbol: int = 10
    ) -> dict[str, list[NewsItem]]:
        """批量抓取组合内所有标的的新闻/公告

        Args:
            symbols: 股票代码列表
            limit_per_symbol: 每个标的的最大条数

        Returns:
            {symbol: [NewsItem, ...], ...}
        """
        result: dict[str, list[NewsItem]] = {}
        for symbol in symbols:
            try:
                items = self.fetch_announcements(symbol, limit=limit_per_symbol)
                result[symbol] = items
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.warning(f"抓取 {symbol} 失败: {e}")
                result[symbol] = []
        return result

    # ----------------------------------------------------------
    # 持久化 (JSON 缓存)
    # ----------------------------------------------------------

    def save_to_cache_file(self, key: str, data: Any) -> Path:
        """保存到 JSON 缓存文件"""
        cache_file = self.cache_dir / f"{key}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return cache_file

    def load_from_cache_file(self, key: str) -> Optional[Any]:
        """从 JSON 缓存文件加载"""
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"读取缓存文件失败 ({cache_file}): {e}")
            return None

    # ============================================================
    # 自媒体舆情 (MediaCrawler 集成)
    # ============================================================

    def fetch_social_media_news(
        self,
        keyword: str,
        platforms: Optional[list[str]] = None,
        max_items: int = 50,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """
        抓取自媒体平台舆情新闻 (小红书/抖音/B站/微博/知乎等)

        通过 MediaCrawlerAdapter 调用外部 MediaCrawler 服务,
        Feature Flag 关闭或服务不可用时返回空列表, 不影响主流程。

        参数:
            keyword:   搜索关键词 (股票名/行业/题材, 如 "半导体", "贵州茅台")
            platforms: 平台列表, None 表示默认 4 个主流平台
                       支持: xhs(小红书), dy(抖音), ks(快手), bili(B站),
                             wb(微博), tieba(贴吧), zhihu(知乎)
            max_items: 最大返回条数
            use_cache: 是否使用缓存

        返回:
            新闻条目列表, 格式对齐 fetch_stock_news:
            [
                {
                    "title": "...",
                    "content": "...",
                    "url": "...",
                    "source": "小红书",
                    "category": "social",
                    "published_at": "2026-07-28T10:30:00",
                    "symbol": "",
                    "sentiment_score": 0.0,
                    "like_count": 1234,
                    "comment_count": 56,
                    "share_count": 12,
                    ...
                },
                ...
            ]
        """
        # 缓存 key
        cache_key = (
            f"social_media:{keyword}:{','.join(sorted(platforms or []))}:{max_items}"
        )

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.debug(f"自媒体舆情命中缓存: {keyword}")
                return cached

        # 尝试调用 MediaCrawlerAdapter
        try:
            from utils.media_crawler_adapter import MediaCrawlerAdapter

            adapter = MediaCrawlerAdapter(enabled=True)

            # 健康检查
            health = adapter.check_health()
            if not health.get("available", False):
                logger.debug(
                    f"MediaCrawler 服务不可用 ({health.get('reason')}), 跳过自媒体舆情"
                )
                return []

            # 抓取数据
            items = adapter.fetch_social_news(
                keyword=keyword,
                platforms=platforms,
                max_items=max_items,
            )

            # 转换为 Dict 格式 (对齐 NewsItem.to_dict())
            result = [item.to_dict() for item in items]

            # 写入缓存 (缓存 30 分钟)
            if use_cache and result:
                self.cache.set(cache_key, result, ttl=1800)

            logger.info(f"自媒体舆情抓取完成: {keyword} → {len(result)} 条")
            return result

        except ImportError:
            logger.debug("MediaCrawlerAdapter 未安装, 跳过自媒体舆情")
            return []
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"自媒体舆情抓取异常: {e}")
            return []

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    def clear_cache(self):
        """清空内存缓存"""
        self.cache.clear()
        logger.info("已清空内存缓存")

    def cache_info(self) -> dict[str, Any]:
        """获取缓存信息"""
        return {
            "memory_cache": self.cache.info(),
            "cache_dir": str(self.cache_dir),
            "scrapling_enabled": HAS_SCRAPLING,
            "bs4_enabled": HAS_BS4,
        }

    def get_status(self) -> dict[str, Any]:
        """获取抓取器状态"""
        return {
            "scrapling_available": HAS_SCRAPLING,
            "bs4_available": HAS_BS4,
            "timeout": self.timeout,
            "cache": self.cache.info(),
            "cache_dir": str(self.cache_dir),
        }


# ============================================================
# 便捷函数
# ============================================================

_scraper_instance: Optional[WebScraper] = None


def get_scraper() -> WebScraper:
    """获取全局 WebScraper 实例 (单例)"""
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = WebScraper()
    return _scraper_instance


def fetch_announcements(symbol: str, limit: int = 20) -> list[dict]:
    """便捷函数: 抓取公告"""
    items = get_scraper().fetch_announcements(symbol, limit)
    return [item.to_dict() for item in items]


def fetch_research_reports(symbol: str, limit: int = 15) -> list[dict]:
    """便捷函数: 抓取研报"""
    items = get_scraper().fetch_research_reports(symbol, limit)
    return [item.to_dict() for item in items]


def fetch_news(keyword: str, limit: int = 20) -> list[dict]:
    """便捷函数: 抓取新闻"""
    items = get_scraper().fetch_news(keyword, limit)
    return [item.to_dict() for item in items]


# ============================================================
# 自检
# ============================================================


def self_test() -> bool:
    """模块自检 (不发起真实网络请求, 仅验证依赖和类定义)"""
    try:
        assert HAS_BS4, "bs4 (BeautifulSoup) 未安装"
        scraper = WebScraper(timeout=5)
        assert scraper.timeout == 5

        # 测试数据结构
        item = NewsItem(
            title="测试公告",
            content="测试内容",
            source="test",
            symbol="002371",
        )
        d = item.to_dict()
        assert d["title"] == "测试公告"
        assert d["symbol"] == "002371"

        # 测试缓存
        scraper.cache.set("test_key", "test_value", ttl=10)
        assert scraper.cache.get("test_key") == "test_value"
        scraper.cache.clear()
        assert scraper.cache.get("test_key") is None

        # 测试状态
        status = scraper.get_status()
        assert "scrapling_available" in status
        assert "bs4_available" in status

        logger.info("[OK] web_scraper.py 自检通过")
        logger.info(f"  - Scrapling 可用: {status['scrapling_available']}")
        logger.info(f"  - BeautifulSoup 可用: {status['bs4_available']}")
        logger.info(f"  - 超时: {status['timeout']}s")
        return True
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as e:
        logger.error(f"[FAIL] web_scraper.py 自检失败: {e}")
        return False


if __name__ == "__main__":
    self_test()
