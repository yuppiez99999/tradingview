"""D4 单元测试 — DualLoopOrchestrator 双层闭环编排器."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from utils.llm_evolution.dual_loop_orchestrator import (
    DualLoopOrchestrator,
    DualLoopReport,
    DualLoopSafetyConfig,
)
from utils.llm_evolution.strategy_ideation import (
    StrategyIdeationEngine,
    MarketObservation,
    Hypothesis,
)
from utils.llm_evolution.hypothesis_verifier import HypothesisVerifier
from utils.llm_evolution.knowledge_base import KnowledgeBase


# ============================================================
# 测试夹具
# ============================================================

class MockLLM:
    name = "mock"

    def chat(self, prompt: str, system: str = "", temperature: float | None = None,
             max_tokens: int | None = None) -> str | None:
        return json.dumps({
            "hypotheses": [{
                "description": "低估值因子在调整后反弹",
                "market_observation": "市场下跌",
                "factor_direction": "long_small",
                "proposed_factors": [{"name": "EP", "category": "Value", "formula": "1/PE"}],
                "strategy_style": "value",
            }]
        })


def _make_market_data() -> dict[str, Any]:
    return {"date": "2026-08-12", "index_close": 3200.0, "index_change_pct": -1.5}


def _make_factor_data() -> dict[str, dict[str, Any]]:
    """IC 显著的因子数据."""
    import random
    random.seed(42)
    return {
        "EP": {
            "ic_series": [0.04 + random.gauss(0, 0.01) for _ in range(100)],
            "max_drawdown": 0.08,
            "wf_mean_ic": 0.038,
            "dsr_score": 1.5,
        }
    }


def _make_orchestrator(
    tmp_path: Path,
    safety: DualLoopSafetyConfig | None = None,
    kill_switch: Any = None,
    circuit_breaker: Any = None,
) -> DualLoopOrchestrator:
    audit = MagicMock()
    engine = StrategyIdeationEngine(llm_router=MockLLM(), audit_logger=audit)
    verifier = HypothesisVerifier(audit_logger=audit)
    kb = KnowledgeBase(path=tmp_path / "kb.jsonl")
    return DualLoopOrchestrator(
        ideation_engine=engine,
        verifier=verifier,
        knowledge_base=kb,
        kill_switch=kill_switch,
        circuit_breaker=circuit_breaker,
        safety=safety,
    )


# ============================================================
# DualLoopReport 属性
# ============================================================

class TestDualLoopReport:
    def test_success_rate(self):
        r = DualLoopReport(total_cycles=4, successful_cycles=3, failed_cycles=1)
        assert r.success_rate == 0.75

    def test_success_rate_zero(self):
        r = DualLoopReport()
        assert r.success_rate == 0.0

    def test_summary_text(self):
        r = DualLoopReport(total_cycles=5, successful_cycles=3, total_hypotheses=10)
        text = r.summary_text()
        assert "周期=5" in text
        assert "假设=10" in text


# ============================================================
# 构造
# ============================================================

class TestConstruction:
    def test_none_engine_raises(self, tmp_path):
        with pytest.raises(ValueError, match="ideation_engine 不能为 None"):
            DualLoopOrchestrator(
                ideation_engine=None,
                verifier=MagicMock(),
                knowledge_base=MagicMock(),
            )

    def test_default_safety(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        assert orch.safety.max_cycles == 7
        assert orch.safety.max_consecutive_failures == 3


# ============================================================
# 单周期
# ============================================================

class TestRunCycle:
    def test_normal_cycle(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        report = orch.run_cycle(_make_market_data(), _make_factor_data())
        assert report.total_cycles == 1
        assert report.total_hypotheses > 0
        assert report.total_kb_entries > 0

    def test_cycle_without_factor_data(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        report = orch.run_cycle(_make_market_data())
        # 无 factor_data, 因子会被证伪, 但知识库仍有记录
        assert report.total_cycles == 1
        assert report.total_kb_entries > 0

    def test_cycle_exception_handled(self, tmp_path):
        """LLM 异常时 cycle 不崩溃."""
        from utils.llm_evolution.strategy_ideation import StrategyIdeationEngine

        class ErrorLLM:
            name = "error"
            def chat(self, *a, **kw):
                raise RuntimeError("API down")

        engine = StrategyIdeationEngine(llm_router=ErrorLLM(), audit_logger=MagicMock())
        orch = DualLoopOrchestrator(
            ideation_engine=engine,
            verifier=HypothesisVerifier(audit_logger=MagicMock()),
            knowledge_base=KnowledgeBase(path=tmp_path / "kb.jsonl"),
        )
        report = orch.run_cycle(_make_market_data())
        assert report.total_cycles == 1
        # LLM 异常, 无假设生成
        assert report.total_hypotheses == 0


# ============================================================
# 安全检查
# ============================================================

class TestSafetyChecks:
    def test_kill_switch_pause(self, tmp_path):
        ks = MagicMock()
        ks.evaluate_trade.return_value = MagicMock(allowed=False, reason="L2 REDUCTION")
        orch = _make_orchestrator(tmp_path, kill_switch=ks)
        report = orch.run_cycle(_make_market_data())
        assert report.paused is True
        assert "Kill Switch" in report.pause_reason

    def test_circuit_breaker_pause(self, tmp_path):
        cb = MagicMock()
        cb.allow_trading = False
        orch = _make_orchestrator(tmp_path, circuit_breaker=cb)
        report = orch.run_cycle(_make_market_data())
        assert report.paused is True
        assert "Circuit Breaker" in report.pause_reason

    def test_kill_switch_allows(self, tmp_path):
        ks = MagicMock()
        ks.evaluate_trade.return_value = MagicMock(allowed=True, reason="NORMAL")
        orch = _make_orchestrator(tmp_path, kill_switch=ks)
        report = orch.run_cycle(_make_market_data(), _make_factor_data())
        assert report.paused is False

    def test_safety_disabled(self, tmp_path):
        """关闭安全检查时, Kill Switch 不拦截."""
        ks = MagicMock()
        ks.evaluate_trade.return_value = MagicMock(allowed=False, reason="BLOCKED")
        safety = DualLoopSafetyConfig(kill_switch_check=False, circuit_breaker_check=False)
        orch = _make_orchestrator(tmp_path, safety=safety, kill_switch=ks)
        report = orch.run_cycle(_make_market_data(), _make_factor_data())
        assert report.paused is False


# ============================================================
# 连续运行
# ============================================================

class TestRunContinuous:
    def test_max_cycles(self, tmp_path):
        safety = DualLoopSafetyConfig(max_cycles=3, require_manual_approval_after=False)
        orch = _make_orchestrator(tmp_path, safety=safety)
        report = orch.run_continuous(
            market_data_fn=_make_market_data,
            factor_data_fn=_make_factor_data,
            max_cycles=3,
        )
        assert report.total_cycles == 3
        assert report.paused is False  # 不要求人工审批

    def test_consecutive_failures_pause(self, tmp_path):
        """连续失败 ≥3 次后暂停."""
        from utils.llm_evolution.strategy_ideation import StrategyIdeationEngine

        class ErrorLLM:
            name = "error"
            def chat(self, *a, **kw):
                raise RuntimeError("always fails")

        engine = StrategyIdeationEngine(llm_router=ErrorLLM(), audit_logger=MagicMock())
        safety = DualLoopSafetyConfig(max_cycles=10, max_consecutive_failures=3)
        orch = DualLoopOrchestrator(
            ideation_engine=engine,
            verifier=HypothesisVerifier(audit_logger=MagicMock()),
            knowledge_base=KnowledgeBase(path=tmp_path / "kb.jsonl"),
            safety=safety,
        )
        report = orch.run_continuous(market_data_fn=_make_market_data)
        assert report.paused is True
        assert "连续失败" in report.pause_reason
        assert report.total_cycles <= 3

    def test_manual_approval_required(self, tmp_path):
        safety = DualLoopSafetyConfig(max_cycles=2, require_manual_approval_after=True)
        orch = _make_orchestrator(tmp_path, safety=safety)
        report = orch.run_continuous(
            market_data_fn=_make_market_data,
            factor_data_fn=_make_factor_data,
        )
        assert "人工审批" in report.pause_reason

    def test_kill_switch_pauses_continuous(self, tmp_path):
        ks = MagicMock()
        ks.evaluate_trade.return_value = MagicMock(allowed=False, reason="BLOCKED")
        safety = DualLoopSafetyConfig(max_cycles=5)
        orch = _make_orchestrator(tmp_path, safety=safety, kill_switch=ks)
        report = orch.run_continuous(market_data_fn=_make_market_data)
        assert report.paused is True
        assert report.total_cycles == 0  # 第一周期就暂停


# ============================================================
# 状态重置
# ============================================================

class TestReset:
    def test_reset(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._consecutive_failures = 5
        orch._total_token = 10000
        orch.reset()
        assert orch._consecutive_failures == 0
        assert orch._total_token == 0
