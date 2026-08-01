# -*- coding: utf-8 -*-
"""_preload_historical_data B2.3 合并去重 + 并发 单元测试

覆盖场景:
1. 4 个 symbol 来源 (ctx.symbols + POSITION_SYMBOLS + CROSS_MARKET_PROXY + ETF) 合并去重
2. 并发拉取 (run_io_batch), 不再串行 for 循环
3. data_provider=None 时直接返回 (无副作用)
4. cache 写入所有成功拉取的 symbol
5. 部分拉取失败不影响其他 symbol
6. 不重复拉取同一 symbol (去重生效)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# 确保 utils 在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext


# ============================================================
# 测试辅助
# ============================================================
def _make_synthetic_df(days: int = 200, end_date: str = "2025-06-30") -> pd.DataFrame:
    """生成合成的 OHLCV 数据 (5y 历史, 截止 2025-06-30 以保证截断有效)"""
    dates = pd.date_range(end=end_date, periods=days, freq="B")
    rng = np.random.default_rng(seed=42)
    close = 100.0 + np.cumsum(rng.standard_normal(days) * 0.5)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": rng.integers(1_000_000, 10_000_000, days).astype(float),
    }, index=dates)


def _make_runner(symbols=None, mode="backtest", report_date="2025-06-30"):
    """构造一个 backtest 模式的 runner.

    Note: 用 `symbols if symbols is not None else [...]` 而非 `symbols or [...]`,
    因为后者会把空列表 `[]` 误判为 falsy.
    """
    ctx = PipelineContext(
        mode=mode,
        symbols=symbols if symbols is not None else ["600519", "000858"],
        report_date=report_date,
    )
    runner = InstitutionalPipelineRunner(ctx)
    return runner


# ============================================================
# 1. data_provider=None 边界
# ============================================================
class TestPreloadDataProviderNone:
    def test_no_side_effects_when_data_provider_none(self):
        """data_provider=None 时直接返回, cache 保持空"""
        # 强制 data_provider=None (绕过 _HAS_DATA_PROVIDER)
        runner = _make_runner(symbols=["600519"])
        runner.data_provider = None
        runner._historical_cache = {}

        runner._preload_historical_data()
        assert runner._historical_cache == {}


# ============================================================
# 2. 合并去重
# ============================================================
class TestPreloadMergeDeduplicate:
    def test_all_symbol_sources_merged(self):
        """4 个 symbol 来源全部合并到 cache (假设全部拉取成功)"""
        df = _make_synthetic_df()
        runner = _make_runner(symbols=["600519", "000858"])
        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.return_value = df
        runner._historical_cache = {}

        runner._preload_historical_data()

        cached = set(runner._historical_cache.keys())
        # ctx.symbols 必然在 cache
        assert "600519" in cached
        assert "000858" in cached
        # ETF 候选必然在 cache
        for etf in ["510300", "510500", "510050", "159915", "512100",
                    "512010", "512480", "512760", "515030", "515790"]:
            assert etf in cached, f"ETF {etf} 未被预加载"
        # 跨市场代理标的必然在 cache
        for proxy in ["518880", "600036", "588000", "515180"]:
            assert proxy in cached, f"跨市场代理 {proxy} 未被预加载"

    def test_no_duplicate_fetch_for_overlapping_symbols(self):
        """ctx.symbols 与 POSITION_SYMBOLS/ETF 有重叠时, 不重复拉取

        直接 mock _load_and_truncate 计数 (因为它优先读 parquet,
        data_provider.get_historical_data 不一定被调用).
        """
        # 600036 同时在 ctx.symbols + POSITION_SYMBOLS (银行) + CROSS_MARKET_PROXY
        runner = _make_runner(symbols=["600036"])
        df = _make_synthetic_df()

        call_count = {"n": 0}
        fetched_symbols = []

        def _mock_load_and_truncate(symbol, period, cutoff):
            call_count["n"] += 1
            fetched_symbols.append(symbol)
            return df

        runner._load_and_truncate = _mock_load_and_truncate
        runner._historical_cache = {}

        runner._preload_historical_data()

        # 应有调用 (合并去重后仍然要拉取所有 unique symbol)
        assert call_count["n"] > 0
        # 600036 应只拉取 1 次 (合并去重生效, 不会因为出现在 3 个来源中而拉取 3 次)
        assert fetched_symbols.count("600036") == 1, (
            f"600036 被重复拉取 {fetched_symbols.count('600036')} 次, 合并去重失效"
        )
        # cache 中只有 1 个 600036 条目
        assert "600036" in runner._historical_cache


# ============================================================
# 3. 并发拉取 (性能验证)
# ============================================================
class TestPreloadConcurrent:
    def test_concurrent_faster_than_serial(self):
        """并发拉取应明显快于串行 (sleep 模拟网络延迟)"""
        runner = _make_runner(symbols=["600519", "000858", "601318", "000001"])
        df = _make_synthetic_df()

        def _slow_fetch(symbol, period="5y"):
            time.sleep(0.1)  # 模拟 100ms 网络延迟
            return df

        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.side_effect = _slow_fetch
        runner._historical_cache = {}

        t0 = time.time()
        runner._preload_historical_data()
        elapsed = time.time() - t0

        # 串行: ~20+ symbol × 0.1s ≈ 2s+
        # 并发(8 workers): 应 < 1s
        assert elapsed < 1.5, f"并发预加载耗时 {elapsed:.2f}s 过长, 可能未真正并发"

    def test_partial_failure_does_not_block_others(self):
        """部分 symbol 拉取失败不影响其他 symbol"""
        runner = _make_runner(symbols=["600519", "FAIL_HERE", "000858"])
        df = _make_synthetic_df()

        def _mock_fetch(symbol, period="5y"):
            if symbol == "FAIL_HERE":
                raise RuntimeError("intentional failure")
            return df

        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.side_effect = _mock_fetch
        runner._historical_cache = {}

        runner._preload_historical_data()

        # 失败 symbol 不在 cache, 其他成功 symbol 在 cache
        assert "FAIL_HERE" not in runner._historical_cache
        assert "600519" in runner._historical_cache
        assert "000858" in runner._historical_cache


# ============================================================
# 4. 回测完整性 (无前视偏差)
# ============================================================
class TestPreloadNoLookAheadBias:
    def test_data_truncated_to_report_date(self):
        """预加载的数据全部 <= report_date (无未来信息泄漏)"""
        runner = _make_runner(symbols=["600519"], report_date="2025-06-30")
        # 数据包含 2025-06-30 之后的日期 (未来信息)
        df = _make_synthetic_df(days=300, end_date="2026-07-30")
        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.return_value = df
        runner._historical_cache = {}

        runner._preload_historical_data()

        cached_df = runner._historical_cache["600519"]
        cutoff = pd.Timestamp("2025-06-30").normalize()
        assert (cached_df.index <= cutoff).all(), "预加载数据包含未来信息 (前视偏差)"


# ============================================================
# 5. cache 与后续 _get_or_load_historical 协同
# ============================================================
class TestPreloadFeedsCacheForSubsequentCalls:
    def test_preloaded_cache_hit_no_fetch_in_get_or_load(self):
        """预加载后, _get_or_load_historical 应命中 cache 不再拉取"""
        runner = _make_runner(symbols=["600519"])
        df = _make_synthetic_df()
        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.return_value = df
        runner._historical_cache = {}

        # 预加载
        runner._preload_historical_data()
        assert "600519" in runner._historical_cache
        fetch_count_after_preload = runner.data_provider.get_historical_data.call_count

        # 后续调用应命中 cache
        result = runner._get_or_load_historical("600519")
        assert result is not None
        # 调用次数不应增加 (cache hit)
        assert runner.data_provider.get_historical_data.call_count == fetch_count_after_preload


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
