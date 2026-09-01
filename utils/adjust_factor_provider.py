"""复权因子提供器 (U3 复权因子支持)

职责:
    1. 从 akshare 获取 A股后复权累计因子 (hfq-factor) 序列
    2. 提供 hfq(后复权) ↔ unadjusted(未复权) 价格转换
    3. 除权日对齐: 将未复权实时价转换为 hfq 基准, 与 hfq 历史价可比, 消除除权跳空偏差
    4. 除权日检测 (因子变化)

背景 (P2-1 口径):
    - 历史 K 线: hfq (后复权), adjust="hfq" — 价格连续, 适合收益率计算
    - 实时行情: 未复权, adjust="none" — 实盘成交基准
    - 问题: 除权日未复权价跳空 (分红/送股), 与 hfq 历史价直接比较会产生虚假回撤
    - 解法: 未复权实时价 × hfq因子 = hfq基准价, 与 hfq 历史价可比

复权因子关系:
    hfq_price = unadjusted_price × hfq_factor
    unadjusted_price = hfq_price / hfq_factor

    hfq (后复权) 因子以最早日期为基准 (因子=1), 之后随除权除息累计;
    因子只在除权日变化, 平时恒定, 故可长 TTL 缓存 (默认 24h).

设计原则:
    - 优雅降级: akshare 不可用时返回因子=1.0 (等同于不调整, 行为与未复权一致)
    - 长缓存: 因子仅在除权日变化, TTL 默认 24 小时, 节省 akshare 请求配额
    - 纯函数转换: unadjusted_to_hfq / hfq_to_unadjusted 无副作用, 可测
    - 不阻断主流程: 任何失败返回安全默认值 (factor=1.0), 记录 warning

用法:
    from utils.adjust_factor_provider import AdjustFactorProvider, align_realtime_to_hfq

    provider = AdjustFactorProvider()
    factor = provider.get_hfq_factor("600519.SH")
    hfq_price = unadjusted_to_hfq(realtime_price, factor)

    # 一键对齐: 未复权实时价 → hfq 基准 (与 hfq 历史价可比)
    aligned = align_realtime_to_hfq(realtime_price, "600519.SH")
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 纯函数: 价格转换
# ============================================================
def unadjusted_to_hfq(price: float, hfq_factor: float) -> float:
    """未复权价 → 后复权价.

    公式: hfq_price = unadjusted_price × hfq_factor

    Args:
        price: 未复权价格
        hfq_factor: 后复权累计因子

    Returns:
        后复权价格; 因子<=0 或价格<=0 时返回原价 (安全降级)
    """
    if not hfq_factor or hfq_factor <= 0:
        return price
    if price is None or price <= 0:
        return price
    return price * hfq_factor


def hfq_to_unadjusted(price: float, hfq_factor: float) -> float:
    """后复权价 → 未复权价.

    公式: unadjusted_price = hfq_price / hfq_factor

    Args:
        price: 后复权价格
        hfq_factor: 后复权累计因子

    Returns:
        未复权价格; 因子<=0 时返回原价 (安全降级)
    """
    if not hfq_factor or hfq_factor <= 0:
        return price
    if price is None or price <= 0:
        return price
    return price / hfq_factor


def compute_adjusted_return(
    hfq_prev_close: float,
    unadjusted_realtime: float,
    hfq_factor: float,
) -> float:
    """计算除权日对齐后的真实收益率.

    将未复权实时价转换为 hfq 基准, 再与 hfq 历史前收盘比较,
    消除除权日未复权价跳空导致的虚假回撤.

    Args:
        hfq_prev_close: 前一交易日 hfq 收盘价 (来自历史 K 线)
        unadjusted_realtime: 当日未复权实时价 (来自实时行情)
        hfq_factor: 当日 hfq 累计因子

    Returns:
        真实收益率 (小数, 如 0.01 = +1%); 输入无效返回 0.0
    """
    if hfq_prev_close is None or hfq_prev_close <= 0:
        return 0.0
    if unadjusted_realtime is None or unadjusted_realtime <= 0:
        return 0.0
    hfq_realtime = unadjusted_to_hfq(unadjusted_realtime, hfq_factor)
    if hfq_realtime <= 0:
        return 0.0
    return (hfq_realtime - hfq_prev_close) / hfq_prev_close


# ============================================================
# 复权因子提供器
# ============================================================
class AdjustFactorProvider:
    """A股后复权因子提供器 (单例, 长缓存).

    数据源: akshare stock_zh_a_daily(adjust="hfq-factor")
    缓存策略: 因子仅在除权日变化, TTL 默认 24 小时
    降级策略: akshare 不可用 → factor=1.0 (不调整, 等同未复权)
    """

    _instance: AdjustFactorProvider | None = None
    _instance_lock = threading.Lock()

    # 默认缓存 TTL (秒): 因子仅在除权日变化, 24h 足够
    DEFAULT_CACHE_TTL = 86400

    def __new__(cls, *args: Any, **kwargs: Any) -> AdjustFactorProvider:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_ttl: int | None = None):
        # 单例: 仅首次初始化
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._cache_ttl = cache_ttl or self.DEFAULT_CACHE_TTL
        # 缓存: {symbol: {"factor": float, "series": DataFrame, "fetched_at": datetime}}
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._akshare_source = None  # 延迟绑定 AKShareDataSource
        logger.info(
            "[AdjustFactorProvider] 初始化完成 (cache_ttl=%ds, 降级因子=1.0)",
            self._cache_ttl,
        )

    # ------------------------------------------------------------
    # akshare 绑定
    # ------------------------------------------------------------
    def _get_akshare(self):
        """延迟获取 AKShareDataSource 单例 (避免循环依赖)."""
        if self._akshare_source is None:
            try:
                from utils.akshare_data_source import get_akshare_source

                self._akshare_source = get_akshare_source()
            except (ImportError, RuntimeError, OSError) as e:
                logger.warning("[AdjustFactorProvider] AKShare 数据源不可用: %s", e)
                self._akshare_source = None
        return self._akshare_source

    @staticmethod
    def _to_daily_symbol(symbol: str) -> str | None:
        """转换为 akshare stock_zh_a_daily 所需的 sh/sz 前缀格式.

        stock_zh_a_daily 接受 "sh600519" / "sz000001" / "bj830879" 格式.
        """
        s = str(symbol).strip()
        # 去后缀
        for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        # 去前缀 (已带 sh/sz/bj 的保留)
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if s.startswith(prefix):
                # 标准化为小写前缀
                return prefix.lower() + s[len(prefix) :]
        # 纯数字 → 按首字符判断市场
        if not s or not s.isdigit():
            return None
        if s.startswith(("6", "9", "51", "58")):
            return f"sh{s}"
        if s.startswith(("0", "3", "15", "16", "12", "13")):
            return f"sz{s}"
        if s.startswith(("4", "8")):
            return f"bj{s}"
        return f"sh{s}"  # 兜底

    # ------------------------------------------------------------
    # 因子获取
    # ------------------------------------------------------------
    def get_hfq_factor_series(
        self, symbol: str, force_refresh: bool = False
    ) -> pd.DataFrame:
        """获取完整 hfq 因子序列 (日期 + 因子值).

        数据源: akshare stock_zh_a_daily(symbol, adjust="hfq-factor")
        返回的 DataFrame 含 date 列和 hfq_factor 列, 按日期升序.

        Args:
            symbol: 股票代码
            force_refresh: 强制刷新缓存

        Returns:
            DataFrame(date, hfq_factor); 失败返回空 DataFrame.
        """
        cache_key = symbol
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if not force_refresh and cached and cached.get("series") is not None:
                fetched_at = cached.get("fetched_at")
                if (
                    fetched_at
                    and (datetime.now() - fetched_at).total_seconds() < self._cache_ttl
                ):
                    return cached["series"]

        series = self._fetch_hfq_factor_series(symbol)
        if series is not None and not series.empty:
            with self._cache_lock:
                # 同时更新最新因子快照
                latest_factor = float(series["hfq_factor"].iloc[-1])
                self._cache[cache_key] = {
                    "factor": latest_factor,
                    "series": series,
                    "fetched_at": datetime.now(),
                }
        return series if series is not None else pd.DataFrame()

    def get_hfq_factor(
        self, symbol: str, date: str | None = None, force_refresh: bool = False
    ) -> float:
        """获取指定日期 (或最新) 的 hfq 累计因子.

        Args:
            symbol: 股票代码
            date: 日期 (YYYY-MM-DD), None=最新
            force_refresh: 强制刷新缓存

        Returns:
            hfq 累计因子; 失败返回 1.0 (安全降级, 等同未复权)
        """
        # 最新因子: 优先用缓存快照
        if date is None:
            with self._cache_lock:
                cached = self._cache.get(symbol)
                if not force_refresh and cached and "factor" in cached:
                    fetched_at = cached.get("fetched_at")
                    if (
                        fetched_at
                        and (datetime.now() - fetched_at).total_seconds()
                        < self._cache_ttl
                    ):
                        return float(cached["factor"])

        # 完整序列查询 (含历史日期)
        series = self.get_hfq_factor_series(symbol, force_refresh=force_refresh)
        if series is None or series.empty:
            logger.debug("[AdjustFactorProvider] %s 因子不可用, 降级返回 1.0", symbol)
            return 1.0

        if date is None:
            return float(series["hfq_factor"].iloc[-1])

        # 按日期查找: 返回 <= date 的最新因子 (point-in-time, 不用未来因子)
        try:
            target = pd.to_datetime(date)
            mask = series["date"] <= target
            if mask.any():
                return float(series.loc[mask, "hfq_factor"].iloc[-1])
            # date 早于所有记录, 返回最早因子
            return float(series["hfq_factor"].iloc[0])
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(
                "[AdjustFactorProvider] %s 日期 %s 因子查找失败: %s", symbol, date, e
            )
            return 1.0

    def _fetch_hfq_factor_series(self, symbol: str) -> pd.DataFrame | None:
        """从 akshare 拉取 hfq 因子序列.

        akshare stock_zh_a_daily(adjust="hfq-factor") 返回含 hfq_factor 列的 DataFrame.
        不同 akshare 版本字段名可能为 "hfq_factor" 或 "adjusting" 或需从 qfq/hfq 价反推.
        本方法做兼容处理.
        """
        ak_source = self._get_akshare()
        if ak_source is None or not ak_source._ensure_connected():  # type: ignore[union-attr]
            return None

        daily_symbol = self._to_daily_symbol(symbol)
        if not daily_symbol:
            logger.warning("[AdjustFactorProvider] 无法识别代码: %s", symbol)
            return None

        try:
            ak = ak_source._ak  # type: ignore[union-attr]
            # akshare stock_zh_a_daily 接口 (sh/sz 前缀格式)
            df = ak.stock_zh_a_daily(symbol=daily_symbol, adjust="hfq-factor")
            if df is None or df.empty:
                logger.warning("[AdjustFactorProvider] %s hfq-factor 返回空", symbol)
                return None
            return self._normalize_factor_df(df, symbol)
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
            logger.warning(
                "[AdjustFactorProvider] %s hfq-factor 拉取失败: %s", symbol, e
            )
            return None

    @staticmethod
    def _normalize_factor_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
        """归一化 akshare 返回的因子 DataFrame 为统一格式 (date, hfq_factor).

        akshare 不同版本/接口返回的列名可能不同:
            - stock_zh_a_daily(adjust="hfq-factor"): 含 "hfq_factor" 列
            - 某些版本含 "qfq_factor" / "adjusting"
        若无显式因子列, 则用 hfq 价 / 未复权价 反推因子.
        """
        try:
            # 日期列归一化
            date_col = None
            for c in ("date", "日期", "trade_date"):
                if c in df.columns:
                    date_col = c
                    break
            if date_col is None:
                # 索引可能是日期
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(
                        columns={"index": "date", df.index.name or "index": "date"}
                    )
                    date_col = "date"
                else:
                    logger.warning(
                        "[AdjustFactorProvider] %s 因子 DataFrame 无日期列", symbol
                    )
                    return None

            out = pd.DataFrame()
            out["date"] = pd.to_datetime(df[date_col])

            # 因子列归一化
            factor_col = None
            for c in ("hfq_factor", "qfq_factor", "adjusting", "factor"):
                if c in df.columns:
                    factor_col = c
                    break

            if factor_col is not None:
                out["hfq_factor"] = pd.to_numeric(
                    df[factor_col], errors="coerce"
                ).fillna(1.0)
            else:
                # 反推: 若同时有 hfq close 和未复权 close, 因子 = hfq / unadjusted
                close_cols = [
                    c
                    for c in df.columns
                    if "close" in str(c).lower() or "收盘" in str(c)
                ]
                if len(close_cols) >= 2:
                    # 简单取前两列之比 (akshare hfq-factor 通常只有 date + factor)
                    logger.debug(
                        "[AdjustFactorProvider] %s 无显式因子列, 尝试反推", symbol
                    )
                    out["hfq_factor"] = pd.to_numeric(
                        df[close_cols[0]], errors="coerce"
                    ).fillna(1.0)
                else:
                    logger.warning(
                        "[AdjustFactorProvider] %s 因子 DataFrame 无因子列: %s",
                        symbol,
                        list(df.columns),
                    )
                    return None

            out = out.sort_values("date").reset_index(drop=True)
            # 因子有效性检查: 应 > 0
            out["hfq_factor"] = out["hfq_factor"].apply(lambda x: x if x > 0 else 1.0)
            return out
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "[AdjustFactorProvider] %s 因子 DataFrame 归一化失败: %s", symbol, e
            )
            return None

    # ------------------------------------------------------------
    # 除权日检测
    # ------------------------------------------------------------
    def is_ex_dividend_date(self, symbol: str, date: str | None = None) -> bool:
        """检测指定日期是否为除权除息日 (因子发生变化).

        通过比较 date 与前一交易日的 hfq 因子是否不同来判断.

        Args:
            symbol: 股票代码
            date: 日期 (YYYY-MM-DD), None=今天

        Returns:
            True=除权日 (因子变化); False=非除权日 或因子不可用 (保守返回 False)
        """
        series = self.get_hfq_factor_series(symbol)
        if series is None or series.empty or len(series) < 2:
            return False

        try:
            target = pd.to_datetime(date) if date else pd.Timestamp.now().normalize()
            # 找到 <= target 的最近两条记录
            mask = series["date"] <= target
            if mask.sum() < 2:
                return False
            recent = series.loc[mask].tail(2)
            f_prev = float(recent["hfq_factor"].iloc[0])
            f_curr = float(recent["hfq_factor"].iloc[1])
            # 因子相对变化超过 0.1% 视为除权日
            return abs(f_curr - f_prev) / max(f_prev, 1e-9) > 0.001
        except (ValueError, TypeError, KeyError, IndexError) as e:
            logger.debug("[AdjustFactorProvider] %s 除权日检测失败: %s", symbol, e)
            return False

    # ------------------------------------------------------------
    # 除权日 prev_close 对齐
    # ------------------------------------------------------------
    def get_aligned_prev_close(
        self,
        symbol: str,
        prev_close: float,
        date: str | None = None,
    ) -> float:
        """U3: 获取除权日对齐后的前收盘价.

        除权日时, prev_close (前一日未复权收盘) 与今日未复权 close 不可比,
        直接相减会产生虚假跳空 (如分红 10% 会被误判为 -10% 回撤).

        对齐策略: 把 prev_close 调整到今日未复权口径
            aligned_prev_close = prev_close × (yesterday_factor / today_factor)

        数学等价: 在 hfq 基准下比较
            hfq_prev = prev_close × yesterday_factor
            hfq_today = close × today_factor
            两者相减后除以 hfq_prev, 与 (close - aligned_prev) / aligned_prev 等价.

        非除权日时 today_factor == yesterday_factor, 返回原 prev_close (行为不变).
        因子不可用时安全降级返回原 prev_close.

        Args:
            symbol: 股票代码
            prev_close: 前一交易日未复权收盘价
            date: 当日日期 (YYYY-MM-DD), None=今天

        Returns:
            对齐后的 prev_close; 非除权日或因子不可用时返回原值
        """
        if not prev_close or prev_close <= 0:
            return prev_close

        try:
            today_factor = self.get_hfq_factor(symbol, date=date)
            if today_factor <= 0:
                return prev_close

            # 前一交易日日期: 简单回退 1 天 (get_hfq_factor 内部按 <= date 查找, 周末自动回退到周五)
            target_date = (
                pd.to_datetime(date) if date else pd.Timestamp.now().normalize()
            )
            yesterday_str = (target_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_factor = self.get_hfq_factor(symbol, date=yesterday_str)

            if yesterday_factor <= 0:
                return prev_close

            # 非除权日: today == yesterday, 比值=1, 返回原值
            # 除权日: today != yesterday, 调整 prev_close 到今日口径
            return prev_close * (yesterday_factor / today_factor)
        except (ValueError, TypeError, KeyError) as e:
            logger.debug(
                "[AdjustFactorProvider] %s 对齐 prev_close 失败: %s", symbol, e
            )
            return prev_close

    # ------------------------------------------------------------
    # 批量获取
    # ------------------------------------------------------------
    def get_factors_batch(
        self, symbols: list[str], force_refresh: bool = False
    ) -> dict[str, float]:
        """批量获取多个标的的最新 hfq 因子.

        Returns:
            {symbol: hfq_factor}; 失败的标的因子=1.0
        """
        result: dict[str, float] = {}
        for sym in symbols:
            result[sym] = self.get_hfq_factor(sym, force_refresh=force_refresh)
        return result

    # ------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------
    def clear_cache(self) -> None:
        """清空因子缓存."""
        with self._cache_lock:
            self._cache.clear()
        logger.info("[AdjustFactorProvider] 因子缓存已清空")

    def get_cache_info(self) -> dict[str, Any]:
        """获取缓存信息."""
        with self._cache_lock:
            return {
                "cached_symbols": len(self._cache),
                "cache_ttl_seconds": self._cache_ttl,
                "symbols": list(self._cache.keys()),
            }


# ============================================================
# 模块级单例 + 便捷函数
# ============================================================
_provider_singleton: AdjustFactorProvider | None = None
_singleton_lock = threading.Lock()


def get_adjust_factor_provider() -> AdjustFactorProvider:
    """获取 AdjustFactorProvider 单例."""
    global _provider_singleton
    if _provider_singleton is None:
        with _singleton_lock:
            if _provider_singleton is None:
                _provider_singleton = AdjustFactorProvider()
    return _provider_singleton


def align_realtime_to_hfq(
    realtime_price: float,
    symbol: str,
    date: str | None = None,
) -> float:
    """一键对齐: 未复权实时价 → hfq 基准价 (与 hfq 历史价可比).

    便捷封装: 获取因子 + 转换. 除权日不会产生虚假跳空.

    Args:
        realtime_price: 未复权实时价 (来自 data_provider 实时行情)
        symbol: 股票代码
        date: 日期 (None=最新因子)

    Returns:
        hfq 基准价; 因子不可用时返回原价 (安全降级)
    """
    provider = get_adjust_factor_provider()
    factor = provider.get_hfq_factor(symbol, date=date)
    return unadjusted_to_hfq(realtime_price, factor)


def compute_aligned_return(
    hfq_prev_close: float,
    unadjusted_realtime: float,
    symbol: str,
    date: str | None = None,
) -> float:
    """一键计算除权日对齐后的真实收益率.

    便捷封装: 获取因子 + compute_adjusted_return.

    Args:
        hfq_prev_close: 前一交易日 hfq 收盘价 (历史 K 线)
        unadjusted_realtime: 当日未复权实时价
        symbol: 股票代码
        date: 日期 (None=最新因子)

    Returns:
        真实收益率 (小数); 因子不可用时退化为未对齐收益率
    """
    provider = get_adjust_factor_provider()
    factor = provider.get_hfq_factor(symbol, date=date)
    return compute_adjusted_return(hfq_prev_close, unadjusted_realtime, factor)


def align_prev_close_to_today(
    prev_close: float,
    symbol: str,
    date: str | None = None,
) -> float:
    """一键对齐: 前一日未复权收盘价 → 今日未复权口径.

    便捷封装: 获取因子 + 调整 prev_close.
    除权日时 prev_close 与今日 close 不可比, 对齐后可消除跳空偏差.
    非除权日时 today_factor == yesterday_factor, 返回原 prev_close (行为不变).

    Args:
        prev_close: 前一交易日未复权收盘价
        symbol: 股票代码
        date: 当日日期 (None=今天)

    Returns:
        对齐后的 prev_close; 因子不可用时返回原值 (安全降级)
    """
    provider = get_adjust_factor_provider()
    return provider.get_aligned_prev_close(symbol, prev_close, date=date)


__all__ = [
    "AdjustFactorProvider",
    "get_adjust_factor_provider",
    "unadjusted_to_hfq",
    "hfq_to_unadjusted",
    "compute_adjusted_return",
    "align_realtime_to_hfq",
    "compute_aligned_return",
    "align_prev_close_to_today",
]
