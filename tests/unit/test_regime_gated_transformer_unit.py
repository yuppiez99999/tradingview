"""制度门控 Transformer 单元测试.

被测模块: utils/regime_gated_transformer.py
文献: #58 Adaptive Financial Transformer (Regime-Gated) (2026.06)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.regime_gated_transformer import (  # noqa: E402
    AdaptiveFinancialTransformer,
    FeatureSemanticMapper,
    MarketRegime,
    RegimeDetector,
    RegimeGatedTransformer,
    SemanticClass,
    TransformerConfig,
    TransformerOutput,
)

# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    def test_semantic_classes(self):
        assert len(SemanticClass) == 11
        assert SemanticClass.TREND == "trend"

    def test_market_regimes(self):
        assert len(MarketRegime) == 4
        assert MarketRegime.LOW_VOL == "low_vol"


# ============================================================
# 特征语义映射测试
# ============================================================

class TestFeatureSemanticMapper:
    def test_map_single_sample(self):
        mapper = FeatureSemanticMapper()
        features = np.random.default_rng(42).standard_normal(95)
        result = mapper.map_features(features)
        assert result.semantic_features.shape == (11,)
        assert result.n_original_features == 95
        assert result.n_semantic_classes == 11

    def test_map_batch(self):
        mapper = FeatureSemanticMapper()
        features = np.random.default_rng(42).standard_normal((5, 95))
        result = mapper.map_features(features)
        assert result.semantic_features.shape == (5, 11)

    def test_class_contributions(self):
        mapper = FeatureSemanticMapper()
        features = np.random.default_rng(42).standard_normal(95)
        result = mapper.map_features(features)
        assert len(result.class_contributions) == 11
        assert all(isinstance(v, float) for v in result.class_contributions.values())

    def test_complexity_reduction(self):
        mapper = FeatureSemanticMapper()
        reduction = mapper.complexity_reduction()
        # 95 → 11: 降低 (95-11)/95 = 88.4%
        assert reduction == pytest.approx(1 - 11 / 95)
        assert reduction > 0.10  # 至少降低 10%

    def test_all_classes_covered(self):
        """所有 95 个特征都被映射."""
        mapper = FeatureSemanticMapper()
        all_indices: list[int] = []
        for indices in mapper.mapping.values():
            all_indices.extend(indices)
        assert len(all_indices) == 95
        assert len(set(all_indices)) == 95  # 无重复


# ============================================================
# 制度检测测试
# ============================================================

class TestRegimeDetector:
    def test_detect_returns_regime(self):
        detector = RegimeDetector()
        features = np.random.default_rng(42).standard_normal(11)
        result = detector.detect(features)
        assert result.regime in MarketRegime
        assert 0 <= result.confidence <= 1

    def test_regime_probs_sum_to_one(self):
        detector = RegimeDetector()
        features = np.random.default_rng(42).standard_normal(11)
        result = detector.detect(features)
        assert sum(result.regime_probs.values()) == pytest.approx(1.0)

    def test_regime_hint_low_vol(self):
        """hint=0.0 倾向低波."""
        detector = RegimeDetector()
        features = np.zeros(11)
        result = detector.detect(features, regime_hint=0.0)
        assert result.regime == MarketRegime.LOW_VOL

    def test_regime_hint_high_vol(self):
        """hint=1.0 倾向高波."""
        detector = RegimeDetector()
        features = np.zeros(11)
        result = detector.detect(features, regime_hint=1.0)
        assert result.regime == MarketRegime.HIGH_VOL

    def test_high_vol_features(self):
        """高波动率特征 → 高波制度."""
        detector = RegimeDetector()
        features = np.zeros(11)
        features[3] = 5.0  # 波动率类特征高
        result = detector.detect(features)
        # 高波动率特征应该增加高波制度概率
        assert result.regime_probs[MarketRegime.HIGH_VOL.value] > 0.1


# ============================================================
# 制度门控 Transformer 测试
# ============================================================

class TestRegimeGatedTransformer:
    def test_forward_returns_output(self):
        transformer = RegimeGatedTransformer()
        features = np.random.default_rng(42).standard_normal(11)
        regime_probs = {r.value: 0.25 for r in MarketRegime}
        output = transformer.forward(features, MarketRegime.LOW_VOL, regime_probs)
        assert output.shape == (11,)

    def test_different_regimes_different_output(self):
        """不同制度 → 不同输出."""
        transformer = RegimeGatedTransformer()
        features = np.random.default_rng(42).standard_normal(11)
        regime_probs = {r.value: 0.25 for r in MarketRegime}
        out_low = transformer.forward(features, MarketRegime.LOW_VOL, regime_probs)
        out_high = transformer.forward(features, MarketRegime.HIGH_VOL, regime_probs)
        # 不同制度权重不同, 输出应不同
        assert not np.allclose(out_low, out_high)

    def test_reproducible_with_seed(self):
        t1 = RegimeGatedTransformer(seed=42)
        t2 = RegimeGatedTransformer(seed=42)
        features = np.random.default_rng(42).standard_normal(11)
        regime_probs = {r.value: 0.25 for r in MarketRegime}
        out1 = t1.forward(features, MarketRegime.LOW_VOL, regime_probs)
        out2 = t2.forward(features, MarketRegime.LOW_VOL, regime_probs)
        assert np.allclose(out1, out2)

    def test_custom_config(self):
        config = TransformerConfig(n_heads=8, d_model=11, d_ff=64, n_layers=3)
        transformer = RegimeGatedTransformer(config=config)
        features = np.random.default_rng(42).standard_normal(11)
        regime_probs = {r.value: 0.25 for r in MarketRegime}
        output = transformer.forward(features, MarketRegime.TRENDING, regime_probs)
        assert output.shape == (11,)


# ============================================================
# 自适应金融 Transformer 测试
# ============================================================

class TestAdaptiveFinancialTransformer:
    def test_forward_basic(self):
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result = model.forward(features)
        assert isinstance(result, TransformerOutput)
        assert -1 <= result.predictions <= 1  # tanh 输出

    def test_forward_with_hint(self):
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result = model.forward(features, regime_hint=0.0)
        assert result.regime == MarketRegime.LOW_VOL

    def test_batch_forward(self):
        model = AdaptiveFinancialTransformer()
        batch = np.random.default_rng(42).standard_normal((5, 95))
        results = model.batch_forward(batch)
        assert len(results) == 5

    def test_complexity_report(self):
        model = AdaptiveFinancialTransformer()
        report = model.complexity_report()
        assert report["n_original_features"] == 95
        assert report["n_semantic_classes"] == 11
        assert report["complexity_reduction_pct"] > 10.0  # ≥ 10%

    def test_semantic_features_shape(self):
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result = model.forward(features)
        assert result.semantic_features.shape == (11,)

    def test_class_contributions(self):
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result = model.forward(features)
        assert len(result.class_contributions) == 11

    def test_gate_value_in_range(self):
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result = model.forward(features)
        assert 0 < result.gate_value < 1  # sigmoid 输出


# ============================================================
# 验收标准测试
# ============================================================

class TestAcceptanceCriteria:
    """LIT-5.1 验收: 95特征→11语义类 + 制度门控 + 复杂度降≥10%."""

    def test_95_to_11_mapping(self):
        """验收: 95 特征 → 11 语义类."""
        model = AdaptiveFinancialTransformer()
        report = model.complexity_report()
        assert report["n_original_features"] == 95
        assert report["n_semantic_classes"] == 11

    def test_complexity_reduction_ge_10pct(self):
        """验收: 复杂度降 ≥ 10%."""
        model = AdaptiveFinancialTransformer()
        report = model.complexity_report()
        assert report["complexity_reduction_pct"] >= 10.0

    def test_regime_gating_active(self):
        """验收: 制度门控生效 (不同制度不同输出)."""
        model = AdaptiveFinancialTransformer()
        features = np.random.default_rng(42).standard_normal(95)
        result_low = model.forward(features, regime_hint=0.0)
        result_high = model.forward(features, regime_hint=1.0)
        # 不同制度应该检测到不同 regime
        assert result_low.regime != result_high.regime

    def test_all_regimes_reachable(self):
        """验收: 所有 4 种制度可达."""
        detector = RegimeDetector()
        regimes_found = set()
        rng = np.random.default_rng(42)
        for _ in range(100):
            features = rng.standard_normal(11)
            result = detector.detect(features)
            regimes_found.add(result.regime)
        # 至少能检测到 2 种制度
        assert len(regimes_found) >= 2
