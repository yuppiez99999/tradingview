"""
数据提供器

功能：
- 市场数据获取（Wind MCP 唯一数据源）
- 数据预处理
- 数据缓存
- 数据验证
"""

import importlib.util
import json
import logging
import pathlib
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, cast

import pandas as pd

from utils.data_types import safe_float

# B-4.1: 统一无代理 Session 工厂 (绕过系统代理, 避免国内金融 API 被拦截)
from utils.http_session import make_no_proxy_session

# P1-2: 移除全局禁用TLS验证，改为默认启用证书校验
from utils.logger import get_logger

logger: Any = logging.getLogger(__name__)

_SINA_SESSION = make_no_proxy_session("sina")

_np: Optional[Any] = None
try:
    import numpy as _np_module

    _np = _np_module
    HAS_NUMPY = True
except (ImportError, AttributeError):
    HAS_NUMPY = False

logger = get_logger("data_provider")


# ===========================================================
# 类型声明 (TypedDict) — 消除 source_health 嵌套字典的 type:ignore
# ===========================================================
class SourceHealthEntry(TypedDict):
    """单数据源健康状态条目"""

    ok: bool
    last_error: Optional[str]


class SourceHealth(TypedDict):
    """四数据源健康状态汇总 (wind_mcp / tdx / akshare / sina_http)"""

    wind_mcp: SourceHealthEntry
    tdx: SourceHealthEntry
    akshare: SourceHealthEntry
    sina_http: SourceHealthEntry


def _parse_markdown_table(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return float((sum((x - mean) ** 2 for x in values) / (len(values) - 1)) ** 0.5)


def _diff(values: List[float]) -> List[float]:
    return [values[i] - values[i - 1] for i in range(1, len(values))]


def _where(condition, x, y):
    """条件选择器 (元素级)

    注: x/y 既支持标量也支持列表, 保持无类型注解以兼容现有调用模式
    """
    if hasattr(x, '__iter__') and not isinstance(x, (str, bytes)):
        return [x_i if c else y for c, x_i in zip(condition, x)]
    return [x if c else y for c in condition]


def _eye(size):
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def _zeros(size):
    return [0.0] * size


class MarketDataProvider:
    """市场数据提供器 - 多数据源优先级: Wind MCP > 通达信 > AKShare > 新浪财经 (已剔除 iFinD)"""

    def __init__(self, cache_size: int = 1000, backtest_mode: bool = False):
        self.cache_size = cache_size
        self.backtest_mode = backtest_mode
        self._backtest_date: Optional[str] = None
        self.data_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        self.persistent_cache_dir = pathlib.Path(__file__).resolve().parents[1] / "data_cache"
        self.persistent_cache_dir.mkdir(exist_ok=True)
        self.source_health: SourceHealth = {
            "wind_mcp": {"ok": False, "last_error": None},
            "tdx": {"ok": False, "last_error": None},
            "akshare": {"ok": False, "last_error": None},
            "sina_http": {"ok": False, "last_error": None},
        }

        self.data_sources: Dict[str, Any] = {
            "real_time": {"enabled": True, "refresh_interval": 60, "last_update": None},
            "historical": {"enabled": True, "cache_days": 365, "update_frequency": "daily"},
            "sentiment": {"enabled": True, "refresh_interval": 300, "last_update": None},
        }

        self._wind_mcp_client: Optional[Dict[str, Any]] = None
        self._tdx_source: Optional[Any] = None
        self._akshare_source: Optional[Any] = None
        self._init_wind_mcp()
        self._init_tdx()
        self._init_akshare()
        logger.info(
            "市场数据提供器初始化完成 (多数据源优先级: Wind MCP > 通达信 > AKShare > 新浪财经, 已剔除 iFinD, backtest_mode=%s)",
            backtest_mode,
        )

    def set_backtest_date(self, report_date: str) -> None:
        self._backtest_date = report_date

    def _cache_suffix(self) -> str:
        if self.backtest_mode and self._backtest_date:
            return f"_{self._backtest_date}"
        return ""

    def _init_wind_mcp(self):
        """初始化 Wind MCP 客户端

        路径搜索优先级 (修复 v8.6.11: 原仅查项目根目录, 漏掉 tools/ 子目录):
            1. 项目根目录/wind_mcp_fetcher.py
            2. 项目根目录/tools/wind_mcp_fetcher.py  ★ 实际位置
            3. utils/wind_mcp_fetcher.py
            4. v8.3_institutional/src/bridges/wind_mcp.py (备选)
        """
        # 候选路径列表 (按优先级排序)
        _project_root = pathlib.Path(__file__).resolve().parents[1]
        candidate_paths = [
            _project_root / "wind_mcp_fetcher.py",                      # 项目根目录
            _project_root / "tools" / "wind_mcp_fetcher.py",            # ★ 实际位置
            _project_root / "utils" / "wind_mcp_fetcher.py",            # utils 目录
            _project_root / "v8.3_institutional" / "src" / "bridges" / "wind_mcp.py",  # 备选
        ]

        wind_path = None
        for candidate in candidate_paths:
            if candidate.is_file():
                wind_path = candidate
                break

        if wind_path is None:
            self.source_health["wind_mcp"]["last_error"] = (
                f"文件不存在, 已尝试 {len(candidate_paths)} 个候选路径: " +
                ", ".join(str(p) for p in candidate_paths)
            )
            logger.warning(f"Wind MCP 文件不存在, 已尝试 {len(candidate_paths)} 个候选路径")
            return

        try:
            spec = importlib.util.spec_from_file_location("wind_mcp_fetcher", str(wind_path))
            if spec is None or spec.loader is None:
                self.source_health["wind_mcp"]["last_error"] = f"无法创建 importlib spec: {wind_path}"
                logger.warning(f"Wind MCP importlib spec 创建失败: {wind_path}")
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # 校验必要函数是否存在
            if not (hasattr(mod, "wind_get_quote") and hasattr(mod, "wind_get_kline")):
                self.source_health["wind_mcp"]["last_error"] = (
                    f"模块缺少 wind_get_quote 或 wind_get_kline 函数: {wind_path}"
                )
                logger.warning(f"Wind MCP 模块缺少必要函数: {wind_path}")
                return

            self._wind_mcp_client = {
                "quote": mod.wind_get_quote,
                "kline": mod.wind_get_kline,
            }
            self.source_health["wind_mcp"]["ok"] = True
            logger.info(f"Wind MCP 客户端已加载 (P1, path={wind_path})")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["wind_mcp"]["last_error"] = str(e)
            logger.warning(f"Wind MCP 客户端加载失败 ({wind_path}): {e}")

    def _init_tdx(self):
        """初始化通达信数据源"""
        try:
            from utils.tdx_data_source import get_tdx_source

            self._tdx_source = get_tdx_source()
            if self._tdx_source and self._tdx_source.source_health.get("tdx", {}).get("ok"):
                self.source_health["tdx"]["ok"] = True
                logger.info("通达信数据源已加载 (P3)")
            else:
                self.source_health["tdx"]["last_error"] = "通达信连接初始化失败"
                logger.warning("通达信数据源初始化失败")
        except ImportError as e:
            self.source_health["tdx"]["last_error"] = f"模块导入失败: {e}"
            logger.warning(f"通达信数据源模块导入失败: {e}")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["tdx"]["last_error"] = str(e)
            logger.warning(f"通达信数据源初始化失败: {e}")

    def _init_akshare(self):
        """初始化 AKShare 数据源"""
        try:
            from utils.akshare_data_source import get_akshare_source

            self._akshare_source = get_akshare_source()
            if self._akshare_source and self._akshare_source.source_health.get("akshare", {}).get("ok"):
                self.source_health["akshare"]["ok"] = True
                logger.info("AKShare 数据源已加载 (P4)")
            else:
                self.source_health["akshare"]["last_error"] = "AKShare 初始化失败"
                logger.warning("AKShare 数据源初始化失败")
        except ImportError as e:
            self.source_health["akshare"]["last_error"] = f"模块导入失败: {e}"
            logger.warning(f"AKShare 数据源模块导入失败: {e}")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["akshare"]["last_error"] = str(e)
            logger.warning(f"AKShare 数据源初始化失败: {e}")

    @staticmethod
    def _to_wind_code(symbol: str) -> str:
        s = str(symbol).strip()
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if s.startswith(prefix):
                s = s[len(prefix) :]
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
            quote = self._wind_mcp_client["quote"](windcode, is_fund=self._is_fund(symbol))
            if not quote:
                self.source_health["wind_mcp"]["last_error"] = "empty_quote"
                logger.warning(f"Wind MCP 返回空数据: {symbol}")
                return None
            self.source_health["wind_mcp"]["ok"] = True
            return {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "index_price": safe_float(quote.get("price")),
                "prev_close": safe_float(quote.get("prev_close")),
                "open": safe_float(quote.get("open")),
                "high": safe_float(quote.get("high")),
                "low": safe_float(quote.get("low")),
                "volume": safe_float(quote.get("volume"), default=0),
                "adjust": "none",  # P2-1: 实时行情统一未复权(实盘成交基准)
                "source": "wind_mcp",
            }
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["wind_mcp"]["ok"] = False
            self.source_health["wind_mcp"]["last_error"] = str(e)
            logger.error(f"Wind MCP 获取实时数据失败: {e}")
            return None

    def _try_wind_mcp_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        if not self._wind_mcp_client:
            return None
        try:
            period_mapping = {
                "1d": 252,
                "1w": 5,
                "1m": 20,
                "3m": 60,
                "6m": 120,
                "1y": 252,
                "2y": 504,
                "3y": 756,
                "5y": 1260,
            }
            data_points = period_mapping.get(period, 252)
            windcode = self._to_wind_code(symbol)
            klines = self._wind_mcp_client["kline"](windcode, days=data_points, is_fund=self._is_fund(symbol))
            if not klines:
                return None

            records = []
            for k in klines:
                close = safe_float(k.get("close") or k.get("match") or k.get("MATCH"))
                if close is None or close <= 0:
                    continue
                records.append(
                    {
                        "date": pd.to_datetime(
                            k.get("date") or k.get("trade_date") or k.get("time") or k.get("TIME") or k.get("_DATE")
                        ),
                        "open": safe_float(k.get("open") or k.get("OPEN")) or close,
                        "high": safe_float(k.get("high") or k.get("HIGH")) or close,
                        "low": safe_float(k.get("low") or k.get("LOW")) or close,
                        "close": close,
                        "volume": safe_float(k.get("volume") or k.get("VOLUME"), default=0),
                    }
                )
            df = pd.DataFrame(records)
            if df.empty:
                logger.warning("Wind MCP 返回历史数据但解析后为空，尝试下一数据源")
                return None
            df.set_index("date", inplace=True)
            return df
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"Wind MCP 获取历史数据失败: {e}")
            return None

    def _try_tdx_realtime(self, symbol: str) -> Optional[Dict]:
        """通达信实时数据 (P3)"""
        if not self._tdx_source:
            return None
        try:
            quote = self._tdx_source.get_realtime_quote(symbol)
            if not quote:
                self.source_health["tdx"]["last_error"] = "empty_quote"
                logger.warning(f"通达信返回空数据: {symbol}")
                return None
            self.source_health["tdx"]["ok"] = True
            return {
                "timestamp": quote.get("timestamp", datetime.now().isoformat()),
                "symbol": symbol,
                "index_price": safe_float(quote.get("index_price")),
                "prev_close": safe_float(quote.get("prev_close")),
                "open": safe_float(quote.get("open")),
                "high": safe_float(quote.get("high")),
                "low": safe_float(quote.get("low")),
                "volume": safe_float(quote.get("volume"), default=0),
                "adjust": "none",  # P2-1: 实时行情统一未复权(实盘成交基准)
                "source": "tdx",
            }
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["tdx"]["ok"] = False
            self.source_health["tdx"]["last_error"] = str(e)
            logger.error(f"通达信获取实时数据失败: {e}")
            return None

    def _try_tdx_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """通达信历史K线数据 (P3)"""
        if not self._tdx_source:
            return None
        try:
            period_mapping = {
                "1d": "1d",
                "1w": "1w",
                "1m": "1m",
                "3m": "1m",
                "6m": "1m",
                "1y": "1d",
                "2y": "1d",
                "3y": "1d",
                "5y": "1d",
            }
            tdx_period = period_mapping.get(period, "1d")
            count_map = {
                "1d": 252,
                "1w": 120,
                "1m": 60,
                "3m": 90,
                "6m": 180,
                "1y": 252,
                "2y": 504,
                "3y": 756,
                "5y": 1260,
            }
            count = count_map.get(period, 252)

            df = self._tdx_source.get_historical_klines(symbol, period=tdx_period, count=count)
            if df is None or df.empty:
                logger.warning("通达信返回历史数据为空，尝试下一数据源")
                return None

            self.source_health["tdx"]["ok"] = True
            return df
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"通达信获取历史数据失败: {e}")
            return None

    def _try_akshare_realtime(self, symbol: str) -> Optional[Dict]:
        """AKShare 实时数据 (P4)"""
        if not self._akshare_source:
            return None
        try:
            quote = self._akshare_source.get_realtime_quote(symbol)
            if not quote:
                self.source_health["akshare"]["last_error"] = "empty_quote"
                logger.warning(f"AKShare 返回空数据: {symbol}")
                return None
            self.source_health["akshare"]["ok"] = True
            return {
                "timestamp": quote.get("timestamp", datetime.now().isoformat()),
                "symbol": symbol,
                "index_price": safe_float(quote.get("index_price")),
                "prev_close": safe_float(quote.get("prev_close")),
                "open": safe_float(quote.get("open")),
                "high": safe_float(quote.get("high")),
                "low": safe_float(quote.get("low")),
                "volume": safe_float(quote.get("volume"), default=0),
                "adjust": "none",  # P2-1: 实时行情统一未复权(实盘成交基准)
                "source": "akshare",
            }
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["akshare"]["ok"] = False
            self.source_health["akshare"]["last_error"] = str(e)
            logger.error(f"AKShare 获取实时数据失败: {e}")
            return None

    def _try_akshare_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """AKShare 历史K线数据 (P4)"""
        if not self._akshare_source:
            return None
        try:
            period_mapping = {
                "1d": "1d",
                "1w": "1w",
                "1m": "1m",
                "3m": "1m",
                "6m": "1m",
                "1y": "1d",
                "2y": "1d",
                "3y": "1d",
                "5y": "1d",
            }
            ak_period = period_mapping.get(period, "1d")
            count_map = {
                "1d": 252,
                "1w": 120,
                "1m": 60,
                "3m": 90,
                "6m": 180,
                "1y": 252,
                "2y": 504,
                "3y": 756,
                "5y": 1260,
            }
            count = count_map.get(period, 252)

            df = self._akshare_source.get_historical_klines(symbol, period=ak_period, count=count)
            if df is None or df.empty:
                logger.warning("AKShare 返回历史数据为空，尝试下一数据源")
                return None

            self.source_health["akshare"]["ok"] = True
            return df
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"AKShare 获取历史数据失败: {e}")
            return None

    def _try_sina_http_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """新浪 HTTP 历史 KLine 数据（P3，绕过系统代理）"""
        try:
            period_mapping = {
                "1d": 252,
                "1w": 5,
                "1m": 20,
                "3m": 60,
                "6m": 120,
                "1y": 252,
                "2y": 504,
                "3y": 756,
                "5y": 1260,
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
                records.append(
                    {
                        "date": pd.to_datetime(day),
                        "open": float(item.get("open", 0) or 0),
                        "high": float(item.get("high", 0) or 0),
                        "low": float(item.get("low", 0) or 0),
                        "close": float(item.get("close", 0) or 0),
                        "volume": float(item.get("volume", 0) or 0),
                    }
                )
            if not records:
                return None
            df = pd.DataFrame(records)
            df.set_index("date", inplace=True)
            self.source_health["sina_http"]["ok"] = True
            return df
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"新浪 HTTP 获取历史数据失败: {e}")
            return None

    def _try_sina_http_realtime(self, symbol: str) -> Optional[Dict]:
        """新浪财经实时行情（P4，绕过系统代理）。

        接口: https://hq.sinajs.cn/list={sina_code}
        复用 _SINA_SESSION (trust_env=False, 无代理) 避免系统代理干扰。
        """
        try:
            sina_code = self._to_sina_code(symbol)
            url = f"https://hq.sinajs.cn/list={sina_code}"
            headers = {"Referer": "https://finance.sina.com.cn"}
            resp = _SINA_SESSION.get(url, headers=headers, timeout=10)
            resp.encoding = "gbk"
            text = (resp.text or "").strip()
            # 格式: var hq_str_sh688041="名称,今开,昨收,当前价,...";
            import re

            match = re.search(r'"(.*)"', text)
            if not match:
                self.source_health["sina_http"]["last_error"] = "empty_payload"
                logger.warning("新浪实时行情返回空: %s", symbol)
                return None
            fields = match.group(1).split(",")
            if len(fields) < 10:
                self.source_health["sina_http"]["last_error"] = "insufficient_fields"
                logger.warning("新浪实时行情字段不足: %s (got %d)", symbol, len(fields))
                return None

            def _safe_float(val, default=0.0):
                try:
                    v = float(val or 0)
                    return v if v == v else default  # NaN check
                except (ValueError, TypeError):
                    return default

            name = fields[0]
            open_price = _safe_float(fields[1])
            prev_close = _safe_float(fields[2])
            price = _safe_float(fields[3])
            high = _safe_float(fields[4])
            low = _safe_float(fields[5])
            volume = _safe_float(fields[8], default=0)
            # 成交额 fields[9]（元），保留但不返回（与 Wind MCP 格式一致）

            if price <= 0 and open_price <= 0:
                self.source_health["sina_http"]["last_error"] = "zero_price"
                logger.warning("新浪实时行情价格为 0: %s", symbol)
                return None

            self.source_health["sina_http"]["ok"] = True
            self.source_health["sina_http"]["last_error"] = None
            return {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "name": name,
                "index_price": price,
                "prev_close": prev_close,
                "open": open_price,
                "high": high,
                "low": low,
                "volume": volume,
                "adjust": "none",  # P2-1: 实时行情统一未复权(实盘成交基准)
                "source": "sina_http",
            }
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            self.source_health["sina_http"]["ok"] = False
            self.source_health["sina_http"]["last_error"] = str(e)
            logger.error(f"新浪 HTTP 获取实时行情失败: {e}")
            return None

    def get_market_data(self, symbol: Optional[str] = None) -> Dict:
        cache_key = f"market_{symbol or 'SPY'}{self._cache_suffix()}"

        with self.cache_lock:
            if cache_key in self.data_cache:
                cached_data = self.data_cache[cache_key]
                cache_time = cached_data.get("timestamp")

                if cache_time and (datetime.now() - cache_time).total_seconds() < 60:
                    logger.debug(f"使用缓存的市场数据: {cache_key}")
                    return cast(Dict, cached_data["data"])

        try:
            market_data = self._fetch_real_time_data(symbol or "SPY")

            with self.cache_lock:
                self.data_cache[cache_key] = {"data": market_data, "timestamp": datetime.now()}

                if len(self.data_cache) > self.cache_size:
                    oldest_key = next(iter(self.data_cache))
                    del self.data_cache[oldest_key]

            logger.info(f"获取市场数据: {cache_key}")
            return market_data

        except RuntimeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"获取市场数据失败: {e}")
            raise RuntimeError(f"获取市场数据失败 ({symbol}): {e}") from e

    def _load_persistent_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        """加载持久化缓存,带过期机制(默认TTL=24小时)"""
        try:
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            if cache_file.exists():
                # P1-3: 检查缓存文件修改时间,超过24小时视为过期
                file_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
                if (datetime.now() - file_mtime).total_seconds() > 86400:  # 24小时
                    logger.debug(f"持久化缓存已过期,删除: {cache_file.name}")
                    cache_file.unlink()
                    return None
                df = pd.read_parquet(cache_file)
                logger.debug(f"加载持久化缓存: {cache_file.name}")
                return df
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.debug(f"加载持久化缓存失败: {e}")
        return None

    def _save_persistent_cache(self, cache_key: str, data: pd.DataFrame) -> None:
        try:
            if data is None or data.empty:
                return
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            data.to_parquet(cache_file, index=True)
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.debug(f"保存持久化缓存失败: {e}")

    def get_historical_data(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        cache_key = f"historical_{symbol}_{period}{self._cache_suffix()}"

        with self.cache_lock:
            if cache_key in self.data_cache:
                cached_data = self.data_cache[cache_key]
                cache_time = cached_data.get("timestamp")
                if cache_time and (datetime.now() - cache_time).days < 1:
                    logger.debug(f"使用内存缓存的历史数据: {cache_key}")
                    return cached_data["data"]

        # 优先读本地持久化缓存
        persistent = self._load_persistent_cache(cache_key)
        if persistent is not None and not persistent.empty:
            with self.cache_lock:
                self.data_cache[cache_key] = {"data": persistent, "timestamp": datetime.now()}
            return persistent

        try:
            historical_data = self._fetch_historical_data(symbol, period)

            with self.cache_lock:
                self.data_cache[cache_key] = {"data": historical_data, "timestamp": datetime.now()}

            logger.info(f"获取历史数据: {cache_key}")
            try:
                self._save_persistent_cache(cache_key, historical_data)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.debug(f"写入持久化缓存失败: {e}")
            return historical_data

        except RuntimeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"获取历史数据失败: {e}")
            raise RuntimeError(f"获取历史数据失败 ({symbol}, period={period}): {e}") from e

    def get_sentiment_data(self, symbol: Optional[str] = None) -> Optional[Dict]:
        cache_key = f"sentiment_{symbol or 'SPY'}{self._cache_suffix()}"

        with self.cache_lock:
            if cache_key in self.data_cache:
                cached_data = self.data_cache[cache_key]
                cache_time = cached_data.get("timestamp")

                if cache_time and (datetime.now() - cache_time).total_seconds() < 300:
                    logger.debug(f"使用缓存的情绪数据: {cache_key}")
                    return cast(Optional[Dict], cached_data["data"])

        try:
            sentiment_data = self._fetch_sentiment_data(symbol or "SPY")

            if sentiment_data is not None:
                with self.cache_lock:
                    self.data_cache[cache_key] = {"data": sentiment_data, "timestamp": datetime.now()}
                logger.info(f"获取情绪数据: {cache_key}")
            else:
                logger.warning(f"情绪数据不可用 ({symbol or 'SPY'})")

            return sentiment_data

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"获取情绪数据失败: {e}")
            return None

    def get_technical_indicators(self, symbol: str) -> Dict:
        try:
            historical_data = self.get_historical_data(symbol)
            technical_indicators = self._calculate_technical_indicators(historical_data)
            logger.info(f"计算技术指标: {symbol}")
            return technical_indicators

        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.error(f"获取技术指标失败: {e}")
            return {}

    def _fetch_real_time_data(self, symbol: str) -> Dict:
        try:
            # P1: Wind MCP
            wind_data = self._try_wind_mcp_realtime(symbol)
            if wind_data:
                return wind_data

            logger.warning("Wind MCP 实时数据获取失败，尝试通达信: %s", symbol)

            # P2: 通达信
            tdx_data = self._try_tdx_realtime(symbol)
            if tdx_data:
                return tdx_data

            logger.warning("通达信实时数据获取失败，尝试 AKShare: %s", symbol)

            # P3: AKShare
            akshare_data = self._try_akshare_realtime(symbol)
            if akshare_data:
                return akshare_data

            logger.warning("AKShare 实时数据获取失败，尝试新浪财经: %s", symbol)

            # P4: 新浪财经实时行情（免费 HTTP 兜底）
            sina_data = self._try_sina_http_realtime(symbol)
            if sina_data:
                return sina_data

            logger.warning("新浪财经实时行情获取失败: %s", symbol)
            raise RuntimeError(
                f"所有数据源获取实时数据失败: {symbol} "
                f"(Wind MCP: {self.source_health['wind_mcp'].get('last_error')}, "
                f"通达信: {self.source_health['tdx'].get('last_error')}, "
                f"AKShare: {self.source_health['akshare'].get('last_error')}, "
                f"新浪HTTP: {self.source_health['sina_http'].get('last_error')})"
            )
        except RuntimeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"获取实时数据失败: {e}")
            raise RuntimeError(f"获取实时数据失败 ({symbol}): {e}") from e

    def _fetch_historical_data(self, symbol: str, period: str) -> pd.DataFrame:
        try:
            # P1: Wind MCP
            wind_data = self._try_wind_mcp_historical(symbol, period)
            if wind_data is not None and not wind_data.empty:
                return wind_data

            logger.warning("Wind MCP 历史数据获取失败，尝试通达信: %s", symbol)

            # P2: 通达信
            tdx_data = self._try_tdx_historical(symbol, period)
            if tdx_data is not None and not tdx_data.empty:
                return tdx_data

            logger.warning("通达信历史数据获取失败，尝试 AKShare: %s", symbol)

            # P3: AKShare
            akshare_data = self._try_akshare_historical(symbol, period)
            if akshare_data is not None and not akshare_data.empty:
                return akshare_data

            logger.warning("AKShare 历史数据获取失败，尝试新浪 HTTP: %s", symbol)

            # P4: 新浪 HTTP
            sina_data = self._try_sina_http_historical(symbol, period)
            if sina_data is not None and not sina_data.empty:
                return sina_data

            logger.warning("新浪 HTTP 历史数据获取失败: %s", symbol)
            raise RuntimeError(
                f"所有数据源获取历史数据失败: {symbol} (period={period}) "
                f"(Wind MCP: {self.source_health['wind_mcp'].get('last_error')}, "
                f"通达信: {self.source_health['tdx'].get('last_error')}, "
                f"AKShare: {self.source_health['akshare'].get('last_error')}, "
                f"新浪HTTP: {self.source_health['sina_http'].get('last_error')})"
            )
        except RuntimeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"获取历史数据失败: {e}")
            raise RuntimeError(f"获取历史数据失败 ({symbol}, period={period}): {e}") from e

    def _fetch_sentiment_data(self, symbol: str) -> Optional[Dict]:
        """获取情绪数据

        当前无真实情绪数据源接入，返回 None 并记录 warning。
        接入真实数据源后应在此处实现实际获取逻辑，
        而非返回硬编码假值导致系统基于虚构情绪数据做交易决策。
        """
        logger.warning(f"无真实情绪数据源可用，无法获取情绪数据 ({symbol or 'SPY'})")
        return None

    def _calculate_technical_indicators(self, data: pd.DataFrame) -> Dict:
        try:
            if len(data) < 20:
                return {}

            prices = [float(x) for x in data["close"].values]
            volumes = [float(x) for x in data["volume"].values]

            ma20 = _mean(prices[-20:])
            ma50 = _mean(prices[-50:])
            ma200 = _mean(prices[-200:]) if len(prices) >= 200 else ma50

            delta = _diff(prices)
            gain = _where([d > 0 for d in delta], delta, 0)
            loss = _where([d < 0 for d in delta], [-d for d in delta], 0)

            avg_gain = _mean(gain[-14:])
            avg_loss = _mean(loss[-14:])

            if avg_loss == 0:
                rsi: float = 50.0
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
                "timestamp": datetime.now().isoformat(),
                "ma20": ma20,
                "ma50": ma50,
                "ma200": ma200,
                "rsi": rsi,
                "macd": macd,
                "bb_upper": bb_upper,
                "bb_lower": bb_lower,
                "bb_width": (bb_upper - bb_lower) / sma20 if sma20 else 0.0,
                "price_position": (prices[-1] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) else 0.0,
                "volume_sma": _mean(volumes[-20:]),
                "trend": "upward" if prices[-1] > prices[-5] else "downward",
            }

            return technical_indicators

        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.error(f"计算技术指标失败: {e}")
            return {}

    def _calculate_ema(self, data: List[float], period: int) -> float:
        if len(data) < period:
            return _mean(data)

        alpha = 2 / (period + 1)
        ema: float = data[0]

        for value in data[1:]:
            ema = alpha * value + (1 - alpha) * ema

        return ema

    def _get_default_market_data(self) -> Dict:
        """返回硬编码假数据 (index_price=3000 等)，仅供测试/调试使用。

        .. deprecated:: 此方法已不再被主数据流调用。
        L5 修复: 原返回硬编码假数据 (index_price=3000) 会被误用于交易决策。
        现改为 fail-closed 抛 RuntimeError, 防止任何误调用返回假数据。
        """
        raise RuntimeError(
            "_get_default_market_data 已废弃 (L5): 返回硬编码假数据有误用风险。"
            "主数据流已 fail-fast, 所有数据源失败时抛 RuntimeError 而非假数据。"
        )

    def _get_default_historical_data(self) -> pd.DataFrame:
        """返回硬编码假历史数据 (base_price=3000)，仅供测试/调试使用。

        .. deprecated:: 此方法已不再被主数据流调用。
        L5 修复: 原返回硬编码假历史数据有误用风险。现改为 fail-closed 抛 RuntimeError。
        """
        raise RuntimeError(
            "_get_default_historical_data 已废弃 (L5): 返回硬编码假历史数据有误用风险。"
            "主数据流已 fail-fast。"
        )

    def _get_default_sentiment_data(self) -> Dict:
        """返回硬编码假情绪数据，仅供测试/调试使用。

        .. deprecated:: 此方法已不再被主数据流调用。
        L5 修复: 原返回硬编码假情绪数据有误用风险。现改为 fail-closed 抛 RuntimeError。
        """
        raise RuntimeError(
            "_get_default_sentiment_data 已废弃 (L5): 返回硬编码假情绪数据有误用风险。"
            "主数据流已返回 None 并记录 warning。"
        )

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
            return result.to_dict() if hasattr(result, "to_dict") else result.__dict__
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.warning(f"价格预测失败 ({symbol}): {e}")
            return {}

    def _get_recent_prices_for_prediction(self, symbol: str, days: int = 120):
        """获取近期收盘价序列 (供预测用)"""
        try:
            import numpy as np

            hist = self.get_historical_data(symbol, period="1y")
            if hist is None or len(hist) < 30:
                return None
            col = "close" if "close" in hist.columns else "Close"
            prices = hist[col].tail(days).values
            return np.array(prices, dtype=float)
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.debug(f"获取预测价格序列失败 ({symbol}): {e}")
            return None

    def get_external_macro(self) -> Dict:
        """获取外部宏观数据 (FRED/Econdb/Treasury)"""
        try:
            from utils.external_data_source import ExternalDataManager

            mgr = ExternalDataManager()
            return mgr.get_macro_snapshot()
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"外部宏观数据获取失败: {e}")
            return {}

    def get_risk_sentiment(self) -> Dict:
        """获取风险情绪指标 (加密货币/国债收益率/VIX代理)"""
        try:
            from utils.external_data_source import ExternalDataManager

            mgr = ExternalDataManager()
            return mgr.get_risk_sentiment()
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"风险情绪指标获取失败: {e}")
            return {}

    def get_news_sentiment(self, symbol: str, limit: int = 20) -> List[Dict]:
        """获取新闻+情感分析 (web_scraper + ai_report_agent)"""
        try:
            from utils.ai_report_agent import AIReportAgent
            from utils.web_scraper import WebScraper

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
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"新闻情感分析失败 ({symbol}): {e}")
            return []

    def get_ai_daily_report(self, symbols: List[str]) -> Dict:
        """生成 AI 每日投资报告"""
        try:
            from utils.ai_report_agent import AIReportAgent

            agent = AIReportAgent()
            report = agent.generate_daily_report(symbols=symbols)
            return report.__dict__
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning(f"AI 每日报告生成失败: {e}")
            return {"error": str(e)}

    def get_extended_status(self) -> Dict:
        """获取扩展状态 (含新模块健康检查)"""
        status: Dict[str, Any] = {
            "data_sources": {
                "wind_mcp": self.source_health.get("wind_mcp", {}).get("ok", False),
                "tdx": self.source_health.get("tdx", {}).get("ok", False),
                "akshare": self.source_health.get("akshare", {}).get("ok", False),
                "sina_http": self.source_health.get("sina_http", {}).get("ok", False),
            },
            "cache": self.get_cache_info(),
        }
        # 新模块可用性
        for module_name in ["tf_price_predictor", "external_data_source", "web_scraper", "ai_report_agent"]:
            try:
                __import__(f"utils.{module_name}")
                status[f"{module_name}_available"] = True
            except ImportError:
                status[f"{module_name}_available"] = False
        return status

    def clear_cache(self):
        with self.cache_lock:
            self.data_cache.clear()
            logger.info("数据缓存已清除")

    def get_cache_info(self) -> Dict:
        with self.cache_lock:
            return {
                "cache_size": len(self.data_cache),
                "max_cache_size": self.cache_size,
                "cached_items": list(self.data_cache.keys()),
            }

    # ===========================================================
    # U3: 复权因子支持 — hfq 历史价 ↔ 未复权实时价 对齐
    # ===========================================================
    def get_hfq_factor(self, symbol: str, date: Optional[str] = None) -> float:
        """U3: 获取 A股后复权累计因子.

        因子仅在除权日变化, 长缓存 (24h); akshare 不可用时降级返回 1.0.

        Args:
            symbol: 股票代码
            date: 日期 (YYYY-MM-DD), None=最新

        Returns:
            hfq 累计因子; 失败返回 1.0 (等同未复权)
        """
        try:
            from utils.adjust_factor_provider import get_adjust_factor_provider

            return get_adjust_factor_provider().get_hfq_factor(symbol, date=date)
        except (ImportError, RuntimeError, ValueError, TypeError, OSError) as e:
            logger.debug("获取 hfq 因子失败 (%s): %s", symbol, e)
            return 1.0

    def enrich_realtime_with_hfq(self, quote: Dict, symbol: str) -> Dict:
        """U3: 为实时行情字典注入复权因子 + hfq 对齐价.

        在原有实时行情 (未复权) 基础上新增:
            - hfq_factor: 当日 hfq 累计因子
            - hfq_equivalent_price: 未复权实时价 × 因子 = hfq 基准价 (与 hfq 历史价可比)
            - is_ex_dividend: 是否为除权日 (因子变化)

        用法:
            quote = provider.get_market_data("600519.SH")
            quote = provider.enrich_realtime_with_hfq(quote, "600519.SH")
            # quote["hfq_equivalent_price"] 可直接与 hfq 历史收盘比较

        Args:
            quote: 实时行情字典 (含 index_price / close 等价格字段)
            symbol: 股票代码

        Returns:
            富化后的 quote (同一对象, 原地修改); 因子不可用时 hfq_factor=1.0
        """
        try:
            from utils.adjust_factor_provider import (
                get_adjust_factor_provider,
                unadjusted_to_hfq,
            )

            provider = get_adjust_factor_provider()
            factor = provider.get_hfq_factor(symbol)
            quote["hfq_factor"] = factor
            # 取实时价 (兼容 index_price / close / price 字段)
            rt_price = float(
                quote.get("index_price")
                or quote.get("close")
                or quote.get("price")
                or 0.0
            )
            quote["hfq_equivalent_price"] = unadjusted_to_hfq(rt_price, factor)
            quote["is_ex_dividend"] = provider.is_ex_dividend_date(symbol)
            return quote
        except (ImportError, RuntimeError, ValueError, TypeError, OSError) as e:
            logger.debug("富化实时行情 hfq 失败 (%s): %s", symbol, e)
            quote.setdefault("hfq_factor", 1.0)
            quote.setdefault("hfq_equivalent_price", quote.get("index_price", 0.0))
            quote.setdefault("is_ex_dividend", False)
            return quote


_data_provider: Optional["MarketDataProvider"] = None


def get_market_data(symbol: Optional[str] = None) -> Dict:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_market_data(symbol)


def get_historical_data(symbol: str, period: str = "1y") -> pd.DataFrame:
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_historical_data(symbol, period)


def get_sentiment_data(symbol: Optional[str] = None) -> Optional[Dict]:
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
    logger.info("测试市场数据提供器 (fail-fast 模式)")

    try:
        market_data = get_market_data()
        logger.info("市场数据:", market_data["index_price"])
    except RuntimeError as e:
        logger.error(f"市场数据获取失败 (fail-fast): {e}")

    try:
        historical_data = get_historical_data("SPY", "1m")
        logger.info("历史数据形状:", historical_data.shape)
    except RuntimeError as e:
        logger.error(f"历史数据获取失败 (fail-fast): {e}")

    sentiment_data = get_sentiment_data()
    if sentiment_data is not None:
        logger.info("情绪数据:", sentiment_data.get("composite_sentiment"))
    else:
        logger.info("情绪数据: 不可用 (无真实数据源)")

    try:
        tech_indicators = get_technical_indicators("SPY")
        logger.info("技术指标:", list(tech_indicators.keys()))
    except RuntimeError as e:
        logger.error(f"技术指标计算失败 (fail-fast): {e}")

    if _data_provider:
        cache_info = _data_provider.get_cache_info()
        logger.info("缓存信息:", cache_info)
