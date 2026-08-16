"""
外部数据源模块 v1.0 — 整合 public-apis 的免费金融数据
============================================================

数据源清单 (来源: public-apis 项目):
  1. FRED (圣路易斯联储经济数据) — 宏观经济指标, 无需API Key
  2. Econdb (全球宏观经济) — 免费无需认证
  3. Fed Treasury (美国财政部) — 国债收益率, 无需认证
  4. Alpha Vantage — 全球股票/外汇/加密货币, 需apiKey (免费500次/天)
  5. Finnhub — 股票/外汇/加密货币+新闻, 需apiKey (免费60次/分钟)
  6. Twelve Data — 股票市场数据, 需apiKey (免费800次/天)
  7. Yahoo Finance — 实时低延迟行情, 需apiKey
  8. CoinGecko — 加密货币, 无需认证

用途:
  - 补充 Wind MCP / iFinD MCP 无法获取的海外市场数据
  - 提供宏观经济指标 (CPI/PPI/GDP/国债收益率)
  - 提供全球股票/ETF 行情 (美股/港股)
  - 提供加密货币价格 (作为风险情绪指标)

设计原则:
  - 所有API调用必须有 timeout
  - 优雅降级: API不可用时返回None, 不崩溃
  - 本地缓存: 避免重复请求
  - 环境变量管理API Key
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import requests

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "external_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 请求 Session (绕过系统代理, 避免代理干扰)
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.proxies = {"http": None, "https": None}

# 默认超时
DEFAULT_TIMEOUT = 15  # 秒

# P0-C1 修复 (2026-07-29): 缓存文件读写锁 (多线程/计划任务重入防护)
_CACHE_LOCK = threading.Lock()


def _parse_api_float(value: Any) -> Optional[float]:
    """安全解析 API 返回的数值字段。

    P1-T1 修复 (2026-07-29): FRED 缺失值返回 ".", 其它 API 可能返回
    None/"N/A"/空串, 此前 float() 直接抛异常被外层 fail-safe 吞掉,
    导致整条指标数据静默丢失。
    """
    if value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if result != result:  # NaN
        return None
    return result


# 缓存有效期
CACHE_TTL = {
    "macro": 3600,  # 宏观数据缓存1小时
    "stock": 300,  # 股票行情缓存5分钟
    "crypto": 180,  # 加密货币缓存3分钟
    "news": 600,  # 新闻缓存10分钟
    "bond": 1800,  # 国债收益率缓存30分钟
}


@dataclass
class MacroIndicator:
    """宏观经济指标"""

    name: str
    value: float
    unit: str
    date: str
    source: str
    previous: Optional[float] = None
    change: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "date": self.date,
            "source": self.source,
            "previous": self.previous,
            "change": self.change,
        }


class FREDApi:
    """FRED (圣路易斯联储经济数据) API

    免费, 需要API Key (https://fred.stlouisfed.org/docs/api/api_key.html)
    提供美国及全球宏观经济指标。
    """

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "")
        self.available = bool(self.api_key)

    def get_indicator(self, series_id: str) -> Optional[MacroIndicator]:
        """获取经济指标最新值

        常用 series_id:
          - CPIAUCSL: 美国CPI
          - PPIACO: 美国PPI
          - GDP: 美国GDP
          - DGS10: 10年期国债收益率
          - DGS2: 2年期国债收益率
          - FEDFUNDS: 联邦基金利率
          - UNRATE: 失业率
          - M2SL: M2货币供应量
        """
        if not self.available:
            return None

        try:
            url = f"{self.BASE_URL}/series/observations"
            params = {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            }
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"FRED API {series_id} 返回 {resp.status_code}")
                return None

            data = resp.json()
            observations = data.get("observations", [])
            if not observations:
                return None

            latest = observations[0]
            # P1-T1: FRED 缺失值为 ".", float() 会抛异常吞掉整条数据
            value = _parse_api_float(latest.get("value"))
            if value is None:
                logger.warning(f"FRED {series_id} 最新观测值无效: {latest.get('value')!r}")
                return None
            date = latest.get("date", "")

            previous = None
            if len(observations) > 1:
                previous = _parse_api_float(observations[1].get("value"))

            change = (value - previous) if previous is not None else None

            return MacroIndicator(
                name=series_id,
                value=value,
                unit="%",
                date=date,
                source="FRED",
                previous=previous,
                change=change,
            )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"FRED 获取 {series_id} 失败: {e}")
            return None

    def get_macro_snapshot(self) -> Dict[str, MacroIndicator]:
        """获取宏观经济快照"""
        indicators = {
            "CPI": "CPIAUCSL",
            "PPI": "PPIACO",
            "GDP": "GDP",
            "10Y_TREASURY": "DGS10",
            "2Y_TREASURY": "DGS2",
            "FED_FUNDS_RATE": "FEDFUNDS",
            "UNEMPLOYMENT": "UNRATE",
            "M2": "M2SL",
        }

        result = {}
        for name, series_id in indicators.items():
            indicator = self.get_indicator(series_id)
            if indicator:
                result[name] = indicator
        return result


class EcondbApi:
    """Econdb API — 全球宏观经济数据

    免费, 无需认证。
    提供: GDP/CPI/工业生产/零售销售/失业率/利率/汇率等。
    """

    BASE_URL = "https://www.econdb.com/api"

    def __init__(self):
        self.available = True

    def get_indicator(self, ticker: str) -> Optional[MacroIndicator]:
        """获取经济指标

        常用 ticker:
          - CPIUS: 美国CPI
          - CPIEA: 欧元区CPI
          - GDPCN: 中国GDP
          - CPICN: 中国CPI
          - IR10YUS: 美国10年期国债
        """
        try:
            url = f"{self.BASE_URL}/series/{ticker}/"
            params = {"format": "json"}
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            series_data = data.get("data", [])
            if not series_data:
                return None

            latest = series_data[-1]
            # P1-T1: 缺失值 None/"N/A" 此前 float() 抛异常吞掉整条数据
            value = _parse_api_float(latest.get("value"))
            if value is None:
                logger.warning(f"Econdb {ticker} 最新观测值无效: {latest.get('value')!r}")
                return None
            date = latest.get("date", "")

            return MacroIndicator(
                name=ticker,
                value=value,
                unit="%",
                date=date,
                source="Econdb",
            )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"Econdb 获取 {ticker} 失败: {e}")
            return None


class FedTreasuryApi:
    """美国财政部 API

    免费, 无需认证。
    提供国债收益率曲线数据。
    """

    BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

    def __init__(self):
        self.available = True

    def get_treasury_yields(self) -> Dict[str, float]:
        """获取最新国债收益率

        Returns:
            {"3M": 4.5, "6M": 4.3, "1Y": 4.0, "2Y": 3.8, "5Y": 3.5, "10Y": 3.2, "30Y": 3.4}
        """
        try:
            url = f"{self.BASE_URL}/v2/accounting/od/avg_interest_rates"
            params = {
                "fields": "security_desc,avg_interest_rate_amount,record_date",
                "filter": "security_desc:in:(Bill,Bond,Note)",
                "sort": "-record_date",
                "page_size": 50,
            }
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return {}

            data = resp.json()
            records = data.get("data", [])

            yields = {}
            for record in records:
                desc = record.get("security_desc", "")
                rate = float(record.get("avg_interest_rate_amount", 0))
                record.get("record_date", "")

                # 简化: 根据描述匹配期限
                if "3-Month" in desc or "3 Month" in desc:
                    yields["3M"] = rate
                elif "6-Month" in desc or "6 Month" in desc:
                    yields["6M"] = rate
                elif "1-Year" in desc or "1 Year" in desc:
                    yields["1Y"] = rate
                elif "2-Year" in desc or "2 Year" in desc:
                    yields["2Y"] = rate
                elif "5-Year" in desc or "5 Year" in desc:
                    yields["5Y"] = rate
                elif "10-Year" in desc or "10 Year" in desc:
                    yields["10Y"] = rate
                elif "30-Year" in desc or "30 Year" in desc:
                    yields["30Y"] = rate

            return yields

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"Fed Treasury 获取国债收益率失败: {e}")
            return {}


class AlphaVantageApi:
    """Alpha Vantage API

    免费, 需要API Key (https://www.alphavantage.co/support/#api-key)
    限制: 500次/天, 5次/分钟
    提供: 股票/外汇/加密货币/技术指标
    """

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.available = bool(self.api_key)

    def get_global_quote(self, symbol: str) -> Optional[Dict]:
        """获取全球股票报价

        Args:
            symbol: 股票代码 (如 "AAPL", "MSFT", "0700.HK")
        """
        if not self.available:
            return None

        try:
            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self.api_key,
            }
            resp = _SESSION.get(self.BASE_URL, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            quote = data.get("Global Quote", {})
            if not quote:
                return None

            return {
                "symbol": symbol,
                "open": float(quote.get("02. open", 0)),
                "high": float(quote.get("03. high", 0)),
                "low": float(quote.get("04. low", 0)),
                "close": float(quote.get("05. price", 0)),
                "prev_close": float(quote.get("08. previous close", 0)),
                "volume": int(quote.get("06. volume", 0)),
                "change_pct": float(quote.get("10. change percent", "0%").strip("%")),
                "source": "alpha_vantage",
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"Alpha Vantage 获取 {symbol} 失败: {e}")
            return None


class FinnhubApi:
    """Finnhub API

    免费, 需要API Key (https://finnhub.io/register)
    限制: 60次/分钟
    提供: 股票/外汇/加密货币行情 + 新闻 + 情绪分析
    """

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self.available = bool(self.api_key)

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """获取股票报价 (美股/港股)"""
        if not self.available:
            return None

        try:
            url = f"{self.BASE_URL}/quote"
            params = {
                "symbol": symbol,
                "token": self.api_key,
            }
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            if not data or data.get("c", 0) == 0:
                return None

            current = float(data.get("c", 0))
            prev_close = float(data.get("pc", 0))
            change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0

            return {
                "symbol": symbol,
                "open": float(data.get("o", 0)),
                "high": float(data.get("h", 0)),
                "low": float(data.get("l", 0)),
                "close": current,
                "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
                "source": "finnhub",
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"Finnhub 获取 {symbol} 失败: {e}")
            return None

    def get_market_news(self, category: str = "general") -> List[Dict]:
        """获取市场新闻

        Args:
            category: general/forex/crypto/merger
        """
        if not self.available:
            return []

        try:
            url = f"{self.BASE_URL}/news"
            params = {
                "category": category,
                "token": self.api_key,
            }
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return []

            news = resp.json()
            return [
                {
                    "title": n.get("headline", ""),
                    "summary": n.get("summary", ""),
                    "source": n.get("source", ""),
                    "url": n.get("url", ""),
                    "datetime": datetime.fromtimestamp(n.get("datetime", 0)).isoformat(),
                    "category": category,
                }
                for n in news[:10]  # 最多10条
            ]

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"Finnhub 获取新闻失败: {e}")
            return []


class CoinGeckoApi:
    """CoinGecko API

    免费, 无需认证。
    提供: 加密货币价格/市值/交易量
    限制: 10-50次/分钟 (无认证)
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.available = True

    def get_price(self, coin_id: str = "bitcoin", vs_currency: str = "usd") -> Optional[Dict]:
        """获取加密货币价格

        Args:
            coin_id: bitcoin/ethereum/tether等
            vs_currency: usd/cny
        """
        try:
            url = f"{self.BASE_URL}/simple/price"
            params = {
                "ids": coin_id,
                "vs_currencies": vs_currency,
                "include_24hr_change": "true",
                "include_market_cap": "true",
            }
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            coin_data = data.get(coin_id, {})
            if not coin_data:
                return None

            return {
                "coin": coin_id,
                "price": coin_data.get(vs_currency, 0),
                "change_24h_pct": coin_data.get(f"{vs_currency}_24h_change", 0),
                "market_cap": coin_data.get(f"{vs_currency}_market_cap", 0),
                "source": "coingecko",
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"CoinGecko 获取 {coin_id} 失败: {e}")
            return None

    def get_global_market(self) -> Optional[Dict]:
        """获取加密货币全球市场数据 (作为风险情绪指标)"""
        try:
            url = f"{self.BASE_URL}/global"
            resp = _SESSION.get(url, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json().get("data", {})
            return {
                "total_market_cap": data.get("total_market_cap", {}).get("usd", 0),
                "total_volume": data.get("total_volume", {}).get("usd", 0),
                "market_cap_percentage": data.get("market_cap_percentage", {}),
                "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd", 0),
                "source": "coingecko",
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"CoinGecko 全球市场数据获取失败: {e}")
            return None


class ExternalDataManager:
    """外部数据管理器 — 统一入口

    整合所有外部数据源, 提供统一接口。
    自动缓存, 避免重复请求。
    """

    def __init__(self):
        self.fred = FREDApi()
        self.econdb = EcondbApi()
        self.treasury = FedTreasuryApi()
        self.alpha_vantage = AlphaVantageApi()
        self.finnhub = FinnhubApi()
        self.coingecko = CoinGeckoApi()

        logger.info(
            f"ExternalDataManager 初始化: "
            f"FRED={'✓' if self.fred.available else '✗'} "
            f"Econdb=✓ "
            f"Treasury=✓ "
            f"AlphaVantage={'✓' if self.alpha_vantage.available else '✗'} "
            f"Finnhub={'✓' if self.finnhub.available else '✗'} "
            f"CoinGecko=✓"
        )

    def _cache_path(self, category: str, key: str) -> Path:
        """获取缓存文件路径"""
        safe_key = key.replace("/", "_").replace("\\", "_")
        return CACHE_DIR / f"{category}_{safe_key}.json"

    def _load_cache(self, category: str, key: str) -> Optional[Any]:
        """加载缓存"""
        cache_file = self._cache_path(category, key)
        if not cache_file.exists():
            return None

        try:
            with _CACHE_LOCK:
                with open(cache_file, encoding="utf-8") as _f:
                    cache = json.load(_f)
            cache_time = cache.get("_cache_time", 0)
            ttl = CACHE_TTL.get(category, 300)
            if time.time() - cache_time < ttl:
                return cache.get("data")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            # P1-02 修复: 此前 except: pass 静默吞掉损坏缓存, 无法察觉
            logger.debug(f"缓存读取失败 ({cache_file.name}): {e}")
        return None

    def _save_cache(self, category: str, key: str, data: Any):
        """保存缓存 (P0-C1: 临时文件+os.replace 原子写, 防并发写坏缓存)"""
        cache_file = self._cache_path(category, key)
        try:
            cache = {
                "_cache_time": time.time(),
                "_cache_date": datetime.now().isoformat(),
                "data": data,
            }
            payload = json.dumps(cache, ensure_ascii=False, indent=2, default=str)
            with _CACHE_LOCK:
                # 复用统一原子写 (临时文件+os.replace+Windows共享违规重试)
                from utils.concurrency import atomic_write_text

                atomic_write_text(cache_file, payload)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # 缓存失败不阻断主流程
            logger.warning(f"缓存保存失败: {e}")

    def get_macro_snapshot(self) -> Dict[str, Any]:
        """获取宏观经济快照

        Returns:
            {
                "fred": {CPI/PPI/GDP/国债收益率...},
                "treasury": {3M/6M/1Y/2Y/5Y/10Y/30Y...},
                "coingecko_global": {加密货币市场情绪...},
            }
        """
        # 检查缓存
        cached = self._load_cache("macro", "snapshot")
        if cached:
            return cast(Dict, cached)
        snapshot: Dict[str, Any] = {}

        # FRED 宏观指标
        if self.fred.available:
            fred_data = self.fred.get_macro_snapshot()
            if fred_data:
                snapshot["fred"] = {k: v.to_dict() for k, v in fred_data.items()}

        # 国债收益率
        treasury_yields = self.treasury.get_treasury_yields()
        if treasury_yields:
            snapshot["treasury_yields"] = treasury_yields
        # 加密货币市场情绪 (风险偏好指标)
        crypto_global = self.coingecko.get_global_market()
        if crypto_global:
            snapshot["crypto_market"] = crypto_global

        if snapshot:
            self._save_cache("macro", "snapshot", snapshot)

        return snapshot

    def get_global_stock(self, symbol: str) -> Optional[Dict]:
        """获取全球股票行情

        优先级: Finnhub > Alpha Vantage

        Args:
            symbol: 股票代码 (如 "AAPL", "MSFT")
        """
        # 检查缓存
        cached = self._load_cache("stock", symbol)
        if cached:
            return cast(Dict, cached)
        quote = None

        # 优先级 1: Finnhub
        if self.finnhub.available:
            quote = self.finnhub.get_quote(symbol)

        # 优先级 2: Alpha Vantage
        if quote is None and self.alpha_vantage.available:
            quote = self.alpha_vantage.get_global_quote(symbol)

        if quote:
            self._save_cache("stock", symbol, quote)

        return quote

    def get_crypto_price(self, coin_id: str = "bitcoin") -> Optional[Dict]:
        """获取加密货币价格"""
        cached = self._load_cache("crypto", coin_id)
        if cached:
            return cast(Dict, cached)
        quote = self.coingecko.get_price(coin_id)
        if quote:
            self._save_cache("crypto", coin_id, quote)

        return quote

    def get_market_news(self) -> List[Dict]:
        """获取市场新闻"""
        cached = self._load_cache("news", "market")
        if cached:
            return cast(List[Dict], cached)
        news = self.finnhub.get_market_news("general") if self.finnhub.available else []

        if news:
            self._save_cache("news", "market", news)

        return news

    def get_risk_sentiment(self) -> Dict[str, Any]:
        """获取风险情绪指标

        Returns:
            {
                "vix_proxy": 加密货币市场变化率 (替代VIX),
                "treasury_yield_curve": 国债收益率曲线,
                "fed_rate": 联邦基金利率,
            }
        """
        snapshot = self.get_macro_snapshot()

        sentiment: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "vix_proxy": None,
            "treasury_yield_curve": {},
            "fed_rate": None,
        }

        # 加密货币市场变化 (风险情绪代理)
        crypto = snapshot.get("crypto_market", {})
        if crypto:
            sentiment["vix_proxy"] = crypto.get("market_cap_change_24h_pct", 0)

        # 国债收益率曲线
        treasury = snapshot.get("treasury_yields", {})
        if treasury:
            sentiment["treasury_yield_curve"] = treasury
            # 收益率曲线倒挂 (2Y > 10Y) 是衰退信号
            y2 = treasury.get("2Y", 0)
            y10 = treasury.get("10Y", 0)
            sentiment["yield_curve_inverted"] = y2 > y10

        # 联邦基金利率
        fred = snapshot.get("fred", {})
        if "FED_FUNDS_RATE" in fred:
            sentiment["fed_rate"] = fred["FED_FUNDS_RATE"].get("value")

        return sentiment


# 模块自检
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("外部数据源模块自检")
    logger.info("=" * 60)

    manager = ExternalDataManager()

    # 测试宏观经济快照
    logger.info("\n--- 宏观经济快照 ---")
    macro = manager.get_macro_snapshot()
    for source, data in macro.items():
        logger.info(f"\n[{source}]")
        if isinstance(data, dict):
            for key, val in list(data.items())[:5]:
                if isinstance(val, dict):
                    logger.info(f"  {key}: {val.get('value', val)}")
                else:
                    logger.info(f"  {key}: {val}")

    # 测试风险情绪
    logger.info("\n--- 风险情绪指标 ---")
    sentiment = manager.get_risk_sentiment()
    for key, val in sentiment.items():
        logger.info(f"  {key}: {val}")

    # 测试加密货币 (无需API Key)
    logger.info("\n--- 加密货币价格 ---")
    btc = manager.get_crypto_price("bitcoin")
    if btc:
        logger.info(f"  BTC: ${btc.get('price', 0):,.2f} ({btc.get('change_24h_pct', 0):.2f}%)")

    logger.info("\n✅ 自检完成")
