"""
通达信股票专业数据源适配器
基于 pytdx/pytdx2 实现，接入现有数据提供者架构
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class TDXDataSource:
    """通达信数据源适配器，提供实时行情和历史K线数据"""

    def __init__(self):
        self._api = None
        self._api_cls = None
        self._ex_api_cls = None
        self._connected = False
        self._last_connect_time = None
        self._reconnect_interval = 300
        self.source_health = {"tdx": {"ok": False, "last_error": None, "last_success": None}}
        self._init_connection()

    def _init_connection(self):
        """初始化通达信连接"""
        try:
            # 尝试导入 pytdx 或 pytdx2
            try:
                from pytdx.exhq import TdxExHq_API
                from pytdx.hq import TdxHq_API

                self._api_cls = TdxHq_API
                self._ex_api_cls = TdxExHq_API
                logger.info("使用 pytdx 原生库")
            except ImportError:
                try:
                    from pytdx2.exhq import TdxExHq_API
                    from pytdx2.hq import TdxHq_API

                    self._api_cls = TdxHq_API
                    self._ex_api_cls = TdxExHq_API
                    logger.info("使用 pytdx2 兼容库")
                except ImportError:
                    raise ImportError("未找到 pytdx 或 pytdx2 库，请安装: pip install pytdx2") from None

            # 建立连接
            self._connect()

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            self.source_health["tdx"]["last_error"] = str(e)  # type: ignore[index]
            logger.warning(f"通达信数据源初始化失败: {e}")

    def _connect(self):
        """连接到通达信服务器"""
        try:
            if self._api_cls is None:
                logger.error("通达信API类未初始化，无法连接")
                return

            if self._api is not None:
                try:
                    self._api.disconnect()
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                    logger.debug(f"[tdx] disconnect 失败: {e}")

            self._api = self._api_cls()

            # 使用标准行情服务器列表
            servers = [
                ("119.147.212.81", 7709),  # 深圳电信
                ("218.75.126.9", 7709),  # 上海电信
                ("221.194.181.176", 7709),  # 北京联通
                ("117.184.140.156", 7709),  # 广州移动
            ]

            last_error = None
            for ip, port in servers:
                try:
                    if self._api.connect(ip, port):
                        self._connected = True
                        self._last_connect_time = time.time()
                        self.source_health["tdx"]["ok"] = True
                        self.source_health["tdx"]["last_success"] = datetime.now().isoformat()  # type: ignore[index]
                        self.source_health["tdx"]["last_error"] = None
                        logger.info(f"通达信连接成功: {ip}:{port}")
                        return
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                    last_error = e
                    continue

            raise last_error or Exception("所有通达信服务器连接失败")

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            self._connected = False
            self.source_health["tdx"]["ok"] = False
            self.source_health["tdx"]["last_error"] = str(e)  # type: ignore[index]
            logger.error(f"通达信连接失败: {e}")

    def _ensure_connected(self):
        """确保连接有效，必要时重连"""
        if self._api_cls is None:
            return False
        if not self._connected or self._api is None:
            self._connect()
            return self._connected

        if self._last_connect_time and (time.time() - self._last_connect_time) > self._reconnect_interval:
            self._connect()

        return self._connected

    def _to_tdx_code(self, symbol: str) -> str:
        """将股票代码转换为通达信代码字符串"""
        s = str(symbol).strip()
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if s.startswith(prefix):
                s = s[len(prefix) :]
                break
        for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        return s

    def _get_market(self, symbol: str) -> int:
        """获取市场代码: 0=深圳, 1=上海, 2=北京"""
        s = str(symbol).strip()
        for prefix in ("sh", "SH"):
            if s.startswith(prefix):
                return 1
        for prefix in ("sz", "SZ"):
            if s.startswith(prefix):
                return 0
        for prefix in ("bj", "BJ"):
            if s.startswith(prefix):
                return 2
        for suffix in (".SH", ".sh"):
            if s.endswith(suffix):
                return 1
        for suffix in (".SZ", ".sz"):
            if s.endswith(suffix):
                return 0
        for suffix in (".BJ", ".bj"):
            if s.endswith(suffix):
                return 2

        # 根据代码判断
        if s.startswith(("6", "5", "9")):
            return 1  # 上海
        elif s.startswith(("0", "1", "3")):
            return 0  # 深圳
        elif s.startswith(("4", "8")):
            return 2  # 北京
        return 0

    def get_realtime_quote(self, symbol: str) -> Optional[Dict]:
        """获取实时行情"""
        if not self._ensure_connected():
            return None

        try:
            code = self._to_tdx_code(symbol)
            market = self._get_market(symbol)

            if not code or market not in (0, 1, 2):
                return None

            # 获取实时行情
            quotes = self._api.get_security_quotes([(market, code)])  # type: ignore[union-attr]
            if not quotes:
                return None

            q = quotes[0]

            # 获取前收盘价
            try:
                stock_info = self._api.get_security_info(market, code)  # type: ignore[union-attr]
                prev_close = stock_info.get("last_close", 0) if stock_info else 0
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                prev_close = 0

            result = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "index_price": safe_float(q.get("price", 0)),
                "prev_close": safe_float(prev_close),
                "open": safe_float(q.get("open", 0)),
                "high": safe_float(q.get("high", 0)),
                "low": safe_float(q.get("low", 0)),
                "volume": safe_float(q.get("vol", 0), default=0),
                "amount": safe_float(q.get("amount", 0), default=0),
                "source": "tdx",
            }

            self.source_health["tdx"]["ok"] = True
            self.source_health["tdx"]["last_success"] = datetime.now().isoformat()  # type: ignore[index]
            return result

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            self.source_health["tdx"]["ok"] = False
            self.source_health["tdx"]["last_error"] = str(e)  # type: ignore[index]
            logger.error(f"通达信获取实时行情失败: {e}")
            return None

    def get_historical_klines(self, symbol: str, period: str = "1d", count: int = 252) -> Optional[pd.DataFrame]:
        """获取历史K线数据

        Args:
            symbol: 股票代码
            period: 周期 '1d'/'1w'/'1m'/'5m'/'15m'/'30m'/'60m'
            count: 获取条数
        """
        if not self._ensure_connected():
            return None

        try:
            code = self._to_tdx_code(symbol)
            market = self._get_market(symbol)

            if not code or market not in (0, 1, 2):
                return None

            # 周期映射
            period_map = {
                "1d": 9,  # 日线
                "1w": 10,  # 周线
                "1m": 11,  # 月线
                "5m": 0,  # 5分钟
                "15m": 1,  # 15分钟
                "30m": 2,  # 30分钟
                "60m": 3,  # 60分钟
            }
            tdx_period = period_map.get(period, 9)

            # 获取K线数据
            klines = self._api.get_security_bars(tdx_period, market, code, 0, count)  # type: ignore[union-attr]
            if not klines:
                return None

            records = []
            for k in klines:
                records.append(
                    {
                        "date": pd.to_datetime(k.get("datetime", "")),
                        "open": safe_float(k.get("open", 0)),
                        "high": safe_float(k.get("high", 0)),
                        "low": safe_float(k.get("low", 0)),
                        "close": safe_float(k.get("close", 0)),
                        "volume": safe_float(k.get("vol", 0), default=0),
                        "amount": safe_float(k.get("amount", 0), default=0),
                    }
                )

            if not records:
                return None

            df = pd.DataFrame(records)
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)

            self.source_health["tdx"]["ok"] = True
            self.source_health["tdx"]["last_success"] = datetime.now().isoformat()  # type: ignore[index]
            return df

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            self.source_health["tdx"]["ok"] = False
            self.source_health["tdx"]["last_error"] = str(e)  # type: ignore[index]
            logger.error(f"通达信获取历史K线失败: {e}")
            return None

    def get_financial_data(self, symbol: str) -> Optional[Dict]:
        """获取财务数据（如需要）"""
        if not self._ensure_connected():
            return None

        try:
            code = self._to_tdx_code(symbol)
            market = self._get_market(symbol)

            if not code or market not in (0, 1, 2):
                return None

            # 获取财务数据
            finance = self._api.get_finance_info(market, code)  # type: ignore[union-attr]
            if not finance:
                return None

            result = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "source": "tdx",
                "data": finance,
            }

            self.source_health["tdx"]["ok"] = True
            return result

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            self.source_health["tdx"]["ok"] = False
            self.source_health["tdx"]["last_error"] = str(e)  # type: ignore[index]
            logger.error(f"通达信获取财务数据失败: {e}")
            return None

    def get_sector_stocks(self, sector_name: str) -> List[Dict]:
        """获取板块成分股（如需要）"""
        if not self._ensure_connected():
            return []

        try:
            # 板块查询逻辑
            # 这里需要根据通达信API具体实现
            return []
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"通达信获取板块数据失败: {e}")
            return []

    def disconnect(self):
        """断开连接"""
        try:
            if self._api:
                self._api.disconnect()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass
        finally:
            self._connected = False
            self._api = None


def safe_float(value, default=None):
    """安全转换为float"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# 全局通达信数据源实例
_tdx_instance = None
# P0-C3 修复 (2026-07-29): 单例并发初始化锁, 防多线程首次调用双重初始化
_tdx_instance_lock = threading.Lock()


def get_tdx_source() -> Optional[TDXDataSource]:
    """获取通达信数据源单例 (双重检查锁定)"""
    global _tdx_instance
    if _tdx_instance is None:
        with _tdx_instance_lock:
            if _tdx_instance is None:
                _tdx_instance = TDXDataSource()
    return _tdx_instance
