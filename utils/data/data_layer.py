"""L2 数据层 — 统一数据降级链 P0-P6.

模块整合 8.4 — ARCHITECTURE §2.1 / TASK T1.7

设计目标:
    1. 作为 MarketDataProvider 的门面层 (不破坏现有降级链)
    2. 配置驱动的 P0-P6 降级链, 每级失败自动 fallback 到下一级
    3. 集成 DataGate 数据质量门控 (坏数据不交易)
    4. fallback 审计日志: reports/data_layer/fallback_{date}.jsonl
    5. P6 缓存兜底: 所有数据源失败时返回最近成功数据 + 警告
    6. Feature Flag USE_INTEGRATED_DATA_LAYER=False 时透明转发到 MarketDataProvider

降级链优先级 (P0=最高, P5=最低, 已剔除 iFinD):
    P0: Wind MCP        (生产主源, license 限制)
    P1: 通达信 tdx      (本地客户端, 免费)
    P2: AKShare         (开源, 限流)
    P3: ExternalData    (外部源, 慢)
    P4: 新浪财经         (网页抓取, 不稳定)
    P5: 缓存兜底         (最近成功数据, 警告 stale)

硬约束:
    - HC-1: 不破坏 V9 生产基线 (Feature Flag 默认 False)
    - HC-5: 配置走 ConfigManager 4 级优先级
    - HC-6: P0-P6 降级链每级失败必须记录 fallback 日志

用法:
    from utils.data.data_layer import DataLayer

    layer = DataLayer()  # 默认走 Feature Flag
    df = layer.get_ohlcv("510300.SH", "2026-01-01", "2026-07-26")
    snap = layer.get_snapshot("510300.SH")
    macro = layer.get_macro_indicators()
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

# 复用 ConfigManager 4 级优先级 (HC-5)
from utils.config_manager import get_config
from utils.datetime_utils import now_bj

logger = logging.getLogger("data_layer")

# ============================================================
# 常量
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# fallback 审计日志目录
_FALLBACK_LOG_DIR = _PROJECT_ROOT / "reports" / "data_layer"

# P6 缓存目录
_P6_CACHE_DIR = _PROJECT_ROOT / "data_cache" / "data_layer_p6"

# 降级链级别 (P0=最高优先级, P6=最低)
PROVIDER_LEVELS: list[str] = ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]

# 默认降级链配置 (优先级: 高 → 低, 已剔除 iFinD)
# 与 MarketDataProvider 现有降级链对齐: Wind MCP > 通达信 > AKShare > 新浪
_DEFAULT_FALLBACK_CHAIN: list[dict[str, Any]] = [
    {"level": "P0", "name": "wind_mcp", "description": "Wind MCP (生产主源)"},
    {"level": "P1", "name": "tdx", "description": "通达信本地客户端"},
    {"level": "P2", "name": "akshare", "description": "AKShare 开源"},
    {"level": "P4", "name": "external", "description": "外部数据源 (慢)"},
    {"level": "P5", "name": "sina", "description": "新浪财经网页"},
    {"level": "P6", "name": "cache", "description": "缓存兜底 (stale 警告)"},
]

# 缓存最大有效期 (秒), P6 兜底超过此值拒绝返回
DEFAULT_CACHE_TTL_SECONDS: int = 86400  # 24 小时

# P6 缓存兜底(陈旧数据)命中的质量分: 显式标记为非 100, 提醒下游这是陈旧数据
# 真正的陈旧信号是 QueryResult.from_cache=True, 此处仅避免把陈旧数据误报为"满分新鲜"
STALE_QUALITY_SCORE: float = 0.0


# ============================================================
# 异常
# ============================================================


class DataLayerError(Exception):
    """DataLayer 基础异常."""

    def __init__(self, message: str, *, level: str | None = None, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.level = level
        self.cause = cause


class AllSourcesFailedError(DataLayerError):
    """所有数据源 (含 P6 缓存) 均失败."""


class DataQualityBlockedError(DataLayerError):
    """DataGate 门控拒绝 (坏数据不交易)."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class FallbackRecord:
    """单次 fallback 审计记录."""

    timestamp: str
    symbol: str
    operation: str  # get_ohlcv / get_snapshot / get_macro_indicators
    failed_level: str  # 失败的级别, 如 "P0"
    failed_provider: str  # 失败的 provider name, 如 "wind_mcp"
    next_level: str  # 接管的级别, 如 "P1"
    next_provider: str
    error_type: str  # 异常类型
    error_message: str
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "operation": self.operation,
            "failed_level": self.failed_level,
            "failed_provider": self.failed_provider,
            "next_level": self.next_level,
            "next_provider": self.next_provider,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class QueryResult:
    """DataLayer 查询结果."""

    data: Any  # pd.DataFrame for ohlcv, Dict for snapshot/macro
    provider_level: str  # 实际命中的级别, 如 "P0"
    provider_name: str  # 实际命中的 provider, 如 "wind_mcp"
    from_cache: bool = False  # 是否来自 P6 缓存兜底
    quality_score: float = 100.0  # DataGate 评分
    fallback_chain_used: list[str] = field(default_factory=list)  # 经过的降级路径
    latency_ms: float = 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "provider_level": self.provider_level,
            "provider_name": self.provider_name,
            "from_cache": self.from_cache,
            "quality_score": round(self.quality_score, 2),
            "fallback_chain_used": self.fallback_chain_used,
            "latency_ms": round(self.latency_ms, 2),
        }


# ============================================================
# Provider 抽象 (每个 P 级别一个 callable)
# ============================================================

# Provider callable 签名:
#   fn(symbol: str, **kwargs) -> Any
# 返回:
#   - ohlcv: pd.DataFrame (含 open/high/low/close/volume 列, DatetimeIndex)
#   - snapshot: Dict[str, Any]
#   - macro: Dict[str, Any]
# 失败: 抛任意 Exception


ProviderFn = Callable[..., Any]


# ============================================================
# DataLayer 主类
# ============================================================


class DataLayer:
    """统一数据降级链 (P0-P6).

    线程安全: 内部使用 RLock 保护 fallback 日志和 P6 缓存写入.
    幂等: 多次实例化共享同一份 fallback 日志和 P6 缓存目录.
    """

    def __init__(
        self,
        *,
        fallback_chain: list[dict[str, Any]] | None = None,
        providers: dict[str, ProviderFn] | None = None,
        enable_data_gate: bool = True,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        feature_flag_name: str = "USE_INTEGRATED_DATA_LAYER",
        feature_flag_check: Callable[[], bool] | None = None,
        fallback_log_dir: Path | None = None,
        p6_cache_dir: Path | None = None,
        auto_register_providers: bool = True,
    ) -> None:
        """初始化 DataLayer.

        Args:
            fallback_chain: 自定义降级链配置 (默认使用 _DEFAULT_FALLBACK_CHAIN)
            providers: 自定义 provider 字典 {name: callable} (默认自动注册)
            enable_data_gate: 是否启用 DataGate 数据质量门控
            cache_ttl_seconds: P6 缓存最大有效期 (秒), 超过则拒绝返回
            feature_flag_name: Feature Flag 名称 (默认 USE_INTEGRATED_DATA_LAYER)
            feature_flag_check: 自定义 flag 检查 callable (默认走 FeatureFlags.is_enabled)
            fallback_log_dir: fallback 日志目录 (默认 reports/data_layer/)
            p6_cache_dir: P6 缓存目录 (默认 data_cache/data_layer_p6/)
            auto_register_providers: 是否自动注册默认 provider (默认 True)
        """
        self._lock = threading.RLock()

        # 降级链配置 (走 ConfigManager 4 级优先级, HC-5)
        # 配置优先级: 显式传参 > ConfigManager 加载 > 默认值
        self.fallback_chain: list[dict[str, Any]] = fallback_chain or self._load_fallback_chain_from_config()

        # providers 字典
        self._providers: dict[str, ProviderFn] = {}
        if providers:
            self._providers.update(providers)

        # 自动注册默认 provider (延迟导入避免循环依赖)
        if auto_register_providers:
            self._register_default_providers()

        # DataGate (延迟导入)
        self._data_gate = None
        self.enable_data_gate = enable_data_gate
        if enable_data_gate:
            try:
                from utils.data_gate import DataGate
                self._data_gate = DataGate()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
                logger.warning(f"DataGate 加载失败, 数据质量门控禁用: {e}")
                self.enable_data_gate = False

        # P6 缓存配置
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self._p6_cache_dir = p6_cache_dir or _P6_CACHE_DIR
        self._p6_cache_dir.mkdir(parents=True, exist_ok=True)

        # Feature Flag
        self._feature_flag_name = feature_flag_name
        self._feature_flag_check = feature_flag_check or self._default_flag_check

        # fallback 日志目录
        self._fallback_log_dir = fallback_log_dir or _FALLBACK_LOG_DIR
        self._fallback_log_dir.mkdir(parents=True, exist_ok=True)

        # 内部状态
        self._market_data_provider: Any = None  # 延迟加载 MarketDataProvider

        logger.info(
            f"DataLayer 初始化完成: chain={[p['name'] for p in self.fallback_chain]}, "
            f"data_gate={self.enable_data_gate}, flag={self._feature_flag_name}"
        )

    # ============================================================
    # 公共 API
    # ============================================================

    def get_ohlcv(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        period: str = "1y",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取 OHLCV 日线数据.

        Args:
            symbol: 标的代码, 如 "510300.SH"
            start: 起始日期 "YYYY-MM-DD" (可选)
            end: 结束日期 "YYYY-MM-DD" (可选)
            period: 周期 "1y"/"6m"/"3m" (start/end 为空时使用)
            **kwargs: 透传给 provider

        Returns:
            pd.DataFrame, 含 open/high/low/close/volume 列, DatetimeIndex

        Raises:
            AllSourcesFailedError: 所有数据源失败
            DataQualityBlockedError: DataGate 门控拒绝
        """
        result = self._query_with_fallback(
            operation="get_ohlcv",
            symbol=symbol,
            query_fn=lambda provider_fn: provider_fn(
                symbol, start=start, end=end, period=period, **kwargs
            ),
        )
        df = result.data
        if not isinstance(df, pd.DataFrame):
            logger.warning(f"get_ohlcv 返回非 DataFrame 类型: {type(df)}, 转换为空 DataFrame")
            df = pd.DataFrame()
        return df

    def get_snapshot(self, symbol: str, **kwargs: Any) -> dict[str, Any]:
        """获取实时行情快照.

        Args:
            symbol: 标的代码
            **kwargs: 透传给 provider

        Returns:
            Dict 含 price/pre_close/change_pct 等字段
        """
        result = self._query_with_fallback(
            operation="get_snapshot",
            symbol=symbol,
            query_fn=lambda provider_fn: provider_fn(symbol, **kwargs),
        )
        data = result.data
        if not isinstance(data, dict):
            logger.warning(f"get_snapshot 返回非 dict 类型: {type(data)}, 转换为空 dict")
            data = {}
        return data

    def get_macro_indicators(self, **kwargs: Any) -> dict[str, Any]:
        """获取宏观指标 (CPI/PMI/M2/利率).

        Returns:
            Dict 含多个宏观指标
        """
        result = self._query_with_fallback(
            operation="get_macro_indicators",
            symbol="__macro__",
            query_fn=lambda provider_fn: provider_fn(**kwargs),
        )
        data = result.data
        if not isinstance(data, dict):
            data = {}
        return data

    def list_providers(self) -> list[dict[str, Any]]:
        """列出降级链中所有 provider 的健康状态."""
        with self._lock:
            return [
                {
                    "level": p["level"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "registered": p["name"] in self._providers,
                }
                for p in self.fallback_chain
            ]

    def register_provider(self, name: str, fn: ProviderFn) -> None:
        """注册或覆盖一个 provider callable."""
        with self._lock:
            self._providers[name] = fn
            logger.info(f"Provider 已注册: {name}")

    def get_fallback_log_path(self) -> Path:
        """获取当日 fallback 日志文件路径."""
        date_str = now_bj().strftime("%Y%m%d")
        return self._fallback_log_dir / f"fallback_{date_str}.jsonl"

    # ============================================================
    # 内部实现
    # ============================================================

    def _default_flag_check(self) -> bool:
        """默认 Feature Flag 检查 (走 FeatureFlags.is_enabled)."""
        try:
            from utils.infra.feature_flags import FeatureFlags
            return FeatureFlags.get_instance().is_enabled(self._feature_flag_name)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            # FeatureFlags 不可用, 默认走旧路径 (保守)
            logger.debug(f"FeatureFlags 检查失败, 默认走旧路径: {e}")
            return False

    def _load_fallback_chain_from_config(self) -> list[dict[str, Any]]:
        """从 ConfigManager 加载降级链配置 (HC-5)."""
        try:
            cfg = get_config("data_layer")
            if cfg and isinstance(cfg, dict) and "fallback_chain" in cfg:
                chain = cfg["fallback_chain"]
                if isinstance(chain, list) and chain:
                    logger.info(f"从 ConfigManager 加载降级链: {len(chain)} 级")
                    return chain
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            logger.debug(f"ConfigManager 加载 data_layer 配置失败, 使用默认值: {e}")
        return list(_DEFAULT_FALLBACK_CHAIN)

    def _register_default_providers(self) -> None:
        """注册默认 provider (P0-P5 走 MarketDataProvider, P6 走缓存)."""
        # P0-P5: 委托给 MarketDataProvider (现有降级链已实现)
        # P6: 缓存兜底 (本类实现)
        try:
            # 延迟导入避免循环依赖
            from utils.data_provider import MarketDataProvider
            provider = MarketDataProvider()
            self._market_data_provider = provider

            # P0-P5 共享一个委托函数 (MarketDataProvider 内部已有降级链)
            def _make_delegator(operation: str) -> ProviderFn:
                def _fn(symbol: str, **kwargs: Any) -> Any:
                    if operation == "get_ohlcv":
                        # MarketDataProvider.get_historical_data(symbol, period)
                        period = kwargs.get("period", "1y")
                        df = provider.get_historical_data(symbol, period=period)
                        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                            raise RuntimeError(f"MarketDataProvider 返回空数据 (operation={operation})")
                        return df
                    elif operation == "get_snapshot":
                        snap = provider.get_market_data(symbol)
                        if not snap:
                            raise RuntimeError(f"MarketDataProvider 返回空快照 (operation={operation})")
                        return snap
                    elif operation == "get_macro_indicators":
                        macro = provider.get_external_macro()
                        if not macro:
                            raise RuntimeError("MarketDataProvider 返回空宏观数据")
                        return macro
                    else:
                        raise ValueError(f"未知 operation: {operation}")
                return _fn

            # 为每个 P 级别注册对应 operation 的委托函数
            # 实际生产中, MarketDataProvider 内部已按 P0→P5 降级, 这里只是包装
            for chain_entry in self.fallback_chain:
                level = chain_entry["level"]
                name = chain_entry["name"]
                if level == "P6":
                    # P6 缓存兜底, 稍后单独注册
                    continue
                # 所有 P0-P5 都委托给 MarketDataProvider
                # (生产场景可改为分别调用 wind_mcp/tdx/akshare/external/sina, iFinD 已剔除)
                # 这里简化为统一委托, 真实降级由 MarketDataProvider 内部完成
                self._providers[name] = _make_delegator("get_ohlcv")  # 占位, 实际调用按 operation 路由
            # P6 缓存兜底
            self._providers["cache"] = self._p6_cache_lookup
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"默认 provider 注册失败: {e}, DataLayer 将走 MarketDataProvider 透传")
            self._market_data_provider = None

    def _query_with_fallback(
        self,
        operation: str,
        symbol: str,
        query_fn: Callable[[ProviderFn], Any],
    ) -> QueryResult:
        """执行查询, 按 P0-P6 降级链 fallback.

        Feature Flag 关闭时直接透传到 MarketDataProvider (零开销).
        """
        start_ts = time.perf_counter()

        # Feature Flag 透传 (HC-1: 不破坏 V9 生产基线)
        if not self._feature_flag_check():
            return self._passthrough_to_market_provider(operation, symbol, query_fn, start_ts)

        # flag 开启: 走新降级链
        fallback_chain_used: list[str] = []
        last_error: Exception | None = None

        # 锁收窄 (缺陷6修复): 不包裹整个降级循环 (含网络 IO 的 query_fn),
        # 只在写共享状态 (P6 缓存 / fallback 日志) 时加锁, 避免跨线程查询串行阻塞.
        for chain_entry in self.fallback_chain:
            level = chain_entry["level"]
            name = chain_entry["name"]
            fallback_chain_used.append(level)

            provider_fn = self._providers.get(name)
            if provider_fn is None:
                logger.debug(f"Provider 未注册, 跳过: {level}/{name}")
                continue

            # 调用前确认 provider_fn 是按 operation 路由的
            actual_fn = self._route_provider_fn(name, operation, provider_fn)
            if actual_fn is None:
                continue

            try:
                data = query_fn(actual_fn)  # 网络 IO, 锁外执行 (允许跨线程并行)
                # 数据质量门控: P6 缓存是陈旧数据, 跳过 DataGate, 质量分降级标记
                if level == "P6":
                    # F-8 修复: 陈旧缓存明确降级质量分并告警, 履行"stale 警告"契约,
                    # 避免下游信任 quality_score=100 把旧数据当实时数据用于交易
                    quality_score = STALE_QUALITY_SCORE
                    logger.warning(
                        f"DataLayer 使用 P6 陈旧缓存兜底: symbol={symbol}, operation={operation}, "
                        f"age 可能最长 {self.cache_ttl_seconds}s — 请勿作为实时数据用于交易决策"
                    )
                else:
                    quality_score = 100.0
                    if self.enable_data_gate and self._data_gate is not None:
                        quality_score = self._apply_data_gate(symbol, data, operation)

                latency_ms = (time.perf_counter() - start_ts) * 1000.0
                result = QueryResult(
                    data=data,
                    provider_level=level,
                    provider_name=name,
                    from_cache=(level == "P6"),
                    quality_score=quality_score,
                    fallback_chain_used=fallback_chain_used,
                    latency_ms=latency_ms,
                )

                # P6 命中不写缓存 (避免循环); 写缓存需加锁保护共享状态
                if level != "P6":
                    with self._lock:
                        self._p6_cache_store(symbol, operation, data)

                logger.info(
                    f"DataLayer 查询成功: operation={operation}, symbol={symbol}, "
                    f"hit={level}/{name}, latency={latency_ms:.1f}ms, "
                    f"chain={fallback_chain_used}"
                )
                return result

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
                last_error = e
                # 记录 fallback 日志 (加锁保护共享日志状态)
                next_level, next_name = self._find_next_provider(level)
                with self._lock:
                    self._write_fallback_log(
                        FallbackRecord(
                            timestamp=now_bj().isoformat(),
                            symbol=symbol,
                            operation=operation,
                            failed_level=level,
                            failed_provider=name,
                            next_level=next_level or "NONE",
                            next_provider=next_name or "NONE",
                            error_type=type(e).__name__,
                            error_message=str(e)[:500],
                            latency_ms=(time.perf_counter() - start_ts) * 1000.0,
                        )
                    )
                logger.warning(
                    f"DataLayer fallback: operation={operation}, symbol={symbol}, "
                    f"failed={level}/{name} ({type(e).__name__}), next={next_level}/{next_name}"
                )
                continue

        # 所有 provider 均失败
        latency_ms = (time.perf_counter() - start_ts) * 1000.0
        raise AllSourcesFailedError(
            f"DataLayer 所有数据源失败: operation={operation}, symbol={symbol}, "
            f"chain={fallback_chain_used}, last_error={last_error}",
            level="ALL",
            cause=last_error,
        )

    def _route_provider_fn(
        self,
        name: str,
        operation: str,
        registered_fn: ProviderFn,
    ) -> ProviderFn | None:
        """根据 operation 路由到正确的 provider 函数.

        由于 _register_default_providers 中所有 P0-P5 都注册为 get_ohlcv 委托,
        这里需要根据 operation 重新选择 MarketDataProvider 的对应方法.
        """
        if name == "cache":
            return self._p6_cache_lookup_wrapper(operation)

        if self._market_data_provider is None:
            return registered_fn

        provider = self._market_data_provider

        # 根据 operation 和 name 路由到 MarketDataProvider 的具体方法
        # 实际生产中, 这里应该按 name 调用不同的底层客户端:
        #   wind_mcp -> WindClient.get_xxx
        #   tdx -> TdxDataSource.get_xxx
        #   (iFinD 已剔除)
        # 简化实现: 统一委托给 MarketDataProvider (其内部已实现降级)
        def _routed_fn(symbol: str, **kwargs: Any) -> Any:
            if operation == "get_ohlcv":
                period = kwargs.get("period", "1y")
                df = provider.get_historical_data(symbol, period=period)
                if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                    raise RuntimeError(f"{name}: get_historical_data 返回空")
                return df
            elif operation == "get_snapshot":
                snap = provider.get_market_data(symbol)
                if not snap:
                    raise RuntimeError(f"{name}: get_market_data 返回空")
                return snap
            elif operation == "get_macro_indicators":
                macro = provider.get_external_macro()
                if not macro:
                    raise RuntimeError(f"{name}: get_external_macro 返回空")
                return macro
            else:
                raise ValueError(f"未知 operation: {operation}")

        return _routed_fn

    def _passthrough_to_market_provider(
        self,
        operation: str,
        symbol: str,
        query_fn: Callable[[ProviderFn], Any],
        start_ts: float,
    ) -> QueryResult:
        """Feature Flag 关闭时的透明转发 (零开销)."""
        try:
            if self._market_data_provider is None:
                from utils.data_provider import MarketDataProvider
                self._market_data_provider = MarketDataProvider()

            provider = self._market_data_provider

            def _passthrough_fn(symbol: str = "__macro__", **kwargs: Any) -> Any:
                if operation == "get_ohlcv":
                    period = kwargs.get("period", "1y")
                    df = provider.get_historical_data(symbol, period=period)
                    if df is None:
                        return pd.DataFrame()
                    return df
                elif operation == "get_snapshot":
                    return provider.get_market_data(symbol) or {}
                elif operation == "get_macro_indicators":
                    return provider.get_external_macro() or {}
                else:
                    raise ValueError(f"未知 operation: {operation}")

            data = query_fn(_passthrough_fn)
            latency_ms = (time.perf_counter() - start_ts) * 1000.0
            return QueryResult(
                data=data,
                provider_level="PASSTHROUGH",
                provider_name="market_data_provider",
                from_cache=False,
                quality_score=100.0,
                fallback_chain_used=["PASSTHROUGH"],
                latency_ms=latency_ms,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            raise DataLayerError(
                f"透传失败: operation={operation}, symbol={symbol}, error={e}",
                level="PASSTHROUGH",
                cause=e,
            ) from e

    def _find_next_provider(self, current_level: str) -> tuple[str | None, str | None]:
        """找到当前级别的下一个 provider."""
        try:
            idx = PROVIDER_LEVELS.index(current_level)
        except ValueError:
            return None, None
        if idx + 1 >= len(PROVIDER_LEVELS):
            return None, None
        next_level = PROVIDER_LEVELS[idx + 1]
        for entry in self.fallback_chain:
            if entry["level"] == next_level:
                return next_level, entry["name"]
        return next_level, None

    def _apply_data_gate(self, symbol: str, data: Any, operation: str) -> float:
        """应用 DataGate 数据质量门控, 返回质量评分."""
        if self._data_gate is None:
            return 100.0
        try:
            # 将数据转为 snapshot 字典供 DataGate 评估
            snapshot: dict[str, Any] = {}
            if operation == "get_snapshot" and isinstance(data, dict):
                snapshot = data
            elif operation == "get_ohlcv" and isinstance(data, pd.DataFrame) and not data.empty:
                last_row = data.iloc[-1]
                snapshot = {
                    "symbol": symbol,
                    "price": float(last_row.get("close", 0)),
                    "volume": float(last_row.get("volume", 0)),
                    "timestamp": now_bj().isoformat(),
                }
            else:
                return 100.0  # 无法评估, 默认通过

            result = self._data_gate.check_and_gate(symbol, snapshot)
            if not result.allowed:
                raise DataQualityBlockedError(
                    f"DataGate 拒绝: symbol={symbol}, score={result.quality_score}, "
                    f"reasons={result.reasons}",
                    level="DATA_GATE",
                )
            return result.quality_score
        except DataQualityBlockedError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"DataGate 评估失败, 跳过门控: {e}")
            return 100.0

    # ============================================================
    # P6 缓存兜底
    # ============================================================

    def _p6_cache_key(self, symbol: str, operation: str) -> str:
        """生成 P6 缓存 key (safe filename)."""
        safe_symbol = symbol.replace("/", "_").replace("\\", "_").replace(":", "_")
        return f"{operation}__{safe_symbol}.json"

    def _p6_cache_store(self, symbol: str, operation: str, data: Any) -> None:
        """存储数据到 P6 缓存 (供后续兜底使用)."""
        try:
            cache_file = self._p6_cache_dir / self._p6_cache_key(symbol, operation)
            payload = {
                "symbol": symbol,
                "operation": operation,
                "stored_at": now_bj().isoformat(),
                "stored_ts": time.time(),
                "data": self._serialize_data(data),
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            logger.debug(f"P6 缓存写入失败 (不影响业务): symbol={symbol}, op={operation}, err={e}")

    def _p6_cache_lookup(self, symbol: str, **kwargs: Any) -> Any:
        """P6 缓存兜底查询 (直接调用, 不通过 _route_provider_fn).

        Raises:
            RuntimeError: 缓存不存在 / 已过期
        """
        operation = kwargs.get("operation", "get_ohlcv")
        cache_file = self._p6_cache_dir / self._p6_cache_key(symbol, operation)
        if not cache_file.exists():
            raise RuntimeError(f"P6 缓存不存在: symbol={symbol}, op={operation}")

        try:
            with open(cache_file, encoding="utf-8") as f:
                payload = json.load(f)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            raise RuntimeError(f"P6 缓存读取失败: {e}") from e

        # 检查 TTL
        stored_ts = float(payload.get("stored_ts", 0))
        age_seconds = time.time() - stored_ts
        if age_seconds > self.cache_ttl_seconds:
            raise RuntimeError(
                f"P6 缓存已过期: age={age_seconds:.0f}s, ttl={self.cache_ttl_seconds}s"
            )

        return self._deserialize_data(payload.get("data"))

    def _p6_cache_lookup_wrapper(self, operation: str) -> ProviderFn:
        """P6 缓存查询的包装函数 (供 _route_provider_fn 使用)."""
        def _fn(symbol: str, **kwargs: Any) -> Any:
            kwargs["operation"] = operation
            return self._p6_cache_lookup(symbol, **kwargs)
        return _fn

    def _serialize_data(self, data: Any) -> Any:
        """序列化数据供 JSON 存储."""
        if isinstance(data, pd.DataFrame):
            return {"__type__": "DataFrame", "records": data.reset_index().to_dict(orient="records")}
        return data

    def _deserialize_data(self, data: Any) -> Any:
        """反序列化 JSON 数据."""
        if isinstance(data, dict) and data.get("__type__") == "DataFrame":
            records = data.get("records", [])
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame(records)
            # 尝试恢复日期 index
            if "index" in df.columns:
                df = df.set_index("index")
                try:
                    df.index = pd.to_datetime(df.index)
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
                    pass
            return df
        return data

    # ============================================================
    # fallback 日志
    # ============================================================

    def _write_fallback_log(self, record: FallbackRecord) -> None:
        """写入 fallback 审计日志 (JSONL)."""
        try:
            log_file = self.get_fallback_log_path()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001, E501  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"fallback 日志写入失败 (不影响业务): {e}")


# ============================================================
# 模块级单例 (类似 FeatureFlags.get_instance())
# ============================================================


_DataLayer_singleton: DataLayer | None = None
_DataLayer_lock = threading.RLock()


def get_data_layer() -> DataLayer:
    """获取 DataLayer 单例 (线程安全)."""
    global _DataLayer_singleton
    with _DataLayer_lock:
        if _DataLayer_singleton is None:
            _DataLayer_singleton = DataLayer()
        return _DataLayer_singleton


def reset_data_layer_singleton() -> None:
    """重置单例 (仅供测试使用)."""
    global _DataLayer_singleton
    with _DataLayer_lock:
        _DataLayer_singleton = None


# ============================================================
# 便捷函数 (向后兼容)
# ============================================================


def get_ohlcv(symbol: str, start: str | None = None, end: str | None = None, **kwargs: Any) -> pd.DataFrame:
    """便捷函数: 获取 OHLCV."""
    return get_data_layer().get_ohlcv(symbol, start=start, end=end, **kwargs)


def get_snapshot(symbol: str, **kwargs: Any) -> dict[str, Any]:
    """便捷函数: 获取快照."""
    return get_data_layer().get_snapshot(symbol, **kwargs)


def get_macro_indicators(**kwargs: Any) -> dict[str, Any]:
    """便捷函数: 获取宏观指标."""
    return get_data_layer().get_macro_indicators(**kwargs)
