"""A股涨停池/跌停池数据提供器 (W6.6.3 P1-a)

从东方财富涨停板数据接口获取当日涨停/跌停股票池, 与基于 prev_close 计算的涨跌停价
互为双保险. 交易所涨停板数据更准确 (含炸板/连板信息), 但仅提供当日数据, 历史数据
需付费或使用计算近似.

数据源: akshare
    - ak.stock_zt_pool_em(date)     涨停板池
    - ak.stock_zt_pool_dt_em(date)  跌停板池
    - ak.stock_zt_pool_zbgc_em(date) 炸板股池 (涨停后打开)

缓存策略: 当日数据, TTL 默认 300 秒 (5 分钟, 盘中刷新)
降级策略: akshare 不可用 → 返回空集合, 不影响回测 (回退到 price_limit_calculator 计算)

设计原则 (与 adjust_factor_provider.py 一致):
    - 单例 + 线程安全
    - 延迟绑定 AKShareDataSource (避免循环依赖)
    - Fail-Open: 数据源不可用时返回空集合, 不抛异常

用法:
    from utils.limit_pool_provider import LimitPoolProvider

    provider = LimitPoolProvider()
    zt_codes = provider.get_limit_up_pool("20260812")
    dt_codes = provider.get_limit_down_pool("20260812")
    zbgc_codes = provider.get_broken_pool("20260812")

    # 批量获取 (回测预热)
    pools = provider.get_pools_batch(["20260810", "20260811", "20260812"])
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


class LimitPoolData:
    """单日涨停/跌停池数据"""

    def __init__(
        self,
        date: str,
        limit_up_codes: set[str] | None = None,
        limit_down_codes: set[str] | None = None,
        broken_codes: set[str] | None = None,
        limit_up_detail: list[dict[str, Any]] | None = None,
        limit_down_detail: list[dict[str, Any]] | None = None,
    ):
        self.date = date
        self.limit_up_codes = limit_up_codes or set()
        self.limit_down_codes = limit_down_codes or set()
        self.broken_codes = broken_codes or set()
        # 详情 (含涨停原因/连板数/涨停时长等, 供因子挖掘)
        self.limit_up_detail = limit_up_detail or []
        self.limit_down_detail = limit_down_detail or []

    @property
    def n_limit_up(self) -> int:
        return len(self.limit_up_codes)

    @property
    def n_limit_down(self) -> int:
        return len(self.limit_down_codes)

    @property
    def n_broken(self) -> int:
        return len(self.broken_codes)

    def is_limit_up(self, code: str) -> bool:
        """检查股票是否在涨停池中"""
        return code in self.limit_up_codes

    def is_limit_down(self, code: str) -> bool:
        """检查股票是否在跌停池中"""
        return code in self.limit_down_codes

    def is_broken(self, code: str) -> bool:
        """检查股票是否在炸板池中 (涨停后打开)"""
        return code in self.broken_codes

    def __repr__(self) -> str:
        return (
            f"LimitPoolData(date={self.date}, "
            f"涨停={self.n_limit_up}, 跌停={self.n_limit_down}, 炸板={self.n_broken})"
        )


# ============================================================
# 涨停池/跌停池提供器
# ============================================================


class LimitPoolProvider:
    """A股涨停池/跌停池数据提供器 (单例, 短缓存).

    数据源: akshare stock_zt_pool_em / stock_zt_pool_dt_em / stock_zt_pool_zbgc_em
    缓存策略: 盘中 TTL 300 秒 (5 分钟), 盘后 24 小时
    降级策略: akshare 不可用 → 返回空 LimitPoolData, 不影响回测
    """

    _instance: LimitPoolProvider | None = None
    _instance_lock = threading.Lock()

    # 盘中 TTL (300s), 盘后 TTL (86400s)
    INTRADAY_TTL = 300
    POST_MARKET_TTL = 86400

    def __new__(cls, *args: Any, **kwargs: Any) -> LimitPoolProvider:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_ttl: int | None = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._cache_ttl = cache_ttl or self.INTRADAY_TTL
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._akshare_source = None
        logger.info(
            "[LimitPoolProvider] 初始化完成 (cache_ttl=%ds, 降级=空池)",
            self._cache_ttl,
        )

    # ------------------------------------------------------------
    # akshare 绑定
    # ------------------------------------------------------------
    def _get_akshare(self):
        """延迟获取 AKShareDataSource 单例"""
        if self._akshare_source is None:
            try:
                from utils.akshare_data_source import get_akshare_source

                self._akshare_source = get_akshare_source()
            except (ImportError, RuntimeError, OSError) as e:
                logger.warning("[LimitPoolProvider] AKShare 数据源不可用: %s", e)
                self._akshare_source = None
        return self._akshare_source

    def _get_ak_module(self):
        """直接获取 akshare 模块 (用于涨停池 API)"""
        try:
            import akshare as ak
            return ak
        except ImportError:
            logger.warning("[LimitPoolProvider] akshare 未安装")
            return None

    # ------------------------------------------------------------
    # 日期工具
    # ------------------------------------------------------------
    @staticmethod
    def _normalize_date(date: str | datetime) -> str:
        """归一化日期为 YYYYMMDD 格式 (akshare 涨停池接口要求)"""
        if isinstance(date, datetime):
            return date.strftime("%Y%m%d")
        # 支持 YYYY-MM-DD / YYYYMMDD
        date = date.strip().replace("-", "").replace("/", "")
        return date

    @staticmethod
    def _is_today(date: str) -> bool:
        """判断日期是否为今天"""
        today = datetime.now().strftime("%Y%m%d")
        return date == today

    def _get_ttl(self, date: str) -> int:
        """根据日期选择 TTL: 今天用盘中 TTL, 历史用盘后 TTL"""
        if self._is_today(date):
            # 判断是否在交易时段 (9:25-15:05)
            now = datetime.now()
            if now.weekday() < 5 and 9 <= now.hour <= 15:
                return self.INTRADAY_TTL
        return self.POST_MARKET_TTL

    # ------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------

    def get_limit_up_pool(self, date: str | datetime) -> set[str]:
        """获取涨停股票代码集合"""
        data = self.get_pool(date)
        return data.limit_up_codes

    def get_limit_down_pool(self, date: str | datetime) -> set[str]:
        """获取跌停股票代码集合"""
        data = self.get_pool(date)
        return data.limit_down_codes

    def get_broken_pool(self, date: str | datetime) -> set[str]:
        """获取炸板股票代码集合"""
        data = self.get_pool(date)
        return data.broken_codes

    def get_pool(self, date: str | datetime) -> LimitPoolData:
        """获取单日完整涨停/跌停/炸板池数据

        Args:
            date: 日期 (支持 YYYYMMDD / YYYY-MM-DD / datetime)

        Returns:
            LimitPoolData (数据不可用时返回空池)
        """
        date_str = self._normalize_date(date)

        # 缓存检查
        with self._cache_lock:
            cached = self._cache.get(date_str)
            if cached:
                age = (datetime.now() - cached["fetched_at"]).total_seconds()
                if age < self._get_ttl(date_str):
                    return cached["data"]

        # 获取数据
        pool_data = LimitPoolData(date=date_str)
        ak = self._get_ak_module()

        if ak is None:
            logger.warning("[LimitPoolProvider] akshare 不可用, 返回空池: %s", date_str)
            return pool_data

        # 涨停池
        try:
            df_zt = ak.stock_zt_pool_em(date=date_str)
            if df_zt is not None and len(df_zt) > 0:
                # 代码列可能叫 "代码" 或 "symbol"
                code_col = "代码" if "代码" in df_zt.columns else df_zt.columns[1]
                pool_data.limit_up_codes = set(df_zt[code_col].astype(str).str.zfill(6).tolist())
                pool_data.limit_up_detail = df_zt.to_dict("records")
            logger.info(
                "[LimitPoolProvider] %s 涨停池: %d 只", date_str, pool_data.n_limit_up,
            )
        except Exception as e:
            logger.warning("[LimitPoolProvider] 涨停池获取失败 %s: %s", date_str, e)

        # 跌停池
        try:
            df_dt = ak.stock_zt_pool_dt_em(date=date_str)
            if df_dt is not None and len(df_dt) > 0:
                code_col = "代码" if "代码" in df_dt.columns else df_dt.columns[1]
                pool_data.limit_down_codes = set(df_dt[code_col].astype(str).str.zfill(6).tolist())
                pool_data.limit_down_detail = df_dt.to_dict("records")
            logger.info(
                "[LimitPoolProvider] %s 跌停池: %d 只", date_str, pool_data.n_limit_down,
            )
        except Exception as e:
            logger.warning("[LimitPoolProvider] 跌停池获取失败 %s: %s", date_str, e)

        # 炸板池
        try:
            df_zbgc = ak.stock_zt_pool_zbgc_em(date=date_str)
            if df_zbgc is not None and len(df_zbgc) > 0:
                code_col = "代码" if "代码" in df_zbgc.columns else df_zbgc.columns[1]
                pool_data.broken_codes = set(df_zbgc[code_col].astype(str).str.zfill(6).tolist())
            logger.info(
                "[LimitPoolProvider] %s 炸板池: %d 只", date_str, pool_data.n_broken,
            )
        except Exception as e:
            # 炸板池不是关键数据, 失败不告警
            logger.debug("[LimitPoolProvider] 炸板池获取失败 %s: %s", date_str, e)

        # 写入缓存
        with self._cache_lock:
            self._cache[date_str] = {
                "data": pool_data,
                "fetched_at": datetime.now(),
            }

        return pool_data

    # ------------------------------------------------------------
    # 批量 API (回测预热)
    # ------------------------------------------------------------

    def get_pools_batch(
        self, dates: list[str | datetime]
    ) -> dict[str, LimitPoolData]:
        """批量获取多日涨停池数据 (回测预热)

        Args:
            dates: 日期列表

        Returns:
            {date_str: LimitPoolData}
        """
        result: dict[str, LimitPoolData] = {}
        for date in dates:
            date_str = self._normalize_date(date)
            result[date_str] = self.get_pool(date_str)
        logger.info(
            "[LimitPoolProvider] 批量预热 %d 天涨停池数据完成", len(dates),
        )
        return result

    # ------------------------------------------------------------
    # 与 price_limit_calculator 的交叉校验
    # ------------------------------------------------------------

    def cross_validate_with_calc(
        self,
        date: str | datetime,
        prev_closes: dict[str, float],
        st_codes: set[str] | None = None,
    ) -> dict[str, dict[str, bool]]:
        """用涨停池数据交叉校验基于 prev_close 的计算结果

        Args:
            date: 日期
            prev_closes: {code: prev_close}
            st_codes: ST 股票集合

        Returns:
            {code: {"calc_limit_up": bool, "pool_limit_up": bool, "match": bool}}
        """
        from utils.price_limit_calculator import calc_limit_prices, normalize_code

        pool = self.get_pool(date)
        st_set = {normalize_code(c) for c in (st_codes or set())}

        result: dict[str, dict[str, bool]] = {}
        for code, prev_close in prev_closes.items():
            nc = normalize_code(code)
            is_st = nc in st_set
            lu_calc, _ = calc_limit_prices(prev_close, code, is_st=is_st)
            calc_up = lu_calc > 0
            pool_up = nc in pool.limit_up_codes or code in pool.limit_up_codes
            result[code] = {
                "calc_limit_up": calc_up,
                "pool_limit_up": pool_up,
                "match": calc_up == pool_up,
            }
        return result

    # ------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------

    def clear_cache(self) -> None:
        """清除缓存"""
        with self._cache_lock:
            self._cache.clear()

    def get_cache_info(self) -> dict[str, Any]:
        """获取缓存信息"""
        with self._cache_lock:
            return {
                "cached_dates": list(self._cache.keys()),
                "cache_size": len(self._cache),
                "ttl_intraday": self.INTRADAY_TTL,
                "ttl_post_market": self.POST_MARKET_TTL,
            }


# ============================================================
# 模块级便捷函数
# ============================================================


def get_limit_pool_provider() -> LimitPoolProvider:
    """获取 LimitPoolProvider 单例"""
    return LimitPoolProvider()


def get_limit_up_pool(date: str | datetime) -> set[str]:
    """便捷函数: 获取涨停池"""
    return get_limit_pool_provider().get_limit_up_pool(date)


def get_limit_down_pool(date: str | datetime) -> set[str]:
    """便捷函数: 获取跌停池"""
    return get_limit_pool_provider().get_limit_down_pool(date)
