"""策略注册表与性能追踪装饰器 — 模块整合 8.4 T1.6.

模块整合 8.4 — ARCHITECTURE §1.4 / ADR-002
任务: T1.6

设计目标:
    1. 统一策略注册入口: 整合 RuleEngine / MultiStrategyCoordinator / StrategyReleaseManager 三套分散注册逻辑
    2. 延迟实例化: register() 注册类, get() 首次调用时实例化并缓存
    3. 性能追踪: @track_performance 装饰器记录 PnL/Sharpe/回撤/IC_IR/延迟/异常
    4. 不破坏 V9 基线: USE_INTEGRATED_CORE_REGISTRY flag 默认 False (ADR-003 铁律)

API:
    from utils.infra.core import StrategyRegistry, track_performance, registry

    # 方式 1: 实例方法注册
    class ConservativeStrategy:
        def generate_signals(self, context): ...
    registry.register("conservative_v1", ConservativeStrategy, metadata={"capital": 1_000_000})

    # 方式 2: 类装饰器注册
    @StrategyRegistry.register_decorator("aggressive_v1", metadata={"capital": 800_000})
    class AggressiveStrategy:
        def generate_signals(self, context): ...

    # 获取策略实例 (首次获取时实例化)
    strategy = registry.get("conservative_v1")

    # 性能追踪装饰器 (flag 关闭时零开销透传)
    @track_performance(strategy_name="conservative_v1")
    def generate_signals(context):
        ...

硬约束:
    - HC-1: 不破坏 V9 基线 (默认 False, flag 关闭时旧路径仍可用)
    - HC-5: ConfigManager 4 级优先级解析不可绕过
"""

from __future__ import annotations

import functools
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 审计与性能日志目录
_AUDIT_LOG_DIR = _PROJECT_ROOT / "reports" / "strategy_registry"

logger = logging.getLogger("strategy_registry")


# ============================================================
# 数据类型
# ============================================================
@dataclass
class StrategyMetadata:
    """策略元数据 (注册时由调用方提供)."""

    name: str
    strategy_class: type[Any]
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    capital: float = 0.0
    max_weight: float = 0.40
    min_weight: float = 0.05
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceRecord:
    """性能追踪记录 (装饰器自动维护)."""

    strategy_name: str
    call_count: int = 0
    errors_count: int = 0
    last_error: str = ""
    last_call_ts: str = ""
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    # 业务指标 (由策略主动调用 update_metrics 更新)
    pnl_total: float = 0.0
    pnl_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    var_95: float = 0.0
    var_99: float = 0.0
    volatility: float = 0.0
    beta_to_market: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    # Alpha 维度 (策略主动更新)
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    turnover: float = 0.0
    # 失效预警
    is_degraded: bool = False
    degradation_reason: str = ""

    @property
    def avg_latency_ms(self) -> float:
        """平均延迟."""
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count

    @property
    def p99_latency_ms(self) -> float:
        """P99 延迟 (用 max 近似, 精确计算需保留全部样本)."""
        return self.max_latency_ms

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (持久化用).

        所有 float 字段统一舍入到 3 位小数, 保证输出一致性.
        """
        return {
            "strategy_name": self.strategy_name,
            "call_count": self.call_count,
            "errors_count": self.errors_count,
            "last_error": self.last_error,
            "last_call_ts": self.last_call_ts,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "p99_latency_ms": round(self.p99_latency_ms, 3),
            "pnl_total": round(self.pnl_total, 3),
            "pnl_pct": round(self.pnl_pct, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown": round(self.max_drawdown, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 3),
            "var_95": round(self.var_95, 3),
            "var_99": round(self.var_99, 3),
            "volatility": round(self.volatility, 3),
            "beta_to_market": round(self.beta_to_market, 3),
            "information_ratio": round(self.information_ratio, 3),
            "tracking_error": round(self.tracking_error, 3),
            "ic_mean": round(self.ic_mean, 3),
            "ic_ir": round(self.ic_ir, 3),
            "turnover": round(self.turnover, 3),
            "is_degraded": self.is_degraded,
            "degradation_reason": self.degradation_reason,
        }


class StrategyNotFoundError(KeyError):
    """策略未注册."""


class StrategyAlreadyRegisteredError(ValueError):
    """策略已注册 (且 overwrite=False)."""


# ============================================================
# 策略注册表 (单例)
# ============================================================
class StrategyRegistry:
    """策略注册表 (单例 + RLock).

    设计原则:
        1. 延迟实例化: register() 注册类, get() 首次调用时实例化并缓存
        2. 线程安全: 所有可变操作加 RLock
        3. duck typing: 不强依赖 BaseStrategy, 任何有 name 属性的类即可注册
        4. 审计可追溯: register/unregister/set_active 写入审计日志
        5. flag 守护: USE_INTEGRATED_CORE_REGISTRY 关闭时, 旧路径仍可用
    """

    _instance: StrategyRegistry | None = None
    _lock: RLock = RLock()

    def __init__(self) -> None:
        self._metadata: dict[str, StrategyMetadata] = {}  # name -> 元数据
        self._instances: dict[str, Any] = {}  # name -> 实例缓存
        self._active: dict[str, bool] = {}  # name -> 是否启用
        self._perf_records: dict[str, PerformanceRecord] = {}  # name -> 性能记录
        self._perf_locks: dict[str, RLock] = {}  # name -> 独立锁 (装饰器用)

    # ============================================================
    # 单例接口
    # ============================================================
    @classmethod
    def get_instance(cls) -> StrategyRegistry:
        """获取单例 (线程安全)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (仅测试用)."""
        with cls._lock:
            cls._instance = None

    # ============================================================
    # 注册 / 卸载
    # ============================================================
    def register(
        self,
        name: str,
        strategy_class: type[Any],
        *,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> None:
        """注册策略类 (延迟实例化).

        Args:
            name: 策略名 (唯一 key)
            strategy_class: 策略类 (调用 get() 时实例化)
            metadata: 元数据 (description/version/author/capital/max_weight/min_weight/extra)
            overwrite: 已存在时是否覆盖 (默认 False)

        Raises:
            StrategyAlreadyRegisteredError: 已注册且 overwrite=False
        """
        with self._lock:
            if name in self._metadata and not overwrite:
                raise StrategyAlreadyRegisteredError(f"策略 '{name}' 已注册, overwrite=False 阻止覆盖")

            meta_dict = metadata or {}
            self._metadata[name] = StrategyMetadata(
                name=name,
                strategy_class=strategy_class,
                description=meta_dict.get("description", ""),
                version=meta_dict.get("version", "1.0.0"),
                author=meta_dict.get("author", ""),
                capital=meta_dict.get("capital", 0.0),
                max_weight=meta_dict.get("max_weight", 0.40),
                min_weight=meta_dict.get("min_weight", 0.05),
                extra=meta_dict.get("extra", {}),
            )
            # 清空旧实例 (overwrite 场景)
            self._instances.pop(name, None)
            self._active[name] = True
            self._perf_records[name] = PerformanceRecord(strategy_name=name)
            self._perf_locks[name] = RLock()

            self._audit_log(
                "register",
                name,
                {
                    "class": strategy_class.__name__,
                    "module": getattr(strategy_class, "__module__", ""),
                    "overwrite": overwrite,
                    "metadata": meta_dict,
                },
            )
            logger.info(f"策略已注册: {name} -> {strategy_class.__name__}")

    def unregister(self, name: str) -> bool:
        """卸载策略.

        Returns:
            True 表示成功卸载, False 表示策略未注册
        """
        with self._lock:
            if name not in self._metadata:
                return False
            del self._metadata[name]
            self._instances.pop(name, None)
            self._active.pop(name, None)
            self._perf_records.pop(name, None)
            self._perf_locks.pop(name, None)
            self._audit_log("unregister", name, {})
            logger.info(f"策略已卸载: {name}")
            return True

    # ============================================================
    # 获取 / 查询
    # ============================================================
    def get(self, name: str) -> Any:
        """获取策略实例 (首次获取时实例化并缓存).

        Raises:
            StrategyNotFoundError: 策略未注册
        """
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            if name not in self._instances:
                meta = self._metadata[name]
                try:
                    self._instances[name] = meta.strategy_class()
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                        OSError, ImportError) as e:  # P2 模块 fail-safe, 待后续精确化
                    # 策略实例化可能抛: 构造函数参数错误/类型不匹配/字段缺失/
                    # 属性不存在/运行时错误/IO 异常/依赖未安装
                    logger.error(f"策略实例化失败: {name}, error={e}")
                    raise
            return self._instances[name]

    def get_class(self, name: str) -> type[Any]:
        """仅获取策略类, 不实例化."""
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            return self._metadata[name].strategy_class

    def is_registered(self, name: str) -> bool:
        """检查是否已注册."""
        with self._lock:
            return name in self._metadata

    def list_strategies(self) -> list[str]:
        """返回所有已注册策略名."""
        with self._lock:
            return list(self._metadata.keys())

    def list_active(self) -> list[str]:
        """返回启用中的策略名."""
        with self._lock:
            return [name for name, active in self._active.items() if active]

    def set_active(self, name: str) -> None:
        """启用策略."""
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            self._active[name] = True
            self._audit_log("set_active", name, {})

    def set_inactive(self, name: str) -> None:
        """禁用策略."""
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            self._active[name] = False
            self._audit_log("set_inactive", name, {})

    def get_metadata(self, name: str) -> StrategyMetadata:
        """获取策略元数据."""
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            return self._metadata[name]

    def get_state(self, name: str) -> dict[str, Any]:
        """获取策略完整状态 (元数据 + 性能记录)."""
        with self._lock:
            if name not in self._metadata:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            meta = self._metadata[name]
            perf = self._perf_records.get(name, PerformanceRecord(strategy_name=name))
            return {
                "name": meta.name,
                "class": meta.strategy_class.__name__,
                "description": meta.description,
                "version": meta.version,
                "author": meta.author,
                "capital": meta.capital,
                "max_weight": meta.max_weight,
                "min_weight": meta.min_weight,
                "is_active": self._active.get(name, False),
                "extra": meta.extra,
                "performance": perf.to_dict(),
            }

    def snapshot(self) -> dict[str, Any]:
        """全量快照 (供 bootstrap.dump 使用)."""
        with self._lock:
            return {name: self.get_state(name) for name in self._metadata}

    def clear(self) -> None:
        """清空所有注册 (仅测试用)."""
        with self._lock:
            self._metadata.clear()
            self._instances.clear()
            self._active.clear()
            self._perf_records.clear()
            self._perf_locks.clear()

    # ============================================================
    # 性能记录 (装饰器调用)
    # ============================================================
    def _record_call(
        self,
        name: str,
        latency_ms: float,
        success: bool,
        error_msg: str = "",
    ) -> None:
        """记录一次调用 (装饰器自动调用)."""
        with self._perf_locks.get(name, self._lock):
            perf = self._perf_records.get(name)
            if perf is None:
                return  # 策略已卸载, 丢弃记录
            perf.call_count += 1
            perf.total_latency_ms += latency_ms
            perf.min_latency_ms = min(perf.min_latency_ms, latency_ms)
            perf.max_latency_ms = max(perf.max_latency_ms, latency_ms)
            perf.last_call_ts = datetime.now().isoformat()
            if not success:
                perf.errors_count += 1
                perf.last_error = error_msg

    def update_metrics(self, name: str, metrics: dict[str, Any]) -> None:
        """策略主动更新业务指标 (PnL/Sharpe/IC_IR 等).

        Args:
            name: 策略名
            metrics: 指标字典 (key 与 PerformanceRecord 字段对应)
        """
        with self._perf_locks.get(name, self._lock):
            perf = self._perf_records.get(name)
            if perf is None:
                return
            for key, value in metrics.items():
                if hasattr(perf, key):
                    setattr(perf, key, value)

    def update_alpha_metrics(
        self,
        name: str,
        factor_values: Any,
        forward_returns: Any,
    ) -> None:
        """策略主动更新 Alpha 维度指标 (IC/IC_IR).

        Args:
            name: 策略名
            factor_values: 因子值数组
            forward_returns: 前瞻收益率数组
        """
        try:
            import numpy as np
            from scipy.stats import spearmanr

            if len(factor_values) == 0 or len(forward_returns) == 0:
                return

            factor_arr = np.asarray(factor_values, dtype=float)
            return_arr = np.asarray(forward_returns, dtype=float)

            # Pearson IC
            if len(factor_arr) == len(return_arr) and len(factor_arr) > 1:
                ic = float(np.corrcoef(factor_arr, return_arr)[0, 1])
                # 防御: 常数因子/收益使 corrcoef 返回 NaN, 不可写入 IC 指标 (F-4)
                ic = 0.0 if not np.isfinite(ic) else ic
                ic_rank = float(spearmanr(factor_arr, return_arr).correlation)
                ic_rank = 0.0 if not np.isfinite(ic_rank) else ic_rank
                self.update_metrics(
                    name,
                    {
                        "ic_mean": ic,
                        "ic_ir": ic,  # 单次 IC_IR 近似为 IC (滚动 IC_IR 由策略自行计算)
                    },
                )
                logger.debug(f"策略 {name} IC 更新: ic={ic:.4f}, ic_rank={ic_rank:.4f}")
        except ImportError:
            logger.warning("scipy 未安装, IC 计算跳过")
        except (ValueError, TypeError) as e:
            logger.warning(
                "Alpha 指标计算数据无效: strategy=%s, factor_len=%d, return_len=%d, error=%s",
                name,
                len(factor_values),
                len(forward_returns),
                e,
            )
        except (KeyError, AttributeError, RuntimeError,
                ZeroDivisionError, OSError) as e:
            # scipy.stats 计算或记录过程可能抛: 数据格式/类型错误/
            # 字段缺失/属性不存在/运行时错误/除零/IO 异常
            logger.error(
                "Alpha 指标更新失败: strategy=%s, factor_len=%d, return_len=%d, error=%s",
                name,
                len(factor_values),
                len(forward_returns),
                e,
            )

    def get_performance(self, name: str) -> PerformanceRecord:
        """获取策略性能记录."""
        with self._lock:
            if name not in self._perf_records:
                raise StrategyNotFoundError(f"策略未注册: {name}")
            return self._perf_records[name]

    # ============================================================
    # 装饰器: 类装饰器形式
    # ============================================================
    @classmethod
    def register_decorator(
        cls,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> Callable[[type[Any]], type[Any]]:
        """类装饰器: @StrategyRegistry.register_decorator("name", metadata={...}).

        与实例方法 register() 区分:
            - 实例方法: registry.register("name", StrategyClass)
            - 类装饰器: @StrategyRegistry.register_decorator("name")
        """

        def decorator(strategy_class: type[Any]) -> type[Any]:
            cls.get_instance().register(name, strategy_class, metadata=metadata, overwrite=overwrite)
            return strategy_class

        return decorator

    # ============================================================
    # 审计日志
    # ============================================================
    def _audit_log(self, action: str, name: str, details: dict[str, Any]) -> None:
        """写入审计日志 (失败不阻塞)."""
        try:
            _AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = _AUDIT_LOG_DIR / f"{action}_{name}_{datetime.now().strftime('%Y%m%d')}.jsonl"
            record = {
                "ts": datetime.now().isoformat(),
                "action": action,
                "name": name,
                "details": details,
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
        OSError, ZeroDivisionError, ImportError) as e:  # P2 模块 fail-safe, 待后续精确化
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # OSError — 文件/网络 IO 异常
            # ZeroDivisionError — 除零
            # ImportError — 依赖未安装
            logger.warning(f"审计日志写入失败: {e}")


# ============================================================
# 模块级单例 (推荐用法)
# ============================================================
registry = StrategyRegistry.get_instance()


# ============================================================
# 性能追踪装饰器
# ============================================================
def track_performance(
    strategy_name: str,
    *,
    record_metrics: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """策略性能追踪装饰器.

    Args:
        strategy_name: 策略名 (必须在注册表中)
        record_metrics: 是否记录延迟/异常 (默认 True)

    Behavior:
        - flag USE_INTEGRATED_CORE_REGISTRY=False 时, 装饰器退化为透传 (零开销)
        - flag=True 时, 记录每次调用的延迟/异常/调用次数
        - 不修改原函数签名, 不修改返回值
        - 异常隔离: 指标记录失败不影响业务逻辑

    Usage:
        @track_performance(strategy_name="conservative_v1")
        def generate_signals(context):
            ...

        # flag 关闭时, 等价于:
        def generate_signals(context):
            ...
    """
    # 装饰时预导入 FeatureFlags (避免热路径每次调用都 import)
    _FeatureFlags = None  # noqa: N806
    try:
        from utils.infra.feature_flags import FeatureFlags as _FF

        _FeatureFlags = _FF  # noqa: N806
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
        OSError, ImportError):  # P2 模块 fail-safe, 待后续精确化
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # OSError — IO 异常; ImportError — 依赖未安装
        pass

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 轻量级 flag 检查 (预导入的类直接调用, 不重复 import)
            if _FeatureFlags is None:
                return func(*args, **kwargs)
            try:
                if not _FeatureFlags.get_instance().is_enabled("USE_INTEGRATED_CORE_REGISTRY"):
                    return func(*args, **kwargs)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
        OSError, ImportError):  # P2 模块 fail-safe, 待后续精确化
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # OSError — IO 异常; ImportError — 依赖未安装
                return func(*args, **kwargs)

            # flag 开启: 记录性能
            start_ts = time.perf_counter()
            success = True
            error_msg = ""
            try:
                result = func(*args, **kwargs)
                return result
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
        OSError, ZeroDivisionError, ImportError) as e:  # P2 模块 fail-safe, 待后续精确化
            # ValueError/TypeError — 数据格式/类型错误
            # KeyError/AttributeError — 字段/属性缺失
            # RuntimeError — 运行时错误
            # OSError — 文件/网络 IO 异常
            # ZeroDivisionError — 除零
            # ImportError — 依赖未安装
                success = False
                error_msg = f"{type(e).__name__}: {e}"
                raise
            finally:
                try:
                    latency_ms = (time.perf_counter() - start_ts) * 1000.0
                    StrategyRegistry.get_instance()._record_call(
                        strategy_name,
                        latency_ms=latency_ms,
                        success=success,
                        error_msg=error_msg,
                    )
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError,
                        OSError) as record_err:  # P2 模块 fail-safe, 待后续精确化
                    # 性能记录失败不影响业务: 数据/类型/字段/属性/运行时/IO 异常
                    logger.warning(f"性能记录失败 (不影响业务): strategy={strategy_name}, error={record_err}")

        return wrapper

    return decorator


# ============================================================
# 公共 API
# ============================================================
__all__ = [
    "PerformanceRecord",
    "StrategyAlreadyRegisteredError",
    "StrategyMetadata",
    "StrategyNotFoundError",
    "StrategyRegistry",
    "registry",
    "track_performance",
]
