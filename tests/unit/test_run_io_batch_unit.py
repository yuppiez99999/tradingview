# -*- coding: utf-8 -*-
"""run_io_batch 单元测试 (B2.1)

覆盖场景:
1. 正常并发执行 + 结果顺序
2. 空列表快速返回
3. 单项超时 → 降级值
4. 单项异常 → 降级值
5. 进度回调被正确调用
6. fail_default 自定义值
7. timeout=None 不限制
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 确保 utils 在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.concurrency import run_io_batch


class TestRunIoBatchSuccess:
    """正常并发执行"""

    def test_basic_map(self):
        """基本映射: items → fn(items) 顺序一致"""
        items = [1, 2, 3, 4, 5]
        results = run_io_batch(items, lambda x: x * 2)
        assert results == [2, 4, 6, 8, 10]

    def test_concurrent_faster_than_serial(self):
        """并发应比串行快 (sleep 场景)"""
        items = list(range(8))

        def slow_fn(x):
            time.sleep(0.1)
            return x

        t0 = time.time()
        results = run_io_batch(items, slow_fn, max_workers=8, timeout=5.0)
        elapsed = time.time() - t0

        assert results == items
        # 8 项 * 0.1s 串行 = 0.8s, 并发应 < 0.4s
        assert elapsed < 0.4, f"并发执行耗时 {elapsed:.2f}s 过长, 可能未真正并发"

    def test_max_workers_capped_by_items_count(self):
        """max_workers 超过 items 数量时应自动限制"""
        items = [1, 2]
        results = run_io_batch(items, lambda x: x, max_workers=100)
        assert results == [1, 2]


class TestRunIoBatchEmpty:
    """空列表"""

    def test_empty_returns_empty(self):
        """空列表应立即返回空列表"""
        results = run_io_batch([], lambda x: x)
        assert results == []


class TestRunIoBatchTimeout:
    """超时降级"""

    def test_timeout_returns_fallback(self):
        """超时项应返回降级值 (None)"""
        items = ["fast", "slow"]

        def fn(x):
            if x == "slow":
                time.sleep(2.0)
            return x

        results = run_io_batch(items, fn, max_workers=2, timeout=0.3)
        # fast 完成, slow 超时
        assert results[0] == "fast"
        assert results[1] is None  # 默认降级

    def test_timeout_custom_fallback(self):
        """超时应返回自定义 fail_default"""
        items = ["slow"]

        def fn(x):
            time.sleep(2.0)
            return x

        results = run_io_batch(items, fn, max_workers=1, timeout=0.2, fail_default="TIMEOUT")
        assert results == ["TIMEOUT"]

    def test_no_timeout(self):
        """timeout=None 应不限制"""
        items = [1, 2, 3]

        def fn(x):
            time.sleep(0.05)
            return x * 10

        results = run_io_batch(items, fn, max_workers=3, timeout=None)
        assert results == [10, 20, 30]


class TestRunIoBatchException:
    """异常降级"""

    def test_exception_returns_fallback(self):
        """异常项应返回降级值"""
        items = [1, 2, 3]

        def fn(x):
            if x == 2:
                raise ValueError("boom")
            return x

        results = run_io_batch(items, fn, max_workers=3, timeout=5.0)
        assert results[0] == 1
        assert results[1] is None  # 异常 → 降级
        assert results[2] == 3

    def test_exception_custom_fallback(self):
        """异常应返回自定义 fail_default"""
        items = [1]

        def fn(x):
            raise RuntimeError("error")

        results = run_io_batch(items, fn, max_workers=1, timeout=5.0, fail_default=-1)
        assert results == [-1]


class TestRunIoBatchProgress:
    """进度回调"""

    def test_progress_cb_called(self):
        """进度回调应被调用 completed_count 次"""
        items = list(range(5))
        progress_log = []

        def cb(completed, total):
            progress_log.append((completed, total))

        results = run_io_batch(items, lambda x: x, max_workers=3, progress_cb=cb)

        assert results == items
        assert len(progress_log) == 5
        # 最后一次回调应为 (5, 5)
        assert progress_log[-1] == (5, 5)
        # total 应始终为 5
        assert all(t == 5 for _, t in progress_log)

    def test_progress_cb_exception_ignored(self):
        """进度回调异常不应影响主流程"""
        items = [1, 2, 3]

        def bad_cb(completed, total):
            raise RuntimeError("cb error")

        results = run_io_batch(items, lambda x: x, max_workers=2, progress_cb=bad_cb)
        assert results == [1, 2, 3]


class TestRunIoBatchStringItems:
    """字符串/复杂类型 items"""

    def test_string_items(self):
        """字符串 items 应正常处理"""
        items = ["hello", "world", "test"]
        results = run_io_batch(items, lambda s: s.upper(), max_workers=3)
        assert results == ["HELLO", "WORLD", "TEST"]

    def test_dict_items(self):
        """dict items 应正常处理"""
        items = [{"code": "001"}, {"code": "002"}]
        results = run_io_batch(items, lambda d: d["code"], max_workers=2)
        assert results == ["001", "002"]
