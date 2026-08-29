"""D1 单元测试 — StrategyIdeationEngine LLM 策略 Ideation 引擎."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from utils.llm_evolution.strategy_ideation import (
    Hypothesis,
    MarketObservation,
    StrategyIdeationEngine,
)

# ============================================================
# 测试夹具
# ============================================================


class MockLLM:
    """模拟 LLM 返回预设 JSON."""

    def __init__(self, response: str | None = None, name: str = "mock"):
        self.name = name
        self._response = response or self._default_response()
        self.call_count = 0

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        self.call_count += 1
        return self._response

    def _default_response(self) -> str:
        return json.dumps(
            {
                "hypotheses": [
                    {
                        "description": "低估值因子在市场调整后表现更佳",
                        "market_observation": "今日指数下跌 2%, 估值因子有望反弹",
                        "factor_direction": "long_small",
                        "proposed_factors": [
                            {"name": "EP", "category": "Value", "formula": "1/PE"}
                        ],
                        "strategy_style": "value",
                    },
                    {
                        "description": "动量因子在上涨趋势中持续有效",
                        "market_observation": "指数连续 3 日上涨",
                        "factor_direction": "long_large",
                        "proposed_factors": [
                            {
                                "name": "MOM_20D",
                                "category": "Momentum",
                                "formula": "close/close[-20]-1",
                            }
                        ],
                        "strategy_style": "momentum",
                    },
                ]
            }
        )


class EmptyLLM:
    """返回 None 的 LLM."""

    name = "empty"

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        return None


class ErrorLLM:
    """抛异常的 LLM."""

    name = "error"

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        raise RuntimeError("API timeout")


def _make_market_data() -> dict[str, Any]:
    return {
        "date": "2026-08-12",
        "index_close": 3200.0,
        "index_change_pct": -1.5,
        "volume": 500_000_000,
        "sector_performance": {"银行": 1.2, "科技": -3.1, "消费": -0.5},
        "north_flow": -25.3,
        "market_breadth": 0.35,
        "volatility": 0.018,
        "summary": "市场调整, 北向流出",
    }


def _make_engine(
    llm: Any = None, audit: Any = None, **kwargs
) -> StrategyIdeationEngine:
    return StrategyIdeationEngine(
        llm_router=llm or MockLLM(),
        audit_logger=audit or MagicMock(),
        **kwargs,
    )


# ============================================================
# MarketObservation 测试
# ============================================================


class TestMarketObservation:
    def test_to_prompt_context(self):
        obs = MarketObservation(
            date="2026-08-12",
            index_close=3200.0,
            index_change_pct=-1.5,
            volume=500_000_000,
            north_flow=-25.3,
            market_breadth=0.35,
            volatility=0.018,
            sector_performance={"银行": 1.2, "科技": -3.1},
        )
        ctx = obs.to_prompt_context()
        assert "2026-08-12" in ctx
        assert "3200" in ctx
        assert "-1.50%" in ctx
        assert "银行" in ctx

    def test_to_prompt_context_empty(self):
        obs = MarketObservation()
        ctx = obs.to_prompt_context()
        assert "日期" in ctx  # 不报错即可


# ============================================================
# Hypothesis 测试
# ============================================================


class TestHypothesis:
    def test_compute_diversity_hash(self):
        h1 = Hypothesis(
            description="低估值因子",
            factor_direction="long_small",
            strategy_style="value",
        )
        h2 = Hypothesis(
            description="低估值因子",
            factor_direction="long_small",
            strategy_style="value",
        )
        h3 = Hypothesis(
            description="动量因子",
            factor_direction="long_large",
            strategy_style="momentum",
        )
        h1.compute_diversity_hash()
        h2.compute_diversity_hash()
        h3.compute_diversity_hash()
        assert h1.diversity_hash == h2.diversity_hash
        assert h1.diversity_hash != h3.diversity_hash


# ============================================================
# 引擎构造
# ============================================================


class TestEngineConstruction:
    def test_none_llm_raises(self):
        with pytest.raises(ValueError, match="llm_router 不能为 None"):
            StrategyIdeationEngine(llm_router=None)

    def test_default_shadow_mode(self):
        engine = _make_engine()
        assert engine.shadow_mode is True

    def test_custom_min_diversity(self):
        engine = _make_engine(min_diversity=0.8)
        assert engine.min_diversity == 0.8


# ============================================================
# 市场观察
# ============================================================


class TestObserveMarket:
    def test_observe_market(self):
        engine = _make_engine()
        obs = engine.observe_market(_make_market_data())
        assert obs.date == "2026-08-12"
        assert obs.index_close == 3200.0
        assert obs.index_change_pct == -1.5
        assert "银行" in obs.sector_performance

    def test_observe_market_defaults(self):
        engine = _make_engine()
        obs = engine.observe_market({})
        assert obs.date != ""  # 默认当天


# ============================================================
# 假设生成
# ============================================================


class TestGenerateHypotheses:
    def test_normal_generation(self):
        engine = _make_engine()
        obs = engine.observe_market(_make_market_data())
        hyps = engine.generate_hypotheses(obs, n=5)
        assert len(hyps) == 2
        assert hyps[0].description != ""
        assert hyps[0].factor_direction in ("long_small", "long_large", "long_short")
        assert hyps[0].diversity_hash != ""

    def test_llm_returns_none(self):
        engine = _make_engine(llm=EmptyLLM())
        obs = MarketObservation(date="2026-08-12")
        hyps = engine.generate_hypotheses(obs)
        assert len(hyps) == 0

    def test_llm_raises_exception(self):
        engine = _make_engine(llm=ErrorLLM())
        obs = MarketObservation(date="2026-08-12")
        hyps = engine.generate_hypotheses(obs)
        assert len(hyps) == 0

    def test_n_zero_raises(self):
        engine = _make_engine()
        obs = MarketObservation()
        with pytest.raises(ValueError, match="n 应 ≥1"):
            engine.generate_hypotheses(obs, n=0)

    def test_json_with_code_block(self):
        """LLM 返回 ```json ... ``` 格式."""
        llm = MockLLM()
        llm._response = (
            "```json\n"
            + json.dumps(
                {
                    "hypotheses": [
                        {
                            "description": "测试",
                            "factor_direction": "long_small",
                            "proposed_factors": [],
                            "strategy_style": "value",
                        }
                    ]
                }
            )
            + "\n```"
        )
        engine = _make_engine(llm=llm)
        obs = MarketObservation(date="2026-08-12")
        hyps = engine.generate_hypotheses(obs)
        assert len(hyps) == 1
        assert hyps[0].description == "测试"

    def test_invalid_json_returns_empty(self):
        llm = MockLLM()
        llm._response = "这不是 JSON"
        engine = _make_engine(llm=llm)
        obs = MarketObservation(date="2026-08-12")
        hyps = engine.generate_hypotheses(obs)
        assert len(hyps) == 0

    def test_diversity_filter(self):
        """重复假设应被去重."""
        llm = MockLLM()
        # 返回两个完全相同的假设
        llm._response = json.dumps(
            {
                "hypotheses": [
                    {
                        "description": "相同假设",
                        "factor_direction": "long_small",
                        "proposed_factors": [],
                        "strategy_style": "value",
                    },
                    {
                        "description": "相同假设",
                        "factor_direction": "long_small",
                        "proposed_factors": [],
                        "strategy_style": "value",
                    },
                ]
            }
        )
        engine = _make_engine(llm=llm)
        obs = MarketObservation(date="2026-08-12")
        hyps = engine.generate_hypotheses(obs)
        assert len(hyps) == 1  # 去重后只剩 1 个


# ============================================================
# 因子设计与验证
# ============================================================


class TestDesignAndValidate:
    def test_design_factors(self):
        engine = _make_engine()
        hyp = Hypothesis(
            id="h1",
            proposed_factors=[{"name": "EP", "category": "Value", "formula": "1/PE"}],
        )
        candidates = engine.design_factors(hyp)
        assert len(candidates) == 1
        assert candidates[0]["name"] == "EP"

    def test_validate_no_verifier(self):
        """无 verifier 时直接返回候选."""
        engine = _make_engine()
        candidates = [{"name": "EP", "category": "Value"}]
        result = engine.validate_factors(candidates, verifier=None)
        assert len(result) == len(candidates)

    def test_validate_with_mock_verifier(self):
        engine = _make_engine()
        verifier = MagicMock()
        verifier.verify.return_value = {"pass": True, "factor_name": "EP"}
        candidates = [{"name": "EP", "category": "Value"}]
        result = engine.validate_factors(candidates, verifier=verifier)
        assert len(result) == 1
        assert result[0]["validated"] is True

    def test_validate_rejected(self):
        engine = _make_engine()
        verifier = MagicMock()
        verifier.verify.return_value = {"pass": False, "factor_name": "EP"}
        candidates = [{"name": "EP", "category": "Value"}]
        result = engine.validate_factors(candidates, verifier=verifier)
        assert len(result) == 0


# ============================================================
# 入库
# ============================================================


class TestPromoteToLibrary:
    def test_shadow_mode(self):
        engine = _make_engine(shadow_mode=True)
        count = engine.promote_to_library([{"name": "EP"}])
        assert count == 1  # Shadow 模式也返回 count, 但不实际执行

    def test_production_mode(self):
        engine = _make_engine(shadow_mode=False)
        count = engine.promote_to_library([{"name": "EP"}, {"name": "MOM"}])
        assert count == 2


# ============================================================
# 完整周期
# ============================================================


class TestIdeationCycle:
    def test_full_cycle(self):
        engine = _make_engine()
        result = engine.run_ideation_cycle(_make_market_data(), n_hypotheses=5)
        assert result.cycle_id.startswith("ideation_")
        assert len(result.hypotheses) > 0
        assert result.diversity_score > 0

    def test_cycle_with_verifier(self):
        engine = _make_engine()
        verifier = MagicMock()
        verifier.verify.return_value = {
            "pass": True,
            "factor_name": "EP",
            "rank_ic_mean": 0.05,
            "icir": 0.8,
        }
        result = engine.run_ideation_cycle(
            _make_market_data(), n_hypotheses=5, verifier=verifier
        )
        assert result.total_validated > 0

    def test_cycle_llm_error(self):
        engine = _make_engine(llm=ErrorLLM())
        result = engine.run_ideation_cycle(_make_market_data())
        assert len(result.errors) > 0 or len(result.hypotheses) == 0


# ============================================================
# 多样性计算
# ============================================================


class TestDiversity:
    def test_all_unique(self):
        engine = _make_engine()
        hyps = [
            Hypothesis(
                description="A", factor_direction="long_small", strategy_style="value"
            ),
            Hypothesis(
                description="B",
                factor_direction="long_large",
                strategy_style="momentum",
            ),
        ]
        for h in hyps:
            h.compute_diversity_hash()
        score = engine._compute_diversity_score(hyps)
        assert score == 1.0

    def test_all_same(self):
        engine = _make_engine()
        hyps = [
            Hypothesis(
                description="A", factor_direction="long_small", strategy_style="value"
            ),
            Hypothesis(
                description="A", factor_direction="long_small", strategy_style="value"
            ),
        ]
        for h in hyps:
            h.compute_diversity_hash()
        score = engine._compute_diversity_score(hyps)
        assert score == 0.5

    def test_empty(self):
        engine = _make_engine()
        assert engine._compute_diversity_score([]) == 0.0
