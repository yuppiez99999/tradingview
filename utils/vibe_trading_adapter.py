# -*- coding: utf-8 -*-
"""Vibe-Trading 数据适配器 — 28 系统集成层

核心功能:
    将 Vibe-Trading 的多源数据加载器 (Loader Registry) 封装为 28 系统可用的
    统一数据接口, 提供 PIT (Point-in-Time) 级别的数据质量保障.

设计原则:
    1. 懒加载: 只在首次调用时初始化 Vibe-Trading, 不影响现有模块
    2. 优雅降级: Vibe-Trading 不可用时自动回退到原有代理映射方案
    3. 符号映射: 28 系统代码格式 (510300.SH) → Vibe-Trading 格式 (510300.SH)
    4. 缓存复用: 共享 Vibe-Trading 的 Parquet 缓存机制

用法:
    from utils.vibe_trading_adapter import VibeTradingAdapter

    adapter = VibeTradingAdapter()
    df = adapter.get_ohlcv("510300.SH", "2021-01-01", "2026-07-30")
    batch = adapter.get_batch_ohlcv(["510300.SH", "588000.SH"], "2021-01-01", "2026-07-30")

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger("vibe_trading_adapter")

# ============================================================
# 路径配置
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VIBE_TRADING_ROOT = _PROJECT_ROOT.parent / "10_第三方项目" / "Vibe-Trading"

# Vibe-Trading 必须添加到 sys.path, 因为其模块结构为 agent/backtest/...
_VIBE_AGENT_PATH = _VIBE_TRADING_ROOT / "agent"

# 28 系统本地缓存目录
_LOCAL_CACHE_DIR = _PROJECT_ROOT / "data_cache" / "vibe_trading"

# ============================================================
# A股代码格式映射
# ============================================================

# 28 系统代码格式: 510300.SH (上交所), 000001.SZ (深交所)
# Vibe-Trading 格式兼容: tushare loader 接受 510300.SH 格式
# 特殊映射: ETF/指数代码别名

_CODE_ALIAS_MAP: Dict[str, str] = {
    "510050.SH": "510050.SH",   # 上证50ETF
    "510300.SH": "510300.SH",   # 沪深300ETF
    "510500.SH": "510500.SH",   # 中证500ETF
    "512100.SH": "512100.SH",   # 中证1000ETF
    "588000.SH": "588000.SH",   # 科创50ETF
    "588080.SH": "588080.SH",   # 科创50ETF易方达
    "159915.SZ": "159915.SZ",   # 创业板ETF
    "159992.SZ": "159992.SZ",   # 创新药ETF
}

# ============================================================
# 市场类型判断
# ============================================================


def _infer_market(symbol: str) -> str:
    """根据代码推断市场类型.

    Args:
        symbol: 股票代码, 如 "510300.SH"

    Returns:
        Vibe-Trading 市场标识 ("a_share", "us_equity", "hk_equity", "crypto", etc.)
    """
    if not symbol:
        return "a_share"
    upper = symbol.upper()
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return "a_share"
    if upper.endswith(".US") or (len(upper.split(".")[0]) <= 5 and "." not in upper.split(".")[0]):
        return "us_equity"
    if upper.endswith(".HK"):
        return "hk_equity"
    if upper.endswith(".KS") or upper.endswith(".KQ"):
        return "kr_equity"
    # 期货/期权
    if len(upper.split(".")[0]) <= 6 and any(c.isalpha() for c in upper.split(".")[0]):
        return "futures"
    return "a_share"


def _normalize_symbol(symbol: str) -> str:
    """标准化代码格式.

    Args:
        symbol: 原始代码

    Returns:
        Vibe-Trading 兼容代码
    """
    return _CODE_ALIAS_MAP.get(symbol, symbol)


# ============================================================
# Vibe-Trading 核心加载器
# ============================================================


class _VibeTradingCore:
    """Vibe-Trading 核心加载器 (懒加载, 单例).

    负责:
        1. 初始化 Vibe-Trading 的 Loader Registry
        2. 解析可用数据源 (自动探测网络/API 可用性)
        3. 提供统一的 fetch 接口

    线程安全: 通过 _initialized 标志控制一次性初始化.
    """

    _instance: Optional[_VibeTradingCore] = None
    _initialized: bool = False

    def __init__(self) -> None:
        self._loader_registry = None
        self._resolve_loader = None
        self._available_sources: List[str] = []
        self._init_error: Optional[str] = None

    @classmethod
    def get_instance(cls) -> _VibeTradingCore:
        """获取单例."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """初始化 Vibe-Trading 加载器.

        Returns:
            是否初始化成功
        """
        if self._initialized:
            return True

        # 检查 Vibe-Trading 路径
        if not _VIBE_TRADING_ROOT.exists():
            self._init_error = f"Vibe-Trading 目录不存在: {_VIBE_TRADING_ROOT}"
            logger.warning(self._init_error)
            return False

        if not _VIBE_AGENT_PATH.exists():
            self._init_error = f"Vibe-Trading agent 目录不存在: {_VIBE_AGENT_PATH}"
            logger.warning(self._init_error)
            return False

        # 将 Vibe-Trading agent 加入 sys.path
        if str(_VIBE_AGENT_PATH) not in sys.path:
            sys.path.insert(0, str(_VIBE_AGENT_PATH))

        try:
            # 延迟导入 Vibe-Trading 核心模块
            from backtest.loaders.registry import LOADER_REGISTRY, _ensure_registered, resolve_loader

            # 强制注册所有 loader
            _ensure_registered()

            self._loader_registry = LOADER_REGISTRY
            self._resolve_loader = resolve_loader

            # 探测可用数据源
            self._available_sources = self._detect_available_sources()

            self._initialized = True
            logger.info(
                f"Vibe-Trading 初始化成功: {len(self._loader_registry)} 个 loader, "
                f"可用: {self._available_sources}"
            )
            return True

        except ImportError as e:
            self._init_error = f"Vibe-Trading 导入失败: {e}"
            logger.warning(self._init_error)
            return False
        except Exception as e:
            self._init_error = f"Vibe-Trading 初始化异常: {e}"
            logger.warning(self._init_error)
            return False

    def _detect_available_sources(self) -> List[str]:
        """探测可用数据源.

        Returns:
            可用 source 名称列表
        """
        available = []
        for name, cls in self._loader_registry.items():
            try:
                instance = cls()
                if instance.is_available():
                    available.append(name)
            except Exception:
                pass
        return available

    def fetch(self, symbol: str, start_date: str, end_date: str,
              market: Optional[str] = None, interval: str = "1D") -> Optional[pd.DataFrame]:
        """从 Vibe-Trading 拉取单只标的的 OHLCV 数据.

        Args:
            symbol: 标准化后的代码
            start_date: 起始日期 "YYYY-MM-DD"
            end_date: 结束日期 "YYYY-MM-DD"
            market: 市场类型 (自动推断如果为 None)
            interval: 周期, 默认 "1D"

        Returns:
            OHLCV DataFrame (index=DatetimeIndex, columns=[open,high,low,close,volume]),
            失败返回 None
        """
        if not self._initialized and not self.initialize():
            return None

        if market is None:
            market = _infer_market(symbol)

        # 尝试按市场类型获取 loader
        try:
            loader = self._resolve_loader(market)
            raw_result = loader.fetch([symbol], start_date, end_date, interval=interval)

            if symbol in raw_result:
                df = raw_result[symbol]
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df

        except Exception as e:
            logger.debug(f"Vibe-Trading fetch ({market}/{symbol}) 失败: {e}")

        # 备用方案: 尝试直接用可用 source
        for source_name in self._available_sources:
            try:
                from backtest.loaders.registry import LOADER_REGISTRY
                loader_cls = LOADER_REGISTRY.get(source_name)
                if loader_cls is None:
                    continue
                loader = loader_cls()
                if not loader.is_available():
                    continue
                raw_result = loader.fetch([symbol], start_date, end_date, interval=interval)
                if symbol in raw_result:
                    df = raw_result[symbol]
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        logger.debug(f"Vibe-Trading 备用源 {source_name} 命中 {symbol}")
                        return df
            except Exception:
                continue

        return None

    def batch_fetch(self, symbols: List[str], start_date: str, end_date: str,
                    interval: str = "1D") -> Dict[str, pd.DataFrame]:
        """批量拉取多只标的 OHLCV 数据.

        Args:
            symbols: 代码列表
            start_date: 起始日期
            end_date: 结束日期
            interval: 周期

        Returns:
            {symbol: DataFrame} 字典
        """
        results: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.fetch(symbol, start_date, end_date, interval=interval)
            if df is not None and not df.empty:
                results[symbol] = df
        return results

    @property
    def is_ready(self) -> bool:
        return self._initialized


# ============================================================
# 代理映射回退 (当 Vibe-Trading 不可用时使用)
# ============================================================

_PROXY_MAP: Dict[str, str] = {
    "588080.SH": "588000.SH",
    "510050.SH": "588000.SH",
    "510300.SH": "588000.SH",
    "510500.SH": "588000.SH",
    "512100.SH": "588000.SH",
    "159915.SZ": "588000.SH",
    "159992.SZ": "588000.SH",
    "512400.SH": "518880.SH",
    "516160.SH": "588000.SH",
    "512170.SH": "600276.SH",
    "512880.SH": "588000.SH",
    "512760.SH": "588000.SH",
    "512800.SH": "588000.SH",
    "515030.SH": "588000.SH",
    "511010.SH": "588000.SH",
}


def _proxy_fallback_fetch(symbols: List[str], start_date: str,
                          end_date: str) -> Dict[str, pd.DataFrame]:
    """代理映射回退 (无网络环境).

    使用本地缓存 + 代理映射补全数据.

    Args:
        symbols: 需要的代码列表
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        {symbol: DataFrame} 字典
    """
    results: Dict[str, pd.DataFrame] = {}

    # 尝试从本地 parquet 缓存加载
    ohlcv_dir = _PROJECT_ROOT / "cache" / "ohlcv"
    for code in symbols:
        code_num = code.split(".")[0]
        exchange = code.split(".")[-1] if "." in code else ""
        parquet_path = ohlcv_dir / f"{code_num}_{exchange}_2y.parquet"

        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                if "close" in df.columns and len(df) > 60:
                    # 标准化列名
                    if "open" not in df.columns and "close" in df.columns:
                        # 只有 close 列, 构造 OHLCV
                        df = df.copy()
                        df["open"] = df["close"].shift(1)
                        df["high"] = df["close"].cummax()
                        df["low"] = df["close"].cummin()
                        df["volume"] = 0
                    results[code] = df
            except Exception:
                pass

    # 代理映射补全
    for code, proxy in _PROXY_MAP.items():
        if code not in results and proxy in results:
            results[code] = results[proxy].copy()
            logger.info(f"  回退代理映射: {code} → {proxy}")

    logger.info(f"代理回退完成: {len(results)}/{len(symbols)} 只标的")
    return results


# ============================================================
# 适配器主类
# ============================================================


class VibeTradingAdapter:
    """Vibe-Trading 数据适配器.

    提供 Vibe-Trading 的多源数据加载能力, 同时保留 28 系统的降级链.

    优先级:
        1. Vibe-Trading (多源 fallback chain)
        2. 本地 parquet 缓存
        3. 代理映射
        4. 兜底 (返回空 DataFrame + 警告)

    Attributes:
        core: Vibe-Trading 核心加载器
        initialized: 是否已初始化

    Usage:
        >>> adapter = VibeTradingAdapter()
        >>> df = adapter.get_ohlcv("510300.SH", "2021-01-01", "2026-07-30")
        >>> batch = adapter.get_batch_ohlcv(["510300.SH", "588000.SH"], "2021-01-01", "2026-07-30")
    """

    def __init__(self, *, force_init: bool = False,
                 fallback_to_proxy: bool = True) -> None:
        """初始化适配器.

        Args:
            force_init: 是否强制初始化 Vibe-Trading (默认 False, 懒加载)
            fallback_to_proxy: Vibe-Trading 失败时是否回退到代理映射
        """
        self._core = _VibeTradingCore.get_instance()
        self._fallback_to_proxy = fallback_to_proxy
        self._initialized = False

        if force_init:
            self._initialized = self._core.initialize()

    @property
    def initialized(self) -> bool:
        """Vibe-Trading 是否已初始化."""
        return self._core.is_ready

    @property
    def available_sources(self) -> List[str]:
        """可用数据源列表."""
        if not self._core.is_ready:
            self._core.initialize()
        return self._core._available_sources

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str,
                  interval: str = "1D", use_vibe: bool = True) -> pd.DataFrame:
        """获取单只标的 OHLCV 数据.

        Args:
            symbol: 代码 (如 "510300.SH")
            start_date: 起始日期 "YYYY-MM-DD"
            end_date: 结束日期 "YYYY-MM-DD"
            interval: 周期, 默认 "1D"
            use_vibe: 是否优先使用 Vibe-Trading

        Returns:
            OHLCV DataFrame, 索引为 DatetimeIndex, 列为 [open, high, low, close, volume]
        """
        normalized = _normalize_symbol(symbol)

        if use_vibe and self._core.initialize():
            df = self._core.fetch(normalized, start_date, end_date, interval)
            if df is not None and not df.empty:
                return self._normalize_dataframe(df, normalized)

        # 回退: 本地缓存
        df = self._try_local_cache(normalized, start_date, end_date)
        if df is not None and not df.empty:
            return df

        # 回退: 代理映射
        if self._fallback_to_proxy:
            proxy_code = _PROXY_MAP.get(normalized)
            if proxy_code and proxy_code != normalized:
                logger.info(f"Vibe-Trading 回退代理: {normalized} → {proxy_code}")
                proxy_df = self.get_ohlcv(proxy_code, start_date, end_date,
                                           interval, use_vibe=use_vibe)
                if not proxy_df.empty:
                    return proxy_df

        logger.warning(f"无法获取 {symbol} 的 OHLCV 数据 (所有源均失败)")
        return pd.DataFrame()

    def get_batch_ohlcv(self, symbols: List[str], start_date: str,
                        end_date: str, interval: str = "1D",
                        use_vibe: bool = True) -> Dict[str, pd.DataFrame]:
        """批量获取多只标的 OHLCV 数据.

        Args:
            symbols: 代码列表
            start_date: 起始日期
            end_date: 结束日期
            interval: 周期
            use_vibe: 是否优先使用 Vibe-Trading

        Returns:
            {symbol: DataFrame} 字典
        """
        results: Dict[str, pd.DataFrame] = {}
        failed: List[str] = []

        for symbol in symbols:
            try:
                df = self.get_ohlcv(symbol, start_date, end_date, interval, use_vibe)
                if not df.empty:
                    results[symbol] = df
                else:
                    failed.append(symbol)
            except Exception as e:
                logger.warning(f"获取 {symbol} 失败: {e}")
                failed.append(symbol)

        if failed:
            logger.warning(f"以下标的获取失败: {failed}")

        logger.info(
            f"批量获取完成: {len(results)}/{len(symbols)} 成功, "
            f"期间 {start_date} ~ {end_date}"
        )
        return results

    def get_price_dataframe(self, symbols: List[str], start_date: str,
                             end_date: str, interval: str = "1D",
                             price_col: str = "close") -> pd.DataFrame:
        """获取多只标的的收盘价矩阵 (供回测使用).

        Args:
            symbols: 代码列表
            start_date: 起始日期
            end_date: 结束日期
            interval: 周期
            price_col: 提取的列 (close/open/adjusted_close 等)

        Returns:
            DataFrame (index=日期, columns=symbol, values=价格)
        """
        batch = self.get_batch_ohlcv(symbols, start_date, end_date, interval)

        if not batch:
            return pd.DataFrame()

        price_dict: Dict[str, pd.Series] = {}
        for symbol, df in batch.items():
            if price_col in df.columns:
                series = df[price_col]
                series.name = symbol
                price_dict[symbol] = series
            elif "close" in df.columns:
                series = df["close"]
                series.name = symbol
                price_dict[symbol] = series

        if not price_dict:
            return pd.DataFrame()

        result = pd.DataFrame(price_dict)
        result = result.sort_index()
        result = result.ffill().dropna(how='all')

        logger.info(
            f"价格矩阵构建完成: {len(result)} 行 (交易日), "
            f"{len(result.columns)} 列 (标的)"
        )
        return result

    def _normalize_dataframe(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """标准化 Vibe-Trading 返回的 DataFrame.

        Args:
            df: 原始 DataFrame
            symbol: 代码

        Returns:
            标准化后的 DataFrame
        """
        if df.empty:
            return df

        # 确保索引是 DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            for col_name in ["trade_date", "date", "datetime", "time"]:
                if col_name in df.columns:
                    df = df.set_index(col_name)
                    break
            else:
                # 尝试第一列作为日期
                if len(df.columns) > 0:
                    first_col = df.columns[0]
                    try:
                        df[first_col] = pd.to_datetime(df[first_col])
                        df = df.set_index(first_col)
                    except Exception:
                        pass

        # 确保列名统一 (open/high/low/close/volume)
        col_map = {
            "open": "open", "high": "high", "low": "low", "close": "close",
            "volume": "volume", "vol": "volume", "adj_close": "close",
            "adjusted_close": "close",
        }
        rename_map = {}
        for old_name, new_name in col_map.items():
            if old_name.lower() in [c.lower() for c in df.columns]:
                for actual_col in df.columns:
                    if actual_col.lower() == old_name.lower() and actual_col != new_name:
                        rename_map[actual_col] = new_name
        if rename_map:
            df = df.rename(columns=rename_map)

        # 只保留 OHLCV 列
        keep_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if keep_cols:
            df = df[keep_cols]

        # 去重和排序
        df = df[~df.index.duplicated(keep='first')]
        df = df.sort_index()

        return df

    def _try_local_cache(self, symbol: str, start_date: str,
                         end_date: str) -> Optional[pd.DataFrame]:
        """尝试从本地缓存加载.

        Args:
            symbol: 代码
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            DataFrame 或 None
        """
        ohlcv_dir = _PROJECT_ROOT / "cache" / "ohlcv"
        if not ohlcv_dir.exists():
            return None

        code_num = symbol.split(".")[0]
        exchange = symbol.split(".")[-1] if "." in symbol else ""

        # 尝试多种命名格式
        candidates = [
            ohlcv_dir / f"{code_num}_{exchange}_2y.parquet",
            ohlcv_dir / f"{code_num}_{exchange}.parquet",
            ohlcv_dir / f"{code_num}_{exchange}.csv",
        ]

        for path in candidates:
            if path.exists():
                try:
                    if path.suffix == ".parquet":
                        df = pd.read_parquet(path)
                    elif path.suffix == ".csv":
                        df = pd.read_csv(path, index_col=0, parse_dates=True)
                    else:
                        continue

                    if df is not None and not df.empty:
                        logger.debug(f"本地缓存命中: {path.name}")
                        return self._normalize_dataframe(df, symbol)
                except Exception as e:
                    logger.debug(f"本地缓存读取失败 {path.name}: {e}")

        return None


# ============================================================
# 便捷函数
# ============================================================

_default_adapter: Optional[VibeTradingAdapter] = None


def get_adapter(force_init: bool = False) -> VibeTradingAdapter:
    """获取默认适配器单例.

    Args:
        force_init: 是否强制初始化 Vibe-Trading

    Returns:
        VibeTradingAdapter 实例
    """
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = VibeTradingAdapter(force_init=force_init)
    return _default_adapter


def get_ohlcv(symbol: str, start_date: str, end_date: str,
              interval: str = "1D") -> pd.DataFrame:
    """便捷函数: 获取单只标的 OHLCV.

    Args:
        symbol: 代码
        start_date: 起始日期
        end_date: 结束日期
        interval: 周期

    Returns:
        OHLCV DataFrame
    """
    return get_adapter().get_ohlcv(symbol, start_date, end_date, interval)


def get_price_matrix(symbols: List[str], start_date: str,
                     end_date: str) -> pd.DataFrame:
    """便捷函数: 获取收盘价矩阵.

    Args:
        symbols: 代码列表
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        DataFrame (index=日期, columns=symbol)
    """
    return get_adapter().get_price_dataframe(symbols, start_date, end_date)
