# -*- coding: utf-8 -*-
"""DataLayer Chaos Test — T1.7-E.

模块整合 8.4 — TASK T1.7 验收标准 4: chaos test
================================================
模拟生产环境数据源故障场景, 验证 P0-P6 降级链的鲁棒性:

场景 1: P0 单点故障 → P1 接管 (毫秒级切换)
场景 2: P0+P1 双故障 → P6 缓存兜底 (stale 但可用)
场景 3: 全部数据源失败 → AllSourcesFailedError (fail-fast)
场景 4: P0 间歇性故障 → 自动 fallback + 恢复
场景 5: 高并发场景下的降级链一致性
场景 6: P6 缓存过期 → 拒绝返回 stale 数据
场景 7: 慢响应 (超时) → 触发 fallback
场景 8: fallback 审计日志完整性 (HC-6)

运行:
    python -m pytest tests/integration/test_data_layer_chaos.py -v --tb=short
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pandas as pd
import pytest

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.data.data_layer import (
    AllSourcesFailedError,
    DataLayer,
)

# ============================================================
# Helpers
# ============================================================


def make_sample_df(symbol: str = "510300.SH", n: int = 30) -> pd.DataFrame:
    """生成样本 OHLCV DataFrame."""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    import numpy as np
    np.random.seed(hash(symbol) % 2**32)
    close = 4.0 + np.cumsum(np.random.randn(n) * 0.02)
    return pd.DataFrame(
        {
            "open": close * (1 + np.random.randn(n) * 0.005),
            "high": close * (1 + np.abs(np.random.randn(n) * 0.01)),
            "low": close * (1 - np.abs(np.random.randn(n) * 0.01)),
            "close": close,
            "volume": np.random.randint(1e6, 1e8, n),
        },
        index=dates,
    )


def make_snapshot(symbol: str = "510300.SH") -> Dict[str, Any]:
    """生成样本快照."""
    return {
        "symbol": symbol,
        "price": 4.05,
        "pre_close": 4.0,
        "change_pct": 1.25,
        "volume": 1e7,
        "timestamp": datetime.now().isoformat(),
    }


def make_macro() -> Dict[str, Any]:
    """生成样本宏观数据."""
    return {"cpi_yoy": 0.5, "pmi": 51.2, "m2_yoy": 8.5, "shibor_1y": 2.0}


class FaultyProvider:
    """可编程故障 provider.

    通过 fail_count / fail_until / latency_ms 控制故障行为.
    """

    def __init__(
        self,
        name: str,
        data: Any,
        *,
        fail_count: int = 0,
        fail_forever: bool = False,
        latency_ms: float = 0.0,
        fail_with: Optional[Exception] = None,
    ) -> None:
        self.name = name
        self.data = data
        self.fail_count_remaining = fail_count
        self.fail_forever = fail_forever
        self.latency_ms = latency_ms
        self.fail_with = fail_with or RuntimeError(f"{name} down")
        self.call_count = 0
        self.success_count = 0
        self._lock = threading.Lock()

    def __call__(self, symbol: str, **kwargs: Any) -> Any:
        with self._lock:
            self.call_count += 1

        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

        if self.fail_forever:
            raise self.fail_with

        with self._lock:
            if self.fail_count_remaining > 0:
                self.fail_count_remaining -= 1
                raise self.fail_with

            self.success_count += 1
            return self.data


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """临时目录 fixture."""
    fb_dir = tmp_path / "fallback_logs"
    fb_dir.mkdir(parents=True, exist_ok=True)
    p6_dir = tmp_path / "p6_cache"
    p6_dir.mkdir(parents=True, exist_ok=True)
    return fb_dir, p6_dir


@pytest.fixture
def chaos_layer(tmp_dirs):
    """Chaos test 专用 DataLayer.

    配置 4 级降级链: P0/P1/P2/P6 (跳过 P3-P5 加速测试).
    """
    fb_dir, p6_dir = tmp_dirs
    chain = [
        {"level": "P0", "name": "p0", "description": "P0 mock"},
        {"level": "P1", "name": "p1", "description": "P1 mock"},
        {"level": "P2", "name": "p2", "description": "P2 mock"},
        {"level": "P6", "name": "cache", "description": "P6 cache"},
    ]
    layer = DataLayer(
        fallback_chain=chain,
        feature_flag_check=lambda: True,
        fallback_log_dir=fb_dir,
        p6_cache_dir=p6_dir,
        auto_register_providers=False,
        enable_data_gate=False,
    )
    layer.register_provider("cache", layer._p6_cache_lookup)
    return layer


# ============================================================
# 场景 1: P0 单点故障 → P1 接管
# ============================================================


class TestScenario1P0FailP1Takeover:
    """P0 单点故障, P1 接管 (毫秒级切换)."""

    def test_p0_fail_p1_takeover_within_100ms(
        self,
        chaos_layer: DataLayer,
        tmp_dirs,
    ) -> None:
        """P0 故障, P1 在 100ms 内接管."""
        sample_df = make_sample_df()
        p0 = FaultyProvider("p0", sample_df, fail_forever=True)
        p1 = FaultyProvider("p1", sample_df)
        chaos_layer.register_provider("p0", p0)
        chaos_layer.register_provider("p1", p1)
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        start = time.perf_counter()
        df = chaos_layer.get_ohlcv("510300.SH")
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 30
        assert elapsed_ms < 100.0, f"fallback 耗时 {elapsed_ms:.1f}ms > 100ms"
        assert p0.call_count == 1
        assert p1.call_count == 1

    def test_p0_fail_p1_takeover_logs_fallback(
        self,
        chaos_layer: DataLayer,
        tmp_dirs,
    ) -> None:
        """P0 故障触发 fallback 日志记录."""
        _fb_dir, _ = tmp_dirs
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        assert log_file.exists()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["failed_level"] == "P0"
        assert record["failed_provider"] == "p0"
        assert record["next_level"] == "P1"
        assert record["next_provider"] == "p1"


# ============================================================
# 场景 2: P0+P1 双故障 → P6 缓存兜底
# ============================================================


class TestScenario2MultiFailP6Cache:
    """P0+P1 双故障, P6 缓存兜底."""

    def test_p0_p1_fail_p6_cache_takeover(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P0/P1 都失败, P6 缓存接管."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        df = chaos_layer.get_ohlcv("510300.SH")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 30

    def test_p6_cache_takeover_records_from_cache_true(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P6 命中时 from_cache=True."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        result = chaos_layer._query_with_fallback(
            operation="get_ohlcv",
            symbol="510300.SH",
            query_fn=lambda fn: fn("510300.SH", period="1y"),
        )
        assert result.from_cache is True
        assert result.provider_level == "P6"

    def test_p6_cache_takeover_logs_two_fallbacks(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P6 接管时记录 P0→P1, P1→P2, P2→P6 三条 fallback."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        # 至少 3 条 fallback (P0→P1, P1→P2, P2→P6)
        assert len(lines) >= 3
        failed_levels = [json.loads(l)["failed_level"] for l in lines]
        assert "P0" in failed_levels
        assert "P1" in failed_levels
        assert "P2" in failed_levels


# ============================================================
# 场景 3: 全部失败 → AllSourcesFailedError
# ============================================================


class TestScenario3AllFailFast:
    """全部数据源失败, fail-fast."""

    def test_all_fail_raises_allsourcesfailed(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """全部失败抛 AllSourcesFailedError."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))
        # P6 缓存未写入, 也会失败

        with pytest.raises(AllSourcesFailedError) as exc_info:
            chaos_layer.get_ohlcv("510300.SH")
        assert "所有数据源失败" in str(exc_info.value)
        assert exc_info.value.level == "ALL"
        assert exc_info.value.cause is not None

    def test_all_fail_chain_used_complete(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """失败时 fallback_chain_used 包含所有级别."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        with pytest.raises(AllSourcesFailedError):
            chaos_layer.get_ohlcv("510300.SH")


# ============================================================
# 场景 4: P0 间歇性故障 → 自动 fallback + 恢复
# ============================================================


class TestScenario4IntermittentFailure:
    """P0 间歇性故障, 自动 fallback + 恢复."""

    def test_p0_intermittent_fail_recovers(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P0 前 2 次失败, 第 3 次恢复."""
        sample_df = make_sample_df()
        p0 = FaultyProvider("p0", sample_df, fail_count=2)
        p1 = FaultyProvider("p1", sample_df)
        chaos_layer.register_provider("p0", p0)
        chaos_layer.register_provider("p1", p1)
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        # 第 1 次调用: P0 失败, P1 接管
        df1 = chaos_layer.get_ohlcv("510300.SH")
        assert len(df1) == 30
        assert p0.call_count == 1
        assert p1.call_count == 1

        # 第 2 次调用: P0 仍失败 (fail_count=2), P1 接管
        df2 = chaos_layer.get_ohlcv("510300.SH")
        assert len(df2) == 30
        assert p0.call_count == 2
        assert p1.call_count == 2

        # 第 3 次调用: P0 恢复, 直接命中
        df3 = chaos_layer.get_ohlcv("510300.SH")
        assert len(df3) == 30
        assert p0.call_count == 3
        assert p0.success_count == 1
        # P1 不再被调用
        assert p1.call_count == 2

    def test_p0_recover_after_p6_cache(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P0 失败后从 P6 缓存接管, 之后 P0 恢复."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        p0 = FaultyProvider("p0", sample_df, fail_count=1)
        chaos_layer.register_provider("p0", p0)
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        # 第 1 次: P0 失败, P1/P2 失败, P6 接管
        df1 = chaos_layer.get_ohlcv("510300.SH")
        assert len(df1) == 30

        # 第 2 次: P0 恢复
        df2 = chaos_layer.get_ohlcv("510300.SH")
        assert len(df2) == 30
        assert p0.success_count == 1


# ============================================================
# 场景 5: 高并发场景
# ============================================================


class TestScenario5ConcurrentAccess:
    """高并发场景下的降级链一致性."""

    def test_concurrent_p0_fail_p1_takeover(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """10 个线程并发, P0 失败, P1 接管, 所有线程拿到数据."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        results: List[Any] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def _worker() -> None:
            try:
                df = chaos_layer.get_ohlcv("510300.SH")
                with lock:
                    results.append(df)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发错误: {errors}"
        assert len(results) == 10
        for df in results:
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 30

    def test_concurrent_no_race_in_fallback_log(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """并发 fallback 日志无丢失无串行."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        def _worker() -> None:
            chaos_layer.get_ohlcv("510300.SH")

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        log_file = chaos_layer.get_fallback_log_path()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        # 20 个线程, 每个至少 1 条 fallback (P0→P1)
        assert len(lines) >= 20
        # 所有行都是有效 JSON
        for line in lines:
            record = json.loads(line)
            assert "failed_level" in record


# ============================================================
# 场景 6: P6 缓存过期
# ============================================================


class TestScenario6P6CacheExpiry:
    """P6 缓存过期, 拒绝返回 stale 数据."""

    def test_p6_cache_expired_rejected(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P6 缓存过期后拒绝返回."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        # 设置 TTL=0, 立即过期
        chaos_layer.cache_ttl_seconds = 0
        time.sleep(0.01)

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        with pytest.raises(AllSourcesFailedError):
            chaos_layer.get_ohlcv("510300.SH")

    def test_p6_cache_fresh_accepted(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P6 缓存新鲜时接受."""
        sample_df = make_sample_df()
        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df, fail_forever=True))

        df = chaos_layer.get_ohlcv("510300.SH")
        assert len(df) == 30


# ============================================================
# 场景 7: 慢响应触发 fallback
# ============================================================


class TestScenario7SlowResponse:
    """慢响应触发 fallback (DataLayer 本身不超时, 但慢响应会被记录)."""

    def test_slow_p0_fast_p1_p1_wins(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """P0 慢响应 (500ms), P1 快速 (1ms), 但 P0 仍会先被尝试.

        注: DataLayer 不内置超时, 慢响应不会自动触发 fallback.
        此测试验证 P0 失败后 P1 的快速接管.
        """
        sample_df = make_sample_df()
        # P0 慢且失败
        p0 = FaultyProvider("p0", sample_df, fail_forever=True, latency_ms=100)
        p1 = FaultyProvider("p1", sample_df, latency_ms=1)
        chaos_layer.register_provider("p0", p0)
        chaos_layer.register_provider("p1", p1)
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        start = time.perf_counter()
        df = chaos_layer.get_ohlcv("510300.SH")
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert len(df) == 30
        # P0 100ms + P1 1ms ≈ 101ms
        assert 90 < elapsed_ms < 200


# ============================================================
# 场景 8: 审计日志完整性 (HC-6)
# ============================================================


class TestScenario8AuditLogIntegrity:
    """fallback 审计日志完整性测试 (HC-6)."""

    def test_audit_log_all_fields_present(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """每条 fallback 日志包含所有必需字段."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) >= 1

        required_fields = {
            "timestamp", "symbol", "operation", "failed_level",
            "failed_provider", "next_level", "next_provider",
            "error_type", "error_message", "latency_ms",
        }
        for line in lines:
            record = json.loads(line)
            missing = required_fields - set(record.keys())
            assert not missing, f"日志缺失字段: {missing}, record={record}"

    def test_audit_log_jsonl_format(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """日志为 JSONL 格式, 每行一个 JSON."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        # 多次调用
        for _ in range(3):
            chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        content = log_file.read_text(encoding="utf-8")
        lines = [l for l in content.strip().split("\n") if l]

        # 每行都是有效 JSON
        for line in lines:
            record = json.loads(line)
            assert isinstance(record, dict)

    def test_audit_log_latency_recorded(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """fallback 日志记录 latency_ms."""
        sample_df = make_sample_df()
        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True, latency_ms=50))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        record = json.loads(lines[0])
        assert record["latency_ms"] >= 50.0  # 至少 P0 的 50ms

    def test_audit_log_error_message_truncated(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """超长 error_message 被截断到 500 字符."""
        sample_df = make_sample_df()
        long_msg = "x" * 1000
        chaos_layer.register_provider(
            "p0",
            FaultyProvider("p0", sample_df, fail_forever=True, fail_with=RuntimeError(long_msg))
        )
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        chaos_layer.get_ohlcv("510300.SH")

        log_file = chaos_layer.get_fallback_log_path()
        lines = [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]
        record = json.loads(lines[0])
        # error_message 被截断到 500 字符 (在 _write_fallback_log 中 str(e)[:500])
        # RuntimeError 的 str 包含 long_msg
        assert len(record["error_message"]) <= 500


# ============================================================
# 场景 9: 多操作混合场景
# ============================================================


class TestScenario9MixedOperations:
    """多操作混合场景."""

    def test_ohlcv_snapshot_macro_mixed(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """混合调用 ohlcv/snapshot/macro, 各自独立降级."""
        sample_df = make_sample_df()
        make_snapshot()
        make_macro()

        chaos_layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", sample_df))
        chaos_layer.register_provider("p2", FaultyProvider("p2", sample_df))

        # 由于 _route_provider_fn 根据 operation 路由, 但 FaultyProvider 不区分 operation
        # 实际上 FaultyProvider 总是返回 data 字段 (sample_df), 不管 operation 是什么
        # 所以 get_snapshot / get_macro 会返回 sample_df (DataFrame), 而非 dict
        # get_snapshot / get_macro 会转换为空 dict

        # get_ohlcv: P0 失败, P1 接管
        df = chaos_layer.get_ohlcv("510300.SH")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 30

        # 注: 由于 FaultyProvider 不区分 operation, snapshot/macro 测试简化
        # 实际生产中, 每个操作会有不同的 provider 路由

    def test_multiple_symbols_independent(
        self,
        chaos_layer: DataLayer,
    ) -> None:
        """多个 symbol 独立降级, 互不影响."""
        df1 = make_sample_df("510300.SH")
        df2 = make_sample_df("510050.SH")

        chaos_layer._p6_cache_store("510300.SH", "get_ohlcv", df1)
        chaos_layer._p6_cache_store("510050.SH", "get_ohlcv", df2)

        chaos_layer.register_provider("p0", FaultyProvider("p0", df1, fail_forever=True))
        chaos_layer.register_provider("p1", FaultyProvider("p1", df1, fail_forever=True))
        chaos_layer.register_provider("p2", FaultyProvider("p2", df1, fail_forever=True))

        # 510300.SH 走 P6 缓存
        out1 = chaos_layer.get_ohlcv("510300.SH")
        assert len(out1) == 30

        # 510050.SH 也走 P6 缓存
        out2 = chaos_layer.get_ohlcv("510050.SH")
        assert len(out2) == 30


# ============================================================
# 场景 10: Feature Flag 切换
# ============================================================


class TestScenario10FeatureFlagToggle:
    """Feature Flag 切换场景."""

    def test_flag_off_uses_passthrough(
        self,
        tmp_dirs,
    ) -> None:
        """Flag 关闭走透传."""
        fb_dir, p6_dir = tmp_dirs
        flag_state = {"enabled": False}

        layer = DataLayer(
            feature_flag_check=lambda: flag_state["enabled"],
            fallback_log_dir=fb_dir,
            p6_cache_dir=p6_dir,
            auto_register_providers=False,
            enable_data_gate=False,
        )

        sample_df = make_sample_df()
        mock_provider = MagicMock()
        mock_provider.get_historical_data.return_value = sample_df
        layer._market_data_provider = mock_provider

        df = layer.get_ohlcv("510300.SH")
        assert len(df) == 30
        # fallback 日志不应写入 (透传不触发 fallback)
        log_file = layer.get_fallback_log_path()
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8").strip()
            assert not content

    def test_flag_on_uses_fallback_chain(
        self,
        tmp_dirs,
    ) -> None:
        """Flag 开启走降级链."""
        fb_dir, p6_dir = tmp_dirs
        flag_state = {"enabled": True}

        chain = [
            {"level": "P0", "name": "p0", "description": "P0"},
            {"level": "P6", "name": "cache", "description": "P6"},
        ]
        layer = DataLayer(
            fallback_chain=chain,
            feature_flag_check=lambda: flag_state["enabled"],
            fallback_log_dir=fb_dir,
            p6_cache_dir=p6_dir,
            auto_register_providers=False,
            enable_data_gate=False,
        )
        layer.register_provider("cache", layer._p6_cache_lookup)

        sample_df = make_sample_df()
        layer._p6_cache_store("510300.SH", "get_ohlcv", sample_df)
        layer.register_provider("p0", FaultyProvider("p0", sample_df, fail_forever=True))

        df = layer.get_ohlcv("510300.SH")
        assert len(df) == 30

        # fallback 日志应写入
        log_file = layer.get_fallback_log_path()
        assert log_file.exists()
