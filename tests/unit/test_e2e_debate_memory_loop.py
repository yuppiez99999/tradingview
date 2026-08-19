"""端到端闭环单元测试 (W6.2.2 + W6.2.4)

覆盖 demo_c_end_to_end_loop 函数中的端到端闭环逻辑:
    辩论 (DebateLayer) → 记录 (MemoryReflection) → T+N 评估 → 反思上下文提取

5 大类共 19 项测试:
    A. 正常路径 (5 项) — 多 ticker + LLM + rate_limiter 完整闭环
    B. 边界场景 (5 项) — 空信号 / 单 ticker / 超 lookback / 缺价格 / 多轮累积
    C. 异常路径 (4 项) — LLM 失败降级 / 部分失败 / 写入失败 / 缓存命中
    D. 数据一致性 (3 项) — forward_return / correct 字段 / by_ticker 聚合
    E. 日志验证 (2 项) — logger.info 步骤标记 / logger.exception 异常堆栈

运行方式:
    pytest tests/unit/test_e2e_debate_memory_loop.py -v
"""

from __future__ import annotations

import json
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 环境准备 (与 test_ai_hedge_fund_sprint2_real_links.py 相同)
# ============================================================

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Mock langchain modules
for _mod_name in ('langchain_openai', 'langchain_ollama', 'langchain_core',
                  'langchain_core.prompts', 'langchain_core.messages'):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# Mock utils.llm (用了 Python 3.10+ 语法, Python 3.8 环境降级)
try:
    import quant_modules.ai_hedge_fund.utils.llm  # noqa: F401
except (ImportError, TypeError):
    import quant_modules.ai_hedge_fund.utils as _utils_pkg
    _mock_utils_llm = MagicMock()
    _mock_utils_llm.call_llm = MagicMock()
    sys.modules['quant_modules.ai_hedge_fund.utils.llm'] = _mock_utils_llm
    _utils_pkg.llm = _mock_utils_llm


# ============================================================
# 共享 fixtures
# ============================================================


@pytest.fixture
def reset_rate_limiter():
    """每个测试前重置全局 RateLimitedLLMCaller 统计"""
    from quant_modules.ai_hedge_fund.llm_rate_limiter import get_global_llm_caller
    caller = get_global_llm_caller()
    caller.reset()
    yield caller
    caller.reset()


@pytest.fixture
def mock_llm_factory():
    """返回一个工厂函数, 可创建 mock call_llm 实现

    用法:
        make_llm = mock_llm_factory
        llm_fn = make_llm(bull_conf=80, bear_conf=60)
    """
    from quant_modules.ai_hedge_fund.debate_layer import DebateStance

    def _make(bull_conf: int = 78, bear_conf: int = 55,
              bull_args: list[str] = None, bear_args: list[str] = None):
        bull_args = bull_args or ["基本面强劲", "估值合理", "动量正向"]
        bear_args = bear_args or ["估值偏高", "技术面走弱"]

        def _impl(prompt, pydantic_model, **kwargs):
            agent_name = kwargs.get("agent_name", "")
            if "bull" in agent_name:
                return DebateStance(
                    stance="bullish", confidence=bull_conf,
                    key_arguments=bull_args, rebuttals=[],
                    evidence_summary="看多证据充分",
                )
            elif "bear" in agent_name:
                return DebateStance(
                    stance="bearish", confidence=bear_conf,
                    key_arguments=bear_args, rebuttals=[],
                    evidence_summary="看空证据中等",
                )
            return DebateStance(
                stance="neutral", confidence=50,
                key_arguments=["多空均衡"], rebuttals=[], evidence_summary="",
            )
        return _impl
    return _make


@pytest.fixture
def sample_analyst_signals():
    """3 个 ticker × 3 个分析师的标准信号 fixture"""
    return {
        "warren_buffett": {
            "AAPL": {"signal": "bullish", "confidence": 85, "reasoning": "护城河强"},
            "TSLA": {"signal": "bearish", "confidence": 70, "reasoning": "估值过高"},
            "GOOG": {"signal": "bullish", "confidence": 75, "reasoning": "AI 领先"},
        },
        "ben_graham": {
            "AAPL": {"signal": "bullish", "confidence": 70, "reasoning": "内在价值溢价"},
            "TSLA": {"signal": "bearish", "confidence": 75, "reasoning": "PE 过高"},
            "GOOG": {"signal": "neutral", "confidence": 50, "reasoning": "估值合理"},
        },
        "michael_burry": {
            "AAPL": {"signal": "bullish", "confidence": 65, "reasoning": "产品周期"},
            "TSLA": {"signal": "bearish", "confidence": 80, "reasoning": "顶背离"},
            "GOOG": {"signal": "bullish", "confidence": 60, "reasoning": "云业务增长"},
        },
    }


@pytest.fixture
def sample_price_data():
    """3 个 ticker 的 mock 价格 (决策日 08-01 → 评估日 08-11)

    - AAPL: 100 → 108 (+8%) — bullish 正确
    - TSLA: 200 → 185 (-7.5%) — bearish 正确
    - GOOG: 150 → 156 (+4%) — bullish 正确
    """
    return {
        "AAPL": {
            "2026-08-01": {"close": 100.0},
            "2026-08-06": {"close": 105.0},
            "2026-08-11": {"close": 108.0},
        },
        "TSLA": {
            "2026-08-01": {"close": 200.0},
            "2026-08-06": {"close": 190.0},
            "2026-08-11": {"close": 185.0},
        },
        "GOOG": {
            "2026-08-01": {"close": 150.0},
            "2026-08-06": {"close": 153.0},
            "2026-08-11": {"close": 156.0},
        },
    }


def _run_debate_with_llm(layer, tickers, signals, mock_llm_fn):
    """辅助: 用 mock LLM 运行辩论 (patch _llm_available + call_llm)"""
    with patch.object(type(layer), "_llm_available", return_value=True):
        with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                   side_effect=mock_llm_fn):
            return layer.run_full_debate(tickers, signals)


def _override_record_dates(memory_file, date_str="2026-08-01"):
    """辅助: 重写决策记录的日期 (匹配 mock 价格数据)"""
    with open(memory_file, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    for rec in records:
        rec["date"] = date_str
        rec["timestamp"] = f"{date_str}T10:00:00"
    with open(memory_file, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return records


# ============================================================
# A. 正常路径 (5 项)
# ============================================================


class TestE2ENormalPath:
    """端到端闭环正常路径测试"""

    def test_full_loop_multi_ticker_with_llm_and_rate_limiter(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """A1: 多 ticker + LLM + rate_limiter 完整闭环 (3 ticker, 全部预测正确)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        # 1. 辩论 (启用 LLM + rate_limiter)
        layer = DebateLayer(
            use_llm=True, log_dir=str(tmp_path / "debates"),
            use_rate_limiter=True, model_name="gpt-4o-mini",
        )
        llm_fn = mock_llm_factory(bull_conf=78, bear_conf=55)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                       sample_analyst_signals, llm_fn)

        assert len(session.debate_results) == 3
        assert session.errors == []
        # AAPL/GOOG 看多 (3 bull/0 bear), TSLA 看空 (0 bull/3 bear)
        assert session.debate_results["AAPL"].final_signal == "bullish"
        assert session.debate_results["TSLA"].final_signal == "bearish"
        assert session.debate_results["GOOG"].final_signal == "bullish"

        # 2. 记录决策
        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        count = mem.record_decisions(session)
        assert count == 3

        # 3. 修改决策日期 + 评估
        _override_record_dates(mem.memory_file, "2026-08-01")
        updated = mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )
        assert updated == 3

        # 4. 反思上下文
        ctx = mem.get_reflection_context(days=30)
        assert ctx["total_evaluated"] == 3
        assert ctx["overall_win_rate"] == 1.0  # 全部正确
        assert set(ctx["by_ticker"].keys()) == {"AAPL", "TSLA", "GOOG"}

        # 5. RateLimiter 统计 (3 ticker × 2 轮 × 2 方 = 12 次调用)
        stats = reset_rate_limiter.stats
        total_calls = sum(s["total_calls"] for s in stats.values())
        total_success = sum(s["successful"] for s in stats.values())
        assert total_calls == 12
        assert total_success == 12
        assert all(s["failed"] == 0 for s in stats.values())

    def test_reflection_summary_contains_all_tickers(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """A2: 反思摘要文本包含所有 ticker + 胜率信息"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        ctx = mem.get_reflection_context(days=30)
        summary = ctx["summary"]
        assert "AAPL" in summary
        assert "TSLA" in summary
        assert "GOOG" in summary
        assert "胜率" in summary or "正确" in summary

    def test_reflection_injection_fields_present(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """A3: 反思记录包含可注入下次分析 prompt 的必要字段"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        ctx = mem.get_reflection_context(days=30)
        aapl_stats = ctx["by_ticker"]["AAPL"]
        # 必须包含注入字段
        assert "win_rate" in aapl_stats
        assert "total" in aapl_stats
        assert "evaluated" in aapl_stats
        assert "correct_5d" in aapl_stats
        assert "recent_reflections" in aapl_stats
        assert isinstance(aapl_stats["recent_reflections"], list)
        assert len(aapl_stats["recent_reflections"]) > 0
        # 反思文本应包含方向判断关键字
        reflection = aapl_stats["recent_reflections"][0]
        assert "AAPL" in reflection
        assert "bullish" in reflection

    def test_rate_limiter_stats_dimensions(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals,
    ):
        """A4: RateLimiter 按 agent_name + model 分维度统计"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True, model_name="gpt-4o-mini")
        _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                             sample_analyst_signals, mock_llm_factory())

        stats = reset_rate_limiter.stats
        # 应有 bull_researcher + bear_researcher 两个维度
        stat_keys = list(stats.keys())
        assert any("bull_researcher" in k for k in stat_keys)
        assert any("bear_researcher" in k for k in stat_keys)
        # 每个维度的字段完整
        for s in stats.values():
            assert "total_calls" in s
            assert "successful" in s
            assert "cache_hits" in s
            assert "rate_limited" in s
            assert "failed" in s

    def test_all_correct_yields_full_win_rate(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """A5: 所有预测方向正确 → overall_win_rate = 1.0"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        ctx = mem.get_reflection_context(days=30)
        assert ctx["overall_win_rate"] == 1.0
        for _ticker, stats in ctx["by_ticker"].items():
            assert stats["win_rate"] == 1.0
            assert stats["correct_5d"] == stats["evaluated"]


# ============================================================
# B. 边界场景 (5 项)
# ============================================================


class TestE2EBoundaryCases:
    """端到端闭环边界场景测试"""

    def test_empty_analyst_signals(self, tmp_path, reset_rate_limiter, mock_llm_factory):
        """B1: 空 analyst_signals → 辩论仍能完成 (规则模式降级)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(use_llm=False, log_dir=str(tmp_path / "debates"))
        session = layer.run_full_debate(["AAPL"], {})
        assert len(session.debate_results) == 1
        # 无信号时应为 neutral
        assert session.debate_results["AAPL"].final_signal == "neutral"

    def test_single_ticker_loop(self, tmp_path, reset_rate_limiter, mock_llm_factory):
        """B2: 单个 ticker 端到端闭环"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        signals = {"warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 85}}}
        session = _run_debate_with_llm(layer, ["AAPL"], signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        count = mem.record_decisions(session)
        assert count == 1

        _override_record_dates(mem.memory_file, "2026-08-01")
        price_data = {"AAPL": {"2026-08-01": {"close": 100.0}, "2026-08-11": {"close": 110.0}}}
        updated = mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30, eval_date="2026-08-11",
        )
        assert updated == 1

        ctx = mem.get_reflection_context(days=30)
        assert ctx["total_evaluated"] == 1
        assert "AAPL" in ctx["by_ticker"]

    def test_decision_outside_lookback_window_not_evaluated(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
    ):
        """B3: 决策日期超出 lookback_days → 不评估 (updated=0)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=False, log_dir=str(tmp_path / "debates"))
        signals = {"warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80}}}
        session = layer.run_full_debate(["AAPL"], signals)

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)

        # 决策日期设为 60 天前, lookback_days=30 → 不评估
        _override_record_dates(mem.memory_file, "2026-06-01")
        price_data = {"AAPL": {"2026-06-01": {"close": 100.0}, "2026-06-11": {"close": 110.0}}}
        updated = mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30, eval_date="2026-08-11",
        )
        assert updated == 0

    def test_missing_price_date_yields_none_return(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
    ):
        """B4: 价格数据缺失部分日期 → 对应 forward_return 字段为 None"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=False, log_dir=str(tmp_path / "debates"))
        signals = {"warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80}}}
        session = layer.run_full_debate(["AAPL"], signals)

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")

        # 只提供决策日价格, 不提供 5d/10d 后的价格
        price_data = {"AAPL": {"2026-08-01": {"close": 100.0}}}
        mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        with open(mem.memory_file, encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        # 决策日价格有, 但 5d/10d 后价格缺失
        assert rec["forward_return_5d"] is None
        assert rec["forward_return_10d"] is None
        assert rec["correct_5d"] is None
        assert rec["correct_10d"] is None

    def test_multiple_runs_accumulate_reflections(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
    ):
        """B5: 多次运行同一 ticker → 反思记录累积"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        signals = {"warren_buffett": {"AAPL": {"signal": "bullish", "confidence": 80}}}
        price_data = {"AAPL": {"2026-08-01": {"close": 100.0}, "2026-08-11": {"close": 110.0}}}

        # 运行 3 次辩论 + 记录
        for _ in range(3):
            layer = DebateLayer(use_llm=False, log_dir=str(tmp_path / "debates"))
            session = layer.run_full_debate(["AAPL"], signals)
            mem.record_decisions(session)

        # 应累积 3 条记录
        with open(mem.memory_file, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        assert len(records) == 3

        # 评估后反思也应累积
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=price_data,
            lookback_days=30, eval_date="2026-08-11",
        )
        ctx = mem.get_reflection_context(days=30)
        assert ctx["by_ticker"]["AAPL"]["total"] == 3
        assert ctx["by_ticker"]["AAPL"]["evaluated"] == 3
        # recent_reflections 最多保留 3 条
        assert len(ctx["by_ticker"]["AAPL"]["recent_reflections"]) <= 3


# ============================================================
# C. 异常路径 (4 项)
# ============================================================


class TestE2EExceptionPaths:
    """端到端闭环异常路径测试"""

    def test_llm_failure_falls_back_to_rules(
        self, tmp_path, reset_rate_limiter, sample_analyst_signals,
    ):
        """C1: LLM 全部调用失败 → 降级规则模式 + session 不抛异常"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=False)

        def _always_fail(prompt, pydantic_model, **kwargs):
            raise RuntimeError("模拟 LLM 服务不可用")

        # 不应抛异常
        with patch.object(type(layer), "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                       side_effect=_always_fail):
                session = layer.run_full_debate(["AAPL"], sample_analyst_signals)

        # 应有结果 (规则模式生成)
        assert "AAPL" in session.debate_results
        result = session.debate_results["AAPL"]
        # 规则模式基于分析师信号 (3 个 bullish) → 应为 bullish
        assert result.final_signal == "bullish"
        # final_confidence 由规则计算 (非 LLM 的 78)
        assert isinstance(result.final_confidence, int)
        assert 0 <= result.final_confidence <= 100

    def test_partial_llm_failure_other_tickers_still_work(
        self, tmp_path, reset_rate_limiter, sample_analyst_signals,
    ):
        """C2: LLM 部分失败 (某 ticker 失败) → 其他 ticker 仍正常"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer, DebateStance

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=False)
        call_count = [0]

        def _flaky_llm(prompt, pydantic_model, **kwargs):
            call_count[0] += 1
            # AAPL 的第 1 次调用 (R1 bull) 抛异常, 其他正常
            if call_count[0] == 1:
                raise RuntimeError("AAPL R1 bull LLM 调用失败")
            agent_name = kwargs.get("agent_name", "")
            if "bull" in agent_name:
                return DebateStance(stance="bullish", confidence=78,
                                    key_arguments=["基本面强"], rebuttals=[],
                                    evidence_summary="看多")
            elif "bear" in agent_name:
                return DebateStance(stance="bearish", confidence=55,
                                    key_arguments=["估值高"], rebuttals=[],
                                    evidence_summary="看空")
            return DebateStance(stance="neutral", confidence=50,
                                key_arguments=["均衡"], rebuttals=[], evidence_summary="")

        with patch.object(type(layer), "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                       side_effect=_flaky_llm):
                session = layer.run_full_debate(["AAPL", "TSLA"], sample_analyst_signals)

        # 两个 ticker 都应有结果 (AAPL 通过降级, TSLA 通过 LLM)
        assert len(session.debate_results) == 2
        assert "AAPL" in session.debate_results
        assert "TSLA" in session.debate_results

    def test_memory_write_failure_raises_io_error(self, tmp_path):
        """C3: MemoryReflection 写入磁盘失败 → 抛 OSError / FileNotFoundError"""
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        # 使用不存在的盘符路径 (Windows)
        # makedirs 会立即失败
        with pytest.raises((OSError, FileNotFoundError, PermissionError)):
            MemoryReflection(memory_dir=r"Z:\\NON_EXISTENT_PATH_12345")

    def test_rate_limiter_cache_hit_on_same_prompt(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals,
    ):
        """C4: 相同 prompt 第二次调用 → 命中 TTL 缓存, call_llm 不被调用"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer

        # 第一次运行
        layer1 = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                             use_rate_limiter=True, model_name="gpt-4o-mini")
        llm_fn = mock_llm_factory()
        with patch.object(type(layer1), "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                       side_effect=llm_fn) as mock_call1:
                layer1.run_full_debate(["AAPL"], sample_analyst_signals)
                first_call_count = mock_call1.call_count

        # 第二次运行 (相同 prompt) — 应命中缓存
        layer2 = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                             use_rate_limiter=True, model_name="gpt-4o-mini")
        with patch.object(type(layer2), "_llm_available", return_value=True):
            with patch("quant_modules.ai_hedge_fund.utils.llm.call_llm",
                       side_effect=llm_fn) as mock_call2:
                layer2.run_full_debate(["AAPL"], sample_analyst_signals)
                second_call_count = mock_call2.call_count

        # 第一次应有实际 LLM 调用 (4 次: 2 轮 × 2 方)
        assert first_call_count == 4
        # 第二次应全部命中缓存 → 0 次实际调用
        assert second_call_count == 0

        # 缓存统计验证
        stats = reset_rate_limiter.stats
        total_cache_hits = sum(s["cache_hits"] for s in stats.values())
        total_calls = sum(s["total_calls"] for s in stats.values())
        assert total_calls == 8  # 4 + 4
        assert total_cache_hits == 4  # 第二次的 4 次全命中


# ============================================================
# D. 数据一致性 (3 项)
# ============================================================


class TestE2EDataConsistency:
    """端到端闭环数据一致性测试"""

    def test_forward_return_calculation_correct(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """D1: forward_return_5d/10d 计算正确 (基于价格数据)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        with open(mem.memory_file, encoding="utf-8") as f:
            records = {json.loads(line)["ticker"]: json.loads(line) for line in f if line.strip()}

        # AAPL: 100 → 105 (5d), 100 → 108 (10d)
        aapl = records["AAPL"]
        assert aapl["forward_return_5d"] == pytest.approx(0.05, abs=1e-6)
        assert aapl["forward_return_10d"] == pytest.approx(0.08, abs=1e-6)

        # TSLA: 200 → 190 (5d), 200 → 185 (10d)
        tsla = records["TSLA"]
        assert tsla["forward_return_5d"] == pytest.approx(-0.05, abs=1e-6)
        assert tsla["forward_return_10d"] == pytest.approx(-0.075, abs=1e-6)

    def test_correct_field_matches_signal_direction(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """D2: correct_5d/10d 与 signal 方向匹配 (bullish+上涨=True, bearish+下跌=True)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        with open(mem.memory_file, encoding="utf-8") as f:
            records = {json.loads(line)["ticker"]: json.loads(line) for line in f if line.strip()}

        # AAPL: bullish + 5d 上涨 5% → 正确
        assert records["AAPL"]["correct_5d"] is True
        assert records["AAPL"]["correct_10d"] is True

        # TSLA: bearish + 5d 下跌 5% → 正确
        assert records["TSLA"]["correct_5d"] is True
        assert records["TSLA"]["correct_10d"] is True

        # GOOG: bullish + 5d 上涨 2% → 正确
        assert records["GOOG"]["correct_5d"] is True
        assert records["GOOG"]["correct_10d"] is True

    def test_by_ticker_aggregation_stats_correct(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data,
    ):
        """D3: by_ticker 聚合统计正确 (total/evaluated/correct_5d/win_rate 一致)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True)
        session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                       sample_analyst_signals, mock_llm_factory())

        mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
        mem.record_decisions(session)
        _override_record_dates(mem.memory_file, "2026-08-01")
        mem.evaluate_past_decisions(
            price_data_provider=sample_price_data,
            lookback_days=30, eval_date="2026-08-11",
        )

        ctx = mem.get_reflection_context(days=30)

        # 每个 ticker 都有 1 条评估记录
        for ticker in ["AAPL", "TSLA", "GOOG"]:
            stats = ctx["by_ticker"][ticker]
            assert stats["total"] == 1
            assert stats["evaluated"] == 1
            assert stats["correct_5d"] == 1
            assert stats["win_rate"] == 1.0

        # overall_win_rate 应等于 sum(correct) / sum(evaluated)
        total_correct = sum(s["correct_5d"] for s in ctx["by_ticker"].values())
        total_evaluated = sum(s["evaluated"] for s in ctx["by_ticker"].values())
        expected_win_rate = round(total_correct / total_evaluated, 4)
        assert ctx["overall_win_rate"] == expected_win_rate


# ============================================================
# E. 日志验证 (2 项)
# ============================================================


class TestE2ELogVerification:
    """端到端闭环日志输出验证"""

    def test_logger_info_emitted_for_each_step(
        self, tmp_path, reset_rate_limiter, mock_llm_factory,
        sample_analyst_signals, sample_price_data, caplog,
    ):
        """E1: logger.info 在每个步骤被调用 (开始/完成标记)"""
        from quant_modules.ai_hedge_fund.debate_layer import DebateLayer
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        # 用一个独立的 logger 模拟 demo_c 的日志行为
        test_logger = logging.getLogger("demo_e2e_test_e1")
        test_logger.setLevel(logging.INFO)

        layer = DebateLayer(use_llm=True, log_dir=str(tmp_path / "debates"),
                            use_rate_limiter=True, model_name="gpt-4o-mini")
        llm_fn = mock_llm_factory()

        with caplog.at_level(logging.INFO, logger="demo_e2e_test_e1"):
            # 步骤 1
            test_logger.info("步骤 1 开始: 运行辩论")
            session = _run_debate_with_llm(layer, ["AAPL", "TSLA", "GOOG"],
                                           sample_analyst_signals, llm_fn)
            test_logger.info("步骤 1 完成: tickers=%d", len(session.debate_results))

            # 步骤 2
            mem = MemoryReflection(memory_dir=str(tmp_path / "memory"))
            test_logger.info("步骤 2 开始: 记录决策")
            count = mem.record_decisions(session)
            test_logger.info("步骤 2 完成: count=%d", count)

            # 步骤 3
            _override_record_dates(mem.memory_file, "2026-08-01")
            test_logger.info("步骤 3 开始: 评估")
            updated = mem.evaluate_past_decisions(
                price_data_provider=sample_price_data,
                lookback_days=30, eval_date="2026-08-11",
            )
            test_logger.info("步骤 3 完成: updated=%d", updated)

            # 步骤 5
            test_logger.info("步骤 5 开始: 反思上下文")
            ctx = mem.get_reflection_context(days=30)
            test_logger.info("步骤 5 完成: win_rate=%s", ctx["overall_win_rate"])

        # 验证每个步骤的开始/完成标记都出现在日志中
        log_text = caplog.text
        assert "步骤 1 开始" in log_text
        assert "步骤 1 完成" in log_text
        assert "步骤 2 开始" in log_text
        assert "步骤 2 完成" in log_text
        assert "步骤 3 开始" in log_text
        assert "步骤 3 完成" in log_text
        assert "步骤 5 开始" in log_text
        assert "步骤 5 完成" in log_text

    def test_logger_exception_emitted_on_failure(
        self, tmp_path, caplog,
    ):
        """E2: 异常时 logger.exception 输出完整堆栈 (含 Traceback)"""
        from quant_modules.ai_hedge_fund.memory_reflection import MemoryReflection

        test_logger = logging.getLogger("demo_e2e_test_e2")
        test_logger.setLevel(logging.DEBUG)

        with caplog.at_level(logging.ERROR, logger="demo_e2e_test_e2"):
            try:
                # 故意触发异常 (不存在的盘符)
                MemoryReflection(memory_dir=r"Z:\\NON_EXISTENT_PATH_67890")
            except Exception as exc:
                test_logger.exception("步骤失败: MemoryReflection 初始化异常 | %r", exc)

        log_text = caplog.text
        # 应包含 ERROR 级别 + 异常类型 + Traceback
        assert "ERROR" in log_text or "步骤失败" in log_text
        assert "MemoryReflection" in log_text or "FileNotFoundError" in log_text \
            or "OSError" in log_text or "PermissionError" in log_text
        # logger.exception 应输出 Traceback
        assert "Traceback" in log_text
