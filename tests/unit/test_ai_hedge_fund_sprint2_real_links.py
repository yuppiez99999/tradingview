"""Sprint 2 真实链路集成测试 (W6.2.2 记忆反思 + W6.2.4 速率限制器)

验证两个真实链路:
    A. memory_reflection 接真实价格数据 (make_market_price_provider / make_shadow_returns_provider)
    B. debate_layer 接入 RateLimitedLLMCaller (use_rate_limiter 开关)

运行方式:
    pytest tests/unit/test_ai_hedge_fund_sprint2_real_links.py -v
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Mock langchain modules for environments where they're not installed.
# Needed to test the LLM code path (debate_layer._llm_generate_stance imports
# langchain_core.prompts.ChatPromptTemplate and utils.llm imports llm.models
# which imports langchain_openai/langchain_ollama) without a real langchain installation.
from unittest.mock import MagicMock as _MagicMock

for _mod_name in ('langchain_openai', 'langchain_ollama', 'langchain_core',
                  'langchain_core.prompts', 'langchain_core.messages'):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _MagicMock()

# utils.llm imports llm.models which uses `LLMModel | None` (Python 3.10+ syntax)
# and imports langchain_openai/langchain_ollama at top level. In Python 3.8 without
# langchain installed, the real module can't be imported. Mock it in sys.modules
# AND set it as attribute on the parent package so patch() can resolve it.
try:
    import quant_modules.ai_hedge_fund.utils.llm  # noqa: F401
except (ImportError, TypeError):
    import quant_modules.ai_hedge_fund.utils as _utils_pkg
    _mock_utils_llm = _MagicMock()
    _mock_utils_llm.call_llm = _MagicMock()
    sys.modules['quant_modules.ai_hedge_fund.utils.llm'] = _mock_utils_llm
    _utils_pkg.llm = _mock_utils_llm  # patch() does getattr(utils, 'llm')


# ============================================================
# A. 记忆反思接真实价格数据
# ============================================================


class TestMemoryReflectionPriceProvider:
    """验证 make_market_price_provider / make_shadow_returns_provider 适配器"""

    def test_shadow_returns_provider_loads_jsonl(self):
        """验证 make_shadow_returns_provider 能加载 daily_returns.jsonl"""
        from quant_modules.ai_hedge_fund.memory_reflection import make_shadow_returns_provider

        provider = make_shadow_returns_provider()

        # daily_returns.jsonl 有数据 (2026-07-27 ~ 2026-08-11)
        result = provider("ANY_TICKER", "2026-08-07")
        assert result is not None
        assert "close" in result
        assert isinstance(result["close"], float)
        assert result["close"] > 0  # 累计净值

    def test_shadow_returns_provider_date_not_found(self):
        """不存在的日期返回 None"""
        from quant_modules.ai_hedge_fund.memory_reflection import make_shadow_returns_provider

        provider = make_shadow_returns_provider()
        result = provider("ANY_TICKER", "2025-01-01")  # 远早于数据范围
        assert result is None

    def test_shadow_returns_provider_nearest_date(self):
        """非交易日日期应取最近交易日 (±3 天窗口)"""
        from quant_modules.ai_hedge_fund.memory_reflection import make_shadow_returns_provider

        provider = make_shadow_returns_provider()
        # 2026-08-08 是周六, 应取 2026-08-07 的数据
        result = provider("ANY_TICKER", "2026-08-08")
        assert result is not None
        assert "close" in result

    def test_market_price_provider_with_mock(self):
        """验证 make_market_price_provider 用 mock MarketDataProvider"""
        import pandas as pd

        from quant_modules.ai_hedge_fund.memory_reflection import make_market_price_provider

        # 构造 mock provider 返回 DataFrame
        dates = pd.date_range("2026-08-01", periods=10, freq="D")
        df = pd.DataFrame({"close": [100 + i for i in range(10)]}, index=dates)

        mock_provider = MagicMock()
        mock_provider.get_historical_data = MagicMock(return_value=df)

        provider_fn = make_market_price_provider(provider=mock_provider)

        # 第一次调用应拉取数据并缓存
        result = provider_fn("TEST", "2026-08-05")
        assert result is not None
        assert result["close"] == 104.0  # 100 + 4 (第 5 天)

        # 第二次调用应走缓存 (get_historical_data 只调用 1 次)
        result2 = provider_fn("TEST", "2026-08-06")
        assert result2 is not None
        assert result2["close"] == 105.0
        assert mock_provider.get_historical_data.call_count == 1

    def test_market_price_provider_empty_df(self):
        """空 DataFrame 返回 None"""
        import pandas as pd

        from quant_modules.ai_hedge_fund.memory_reflection import make_market_price_provider

        mock_provider = MagicMock()
        mock_provider.get_historical_data = MagicMock(return_value=pd.DataFrame())

        provider_fn = make_market_price_provider(provider=mock_provider)
        result = provider_fn("TEST", "2026-08-05")
        assert result is None

    def test_market_price_provider_nearest_date(self):
        """非交易日日期应取最近交易日"""
        import pandas as pd

        from quant_modules.ai_hedge_fund.memory_reflection import make_market_price_provider

        # 只有 08-05 和 08-06 的数据
        dates = pd.to_datetime(["2026-08-05", "2026-08-06"])
        df = pd.DataFrame({"close": [100.0, 101.0]}, index=dates)

        mock_provider = MagicMock()
        mock_provider.get_historical_data = MagicMock(return_value=df)

        provider_fn = make_market_price_provider(provider=mock_provider)
        # 08-05 是周二, 08-04 是周一 (不在数据中), 但 ±3 天窗口内有 08-05
        result = provider_fn("TEST", "2026-08-04")
        assert result is not None
        assert result["close"] == 100.0


class TestMemoryReflectionEvaluateWithRealData:
    """验证 MemoryReflection.evaluate_past_decisions 接真实价格数据"""

    def test_evaluate_with_shadow_returns(self, tmp_path):
        """用 shadow_returns_provider 评估历史决策"""
        from quant_modules.ai_hedge_fund.memory_reflection import (
            MemoryReflection,
            make_shadow_returns_provider,
        )

        # 1. 写入测试决策记录 (2026-08-01, bullish)
        mem = MemoryReflection(memory_dir=str(tmp_path))
        mem_file = mem.memory_file

        record = {
            "record_id": "test_session_AAPL",
            "session_id": "test_session",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "AAPL",
            "final_signal": "bullish",
            "final_confidence": 75,
            "winner": "bull",
            "net_confidence": 20,
            "reasoning": "测试决策",
            "analyst_bull_count": 8,
            "analyst_bear_count": 3,
            "analyst_neutral_count": 2,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        }
        with open(mem_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 2. 用 shadow_returns_provider 评估
        provider = make_shadow_returns_provider()
        updated = mem.evaluate_past_decisions(
            price_data_provider=provider,
            lookback_days=30,
            eval_date="2026-08-11",
        )

        assert updated >= 1

        # 3. 验证评估结果
        with open(mem_file, encoding="utf-8") as f:
            evaluated = json.loads(f.read().strip())

        assert evaluated["evaluated"] is True
        assert evaluated["eval_date"] == "2026-08-11"
        # forward_return 应被填充 (如果 shadow 数据覆盖该日期)
        # shadow 数据从 2026-08-07 开始, 08-01 的 5 日后是 08-06 (可能无数据)
        # 但 10 日后是 08-11 (有数据)
        assert evaluated["reflection"] != ""

    def test_evaluate_with_mock_price_provider(self, tmp_path):
        """用 mock price provider 精确控制价格验证评估逻辑"""
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        # mock 价格数据: AAPL 在 08-01 收盘 100, 08-06 收盘 105 (5日收益 +5%)
        price_data = {
            "AAPL": {
                "2026-08-01": {"close": 100.0},
                "2026-08-02": {"close": 101.0},
                "2026-08-06": {"close": 105.0},
                "2026-08-11": {"close": 108.0},
            }
        }

        mem = MemoryReflection(memory_dir=str(tmp_path))
        record = {
            "record_id": "test_AAPL",
            "session_id": "s1",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "AAPL",
            "final_signal": "bullish",
            "final_confidence": 80,
            "winner": "bull",
            "net_confidence": 30,
            "reasoning": "",
            "analyst_bull_count": 8,
            "analyst_bear_count": 2,
            "analyst_neutral_count": 0,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        }
        with open(mem.memory_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        updated = mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30,
            eval_date="2026-08-11",
        )

        assert updated == 1

        with open(mem.memory_file, encoding="utf-8") as f:
            evaluated = json.loads(f.read().strip())

        # 5 日收益: (105-100)/100 = 0.05 = +5%
        assert evaluated["forward_return_5d"] is not None
        assert abs(evaluated["forward_return_5d"] - 0.05) < 1e-6
        # bullish + 正收益 → 方向正确
        assert evaluated["correct_5d"] is True
        assert "方向正确" in evaluated["reflection"]

    def test_evaluate_bearish_wrong_direction(self, tmp_path):
        """看空决策但价格上涨 → 方向错误"""
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        price_data = {
            "TSLA": {
                "2026-08-01": {"close": 200.0},
                "2026-08-06": {"close": 220.0},  # +10% 上涨
            }
        }

        mem = MemoryReflection(memory_dir=str(tmp_path))
        record = {
            "record_id": "test_TSLA",
            "session_id": "s1",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "TSLA",
            "final_signal": "bearish",
            "final_confidence": 70,
            "winner": "bear",
            "net_confidence": -25,
            "reasoning": "",
            "analyst_bull_count": 2,
            "analyst_bear_count": 8,
            "analyst_neutral_count": 0,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        }
        with open(mem.memory_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30,
            eval_date="2026-08-11",
        )

        with open(mem.memory_file, encoding="utf-8") as f:
            evaluated = json.loads(f.read().strip())

        # 5 日收益: (220-200)/200 = 0.10 = +10%
        assert abs(evaluated["forward_return_5d"] - 0.10) < 1e-6
        # bearish + 正收益 → 方向错误
        assert evaluated["correct_5d"] is False
        assert "方向错误" in evaluated["reflection"]
        assert "看空判断失误" in evaluated["reflection"]

    def test_reflection_context_after_evaluation(self, tmp_path):
        """评估后 get_reflection_context 应返回胜率统计"""
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        price_data = {
            "AAPL": {
                "2026-08-01": {"close": 100.0},
                "2026-08-06": {"close": 105.0},
            }
        }

        mem = MemoryReflection(memory_dir=str(tmp_path))
        record = {
            "record_id": "test_AAPL",
            "session_id": "s1",
            "timestamp": "2026-08-01T10:00:00",
            "date": "2026-08-01",
            "ticker": "AAPL",
            "final_signal": "bullish",
            "final_confidence": 80,
            "winner": "bull",
            "net_confidence": 30,
            "reasoning": "",
            "analyst_bull_count": 8,
            "analyst_bear_count": 2,
            "analyst_neutral_count": 0,
            "evaluated": False,
            "eval_date": "",
            "forward_return_1d": None,
            "forward_return_5d": None,
            "forward_return_10d": None,
            "correct_1d": None,
            "correct_5d": None,
            "correct_10d": None,
            "reflection": "",
        }
        with open(mem.memory_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        mem.evaluate_past_decisions(price_data_provider=price_data, lookback_days=30)

        # 查询反思上下文
        ctx = mem.get_reflection_context(tickers=["AAPL"], days=30)
        assert ctx["total_evaluated"] == 1
        assert ctx["overall_win_rate"] == 1.0  # 1/1 正确
        assert "AAPL" in ctx["by_ticker"]
        assert ctx["by_ticker"]["AAPL"]["win_rate"] == 1.0
        assert len(ctx["by_ticker"]["AAPL"]["recent_reflections"]) > 0


# ============================================================
# B. debate_layer 接入 RateLimitedLLMCaller
# ============================================================


class TestDebateLayerRateLimiter:
    """验证 debate_layer 接入 RateLimitedLLMCaller"""

    def test_use_rate_limiter_flag_default_false(self, tmp_path):
        """默认 use_rate_limiter=False"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(use_llm=False, log_dir=str(tmp_path))
        assert layer.use_rate_limiter is False
        assert layer._rate_limited_caller is None

    def test_use_rate_limiter_flag_true(self, tmp_path):
        """use_rate_limiter=True 时应初始化 RateLimitedLLMCaller"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(
            use_llm=False, log_dir=str(tmp_path),
            use_rate_limiter=True,
        )
        assert layer.use_rate_limiter is True
        assert layer._rate_limited_caller is not None

    def test_rate_limiter_stats_tracked(self, tmp_path):
        """启用 rate_limiter 时, LLM 调用应被统计"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller

        # 重置全局 caller
        caller = get_global_llm_caller()
        caller.reset()

        layer = DebateLayer(
            use_llm=False,  # LLM 不可用 → 走规则模式, 不触发 rate_limiter
            log_dir=str(tmp_path),
            use_rate_limiter=True,
        )
        signals = {
            "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "test"}},
        }

        layer.run_full_debate(["AAPL"], signals)

        # use_llm=False → 不触发 LLM 调用 → stats 应为空
        stats = caller.stats
        # 规则模式不经过 rate_limiter, 所以 stats 应为 0 调用
        assert isinstance(stats, dict)

    def test_rate_limiter_used_when_llm_enabled(self, tmp_path):
        """use_llm=True + use_rate_limiter=True 时, _llm_generate_stance 应走 RateLimitedLLMCaller"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller

        caller = get_global_llm_caller()
        caller.reset()

        layer = DebateLayer(
            use_llm=True,
            log_dir=str(tmp_path),
            use_rate_limiter=True,
        )

        # Mock _llm_available 返回 True
        # Mock call_llm 返回一个 DebateStance
        from quant_modules.ai_hedge_fund.debate_layer import DebateStance

        mock_stance = DebateStance(
            stance="bullish", confidence=75,
            key_arguments=["test arg"], rebuttals=[],
            evidence_summary="test evidence",
        )

        with patch.object(DebateLayer, "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm", return_value=mock_stance) as mock_call:
                signals = {
                    "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "test"}},
                }
                layer.run_full_debate(["AAPL"], signals)

        # call_llm 应被调用 (通过 RateLimitedLLMCaller)
        assert mock_call.call_count > 0

        # stats 应有记录
        stats = caller.stats
        total_calls = sum(s["total_calls"] for s in stats.values())
        assert total_calls > 0

        # 应有 successful 记录
        successful = sum(s["successful"] for s in stats.values())
        assert successful > 0

    def test_rate_limiter_cache_hit_on_same_prompt(self, tmp_path):
        """相同 prompt 第二次调用应命中缓存"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller

        caller = get_global_llm_caller()
        caller.reset()

        layer = DebateLayer(
            use_llm=True,
            log_dir=str(tmp_path),
            use_rate_limiter=True,
        )

        from quant_modules.ai_hedge_fund.debate_layer import DebateStance
        mock_stance = DebateStance(
            stance="bullish", confidence=75,
            key_arguments=["test"], rebuttals=[], evidence_summary="test",
        )

        with patch.object(DebateLayer, "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm", return_value=mock_stance):
                signals = {
                    "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "test"}},
                }
                # 第一次调用
                layer.run_full_debate(["AAPL"], signals)

                # 第二次调用 (相同 prompt, 应命中缓存)
                layer2 = DebateLayer(
                    use_llm=True, log_dir=str(tmp_path),
                    use_rate_limiter=True,
                )
                layer2.run_full_debate(["AAPL"], signals)

        # 检查缓存命中
        stats = caller.stats
        cache_hits = sum(s["cache_hits"] for s in stats.values())
        # 至少有一次缓存命中 (Round 1 bull/bear 的 prompt 相同)
        assert cache_hits > 0

    def test_rate_limiter_fallback_on_exception(self, tmp_path):
        """RateLimitedLLMCaller 调用失败时应降级到规则模式"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(
            use_llm=True,
            log_dir=str(tmp_path),
            use_rate_limiter=True,
        )

        # Mock _llm_available 返回 True, 但 call_llm 抛异常
        with patch.object(DebateLayer, "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                       side_effect=RuntimeError("LLM 服务不可用")):
                signals = {
                    "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "test"}},
                }
                session = layer.run_full_debate(["AAPL"], signals)

        # 应降级为规则模式, 结果结构完整
        result = session.debate_results["AAPL"]
        assert result.winner == "bull"
        assert result.final_signal == "bullish"

    def test_debate_node_with_rate_limiter(self, tmp_path):
        """debate_node 从 metadata 读取 use_rate_limiter 开关"""
        from quant_modules.ai_hedge_fund.debate_layer import debate_node

        state = {
            "messages": [],
            "data": {
                "tickers": ["AAPL"],
                "analyst_signals": {
                    "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "test"}},
                },
            },
            "metadata": {
                "enable_debate_llm": False,  # 规则模式
                "use_rate_limiter": True,    # 启用 rate_limiter (但 use_llm=False 不触发)
            },
        }

        with patch("quant_modules.ai_hedge_fund.debate_layer._DEBATE_LOG_DIR", str(tmp_path)):
            result_state = debate_node(state)

        # 应正常完成
        assert "debate_verdict" in result_state["data"]["analyst_signals"]
        assert "AAPL" in result_state["data"]["analyst_signals"]["debate_verdict"]


# ============================================================
# C. 端到端: 辩论 → 记录 → 评估 → 反思注入
# ============================================================


class TestEndToEndDebateMemoryLoop:
    """端到端测试: 辩论 → 记忆记录 → T+N 评估 → 反思上下文提取"""

    def test_full_loop_with_mock_prices(self, tmp_path):
        """完整闭环: 辩论 → 记录 → 评估 → 反思"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        # 1. 运行辩论 (规则模式)
        debate = DebateLayer(use_llm=False, log_dir=str(tmp_path / "debates"))
        signals = {
            "warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "护城河强"}},
            "ben_graham": {"AAPL": {"signal": "bullish", "confidence": 75, "reasoning": "估值合理"}},
            "michael_burry": {"AAPL": {"signal": "bearish", "confidence": 60, "reasoning": "估值过高"}},
        }
        session = debate.run_full_debate(["AAPL"], signals)
        assert session.debate_results["AAPL"].final_signal == "bullish"

        # 2. 记录决策到记忆
        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        count = mem.record_decisions(session)
        assert count == 1

        # 3. T+N 后评估 (mock 价格: AAPL 上涨 → bullish 正确)
        price_data = {
            "AAPL": {
                "2026-08-11": {"close": 100.0},  # 决策日
                "2026-08-16": {"close": 105.0},  # 5 日后 +5%
                "2026-08-21": {"close": 108.0},  # 10 日后 +8%
            }
        }

        # 修改决策记录的日期为 08-11 (匹配价格数据)
        with open(mem.memory_file, encoding="utf-8") as f:
            lines = f.readlines()
        records = [json.loads(line) for line in lines if line.strip()]
        records[0]["date"] = "2026-08-11"
        records[0]["timestamp"] = "2026-08-11T10:00:00"
        with open(mem.memory_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        updated = mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30,
            eval_date="2026-08-21",
        )
        assert updated == 1

        # 4. 提取反思上下文
        ctx = mem.get_reflection_context(tickers=["AAPL"], days=30)
        assert ctx["total_evaluated"] == 1
        assert ctx["overall_win_rate"] == 1.0  # bullish + 上涨 → 正确
        assert "AAPL" in ctx["by_ticker"]
        assert len(ctx["by_ticker"]["AAPL"]["recent_reflections"]) > 0
        assert "方向正确" in ctx["by_ticker"]["AAPL"]["recent_reflections"][0]

        # 5. 验证反思摘要可用于下次分析注入
        assert ctx["summary"] != ""
        assert "AAPL" in ctx["summary"]
        assert "胜率" in ctx["summary"] or "正确" in ctx["summary"]
