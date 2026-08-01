"""
熔断器 (Circuit Breaker) — 对冲基金级容错

世界顶级对冲基金的数据源管理核心：当数据源连续失败时自动熔断，
避免雪崩效应。支持半开探测、自动恢复、指数退避重试。

三种状态:
  CLOSED  → 正常请求，计数失败
  OPEN    → 拒绝请求，直接返回 fallback
  HALF_OPEN → 允许少量探测请求，成功则恢复，失败则继续熔断

用法:
    cb = CircuitBreaker("wind_mcp", failure_threshold=3, recovery_timeout=30)

    @cb.protect
    def fetch_quote(code):
        ...

    # 或手动控制
    if cb.allow_request():
        try:
            result = do_request()
            cb.on_success()
        except Exception as e:
            cb.on_failure(e)
"""

from __future__ import annotations

import functools
import logging
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

# [V75] from ..utils.alert_notifier import  # 需在v7.5创建alert_notifier AlertNotifier, AlertLevel
# TODO(v8.5): 创建独立的 alert_notifier 模块, 替换下方 stub
try:
    from utils.stop_loss import AlertLevel  # type: ignore
except ImportError:

    class AlertLevel(Enum):  # type: ignore  # stub
        NORMAL = "normal"
        WARNING = "warning"
        CRITICAL = "critical"
        TRIGGERED = "triggered"


class _AlertNotifierStub:
    """AlertNotifier的stub实现，用于测试环境"""

    def quick_alert(self, title: str, content: str, level: AlertLevel, source: str):
        pass  # 空实现，仅避免测试报错


logger = logging.getLogger("circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"  # 正常
    OPEN = "open"  # 熔断
    HALF_OPEN = "half_open"  # 半开探测


@dataclass
class CircuitStats:
    """熔断器统计"""

    total_requests: int = 0
    total_failures: int = 0
    total_successes: int = 0
    total_timeouts: int = 0
    last_failure_time: float = 0.0
    last_failure_reason: str = ""
    state_changes: int = 0
    current_state: CircuitState = CircuitState.CLOSED


class CircuitBreaker:
    """熔断器 — 线程安全"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_requests: int = 2,
        consecutive_successes_to_close: int = 3,
    ):
        """
        Args:
            name: 熔断器名称 (如 "wind_mcp", "ifind_mcp")
            failure_threshold: 连续失败多少次后熔断
            recovery_timeout: 熔断后多少秒进入半开状态
            half_open_max_requests: 半开状态下允许的最大探测请求数
            consecutive_successes_to_close: 半开状态下连续成功多少次后恢复
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max_requests
        self._close_successes = consecutive_successes_to_close
        # AlertNotifier未实现，使用stub
        # self._alert_notifier = AlertNotifier()
        self._alert_notifier = _AlertNotifierStub()

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._last_failure_reason = ""
        self._opened_at = 0.0
        self._half_open_requests = 0
        self._half_open_successes = 0
        self._lock = threading.RLock()
        self._stats = CircuitStats()

    # ── 状态查询 ──

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition()
            return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def get_stats(self) -> CircuitStats:
        with self._lock:
            stats = CircuitStats(
                total_requests=self._stats.total_requests,
                total_failures=self._stats.total_failures,
                total_successes=self._stats.total_successes,
                total_timeouts=self._stats.total_timeouts,
                last_failure_time=self._last_failure_time,
                last_failure_reason=self._last_failure_reason,
                state_changes=self._stats.state_changes,
                current_state=self._state,
            )
            return stats

    # ── 状态转换 ──

    def _maybe_transition(self):
        """检查是否需要状态转换"""
        now = time.time()

        if self._state == CircuitState.OPEN:
            if now - self._opened_at >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_requests = 0
                self._half_open_successes = 0
                self._stats.state_changes += 1
                logger.info(f"[{self.name}] 熔断器进入半开状态 (熔断{now - self._opened_at:.0f}s后尝试恢复)")

    def _trip(self, reason: str = ""):
        """触发熔断"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                return  # 已熔断
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
            self._last_failure_reason = reason
            self._stats.state_changes += 1
            logger.warning(
                f"[{self.name}] 熔断器触发! "
                f"失败{self._failure_count}次, 原因: {reason}, "
                f"将在{self._recovery_timeout}s后尝试恢复"
            )
            # AlertNotifier未实现，跳过通知
            # self._alert_notifier.quick_alert(
            #     title=f"数据源熔断 [{self.name}]",
            #     content=f"连续失败{self._failure_count}次，原因: {reason}，{self._recovery_timeout:.0f}s后尝试恢复",
            #     level="CRITICAL",
            #     source="circuit_breaker",
            # )

    def _reset(self):
        """重置熔断器到关闭状态"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_requests = 0
            self._half_open_successes = 0
            self._stats.state_changes += 1
            logger.info(f"[{self.name}] 熔断器已恢复")
            # [V75] AlertLevel暂时注释，待v7.5创建alert_notifier
            # self._alert_notifier.quick_alert(
            #     title=f"数据源恢复 [{self.name}]",
            #     content="熔断器已恢复，数据源重新可用",
            #     level=AlertLevel.INFO,
            #     source="circuit_breaker",
            # )

    # ── 请求控制 ──

    def allow_request(self) -> bool:
        """是否允许此次请求"""
        with self._lock:
            self._maybe_transition()

            if self._state == CircuitState.CLOSED:
                self._stats.total_requests += 1
                return True

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_requests < self._half_open_max:
                    self._half_open_requests += 1
                    self._stats.total_requests += 1
                    return True
                return False

            # OPEN
            return False

    def on_success(self):
        """请求成功回调"""
        with self._lock:
            self._stats.total_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self._close_successes:
                    self._reset()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # 成功后重置失败计数

    def on_failure(self, error: Exception | None = None):
        """请求失败回调"""
        with self._lock:
            self._stats.total_failures += 1
            self._last_failure_time = time.time()
            reason = str(error)[:200] if error else "unknown"
            self._last_failure_reason = reason

            if self._state == CircuitState.HALF_OPEN:
                self._trip(reason)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._trip(reason)

    def on_timeout(self):
        """超时回调"""
        with self._lock:
            self._stats.total_timeouts += 1
        self.on_failure(TimeoutError("request timeout"))

    # ── 装饰器 ──

    def protect(self, func: Callable | None = None, *, fallback: Any = None):
        """装饰器: 自动熔断保护"""

        def decorator(f):
            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                if not self.allow_request():
                    logger.debug(f"[{self.name}] 熔断中，返回 fallback")
                    return fallback
                try:
                    result = f(*args, **kwargs)
                    self.on_success()
                    return result
                except Exception as e:
                    self.on_failure(e)
                    if fallback is not None:
                        return fallback
                    raise

            return wrapper

        if func is not None:
            return decorator(func)
        return decorator

    def __enter__(self):
        if not self.allow_request():
            raise CircuitOpenError(f"熔断器 [{self.name}] 已打开: {self._last_failure_reason}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.on_failure(exc_val)
            return False  # 不吞掉异常
        self.on_success()
        return False


class CircuitOpenError(Exception):
    """熔断器打开异常"""

    pass


class CircuitBreakerRegistry:
    """熔断器注册表 — 集中管理所有数据源的熔断状态"""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> CircuitBreaker:
        """获取或创建熔断器"""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name, failure_threshold=failure_threshold, recovery_timeout=recovery_timeout
                )
            return self._breakers[name]

    def get_all_stats(self) -> dict[str, CircuitStats]:
        """获取所有熔断器统计"""
        return {name: cb.get_stats() for name, cb in self._breakers.items()}

    def get_open_breakers(self) -> list:
        """获取当前打开的熔断器列表"""
        return [name for name, cb in self._breakers.items() if cb.is_open]

    def reset_all(self):
        """重置所有熔断器"""
        for cb in self._breakers.values():
            cb._reset()


# ── 重试工具 ──


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError, OSError),
):
    """
    指数退避重试装饰器 (带随机抖动)

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟 (秒)
        max_delay: 最大延迟 (秒)
        backoff_factor: 退避因子
        jitter: 是否添加随机抖动 (避免惊群效应)
        retryable_exceptions: 可重试的异常类型
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (backoff_factor**attempt), max_delay)
                        if jitter:
                            delay = delay * (0.5 + random.random())
                        logger.debug(f"[Retry] {func.__name__} 第{attempt + 1}次重试, 等待{delay:.2f}s: {e}")
                        time.sleep(delay)
                    else:
                        logger.warning(f"[Retry] {func.__name__} 重试{max_retries}次后仍失败: {e}")
                except Exception:
                    # 不可重试的异常直接抛出
                    raise
            raise last_exception

        return wrapper

    return decorator
