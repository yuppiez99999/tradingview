"""
FinRL-X 权重中心接口架构 — 单元测试
====================================

文献: #49 FinRL-X PAKDD 2026
"""

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.finrl_x_interface import (
    BacktestLiveConsistency,
    FinRLXInterface,
    LegacyAdapter,
    PipelineStage,
    StrategyNode,
    StrategyPipeline,
    WeightCenter,
    WeightRecord,
    WeightSource,
)

# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    def test_weight_sources(self):
        assert len(WeightSource) == 3

    def test_pipeline_stages(self):
        assert len(PipelineStage) == 5


# ============================================================
# 权重中心测试
# ============================================================

class TestWeightCenter:
    def test_submit(self):
        wc = WeightCenter()
        record = wc.submit(np.array([0.5, 0.5]), WeightSource.BACKTEST)
        assert isinstance(record, WeightRecord)
        assert record.source == WeightSource.BACKTEST

    def test_get_latest(self):
        wc = WeightCenter()
        wc.submit(np.array([0.3, 0.7]), WeightSource.BACKTEST)
        latest = wc.get_latest(WeightSource.BACKTEST)
        assert latest is not None
        np.testing.assert_array_equal(latest.weights, [0.3, 0.7])

    def test_consistency_pass(self):
        wc = WeightCenter()
        w = np.array([0.25, 0.25, 0.25, 0.25])
        wc.submit(w, WeightSource.BACKTEST)
        wc.submit(w, WeightSource.LIVE)
        report = wc.check_consistency(threshold=0.01)
        assert report["consistent"]

    def test_consistency_fail(self):
        wc = WeightCenter()
        wc.submit(np.array([0.5, 0.5]), WeightSource.BACKTEST)
        wc.submit(np.array([0.6, 0.4]), WeightSource.LIVE)
        report = wc.check_consistency(threshold=0.01)
        assert not report["consistent"]

    def test_consistency_missing(self):
        wc = WeightCenter()
        report = wc.check_consistency()
        assert not report["consistent"]

    def test_history(self):
        wc = WeightCenter()
        wc.submit(np.array([0.5, 0.5]), WeightSource.BACKTEST)
        wc.submit(np.array([0.4, 0.6]), WeightSource.LIVE)
        assert len(wc.get_history()) == 2
        assert len(wc.get_history(WeightSource.BACKTEST)) == 1


# ============================================================
# 策略管线测试
# ============================================================

class TestStrategyPipeline:
    def test_add_remove(self):
        p = StrategyPipeline()
        node = StrategyNode("test")
        p.add(node)
        assert len(p._nodes) == 1
        assert p.remove("test")
        assert len(p._nodes) == 0

    def test_execute(self):
        p = StrategyPipeline()
        p.add(StrategyNode("double", func=lambda x, ctx: x * 2))
        p.add(StrategyNode("add1", func=lambda x, ctx: x + 1))
        result = p.execute(5)
        assert result == 11

    def test_disabled_node(self):
        p = StrategyPipeline()
        p.add(StrategyNode("double", func=lambda x, ctx: x * 2))
        p.add(StrategyNode("skip", func=lambda x, ctx: x * 100, enabled=False))
        result = p.execute(5)
        assert result == 10

    def test_validate(self):
        p = StrategyPipeline()
        p.add(StrategyNode("data", PipelineStage.DATA))
        p.add(StrategyNode("feature", PipelineStage.FEATURE))
        p.add(StrategyNode("signal", PipelineStage.SIGNAL))
        p.add(StrategyNode("weight", PipelineStage.WEIGHT))
        v = p.validate()
        assert v["valid"]

    def test_validate_missing(self):
        p = StrategyPipeline()
        p.add(StrategyNode("data", PipelineStage.DATA))
        v = p.validate()
        assert not v["valid"]


# ============================================================
# 一致性验证测试
# ============================================================

class TestBacktestLiveConsistency:
    def test_verify_weights(self):
        wc = WeightCenter()
        c = BacktestLiveConsistency(wc)
        wc.submit(np.array([0.5, 0.5]), WeightSource.BACKTEST)
        wc.submit(np.array([0.5, 0.5]), WeightSource.LIVE)
        report = c.verify_weights()
        assert report["consistent"]

    def test_verify_data(self):
        wc = WeightCenter()
        c = BacktestLiveConsistency(wc)
        d1 = np.array([1.0, 2.0, 3.0])
        d2 = np.array([1.0, 2.0, 3.0])
        report = c.verify_data(d1, d2)
        assert report["consistent"]

    def test_verify_data_mismatch(self):
        wc = WeightCenter()
        c = BacktestLiveConsistency(wc)
        report = c.verify_data(np.array([1, 2]), np.array([1, 2, 3]))
        assert not report["consistent"]

    def test_verify_execution(self):
        wc = WeightCenter()
        c = BacktestLiveConsistency(wc)
        orders = [{"symbol": "AAPL", "quantity": 100}]
        report = c.verify_execution(orders, orders)
        assert report["consistent"]


# ============================================================
# 集成接口测试
# ============================================================

class TestFinRLXInterface:
    def test_submit_weights(self):
        iface = FinRLXInterface()
        iface.submit_backtest_weights(np.array([0.5, 0.5]))
        iface.submit_live_weights(np.array([0.5, 0.5]))
        report = iface.verify_consistency()
        assert report["consistent"]

    def test_build_pipeline(self):
        iface = FinRLXInterface()
        p = iface.build_pipeline([
            StrategyNode("data", PipelineStage.DATA),
            StrategyNode("signal", PipelineStage.SIGNAL),
            StrategyNode("weight", PipelineStage.WEIGHT),
        ])
        assert isinstance(p, StrategyPipeline)

    def test_run_pipeline(self):
        iface = FinRLXInterface()
        iface.build_pipeline([
            StrategyNode("id", func=lambda x, ctx: x * 2),
        ])
        result = iface.run_pipeline(5)
        assert result == 10

    def test_status(self):
        iface = FinRLXInterface()
        iface.submit_backtest_weights(np.array([0.5, 0.5]))
        status = iface.get_status()
        assert status["weight_history"] == 1


# ============================================================
# 旧管道兼容测试
# ============================================================

class TestLegacyAdapter:
    def test_get_weights(self):
        iface = FinRLXInterface()
        iface.submit_backtest_weights(np.array([0.3, 0.7]))
        adapter = LegacyAdapter(iface)
        w = adapter.get_weights("backtest")
        np.testing.assert_array_equal(w, [0.3, 0.7])

    def test_get_weights_none(self):
        iface = FinRLXInterface()
        adapter = LegacyAdapter(iface)
        assert adapter.get_weights("backtest") is None

    def test_allocate(self):
        iface = FinRLXInterface()
        adapter = LegacyAdapter(iface)
        returns = np.random.randn(100, 4)
        w = adapter.allocate(returns)
        assert abs(w.sum() - 1.0) < 1e-6


# ============================================================
# 端到端测试
# ============================================================

class TestEndToEnd:
    def test_full_workflow(self):
        iface = FinRLXInterface()
        w = np.array([0.3, 0.25, 0.2, 0.15, 0.1])
        iface.submit_backtest_weights(w, timestamp="t1")
        iface.submit_live_weights(w + 0.001, timestamp="t1")
        report = iface.verify_consistency(threshold=0.01)
        assert report["consistent"]

    def test_pipeline_with_weight_center(self):
        iface = FinRLXInterface()

        def weight_fn(data: object, ctx: dict) -> np.ndarray:
            return np.array([0.4, 0.3, 0.2, 0.1])

        iface.build_pipeline([
            StrategyNode("data", PipelineStage.DATA, lambda x, ctx: x),
            StrategyNode("weight", PipelineStage.WEIGHT, weight_fn),
        ])
        result = iface.run_pipeline(np.random.randn(100, 4))
        iface.submit_backtest_weights(result)
        assert abs(result.sum() - 1.0) < 1e-6
