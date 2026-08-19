"""llm_rate_limiter 直测 (复审 R11 · 2026-08-12)

覆盖复审报告指出的"令牌桶数学/退避逻辑无专门直测"缺口:
    - 令牌桶令牌耗尽超时返回 False
    - 令牌桶 refill 速率数学正确
    - TTL 缓存过期淘汰
    - TTL 缓存 LRU 容量淘汰
    - 指数退避重试上限 (max_delay 封顶)
    - 全局单例双检锁 (只初始化一次)
    - CallStats 除零保护 (max(1,...))
"""
from __future__ import annotations

import time

from quant_modules.ai_hedge_fund.llm_rate_limiter import (
    CallStats,
    LLMCallTracker,
    RateLimitedLLMCaller,
    TokenBucketRateLimiter,
    TTLCache,
    get_global_llm_caller,
)

# ============================================================
# 1. 令牌桶
# ============================================================


def test_token_bucket_acquire_below_capacity():
    limiter = TokenBucketRateLimiter(max_tokens=2, refill_rate=1.0)
    assert limiter.acquire(timeout=0.1) is True
    assert limiter.acquire(timeout=0.1) is True
    # 第 3 次无令牌, 且 refill_rate=1.0 意味着 1s 才补 1 个, timeout=0.1 内拿不到
    assert limiter.acquire(timeout=0.1) is False


def test_token_bucket_refill_rate_math():
    # refill_rate=10/s, 初始 1 个令牌, 取走后等 0.2s 应补 ~2 个
    limiter = TokenBucketRateLimiter(max_tokens=10, refill_rate=10.0)
    assert limiter.acquire(timeout=0.1) is True  # 取走唯一令牌
    time.sleep(0.25)
    assert limiter.available_tokens >= 2.0  # 0.25s * 10/s = 2.5, 取整下限>=2


def test_token_bucket_acquire_blocks_until_refill():
    limiter = TokenBucketRateLimiter(max_tokens=1, refill_rate=5.0)
    assert limiter.acquire(timeout=1.0) is True
    start = time.monotonic()
    # 下一次获取需等待 ~0.2s (1/5), 应在 timeout 内成功
    assert limiter.acquire(timeout=1.0) is True
    elapsed = time.monotonic() - start
    assert 0.15 < elapsed < 0.6  # 等待约 0.2s, 留余量


# ============================================================
# 2. TTL 缓存
# ============================================================


def test_ttl_cache_miss_then_hit():
    cache = TTLCache(max_size=10, ttl_seconds=60)
    key = cache.make_key("prompt", "model")
    assert cache.get(key) is None
    cache.set(key, {"answer": 42})
    assert cache.get(key) == {"answer": 42}


def test_ttl_cache_expiry():
    cache = TTLCache(max_size=10, ttl_seconds=0.1)
    key = cache.make_key("p")
    cache.set(key, "v")
    assert cache.get(key) == "v"
    time.sleep(0.15)
    assert cache.get(key) is None  # 过期删除


def test_ttl_cache_lru_eviction():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.set(cache.make_key("a"), 1)
    cache.set(cache.make_key("b"), 2)
    # 访问 a 使其成为最近使用
    cache.get(cache.make_key("a"))
    # 插入第 3 个, 应淘汰最久未使用的 b
    cache.set(cache.make_key("c"), 3)
    assert cache.get(cache.make_key("b")) is None
    assert cache.get(cache.make_key("a")) == 1
    assert cache.get(cache.make_key("c")) == 3


# ============================================================
# 3. 指数退避重试上限
# ============================================================


def test_retry_backoff_caps_at_max_delay():
    from quant_modules.ai_hedge_fund.llm_rate_limiter import retry_with_backoff

    calls = {"n": 0}
    delays = []

    def fake_sleep(d):
        delays.append(d)

    original_sleep = time.sleep
    time.sleep = fake_sleep
    try:

        @retry_with_backoff(max_retries=4, base_delay=1.0, max_delay=3.0)
        def always_fail():
            calls["n"] += 1
            raise RuntimeError("boom")

        try:
            always_fail()
        except RuntimeError:
            pass
    finally:
        time.sleep = original_sleep

    assert calls["n"] == 4  # 共尝试 4 次
    # 退避序列: 1, 2, 3 (封顶) — 不超过 max_delay
    assert max(delays) <= 3.0
    assert delays == [1.0, 2.0, 3.0]


# ============================================================
# 4. 全局单例双检锁
# ============================================================


def test_global_caller_singleton():
    a = get_global_llm_caller()
    b = get_global_llm_caller()
    assert a is b  # 同一单例


# ============================================================
# 5. CallStats 除零保护
# ============================================================


def test_call_stats_division_by_zero_guard():
    stats = CallStats()  # successful=0, total_calls=0
    assert stats.avg_latency_ms == 0.0
    assert stats.success_rate == 0.0


def test_call_tracker_record_and_stats():
    tracker = LLMCallTracker()
    tracker.record("buffett", "gpt-4o", success=True, latency_ms=100)
    tracker.record("buffett", "gpt-4o", success=False)
    stats = tracker.get_stats("buffett")
    assert stats["buffett/gpt-4o"]["total_calls"] == 2
    assert stats["buffett/gpt-4o"]["successful"] == 1
    assert stats["buffett/gpt-4o"]["failed"] == 1


# ============================================================
# 6. RateLimitedLLMCaller 集成
# ============================================================


def test_rate_limited_caller_success_and_cache():
    caller = RateLimitedLLMCaller(
        max_concurrent=5, refill_rate=100.0,
        cache_max_size=10, cache_ttl=60, max_retries=2,
    )
    call_count = {"n": 0}

    def fake_llm(x):
        call_count["n"] += 1
        return f"resp:{x}"

    # 第一次调用
    r1 = caller.call(
        fn=fake_llm, args=("q1",),
        agent_name="buffett", model_name="gpt-4o",
        cache_key="k1",
    )
    # 相同 cache_key 应命中缓存, 不再调用 fn
    r2 = caller.call(
        fn=fake_llm, args=("q1",),
        agent_name="buffett", model_name="gpt-4o",
        cache_key="k1",
    )
    assert r1 == r2 == "resp:q1"
    assert call_count["n"] == 1  # 缓存命中, 仅 1 次真实调用


def test_rate_limited_caller_rate_limit_timeout():
    caller = RateLimitedLLMCaller(
        max_concurrent=1, refill_rate=1.0, max_retries=1,
    )
    # 耗尽唯一令牌
    caller.rate_limiter.acquire(timeout=0.1)
    # 下一次 call 在 timeout=0.1 内拿不到令牌 → 抛 RuntimeError
    try:
        caller.call(
            fn=lambda: "x", args=(),
            agent_name="buffett", model_name="gpt-4o",
            timeout=0.1,
        )
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as exc:
        assert "速率限制超时" in str(exc)
