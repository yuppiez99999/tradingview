"""D5 AutoResearch Skill 单元测试.

覆盖:
    - ExpressionFactorGenerator: 默认模板生成 + 自定义模板
    - StandardFactorEvaluator: IC 计算模拟 + HonestValidation 调用 + 错误降级
    - S1-S5 门禁: 通过/失败边界
    - AutoResearchSkill 编排器: 完整迭代 + 衰减退役 + dry_run 铁律
    - create_default_skill 工厂

运行:
    python -m pytest tests/unit/test_d5_auto_research_skill.py -v
"""
from __future__ import annotations

import pytest

from ai_decision.auto_research_defaults import (
    ExpressionFactorGenerator,
    S1EffectiveICGate,
    S2EffectiveICIRGate,
    S3LongShortSharpeGate,
    S4OrthogonalGate,
    S5BacktestIncrementGate,
    StandardFactorEvaluator,
    create_default_skill,
)
from ai_decision.auto_research_skill import (
    AutoResearchConfig,
    AutoResearchSkill,
    FactorCandidate,
    FactorEvaluationResult,
    GateStage,
    GateStatus,
    InMemoryFactorRegistry,
    ResearchContext,
)

# ============================================================
# Fixtures
# ============================================================


def _make_price_data(n_days: int = 60, base: float = 10.0) -> dict:
    """构造模拟 price_data (单 symbol)."""
    closes = [base * (1 + 0.002 * i + 0.01 * ((-1) ** i)) for i in range(n_days)]
    return {
        "sh600000": {
            "close": closes,
            "open": [c * 0.999 for c in closes],
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "volume": [1000000] * n_days,
        }
    }


@pytest.fixture
def context() -> ResearchContext:
    return ResearchContext(
        price_data=_make_price_data(60),
        active_factors=["MOM_60D"],
        baseline_equity_curve=[1_000_000 * (1 + 0.001 * i) for i in range(60)],
    )


@pytest.fixture
def skill() -> AutoResearchSkill:
    return create_default_skill()


# ============================================================
# 1. ExpressionFactorGenerator
# ============================================================


class TestExpressionFactorGenerator:
    def test_default_templates(self) -> None:
        gen = ExpressionFactorGenerator()
        candidates = gen.generate(ResearchContext())
        assert len(candidates) == 9
        assert all(c.source == "expression" for c in candidates)
        assert candidates[0].name == "EXPR_RANK_CLOSE_5"
        assert candidates[0].category == "Momentum"

    def test_custom_templates(self) -> None:
        custom = [("CUSTOM_1", "Value", "ep", "市盈率倒数")]
        gen = ExpressionFactorGenerator(templates=custom)
        candidates = gen.generate(ResearchContext())
        assert len(candidates) == 1
        assert candidates[0].name == "CUSTOM_1"
        assert candidates[0].category == "Value"

    def test_candidate_has_id_and_timestamp(self) -> None:
        gen = ExpressionFactorGenerator()
        candidates = gen.generate(ResearchContext())
        assert candidates[0].candidate_id.startswith("fc_")
        assert candidates[0].created_at  # ISO 时间戳非空


# ============================================================
# 2. StandardFactorEvaluator
# ============================================================


class TestStandardFactorEvaluator:
    def test_evaluate_normal(self, context: ResearchContext) -> None:
        evaluator = StandardFactorEvaluator()
        cand = FactorCandidate(name="TEST_1", category="Momentum", source="test")
        result = evaluator.evaluate(cand, context)
        assert result.candidate.name == "TEST_1"
        assert result.n_observations > 0
        assert result.evaluation_time_ms >= 0.0
        assert result.error_message == ""

    def test_evaluate_empty_price(self) -> None:
        evaluator = StandardFactorEvaluator()
        ctx = ResearchContext()  # 空 price_data
        cand = FactorCandidate(name="TEST_2", category="Momentum", source="test")
        result = evaluator.evaluate(cand, ctx)
        assert result.error_message != ""
        assert result.n_observations == 0

    def test_evaluate_short_price(self) -> None:
        evaluator = StandardFactorEvaluator()
        ctx = ResearchContext(price_data=_make_price_data(10))  # < 30
        cand = FactorCandidate(name="TEST_3", category="Momentum", source="test")
        result = evaluator.evaluate(cand, ctx)
        assert result.error_message != ""

    def test_gate_status_populated(self, context: ResearchContext) -> None:
        evaluator = StandardFactorEvaluator()
        cand = FactorCandidate(name="TEST_4", category="Momentum", source="test")
        result = evaluator.evaluate(cand, context)
        gs = result.gate_status
        # IC/ICIR/夏普 应被填充 (非 None)
        assert isinstance(gs.s1_effective_ic, float)
        assert isinstance(gs.s2_effective_icir, float)
        assert isinstance(gs.s3_long_short_sharpe, float)

    def test_honest_validation_called(self, context: ResearchContext) -> None:
        """验证 HonestValidation 被调用 (enable_honest_validation=True)."""
        cfg = AutoResearchConfig(enable_honest_validation=True)
        evaluator = StandardFactorEvaluator(cfg)
        cand = FactorCandidate(name="TEST_HV", category="Momentum", source="test")
        result = evaluator.evaluate(cand, context)
        # HonestValidation 可能因数据量不足返回 None, 但不应报错
        assert result.error_message == ""


# ============================================================
# 3. S1-S5 门禁
# ============================================================


class TestGates:
    def _make_eval_result(
        self,
        ic: float = 0.05,
        icir: float = 0.5,
        sharpe: float = 1.5,
        corr: float = 0.3,
        increment: float = 0.1,
    ) -> FactorEvaluationResult:
        cand = FactorCandidate(name="GATE_TEST", category="Momentum", source="test")
        gs = GateStatus(
            s1_effective_ic=ic,
            s2_effective_icir=icir,
            s3_long_short_sharpe=sharpe,
            s4_max_corr_with_existing=corr,
            s5_backtest_increment=increment,
        )
        return FactorEvaluationResult(candidate=cand, gate_status=gs)

    def test_s1_pass(self) -> None:
        gate = S1EffectiveICGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(ic=0.05))
        assert passed is True

    def test_s1_fail(self) -> None:
        gate = S1EffectiveICGate()
        passed, reason = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(ic=0.01))
        assert passed is False
        assert "0.0100" in reason

    def test_s2_pass(self) -> None:
        gate = S2EffectiveICIRGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(icir=0.5))
        assert passed is True

    def test_s2_fail(self) -> None:
        gate = S2EffectiveICIRGate()
        passed, reason = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(icir=0.1))
        assert passed is False
        assert "0.1000" in reason

    def test_s3_pass(self) -> None:
        gate = S3LongShortSharpeGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(sharpe=1.5))
        assert passed is True

    def test_s3_fail(self) -> None:
        gate = S3LongShortSharpeGate()
        passed, reason = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(sharpe=0.5))
        assert passed is False
        assert "0.5000" in reason

    def test_s4_pass(self) -> None:
        gate = S4OrthogonalGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(corr=0.3))
        assert passed is True

    def test_s4_fail(self) -> None:
        gate = S4OrthogonalGate()
        passed, reason = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(corr=0.8))
        assert passed is False
        assert "0.8000" in reason

    def test_s5_pass(self) -> None:
        gate = S5BacktestIncrementGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(increment=0.1))
        assert passed is True

    def test_s5_fail(self) -> None:
        gate = S5BacktestIncrementGate()
        passed, reason = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(increment=0.01))
        assert passed is False
        assert "0.0100" in reason

    def test_s5_no_baseline_pass(self) -> None:
        """无基准曲线 (increment=0) 默认通过."""
        gate = S5BacktestIncrementGate()
        passed, _ = gate.check(FactorCandidate("x", "M", "t"), self._make_eval_result(increment=0.0))
        assert passed is True

    def test_gate_stage_property(self) -> None:
        assert S1EffectiveICGate().stage == GateStage.S1_EFFECTIVE_IC
        assert S2EffectiveICIRGate().stage == GateStage.S2_EFFECTIVE_ICIR
        assert S3LongShortSharpeGate().stage == GateStage.S3_LONG_SHORT_SHARPE
        assert S4OrthogonalGate().stage == GateStage.S4_ORTHOGONAL
        assert S5BacktestIncrementGate().stage == GateStage.S5_BACKTEST_INCREMENT


# ============================================================
# 4. AutoResearchSkill 编排器
# ============================================================


class TestAutoResearchSkill:
    def test_full_iteration(self, skill: AutoResearchSkill, context: ResearchContext) -> None:
        """完整迭代: 生成 → 评估 → 门禁 → 注册."""
        iteration = skill.run_iteration(context)
        assert iteration.iteration_id.startswith("iter_")
        assert len(iteration.candidates_generated) == 9
        assert len(iteration.evaluations) == 9
        assert iteration.duration_ms > 0
        assert "候选 9" in iteration.summary

    def test_dry_run_no_real_register(self, context: ResearchContext) -> None:
        """dry_run=True 时, register 仅内存记录, 不影响生产."""
        skill = create_default_skill(AutoResearchConfig(dry_run=True))
        assert skill.config.dry_run is True
        iteration = skill.run_iteration(context)
        # promoted_factors 可能为空 (模拟数据不一定通过门禁), 但流程应正常
        assert isinstance(iteration.promoted_factors, list)

    def test_max_candidates_limit(self, skill: AutoResearchSkill, context: ResearchContext) -> None:
        iteration = skill.run_iteration(context, max_candidates=3)
        assert len(iteration.candidates_generated) == 3

    def test_monitor_and_retire(self, skill: AutoResearchSkill) -> None:
        """衰减监控 → 自动退役."""
        # 先注册一个因子
        cand = FactorCandidate(name="MOM_60D", category="Momentum", source="test")
        skill._registry.register(cand)
        assert "MOM_60D" in skill._registry.list_active()

        # ICIR < 0.2 → 退役
        retired = skill.monitor_and_retire(
            active_factors=["MOM_60D"],
            decay_signals={"MOM_60D": 0.15},
        )
        assert "MOM_60D" in retired
        assert "MOM_60D" not in skill._registry.list_active()

    def test_monitor_no_decay_signals(self, skill: AutoResearchSkill) -> None:
        """无 decay_signals 时跳过退役."""
        retired = skill.monitor_and_retire(["MOM_60D"], decay_signals=None)
        assert retired == []

    def test_monitor_not_in_active(self, skill: AutoResearchSkill) -> None:
        """decay_signals 中的因子不在 active_factors 时跳过."""
        retired = skill.monitor_and_retire(
            active_factors=["MOM_60D"],
            decay_signals={"UNKNOWN_FACTOR": 0.1},
        )
        assert retired == []

    def test_get_history(self, skill: AutoResearchSkill, context: ResearchContext) -> None:
        skill.run_iteration(context)
        history = skill.get_history()
        assert len(history) == 1
        assert history[0].iteration_id.startswith("iter_")

    def test_empty_gates_raises(self) -> None:
        with pytest.raises(ValueError, match="gates 不能为空"):
            AutoResearchSkill(
                generator=ExpressionFactorGenerator(),
                evaluator=StandardFactorEvaluator(),
                gates=[],
                registry=InMemoryFactorRegistry(),
            )


# ============================================================
# 5. InMemoryFactorRegistry
# ============================================================


class TestInMemoryFactorRegistry:
    def test_register_and_list(self) -> None:
        reg = InMemoryFactorRegistry()
        cand = FactorCandidate(name="F1", category="Momentum", source="test")
        assert reg.register(cand) is True
        assert "F1" in reg.list_active()
        assert reg.is_registered("F1") is True

    def test_retire(self) -> None:
        reg = InMemoryFactorRegistry()
        cand = FactorCandidate(name="F1", category="Momentum", source="test")
        reg.register(cand)
        assert reg.retire("F1", "ICIR 衰减") is True
        assert "F1" not in reg.list_active()
        assert reg.is_registered("F1") is False

    def test_retire_not_exist(self) -> None:
        reg = InMemoryFactorRegistry()
        assert reg.retire("UNKNOWN", "test") is False

    def test_register_after_retire_fails(self) -> None:
        reg = InMemoryFactorRegistry()
        cand = FactorCandidate(name="F1", category="Momentum", source="test")
        reg.register(cand)
        reg.retire("F1", "衰减")
        # 退役后不能重新注册
        assert reg.register(cand) is False


# ============================================================
# 6. GateStatus 属性
# ============================================================


class TestGateStatus:
    def test_passed_s1_to_s5(self) -> None:
        gs = GateStatus()
        gs.passed_stages = list(GateStage.ALL_STAGES[:5])
        assert gs.passed_s1_to_s5 is True

    def test_passed_s1_to_s5_false(self) -> None:
        gs = GateStatus()
        gs.passed_stages = [GateStage.S1_EFFECTIVE_IC]
        assert gs.passed_s1_to_s5 is False

    def test_fully_promoted(self) -> None:
        gs = GateStatus()
        gs.passed_stages = list(GateStage.ALL_STAGES)
        assert gs.fully_promoted is True

    def test_fully_promoted_false(self) -> None:
        gs = GateStatus()
        gs.passed_stages = list(GateStage.ALL_STAGES[:5])
        assert gs.fully_promoted is False


# ============================================================
# 7. FactorEvaluationResult 属性
# ============================================================


class TestFactorEvaluationResult:
    def test_passed_property(self) -> None:
        cand = FactorCandidate(name="F1", category="M", source="t")
        gs = GateStatus()
        gs.passed_stages = list(GateStage.ALL_STAGES[:5])
        result = FactorEvaluationResult(candidate=cand, gate_status=gs)
        assert result.passed is True

    def test_passed_false_on_error(self) -> None:
        cand = FactorCandidate(name="F1", category="M", source="t")
        gs = GateStatus()
        gs.passed_stages = list(GateStage.ALL_STAGES[:5])
        result = FactorEvaluationResult(candidate=cand, gate_status=gs, error_message="err")
        assert result.passed is False
