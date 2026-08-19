"""LLM 速率限制 + 缓存优化 (Wave 6 W6.2.4, DanisHack 风格)

借鉴 DanisHack/ai-hedge-fund 的速率限制和缓存优化机制,
在 LLM 调用层增加:
    1. 令牌桶速率限制 (防止短时间内大量调用触发 429)
    2. 简单 TTL 缓存 (相同 prompt 在 TTL 内复用结果)
    3. 指数退避重试 (429 时自动等待 + 重试)
    4. 调用统计 (按 agent / model 维度计数, 便于成本管控)

与现有系统关系:
    - 包装在 call_llm 之上, 透明代理 (不修改 call_llm 签名)
    - 与 ai_coordinator.py 的成本管控互补:
      ai_coordinator 管全局预算, 本模块管单次调用频率

设计参考:
    - DanisHack/ai-hedge-fund: rate_limit decorator + response_cache
    - virattt/ai-hedge-fund: Agent 角色模板 (本系统的 persona_prompts.py)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("ai_hedge_fund.rate_limiter")


# ============================================================
# 1. 令牌桶速率限制器
# ============================================================


class TokenBucketRateLimiter:
    """令牌桶速率限制器 (线程安全)

    参数:
        max_tokens: 桶容量 (最大并发请求数)
        refill_rate: 每秒补充的令牌数 (请求频率上限)

    用法:
        limiter = TokenBucketRateLimiter(max_tokens=10, refill_rate=2.0)
        if limiter.acquire(timeout=30):
            result = call_llm(...)
        else:
            result = fallback()
    """

    def __init__(self, max_tokens: int = 10, refill_rate: float = 2.0):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """获取一个令牌 (阻塞等待直到有令牌或超时)

        Returns:
            True 如果获取成功, False 如果超时
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # 计算需要等待的时间
                wait = (1.0 - self._tokens) / self.refill_rate
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(wait, remaining))

    def _refill(self) -> None:
        """补充令牌 (必须在锁内调用)"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        """当前可用令牌数 (近似值)"""
        with self._lock:
            self._refill()
            return self._tokens


# ============================================================
# 2. TTL 缓存 (LRU + 过期淘汰)
# ============================================================


class TTLCache:
    """简单 TTL + LRU 缓存 (线程安全)

    参数:
        max_size: 最大缓存条目数
        ttl_seconds: 每条缓存的存活时间 (秒)

    用法:
        cache = TTLCache(max_size=100, ttl_seconds=3600)
        key = cache.make_key(prompt_text, model_name)
        cached = cache.get(key)
        if cached is None:
            result = call_llm(...)
            cache.set(key, result)
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def make_key(*args: Any) -> str:
        """根据参数生成缓存键 (SHA256)"""
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值, 过期则删除"""
        with self._lock:
            if key not in self._store:
                return None
            ts, value = self._store[key]
            if time.monotonic() - ts > self.ttl_seconds:
                del self._store[key]
                return None
            # LRU: 移到末尾 (最近使用)
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        """设置缓存值, 超容量时淘汰最久未使用的"""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic(), value)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)  # FIFO 淘汰最旧的

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


# ============================================================
# 3. 指数退避重试
# ============================================================


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """指数退避重试装饰器

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟 (秒)
        max_delay: 最大延迟 (秒)
        exceptions: 触发重试的异常类型

    用法:
        @retry_with_backoff(max_retries=3, base_delay=2.0)
        def my_llm_call(prompt):
            return call_llm(prompt, ...)
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            "调用失败 (attempt %d/%d): %r, %.1fs 后重试",
                            attempt + 1, max_retries, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error("调用失败, 已达最大重试 %d 次: %r", max_retries, exc)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ============================================================
# 4. 调用统计
# ============================================================


@dataclass
class CallStats:
    """LLM 调用统计 (按 agent / model 维度)"""
    total_calls: int = 0
    successful: int = 0
    failed: int = 0
    cache_hits: int = 0
    rate_limited: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(1, self.successful)

    @property
    def success_rate(self) -> float:
        return self.successful / max(1, self.total_calls)

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "successful": self.successful,
            "failed": self.failed,
            "cache_hits": self.cache_hits,
            "rate_limited": self.rate_limited,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "success_rate": round(self.success_rate, 4),
        }


class LLMCallTracker:
    """LLM 调用统计追踪器 (线程安全)

    用法:
        tracker = LLMCallTracker()
        tracker.record("warren_buffett", "gpt-4o-mini", success=True, latency_ms=1200)
        stats = tracker.get_stats("warren_buffett")
    """

    def __init__(self) -> None:
        self._stats: dict[str, CallStats] = defaultdict(CallStats)
        self._lock = threading.Lock()

    def record(
        self,
        agent_name: str,
        model_name: str = "",
        success: bool = True,
        latency_ms: float = 0.0,
        cache_hit: bool = False,
        rate_limited: bool = False,
    ) -> None:
        key = f"{agent_name}/{model_name}" if model_name else agent_name
        with self._lock:
            stats = self._stats[key]
            stats.total_calls += 1
            if cache_hit:
                stats.cache_hits += 1
            elif rate_limited:
                stats.rate_limited += 1
            elif success:
                stats.successful += 1
                stats.total_latency_ms += latency_ms
            else:
                stats.failed += 1

    def get_stats(self, agent_name: str = "") -> dict[str, dict]:
        with self._lock:
            if agent_name:
                key_prefix = agent_name
                return {
                    k: v.to_dict() for k, v in self._stats.items()
                    if k.startswith(key_prefix)
                }
            return {k: v.to_dict() for k, v in self._stats.items()}

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()


# ============================================================
# 5. 统一包装器: RateLimitedLLMCaller
# ============================================================


class RateLimitedLLMCaller:
    """统一 LLM 调用包装器 (速率限制 + 缓存 + 重试 + 统计)

    借鉴 DanisHack 的 rate_limit + response_cache 整合设计。

    用法:
        caller = RateLimitedLLMCaller(
            max_concurrent=5, refill_rate=2.0,
            cache_ttl=3600, max_retries=3,
        )
        result = caller.call(
            fn=call_llm,
            args=(prompt, pydantic_model),
            kwargs={"agent_name": "warren_buffett", "state": state},
            agent_name="warren_buffett",
            model_name="gpt-4o-mini",
            cache_key=None,  # None=不缓存; 传入字符串则启用缓存
        )
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        refill_rate: float = 2.0,
        cache_max_size: int = 100,
        cache_ttl: int = 3600,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
    ):
        self.rate_limiter = TokenBucketRateLimiter(
            max_tokens=max_concurrent, refill_rate=refill_rate,
        )
        self.cache = TTLCache(max_size=cache_max_size, ttl_seconds=cache_ttl)
        self.tracker = LLMCallTracker()
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    def call(
        self,
        fn: Callable,
        args: tuple = (),
        kwargs: Optional[dict[str, Any]] = None,
        agent_name: str = "",
        model_name: str = "",
        cache_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> Any:
        """带速率限制 + 缓存 + 重试的 LLM 调用

        Args:
            fn: LLM 调用函数 (如 call_llm)
            args: 位置参数
            kwargs: 关键字参数
            agent_name: Agent 名称 (统计用)
            model_name: 模型名称 (统计用)
            cache_key: 缓存键 (None=不缓存)
            timeout: 速率限制等待超时 (秒)

        Returns:
            LLM 调用结果
        """
        kwargs = kwargs or {}

        # 1. 缓存检查
        if cache_key is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.tracker.record(agent_name, model_name, cache_hit=True)
                logger.debug("缓存命中: %s/%s", agent_name, cache_key[:16])
                return cached

        # 2. 速率限制
        if not self.rate_limiter.acquire(timeout=timeout):
            self.tracker.record(agent_name, model_name, rate_limited=True)
            logger.warning("速率限制超时: %s (等待 %.0fs 无令牌)", agent_name, timeout)
            raise RuntimeError(f"LLM 速率限制超时: {agent_name}")

        # 3. 指数退避重试调用
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                latency = (time.monotonic() - start) * 1000
                self.tracker.record(
                    agent_name, model_name, success=True, latency_ms=latency,
                )
                # 写入缓存
                if cache_key is not None:
                    self.cache.set(cache_key, result)
                return result
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    delay = min(self.retry_base_delay * (2 ** attempt), 30.0)
                    logger.warning(
                        "LLM 调用失败 %s (attempt %d/%d): %r, %.1fs 后重试",
                        agent_name, attempt + 1, self.max_retries, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    self.tracker.record(agent_name, model_name, success=False)

        raise last_exc  # type: ignore[misc]

    @property
    def stats(self) -> dict[str, dict]:
        return self.tracker.get_stats()

    def reset(self) -> None:
        self.cache.clear()
        self.tracker.reset()


# ============================================================
# 6. 全局单例 (供整个 ai_hedge_fund 模块共享)
# ============================================================


_global_caller: Optional[RateLimitedLLMCaller] = None
_global_lock = threading.Lock()


def get_global_llm_caller(
    max_concurrent: int = 5,
    refill_rate: float = 2.0,
    cache_ttl: int = 3600,
    max_retries: int = 3,
) -> RateLimitedLLMCaller:
    """获取全局 LLM 调用器单例 (线程安全懒初始化)"""
    global _global_caller
    if _global_caller is None:
        with _global_lock:
            if _global_caller is None:
                _global_caller = RateLimitedLLMCaller(
                    max_concurrent=max_concurrent,
                    refill_rate=refill_rate,
                    cache_ttl=cache_ttl,
                    max_retries=max_retries,
                )
    return _global_caller
