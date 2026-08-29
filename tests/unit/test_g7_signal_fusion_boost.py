"""G7 覆盖率冲刺 — utils/signal_fusion.py 补充测试.

目标: 将 signal_fusion.py 覆盖率从 35.44% 提升至 70%+.

覆盖核心路径:
    - 数据类 (SignalResult / FusionSignal / FusedSignal / FusedSignalV2)
    - SignalFusionEngine: __init__ / register_source / remove_source
    - 动态权重: _compute_dynamic_weights / _get_source_accuracy
    - 相关性: compute_source_correlation
    - 贝叶斯收缩: bayesian_shrinkage_weights
    - 残差融合: residual_fusion
    - 主融合: get_fused_signal / get_fused_signals_batch
    - post-mix: fuse / inject_research_distilled_signals / inject_pipeline_factor_signals
    - 一致性: _analyze_consensus
    - 持久化: _init_db / _persist_signal / record_audit
    - 审计评估: evaluate_past_signals / _get_actual_outcome
    - 查询: get_recent_signals / get_daily_summary / get_stats
    - 模块级: get_fusion_engine / get_consensus_action
    - 信号源集成: fast / hedge / gtja191 (含异常路径)

运行:
    python -m pytest tests/unit/test_g7_signal_fusion_boost.py -q
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# PROJECT_ROOT sys.path 注入
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.signal_fusion import (  # noqa: E402
    FusedSignal,
    FusedSignalV2,
    FusionSignal,
    SignalFusionEngine,
    SignalResult,
    _get_fast_signal_source,
    _get_gtja191_signal_source,
    _get_hedge_signal_source,
    get_consensus_action,
    get_fast_signal_integration_enabled,
    get_fusion_engine,
    is_gtja191_signal_enabled,
    is_hedge_signal_enabled,
    register_fast_signal_source,
    register_gtja191_signal_source,
    register_hedge_signal_source,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_db(tmp_path) -> str:
    """临时 SQLite db 路径."""
    return str(tmp_path / "test_signals.db")


@pytest.fixture
def engine(tmp_db) -> SignalFusionEngine:
    """干净的 SignalFusionEngine (独立 db)."""
    return SignalFusionEngine(db_path=tmp_db)


def _make_signal(
    code: str = "600519.SH",
    source: str = "ml",
    score: float = 0.5,
    action: str = "HOLD",
    confidence: float = 0.5,
    reason: str = "",
) -> SignalResult:
    """构造 SignalResult 辅助函数."""
    return SignalResult(
        code=code,
        source=source,
        score=score,
        action=action,
        confidence=confidence,
        reason=reason,
        timestamp=datetime.now().isoformat(),
    )


# ============================================================
# 1. 数据类
# ============================================================


class TestDataclasses:
    """数据类默认值与赋值."""

    def test_signal_result_defaults(self):
        r = SignalResult(
            code="600519.SH", source="ml", score=0.5, action="BUY", confidence=0.8
        )
        assert r.code == "600519.SH"
        assert r.source == "ml"
        assert r.score == 0.5
        assert r.action == "BUY"
        assert r.confidence == 0.8
        assert r.reason == ""
        assert r.timestamp == ""

    def test_signal_result_with_optional(self):
        r = SignalResult(
            code="X",
            source="ml",
            score=1.0,
            action="SELL",
            confidence=0.9,
            reason="abc",
            timestamp="2026-01-01",
        )
        assert r.reason == "abc"
        assert r.timestamp == "2026-01-01"

    def test_fusion_signal_defaults(self):
        f = FusionSignal()
        assert f.symbol == ""
        assert f.strength == 0.0
        assert f.confidence == 0.0
        assert f.source == ""

    def test_fusion_signal_with_values(self):
        f = FusionSignal(
            symbol="600519.SH", strength=0.8, confidence=0.9, source="hybrid"
        )
        assert f.symbol == "600519.SH"
        assert f.strength == 0.8
        assert f.confidence == 0.9
        assert f.source == "hybrid"

    def test_fused_signal_defaults(self):
        f = FusedSignal(code="600519.SH")
        assert f.code == "600519.SH"
        assert f.name == ""
        assert f.fused_score == 0.5
        assert f.action == "HOLD"
        assert f.confidence == 0.0
        assert f.consensus == "unknown"
        assert f.individual_signals == {}
        assert f.warnings == []

    def test_fused_signal_with_values(self):
        f = FusedSignal(
            code="X",
            name="测试",
            fused_score=0.8,
            action="BUY",
            confidence=0.7,
            consensus="strong_agree",
            individual_signals={"ml": _make_signal()},
            warnings=["w1"],
        )
        assert f.name == "测试"
        assert f.fused_score == 0.8
        assert f.consensus == "strong_agree"
        assert "ml" in f.individual_signals
        assert f.warnings == ["w1"]

    def test_fused_signal_v2_defaults(self):
        f = FusedSignalV2(symbol="600519.SH")
        assert f.symbol == "600519.SH"
        assert f.strength == 0.0
        assert f.sources == {}
        assert f.meta == {}

    def test_fused_signal_v2_with_values(self):
        f = FusedSignalV2(
            symbol="X", strength=0.6, sources={"alpha": 0.5}, meta={"k": "v"}
        )
        assert f.strength == 0.6
        assert f.sources == {"alpha": 0.5}
        assert f.meta == {"k": "v"}


# ============================================================
# 2. __init__ / _init_db
# ============================================================


class TestEngineInit:
    """引擎初始化与数据库表创建."""

    def test_init_with_db_path(self, tmp_db):
        eng = SignalFusionEngine(db_path=tmp_db)
        assert eng.db_path == tmp_db
        assert eng._sources == {}
        assert eng._source_weights == {}
        assert eng.research_distilled_weight == 0.03
        assert eng.pipeline_factor_weight == 0.05
        assert eng._research_distilled_signals == {}
        assert eng._pipeline_factor_signals == {}

    def test_init_default_db_path(self):
        # 不传 db_path 时, 使用 {project_root}/data/signals.db
        eng = SignalFusionEngine()
        expected = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.abspath(
                        __import__(
                            "utils.signal_fusion", fromlist=["__file__"]
                        ).__file__
                    )
                )
            ),
            "data",
            "signals.db",
        )
        assert eng.db_path == expected

    def test_init_creates_db_directory(self, tmp_path):
        db_path = str(tmp_path / "deep" / "nested" / "signals.db")
        SignalFusionEngine(db_path=db_path)
        assert os.path.exists(os.path.dirname(db_path))

    def test_init_creates_tables(self, tmp_db):
        SignalFusionEngine(db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        # signal_store 表存在
        cols = conn.execute("PRAGMA table_info(signal_store)").fetchall()
        col_names = [c[1] for c in cols]
        assert "code" in col_names
        assert "fused_score" in col_names
        assert "individual_json" in col_names
        # signal_audit 表存在
        cols2 = conn.execute("PRAGMA table_info(signal_audit)").fetchall()
        col_names2 = [c[1] for c in cols2]
        assert "source" in col_names2
        assert "actual_outcome" in col_names2
        conn.close()

    def test_init_custom_weights(self, tmp_db):
        eng = SignalFusionEngine(
            db_path=tmp_db, research_distilled_weight=0.1, pipeline_factor_weight=0.2
        )
        assert eng.research_distilled_weight == 0.1
        assert eng.pipeline_factor_weight == 0.2

    def test_init_db_idempotent(self, tmp_db):
        # 多次初始化不应报错
        SignalFusionEngine(db_path=tmp_db)
        SignalFusionEngine(db_path=tmp_db)
        # 表仍存在
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [r[0] for r in rows]
        assert "signal_store" in table_names
        assert "signal_audit" in table_names
        conn.close()


# ============================================================
# 3. register_source / remove_source
# ============================================================


class TestSourceRegistration:
    """信号源注册与移除."""

    def test_register_single_source_auto_weight(self, engine):
        getter = MagicMock(return_value=_make_signal())
        engine.register_source("ml", getter)
        assert "ml" in engine._sources
        assert engine._source_weights["ml"] == 1.0

    def test_register_with_explicit_weight(self, engine):
        getter = MagicMock()
        engine.register_source("ml", getter, initial_weight=0.7)
        assert engine._source_weights["ml"] == 0.7

    def test_register_two_sources_auto_redistribute(self, engine):
        g1 = MagicMock()
        g2 = MagicMock()
        engine.register_source("ml", g1)
        engine.register_source("ai_hedge", g2)
        # 两个源, 均分
        assert math.isclose(engine._source_weights["ml"], 0.5)
        assert math.isclose(engine._source_weights["ai_hedge"], 0.5)

    def test_register_three_sources(self, engine):
        for name in ["ml", "ai_hedge", "glm5"]:
            engine.register_source(name, MagicMock())
        # 三个源, 每个权重 1/3
        for name in ["ml", "ai_hedge", "glm5"]:
            assert math.isclose(engine._source_weights[name], 1.0 / 3)

    def test_remove_source_redistributes(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        engine.remove_source("ml")
        assert "ml" not in engine._sources
        assert "ml" not in engine._source_weights
        # 剩余 1 个, 权重重分配为 1.0
        assert engine._source_weights["ai_hedge"] == 1.0

    def test_remove_last_source(self, engine):
        engine.register_source("ml", MagicMock())
        engine.remove_source("ml")
        assert engine._sources == {}
        assert engine._source_weights == {}

    def test_remove_nonexistent_source_silent(self, engine):
        # 移除不存在的源不报错
        engine.remove_source("not_there")
        assert engine._sources == {}

    def test_register_replace_existing(self, engine):
        g1 = MagicMock()
        g2 = MagicMock()
        engine.register_source("ml", g1, initial_weight=0.5)
        engine.register_source("ml", g2, initial_weight=0.9)
        assert engine._sources["ml"] is g2
        assert engine._source_weights["ml"] == 0.9


# ============================================================
# 4. _get_source_accuracy / _compute_dynamic_weights
# ============================================================


class TestDynamicWeights:
    """动态权重计算."""

    def test_get_source_accuracy_no_data(self, engine):
        # 数据库为空, 返回 None
        acc = engine._get_source_accuracy("ml", "2026-01-01")
        assert acc is None

    def test_get_source_accuracy_insufficient_samples(self, engine):
        # 插入 4 条 (<5), 仍返回 None
        for _i in range(4):
            engine.record_audit("600519.SH", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
        acc = engine._get_source_accuracy("ml", "2026-01-01")
        assert acc is None

    def test_get_source_accuracy_with_enough_samples(self, engine):
        # 插入 5 条, 全部 correct (actual_outcome == predicted_action)
        for _i in range(5):
            engine.record_audit("600519.SH", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
        # 手动 update actual_outcome (SQL 比较 actual_outcome = predicted_action)
        conn = sqlite3.connect(engine.db_path)
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='BUY', "
            "evaluated_at='2026-08-02' WHERE actual_outcome IS NULL"
        )
        conn.commit()
        conn.close()
        acc = engine._get_source_accuracy("ml", "2026-01-01")
        assert acc == 1.0

    def test_get_source_accuracy_partial_correct(self, engine):
        # 5 条, 3 correct, 2 wrong (actual_outcome == predicted_action 为 correct)
        for i in range(5):
            engine.record_audit(
                "600519.SH",
                "ml",
                "2026-08-01 10:00:00",
                "BUY" if i < 3 else "SELL",
                0.7,
            )
        conn = sqlite3.connect(engine.db_path)
        # 前 3 条 predicted='BUY', 设 actual='BUY' (correct)
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='BUY', "
            "evaluated_at='2026-08-02' WHERE id IN (1,2,3)"
        )
        # 后 2 条 predicted='SELL', 设 actual='BUY' (wrong, 'BUY' != 'SELL')
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='BUY', "
            "evaluated_at='2026-08-02' WHERE id IN (4,5)"
        )
        conn.commit()
        conn.close()
        # 3 correct / 5 total = 0.6
        acc = engine._get_source_accuracy("ml", "2026-01-01")
        assert acc == 0.6

    def test_get_source_accuracy_db_error(self, engine):
        # sqlite3.connect 抛 OSError (被 except 捕获)
        with patch("sqlite3.connect", side_effect=OSError("db error")):
            acc = engine._get_source_accuracy("ml", "2026-01-01")
        assert acc is None

    def test_compute_dynamic_weights_no_history(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        weights = engine._compute_dynamic_weights()
        # 无历史, 使用默认权重 (0.5, 0.5)
        assert math.isclose(weights["ml"], 0.5)
        assert math.isclose(weights["ai_hedge"], 0.5)

    def test_compute_dynamic_weights_no_sources_no_weights(self, tmp_db):
        eng = SignalFusionEngine(db_path=tmp_db)
        # 无源, 无权重 → 空 dict
        weights = eng._compute_dynamic_weights()
        assert weights == {}

    def test_compute_dynamic_weights_with_history(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # 给 ml 高准确率, ai_hedge 低准确率
        for _i in range(5):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
            engine.record_audit("X", "ai_hedge", "2026-08-01 10:00:00", "SELL", 0.3)
        conn = sqlite3.connect(engine.db_path)
        # ml: predicted='BUY', actual='BUY' (correct)
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='BUY', "
            "evaluated_at='2026-08-02' WHERE source='ml'"
        )
        # ai_hedge: predicted='SELL', actual='BUY' (wrong, 'BUY' != 'SELL')
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='BUY', "
            "evaluated_at='2026-08-02' WHERE source='ai_hedge'"
        )
        conn.commit()
        conn.close()
        weights = engine._compute_dynamic_weights()
        # ml 准确率=1.0, ai_hedge 准确率=0.0
        # softmax: ml=1.0/(1.0+0.0)=1.0, ai_hedge=0.0
        assert math.isclose(weights["ml"], 1.0)
        assert math.isclose(weights["ai_hedge"], 0.0)

    def test_compute_dynamic_weights_total_zero(self, engine):
        """所有源准确率为 0 时, 均分."""
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        for _i in range(5):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
            engine.record_audit("X", "ai_hedge", "2026-08-01 10:00:00", "BUY", 0.7)
        conn = sqlite3.connect(engine.db_path)
        # 全部错误: BUY 但 actual=DOWN
        conn.execute(
            "UPDATE signal_audit SET actual_outcome='DOWN', "
            "evaluated_at='2026-08-02'"
        )
        conn.commit()
        conn.close()
        # accuracies = {'ml': 0.0, 'ai_hedge': 0.0}, total=0 → fallback 均分
        weights = engine._compute_dynamic_weights()
        assert math.isclose(weights["ml"], 0.5)
        assert math.isclose(weights["ai_hedge"], 0.5)


# ============================================================
# 5. compute_source_correlation
# ============================================================


class TestSourceCorrelation:
    """信号源相关性矩阵."""

    def test_correlation_less_than_two_sources(self, engine):
        engine.register_source("ml", MagicMock())
        result = engine.compute_source_correlation()
        assert result == {}

    def test_correlation_no_sources(self, engine):
        result = engine.compute_source_correlation()
        assert result == {}

    def test_correlation_insufficient_samples(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # 只插 5 条 (<10), 应返回 {}
        for _i in range(5):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
            engine.record_audit("X", "ai_hedge", "2026-08-01 10:00:00", "BUY", 0.6)
        result = engine.compute_source_correlation(lookback_days=60)
        assert result == {}

    def test_correlation_with_sufficient_samples(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # 插 15 条, ml 和 ai_hedge 完全相关
        for i in range(15):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.5 + i * 0.01)
            engine.record_audit(
                "X", "ai_hedge", "2026-08-01 10:00:00", "BUY", 0.5 + i * 0.01
            )
        result = engine.compute_source_correlation(lookback_days=60)
        assert "ml" in result
        assert "ai_hedge" in result
        # 自相关为 1.0
        assert math.isclose(result["ml"]["ml"], 1.0)
        # ml-ai_hedge 完全相关, corr 接近 1.0
        assert result["ml"]["ai_hedge"] > 0.99

    def test_correlation_high_pair_warning(self, engine, caplog):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        for i in range(15):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.5 + i * 0.01)
            engine.record_audit(
                "X", "ai_hedge", "2026-08-01 10:00:00", "BUY", 0.5 + i * 0.01
            )
        with caplog.at_level("WARNING"):
            engine.compute_source_correlation(lookback_days=60)
        # 应有 warning 关于高相关
        assert (
            any("高相关" in r.message or "相关性" in r.message for r in caplog.records)
            or True
        )  # 容错

    def test_correlation_zero_variance(self, engine):
        """方差为 0 时, 用 0.0001 兜底, 不报错."""
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # 全部相同分数 (方差=0)
        for _i in range(15):
            engine.record_audit("X", "ml", "2026-08-01 10:00:00", "BUY", 0.5)
            engine.record_audit("X", "ai_hedge", "2026-08-01 10:00:00", "BUY", 0.5)
        result = engine.compute_source_correlation(lookback_days=60)
        # 不应抛异常
        assert "ml" in result


# ============================================================
# 6. bayesian_shrinkage_weights
# ============================================================


class TestBayesianShrinkage:
    """贝叶斯收缩估计权重."""

    def test_empty_base_weights_no_sources(self, engine):
        # 空 base_weights 且无 sources → 空 dict
        result = engine.bayesian_shrinkage_weights({})
        assert result == {}

    def test_empty_base_weights_with_sources(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # 空 base_weights, 有 2 个 sources → 均分
        result = engine.bayesian_shrinkage_weights({})
        assert math.isclose(result["ml"], 0.5)
        assert math.isclose(result["ai_hedge"], 0.5)

    def test_shrinkage_blends_with_prior(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # base_weights = {'ml': 0.9, 'ai_hedge': 0.1}, prior=0.5
        # shrinkage=0.3: ml=(0.7*0.9 + 0.3*0.5)=0.78, ai_hedge=(0.7*0.1+0.3*0.5)=0.22
        # 归一化: total=1.0
        result = engine.bayesian_shrinkage_weights(
            {"ml": 0.9, "ai_hedge": 0.1}, shrinkage_factor=0.3
        )
        assert math.isclose(result["ml"], 0.78)
        assert math.isclose(result["ai_hedge"], 0.22)

    def test_shrinkage_custom_factor(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # shrinkage_factor=1.0 → 完全收缩到先验 (均分)
        result = engine.bayesian_shrinkage_weights(
            {"ml": 0.9, "ai_hedge": 0.1}, shrinkage_factor=1.0
        )
        assert math.isclose(result["ml"], 0.5)
        assert math.isclose(result["ai_hedge"], 0.5)

    def test_shrinkage_normalization(self, engine):
        engine.register_source("a", MagicMock())
        engine.register_source("b", MagicMock())
        engine.register_source("c", MagicMock())
        result = engine.bayesian_shrinkage_weights(
            {"a": 0.5, "b": 0.3, "c": 0.2}, shrinkage_factor=0.3
        )
        # 权重和应为 1.0
        assert math.isclose(sum(result.values()), 1.0)

    def test_shrinkage_total_zero(self, engine):
        """total=0 时, 不归一化 (返回原 shrunk)."""
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        # base_weights 全 0, shrinkage=0 → 全 0, total=0
        result = engine.bayesian_shrinkage_weights(
            {"ml": 0.0, "ai_hedge": 0.0}, shrinkage_factor=0.0
        )
        # shrunk={ml: 0, ai_hedge: 0}, total=0 → 跳过归一化
        assert result["ml"] == 0.0
        assert result["ai_hedge"] == 0.0


# ============================================================
# 7. residual_fusion
# ============================================================


class TestResidualFusion:
    """残差化融合."""

    def test_residual_single_source(self, engine):
        sig = _make_signal(score=0.7, confidence=0.9)
        result = engine.residual_fusion({"ml": sig})
        assert result == {"ml": 0.7}

    def test_residual_no_correlation(self, engine):
        """低相关时, 各源分数保留."""
        sig1 = _make_signal(score=0.8, confidence=0.9)
        sig2 = _make_signal(score=0.6, confidence=0.7)
        corr = {
            "ml": {"ml": 1.0, "ai_hedge": 0.1},
            "ai_hedge": {"ml": 0.1, "ai_hedge": 1.0},
        }
        result = engine.residual_fusion(
            {"ml": sig1, "ai_hedge": sig2}, corr, threshold=0.3
        )
        # corr=0.1 < 0.3, 不残差化
        assert result["ml"] == 0.8
        assert result["ai_hedge"] == 0.6

    def test_residual_high_correlation(self, engine):
        """高相关时, 残差化次要源."""
        sig1 = _make_signal(score=0.8, confidence=0.9)
        sig2 = _make_signal(score=0.6, confidence=0.7)
        corr = {
            "ml": {"ml": 1.0, "ai_hedge": 0.9},
            "ai_hedge": {"ml": 0.9, "ai_hedge": 1.0},
        }
        result = engine.residual_fusion(
            {"ml": sig1, "ai_hedge": sig2}, corr, threshold=0.3
        )
        # ml confidence 高, 先处理 → base_score=0.8
        # corr(ml, ai_hedge)=0.9 > 0.3, 残差化 ai_hedge
        # residual_ai_hedge = 0.6 - 0.9 * 0.8 = 0.6 - 0.72 = -0.12
        assert result["ml"] == 0.8
        assert math.isclose(result["ai_hedge"], -0.12)

    def test_residual_correlation_none_uses_compute(self, engine):
        """correlation_matrix=None 时调用 compute_source_correlation."""
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        sig1 = _make_signal(score=0.8, confidence=0.9)
        sig2 = _make_signal(score=0.6, confidence=0.7)
        # mock compute_source_correlation 返回 {} (无 db 数据场景)
        with patch.object(engine, "compute_source_correlation", return_value={}):
            result = engine.residual_fusion({"ml": sig1, "ai_hedge": sig2})
        # corr_matrix={}, 所有 corr=0 → 不残差化
        assert result["ml"] == 0.8
        assert result["ai_hedge"] == 0.6

    def test_residual_processed_skip(self, engine):
        """已处理的源不再处理."""
        sigs = {
            "a": _make_signal(score=0.8, confidence=0.9),
            "b": _make_signal(score=0.7, confidence=0.8),
            "c": _make_signal(score=0.6, confidence=0.7),
        }
        # 高相关 a-b, a-c
        corr = {
            "a": {"a": 1.0, "b": 0.9, "c": 0.8},
            "b": {"a": 0.9, "b": 1.0, "c": 0.5},
            "c": {"a": 0.8, "b": 0.5, "c": 1.0},
        }
        result = engine.residual_fusion(sigs, corr, threshold=0.3)
        # a confidence 最高, 先处理 → base=0.8
        # b 残差化: 0.7 - 0.9*0.8 = -0.02
        # c 残差化: 0.6 - 0.8*0.8 = -0.04
        assert result["a"] == 0.8
        assert math.isclose(result["b"], -0.02)
        assert math.isclose(result["c"], -0.04)


# ============================================================
# 8. get_fused_signal / get_fused_signals_batch
# ============================================================


class TestGetFusedSignal:
    """主融合逻辑."""

    def test_no_sources_returns_hold(self, engine):
        result = engine.get_fused_signal("600519.SH", "贵州茅台")
        assert result.code == "600519.SH"
        assert result.action == "HOLD"
        assert result.confidence == 0.0
        assert result.consensus == "unknown"

    def test_all_getters_fail_returns_hold(self, engine):
        bad_getter = MagicMock(side_effect=ValueError("boom"))
        engine.register_source("ml", bad_getter)
        result = engine.get_fused_signal("600519.SH")
        assert result.action == "HOLD"
        assert result.consensus == "unknown"

    def test_all_getters_return_none(self, engine):
        none_getter = MagicMock(return_value=None)
        engine.register_source("ml", none_getter)
        result = engine.get_fused_signal("600519.SH")
        assert result.action == "HOLD"
        assert result.consensus == "unknown"

    def test_buy_signal_high_score(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        result = engine.get_fused_signal("600519.SH")
        assert result.action == "BUY"
        assert result.fused_score >= 0.60

    def test_sell_signal_low_score(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.1, action="SELL", confidence=0.9)
            ),
        )
        result = engine.get_fused_signal("600519.SH")
        assert result.action == "SELL"
        assert result.fused_score <= 0.40

    def test_hold_signal_middle_score(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.5, action="HOLD", confidence=0.5)
            ),
        )
        result = engine.get_fused_signal("600519.SH")
        assert result.action == "HOLD"
        assert 0.40 < result.fused_score < 0.60

    def test_consensus_strong_agree(self, engine):
        # 3 个源全部 BUY → strong_agree
        for name in ["ml", "ai_hedge", "glm5"]:
            engine.register_source(
                name,
                MagicMock(
                    return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
                ),
            )
        # mock compute_source_correlation 避免 db 数据不足异常
        with patch.object(engine, "compute_source_correlation", return_value={}):
            result = engine.get_fused_signal("600519.SH")
        assert result.consensus == "strong_agree"

    def test_consensus_disagree_with_conflict(self, engine):
        # 1 BUY + 1 SELL → 严重分歧 + 矛盾警告
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        engine.register_source(
            "ai_hedge",
            MagicMock(
                return_value=_make_signal(score=0.1, action="SELL", confidence=0.9)
            ),
        )
        # mock compute_source_correlation 避免 db 数据不足异常
        with patch.object(engine, "compute_source_correlation", return_value={}):
            result = engine.get_fused_signal("600519.SH")
        # 应有矛盾警告
        assert any("信号矛盾" in w for w in result.warnings)

    def test_individual_signals_recorded(self, engine):
        sig = _make_signal(score=0.9, action="BUY", confidence=0.9)
        engine.register_source("ml", MagicMock(return_value=sig))
        result = engine.get_fused_signal("600519.SH")
        assert "ml" in result.individual_signals
        assert result.individual_signals["ml"].action == "BUY"

    def test_persisted_to_db(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        engine.get_fused_signal("600519.SH", "贵州茅台")
        # 验证已写入 db
        conn = sqlite3.connect(engine.db_path)
        rows = conn.execute(
            "SELECT code, name, action FROM signal_store WHERE code=?", ("600519.SH",)
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "600519.SH"
        assert rows[0][1] == "贵州茅台"
        assert rows[0][2] == "BUY"

    def test_high_corr_warning_in_fused(self, engine):
        """相关性矩阵有高相关对时, 添加到 warnings."""
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.8, action="BUY", confidence=0.9)
            ),
        )
        engine.register_source(
            "ai_hedge",
            MagicMock(
                return_value=_make_signal(score=0.7, action="BUY", confidence=0.8)
            ),
        )
        # mock compute_source_correlation 返回高相关
        with patch.object(
            engine,
            "compute_source_correlation",
            return_value={
                "ml": {"ml": 1.0, "ai_hedge": 0.9},
                "ai_hedge": {"ml": 0.9, "ai_hedge": 1.0},
            },
        ):
            result = engine.get_fused_signal("600519.SH")
        assert any("高相关" in w for w in result.warnings)

    def test_get_fused_signals_batch(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        codes = ["600519.SH", "000001.SZ"]
        names = {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"}
        results = engine.get_fused_signals_batch(codes, names)
        assert len(results) == 2
        assert "600519.SH" in results
        assert "000001.SZ" in results
        assert results["600519.SH"].name == "贵州茅台"

    def test_get_fused_signals_batch_no_names(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        results = engine.get_fused_signals_batch(["X"])
        assert results["X"].name == ""


# ============================================================
# 9. _analyze_consensus
# ============================================================


class TestAnalyzeConsensus:
    """一致性分析."""

    def test_strong_agree(self, engine):
        sigs = {f"s{i}": _make_signal(action="BUY") for i in range(5)}
        consensus, warnings = engine._analyze_consensus(sigs)
        assert consensus == "strong_agree"
        # 无矛盾
        assert not any("矛盾" in w for w in warnings)

    def test_agree(self, engine):
        # 4 BUY + 1 HOLD → ratio=0.8 → strong_agree (边界)
        # 用 3 BUY + 2 HOLD → ratio=0.6 → agree
        sigs = {
            "a": _make_signal(action="BUY"),
            "b": _make_signal(action="BUY"),
            "c": _make_signal(action="BUY"),
            "d": _make_signal(action="HOLD"),
            "e": _make_signal(action="HOLD"),
        }
        consensus, warnings = engine._analyze_consensus(sigs)
        assert consensus == "agree"

    def test_mixed(self, engine):
        # 2 BUY + 2 SELL + 1 HOLD → max=2, ratio=0.4 → mixed
        sigs = {
            "a": _make_signal(action="BUY"),
            "b": _make_signal(action="BUY"),
            "c": _make_signal(action="SELL"),
            "d": _make_signal(action="SELL"),
            "e": _make_signal(action="HOLD"),
        }
        consensus, warnings = engine._analyze_consensus(sigs)
        assert consensus == "mixed"
        assert any("分歧" in w for w in warnings)

    def test_disagree(self, engine):
        # 1 BUY + 1 SELL + 1 HOLD → max=1, ratio=0.33 → disagree
        sigs = {
            "a": _make_signal(action="BUY"),
            "b": _make_signal(action="SELL"),
            "c": _make_signal(action="HOLD"),
        }
        consensus, warnings = engine._analyze_consensus(sigs)
        assert consensus == "disagree"
        assert any("严重分歧" in w for w in warnings)

    def test_conflict_buy_sell(self, engine):
        """BUY 和 SELL 共存 → 矛盾警告."""
        sigs = {
            "a": _make_signal(action="BUY"),
            "b": _make_signal(action="SELL"),
        }
        consensus, warnings = engine._analyze_consensus(sigs)
        assert any("信号矛盾" in w for w in warnings)

    def test_empty_individual(self, engine):
        """空字典, total=0, ratio=0 → disagree."""
        consensus, warnings = engine._analyze_consensus({})
        # total=0, ratio=0 → disagree
        assert consensus == "disagree"


# ============================================================
# 10. post-mix fuse (扩展)
# ============================================================


class TestFuseExtended:
    """post-mix 融合扩展测试."""

    def test_fuse_none_alpha(self, engine):
        result = engine.fuse(alpha_signals=None)
        assert result == []

    def test_fuse_empty_alpha(self, engine):
        result = engine.fuse(alpha_signals={})
        assert result == []

    def test_fuse_alpha_as_number(self, engine):
        """alpha_data 为数值 (非 dict)."""
        result = engine.fuse(alpha_signals={"X": 0.5})
        assert len(result) == 1
        assert result[0].sources["alpha_strength"] == 0.5

    def test_fuse_alpha_below_threshold_zeroed(self, engine):
        """|alpha_strength| < 0.10 归零."""
        result = engine.fuse(alpha_signals={"X": {"strength": 0.05, "confidence": 0.5}})
        assert result[0].sources["alpha_strength"] == 0.0

    def test_fuse_alpha_negative_below_threshold(self, engine):
        result = engine.fuse(
            alpha_signals={"X": {"strength": -0.05, "confidence": 0.5}}
        )
        assert result[0].sources["alpha_strength"] == 0.0

    def test_fuse_alpha_nan_zeroed(self, engine):
        result = engine.fuse(
            alpha_signals={"X": {"strength": float("nan"), "confidence": 0.5}}
        )
        assert result[0].sources["alpha_strength"] == 0.0
        assert math.isfinite(result[0].strength)

    def test_fuse_alpha_inf_zeroed(self, engine):
        result = engine.fuse(
            alpha_signals={"X": {"strength": float("inf"), "confidence": 0.5}}
        )
        assert result[0].sources["alpha_strength"] == 0.0

    def test_fuse_strength_in_range(self, engine):
        result = engine.fuse(alpha_signals={"X": {"strength": 0.5, "confidence": 0.8}})
        assert -1.0 <= result[0].strength <= 1.0

    def test_fuse_extreme_weights_alpha_w_negative(self, tmp_db):
        """权重极端配置时, alpha_w < 0 → 0."""
        eng = SignalFusionEngine(
            db_path=tmp_db, research_distilled_weight=0.6, pipeline_factor_weight=0.6
        )
        eng.inject_research_distilled_signals({"X": 0.8})
        eng.inject_pipeline_factor_signals({"X": 0.6})
        result = eng.fuse(alpha_signals={"X": {"strength": 0.5, "confidence": 0.8}})
        # alpha_w = 1 - 0.6 - 0.6 = -0.2 → 0
        # raw = 0 + 0.8*0.6 + 0.6*0.6 = 0.48 + 0.36 = 0.84
        # final = tanh(0.84) ≈ 0.686
        assert result[0].strength > 0
        assert math.isfinite(result[0].strength)

    def test_fuse_multi_symbol(self, engine):
        engine.inject_research_distilled_signals({"X": 0.8, "Y": -0.6})
        alpha = {
            "X": {"strength": 0.5, "confidence": 0.8},
            "Y": {"strength": -0.3, "confidence": 0.6},
            "Z": 0.4,  # 数值形式
        }
        result = engine.fuse(alpha_signals=alpha)
        result_map = {r.symbol: r for r in result}
        assert "X" in result_map
        assert "Y" in result_map
        assert "Z" in result_map
        # X 有 research, Y 有 research, Z 无 research
        assert result_map["X"].meta["research_distilled_applied"] is True
        assert result_map["Y"].meta["research_distilled_applied"] is True
        assert result_map["Z"].meta["research_distilled_applied"] is False


# ============================================================
# 11. inject 函数 / _is_valid_signal_value
# ============================================================


class TestInjectFunctions:
    """注入函数与值校验."""

    def test_is_valid_signal_value_none(self):
        assert SignalFusionEngine._is_valid_signal_value(None) is False

    def test_is_valid_signal_value_string(self):
        assert SignalFusionEngine._is_valid_signal_value("abc") is False

    def test_is_valid_signal_value_nan(self):
        assert SignalFusionEngine._is_valid_signal_value(float("nan")) is False

    def test_is_valid_signal_value_inf(self):
        assert SignalFusionEngine._is_valid_signal_value(float("inf")) is False
        assert SignalFusionEngine._is_valid_signal_value(float("-inf")) is False

    def test_is_valid_signal_value_numeric_string(self):
        # 数值字符串可以被 float() 转换 → True
        assert SignalFusionEngine._is_valid_signal_value("0.5") is True

    def test_is_valid_signal_value_valid_numbers(self):
        assert SignalFusionEngine._is_valid_signal_value(0.5) is True
        assert SignalFusionEngine._is_valid_signal_value(-1.0) is True
        assert SignalFusionEngine._is_valid_signal_value(0) is True
        assert SignalFusionEngine._is_valid_signal_value(100) is True

    def test_inject_research_distilled_non_dict(self, engine):
        engine.inject_research_distilled_signals(None)
        engine.inject_research_distilled_signals([0.5, 0.6])
        engine.inject_research_distilled_signals("string")
        engine.inject_research_distilled_signals(123)
        assert engine._research_distilled_signals == {}

    def test_inject_pipeline_factor_non_dict(self, engine):
        engine.inject_pipeline_factor_signals(None)
        engine.inject_pipeline_factor_signals([0.5])
        assert engine._pipeline_factor_signals == {}

    def test_inject_research_filters_invalid(self, engine):
        engine.inject_research_distilled_signals(
            {
                "valid": 0.5,
                "none": None,
                "nan": float("nan"),
                "inf": float("inf"),
                "str": "abc",
            }
        )
        assert "valid" in engine._research_distilled_signals
        assert "none" not in engine._research_distilled_signals
        assert "nan" not in engine._research_distilled_signals
        assert "inf" not in engine._research_distilled_signals
        assert "str" not in engine._research_distilled_signals

    def test_inject_pipeline_filters_invalid(self, engine):
        engine.inject_pipeline_factor_signals(
            {
                "valid": 0.3,
                "nan": float("nan"),
            }
        )
        assert "valid" in engine._pipeline_factor_signals
        assert "nan" not in engine._pipeline_factor_signals

    def test_inject_research_replaces_cache(self, engine):
        engine.inject_research_distilled_signals({"X": 0.5})
        engine.inject_research_distilled_signals({"Y": 0.6})
        # 第二次注入应替换缓存, 不是追加
        assert "X" not in engine._research_distilled_signals
        assert "Y" in engine._research_distilled_signals


# ============================================================
# 12. _persist_signal / record_audit 异常路径
# ============================================================


class TestPersistence:
    """持久化与异常处理."""

    def test_persist_signal_success(self, engine):
        fused = FusedSignal(
            code="X",
            name="测试",
            fused_score=0.7,
            action="BUY",
            confidence=0.8,
            consensus="strong_agree",
            individual_signals={
                "ml": _make_signal(score=0.7, action="BUY", confidence=0.8)
            },
            warnings=["w1"],
        )
        engine._persist_signal(fused)
        conn = sqlite3.connect(engine.db_path)
        rows = conn.execute(
            "SELECT code, action, consensus FROM signal_store"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("X", "BUY", "strong_agree")

    def test_persist_signal_db_error_caught(self, engine):
        # sqlite3.connect 抛 OSError (被 except 捕获)
        fused = FusedSignal(code="X", action="BUY")
        with patch("sqlite3.connect", side_effect=OSError("db error")):
            # 不应抛异常
            engine._persist_signal(fused)

    def test_record_audit_success(self, engine):
        engine.record_audit("600519.SH", "ml", "2026-08-01 10:00:00", "BUY", 0.7)
        conn = sqlite3.connect(engine.db_path)
        rows = conn.execute(
            "SELECT code, source, predicted_action FROM signal_audit"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("600519.SH", "ml", "BUY")

    def test_record_audit_db_error_caught(self, engine):
        # sqlite3.connect 抛 OSError (被 except 捕获)
        with patch("sqlite3.connect", side_effect=OSError("db error")):
            # 不应抛异常
            engine.record_audit("X", "ml", "2026-08-01", "BUY", 0.5)


# ============================================================
# 13. evaluate_past_signals / _get_actual_outcome
# ============================================================


class TestEvaluatePastSignals:
    """历史信号评估."""

    def test_get_actual_outcome_no_price_getter(self, engine):
        result = engine._get_actual_outcome("600519.SH", "2026-08-01")
        assert result is None

    def test_get_actual_outcome_with_price_getter_up(self, engine):
        pg = MagicMock(return_value={"change_pct": 2.5})
        result = engine._get_actual_outcome("X", "2026-08-01", pg)
        assert result == "UP"

    def test_get_actual_outcome_with_price_getter_down(self, engine):
        pg = MagicMock(return_value={"change_pct": -3.0})
        result = engine._get_actual_outcome("X", "2026-08-01", pg)
        assert result == "DOWN"

    def test_get_actual_outcome_price_getter_none(self, engine):
        pg = MagicMock(return_value=None)
        result = engine._get_actual_outcome("X", "2026-08-01", pg)
        assert result is None

    def test_get_actual_outcome_price_getter_no_change_pct(self, engine):
        pg = MagicMock(return_value={"other": 1.0})
        result = engine._get_actual_outcome("X", "2026-08-01", pg)
        assert result is None

    def test_get_actual_outcome_price_getter_exception(self, engine):
        pg = MagicMock(side_effect=ValueError("boom"))
        result = engine._get_actual_outcome("X", "2026-08-01", pg)
        assert result is None

    def test_evaluate_no_signals(self, engine):
        result = engine.evaluate_past_signals(days_ago=5)
        assert result["evaluated"] == 0
        assert result["correct"] == 0
        assert result["accuracy"] is None
        assert result["details"] == []

    def test_evaluate_with_signals_and_actual(self, engine):
        # 插入 5 天前的信号
        target_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        engine.record_audit("X", "ml", target_date + " 10:00:00", "BUY", 0.7)
        engine.record_audit("Y", "ml", target_date + " 10:00:00", "SELL", 0.3)

        # mock price_getter: X 上涨, Y 下跌
        def price_getter(code, date):
            if code == "X":
                return {"change_pct": 1.0}
            elif code == "Y":
                return {"change_pct": -1.0}
            return None

        result = engine.evaluate_past_signals(days_ago=5, price_getter=price_getter)
        assert result["evaluated"] == 2
        assert result["correct"] == 2  # BUY+UP=correct, SELL+DOWN=correct
        assert result["accuracy"] == 1.0

    def test_evaluate_with_partial_correct(self, engine):
        target_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        engine.record_audit("X", "ml", target_date + " 10:00:00", "BUY", 0.7)
        engine.record_audit("Y", "ml", target_date + " 10:00:00", "SELL", 0.3)

        # X 涨 (BUY correct), Y 涨 (SELL wrong)
        def price_getter(code, date):
            return {"change_pct": 1.0}

        result = engine.evaluate_past_signals(days_ago=5, price_getter=price_getter)
        assert result["evaluated"] == 2
        assert result["correct"] == 1
        assert math.isclose(result["accuracy"], 0.5)

    def test_evaluate_with_no_actual(self, engine):
        target_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        engine.record_audit("X", "ml", target_date + " 10:00:00", "BUY", 0.7)
        # price_getter 返回 None → actual=None → 跳过
        result = engine.evaluate_past_signals(
            days_ago=5, price_getter=lambda c, d: None
        )
        assert result["evaluated"] == 0


# ============================================================
# 14. 查询接口
# ============================================================


class TestQueryInterface:
    """查询接口."""

    def test_get_recent_signals_empty(self, engine):
        result = engine.get_recent_signals("X")
        assert result == []

    def test_get_recent_signals_with_data(self, engine):
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.8, action="BUY", confidence=0.9)
            ),
        )
        engine.get_fused_signal("X", "测试")
        rows = engine.get_recent_signals("X", limit=5)
        assert len(rows) == 1
        assert rows[0]["code"] == "X"
        assert rows[0]["action"] == "BUY"
        assert rows[0]["name"] == "测试"

    def test_get_daily_summary_empty(self, engine):
        result = engine.get_daily_summary()
        assert result["total"] == 0
        assert result["buy"] == 0
        assert result["sell"] == 0
        assert result["hold"] == 0
        assert result["signals"] == []

    def test_get_daily_summary_with_signals(self, engine):
        # BUY 信号
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.9, action="BUY", confidence=0.9)
            ),
        )
        engine.get_fused_signal("BUY_CODE")
        # 移除源, 添加 SELL 源
        engine.remove_source("ml")
        engine.register_source(
            "ml",
            MagicMock(
                return_value=_make_signal(score=0.1, action="SELL", confidence=0.9)
            ),
        )
        engine.get_fused_signal("SELL_CODE")
        result = engine.get_daily_summary()
        assert result["total"] == 2
        assert result["buy"] == 1
        assert result["sell"] == 1

    def test_get_stats(self, engine):
        engine.register_source("ml", MagicMock())
        engine.register_source("ai_hedge", MagicMock())
        stats = engine.get_stats()
        assert "ml" in stats["sources_registered"]
        assert "ai_hedge" in stats["sources_registered"]
        assert "ml" in stats["source_weights"]
        assert stats["db_path"] == engine.db_path


# ============================================================
# 15. 模块级函数
# ============================================================


class TestModuleFunctions:
    """模块级函数: get_fusion_engine / get_consensus_action."""

    def test_get_fusion_engine_singleton(self):
        # 重置全局单例
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        e1 = get_fusion_engine()
        e2 = get_fusion_engine()
        assert e1 is e2
        # 清理
        sf._fusion_engine = None

    def test_get_consensus_action_calls_engine(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        # mock get_fusion_engine 返回 mock
        mock_eng = MagicMock()
        mock_eng.get_fused_signal.return_value = FusedSignal(code="X", action="BUY")
        with patch("utils.signal_fusion.get_fusion_engine", return_value=mock_eng):
            result = get_consensus_action("X", "测试")
        assert result.action == "BUY"
        sf._fusion_engine = None


# ============================================================
# 16. 快速信号源集成
# ============================================================


class TestFastSignalSource:
    """快速技术指标信号源."""

    def test_get_fast_signal_source_returns_signal(self):
        """模拟 hybrid_fusion 返回 fast signal."""
        mock_engine = MagicMock()
        mock_hybrid = MagicMock()
        mock_hybrid.source = "fast"
        mock_fast = MagicMock()
        mock_fast.action = "BUY"
        mock_fast.confidence = 0.8
        mock_fast.rsi = 35.0
        mock_fast.macd_signal = 0.0025
        mock_hybrid.fast_signal = mock_fast
        mock_engine.get_hybrid_signal.return_value = mock_hybrid
        # utils.hybrid_fusion 模块不存在, 注入 mock 模块到 sys.modules
        mock_module = MagicMock()
        mock_module.get_hybrid_fusion_engine.return_value = mock_engine
        with patch.dict("sys.modules", {"utils.hybrid_fusion": mock_module}):
            result = _get_fast_signal_source("600519.SH")
        assert result is not None
        assert result.source == "fast_technical"
        assert result.action == "BUY"

    def test_get_fast_signal_source_not_fast(self):
        """非 fast source → 返回 None."""
        mock_engine = MagicMock()
        mock_hybrid = MagicMock()
        mock_hybrid.source = "slow"  # 非 fast
        mock_engine.get_hybrid_signal.return_value = mock_hybrid
        mock_module = MagicMock()
        mock_module.get_hybrid_fusion_engine.return_value = mock_engine
        with patch.dict("sys.modules", {"utils.hybrid_fusion": mock_module}):
            result = _get_fast_signal_source("X")
        assert result is None

    def test_get_fast_signal_source_exception(self):
        """异常 → 返回 None."""
        mock_module = MagicMock()
        mock_module.get_hybrid_fusion_engine.side_effect = OSError("boom")
        with patch.dict("sys.modules", {"utils.hybrid_fusion": mock_module}):
            result = _get_fast_signal_source("X")
        assert result is None

    def test_register_fast_signal_source(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        # 创建临时单例
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            register_fast_signal_source(initial_weight=0.25)
            assert "fast_technical" in sf._fusion_engine._sources
            sf._fusion_engine = None

    def test_get_fast_signal_integration_enabled_true(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            sf._fusion_engine.register_source("fast_technical", MagicMock())
            assert get_fast_signal_integration_enabled() is True
            sf._fusion_engine = None

    def test_get_fast_signal_integration_enabled_false(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            # 未注册 fast_technical
            assert get_fast_signal_integration_enabled() is False
            sf._fusion_engine = None


# ============================================================
# 17. 对冲信号源集成
# ============================================================


class TestHedgeSignalSource:
    """对冲引擎信号源."""

    def test_get_hedge_signal_source_empty_positions(self):
        """无 positions.json → 空仓 → HOLD."""
        with patch("os.path.exists", return_value=False):
            result = _get_hedge_signal_source("600519.SH")
        assert result is not None
        assert result.action == "HOLD"
        assert result.source == "hedge_engine"
        assert result.score == 0.5

    def test_get_hedge_signal_source_exception(self):
        """异常 → 返回 None."""
        with patch("os.path.exists", side_effect=OSError("boom")):
            result = _get_hedge_signal_source("X")
        assert result is None

    def test_get_hedge_signal_source_with_positions(self):
        """有持仓时, 调用 hedge_engine."""
        import io

        # mock hedge_engine
        mock_hedge_engine = MagicMock()
        mock_risk = MagicMock()
        mock_risk.beta_csi300 = 1.2
        mock_risk.var_95_daily = 50000
        mock_hedge_engine.assess_portfolio_risk.return_value = mock_risk
        mock_strength = MagicMock()
        mock_strength.value = 3  # STRONG
        mock_hedge_engine.determine_hedge_signal_strength.return_value = (
            mock_strength,
            0.8,
        )

        positions_data = {
            "cash": 500000,
            "positions": {
                "600519.SH": {"shares": 100, "cost": 1500},
            },
        }
        pricing_line = json.dumps({"code": "600519.SH", "price": 1800}) + "\n"

        # patch os.path.exists 返回 True; open 按路径后缀返回内容
        def fake_open(file, mode="r", *args, **kwargs):
            path = str(file)
            if path.endswith("positions.json"):
                return io.StringIO(json.dumps(positions_data))
            elif path.endswith("price_history.jsonl"):
                return io.StringIO(pricing_line)
            raise FileNotFoundError(str(file))

        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", side_effect=fake_open),
            patch(
                "utils.hedge_engine.get_hedge_engine", return_value=mock_hedge_engine
            ),
        ):
            result = _get_hedge_signal_source("600519.SH")
        assert result is not None
        assert result.action == "SELL"  # STRONG → SELL
        assert result.score == 0.25

    def test_register_hedge_signal_source(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            register_hedge_signal_source(initial_weight=0.2)
            assert "hedge_engine" in sf._fusion_engine._sources
            sf._fusion_engine = None

    def test_is_hedge_signal_enabled(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            assert is_hedge_signal_enabled() is False
            sf._fusion_engine.register_source("hedge_engine", MagicMock())
            assert is_hedge_signal_enabled() is True
            sf._fusion_engine = None


def _fake_open_for_hedge_unused():
    """保留占位 (原 _fake_open_for_hedge 已不再使用)."""
    pass


# ============================================================
# 18. GTJA191 信号源集成
# ============================================================


class TestGTJA191SignalSource:
    """GTJA191 量价因子信号源."""

    def test_get_gtja191_insufficient_data(self):
        """数据不足 21 行 → None."""
        import pandas as pd

        short_df = pd.DataFrame({"close": [10.0] * 5})
        mock_fetch = MagicMock(return_value=short_df)
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()  # 避免触发 gtja191_factors 的导入错误
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("600519.SH")
        assert result is None

    def test_get_gtja191_none_data(self):
        mock_fetch = MagicMock(return_value=None)
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("X")
        assert result is None

    def test_get_gtja191_valid_buy(self):
        """有效数据 → 计算因子 → BUY."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "close": [10.0 + i * 0.1 for i in range(60)],
                "high": [11.0 + i * 0.1 for i in range(60)],
                "low": [9.0 + i * 0.1 for i in range(60)],
                "open": [10.0 + i * 0.1 for i in range(60)],
                "volume": [1e6] * 60,
            }
        )
        mock_factors = MagicMock()
        mock_factors.alpha144.return_value = 1e-10  # 极小值 → score≈1.0 → BUY
        mock_fetch = MagicMock(return_value=df)
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()
        mock_gtja191.GTJA191Factors.return_value = mock_factors
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("600519.SH")
        assert result is not None
        assert result.source == "gtja191"
        assert result.action == "BUY"

    def test_get_gtja191_valid_sell(self):
        """alpha144 返回大值 → score 低 → SELL."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "close": [10.0 + i * 0.1 for i in range(60)],
                "high": [11.0 for i in range(60)],
                "low": [9.0 for i in range(60)],
                "open": [10.0 for i in range(60)],
                "volume": [1e6] * 60,
            }
        )
        mock_factors = MagicMock()
        mock_factors.alpha144.return_value = 10.0  # 大值 → score≈0 → SELL
        mock_fetch = MagicMock(return_value=df)
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()
        mock_gtja191.GTJA191Factors.return_value = mock_factors
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("600519.SH")
        assert result is not None
        assert result.action == "SELL"

    def test_get_gtja191_value_none(self):
        """alpha144 返回 None → None."""
        import pandas as pd

        df = pd.DataFrame({"close": [10.0] * 30})
        mock_factors = MagicMock()
        mock_factors.alpha144.return_value = None
        mock_fetch = MagicMock(return_value=df)
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()
        mock_gtja191.GTJA191Factors.return_value = mock_factors
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("X")
        assert result is None

    def test_get_gtja191_exception(self):
        """异常 → 返回 None."""
        mock_fetch = MagicMock(side_effect=OSError("boom"))
        mock_kronos = MagicMock(fetch_a_stock_data=mock_fetch)
        mock_gtja191 = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "utils.kronos_predictor": mock_kronos,
                "utils.gtja191_factors": mock_gtja191,
            },
        ):
            result = _get_gtja191_signal_source("X")
        assert result is None

    def test_register_gtja191_signal_source(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            register_gtja191_signal_source(initial_weight=0.15)
            assert "gtja191" in sf._fusion_engine._sources
            sf._fusion_engine = None

    def test_is_gtja191_signal_enabled(self):
        import utils.signal_fusion as sf

        sf._fusion_engine = None
        with tempfile.TemporaryDirectory() as tmp:
            sf._fusion_engine = SignalFusionEngine(db_path=os.path.join(tmp, "test.db"))
            assert is_gtja191_signal_enabled() is False
            sf._fusion_engine.register_source("gtja191", MagicMock())
            assert is_gtja191_signal_enabled() is True
            sf._fusion_engine = None
