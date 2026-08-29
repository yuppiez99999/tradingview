"""enhanced_signal_fusion 单元测试 — 增强信号融合引擎

覆盖:
- SourcePerformanceMetrics dataclass
- WeightAdjustmentConfig dataclass
- EnhancedSignalFusionEngine: 初始化/注册/权重计算 (基础)
"""

from __future__ import annotations

import os
import tempfile

from utils.enhanced_signal_fusion import (
    EnhancedSignalFusionEngine,
    SourcePerformanceMetrics,
    WeightAdjustmentConfig,
)

# ============================================================
# SourcePerformanceMetrics
# ============================================================


class TestSourcePerformanceMetrics:
    """SourcePerformanceMetrics dataclass 测试"""

    def test_init_defaults(self):
        m = SourcePerformanceMetrics(source_name="test")
        assert m.source_name == "test"
        assert m.total_signals == 0
        assert m.correct_predictions == 0
        assert m.recent_accuracy == 0.0
        assert m.volatility == 0.0
        assert m.performance_history == []
        assert m.consecutive_losses == 0
        assert m.consecutive_wins == 0

    def test_init_custom(self):
        m = SourcePerformanceMetrics(
            source_name="alpha",
            total_signals=100,
            correct_predictions=60,
            recent_accuracy=0.6,
            volatility=0.15,
        )
        assert m.source_name == "alpha"
        assert m.total_signals == 100
        assert m.recent_accuracy == 0.6


# ============================================================
# WeightAdjustmentConfig
# ============================================================


class TestWeightAdjustmentConfig:
    """WeightAdjustmentConfig dataclass 测试"""

    def test_init_defaults(self):
        c = WeightAdjustmentConfig()
        assert c.lookback_days == 30
        assert c.min_samples == 5
        assert c.max_weight_per_source == 0.5
        assert c.min_weight_per_source == 0.05
        assert c.adaptive_learning_rate == 0.1

    def test_init_custom(self):
        c = WeightAdjustmentConfig(lookback_days=60, max_weight_per_source=0.3)
        assert c.lookback_days == 60
        assert c.max_weight_per_source == 0.3


# ============================================================
# EnhancedSignalFusionEngine
# ============================================================


class TestEnhancedSignalFusionEngine:
    """EnhancedSignalFusionEngine 引擎测试"""

    def test_init_with_temp_db(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            assert engine.config is not None
            assert engine._performance_metrics == {}

    def test_init_with_config(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            config = WeightAdjustmentConfig(lookback_days=60)
            engine = EnhancedSignalFusionEngine(db_path=db_path, config=config)
            assert engine.config.lookback_days == 60

    def test_register_enhanced_source(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            engine.register_enhanced_source(
                "test_source", lambda: None, initial_weight=0.5
            )
            assert "test_source" in engine._performance_metrics
            assert (
                engine._performance_metrics["test_source"].source_name == "test_source"
            )

    def test_compute_multi_dimensional_scores_empty(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            scores = engine._compute_multi_dimensional_scores([])
            assert scores == {}

    def test_compute_multi_dimensional_scores_default(self):
        """无性能指标 → 默认 0.5"""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            scores = engine._compute_multi_dimensional_scores(["source_a"])
            assert scores["source_a"] == 0.5

    def test_compute_correlation_penalty_empty(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            penalty = engine._compute_correlation_penalty([])
            assert penalty == {}

    def test_get_source_performance_score_no_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            assert engine._get_source_performance_score("unknown") == 0.5

    def test_get_source_performance_score_with_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "test.db")
            engine = EnhancedSignalFusionEngine(db_path=db_path)
            engine._performance_metrics["x"] = SourcePerformanceMetrics(
                source_name="x", recent_accuracy=0.8
            )
            assert engine._get_source_performance_score("x") == 0.8
