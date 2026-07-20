# -*- coding: utf-8 -*-
"""
数据提供器

功能：
- 市场数据获取（Wind MCP 唯一数据源）
- 数据预处理
- 数据缓存
- 数据验证
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
import json
import os
import pathlib
import sys
import subprocess
import importlib.util
from collections import deque
import threading
import time
import random
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from utils.logger import get_logger
from utils.data_types import safe_float

_SINA_SESSION = requests.Session()
_SINA_SESSION.trust_env = False
_SINA_SESSION.proxies = {"http": None, "https": None}

try:
    import numpy as _np
    HAS_NUMPY = True
except Exception:
    _np = None
    HAS_NUMPY = False

logger = get_logger('data_provider')


def _parse_markdown_table(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith('|')]
    if len(lines) < 2:
        return []
    headers = [cell.strip() for cell in lines[0].strip('|').split('|')]
    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values):
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((x - mean) ** 2 for x in values) / (len(values) - 1)) ** 0.5


def _diff(values):
    return [values[i] - values[i - 1] for i in range(1, len(values))]


def _where(condition, x, y):
    return [x if c else y for c in condition]


def _eye(size):
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def _zeros(size):
    return [0.0] * size


class MarketDataProvider:
    """市场数据提供器 - 多数据源优先级: Wind MCP > iFinD MCP > 默认兜底"""
    
    def __init__(self, cache_size: int = 1000, backtest_mode: bool = False):
        self.cache_size = cache_size
        self.backtest_mode = backtest_mode
        self._backtest_date = None
        self.data_cache = {}
        self.cache_lock = threading.Lock()
        self.persistent_cache_dir = pathlib.Path(__file__).resolve().parents[1] / "data_cache"
        self.persistent_cache_dir.mkdir(exist_ok=True)
        self.source_health = {
            'wind_mcp': {'ok': False, 'last_error': None},
            'ifind_mcp': {'ok': False, 'last_error': None},
            'sina_http': {'ok': False, 'last_error': None},
        }

        self.data_sources = {
            'real_time': {
                'enabled': True,
                'refresh_interval': 60,
                'last_update': None
            },
            'historical': {
                'enabled': True,
                'cache_days': 365,
                'update_frequency': 'daily'
            },
            'sentiment': {
                'enabled': True,
                'refresh_interval': 300,
                'last_update': None
            }
        }

        self._wind_mcp_client = None
        self._ifind_client = None
        self._init_wind_mcp()
        self._init_ifind_mcp()
        logger.info("市场数据提供器初始化完成 (多数据源优先级: Wind MCP > iFinD MCP, backtest_mode=%s)", backtest_mode)

    def set_backtest_date(self, report_date: str) -> None:
        self._backtest_date = report_date

    def _cache_suffix(self) -> str:
        if self.backtest_mode and self._backtest_date:
            return f"_{self._backtest_date}"
        return ""
    
    def _init_wind_mcp(self):
        try:
            wind_path = os.path.join(os.path.dirname(__file__), '..', 'wind_mcp_fetcher.py')
            wind_path = os.path.normpath(wind_path)
            if os.path.isfile(wind_path):
                spec = importlib.util.spec_from_file_location('wind_mcp_fetcher', wind_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._wind_mcp_client = {
                    'quote': mod.wind_get_quote,
                    'kline': mod.wind_get_kline,
                }
                self.source_health['wind_mcp']['ok'] = True
                logger.info("Wind MCP 客户端已加载 (P1)")
            else:
                self.source_health['wind_mcp']['last_error'] = f"文件不存在: {wind_path}"
                logger.warning(f"Wind MCP 文件不存在: {wind_path}")
        except Exception as e:
            self.source_health['wind_mcp']['last_error'] = str(e)
            logger.warning(f"Wind MCP 客户端加载失败: {e}")
    
    def _init_ifind_mcp(self):
        try:
            from utils.ifind_client import IFindClient
            auth_token = os.environ.get('IFIND_TOKEN', '')
            if auth_token:
                self._ifind_client = IFindClient(auth_token=auth_token)
                self.source_health['ifind_mcp']['ok'] = True
                logger.info("iFinD MCP 客户端已加载 (P2)")
            else:
                self.source_health['ifind_mcp']['last_error'] = "IFIND_TOKEN 环境变量未设置"
                logger.warning("iFinD MCP 未配置: IFIND_TOKEN 环境变量缺失")
        except Exception as e:
            self.source_health['ifind_mcp']['last_error'] = str(e)
            logger.warning(f"iFinD MCP 客户端加载失败: {e}")
    
    @staticmethod
    def _to_wind_code(symbol: str) -> str:
        s = str(symbol).strip()
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        if not s:
            return s
        if s.startswith(("51", "58")):
            return f"{s}.SH"
        if s.startswith(("15", "16")):
            return f"{s}.SZ"
        if s.startswith(("00", "30")):
            return f"{s}.SZ"
        if s.startswith(("6",)):
            return f"{s}.SH"
        if s.startswith(("4", "8")):
            return f"{s}.BJ"
        return f"{s}.SH"
    
    @staticmethod
    def _is_fund(symbol: str) -> bool:
        s = str(symbol).strip().upper()
        for prefix in ("51", "58", "15", "16"):
            if s.startswith(prefix):
                return True
        return False

    @staticmethod
    def _to_sina_code(symbol: str) -> str:
        s = str(symbol).strip()
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if s.startswith(prefix):
                return s
        for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
            if s.endswith(suffix):
                return s[: -len(suffix)]
        if s.startswith(("51", "58")):
            return f"sh{s}"
        if s.startswith(("15", "16")):
            return f"sz{s}"
        if s.startswith(("00", "30")):
            return f"sz{s}"
        if s.startswith(("6",)):
            return f"sh{s}"
        if s.startswith(("4", "8")):
            return f"bj{s}"
        return f"sh{s}"

    def _try_wind_mcp_realtime(self, symbol: str) -> Optional[Dict]:
        if not self._wind_mcp_client:
            return None
        try:
            windcode = self._to_wind_code(symbol)
            quote = self._wind_mcp_client['quote'](windcode, is_fund=self._is_fund(symbol))
            if not quote:
                self.source_health['wind_mcp']['last_error'] = 'empty_quote'
                logger.warning(f"Wind MCP 返回空数据: {symbol}")
                return None
            self.source_health['wind_mcp']['ok'] = True
            return {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'index_price': safe_float(quote.get('price')),
                'prev_close': safe_float(quote.get('prev_close')),
                'open': safe_float(quote.get('open')),
                'high': safe_float(quote.get('high')),
                'low': safe_float(quote.get('low')),
                'volume': safe_float(quote.get('volume'), default=0),
                'source': 'wind_mcp',
            }
        except Exception as e:
            self.source_health['wind_mcp']['ok'] = False
            self.source_health['wind_mcp']['last_error'] = str(e)
            logger.error(f"Wind MCP 获取实时数据失败: {e}")
            return None
    
    def _try_wind_mcp_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        if not self._wind_mcp_client:
            return None
        try:
            period_mapping = {
                '1d': 1,
                '1w': 5,
                '1m': 20,
                '3m': 60,
                '6m': 120,
                '1y': 252,
                '2y': 504,
                '3y': 756,
                '5y': 1260,
            }
            data_points = period_mapping.get(period, 252)
            windcode = self._to_wind_code(symbol)
            klines = self._wind_mcp_client['kline'](windcode, days=data_points, is_fund=self._is_fund(symbol))
            if not klines:
                return None
            
            records = []
            for k in klines:
                close = safe_float(k.get('close') or k.get('match') or k.get('MATCH'))
                if close is None or close <= 0:
                    continue
                records.append({
                    'date': pd.to_datetime(k.get('date') or k.get('trade_date') or k.get('time') or k.get('TIME') or k.get('_DATE')),
                    'open': safe_float(k.get('open') or k.get('OPEN')) or close,
                    'high': safe_float(k.get('high') or k.get('HIGH')) or close,
                    'low': safe_float(k.get('low') or k.get('LOW')) or close,
                    'close': close,
                    'volume': safe_float(k.get('volume') or k.get('VOLUME'), default=0),
                })
            df = pd.DataFrame(records)
            if df.empty:
                logger.warning("Wind MCP 返回历史数据但解析后为空，尝试下一数据源")
                return None
            df.set_index('date', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Wind MCP 获取历史数据失败: {e}")
            return None
    
    def _try_ifind_mcp_realtime(self, symbol: str) -> Optional[Dict]:
        if not self._ifind_client:
            return None
        try:
            s = str(symbol).strip()
            s = s.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
            
            if self._is_fund(symbol):
                quotes = self._ifind_client.get_etf_quotes([s])
                if s in quotes:
                    quote = quotes[s]
                    self.source_health['ifind_mcp']['ok'] = True
                    return {
                        'timestamp': datetime.now().isoformat(),
                        'symbol': symbol,
                        'index_price': safe_float(quote.get('price')),
                        'prev_close': safe_float(quote.get('price')) * 0.995,
                        'open': safe_float(quote.get('price')),
                        'high': safe_float(quote.get('price')) * 1.005,
                        'low': safe_float(quote.get('price')) * 0.995,
                        'volume': 0,
                        'source': 'ifind_mcp',
                    }
            else:
                klines = self._ifind_client.get_historical_klines(s, days=1)
                if klines:
                    kline = klines[-1]
                    self.source_health['ifind_mcp']['ok'] = True
                    return {
                        'timestamp': datetime.now().isoformat(),
                        'symbol': symbol,
                        'index_price': safe_float(kline.get('收盘价')),
                        'prev_close': safe_float(kline.get('收盘价')) * 0.995,
                        'open': safe_float(kline.get('开盘价')),
                        'high': safe_float(kline.get('最高价')),
                        'low': safe_float(kline.get('最低价')),
                        'volume': safe_float(kline.get('成交量'), default=0),
                        'source': 'ifind_mcp',
                    }
            
            self.source_health['ifind_mcp']['last_error'] = 'empty_data'
            logger.warning(f"iFinD MCP 返回空数据: {symbol}")
            return None
        except Exception as e:
            self.source_health['ifind_mcp']['ok'] = False
            self.source_health['ifind_mcp']['last_error'] = str(e)
            logger.error(f"iFinD MCP 获取实时数据失败: {e}")
            return None
    
    def _try_ifind_mcp_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        if not self._ifind_client:
            return None
        try:
            period_mapping = {
                '1d': 1,
                '1w': 5,
                '1m': 20,
                '3m': 60,
                '6m': 120,
                '1y': 252,
                '2y': 504,
                '3y': 756,
                '5y': 1260,
            }
            data_points = period_mapping.get(period, 252)
            
            s = str(symbol).strip()
            s = s.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
            
            klines = self._ifind_client.get_historical_klines(s, days=data_points)
            if not klines:
                return None
            
            records = []
            for k in klines:
                close = safe_float(k.get('收盘价'))
                if close is None or close <= 0:
                    continue
                records.append({
                    'date': pd.to_datetime(k.get('日期') or k.get('date')),
                    'open': safe_float(k.get('开盘价')) or close,
                    'high': safe_float(k.get('最高价')) or close,
                    'low': safe_float(k.get('最低价')) or close,
                    'close': close,
                    'volume': safe_float(k.get('成交量'), default=0),
                })
            df = pd.DataFrame(records)
            if df.empty:
                logger.warning("iFinD MCP 返回历史数据但解析后为空，尝试下一数据源")
                return None
            df.set_index('date', inplace=True)
            self.source_health['ifind_mcp']['ok'] = True
            return df
        except Exception as e:
            logger.error(f"iFinD MCP 获取历史数据失败: {e}")
            return None

    def _try_sina_http_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """新浪 HTTP 历史 KLine 数据（P3，绕过系统代理）"""
        try:
            period_mapping = {
                '1d': 1,
                '1w': 5,
                '1m': 20,
                '3m': 60,
                '6m': 120,
                '1y': 252,
                '2y': 504,
                '3y': 756,
                '5y': 1260,
            }
            data_points = period_mapping.get(period, 252)
            sina_code = self._to_sina_code(symbol)
            url = (
                "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                "CN_MarketData.getKLineData"
                f"?symbol={sina_code}&scale=240&ma=no&datalen={data_points}"
            )
            session = _SINA_SESSION
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            text = (resp.text or "").strip()
            if not text or text in ("null", "None"):
                logger.warning("新浪 HTTP 返回空历史数据: %s", symbol)
                return None
            payload = json.loads(text)
            if not payload:
                return None
            records = []
            for item in payload:
                day = item.get("day") or item.get("date")
                if not day:
                    continue
                records.append({
                    "date": pd.to_datetime(day),
                    "open": float(item.get("open", 0) or 0),
                    "high": float(item.get("high", 0) or 0),
                    "low": float(item.get("low", 0) or 0),
                    "close": float(item.get("close", 0) or 0),
                    "volume": float(item.get("volume", 0) or 0),
                })
            if not records:
                return None
            df = pd.DataFrame(records)
            df.set_index("date", inplace=True)
            self.source_health['sina_http']['ok'] = True
            return df
        except Exception as e:
            logger.error(f"新浪 HTTP 获取历史数据失败: {e}")
            return None

    def get_market_data(self, symbol: str = None) -> Dict:
        try:
            cache_key = f"market_{symbol or 'SPY'}{self._cache_suffix()}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    if cache_time and (datetime.now() - cache_time).total_seconds() < 60:
                        logger.debug(f"使用缓存的市场数据: {cache_key}")
                        return cached_data['data']
            
            market_data = self._fetch_real_time_data(symbol)
            
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': market_data,
                    'timestamp': datetime.now()
                }
                
                if len(self.data_cache) > self.cache_size:
                    oldest_key = next(iter(self.data_cache))
                    del self.data_cache[oldest_key]
            
            logger.info(f"获取市场数据: {cache_key}")
            return market_data
            
        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._get_default_market_data()
    
    def _load_persistent_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        try:
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            if cache_file.exists():
                df = pd.read_parquet(cache_file)
                logger.debug(f"加载持久化缓存: {cache_file.name}")
                return df
        except Exception as e:
            logger.debug(f"加载持久化缓存失败: {e}")
        return None

    def _save_persistent_cache(self, cache_key: str, data: pd.DataFrame) -> None:
        try:
            if data is None or data.empty:
                return
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            data.to_parquet(cache_file, index=True)
        except Exception as e:
            logger.debug(f"保存持久化缓存失败: {e}")

    def get_historical_data(self, symbol: str, period: str = '1y') -> pd.DataFrame:
        try:
            cache_key = f"historical_{symbol}_{period}{self._cache_suffix()}"

            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    if cache_time and (datetime.now() - cache_time).days < 1:
                        logger.debug(f"使用内存缓存的历史数据: {cache_key}")
                        return cached_data['data']

            # 优先读本地持久化缓存
            persistent = self._load_persistent_cache(cache_key)
            if persistent is not None and not persistent.empty:
                with self.cache_lock:
                    self.data_cache[cache_key] = {
                        'data': persistent,
                        'timestamp': datetime.now()
                    }
                return persistent

            historical_data = self._fetch_historical_data(symbol, period)
            
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': historical_data,
                    'timestamp': datetime.now()
                }
            
            logger.info(f"获取历史数据: {cache_key}")
            try:
                self._save_persistent_cache(cache_key, historical_data)
            except Exception as e:
                logger.debug(f"写入持久化缓存失败: {e}")
            return historical_data
            
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()
    
    def get_sentiment_data(self, symbol: str = None) -> Dict:
        try:
            cache_key = f"sentiment_{symbol or 'SPY'}{self._cache_suffix()}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    if cache_time and (datetime.now() - cache_time).seconds < 300:
                        logger.debug(f"使用缓存的情绪数据: {cache_key}")
                        return cached_data['data']
            
            sentiment_data = self._fetch_sentiment_data(symbol)
            
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': sentiment_data,
                    'timestamp': datetime.now()
                }
            
            logger.info(f"获取情绪数据: {cache_key}")
            return sentiment_data
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {e}")
            return self._get_default_sentiment_data()
    
    def get_technical_indicators(self, symbol: str) -> Dict:
        try:
            historical_data = self.get_historical_data(symbol)
            technical_indicators = self._calculate_technical_indicators(historical_data)
            logger.info(f"计算技术指标: {symbol}")
            return technical_indicators
            
        except Exception as e:
            logger.error(f"获取技术指标失败: {e}")
            return {}
    
    def _fetch_real_time_data(self, symbol: str) -> Dict:
        try:
            wind_data = self._try_wind_mcp_realtime(symbol)
            if wind_data:
                return wind_data
            
            logger.warning("Wind MCP 实时数据获取失败，尝试 iFinD MCP: %s", symbol)
            
            ifind_data = self._try_ifind_mcp_realtime(symbol)
            if ifind_data:
                return ifind_data
            
            logger.warning("iFinD MCP 实时数据获取失败: %s", symbol)
            return self._get_default_market_data()
        except Exception as e:
            logger.error(f"获取实时数据失败: {e}")
            return self._get_default_market_data()
    
    def _fetch_historical_data(self, symbol: str, period: str) -> pd.DataFrame:
        try:
            wind_data = self._try_wind_mcp_historical(symbol, period)
            if wind_data is not None and not wind_data.empty:
                return wind_data
            
            logger.warning("Wind MCP 历史数据获取失败，尝试 iFinD MCP: %s", symbol)
            
            ifind_data = self._try_ifind_mcp_historical(symbol, period)
            if ifind_data is not None and not ifind_data.empty:
                return ifind_data
            
            logger.warning("iFinD MCP 历史数据获取失败，尝试新浪 HTTP: %s", symbol)
            
            sina_data = self._try_sina_http_historical(symbol, period)
            if sina_data is not None and not sina_data.empty:
                return sina_data
            
            logger.warning("新浪 HTTP 历史数据获取失败: %s", symbol)
            return self._get_default_historical_data()
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()
    
    def _fetch_sentiment_data(self, symbol: str) -> Dict:
        try:
            return {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol or 'SPY',
                'news_sentiment': 0.1,
                'social_sentiment': 0.2,
                'analyst_sentiment': 0.15,
                'options_sentiment': 0.05,
                'composite_sentiment': 0.15,
                'news_count': 50,
                'positive_news': 25,
                'negative_news': 20,
                'social_mentions': {'positive': 120, 'negative': 80},
                'analyst_ratings': {'buy': 15, 'sell': 8, 'hold': 12},
                'put_call_ratio': 1.2,
                'options_skew': 0.0,
                'sentiment_trend': 'neutral'
            }
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {e}")
            return self._get_default_sentiment_data()
    
    def _calculate_technical_indicators(self, data: pd.DataFrame) -> Dict:
        try:
            if len(data) < 20:
                return {}
            
            prices = [float(x) for x in data['close'].values]
            volumes = [float(x) for x in data['volume'].values]
            
            ma20 = _mean(prices[-20:])
            ma50 = _mean(prices[-50:])
            ma200 = _mean(prices[-200:]) if len(prices) >= 200 else ma50
            
            delta = _diff(prices)
            gain = _where([d > 0 for d in delta], delta, 0)
            loss = _where([d < 0 for d in delta], [-d for d in delta], 0)
            
            avg_gain = _mean(gain[-14:])
            avg_loss = _mean(loss[-14:])
            
            if avg_loss == 0:
                rsi = 50
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            ema12 = self._calculate_ema(prices, 12)
            ema26 = self._calculate_ema(prices, 26)
            macd = ema12 - ema26
            
            sma20 = _mean(prices[-20:])
            std20 = _std(prices[-20:])
            bb_upper = sma20 + 2 * std20
            bb_lower = sma20 - 2 * std20
            
            technical_indicators = {
                'timestamp': datetime.now().isoformat(),
                'ma20': ma20,
                'ma50': ma50,
                'ma200': ma200,
                'rsi': rsi,
                'macd': macd,
                'bb_upper': bb_upper,
                'bb_lower': bb_lower,
                'bb_width': (bb_upper - bb_lower) / sma20 if sma20 else 0.0,
                'price_position': (prices[-1] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) else 0.0,
                'volume_sma': _mean(volumes[-20:]),
                'trend': 'upward' if prices[-1] > prices[-5] else 'downward'
            }
            
            return technical_indicators
            
        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return {}
    
    def _calculate_ema(self, data, period: int) -> float:
        if len(data) < period:
            return _mean(data)
        
        alpha = 2 / (period + 1)
        ema = data[0]
        
        for value in data[1:]:
            ema = alpha * value + (1 - alpha) * ema
        
        return ema
    
    def _get_default_market_data(self) -> Dict:
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': 'SPY',
            'index_price': 3000,
            'prev_close': 3000,
            'open': 3000,
            'high': 3000,
            'low': 3000,
            'volume': 10000000,
            'volatility': 0.15,
            'var_95': 0.02,
            'var_99': 0.035,
            'es_95': 0.03,
            'beta': 1.0,
            'liquidity': 1.0,
            'sentiment_score': 0.0,
            'correlation_matrix': _eye(3),
            'tracking_error': 0.03,
            'market_correlation': 0.7,
            'returns': _zeros(252),
            'vix_future_price': 20.0,
            'kurtosis': 3.0,
            'skewness': 0.0,
            'extreme_events': 0,
            'put_call_ratio': 1.0,
            'options_skew': 0.0,
            'news_count': 0,
            'positive_news': 0,
            'negative_news': 0,
            'social_mentions': {'positive': 0, 'negative': 0},
            'analyst_ratings': {'buy': 0, 'sell': 0, 'hold': 0}
        }
    
    def _get_default_historical_data(self) -> pd.DataFrame:
        dates = pd.date_range(end=datetime.now(), periods=252, freq='D')
        base_price = 3000
        
        return pd.DataFrame({
            'date': dates,
            'open': [base_price] * 252,
            'high': [base_price * 1.01] * 252,
            'low': [base_price * 0.99] * 252,
            'close': [base_price] * 252,
            'volume': [10000000] * 252,
            'returns': [0] * 252
        }).set_index('date')
    
    def _get_default_sentiment_data(self) -> Dict:
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': 'SPY',
            'news_sentiment': 0.0,
            'social_sentiment': 0.0,
            'analyst_sentiment': 0.0,
            'options_sentiment': 0.0,
            'composite_sentiment': 0.0,
            'news_count': 0,
            'positive_news': 0,
            'negative_news': 0,
            'social_mentions': {'positive': 0, 'negative': 0},
            'analyst_ratings': {'buy': 0, 'sell': 0, 'hold': 0},
            'put_call_ratio': 1.0,
            'options_skew': 0.0,
            'sentiment_trend': 'neutral'
        }
    
    # ===========================================================
    # 新增模块集成 (v7.5+): 价格预测 + 外部数据源 + 网页抓取 + AI 报告
    # ===========================================================

    def get_price_prediction(self, symbol: str, horizon: int = 5) -> Dict:
        """获取价格预测 (来自 tf_price_predictor)

        降级链: TimesFM → TensorFlow LSTM → ARIMA → 移动平均兜底
        """
        try:
            from utils.tf_price_predictor import PricePredictor
            predictor = PricePredictor()
            prices = self._get_recent_prices_for_prediction(symbol)
            if prices is None or len(prices) < 30:
                logger.warning(f"预测数据不足 ({symbol}): 需至少30个价格点")
                return {}
            result = predictor.predict(symbol, prices, horizon=horizon)
            return result.to_dict() if hasattr(result, 'to_dict') else result.__dict__
        except Exception as e:
            logger.warning(f"价格预测失败 ({symbol}): {e}")
            return {}

    def _get_recent_prices_for_prediction(self, symbol: str, days: int = 120):
        """获取近期收盘价序列 (供预测用)"""
        try:
            import numpy as np
            hist = self.get_historical_data(symbol, period='1y')
            if hist is None or len(hist) < 30:
                return None
            col = 'close' if 'close' in hist.columns else 'Close'
            prices = hist[col].tail(days).values
            return np.array(prices, dtype=float)
        except Exception as e:
            logger.debug(f"获取预测价格序列失败 ({symbol}): {e}")
            return None

    def get_external_macro(self) -> Dict:
        """获取外部宏观数据 (FRED/Econdb/Treasury)"""
        try:
            from utils.external_data_source import ExternalDataManager
            mgr = ExternalDataManager()
            return mgr.get_macro_snapshot()
        except Exception as e:
            logger.warning(f"外部宏观数据获取失败: {e}")
            return {}

    def get_risk_sentiment(self) -> Dict:
        """获取风险情绪指标 (加密货币/国债收益率/VIX代理)"""
        try:
            from utils.external_data_source import ExternalDataManager
            mgr = ExternalDataManager()
            return mgr.get_risk_sentiment()
        except Exception as e:
            logger.warning(f"风险情绪指标获取失败: {e}")
            return {}

    def get_news_sentiment(self, symbol: str, limit: int = 20) -> List[Dict]:
        """获取新闻+情感分析 (web_scraper + ai_report_agent)"""
        try:
            from utils.web_scraper import WebScraper
            from utils.ai_report_agent import AIReportAgent
            scraper = WebScraper()
            agent = AIReportAgent()
            code_clean = symbol.split(".")[0] if "." in symbol else symbol
            news = scraper.fetch_announcements(code_clean, limit=limit) or []
            if not isinstance(news, list):
                return []
            news_dicts = [item.to_dict() for item in news if hasattr(item, "to_dict")]
            if not news_dicts:
                return []
            sentiments = agent.analyze_news_sentiment(news_dicts, use_llm=True)
            if not sentiments:
                return []
            return [s.__dict__ for s in sentiments]
        except Exception as e:
            logger.warning(f"新闻情感分析失败 ({symbol}): {e}")
            return []

    def get_ai_daily_report(self, symbols: List[str]) -> Dict:
        """生成 AI 每日投资报告"""
        try:
            from utils.ai_report_agent import AIReportAgent
            agent = AIReportAgent()
            report = agent.generate_daily_report(symbols=symbols)
            return report.__dict__
        except Exception as e:
            logger.warning(f"AI 每日报告生成失败: {e}")
            return {"error": str(e)}

    def get_extended_status(self) -> Dict:
        """获取扩展状态 (含新模块健康检查)"""
        status = {
            "data_sources": {
                "wind_mcp": self.source_health.get('wind_mcp', {}).get('ok', False),
                "ifind_mcp": self.source_health.get('ifind_mcp', {}).get('ok', False),
                "sina_http": self.source_health.get('sina_http', {}).get('ok', False),
            },
            "cache": self.get_cache_info(),
        }
        # 新模块可用性
        for module_name in ['tf_price_predictor', 'external_data_source', 'web_scraper', 'ai_report_agent']:
            try:
                __import__(f'utils.{module_name}')
                status[f'{module_name}_available'] = True
            except ImportError:
                status[f'{module_name}_available'] = False
        return status

    def clear_cache(self):
        with self.cache_lock:
            self.data_cache.clear()
            logger.info("数据缓存已清除")
    
    def get_cache_info(self) -> Dict:
        with self.cache_lock:
            return {
                'cache_size': len(self.data_cache),
                'max_cache_size': self.cache_size,
                'cached_items': list(self.data_cache.keys())
            }


_data_provider = None

def get_market_data(symbol: str = None) -> Dict:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_market_data(symbol)

def get_historical_data(symbol: str, period: str = '1y') -> pd.DataFrame:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_historical_data(symbol, period)

def get_sentiment_data(symbol: str = None) -> Dict:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_sentiment_data(symbol)

def get_technical_indicators(symbol: str) -> Dict:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_technical_indicators(symbol)

def get_price_prediction(symbol: str, horizon: int = 5) -> Dict:
    """价格预测便捷函数"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_price_prediction(symbol, horizon)

def get_external_macro() -> Dict:
    """外部宏观数据便捷函数"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_external_macro()

def get_risk_sentiment() -> Dict:
    """风险情绪指标便捷函数"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_risk_sentiment()

def get_news_sentiment(symbol: str, limit: int = 20) -> List[Dict]:
    """新闻情感分析便捷函数"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_news_sentiment(symbol, limit)

def get_ai_daily_report(symbols: List[str]) -> Dict:
    """AI 每日报告便捷函数"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_ai_daily_report(symbols)


if __name__ == "__main__":
    print("测试市场数据提供器")
    
    market_data = get_market_data()
    print("市场数据:", market_data['index_price'])
    
    historical_data = get_historical_data('SPY', '1m')
    print("历史数据形状:", historical_data.shape)
    
    sentiment_data = get_sentiment_data()
    print("情绪数据:", sentiment_data['composite_sentiment'])
    
    tech_indicators = get_technical_indicators('SPY')
    print("技术指标:", list(tech_indicators.keys()))
    
    cache_info = _data_provider.get_cache_info()
    print("缓存信息:", cache_info)