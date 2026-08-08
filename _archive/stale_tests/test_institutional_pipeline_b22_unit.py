"""institutional_pipeline_runner B2.2 并发化 + cache 回填 单元测试

覆盖场景:
1. _get_or_load_historical helper (cache 优先 + 回填 + 日期截断 + 双检锁)
2. _real_alpha_signals 并发执行 + cache 回填避免重复拉取
3. _real_macro_signals 并发 + sentiment API 命中/miss
4. _real_llm_signals 并发 + 新闻情绪/历史代理兜底
5. _real_etf_signals 并发 + ETF 匹配/历史代理兜底
6. 多方法顺序调用 cache 共享 (消除 4× 重复拉取)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

# 确保 utils 在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext  # noqa: E402


# ============================================================
# 测试辅助
# ============================================================
def _make_synthetic_df(days: int = 200, end_date: str = "2026-07-30") -> pd.DataFrame:
    """生成合成的 OHLCV 数据 (5y 历史)"""
    dates = pd.date_range(end=end_date, periods=days, freq="B")
    rng = np.random.default_rng(seed=42)
    close = 100.0 + np.cumsum(rng.standard_normal(days) * 0.5)
    close = np.maximum(close, 1.0)  # 保证非负
    volume = rng.integers(1_000_000, 10_000_000, days)
    return pd.DataFrame({"close": close, "volume": volume.astype(float)}, index=dates)


def _make_runner(symbols=None, mode="smoke", report_date="2026-07-30"):
    """构造一个 runner, data_provider=None (由测试用例覆盖)

    Note: 用 `symbols if symbols is not None else [...]` 而非 `symbols or [...]`,
    因为后者会把空列表 `[]` 误判为 falsy 而使用默认值.
    """
    ctx = PipelineContext(
        mode=mode,
        symbols=symbols if symbols is not None else ["600519", "000858", "601318"],
        report_date=report_date,
    )
    runner = InstitutionalPipelineRunner(ctx)
    return runner


def _make_mock_data_provider(
    *,
    historical_df=None,
    news_sentiment=None,
    market_data=None,
    external_macro=None,
    risk_sentiment=None,
):
    """构造一个 mock data_provider, 跟踪方法调用次数"""
    provider = MagicMock()
    provider.get_historical_data.return_value = historical_df
    provider.get_news_sentiment.return_value = news_sentiment if news_sentiment is not None else []
    provider.get_market_data.return_value = market_data or {}
    provider.get_external_macro.return_value = external_macro or {}
    provider.get_risk_sentiment.return_value = risk_sentiment or {}
    return provider


# ============================================================
# 1. _get_or_load_historical helper 测试
# ============================================================
class TestGetOrLoadHistorical:
    """B2.2 新增 helper: cache 优先 + 回填 + 日期截断 + 双检锁"""

    def test_data_provider_none_returns_none(self):
        """data_provider=None 时直接返回 None"""
        runner = _make_runner()
        runner.data_provider = None
        assert runner._get_or_load_historical("600519") is None

    def test_cache_hit_no_fetch(self):
        """cache 命中时不调用 data_provider.get_historical_data"""
        runner = _make_runner()
        df = _make_synthetic_df()
        runner._historical_cache["600519"] = df
        runner.data_provider = _make_mock_data_provider()

        result = runner._get_or_load_historical("600519")
        assert result is not None
        assert len(result) == len(df)
        # 应该没有调用 get_historical_data
        runner.data_provider.get_historical_data.assert_not_called()

    def test_cache_miss_fetches_and_writes_back(self):
        """cache 未命中时拉取并回填 cache (后续调用命中)"""
        runner = _make_runner()
        df = _make_synthetic_df()
        runner.data_provider = _make_mock_data_provider(historical_df=df)

        # 第一次调用: cache miss → fetch + 回填
        result1 = runner._get_or_load_historical("600519")
        assert result1 is not None
        assert "600519" in runner._historical_cache
        assert runner.data_provider.get_historical_data.call_count == 1

        # 第二次调用: cache hit → 不再 fetch
        result2 = runner._get_or_load_historical("600519")
        assert result2 is not None
        # 总调用次数仍为 1 (cache 命中, 未重复拉取)
        assert runner.data_provider.get_historical_data.call_count == 1

    def test_empty_df_returns_none(self):
        """data_provider 返回空 DataFrame → 返回 None"""
        runner = _make_runner()
        runner.data_provider = _make_mock_data_provider(historical_df=pd.DataFrame())

        result = runner._get_or_load_historical("600519")
        assert result is None
        # 空 df 不应该写入 cache
        assert "600519" not in runner._historical_cache

    def test_fetch_exception_returns_none(self):
        """data_provider 抛异常 → 返回 None (不向上传播)"""
        runner = _make_runner()
        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.side_effect = RuntimeError("network error")

        result = runner._get_or_load_historical("600519")
        assert result is None

    def test_date_truncation(self):
        """回测模式: 截断到 report_date (防止前视偏差)"""
        runner = _make_runner(mode="backtest", report_date="2025-06-30")
        # 数据包含 2025-06-30 之后的日期 (未来信息)
        df = _make_synthetic_df(days=300, end_date="2026-07-30")
        runner.data_provider = _make_mock_data_provider(historical_df=df)

        result = runner._get_or_load_historical("600519")
        assert result is not None
        # 所有日期应 <= 2025-06-30
        cutoff = pd.Timestamp("2025-06-30").normalize()
        assert (result.index <= cutoff).all()
        # 应有截断后的数据 (不是全空)
        assert len(result) > 0

    def test_concurrent_double_check_lock(self):
        """并发场景: 多线程同时拉取同一 symbol, 只有一个线程真正拉取 (双检锁)"""
        runner = _make_runner()

        call_count = {"n": 0}
        lock = threading.Lock()

        def _mock_fetch(symbol, period="5y"):
            with lock:
                call_count["n"] += 1
            # 模拟网络延迟, 让其他线程在锁外等待
            time.sleep(0.05)
            return _make_synthetic_df()

        runner.data_provider = MagicMock()
        runner.data_provider.get_historical_data.side_effect = _mock_fetch

        # 8 个线程同时拉取同一 symbol
        threads = []
        results = [None] * 8

        def _worker(i):
            results[i] = runner._get_or_load_historical("600519")

        for i in range(8):
            t = threading.Thread(target=_worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # 所有线程都应拿到结果
        assert all(r is not None for r in results)
        # get_historical_data 应该只被调用 1 次 (双检锁生效)
        # (允许 2-3 次由于竞态, 但绝不应该是 8 次)
        assert call_count["n"] <= 3, f"双检锁失效, 调用次数={call_count['n']}"


# ============================================================
# 2. _real_alpha_signals 并发化测试
# ============================================================
class TestRealAlphaSignals:
    """B2.2: _real_alpha_signals 并发执行"""

    def test_data_provider_none_returns_defaults(self):
        """data_provider=None 返回全默认信号"""
        runner = _make_runner(symbols=["600519", "000858"])
        runner.data_provider = None

        result = runner._real_alpha_signals()
        assert set(result.keys()) == {"600519", "000858"}
        for sig in result.values():
            assert sig["strength"] == 0.0
            assert sig["confidence"] == 0.2

    def test_empty_symbols_returns_empty(self):
        """空 symbols 列表返回空字典"""
        runner = _make_runner(symbols=[])
        runner.data_provider = _make_mock_data_provider(historical_df=_make_synthetic_df())

        result = runner._real_alpha_signals()
        assert result == {}

    def test_normal_case_returns_signals_for_all_symbols(self):
        """正常场景: 所有 symbol 都有信号"""
        runner = _make_runner(symbols=["600519", "000858", "601318"])
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(historical_df=df)

        result = runner._real_alpha_signals()
        assert set(result.keys()) == {"600519", "000858", "601318"}
        for _symbol, sig in result.items():
            assert "strength" in sig
            assert "confidence" in sig
            assert -1.0 <= sig["strength"] <= 1.0
            assert 0.0 <= sig["confidence"] <= 1.0

    def test_short_history_returns_fallback(self):
        """历史数据过短 (<30行) → 返回 fallback 信号"""
        runner = _make_runner(symbols=["600519"])
        # 只有 10 行, < 30
        short_df = _make_synthetic_df(days=10)
        runner.data_provider = _make_mock_data_provider(historical_df=short_df)

        result = runner._real_alpha_signals()
        assert result["600519"]["strength"] == 0.0
        assert result["600519"]["confidence"] == 0.2

    def test_cache_backfill_prevents_duplicate_fetch(self):
        """cache 回填: 重复调用不重复拉取"""
        runner = _make_runner(symbols=["600519", "000858"])
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(historical_df=df)

        # 第一次调用: cache miss, 拉取并回填
        runner._real_alpha_signals()
        assert runner.data_provider.get_historical_data.call_count == 2  # 2 symbols 各拉一次

        # 第二次调用: cache hit, 不再拉取
        runner._real_alpha_signals()
        # 调用次数仍为 2 (cache 已回填)
        assert runner.data_provider.get_historical_data.call_count == 2


# ============================================================
# 3. _real_macro_signals 测试
# ============================================================
class TestRealMacroSignals:
    """B2.2: _real_macro_signals 并发 + sentiment"""

    def test_data_provider_none(self):
        """data_provider=None 返回默认 macro 信号"""
        runner = _make_runner()
        runner.data_provider = None

        result = runner._real_macro_signals()
        assert result == {"macro_index": {"strength": 0.0, "confidence": 0.2}}

    def test_sentiment_hit(self):
        """sentiment API 返回非零 score → 直接使用"""
        runner = _make_runner(symbols=["600519", "000858"])
        runner.data_provider = _make_mock_data_provider(
            external_macro={"risk_sentiment": {"score": 0.5}},
        )

        result = runner._real_macro_signals()
        assert result["macro_index"]["strength"] == 0.5
        # score != 0 → confidence=0.5
        assert result["macro_index"]["confidence"] == 0.5

    def test_sentiment_miss_uses_macro_proxy(self):
        """sentiment score=0 → 用历史数据计算 macro_proxy"""
        runner = _make_runner(symbols=["600519", "000858"])
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            external_macro={"risk_sentiment": {"score": 0.0}},
            historical_df=df,
        )

        result = runner._real_macro_signals()
        # macro_proxy 应该是非零 (除非数据完全平)
        assert "strength" in result["macro_index"]
        assert -1.0 <= result["macro_index"]["strength"] <= 1.0
        # score=0 → confidence=0.35
        assert result["macro_index"]["confidence"] == 0.35

    def test_macro_exception_returns_fallback(self):
        """get_external_macro 抛异常 → 返回 fallback"""
        runner = _make_runner()
        runner.data_provider = MagicMock()
        runner.data_provider.get_external_macro.side_effect = RuntimeError("api fail")
        runner.data_provider.get_risk_sentiment.return_value = {}

        result = runner._real_macro_signals()
        assert result == {"macro_index": {"strength": 0.0, "confidence": 0.2}}


# ============================================================
# 4. _real_llm_signals 测试
# ============================================================
class TestRealLLMSignals:
    """B2.2: _real_llm_signals 并发 + 新闻情绪/历史代理"""

    def test_data_provider_none(self):
        """data_provider=None 返回全默认信号"""
        runner = _make_runner(symbols=["600519", "000858"])
        runner.data_provider = None

        result = runner._real_llm_signals()
        assert set(result.keys()) == {"600519", "000858"}
        for sig in result.values():
            assert sig == {"strength": 0.0, "confidence": 0.2}

    def test_news_sentiment_hit(self):
        """新闻情绪 API 命中 → 使用 sentiment 平均值"""
        runner = _make_runner(symbols=["600519"])
        runner.data_provider = _make_mock_data_provider(
            news_sentiment=[
                {"sentiment_score": 0.6, "confidence": 0.8},
                {"sentiment_score": 0.4, "confidence": 0.6},
            ],
        )

        result = runner._real_llm_signals()
        sig = result["600519"]
        # 平均 sentiment = 0.5
        assert abs(sig["strength"] - 0.5) < 1e-6
        # 任一 sentiment_score != 0 → confidence=0.55
        assert sig["confidence"] == 0.55

    def test_news_sentiment_empty_uses_history_proxy(self):
        """新闻情绪为空 → 用历史数据计算代理信号"""
        runner = _make_runner(symbols=["600519"])
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            news_sentiment=[],
            historical_df=df,
        )

        result = runner._real_llm_signals()
        sig = result["600519"]
        assert "strength" in sig
        assert -1.0 <= sig["strength"] <= 1.0
        # proxy 命中 → confidence=0.45
        assert sig["confidence"] == 0.45

    def test_news_sentiment_empty_and_no_history_returns_default(self):
        """新闻情绪空 + 无历史数据 → 返回默认"""
        runner = _make_runner(symbols=["600519"])
        runner.data_provider = _make_mock_data_provider(
            news_sentiment=[],
            historical_df=None,
        )

        result = runner._real_llm_signals()
        sig = result["600519"]
        assert sig["strength"] == 0.0
        assert sig["confidence"] == 0.2


# ============================================================
# 5. _real_etf_signals 测试
# ============================================================
class TestRealEtfSignals:
    """B2.2: _real_etf_signals 并发 + ETF 匹配"""

    def test_data_provider_none(self):
        """data_provider=None 返回全默认信号"""
        runner = _make_runner(symbols=["600519"])
        runner.data_provider = None

        result = runner._real_etf_signals()
        assert set(result.keys()) == {"600519"}
        for sig in result.values():
            assert sig == {"strength": 0.0, "confidence": 0.2}

    def test_symbol_with_matched_etf_market_data_hit(self):
        """匹配到 ETF + market_data 非零 change_pct → 使用 change_pct

        Note: 600519 keywords=["白酒","消费"] 实际不匹配任何 ETF
              (沪深300/中证500 等 cat 与 "白酒"/"消费" 无子串关系)
              使用 600276 (keywords=["医药","创新药"]) 匹配 512010 (cat="医药")
        """
        runner = _make_runner(symbols=["600276"])  # 匹配 512010 (医药)
        runner.data_provider = _make_mock_data_provider(
            market_data={"change_pct": 1.5},  # 1.5% 涨幅
        )

        result = runner._real_etf_signals()
        sig = result["600276"]
        # change_pct=1.5 → strength = 1.5/10 = 0.15
        assert abs(sig["strength"] - 0.15) < 1e-6
        # |strength| >= 0.15 → confidence=0.6
        assert sig["confidence"] == 0.6

    def test_symbol_with_matched_etf_market_data_zero_uses_history(self):
        """匹配到 ETF + market_data change_pct=0 → 用历史数据兜底"""
        runner = _make_runner(symbols=["600276"])
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            market_data={"change_pct": 0.0},
            historical_df=df,
        )

        result = runner._real_etf_signals()
        sig = result["600276"]
        # 历史代理计算后 strength 非零, confidence=0.45
        assert -1.0 <= sig["strength"] <= 1.0
        # change_pct=0 → confidence=0.45 (历史代理兜底)
        assert sig["confidence"] == 0.45

    def test_symbol_without_matched_etf_returns_default(self):
        """symbol 不在 sector_keywords → 无匹配 ETF → 返回默认"""
        runner = _make_runner(symbols=["999999"])  # 不在 sector_keywords
        runner.data_provider = _make_mock_data_provider()

        result = runner._real_etf_signals()
        assert result["999999"] == {"strength": 0.0, "confidence": 0.2}

    def test_etf_history_cache_populated_after_call(self):
        """调用 etf_signals 后, 匹配的 ETF 历史数据被回填到 cache (避免后续重复拉取)"""
        runner = _make_runner(symbols=["600276"])  # 匹配 512010 (医药)
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            market_data={"change_pct": 0.0},  # 强制走历史代理路径
            historical_df=df,
        )

        # 第一次调用: cache miss, 拉取 512010 并回填
        runner._real_etf_signals()
        # ETF 512010 应该被回填到 cache
        assert "512010" in runner._historical_cache
        fetches_after_first = runner.data_provider.get_historical_data.call_count
        assert fetches_after_first == 1

        # 第二次调用: cache hit (512010 已在 cache), 不再拉取
        runner._real_etf_signals()
        assert runner.data_provider.get_historical_data.call_count == 1


# ============================================================
# 6. 4 方法顺序调用 — cache 共享验证
# ============================================================
class TestCacheSharingAcrossMethods:
    """B2.2 核心目标: 消除 4× 重复拉取 (4 个 _real_*_signals 方法共享 cache)"""

    def test_four_methods_share_cache_no_duplicate_fetch(self):
        """4 个方法顺序调用: 后续方法应命中 cache, 总拉取次数 << 4×N"""
        symbols = ["600519", "000858", "601318", "000001", "600036"]
        runner = _make_runner(symbols=symbols)
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            historical_df=df,
            market_data={"change_pct": 0.0},  # 触发历史代理路径
            external_macro={"risk_sentiment": {"score": 0.0}},  # 触发宏观代理
        )

        # 顺序调用 4 个方法
        runner._real_alpha_signals()
        fetches_after_alpha = runner.data_provider.get_historical_data.call_count

        runner._real_macro_signals()
        fetches_after_macro = runner.data_provider.get_historical_data.call_count

        runner._real_llm_signals()
        fetches_after_llm = runner.data_provider.get_historical_data.call_count

        runner._real_etf_signals()
        fetches_after_etf = runner.data_provider.get_historical_data.call_count

        # 串行实现: 每方法都 N 次 = 4 × 5 = 20 次
        # B2.2 优化后: 第一次 5 次, 后续 3 次方法命中 cache = 5 次总数
        # (允许少量竞态, 但不应超过 N+2)
        assert fetches_after_alpha == 5, f"alpha 阶段应为 N={5}, 实际={fetches_after_alpha}"
        assert fetches_after_macro == 5, f"macro 阶段应命中 cache, 实际={fetches_after_macro}"
        assert fetches_after_llm == 5, f"llm 阶段应命中 cache, 实际={fetches_after_llm}"
        assert fetches_after_etf == 5, f"etf 阶段应命中 cache, 实际={fetches_after_etf}"

    def test_concurrent_execution_does_not_corrupt_cache(self):
        """并发执行 4 个方法, cache 不被破坏 (线程安全)"""
        symbols = ["600519", "000858", "601318"]
        runner = _make_runner(symbols=symbols)
        df = _make_synthetic_df(days=200)
        runner.data_provider = _make_mock_data_provider(
            historical_df=df,
            market_data={"change_pct": 0.0},
            external_macro={"risk_sentiment": {"score": 0.0}},
        )

        # 4 个方法并发执行 (模拟 _step_signal_fusion 调用模式)
        results = {}
        threads = []

        def _call_alpha():
            results["alpha"] = runner._real_alpha_signals()

        def _call_macro():
            results["macro"] = runner._real_macro_signals()

        def _call_llm():
            results["llm"] = runner._real_llm_signals()

        def _call_etf():
            results["etf"] = runner._real_etf_signals()

        for target in [_call_alpha, _call_macro, _call_llm, _call_etf]:
            t = threading.Thread(target=target)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # 所有方法应成功完成, cache 中应包含所有 symbols
        assert "alpha" in results
        assert "macro" in results
        assert "llm" in results
        assert "etf" in results

        # cache 应包含所有 symbols (不重复写入)
        cached_symbols = set(runner._historical_cache.keys())
        for s in symbols:
            assert s in cached_symbols, f"cache 缺失 symbol={s}"
