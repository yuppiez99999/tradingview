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


# ============================================================
# SC-16 回归 (2026-09-12): softmax 溢出防护 + 约束在归一化后仍成立
# ============================================================


class TestWeightConstraintsSc16:
    """SC-16 修复回归 — `_apply_weight_constraints`

    修复前两处缺陷（先红实证，见 docs/代码质量与系统Bug审查_20260912.md §SC-16）：
    ① `np.exp(score*10)` 无 max-shift → 大正分数溢出为 inf → softmax 输出 NaN；
    ② 先 clip 到 [min,max] 再归一化 → 归一化把刚压到上限的源重新抬超上限（单源 0.833>0.5）。
    """

    @staticmethod
    def _engine(tmp_path):
        from utils.enhanced_signal_fusion import WeightAdjustmentConfig

        return EnhancedSignalFusionEngine(
            db_path=os.path.join(str(tmp_path), "sc16.db"),
            config=WeightAdjustmentConfig(),
        )

    def test_softmax_no_overflow_on_large_scores(self, tmp_path):
        """缺陷①: 极大正分数不得产生 NaN/inf，权重仍为合法概率分布"""
        import math

        engine = self._engine(tmp_path)
        sources = ["s1", "s2", "s3"]
        scores = {"s1": 100.0, "s2": 1.0, "s3": 0.5}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)

        assert all(math.isfinite(v) for v in w.values()), f"权重含非有限值: {w}"
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert all(0.0 <= v <= 1.0 for v in w.values())

    def test_softmax_extreme_positive_score(self, tmp_path):
        """缺陷① 极端: score=1e3 亦不得溢出"""
        import math

        engine = self._engine(tmp_path)
        sources = ["a", "b"]
        scores = {"a": 1e3, "b": -1e3}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)
        assert all(math.isfinite(v) for v in w.values())
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_constraint_holds_after_normalization(self, tmp_path):
        """缺陷②: 单源主导时，输出必须落在 [min,max] 内（修复前 0.833 > 0.5）"""
        engine = self._engine(tmp_path)
        cfg = engine.config
        sources = ["s1", "s2", "s3"]
        scores = {"s1": 5.0, "s2": 0.0, "s3": 0.0}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)

        assert abs(sum(w.values()) - 1.0) < 1e-9, f"总和不归一: {sum(w.values())}"
        for s in sources:
            assert w[s] <= cfg.max_weight_per_source + 1e-9, f"{s} 越上限: {w[s]}"
            assert w[s] >= cfg.min_weight_per_source - 1e-9, f"{s} 越下限: {w[s]}"

    def test_water_filling_proportional(self, tmp_path):
        """单源主导 + 3 源（上限 0.5/下限 0.05）→ water-filling 应给 0.5/0.25/0.25"""
        engine = self._engine(tmp_path)
        sources = ["dom", "o1", "o2"]
        scores = {"dom": 5.0, "o1": 0.0, "o2": 0.0}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)
        assert abs(w["dom"] - 0.5) < 1e-9
        assert abs(w["o1"] - 0.25) < 1e-9
        assert abs(w["o2"] - 0.25) < 1e-9

    def test_lower_bound_applied(self, tmp_path):
        """低分源应被抬到下限，且不破坏总和=1/上限约束"""
        engine = self._engine(tmp_path)
        cfg = engine.config
        sources = ["a", "b", "c", "d"]
        scores = {"a": 3.0, "b": 3.0, "c": -5.0, "d": -5.0}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        for s in sources:
            assert cfg.min_weight_per_source - 1e-9 <= w[s] <= cfg.max_weight_per_source + 1e-9

    def test_uniform_when_constraints_infeasible(self, tmp_path):
        """约束不可行（n*min>1）→ fail-safe 退化等权，不产出违规权重"""
        from utils.enhanced_signal_fusion import WeightAdjustmentConfig

        engine = EnhancedSignalFusionEngine(
            db_path=os.path.join(str(tmp_path), "inf.db"),
            config=WeightAdjustmentConfig(min_weight_per_source=0.5, max_weight_per_source=0.9),
        )
        sources = ["a", "b", "c"]  # 3 * 0.5 = 1.5 > 1 不可行
        assert engine._apply_weight_constraints(
            {s: 1.0 for s in sources}, {s: 0.0 for s in sources}, sources
        ) == {s: 1.0 / 3 for s in sources}

    def test_nan_score_sanitized(self, tmp_path):
        """NaN 分数不得污染输出权重"""
        import math

        engine = self._engine(tmp_path)
        sources = ["a", "b", "c"]
        scores = {"a": float("nan"), "b": 1.0, "c": 0.5}
        penalty = {s: 0.0 for s in sources}

        w = engine._apply_weight_constraints(scores, penalty, sources)
        assert all(math.isfinite(v) for v in w.values())
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_empty_sources(self, tmp_path):
        engine = self._engine(tmp_path)
        assert engine._apply_weight_constraints({}, {}, []) == {}
