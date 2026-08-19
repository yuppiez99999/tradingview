"""D2 单元测试 — HypothesisVerifier 假设验证框架."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from utils.llm_evolution.hypothesis_verifier import (
    HypothesisVerdict,
    HypothesisVerifier,
    VerificationThresholds,
)

# ============================================================
# 测试夹具
# ============================================================

def _make_verifier(thresholds: VerificationThresholds | None = None) -> HypothesisVerifier:
    return HypothesisVerifier(audit_logger=MagicMock(), thresholds=thresholds)


def _good_factor_data(n: int = 100) -> dict[str, Any]:
    """IC 显著的因子数据."""
    import random
    random.seed(42)
    ic_series = [0.04 + random.gauss(0, 0.01) for _ in range(n)]
    return {"ic_series": ic_series, "max_drawdown": 0.08, "wf_mean_ic": 0.038, "dsr_score": 1.5}

def _bad_factor_data(n: int = 100) -> dict[str, Any]:
    """IC 不显著的因子数据."""
    import random
    random.seed(42)
    ic_series = [random.gauss(0, 0.02) for _ in range(n)]
    return {"ic_series": ic_series, "max_drawdown": 0.20, "wf_mean_ic": 0.001, "dsr_score": 0.5}


# ============================================================
# HypothesisVerdict 属性
# ============================================================

class TestHypothesisVerdict:
    def test_pass_all_true(self):
        v = HypothesisVerdict(
            ic_significant=True, purged_kfold_pass=True, cro_gate_pass=True,
            honest_validation_pass=True,
        )
        assert v.pass_all

    def test_pass_all_false(self):
        v = HypothesisVerdict(ic_significant=True, purged_kfold_pass=False, cro_gate_pass=True)
        assert not v.pass_all

    def test_summary_text(self):
        v = HypothesisVerdict(factor_name="EP", rank_ic_mean=0.05, icir=0.8,
                              ic_positive_ratio=0.65, cv_score=0.3, enter_ab_bucket=True)
        text = v.summary_text()
        assert "EP" in text
        assert "YES" in text


# ============================================================
# 验证逻辑
# ============================================================

class TestVerify:
    def test_good_factor_enters_ab_bucket(self):
        verifier = _make_verifier()
        candidate = {"name": "EP", "category": "Value", "formula": "1/PE"}
        result = verifier.verify(candidate, _good_factor_data())
        assert result["enter_ab_bucket"] is True
        assert result["falsified"] is False
        assert result["ic_significant"] is True
        assert result["purged_kfold_pass"] is True
        assert result["cro_gate_pass"] is True

    def test_bad_factor_falsified(self):
        verifier = _make_verifier()
        candidate = {"name": "BAD", "category": "Momentum"}
        result = verifier.verify(candidate, _bad_factor_data())
        assert result["falsified"] is True
        assert result["enter_ab_bucket"] is False
        assert "IC 不显著" in result["falsified_reason"]

    def test_insufficient_samples(self):
        verifier = _make_verifier()
        candidate = {"name": "EP"}
        factor_data = {"ic_series": [0.05] * 10}  # 只有 10 个样本
        result = verifier.verify(candidate, factor_data)
        assert result["falsified"] is True
        assert "样本不足" in result["falsified_reason"]

    def test_no_factor_data(self):
        """无因子数据时 IC=0, 被证伪."""
        verifier = _make_verifier()
        candidate = {"name": "EP"}
        result = verifier.verify(candidate, factor_data=None)
        assert result["falsified"] is True

    def test_high_drawdown_fails_cro_gate(self):
        verifier = _make_verifier()
        data = _good_factor_data()
        data["max_drawdown"] = 0.25  # 25% 回撤 > 15%
        result = verifier.verify({"name": "EP"}, data)
        assert result["cro_gate_pass"] is False
        assert "CRO Gate" in result["falsified_reason"]

    def test_low_dsr_fails_honest_validation(self):
        verifier = _make_verifier()
        data = _good_factor_data()
        data["dsr_score"] = 0.8  # DSR < 1.0
        result = verifier.verify({"name": "EP"}, data)
        assert result["honest_validation_pass"] is False
        assert "Honest Validation" in result["falsified_reason"]

    def test_noise_unstable_fails(self):
        verifier = _make_verifier()
        data = _good_factor_data()
        data["noise_stable"] = False
        result = verifier.verify({"name": "EP"}, data)
        assert result["honest_validation_pass"] is False


# ============================================================
# 自定义阈值
# ============================================================

class TestCustomThresholds:
    def test_strict_thresholds(self):
        strict = VerificationThresholds(min_rank_ic=0.08, min_icir=1.0)
        verifier = _make_verifier(thresholds=strict)
        result = verifier.verify({"name": "EP"}, _good_factor_data())
        # 0.04 IC < 0.08 strict
        assert result["falsified"] is True

    def test_lenient_thresholds(self):
        lenient = VerificationThresholds(min_rank_ic=0.01, min_icir=0.1,
                                          min_ic_positive_ratio=0.50, min_effect_size=0.1)
        verifier = _make_verifier(thresholds=lenient)
        result = verifier.verify({"name": "EP"}, _good_factor_data())
        assert result["enter_ab_bucket"] is True


# ============================================================
# 批量验证
# ============================================================

class TestBatchVerify:
    def test_batch_mixed(self):
        verifier = _make_verifier()
        candidates = [
            {"name": "GOOD", "category": "Value"},
            {"name": "BAD", "category": "Momentum"},
        ]
        factor_map = {
            "GOOD": _good_factor_data(),
            "BAD": _bad_factor_data(),
        }
        results = verifier.verify_batch(candidates, factor_map)
        assert len(results) == 2
        assert results[0]["enter_ab_bucket"] is True
        assert results[1]["enter_ab_bucket"] is False

    def test_batch_empty(self):
        verifier = _make_verifier()
        results = verifier.verify_batch([])
        assert len(results) == 0


# ============================================================
# 统计工具
# ============================================================

class TestStatsTools:
    def test_safe_mean_empty(self):
        v = _make_verifier()
        assert v._safe_mean([]) == 0.0

    def test_safe_std_single(self):
        v = _make_verifier()
        assert v._safe_std([1.0]) == 0.0

    def test_cohen_d_zero_std(self):
        v = _make_verifier()
        assert v._cohen_d([1.0, 1.0, 1.0]) == 0.0
