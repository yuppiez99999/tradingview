# -*- coding: utf-8 -*-
"""
free-stockdb 本地数据引擎适配层 (阶段 1: 研究/回测专用)
==========================================================

将 free-stockdb 的 Python SDK / HTTP API 封装为与现有系统兼容的接口。

双通道设计:
    通道 A: HTTP API (127.0.0.1:7899) — 稳定, 不依赖 Python 版本/DLL
    通道 B: Python SDK (stockdb.pyd) — 高性能, 但有 DLL 兼容性要求

设计原则:
    1. 不影响实盘交易 (Phase 6 执行订单仍用原有数据源)
    2. 自动降级: free-stockdb 不可用时回退到原有 MarketDataProvider
    3. 零侵入: 仅新增模块, 不修改原有 data_provider.py
    4. 研究专用: 用于 lgb_enhanced_trainer / 因子挖掘 / 回测脚本
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger("free_stockdb_adapter")

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 路径可通过环境变量 FREE_STOCKDB_ROOT 配置, 默认回退到本地安装路径
_FREE_STOCKDB_ROOT = Path(os.environ.get("FREE_STOCKDB_ROOT", r"D:\free-stockdb\stockdb"))
_FREE_STOCKDB_PYBAO = _FREE_STOCKDB_ROOT / "pybao"

# HTTP API 配置
_FS_HTTP_BASE = "http://127.0.0.1:7899"
_FS_HTTP_TIMEOUT = 5  # 秒

# 自动启动配置
_FS_STOCKDB_EXE = _FREE_STOCKDB_ROOT / "stockdb.exe"
_FS_AUTO_START_MAX_WAIT = 15  # 启动后最多等待多少秒
_FS_AUTO_STARTED = False  # 本会话是否已尝试过自动启动

# ============================================================
# 全局状态
# ============================================================
_fs_lock = threading.Lock()
_fs_http_available = False
_fs_sdk_available = False
_fs_last_check = 0.0
_FS_CHECK_CACHE_SECONDS = 60.0

_requests_available = False
try:
    import requests

    _requests_available = True
except ImportError:
    pass


# ============================================================
# 初始化: 自动启动
# ============================================================
def _auto_start_stockdb() -> bool:
    """自动启动 stockdb.exe 服务

    仅当:
    1. 本会话尚未尝试过启动
    2. stockdb.exe 存在
    3. 当前 HTTP 服务未运行

    才会尝试启动。启动后最多等待 _FS_AUTO_START_MAX_WAIT 秒。

    Returns:
        True 表示启动成功且服务就绪
    """
    global _FS_AUTO_STARTED

    if _FS_AUTO_STARTED:
        return False

    if not _FS_STOCKDB_EXE.exists():
        logger.debug(f"stockdb.exe 不存在: {_FS_STOCKDB_EXE}")
        return False

    _FS_AUTO_STARTED = True

    try:
        logger.info("🚀 自动启动 stockdb 服务...")
        subprocess.Popen(
            [str(_FS_STOCKDB_EXE)],
            cwd=str(_FREE_STOCKDB_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # 等待服务启动
        for i in range(_FS_AUTO_START_MAX_WAIT):
            time.sleep(1)
            if _check_http_available():
                logger.info(f"✅ stockdb 服务已启动 (等待 {i + 1}s)")
                return True

        logger.warning(f"⚠️ stockdb 启动超时 ({_FS_AUTO_START_MAX_WAIT}s), 将使用回退数据源")
        return False

    except Exception as e:
        logger.warning(f"⚠️ 自动启动 stockdb 失败: {e}, 将使用回退数据源")
        return False


# ============================================================
# 初始化: HTTP 通道
# ============================================================
def _check_http_available() -> bool:
    """检查 HTTP API 是否可用

    stockdb 根路径返回 400 也表示服务在线 (仅表示请求格式不正确)
    """
    if not _requests_available:
        return False
    try:
        r = requests.get(_FS_HTTP_BASE, timeout=2)
        return r.status_code in (200, 400)
    except Exception:
        return False


def _init_free_stockdb() -> bool:
    """初始化 free-stockdb (双通道检测, 懒加载, 线程安全)

    Returns:
        True 表示任一通道可用
    """
    global _fs_http_available, _fs_sdk_available, _fs_last_check

    now = datetime.now().timestamp()
    if (_fs_http_available or _fs_sdk_available) and (now - _fs_last_check) < _FS_CHECK_CACHE_SECONDS:
        return _fs_http_available or _fs_sdk_available

    with _fs_lock:
        now = datetime.now().timestamp()
        if (_fs_http_available or _fs_sdk_available) and (now - _fs_last_check) < _FS_CHECK_CACHE_SECONDS:
            return _fs_http_available or _fs_sdk_available

        # 通道 A: HTTP API (优先, 更稳定)
        _fs_http_available = _check_http_available()

        # 如果 HTTP 不可用, 尝试自动启动 stockdb.exe
        if not _fs_http_available:
            _auto_start_stockdb()
            _fs_http_available = _check_http_available()

        if _fs_http_available:
            _fs_last_check = now
            logger.info("✅ free-stockdb HTTP API 已就绪 (127.0.0.1:7899)")
            return True

        # 通道 B: Python SDK (备选)
        try:
            pybao_path = str(_FREE_STOCKDB_PYBAO)
            if pybao_path not in sys.path:
                sys.path.insert(0, pybao_path)
            from stock_sdk import bk, rd, zb  # type: ignore

            global _fs_client
            _fs_client = {"rd": rd, "zb": zb, "bk": bk}
            _fs_sdk_available = True
            _fs_last_check = now
            logger.info("✅ free-stockdb Python SDK 已就绪")
            return True
        except Exception as e:
            _fs_sdk_available = False
            logger.debug(f"free-stockdb Python SDK 不可用: {e}")

        _fs_last_check = now
        return False


def is_available() -> bool:
    """检查 free-stockdb 是否可用 (HTTP 或 SDK 任一即可)"""
    return _init_free_stockdb()


# ============================================================
# 工具函数
# ============================================================
def _strip_suffix(symbol: str) -> str:
    """剥离标的代码后缀 (如 600633.SH -> 600633)"""
    for sfx in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if symbol.endswith(sfx):
            return symbol[: -len(sfx)]
    return symbol


def _period_to_date_range(period: str) -> Tuple[str, str]:
    """将 period 字符串转换为 (start_date, end_date)"""
    end_date = datetime.now()
    period_lower = period.lower()
    years = 2
    if "1y" in period_lower or period_lower == "1":
        years = 1
    elif "2y" in period_lower or period_lower == "2":
        years = 2
    elif "3y" in period_lower or period_lower == "3":
        years = 3
    elif "5y" in period_lower or period_lower == "5":
        years = 5
    elif "10y" in period_lower or period_lower == "10":
        years = 10
    start_date = end_date - timedelta(days=years * 365 + 30)
    return start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")


def _normalize_fs_dataframe(df_raw: Any, symbol: str) -> pd.DataFrame:
    """将 free-stockdb 返回的数据标准化为系统统一格式"""
    if df_raw is None or (isinstance(df_raw, pd.DataFrame) and df_raw.empty):
        return pd.DataFrame()
    if isinstance(df_raw, list):
        if not df_raw:
            return pd.DataFrame()
        df = pd.DataFrame(df_raw)
    else:
        df = df_raw.copy() if isinstance(df_raw, pd.DataFrame) else pd.DataFrame(df_raw)
    if df.empty:
        return df

    df.columns = [str(c).lower() for c in df.columns]

    # 日期列处理
    date_col = None
    for col in ("date", "trade_date", "datetime", "time", "day"):
        if col in df.columns:
            date_col = col
            break

    if date_col is not None:
        df[date_col] = df[date_col].astype(str)
        # 支持 YYYYMMDD 和 YYYY-MM-DD 两种格式
        try:
            df.index = pd.to_datetime(df[date_col], format="%Y%m%d", errors="coerce")
        except Exception:
            df.index = pd.to_datetime(df[date_col], errors="coerce")
        df = df.drop(columns=[date_col])
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index, errors="coerce")
            except Exception:
                logger.warning("Unexpected error in free_stockdb_adapter.py", exc_info=True)

    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "date"

    # 关键列别名匹配
    required_cols = ["open", "high", "low", "close", "volume"]
    aliases_map = {
        "open": ["open_price", "开盘价", "o"],
        "high": ["high_price", "最高价", "h"],
        "low": ["low_price", "最低价", "l"],
        "close": ["close_price", "收盘价", "c"],
        "volume": ["vol", "成交量", "v", "amount"],
    }
    for col in required_cols:
        if col not in df.columns:
            for alias in aliases_map.get(col, []):
                if alias in df.columns:
                    df[col] = df[alias]
                    break

    df = df.sort_index()
    df = df.dropna(subset=["close"])
    return df


# ============================================================
# HTTP API 数据获取
# ============================================================
def _http_get_ohlcv(
    code: str,
    start_date: str,
    end_date: str,
    frequency: str = "1d",
    fq: Optional[str] = "qfq",
) -> Optional[List[Dict]]:
    """通过 HTTP API 获取 OHLCV 数据

    Args:
        code: 纯 6 位代码
        start_date: YYYYMMDD
        end_date: YYYYMMDD
        frequency: K线频率
        fq: 复权类型

    Returns:
        数据列表或 None
    """
    if not _requests_available or not _fs_http_available:
        return None

    try:
        # 频率映射
        freq_map = {
            "1d": "日k",
            "1w": "周k",
            "1M": "月k",
            "1m": "分钟k",
            "5m": "5分钟k",
            "15m": "15分钟k",
            "30m": "30分钟k",
            "60m": "60分钟k",
        }
        fs_freq = freq_map.get(frequency, "日k")

        # 复权: 使用复权数据接口
        if fq in ("qfq", "hfq"):
            # 使用复权接口 (qfq/hfq 在后端处理)
            # 格式: 复权:code:start*
            t_param = f"复权:{code}:{start_date[:4]}*"
            params = {"cmd": "get", "t": t_param}
            r = requests.get(_FS_HTTP_BASE, params=params, timeout=_FS_HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    # 过滤日期范围
                    return data
        else:
            # 不复权: 日k接口
            t_param = f"{fs_freq}:{code}:{start_date}"
            params = {"cmd": "get", "t": t_param}
            r = requests.get(_FS_HTTP_BASE, params=params, timeout=_FS_HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    return data

    except Exception as e:
        logger.debug(f"HTTP 获取 {code} 异常: {e}")

    return None


# ============================================================
# 核心数据获取接口
# ============================================================
def get_historical_data_fs(
    symbol: str,
    period: str = "2y",
    frequency: str = "1d",
    fq: Optional[str] = "qfq",
    use_fallback: bool = True,
) -> Optional[pd.DataFrame]:
    """获取历史 OHLCV 数据 (free-stockdb 优先, 支持自动降级)

    与现有 `utils.data_provider.get_historical_data` 接口完全兼容。

    Args:
        symbol: 标的代码 (支持 "600633.SH" 或 "600633")
        period: 回看周期 ("1y", "2y", "3y", "5y")
        frequency: K线频率 ("1d", "1m", "5m", "15m", "30m", "60m", "1w", "1M")
        fq: 复权类型 ("qfq" 前复权, "hfq" 后复权, None 不复权)
        use_fallback: free-stockdb 失败时是否回退到原有 MarketDataProvider

    Returns:
        DataFrame[open, high, low, close, volume, ...] 或 None
    """
    raw_symbol = symbol
    code = _strip_suffix(symbol)

    # 1. 尝试 free-stockdb (HTTP 优先, SDK 备选)
    if is_available():
        try:
            start_date, end_date = _period_to_date_range(period)

            # 通道 A: HTTP API
            if _fs_http_available:
                data_list = _http_get_ohlcv(code, start_date, end_date, frequency, fq)
                if data_list:
                    df = _normalize_fs_dataframe(data_list, raw_symbol)
                    if not df.empty and len(df) >= 30:
                        # 过滤日期范围
                        mask = (df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))
                        df = df.loc[mask]
                        if len(df) >= 30:
                            logger.debug(f"  {raw_symbol}: free-stockdb(HTTP) 返回 {len(df)} 行")
                            return df

            # 通道 B: Python SDK
            if _fs_sdk_available:
                global _fs_client
                rd = _fs_client["rd"]  # type: ignore
                df_raw = rd.get_data(
                    code=code,
                    start=start_date,
                    end=end_date,
                    frequency=frequency,
                    as_df=True,
                    fq=fq,
                )
                df = _normalize_fs_dataframe(df_raw, raw_symbol)
                if not df.empty and len(df) >= 30:
                    logger.debug(f"  {raw_symbol}: free-stockdb(SDK) 返回 {len(df)} 行")
                    return df

            logger.debug(f"  {raw_symbol}: free-stockdb 数据不足, 尝试回退...")

        except Exception as e:
            logger.debug(f"  {raw_symbol}: free-stockdb 查询异常: {e}")

    # 2. 回退到原有数据源
    if use_fallback:
        try:
            from utils.data_provider import get_historical_data

            logger.debug(f"  {raw_symbol}: 回退到 MarketDataProvider")
            return get_historical_data(code, period)
        except Exception as e:
            logger.warning(f"  {raw_symbol}: 回退也失败: {e}")

    return None


def get_batch_ohlcv_fs(
    symbols: List[Tuple],
    period: str = "2y",
    frequency: str = "1d",
    fq: Optional[str] = "qfq",
    use_fallback: bool = True,
) -> Dict[str, pd.DataFrame]:
    """批量获取历史 OHLCV 数据"""
    result: Dict[str, pd.DataFrame] = {}
    total = len(symbols)

    for code, suffix, _server_type, _name, _style in symbols:
        df = get_historical_data_fs(f"{code}{suffix}", period, frequency, fq, use_fallback)
        if df is not None and not df.empty:
            result[code] = df

    fs_count = len(result)
    logger.info(f"批量数据获取: {fs_count}/{total} 成功 (free-stockdb 优先 + 自动回退)")
    return result


# ============================================================
# 便捷封装: drop-in 替换
# ============================================================
FORCE_FREE_STOCKDB = os.environ.get("FORCE_FREE_STOCKDB", "1") == "1"


def get_historical_data(symbol: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """与 utils.data_provider.get_historical_data 签名完全一致的封装"""
    if FORCE_FREE_STOCKDB:
        return get_historical_data_fs(symbol, period, use_fallback=True)
    else:
        from utils.data_provider import get_historical_data as _orig

        return _orig(symbol, period)
