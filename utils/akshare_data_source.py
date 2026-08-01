# -*- coding: utf-8 -*-
"""
AKShare 股票数据源适配器
基于 akshare 实现，接入现有数据提供者架构

优势:
- A股全覆盖（沪深京）
- 支持实时行情、历史K线、财务数据
- 国内可直连，无需代理
- 免费数据，无额度限制
"""

import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0):
    try:
        v = float(val or 0)
        return v if v == v else default
    except (ValueError, TypeError):
        return default


class AKShareDataSource:
    """AKShare 数据源适配器，提供实时行情和历史K线数据"""

    def __init__(self):
        self._ak = None
        self._connected = False
        self._last_connect_time = None
        self._spot_cache = {}
        self._spot_cache_time = 0
        self._spot_cache_ttl = 60
        self.source_health = {"akshare": {"ok": False, "last_error": None, "last_success": None}}
        self._init_connection()

    def _init_connection(self):
        """初始化 AKShare"""
        try:
            import akshare as ak

            self._ak = ak
            self._connected = True
            self.source_health["akshare"]["ok"] = True
            self.source_health["akshare"]["last_success"] = datetime.now().isoformat()  # type: ignore
            logger.info("AKShare 数据源初始化成功")
        except ImportError as e:
            self.source_health["akshare"]["last_error"] = f"模块导入失败: {e}"  # type: ignore
            logger.warning(f"AKShare 数据源模块导入失败: {e}")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            self.source_health["akshare"]["last_error"] = str(e)  # type: ignore
            logger.warning(f"AKShare 数据源初始化失败: {e}")

    def _ensure_connected(self):
        """确保 AKShare 可用"""
        if self._ak is None:
            self._init_connection()
        return self._ak is not None

    def _to_akshare_code(self, symbol: str) -> str:
        """将股票代码转换为 AKShare 格式"""
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

    def _get_market(self, symbol: str) -> str:
        """获取市场代码"""
        s = str(symbol).strip()
        if s.startswith(("sh", "SH")) or s.endswith((".SH", ".sh")) or s.startswith("6"):
            return "sh"
        if s.startswith(("sz", "SZ")) or s.endswith((".SZ", ".sz")) or s.startswith(("0", "3")):
            return "sz"
        if s.startswith(("bj", "BJ")) or s.endswith((".BJ", ".bj")) or s.startswith(("4", "8")):
            return "bj"
        return "sh"

    def _clean_name(self, name: str) -> str:
        """清洗股票名称，去除 XD/XR/DR 等前缀"""
        if not name:
            return ""
        prefixes_to_remove = ["XD", "XR", "DR", "xd", "xr", "dr"]
        result = str(name).strip()
        for prefix in prefixes_to_remove:
            if result.startswith(prefix):
                result = result[len(prefix) :].strip()
                break
        return result

    def _fetch_spot_cache(self):
        """获取全市场实时数据并缓存（TTL 60秒）"""
        now = time.time()
        if now - self._spot_cache_time < self._spot_cache_ttl and self._spot_cache:
            return

        try:
            df = self._ak.stock_zh_a_spot_em()  # type: ignore
            if df is not None and not df.empty:
                self._spot_cache = {}
                for _, row in df.iterrows():
                    code = str(row.get("代码", "")).strip()
                    if code:
                        self._spot_cache[code] = row.to_dict()
                self._spot_cache_time = now  # type: ignore
                logger.debug(f"AKShare 缓存全市场数据: {len(self._spot_cache)} 只股票")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug(f"AKShare 缓存全市场数据失败: {e}")

    def get_realtime_quote(self, symbol: str) -> Optional[Dict]:
        """获取实时行情"""
        if not self._ensure_connected():
            return None

        try:
            code = self._to_akshare_code(symbol)

            if not code:
                return None

            self._fetch_spot_cache()

            row_data = self._spot_cache.get(code)
            if not row_data:
                logger.warning(f"AKShare 未找到股票: {symbol}")
                return None

            name = self._clean_name(row_data.get("名称", ""))
            price = _safe_float(row_data.get("最新价"))
            prev_close = _safe_float(row_data.get("昨收"))
            open_price = _safe_float(row_data.get("今开"))
            high = _safe_float(row_data.get("最高"))
            low = _safe_float(row_data.get("最低"))
            volume = _safe_float(row_data.get("成交量"), default=0)

            if price <= 0 and open_price <= 0:
                logger.warning(f"AKShare 返回无效价格: {symbol}, price={price}")
                return None

            self.source_health["akshare"]["ok"] = True
            self.source_health["akshare"]["last_success"] = datetime.now().isoformat()  # type: ignore

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
                "amount": _safe_float(row_data.get("成交额"), default=0),
                "source": "akshare",
            }

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            self.source_health["akshare"]["ok"] = False
            self.source_health["akshare"]["last_error"] = str(e)  # type: ignore
            logger.error(f"AKShare 获取实时行情失败: {e}")
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
            code = self._to_akshare_code(symbol)

            if not code:
                return None

            period_map = {
                "1d": "daily",
                "1w": "weekly",
                "1m": "monthly",
                "5m": "5",
                "15m": "15",
                "30m": "30",
                "60m": "60",
            }

            ak_period = period_map.get(period, "daily")

            if period in ("1d", "1w", "1m"):
                df = self._ak.stock_zh_a_hist(  # type: ignore
                    symbol=code, period=ak_period, start_date="", end_date="", adjust="qfq"
                )
            else:
                df = self._ak.stock_zh_a_minute(  # type: ignore
                    symbol=code, period=ak_period, adjust="qfq"
                )

            if df is None or df.empty:
                logger.warning(f"AKShare 返回空 K 线数据: {symbol}")
                return None

            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "开盘" in df.columns:
                df = df.rename(columns={"开盘": "open"})
            if "最高" in df.columns:
                df = df.rename(columns={"最高": "high"})
            if "最低" in df.columns:
                df = df.rename(columns={"最低": "low"})
            if "收盘" in df.columns:
                df = df.rename(columns={"收盘": "close"})
            if "成交量" in df.columns:
                df = df.rename(columns={"成交量": "volume"})
            if "成交额" in df.columns:
                df = df.rename(columns={"成交额": "amount"})

            required_cols = ["date", "open", "high", "low", "close", "volume"]
            for col in required_cols:
                if col not in df.columns:
                    logger.warning(f"AKShare K线数据缺少必要字段: {col}")
                    return None

            df["date"] = pd.to_datetime(df["date"])
            df = df[required_cols]
            df = df.sort_values("date")
            df.set_index("date", inplace=True)

            if "amount" in df.columns:
                df["amount"] = df["amount"].apply(_safe_float)

            records = []
            for date, row in df.iterrows():
                close = _safe_float(row.get("close"))
                if close is None or close <= 0:
                    continue
                records.append(
                    {
                        "date": date,
                        "open": _safe_float(row.get("open")) or close,
                        "high": _safe_float(row.get("high")) or close,
                        "low": _safe_float(row.get("low")) or close,
                        "close": close,
                        "volume": _safe_float(row.get("volume"), default=0),
                        "amount": _safe_float(row.get("amount"), default=0),
                    }
                )

            if not records:
                logger.warning(f"AKShare K线数据清洗后为空: {symbol}")
                return None

            result_df = pd.DataFrame(records)
            result_df.set_index("date", inplace=True)
            result_df.sort_index(inplace=True)

            if period not in ("1d", "1w", "1m"):
                result_df = result_df.tail(count)
            else:
                result_df = result_df.tail(count)

            self.source_health["akshare"]["ok"] = True
            self.source_health["akshare"]["last_success"] = datetime.now().isoformat()  # type: ignore

            return result_df

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            self.source_health["akshare"]["ok"] = False
            self.source_health["akshare"]["last_error"] = str(e)  # type: ignore
            logger.error(f"AKShare 获取历史K线失败: {e}")
            return None

    def get_financial_report(self, symbol: str) -> Optional[Dict]:
        """获取财务报表数据"""
        if not self._ensure_connected():
            return None

        try:
            code = self._to_akshare_code(symbol)

            if not code:
                return None

            df = self._ak.stock_financial_report_sina(stock=code)  # type: ignore

            if df is None or df.empty:
                logger.warning(f"AKShare 未找到财务数据: {symbol}")
                return None

            row = df.iloc[0]

            result = {
                "symbol": symbol,
                "total_assets": _safe_float(row.get("总资产", 0)),
                "total_liabilities": _safe_float(row.get("总负债", 0)),
                "total_equity": _safe_float(row.get("净资产", 0)),
                "revenue": _safe_float(row.get("营业收入", 0)),
                "net_profit": _safe_float(row.get("净利润", 0)),
                "eps": _safe_float(row.get("每股收益", 0)),
                "pe": _safe_float(row.get("市盈率", 0)),
                "pb": _safe_float(row.get("市净率", 0)),
                "source": "akshare",
            }

            return result

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"AKShare 获取财务数据失败: {e}")
            return None


_akshare_source = None


def get_akshare_source() -> AKShareDataSource:
    """获取 AKShare 数据源单例"""
    global _akshare_source
    if _akshare_source is None:
        _akshare_source = AKShareDataSource()
    return _akshare_source


if __name__ == "__main__":
    logger.info("测试 AKShare 数据源")

    source = get_akshare_source()
    logger.info("数据源状态:", source.source_health)

    quote = source.get_realtime_quote("600519.SH")
    logger.info("实时行情:", quote)

    df = source.get_historical_klines("600519.SH", "1d", 5)
    logger.info("K线数据:", df.head() if df is not None else None)
