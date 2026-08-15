"""Shadow 账户真实数据注入器 — W1.3a G1 缺口补齐 (2026-08-04).

任务: W1.3a (Wave 1 第一子任务)
关联缺口: G1 — DataProvider 未接真实行情, Shadow 在 dry_run 空数据上运行

功能:
    1. 从 MarketDataProvider 拉取真实行情 (TDX 优先 + AKShare/Wind 多源降级)
    2. 按当日持仓权重计算组合日收益 (sum(weight * ret))
    3. 增量写入 daily_returns.jsonl (不覆盖历史, 仅追加/更新当日)
    4. 提供离线 dry_run 模式 (不写盘, 仅返回结果, 供 W1.3a 离线验证)
    5. 多源交叉校验 (Day 2 任务, Day 1 仅预留接口)

设计原则 (HC 合规):
    - HC-1: 不切任何 Feature Flag (默认行为不变)
    - HC-3: risk_managed=True (下游 ShadowAccountAdapter 已集成)
    - HC-4: 14 天观察期阻塞由下游 launcher 处理, 本模块仅产出数据
    - HC-5: 配置走 ConfigManager (由 CLI/调用方注入参数)
    - fail-safe: 数据源失败时跳过该日, 不抛异常中断流程
    - 不修改 ShadowAccountAdapter (Adapter 模式外观)

参考:
    - 雏形: _archive/one_time_scripts/_fix_shadow_returns.py (已验证可用算法)
    - 设计: docs/自我进化框架/W1.3a_G1_DATA_FEEDER_DESIGN.md
    - 下游: utils/alpha/shadow_account_adapter.py::run_shadow()

用法:
    # 单日注入 (生产)
    from utils.alpha.shadow_real_data_feeder import ShadowRealDataFeeder
    from utils.data_provider import MarketDataProvider
    provider = MarketDataProvider(backtest_mode=False)
    feeder = ShadowRealDataFeeder(data_provider=provider)
    result = feeder.feed_single_date("2026-08-04", {"600276": 0.05, "588000": 0.03})

    # 离线 dry-run (W1.3a 验证)
    result = feeder.dry_run("2026-08-04", {"600276": 0.05, "588000": 0.03})

    # 历史回填 (Day 2)
    results = feeder.feed_history("2026-07-23", "2026-08-04")
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
DEFAULT_SOURCE_TAG = "w13a_real_market_feed"
# 修复 (2026-08-11 v8.6.14): "1m" 在 MarketDataProvider 中被解释为月线/年度数据
# (仅 7 行, 2020-2026 每年一行), 导致 _fetch_symbol_prices 精确匹配历史日期失败,
# 回退到 iloc[-1] (当日收盘价), 多个历史日期的 daily_return 完全相同 (bug).
# 改用 "1d" 日K线 (252 行, 过去1年交易日), 可精确匹配任意历史日期.
DEFAULT_HISTORICAL_PERIOD = "1d"

# 最小有效权重 (绝对值小于此值忽略, 避免噪音)
MIN_SIGNIFICANT_WEIGHT = 0.001  # 0.1%

# 收益率异常阈值 (单日 |ret| > 5% 触发 warning)
ABNORMAL_RETURN_THRESHOLD = 0.05

# 跨源差异阈值 (1%)
CROSS_SOURCE_DIFF_THRESHOLD = 0.01

# 标的覆盖率阈值 (80%)
COVERAGE_THRESHOLD = 0.8

# 20cm 板阈值与识别逻辑统一迁移到 utils.market_rules (单一事实源)
# 这里保留导入以兼容现有调用方 (向后兼容)
from utils.market_rules import (  # noqa: E402
    ABNORMAL_RETURN_THRESHOLD_20CM,
    ABNORMAL_RETURN_THRESHOLD_INTERNAL,
    is_20cm_symbol,
)

# 日期格式
DATE_FMT = "%Y-%m-%d"
ISO_FMT = "%Y-%m-%dT%H:%M:%S"

# Day 2: 并行化与缓存配置
DEFAULT_MAX_WORKERS = 4  # 默认并行度 (IO 密集型, 4 线程平衡吞吐与连接数)
MAX_MAX_WORKERS = 16     # 上限, 防止 provider 端连接数打满
DEFAULT_CACHE_MAX_SYMBOLS = 2000  # symbol 价格缓存上限 (足够覆盖 HS300+ZZ500 约 800 标的)
CACHE_TTL_DEFAULT_SEC = 3600  # 缓存默认 TTL 1 小时 (生产中盘后任务一次性, 主要防同次回填内重复拉取)

# 持仓权重来源候选路径 (按优先级)
_TRADE_PLAN_DIR = _PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
_STRATEGY_PLAN_DIR = _PROJECT_ROOT / "reports" / "strategy"
_POSITIONS_JSON = _PROJECT_ROOT / "config" / "positions.json"


# ============================================================
# 数据类
# ============================================================


@dataclass
class FeedResult:
    """单日注入结果.

    Attributes:
        date: 日期 (YYYY-MM-DD)
        daily_return: 加权日收益 (小数, 如 0.0085 = 0.85%)
        success_count: 成功获取行情的标的数
        fail_count: 失败的标的数
        total_count: 总标的数
        coverage: 标的覆盖率 (success_count / total_count)
        symbols_detail: 每个标的的明细 [{symbol, weight, prev_close, target_close, ret, contrib, error}]
        source_tag: 数据来源标记
        cross_validated: 是否多源交叉校验
        source_consistency: high/medium/low/inconsistent (Day 1 默认 high)
        warnings: 警告信息列表
        written: 是否已写入 jsonl (dry_run 时为 False)
        skipped: 是否跳过 (数据源全失败时为 True)
        error: 错误信息 (失败时填)
    """

    date: str
    daily_return: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    total_count: int = 0
    coverage: float = 0.0
    symbols_detail: list[dict[str, Any]] = field(default_factory=list)
    source_tag: str = DEFAULT_SOURCE_TAG
    cross_validated: bool = False
    source_consistency: str = "high"
    warnings: list[str] = field(default_factory=list)
    written: bool = False
    skipped: bool = False
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """是否成功 (success_count > 0 且 not skipped)."""
        return (not self.skipped) and self.success_count > 0


@dataclass
class ValidationResult:
    """多源交叉校验结果 (Day 2 实现, Day 1 仅占位)."""

    date: str
    primary_return: float = 0.0
    secondary_return: float = 0.0
    max_diff: float = 0.0
    source_consistency: str = "unknown"  # high/medium/low/inconsistent/unknown
    is_valid: bool = False
    sources_used: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class CacheStats:
    """Symbol 价格缓存统计 (Day 2 新增).

    Attributes:
        hits: 缓存命中次数
        misses: 缓存未命中次数 (实际调用 provider)
        evictions: 因容量上限淘汰的条目数
        size: 当前缓存条目数
        bytes_estimate: 估算内存占用 (字节)
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    bytes_estimate: int = 0

    @property
    def hit_rate(self) -> float:
        """命中率 (0~1)."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


@dataclass
class HistoryFeedSummary:
    """历史回填汇总结果 (Day 2 新增).

    用于跨日批量回填的统计信息, 便于日志/审计.

    Attributes:
        start_date: 起始日期
        end_date: 结束日期
        total_days: 总日历日数 (含周末, 若 skip_weekend=True 则包含被跳过的)
        success_days: 成功注入的交易日数
        skipped_days: 跳过的天数 (周末/无权重/数据源全失败)
        failed_days: 失败天数 (非 skipped 但 is_success=False)
        total_daily_return: 累计日收益 (简单求和, 非复利)
        avg_daily_return: 平均日收益
        max_daily_return: 最大单日收益
        min_daily_return: 最小单日收益
        cache_stats: 缓存统计
        elapsed_sec: 总耗时 (秒)
    """

    start_date: str = ""
    end_date: str = ""
    total_days: int = 0
    success_days: int = 0
    skipped_days: int = 0
    failed_days: int = 0
    total_daily_return: float = 0.0
    avg_daily_return: float = 0.0
    max_daily_return: float = 0.0
    min_daily_return: float = 0.0
    cache_stats: Optional[CacheStats] = None
    elapsed_sec: float = 0.0


# ============================================================
# 异常体系
# ============================================================


class ShadowRealDataFeederError(Exception):
    """ShadowRealDataFeeder 基础异常."""


class DataProviderUnavailableError(ShadowRealDataFeederError):
    """数据源全部不可用."""


class WeightsLoadError(ShadowRealDataFeederError):
    """持仓权重加载失败."""


# ============================================================
# 主类
# ============================================================


class ShadowRealDataFeeder:
    """Shadow 账户真实数据注入器 — 从 DataProvider 拉取真实行情,
    按当日持仓权重计算组合日收益, 增量写入 daily_returns.jsonl.

    设计原则:
        1. 不修改 ShadowAccountAdapter (Adapter 模式外观)
        2. 不切任何 Feature Flag (HC-1: 默认行为不变)
        3. 增量写入 (不覆盖历史记录, 仅追加/更新当日)
        4. 多源降级链 (TDX > AKShare > Wind, 任一可用即可)
        5. fail-safe (数据源失败时跳过该日, 不抛异常中断流程)

    HC 合规:
        - HC-1: 不切任何 Feature Flag
        - HC-3: 下游 ShadowAccountAdapter 已集成 risk_managed
        - HC-4: 观察期阻塞由下游 launcher 处理
        - HC-5: 配置通过 __init__ 参数注入
    """

    def __init__(
        self,
        data_provider: Any,
        weights_source: str = "auto",
        weights_path: Optional[Path] = None,
        output_path: Path = DEFAULT_OUTPUT_PATH,
        source_tag: str = DEFAULT_SOURCE_TAG,
        skip_weekend: bool = True,
        verbose: bool = False,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache_enabled: bool = True,
        cache_max_symbols: int = DEFAULT_CACHE_MAX_SYMBOLS,
    ) -> None:
        """初始化 Shadow 真实数据注入器.

        Args:
            data_provider: MarketDataProvider 实例 (必须实现 get_historical_data)
            weights_source: 权重来源模式
                - "auto": 自动检测 (优先 plan_file, 退化 positions_json)
                - "plan_file": 从 trade_plans/ 目录读取
                - "positions_json": 从 data/positions.json 读取
                - "manual": 调用方直接传入 dict (不读文件)
            weights_path: 显式指定的权重文件路径 (覆盖 weights_source 的自动查找)
            output_path: 输出 jsonl 文件路径
            source_tag: 数据来源标记 (写入 jsonl 的 source 字段)
            skip_weekend: 是否跳过周末 (周六周日不注入)
            verbose: 是否输出详细日志
            max_workers: feed_history 并行度 (Day 2 新增)
                - 1: 串行 (Day 1 兼容行为)
                - N>1: 用 ThreadPoolExecutor 并行处理多日
                - 上限 MAX_MAX_WORKERS=16, 防止 provider 端连接打满
            cache_enabled: 是否启用 symbol 价格缓存 (Day 2 新增)
                - True: 多日回填时缓存 provider.get_historical_data 结果, 避免重复 IO
                - False: 每次调用都重新拉取 (Day 1 兼容行为)
            cache_max_symbols: 缓存上限 (LRU 淘汰), 默认 2000

        Raises:
            ValueError: 参数无效
        """
        if data_provider is None:
            raise ValueError("data_provider 不能为 None")
        if not hasattr(data_provider, "get_historical_data"):
            raise ValueError(
                f"data_provider 必须实现 get_historical_data 方法, 实际类型: {type(data_provider).__name__}"
            )
        if weights_source not in ("auto", "plan_file", "positions_json", "manual"):
            raise ValueError(
                f"weights_source 必须为 auto/plan_file/positions_json/manual, 实际: {weights_source}"
            )
        if max_workers < 1:
            raise ValueError(f"max_workers 必须 >= 1, 实际: {max_workers}")
        if max_workers > MAX_MAX_WORKERS:
            raise ValueError(
                f"max_workers 超过上限 {MAX_MAX_WORKERS}, 实际: {max_workers}"
            )
        if cache_max_symbols < 1:
            raise ValueError(f"cache_max_symbols 必须 >= 1, 实际: {cache_max_symbols}")

        self._provider = data_provider
        self._weights_source = weights_source
        self._weights_path = weights_path
        self._output_path = Path(output_path)
        self._source_tag = source_tag
        self._skip_weekend = skip_weekend
        self._verbose = verbose
        self._max_workers = max_workers
        self._cache_enabled = cache_enabled
        self._cache_max_symbols = cache_max_symbols

        # 检查 data_provider 是否有 source_health 属性 (用于日志诊断)
        self._source_health = getattr(data_provider, "source_health", None)

        # Day 2: Symbol 价格缓存
        # 结构: {(symbol, period): (df, fetched_at)}
        # 用 OrderedDict 实现 LRU (move_to_end 支持)
        self._price_cache: OrderedDict[tuple[str, str], tuple[Any, float]] = OrderedDict()
        self._cache_stats = CacheStats()
        self._cache_lock = threading.Lock()

        # Day 2: jsonl 写入锁 (并行计算时保证文件写入互斥)
        self._jsonl_lock = threading.Lock()

    # ------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------

    def feed_single_date(
        self,
        date: str,
        target_weights: Optional[dict[str, float]] = None,
    ) -> FeedResult:
        """注入单日真实收益.

        步骤:
            1. 校验日期 (跳过周末)
            2. 加载 target_weights (若未传入)
            3. 对每个 symbol 拉取历史数据, 找 date 和前一日收盘价
            4. 计算单股收益 ret = target_close / prev_close - 1
            5. 加权 daily_return = sum(weight * ret)
            6. 增量写入 jsonl (若已存在该日期则更新)

        Args:
            date: 日期 (YYYY-MM-DD)
            target_weights: {symbol: weight} 持仓权重; None 时从 weights_source 加载

        Returns:
            FeedResult: 包含 daily_return, success_count, fail_count, errors 等

        Raises:
            ValueError: 日期格式错误
        """
        # 1. 校验日期
        try:
            target_dt = datetime.strptime(date, DATE_FMT)
        except ValueError as e:
            raise ValueError(f"日期格式错误, 期望 YYYY-MM-DD, 实际: {date}") from e

        if self._skip_weekend and target_dt.weekday() >= 5:
            logger.info("跳过周末: %s (weekday=%d)", date, target_dt.weekday())
            return FeedResult(
                date=date,
                skipped=True,
                error=f"weekend (weekday={target_dt.weekday()})",
            )

        # 2. 加载权重
        if target_weights is None:
            try:
                target_weights = self._load_target_weights(date)
            except WeightsLoadError as e:
                logger.warning("加载权重失败: %s", e)
                return FeedResult(
                    date=date,
                    skipped=True,
                    error=f"weights_load_failed: {e}",
                )

        if not target_weights:
            logger.warning("权重为空, 跳过: %s", date)
            return FeedResult(
                date=date,
                skipped=True,
                error="empty_target_weights",
            )

        # 3. 计算加权收益
        result = self._compute_weighted_return(date, target_weights)

        # 4. 写入 jsonl (Day 2: 加锁保证并行模式下的文件写入互斥)
        if result.is_success:
            try:
                with self._jsonl_lock:
                    self._update_jsonl(result)
                result.written = True
                logger.info(
                    "注入成功 %s: daily_return=%.4f%%, coverage=%.2f%%, written=True",
                    date,
                    result.daily_return * 100,
                    result.coverage * 100,
                )
            except OSError as e:
                logger.error("写入 jsonl 失败: %s", e)
                result.warnings.append(f"write_failed: {e}")
        else:
            logger.warning(
                "注入失败 %s: success=0, total=%d, skipped=%s",
                date,
                result.total_count,
                result.skipped,
            )

        return result

    def dry_run(
        self,
        date: str,
        target_weights: Optional[dict[str, float]] = None,
    ) -> FeedResult:
        """离线 dry-run 模式 — 计算但不写盘, 仅返回结果.

        用于 W1.3a 离线验证, 不污染 daily_returns.jsonl.

        与 feed_single_date 的区别:
            - 不调用 _update_jsonl, 文件不会被创建/修改
            - written 字段始终为 False
            - 其余逻辑 (日期校验/权重加载/算法/护栏) 完全一致

        Args:
            date: 日期 (YYYY-MM-DD)
            target_weights: {symbol: weight}; None 时从 weights_source 加载

        Returns:
            FeedResult: written 字段始终为 False

        Raises:
            ValueError: 日期格式错误
        """
        # 1. 校验日期
        try:
            target_dt = datetime.strptime(date, DATE_FMT)
        except ValueError as e:
            raise ValueError(f"日期格式错误, 期望 YYYY-MM-DD, 实际: {date}") from e

        if self._skip_weekend and target_dt.weekday() >= 5:
            logger.info("[DRY-RUN] 跳过周末: %s", date)
            return FeedResult(
                date=date,
                skipped=True,
                error=f"weekend (weekday={target_dt.weekday()})",
            )

        # 2. 加载权重
        if target_weights is None:
            try:
                target_weights = self._load_target_weights(date)
            except WeightsLoadError as e:
                logger.warning("[DRY-RUN] 加载权重失败: %s", e)
                return FeedResult(
                    date=date,
                    skipped=True,
                    error=f"weights_load_failed: {e}",
                )

        if not target_weights:
            return FeedResult(
                date=date,
                skipped=True,
                error="empty_target_weights",
            )

        # 3. 计算加权收益 (不写盘)
        result = self._compute_weighted_return(date, target_weights)
        result.written = False  # 强制标记为未写盘

        if result.is_success:
            logger.info(
                "[DRY-RUN] %s: daily_return=%.4f%% (NOT written to jsonl)",
                date,
                result.daily_return * 100,
            )
        return result

    def feed_history(
        self,
        start_date: str,
        end_date: str,
        weights_history: Optional[dict[str, dict[str, float]]] = None,
        progress_callback: Optional[Callable[[int, int, FeedResult], None]] = None,
    ) -> list[FeedResult]:
        """注入历史日期范围的真实收益 (用于回填观察期).

        Day 2 增强: 支持并行化 (max_workers>1) 与 symbol 价格缓存 (cache_enabled=True).

        执行流程:
            1. 解析日期范围, 构造日期列表
            2. 预加载所有日期的权重 (并行 + 缓存友好)
            3. 若 cache_enabled, 预热 symbol 价格缓存 (一次性拉取所有需要的 symbol)
            4. 按 max_workers 并行执行每日 feed_single_date
               - 单日内部 symbol 拉取复用缓存 (避免重复 IO)
               - jsonl 写入用 _jsonl_lock 保证互斥
            5. 进度回调 (可选): progress_callback(current, total, latest_result)
            6. 返回按日期升序排序的结果列表

        Args:
            start_date: 起始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            weights_history: {date: {symbol: weight}}; None 时每日从 weights_source 加载
            progress_callback: 进度回调 fn(current, total, latest_result), 用于 CLI/监控

        Returns:
            list[FeedResult]: 按日期升序的结果列表

        Raises:
            ValueError: 日期范围无效
        """
        import time as _time

        t_start = _time.perf_counter()

        try:
            start_dt = datetime.strptime(start_date, DATE_FMT)
            end_dt = datetime.strptime(end_date, DATE_FMT)
        except ValueError as e:
            raise ValueError(f"日期格式错误: {e}") from e

        if start_dt > end_dt:
            raise ValueError(
                f"start_date ({start_date}) 不能晚于 end_date ({end_date})"
            )

        # 1. 构造日期列表
        date_list: list[str] = []
        current = start_dt
        while current <= end_dt:
            date_list.append(current.strftime(DATE_FMT))
            current += timedelta(days=1)

        total = len(date_list)
        logger.info(
            "历史回填启动 %s ~ %s: total_days=%d, max_workers=%d, cache_enabled=%s",
            start_date,
            end_date,
            total,
            self._max_workers,
            self._cache_enabled,
        )

        # 2. 预加载权重 (避免并行时多线程同时读文件)
        weights_per_date: dict[str, Optional[dict[str, float]]] = {}
        for d in date_list:
            if weights_history is not None:
                weights_per_date[d] = weights_history.get(d)
            else:
                # manual 模式下不预加载, 由 feed_single_date 处理 (会跳过)
                if self._weights_source == "manual":
                    weights_per_date[d] = None
                    continue
                try:
                    weights_per_date[d] = self._load_target_weights(d)
                except WeightsLoadError as e:
                    logger.debug("预加载权重失败 %s: %s", d, e)
                    weights_per_date[d] = None

        # 3. 预热缓存 (可选): 收集所有 unique symbol, 串行预拉取一次
        # 这样并行阶段几乎全是缓存命中, 减少 provider 端连接压力
        if self._cache_enabled:
            self._preload_symbol_cache(weights_per_date)

        # 4. 执行每日注入 (串行或并行)
        if self._max_workers <= 1:
            results = self._feed_history_serial(
                date_list, weights_per_date, progress_callback
            )
        else:
            results = self._feed_history_parallel(
                date_list, weights_per_date, progress_callback
            )

        # 5. 汇总日志
        elapsed = _time.perf_counter() - t_start
        success_count = sum(1 for r in results if r.is_success)
        skipped_count = sum(1 for r in results if r.skipped)
        logger.info(
            "历史回填完成 %s ~ %s: total=%d, success=%d, skipped=%d, "
            "elapsed=%.2fs, cache_hit_rate=%.1f%%",
            start_date,
            end_date,
            len(results),
            success_count,
            skipped_count,
            elapsed,
            self._cache_stats.hit_rate * 100,
        )

        return results

    def _feed_history_serial(
        self,
        date_list: list[str],
        weights_per_date: dict[str, Optional[dict[str, float]]],
        progress_callback: Optional[Callable[[int, int, FeedResult], None]],
    ) -> list[FeedResult]:
        """串行执行历史回填 (Day 1 兼容路径)."""
        results: list[FeedResult] = []
        total = len(date_list)
        for idx, date_str in enumerate(date_list, 1):
            weights = weights_per_date.get(date_str)
            result = self.feed_single_date(date_str, weights)
            results.append(result)
            if progress_callback is not None:
                try:
                    progress_callback(idx, total, result)
                except (TypeError, ValueError) as e:
                    logger.warning("progress_callback 调用失败: %s", e)
        return results

    def _feed_history_parallel(
        self,
        date_list: list[str],
        weights_per_date: dict[str, Optional[dict[str, float]]],
        progress_callback: Optional[Callable[[int, int, FeedResult], None]],
    ) -> list[FeedResult]:
        """并行执行历史回填 (Day 2 新增).

        策略:
            - 用 ThreadPoolExecutor 并行提交每日任务
            - jsonl 写入在 feed_single_date 内部已用 _jsonl_lock 保护
            - 结果按日期顺序还原 (用 future→date 映射)

        注意:
            - weights_per_date 已在主线程预加载, 避免并行读文件
            - cache 已预热, 并行阶段几乎全命中
        """
        results_by_date: dict[str, FeedResult] = {}
        total = len(date_list)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            # 提交所有日期任务
            future_to_date: dict[Any, str] = {}
            for date_str in date_list:
                weights = weights_per_date.get(date_str)
                fut = executor.submit(self.feed_single_date, date_str, weights)
                future_to_date[fut] = date_str

            # 收集结果 (按完成顺序, 但最终按日期排序)
            completed = 0
            for fut in as_completed(future_to_date):
                date_str = future_to_date[fut]
                try:
                    result = fut.result()
                except (ValueError, RuntimeError, OSError) as e:
                    # 单日异常不应中断整体回填, 构造一个失败结果
                    logger.error("并行回填异常 %s: %s", date_str, e)
                    result = FeedResult(
                        date=date_str,
                        skipped=True,
                        error=f"parallel_error: {type(e).__name__}: {e}",
                    )
                results_by_date[date_str] = result
                completed += 1
                if progress_callback is not None:
                    try:
                        progress_callback(completed, total, result)
                    except (TypeError, ValueError) as e:
                        logger.warning("progress_callback 调用失败: %s", e)

        # 按日期升序还原
        return [results_by_date[d] for d in date_list if d in results_by_date]

    def _preload_symbol_cache(
        self, weights_per_date: dict[str, Optional[dict[str, float]]]
    ) -> None:
        """预热 symbol 价格缓存 (Day 2 新增).

        收集 weights_per_date 中所有 unique symbol, 串行预拉取一次,
        使后续并行阶段几乎全是缓存命中.

        Args:
            weights_per_date: 每日权重映射
        """
        all_symbols: set[str] = set()
        for weights in weights_per_date.values():
            if not weights:
                continue
            for sym, w in weights.items():
                if abs(w) >= MIN_SIGNIFICANT_WEIGHT:
                    all_symbols.add(sym)

        if not all_symbols:
            logger.debug("预加载 symbol 列表为空, 跳过缓存预热")
            return

        logger.info(
            "预热 symbol 缓存: %d 个 unique symbols (period=%s)",
            len(all_symbols),
            DEFAULT_HISTORICAL_PERIOD,
        )

        hit_before = self._cache_stats.hits
        miss_before = self._cache_stats.misses
        for sym in all_symbols:
            # 调用 _fetch_symbol_prices 会自动走缓存逻辑
            # 这里只需要触发拉取, 不关心返回值
            # 用一个虚拟日期 (date_list 中任意一个) 触发拉取
            # 由于 _fetch_symbol_prices 拉取的是 period=1m 的全量数据, 日期不影响 df 内容
            try:
                # 用任意日期触发拉取 (取 weights_per_date 中第一个非空日期)
                self._fetch_symbol_prices(sym, list(weights_per_date.keys())[0])
            except (RuntimeError, OSError, ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("预拉取 %s 失败 (忽略, 后续会重试): %s", sym, e)

        new_misses = self._cache_stats.misses - miss_before
        new_hits = self._cache_stats.hits - hit_before
        logger.info(
            "缓存预热完成: 触发拉取 %d 次 (新 miss=%d, 新 hit=%d, 复用=%d)",
            len(all_symbols),
            new_misses,
            new_hits,
            new_hits,  # 预热阶段重复 symbol 会命中
        )

    def cross_validate(
        self,
        date: str,
        daily_return: float,
        target_weights: Optional[dict[str, float]] = None,
        secondary_provider: Any = None,
    ) -> ValidationResult:
        """多源交叉校验 — 用不同数据源重新计算 daily_return 对比.

        Day 2 实现: 支持两种校验模式.

        模式 1 (推荐): 调用方传入 `secondary_provider` (独立的 MarketDataProvider 实例),
            本方法用 secondary_provider 重新拉取每个 symbol 的价格并计算收益,
            与 `daily_return` (主源结果) 对比.

        模式 2 (降级): 未传入 `secondary_provider` 时, 仅做"内部一致性"检查:
            遍历 `target_weights` 中每个 symbol, 用主源 provider 重新拉取一次价格,
            检测是否存在异常值 (如 prev_close=0, target_close=NaN, 单股 ret 超过 ±20% 等).
            若发现异常, 标记 source_consistency=low.

        Args:
            date: 日期 (YYYY-MM-DD)
            daily_return: 已计算的日收益 (主源结果)
            target_weights: 持仓权重; None 时从 weights_source 加载
            secondary_provider: 第二个独立的 MarketDataProvider 实例 (可选).
                若提供, 用其重新计算 daily_return 做对比.

        Returns:
            ValidationResult: 含 primary_return, secondary_return, max_diff,
                source_consistency (high/medium/low/inconsistent), is_valid,
                sources_used, notes

        Raises:
            ValueError: 日期格式错误
        """
        # 校验日期
        try:
            datetime.strptime(date, DATE_FMT)
        except ValueError as e:
            raise ValueError(f"日期格式错误, 期望 YYYY-MM-DD, 实际: {date}") from e

        # 加载权重
        if target_weights is None:
            try:
                target_weights = self._load_target_weights(date)
            except WeightsLoadError as e:
                logger.warning("cross_validate 加载权重失败: %s", e)
                return ValidationResult(
                    date=date,
                    primary_return=daily_return,
                    secondary_return=0.0,
                    max_diff=0.0,
                    source_consistency="unknown",
                    is_valid=False,
                    sources_used=[],
                    notes=f"weights_load_failed: {e}",
                )

        if not target_weights:
            return ValidationResult(
                date=date,
                primary_return=daily_return,
                secondary_return=0.0,
                max_diff=0.0,
                source_consistency="unknown",
                is_valid=False,
                sources_used=[],
                notes="empty_target_weights",
            )

        # 模式 1: 用 secondary_provider 做真实多源对比
        if secondary_provider is not None:
            return self._cross_validate_with_secondary(
                date, daily_return, target_weights, secondary_provider
            )

        # 模式 2: 内部一致性检查 (无 secondary_provider)
        return self._cross_validate_internal(date, daily_return, target_weights)

    def _cross_validate_with_secondary(
        self,
        date: str,
        primary_return: float,
        target_weights: dict[str, float],
        secondary_provider: Any,
    ) -> ValidationResult:
        """用 secondary_provider 重新计算 daily_return 并对比.

        Args:
            date: 日期
            primary_return: 主源 daily_return
            target_weights: 持仓权重
            secondary_provider: 第二个独立的 MarketDataProvider

        Returns:
            ValidationResult
        """
        # 临时替换 provider 计算 secondary 收益
        original_provider = self._provider
        self._provider = secondary_provider
        try:
            secondary_result = self._compute_weighted_return(date, target_weights)
        finally:
            # 恢复原 provider
            self._provider = original_provider

        secondary_return = secondary_result.daily_return

        # 计算差异 (相对差异, 避免分母为 0)
        if abs(primary_return) < 1e-8:
            # 主源收益接近 0, 用绝对差异
            max_diff = abs(secondary_return - primary_return)
            relative_diff = max_diff
        else:
            relative_diff = abs(secondary_return - primary_return) / abs(primary_return)
            max_diff = abs(secondary_return - primary_return)

        # 一致性分级
        if relative_diff < CROSS_SOURCE_DIFF_THRESHOLD:
            # < 1% 差异
            consistency = "high"
            is_valid = True
            note = f"high consistency: relative_diff={relative_diff:.4%}"
        elif relative_diff < CROSS_SOURCE_DIFF_THRESHOLD * 5:
            # 1% ~ 5% 差异
            consistency = "medium"
            is_valid = True
            note = f"medium consistency: relative_diff={relative_diff:.4%}"
        elif relative_diff < CROSS_SOURCE_DIFF_THRESHOLD * 20:
            # 5% ~ 20% 差异
            consistency = "low"
            is_valid = False
            note = f"low consistency: relative_diff={relative_diff:.4%}, 请人工核查"
        else:
            # > 20% 差异
            consistency = "inconsistent"
            is_valid = False
            note = f"inconsistent: relative_diff={relative_diff:.4%}, 主源与次源差异过大"

        logger.info(
            "[CROSS-VALIDATE] %s: primary=%+.4f%%, secondary=%+.4f%%, diff=%.4f%%, consistency=%s",
            date,
            primary_return * 100,
            secondary_return * 100,
            max_diff * 100,
            consistency,
        )

        # 收集使用的数据源
        sources_used = self._collect_sources_used([self._provider, secondary_provider])

        return ValidationResult(
            date=date,
            primary_return=primary_return,
            secondary_return=secondary_return,
            max_diff=max_diff,
            source_consistency=consistency,
            is_valid=is_valid,
            sources_used=sources_used,
            notes=note,
        )

    @staticmethod
    def _is_20cm_symbol(symbol: str) -> bool:
        """判断 symbol 是否属于 20% 涨跌停板 (科创板/创业板注册制).

        A 股官方规则 (2020-08-24 起创业板注册制改革):
            - 科创板股票/ETF:           20% 涨跌停
            - 创业板注册制股票:         20% 涨跌停
            - 主板/中小板股票 (含 600/601/603/605/000/002): 10% 涨跌停
            - 主板 ETF (含 510/511/512/515/159 等):         10% 涨跌停

        Args:
            symbol: 标的代码, 支持两种格式:
                - "600276" (不带交易所后缀, trade_plans 文件常用)
                - "600276.SH" / "300308.SZ" (带后缀, positions.json 常用)

        Returns:
            True 表示该标的属于 20cm 板, 内部一致性校验阈值应放宽至 ±30%;
            False 表示属于 10cm 板, 保持 ±20% 阈值.

        修复 (2026-08-11 v8.6.14): 原实现引用未导入的 `_20CM_CODE_PATTERNS`
        导致 cross_validate 全部失败 (NameError). 改用从 utils.market_rules
        导入的 `is_20cm_symbol` 函数 (单一事实源), 消除重复定义.
        """
        if not symbol:
            return False
        # 复用 utils.market_rules.is_20cm_symbol (L83 已导入)
        return is_20cm_symbol(symbol)

    def _cross_validate_internal(
        self,
        date: str,
        primary_return: float,
        target_weights: dict[str, float],
    ) -> ValidationResult:
        """内部一致性检查 (无 secondary_provider).

        检查每个 symbol 的价格是否存在异常:
            - prev_close <= 0
            - target_close <= 0
            - 单股 ret 超过异常波动阈值:
                * 主板/常规 ETF: ±20% (ABNORMAL_RETURN_THRESHOLD_INTERNAL)
                * 科创板/创业板注册制 (20cm 板): ±30% (ABNORMAL_RETURN_THRESHOLD_20CM)
            - prev_close == target_close (停牌可能)

        20cm 板识别规则 (A 股官方, 2020-08-24 创业板注册制改革后):
            - 科创板股票 (688xxx/689xxx) + ETF (588xxx/562xxx): 20% 涨跌停
            - 创业板注册制股票 (300xxx/301xxx): 20% 涨跌停
            - 主板股票 (600/601/603/605/000/002) 与主板 ETF: 10% 涨跌停
            对 20cm 板阈值放宽 10 个百分点 (含涨停缓冲), 避免把正常的涨停
            误判为数据异常.

        Args:
            date: 日期
            primary_return: 主源 daily_return
            target_weights: 持仓权重

        Returns:
            ValidationResult
        """
        abnormal_count = 0
        total_checked = 0
        abnormal_symbols: list[str] = []

        for symbol, weight in target_weights.items():
            if abs(weight) < MIN_SIGNIFICANT_WEIGHT:
                continue
            total_checked += 1

            try:
                prev_close, target_close = self._fetch_symbol_prices(symbol, date)
                if prev_close is None or target_close is None:
                    abnormal_count += 1
                    abnormal_symbols.append(f"{symbol}(no_price)")
                    continue
                if prev_close <= 0 or target_close <= 0:
                    abnormal_count += 1
                    abnormal_symbols.append(f"{symbol}(non_positive_price)")
                    continue
                ret = target_close / prev_close - 1.0
                # 按 A 股板别差异化阈值: 20cm 板 ±30%, 主板 ±20%
                threshold = (
                    ABNORMAL_RETURN_THRESHOLD_20CM
                    if self._is_20cm_symbol(symbol)
                    else ABNORMAL_RETURN_THRESHOLD_INTERNAL
                )
                if abs(ret) > threshold:
                    abnormal_count += 1
                    abnormal_symbols.append(
                        f"{symbol}(ret={ret:+.2%},thr={threshold:.0%})"
                    )
            except (RuntimeError, OSError, ConnectionError, TimeoutError) as e:
                abnormal_count += 1
                abnormal_symbols.append(f"{symbol}(fetch_error:{type(e).__name__})")

        if total_checked == 0:
            return ValidationResult(
                date=date,
                primary_return=primary_return,
                secondary_return=primary_return,  # 无次源, 用主源
                max_diff=0.0,
                source_consistency="unknown",
                is_valid=False,
                sources_used=self._collect_sources_used([self._provider]),
                notes="no_symbols_checked",
            )

        abnormal_ratio = abnormal_count / total_checked

        if abnormal_ratio == 0.0:
            consistency = "high"
            is_valid = True
            note = f"all {total_checked} symbols normal (no secondary provider)"
        elif abnormal_ratio <= 0.1:
            consistency = "medium"
            is_valid = True
            note = f"{abnormal_count}/{total_checked} abnormal: {abnormal_symbols[:5]}"
        elif abnormal_ratio <= 0.3:
            consistency = "low"
            is_valid = False
            note = f"{abnormal_count}/{total_checked} abnormal: {abnormal_symbols[:5]}"
        else:
            consistency = "inconsistent"
            is_valid = False
            note = f"{abnormal_count}/{total_checked} abnormal: {abnormal_symbols[:5]}"

        logger.info(
            "[CROSS-VALIDATE-INTERNAL] %s: abnormal=%d/%d, consistency=%s",
            date,
            abnormal_count,
            total_checked,
            consistency,
        )

        return ValidationResult(
            date=date,
            primary_return=primary_return,
            secondary_return=primary_return,  # 内部模式无独立次源
            max_diff=0.0,  # 内部模式不产生 diff
            source_consistency=consistency,
            is_valid=is_valid,
            sources_used=self._collect_sources_used([self._provider]),
            notes=note,
        )

    def _collect_sources_used(self, providers: list[Any]) -> list[str]:
        """从 provider 实例收集使用的数据源名称.

        Args:
            providers: provider 实例列表

        Returns:
            数据源名称列表 (如 ["tdx", "akshare"])
        """
        sources: list[str] = []
        for p in providers:
            health = getattr(p, "source_health", None)
            if not health or not isinstance(health, dict):
                continue
            for src_name, info in health.items():
                if isinstance(info, dict) and info.get("ok", False):
                    if src_name not in sources:
                        sources.append(src_name)
        return sources

    # ------------------------------------------------------------
    # 核心算法
    # ------------------------------------------------------------

    def _compute_weighted_return(
        self,
        date: str,
        target_weights: dict[str, float],
    ) -> FeedResult:
        """计算单日加权收益.

        复用 _fix_shadow_returns.py 的核心算法:
            daily_return = sum(weight * (target_close / prev_close - 1))

        Args:
            date: 日期 (YYYY-MM-DD)
            target_weights: {symbol: weight}

        Returns:
            FeedResult: 含 daily_return, success_count, fail_count, symbols_detail
        """
        result = FeedResult(
            date=date,
            total_count=len(target_weights),
            source_tag=self._source_tag,
        )

        # 总权重 (用于检查零权重场景)
        total_abs_weight = sum(abs(w) for w in target_weights.values())
        if total_abs_weight <= 0:
            result.skipped = True
            result.error = "total_abs_weight_zero"
            return result

        daily_return = 0.0
        success_count = 0
        fail_count = 0
        symbols_detail: list[dict[str, Any]] = []

        for symbol, weight in target_weights.items():
            # 忽略微小权重
            if abs(weight) < MIN_SIGNIFICANT_WEIGHT:
                continue

            detail: dict[str, Any] = {
                "symbol": symbol,
                "weight": weight,
                "prev_close": None,
                "target_close": None,
                "ret": None,
                "contrib": None,
                "error": None,
            }

            try:
                prev_close, target_close = self._fetch_symbol_prices(symbol, date)

                if target_close is None or prev_close is None:
                    fail_count += 1
                    detail["error"] = "no_close_price"
                    symbols_detail.append(detail)
                    if self._verbose:
                        logger.warning(
                            "  %s %s: 无法获取收盘价 (prev=%s, target=%s)",
                            symbol,
                            date,
                            prev_close,
                            target_close,
                        )
                    continue

                if prev_close <= 0:
                    fail_count += 1
                    detail["error"] = f"prev_close_non_positive ({prev_close})"
                    symbols_detail.append(detail)
                    continue

                ret = target_close / prev_close - 1.0
                contrib = weight * ret
                daily_return += contrib

                detail["prev_close"] = prev_close
                detail["target_close"] = target_close
                detail["ret"] = ret
                detail["contrib"] = contrib
                symbols_detail.append(detail)  # 成功时也要记录明细

                success_count += 1
                if self._verbose:
                    logger.info(
                        "  %s: weight=%+.4f, close %.2f -> %.2f, ret=%+.4f%%, contrib=%+.4f%%",
                        symbol,
                        weight,
                        prev_close,
                        target_close,
                        ret * 100,
                        contrib * 100,
                    )

            except (RuntimeError, OSError, ConnectionError, TimeoutError) as e:
                # Provider 拉取失败 — 不中断, 继续处理下一个标的
                fail_count += 1
                detail["error"] = f"{type(e).__name__}: {e}"
                symbols_detail.append(detail)
                if self._verbose:
                    logger.warning("  %s %s: 拉取异常 %s", symbol, date, e)

        result.daily_return = round(daily_return, 6)
        result.success_count = success_count
        result.fail_count = fail_count
        result.symbols_detail = symbols_detail
        result.coverage = (
            success_count / len(target_weights) if target_weights else 0.0
        )

        # 安全护栏检查
        self._apply_safety_guards(result)

        return result

    # ------------------------------------------------------------
    # Day 2: Symbol 价格缓存
    # ------------------------------------------------------------

    def _get_historical_data_cached(
        self, symbol: str, period: str
    ) -> Any:
        """带缓存地获取历史数据 (Day 2 新增).

        策略:
            1. 若 cache_enabled=False, 直接调用 provider (Day 1 兼容)
            2. 若缓存命中且未过期 (TTL 内), 返回缓存 df, hits+1
            3. 否则调用 provider 拉取, misses+1, 写入缓存
            4. 缓存满时按 LRU 淘汰最旧条目, evictions+1

        Args:
            symbol: 标的代码
            period: 周期 (如 "1m", "1d")

        Returns:
            DataFrame 或 None (provider 返回 None 时缓存 None 占位避免重复请求)
        """
        # cache 关闭: 直接调用 provider (Day 1 兼容路径)
        if not self._cache_enabled:
            return self._provider.get_historical_data(symbol, period=period)

        cache_key = (symbol, period)
        now = __import__("time").time()

        with self._cache_lock:
            cached = self._price_cache.get(cache_key)
            if cached is not None:
                df, fetched_at = cached
                age = now - fetched_at
                if age < CACHE_TTL_DEFAULT_SEC:
                    # 命中且未过期
                    self._cache_stats.hits += 1
                    # LRU: 移到末尾 (最近使用)
                    self._price_cache.move_to_end(cache_key)
                    return df
                else:
                    # 过期, 删除
                    del self._price_cache[cache_key]

        # 缓存未命中或已过期: 调用 provider (锁外执行, 避免长时间持锁)
        try:
            df = self._provider.get_historical_data(symbol, period=period)
        except (RuntimeError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("provider 拉取 %s 失败: %s", symbol, e)
            # 不缓存失败结果, 下次重试
            with self._cache_lock:
                self._cache_stats.misses += 1
            return None

        # 写入缓存
        with self._cache_lock:
            self._cache_stats.misses += 1
            # LRU 淘汰
            while len(self._price_cache) >= self._cache_max_symbols:
                # 弹出最旧 (OrderedDict popitem(last=False) = FIFO)
                try:
                    self._price_cache.popitem(last=False)
                    self._cache_stats.evictions += 1
                except KeyError:
                    break
            self._price_cache[cache_key] = (df, now)
            # 更新内存估算 (粗略: 行数 × 列数 × 8 字节)
            if df is not None and hasattr(df, "shape"):
                try:
                    rows, cols = df.shape
                    self._cache_stats.bytes_estimate += int(rows * cols * 8)
                except (ValueError, TypeError, IndexError):
                    pass

        return df

    def clear_cache(self) -> CacheStats:
        """清空 symbol 价格缓存 (Day 2 新增).

        用于:
            - 长时间运行的进程定期清理
            - 测试间隔离
            - 显式刷新数据

        Returns:
            清空前的缓存统计 (便于日志)
        """
        with self._cache_lock:
            stats_before = CacheStats(
                hits=self._cache_stats.hits,
                misses=self._cache_stats.misses,
                evictions=self._cache_stats.evictions,
                size=len(self._price_cache),
                bytes_estimate=self._cache_stats.bytes_estimate,
            )
            self._price_cache.clear()
            # 不重置 hits/misses/evictions 累计计数, 便于监控
            self._cache_stats.bytes_estimate = 0
            logger.info(
                "缓存已清空: 清理前 size=%d, 累计 hits=%d, misses=%d, evictions=%d",
                stats_before.size,
                stats_before.hits,
                stats_before.misses,
                stats_before.evictions,
            )
            return stats_before

    def get_cache_stats(self) -> CacheStats:
        """获取当前缓存统计快照 (Day 2 新增).

        Returns:
            CacheStats 副本
        """
        with self._cache_lock:
            return CacheStats(
                hits=self._cache_stats.hits,
                misses=self._cache_stats.misses,
                evictions=self._cache_stats.evictions,
                size=len(self._price_cache),
                bytes_estimate=self._cache_stats.bytes_estimate,
            )

    def _fetch_symbol_prices(
        self,
        symbol: str,
        date: str,
    ) -> tuple[Optional[float], Optional[float]]:
        """获取指定标的在 date 和前一日的收盘价 (Day 2 重构为走缓存).

        复用 _fix_shadow_returns.py 的查找逻辑:
            1. 调用 provider.get_historical_data(symbol, period="1m")
               Day 2: 优先走缓存 (cache_enabled=True 时), 避免多日回填重复拉取
            2. 在 df 中查找 date (目标日) 和 date-1 (前一日)
            3. 若精确匹配失败, 取最近交易日收盘价

        Args:
            symbol: 标的代码 (如 "600276", "588000")
            date: 目标日期 (YYYY-MM-DD)

        Returns:
            (prev_close, target_close); 失败时对应位置为 None
        """
        df = self._get_historical_data_cached(symbol, DEFAULT_HISTORICAL_PERIOD)

        if df is None or df.empty or len(df) < 2:
            return None, None

        target_dt = datetime.strptime(date, DATE_FMT)
        prev_dt = target_dt - timedelta(days=1)
        prev_window_start = prev_dt - timedelta(days=2)  # 前一日 ±2 天窗口

        target_close: Optional[float] = None
        prev_close: Optional[float] = None

        for idx, row in df.iterrows():
            # 提取日期字符串 (兼容 datetime/date/str index)
            if hasattr(idx, "strftime"):
                idx_str = idx.strftime(DATE_FMT)
            else:
                idx_str = str(idx)[:10]

            # 精确匹配目标日
            if idx_str == date:
                try:
                    target_close = float(row["close"])
                except (ValueError, TypeError, KeyError):
                    pass

            # 前一日窗口 (含周末偏移)
            elif prev_window_start.strftime(DATE_FMT) <= idx_str < date:
                try:
                    prev_close = float(row["close"])  # 取窗口内最近
                except (ValueError, TypeError, KeyError):
                    pass

        # 精确匹配失败的回退: 取 df 末尾两个值 (假设是最新交易日)
        if target_close is None:
            try:
                target_close = float(df["close"].iloc[-1])
            except (ValueError, TypeError, KeyError, IndexError):
                pass

        if prev_close is None:
            try:
                if len(df) >= 2:
                    prev_close = float(df["close"].iloc[-2])
            except (ValueError, TypeError, KeyError, IndexError):
                pass

        return prev_close, target_close

    # ------------------------------------------------------------
    # 权重加载
    # ------------------------------------------------------------

    def _load_target_weights(self, date: str) -> dict[str, float]:
        """根据 weights_source 加载指定日期的持仓权重.

        优先级 (auto 模式):
            1. v8.3_institutional/trade_plans/trade_plan_YYYYMMDD.json
            2. reports/strategy/plan_YYYY-MM-DD.json
            3. data/positions.json (实盘持仓, 不分日期)

        Args:
            date: 日期 (YYYY-MM-DD)

        Returns:
            {symbol: weight}

        Raises:
            WeightsLoadError: 所有来源均失败
        """
        # 显式指定路径优先
        if self._weights_path is not None:
            return self._load_weights_from_file(self._weights_path, date)

        if self._weights_source == "manual":
            raise WeightsLoadError(
                "weights_source='manual' 但未传入 target_weights, 请直接调用 feed_single_date(date, weights)"
            )

        if self._weights_source == "positions_json":
            return self._load_positions_json()

        # auto / plan_file 模式: 按优先级查找
        candidates = self._get_weights_candidates(date)

        for path, loader in candidates:
            if path.exists():
                try:
                    weights = loader(path, date)
                    if weights:
                        logger.debug("加载权重成功: %s (symbols=%d)", path, len(weights))
                        return weights
                except WeightsLoadError as e:
                    logger.debug("加载权重失败 %s: %s", path, e)
                    continue

        raise WeightsLoadError(
            f"所有权重来源均失败 (date={date}, source={self._weights_source})"
        )

    def _get_weights_candidates(
        self, date: str
    ) -> list[tuple[Path, Any]]:
        """获取权重文件候选列表 (按优先级).

        修复 (2026-08-11 v8.6.14): positions.json 提到首位作为"持仓快照"权威源.
        原顺序 trade_plan > strategy_plan > positions.json 会导致:
            - trade_plan 非空时 (如 08-07) 取当日交易标的 14 个作为"持仓权重"
            - trade_plan 为空时 (如 08-06) fallback 到 positions.json 26 个全持仓
            - 结果 symbols_count 在 14/26 间剧烈波动, daily_return 口径不一致
        trade_plan 语义是"当日交易指令", 仅含当日要交易的标的, 不是持仓组合.
        positions.json 是"持仓状态快照", 含完整持仓权重, 符合 shadow 系统语义.

        Args:
            date: 日期 (YYYY-MM-DD)

        Returns:
            [(path, loader), ...] 列表, positions.json 优先
        """
        date_compact = date.replace("-", "")  # YYYYMMDD
        return [
            (_POSITIONS_JSON, self._load_positions_json_file),
            (_TRADE_PLAN_DIR / f"trade_plan_{date_compact}.json", self._load_trade_plan),
            (_STRATEGY_PLAN_DIR / f"plan_{date}.json", self._load_strategy_plan),
        ]

    def _load_weights_from_file(self, path: Path, date: str) -> dict[str, float]:
        """从显式指定的文件加载权重.

        Args:
            path: 文件路径
            date: 日期 (仅用于日志)

        Returns:
            {symbol: weight}

        Raises:
            WeightsLoadError: 加载失败
        """
        if not path.exists():
            raise WeightsLoadError(f"权重文件不存在: {path}")

        # 根据文件名判断格式
        name = path.name
        if name.startswith("trade_plan_"):
            return self._load_trade_plan(path, date)
        if name.startswith("plan_"):
            return self._load_strategy_plan(path, date)
        if name == "positions.json" or "positions" in name:
            return self._load_positions_json_file(path, date)

        # 兜底: 尝试直接 JSON 解析为 {symbol: weight}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and all(
                isinstance(v, (int, float)) for v in data.values()
            ):
                return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as e:
            raise WeightsLoadError(f"解析权重文件失败 {path}: {e}") from e

        raise WeightsLoadError(f"无法识别权重文件格式: {path}")

    def _load_trade_plan(self, path: Path, date: str) -> dict[str, float]:
        """解析 trade_plan_YYYYMMDD.json 格式的权重.

        格式参考 _fix_shadow_returns.py::get_target_weights_from_trade_plans():
            {
                "execution_plan": {
                    "day_capital": 100000,
                    "morning_orders": [{"code": "600276", "est_amount": 5000, "side": "BUY"}, ...],
                    "afternoon_orders": [...]
                }
            }

        Args:
            path: trade_plan 文件路径
            date: 日期 (仅用于日志)

        Returns:
            {symbol: weight}
        """
        try:
            with open(path, encoding="utf-8") as f:
                tp = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise WeightsLoadError(f"读取 trade_plan 失败 {path}: {e}") from e

        exec_plan = tp.get("execution_plan", {}) or {}
        day_capital = float(exec_plan.get("day_capital", 0))
        if day_capital <= 0:
            raise WeightsLoadError(f"day_capital 非正: {day_capital}")

        morning = exec_plan.get("morning_orders", []) or []
        afternoon = exec_plan.get("afternoon_orders", []) or []
        all_orders = morning + afternoon

        weights: dict[str, float] = {}
        for order in all_orders:
            sym = str(order.get("code", "")).strip()
            amt = float(order.get("est_amount", 0))
            side = str(order.get("side", "BUY")).upper()
            sign = 1.0 if side == "BUY" else -1.0
            if sym and amt > 0:
                weights[sym] = weights.get(sym, 0.0) + sign * amt / day_capital

        return weights

    def _load_strategy_plan(self, path: Path, date: str) -> dict[str, float]:
        """解析 reports/strategy/plan_YYYY-MM-DD.json 格式的权重.

        支持两种格式:
            1. {"target_weights": {"600276": 0.05, ...}}
            2. {"positions": [{"symbol": "600276", "weight": 0.05}, ...]}

        Args:
            path: plan 文件路径
            date: 日期 (仅用于日志)

        Returns:
            {symbol: weight}
        """
        try:
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise WeightsLoadError(f"读取 strategy plan 失败 {path}: {e}") from e

        # 格式 1: target_weights 直接字典
        if "target_weights" in plan:
            tw = plan["target_weights"]
            if isinstance(tw, dict):
                return {str(k): float(v) for k, v in tw.items() if isinstance(v, (int, float))}

        # 格式 2: positions 列表
        if "positions" in plan:
            positions = plan["positions"]
            if isinstance(positions, list):
                weights: dict[str, float] = {}
                for pos in positions:
                    sym = str(pos.get("symbol", "")).strip()
                    w = float(pos.get("weight", 0))
                    if sym and w != 0:
                        weights[sym] = weights.get(sym, 0.0) + w
                return weights

        raise WeightsLoadError(f"无法识别 strategy plan 格式: {path}")

    def _load_positions_json(self) -> dict[str, float]:
        """从 data/positions.json 加载 (auto 模式 fallback).

        Returns:
            {symbol: weight}

        Raises:
            WeightsLoadError: 加载失败
        """
        return self._load_positions_json_file(_POSITIONS_JSON, "")

    def _load_positions_json_file(self, path: Path, date: str) -> dict[str, float]:
        """解析 positions.json 格式的权重.

        支持格式:
            {"600276": 0.05, "588000": 0.03, ...}
            或 {"positions": [{"symbol": "600276", "weight": 0.05}, ...]}

        Args:
            path: positions.json 文件路径
            date: 日期 (仅用于日志)

        Returns:
            {symbol: weight}
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise WeightsLoadError(f"读取 positions 失败 {path}: {e}") from e

        # 格式 1: 直接字典
        if isinstance(data, dict) and "positions" not in data:
            return {
                str(k): float(v)
                for k, v in data.items()
                if isinstance(v, (int, float))
            }

        # 格式 2: {"positions": [...]} (list, 每项含 symbol/weight)
        if isinstance(data, dict) and "positions" in data:
            positions = data["positions"]
            if isinstance(positions, list):
                weights: dict[str, float] = {}
                for pos in positions:
                    sym = str(pos.get("symbol", "")).strip()
                    w = float(pos.get("weight", 0))
                    if sym and w != 0:
                        weights[sym] = weights.get(sym, 0.0) + w
                return weights

            # 格式 3: {"positions": {code: {target_weight, shares, ...}}} (dict, 从 target_weight 提取)
            if isinstance(positions, dict):
                weights = {}
                for code, pos in positions.items():
                    if not isinstance(pos, dict):
                        continue
                    sym = str(code).strip()
                    # 优先 target_weight; 其次 weight; 否则跳过
                    w = pos.get("target_weight")
                    if w is None:
                        w = pos.get("weight")
                    try:
                        w = float(w) if w is not None else 0.0
                    except (ValueError, TypeError):
                        w = 0.0
                    if sym and w != 0:
                        weights[sym] = weights.get(sym, 0.0) + w
                if weights:
                    return weights

        raise WeightsLoadError(f"无法识别 positions 格式: {path}")

    # ------------------------------------------------------------
    # JSONL 增量写入
    # ------------------------------------------------------------

    def _update_jsonl(self, result: FeedResult) -> None:
        """增量写入 daily_returns.jsonl.

        策略:
            - 若日期已存在: 更新该行 (daily_return, source, updated_at, ...)
            - 若日期不存在: 追加新行
            - 保持文件按日期升序排序

        Args:
            result: 单日注入结果

        Raises:
            OSError: 文件读写失败
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        # 读取现有记录
        records: dict[str, dict[str, Any]] = {}
        if self._output_path.exists():
            try:
                with open(self._output_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                            d = r.get("date")
                            if d:
                                records[d] = r
                        except json.JSONDecodeError:
                            continue
            except OSError as e:
                logger.warning("读取 jsonl 失败, 将覆盖: %s", e)
                records = {}

        # 构造新记录
        new_record: dict[str, Any] = {
            "date": result.date,
            "daily_return": result.daily_return,
            "source": result.source_tag,
            "updated_at": datetime.now().strftime(ISO_FMT),
            "symbols_count": result.success_count,
            "cross_validated": result.cross_validated,
            "source_consistency": result.source_consistency,
        }
        if result.warnings:
            new_record["warnings"] = result.warnings
        if result.coverage < COVERAGE_THRESHOLD:
            new_record["partial_coverage"] = True

        records[result.date] = new_record

        # 按日期排序写入
        sorted_dates = sorted(records.keys())
        tmp_path = self._output_path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for d in sorted_dates:
                    f.write(json.dumps(records[d], ensure_ascii=False) + "\n")
            # 原子替换
            tmp_path.replace(self._output_path)
        except OSError:
            # 清理临时文件
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    # ------------------------------------------------------------
    # 安全护栏
    # ------------------------------------------------------------

    def _apply_safety_guards(self, result: FeedResult) -> None:
        """应用安全护栏, 标记异常情况.

        护栏规则:
            1. 单日异常收益: |daily_return| > 5% → 标记 warning (仍写入)
            2. 数据源全失败: success_count == 0 → 标记 skipped
            3. 标的覆盖率 < 80% → 标记 partial_coverage warning

        Args:
            result: 待检查的结果 (原地修改)
        """
        # 护栏 1: 单日异常收益
        if abs(result.daily_return) > ABNORMAL_RETURN_THRESHOLD:
            result.warnings.append(
                f"abnormal_return: {result.daily_return:.4%} (threshold={ABNORMAL_RETURN_THRESHOLD:.0%})"
            )
            logger.warning(
                "异常收益 %s: %+.4f%% (超过 %.0f%% 阈值, 请人工核查)",
                result.date,
                result.daily_return * 100,
                ABNORMAL_RETURN_THRESHOLD * 100,
            )

        # 护栏 2: 数据源全失败
        if result.success_count == 0 and result.total_count > 0:
            result.skipped = True
            result.error = "all_symbols_failed"
            return

        # 护栏 3: 标的覆盖率
        if result.total_count > 0 and result.coverage < COVERAGE_THRESHOLD:
            result.warnings.append(
                f"partial_coverage: {result.coverage:.2%} < {COVERAGE_THRESHOLD:.0%}"
            )
            result.source_consistency = "medium"
            logger.warning(
                "覆盖率不足 %s: %.2f%% (success=%d, total=%d)",
                result.date,
                result.coverage * 100,
                result.success_count,
                result.total_count,
            )

    # ------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------

    def get_source_health(self) -> dict[str, Any]:
        """获取数据源健康状态 (供诊断).

        Returns:
            {source_name: {ok: bool, last_error: str}} 或空字典 (无 source_health)
        """
        if self._source_health is None:
            return {}
        try:
            return dict(self._source_health)
        except (TypeError, ValueError):
            return {}


# ============================================================
# CLI 入口 (Day 1 基础版, Day 3 增强)
# ============================================================


def _build_cli_parser():
    """构建 CLI 参数解析器."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shadow 真实数据注入器 (W1.3a G1 缺口补齐)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单日注入
  py -m utils.alpha.shadow_real_data_feeder --date 2026-08-04

  # 离线 dry-run (不写盘)
  py -m utils.alpha.shadow_real_data_feeder --date 2026-08-04 --dry-run

  # 历史回填
  py -m utils.alpha.shadow_real_data_feeder --start 2026-07-23 --end 2026-08-04

  # 手动指定权重文件
  py -m utils.alpha.shadow_real_data_feeder --date 2026-08-04 --weights-file path/to/weights.json
""",
    )
    parser.add_argument("--date", help="单日注入日期 (YYYY-MM-DD)")
    parser.add_argument("--start", help="历史回填起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", help="历史回填结束日期 (YYYY-MM-DD)")
    parser.add_argument(
        "--weights-file",
        type=Path,
        help="权重文件路径 (覆盖默认 weights_source)",
    )
    parser.add_argument(
        "--weights-source",
        default="auto",
        choices=["auto", "plan_file", "positions_json", "manual"],
        help="权重来源模式 (默认 auto)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"输出 jsonl 路径 (默认 {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="离线 dry-run 模式 (不写盘)",
    )
    parser.add_argument(
        "--no-skip-weekend",
        action="store_true",
        help="不跳过周末 (默认跳过)",
    )
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口.

    Returns:
        退出码 (0=成功, 1=失败)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    # 初始化 DataProvider
    try:
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider(backtest_mode=False)
    except (ImportError, RuntimeError) as e:
        logger.error(f"[FAIL] DataProvider 初始化失败: {e}")
        return 1

    # 打印数据源健康状态
    logger.info("\n=== 数据源健康状态 ===")
    health = getattr(provider, "source_health", {}) or {}
    for src, info in health.items():
        ok = info.get("ok", False) if isinstance(info, dict) else False
        marker = "[OK]" if ok else "[FAIL]"
        err = info.get("last_error", "") if isinstance(info, dict) else ""
        logger.info(f"  {marker} {src}: ok={ok} {err}")

    feeder = ShadowRealDataFeeder(
        data_provider=provider,
        weights_source=args.weights_source,
        weights_path=args.weights_file,
        output_path=args.output,
        skip_weekend=not args.no_skip_weekend,
        verbose=args.verbose,
    )

    # 单日模式
    if args.date:
        try:
            if args.dry_run:
                result = feeder.dry_run(args.date)
            else:
                result = feeder.feed_single_date(args.date)
        except ValueError as e:
            logger.error(f"[FAIL] 参数错误: {e}")
            return 1

        logger.info(f"\n=== 结果 ({args.date}) ===")
        logger.info(f"  daily_return: {result.daily_return:+.4%}")
        logger.info(f"  success/total: {result.success_count}/{result.total_count}")
        logger.info(f"  coverage: {result.coverage:.2%}")
        logger.info(f"  written: {result.written}")
        logger.info(f"  skipped: {result.skipped}")
        if result.warnings:
            logger.info(f"  warnings: {result.warnings}")
        if result.error:
            logger.info(f"  error: {result.error}")
        return 0 if result.is_success else 1

    # 历史回填模式
    if args.start and args.end:
        try:
            results = feeder.feed_history(args.start, args.end)
        except ValueError as e:
            logger.error(f"[FAIL] 参数错误: {e}")
            return 1
        success = sum(1 for r in results if r.is_success)
        skipped = sum(1 for r in results if r.skipped)
        logger.info(f"\n=== 回填结果 ({args.start} ~ {args.end}) ===")
        logger.info(f"  total days: {len(results)}")
        logger.info(f"  success: {success}")
        logger.info(f"  skipped: {skipped}")
        for r in results:
            marker = "[OK]" if r.is_success else "[SKIP]"
            logger.info(f"  {marker} {r.date}: {r.daily_return:+.4%}")
        return 0 if success > 0 else 1

    # 没有指定模式
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
