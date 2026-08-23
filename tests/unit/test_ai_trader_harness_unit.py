"""
AI-Trader 实时未污染评估基准 — 单元测试
======================================

测试覆盖:
- DataRecord (哈希计算 + 篡改检测)
- RealTimeStream (时序生成 + 污染注入)
- DataContaminationDetector (五层防线)
- AgentAdapter (5 种策略)
- AITraderHarness (端到端 + 污染注入测试)
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.ai_trader_harness import (
    DEFAULT_AGENTS,
    DEFAULT_SYMBOLS,
    AgentAdapter,
    AgentEvalResult,
    AITraderEvalReport,
    AITraderHarness,
    ContaminationReport,
    DataContaminationDetector,
    DataRecord,
    RealTimeStream,
)

# ============================================================
# DataRecord 测试
# ============================================================

class TestDataRecord:
    """数据记录测试。"""

    def test_hash_auto_computed(self):
        """哈希自动计算。"""
        r = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
        assert r.record_hash != ""
        assert len(r.record_hash) == 16  # SHA256 前 16 字符

    def test_hash_valid_for_unchanged(self):
        """未篡改的记录哈希有效。"""
        r = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
        assert r.is_hash_valid() is True

    def test_hash_invalid_after_tamper(self):
        """篡改后哈希无效。"""
        r = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
        original_hash = r.record_hash
        # 篡改: 修改价格但保持旧哈希
        tampered = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                              price=200.0, volume=1000000,
                              record_hash=original_hash)
        assert tampered.is_hash_valid() is False

    def test_hash_deterministic(self):
        """相同内容产生相同哈希。"""
        r1 = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                        price=100.0, volume=1000000)
        r2 = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                        price=100.0, volume=1000000)
        assert r1.record_hash == r2.record_hash

    def test_to_dict(self):
        """序列化为 dict。"""
        r = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
        d = r.to_dict()
        assert d["timestamp"] == "2024-01-01 15:00:00"
        assert d["price"] == 100.0


# ============================================================
# RealTimeStream 测试
# ============================================================

class TestRealTimeStream:
    """实时数据流测试。"""

    def test_records_generated(self):
        """记录正确生成。"""
        stream = RealTimeStream(["000001.SZ"], "2024-01-01", "2024-01-31", seed=42)
        records = stream.get_all_records()
        assert len(records) > 0
        assert all(r.price > 0 for r in records)
        assert all(r.volume > 0 for r in records)

    def test_strict_temporal_cutoff(self):
        """严格时序切分。"""
        stream = RealTimeStream(["000001.SZ"], "2024-01-01", "2024-01-31", seed=42)
        early = stream.get_records_up_to("2024-01-10 15:00:00")
        late = stream.get_records_up_to("2024-01-20 15:00:00")
        assert len(early) < len(late)
        # early 中的记录都 <= cutoff
        assert all(r.timestamp <= "2024-01-10 15:00:00" for r in early)

    def test_hash_contamination_injection(self):
        """哈希篡改注入。"""
        stream = RealTimeStream(["000001.SZ"], "2024-01-01", "2024-01-31", seed=42)
        original = stream.get_all_records()
        original_valid = sum(1 for r in original if r.is_hash_valid())
        stream.inject_hash_contamination(n=3)
        contaminated = stream.get_all_records()
        new_valid = sum(1 for r in contaminated if r.is_hash_valid())
        assert new_valid < original_valid  # 篡改后有效哈希减少

    def test_order_contamination_injection(self):
        """乱序注入。"""
        stream = RealTimeStream(["000001.SZ"], "2024-01-01", "2024-01-31", seed=42)
        stream.inject_order_contamination(n=5)
        records = stream.get_all_records()
        # 检查是否存在乱序
        has_disorder = any(
            records[i].timestamp < records[i - 1].timestamp
            for i in range(1, len(records))
        )
        assert has_disorder

    def test_future_timestamp_injection(self):
        """未来时间戳注入。"""
        stream = RealTimeStream(["000001.SZ"], "2024-01-01", "2024-01-31", seed=42)
        stream.inject_future_timestamp(n=2)
        records = stream.get_all_records()
        has_future = any(r.timestamp > "2024-12-31 15:00:00" for r in records)
        assert has_future

    def test_reproducible_with_same_seed(self):
        """相同 seed 可复现。"""
        s1 = RealTimeStream(["A"], "2024-01-01", "2024-01-10", seed=42)
        s2 = RealTimeStream(["A"], "2024-01-01", "2024-01-10", seed=42)
        r1 = s1.get_all_records()
        r2 = s2.get_all_records()
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2, strict=False):
            assert a.price == b.price
            assert a.timestamp == b.timestamp


# ============================================================
# DataContaminationDetector 测试
# ============================================================

class TestDataContaminationDetector:
    """数据污染检测器测试 (核心创新)。"""

    def test_clean_records_pass(self):
        """干净数据通过检测。"""
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0 + i, volume=1000000)
            for i in range(1, 11)
        ]
        detector = DataContaminationDetector()
        report = detector.detect(records)
        assert report.is_contaminated is False
        assert report.hash_violations == 0
        assert report.order_violations == 0

    def test_hash_violation_detected(self):
        """哈希篡改被检测。"""
        records = [
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000),
        ]
        # 篡改
        tampered = DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                              price=200.0, volume=1000000,
                              record_hash=records[0].record_hash)
        detector = DataContaminationDetector()
        report = detector.detect([tampered])
        assert report.is_contaminated is True
        assert report.hash_violations > 0

    def test_order_violation_detected(self):
        """乱序被检测。"""
        records = [
            DataRecord(timestamp="2024-01-03 15:00:00", symbol="A",
                       price=100.0, volume=1000000),
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=101.0, volume=1000000),
        ]
        detector = DataContaminationDetector()
        report = detector.detect(records)
        assert report.is_contaminated is True
        assert report.order_violations > 0

    def test_future_timestamp_detected(self):
        """未来时间戳被检测。"""
        records = [
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000),
            DataRecord(timestamp="2099-12-31 15:00:00", symbol="A",
                       price=101.0, volume=1000000),
        ]
        detector = DataContaminationDetector()
        report = detector.detect(records, decision_cutoff="2024-06-30 15:00:00")
        assert report.is_contaminated is True
        assert report.future_ts_violations > 0

    def test_untrusted_source_detected(self):
        """不可信来源被检测。"""
        records = [
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000, source="unknown"),
        ]
        detector = DataContaminationDetector()
        report = detector.detect(records)
        assert report.is_contaminated is True
        assert report.isolation_violations > 0

    def test_empty_records_safe(self):
        """空记录不崩溃。"""
        detector = DataContaminationDetector()
        report = detector.detect([])
        assert report.is_contaminated is False
        assert report.total_records == 0

    def test_contamination_score(self):
        """污染分数计算。"""
        records = [
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000),
            DataRecord(timestamp="2024-01-02 15:00:00", symbol="A",
                       price=101.0, volume=1000000, source="unknown"),
        ]
        detector = DataContaminationDetector()
        report = detector.detect(records)
        assert report.contamination_score > 0.0
        assert report.contamination_score <= 1.0


# ============================================================
# AgentAdapter 测试
# ============================================================

class TestAgentAdapter:
    """Agent 适配器测试。"""

    def test_momentum_strategy(self):
        """动量策略。"""
        agent = AgentAdapter(name="test", strategy="momentum")
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0 + i * 2, volume=1000000)
            for i in range(1, 11)
        ]
        decision = agent.make_decision(records, "A", "2024-01-10 15:00:00")
        assert decision.action in ("buy", "sell", "hold")
        assert 0.0 <= decision.weight <= 1.0

    def test_mean_revert_strategy(self):
        """均值回归策略。"""
        agent = AgentAdapter(name="test", strategy="mean_revert")
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
            for i in range(1, 11)
        ]
        # 添加一个偏离均值的记录
        records.append(DataRecord(timestamp="2024-01-11 15:00:00", symbol="A",
                                  price=150.0, volume=1000000))
        decision = agent.make_decision(records, "A", "2024-01-11 15:00:00")
        assert decision.action in ("buy", "sell", "hold")

    def test_value_strategy(self):
        """价值策略。"""
        agent = AgentAdapter(name="test", strategy="value")
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0 + i, volume=1000000)
            for i in range(1, 21)
        ]
        decision = agent.make_decision(records, "A", "2024-01-20 15:00:00")
        assert decision.action in ("buy", "sell", "hold")

    def test_sentiment_strategy(self):
        """情感策略。"""
        agent = AgentAdapter(name="test", strategy="sentiment")
        records = [
            DataRecord(timestamp="2024-01-01 15:00:00", symbol="A",
                       price=100.0, volume=1000000)
        ]
        decision = agent.make_decision(records, "A", "2024-01-01 15:00:00")
        assert decision.action in ("buy", "sell", "hold")

    def test_ensemble_strategy(self):
        """集成策略。"""
        agent = AgentAdapter(name="test", strategy="ensemble")
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0 + i, volume=1000000)
            for i in range(1, 11)
        ]
        decision = agent.make_decision(records, "A", "2024-01-10 15:00:00")
        assert decision.action in ("buy", "sell", "hold")

    def test_empty_records_hold(self):
        """空数据返回 hold。"""
        agent = AgentAdapter(name="test", strategy="momentum")
        decision = agent.make_decision([], "A", "2024-01-01 15:00:00")
        assert decision.action == "hold"
        assert decision.weight == 0.0

    def test_data_used_recorded(self):
        """决策记录所用数据。"""
        agent = AgentAdapter(name="test", strategy="momentum")
        records = [
            DataRecord(timestamp=f"2024-01-{i:02d} 15:00:00", symbol="A",
                       price=100.0 + i, volume=1000000)
            for i in range(1, 11)
        ]
        decision = agent.make_decision(records, "A", "2024-01-10 15:00:00")
        assert len(decision.data_used) > 0


# ============================================================
# AITraderHarness 端到端测试
# ============================================================

class TestAITraderHarness:
    """主评估器端到端测试。"""

    @pytest.fixture
    def harness(self):
        return AITraderHarness(symbols=["000001.SZ", "600000.SH"], seed=42)

    @pytest.fixture
    def agents(self):
        return [
            AgentAdapter(name=cfg["name"], strategy=cfg["strategy"], mock=True)
            for cfg in DEFAULT_AGENTS
        ]

    def test_evaluation_completes(self, harness, agents):
        """评估正常完成。"""
        report = harness.run_evaluation(agents, "2024-01-01", "2024-02-28")
        assert len(report.results) == 5
        assert report.start_date == "2024-01-01"
        assert report.n_stream_records > 0

    def test_clean_stream_no_contamination(self, harness, agents):
        """干净数据流无污染。"""
        report = harness.run_evaluation(agents, "2024-01-01", "2024-02-28")
        assert report.global_contamination.is_contaminated is False
        for result in report.results:
            assert result.error is None
            assert result.contamination.is_contaminated is False

    def test_report_serialization(self, harness, agents):
        """报告序列化。"""
        report = harness.run_evaluation(agents, "2024-01-01", "2024-02-28")
        data = report.to_dict()
        json_str = json.dumps(data, ensure_ascii=False)
        assert "results" in json_str
        assert "global_contamination" in json_str

    def test_report_save_to_file(self, harness, agents):
        """报告保存到文件。"""
        report = harness.run_evaluation(agents, "2024-01-01", "2024-02-28")
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = harness.save_report(report, tmpdir)
            assert filepath.exists()
            with open(filepath, encoding="utf-8") as f:
                saved = json.load(f)
            assert len(saved["results"]) == 5

    def test_contamination_injection_test(self, harness, agents):
        """污染注入测试 — 检测器检出全部注入。"""
        result = harness.run_contamination_test(agents, "2024-01-01", "2024-02-28")
        assert result["hash_contamination"]["detected"] is True
        assert result["order_contamination"]["detected"] is True
        assert result["future_ts_contamination"]["detected"] is True
        assert result["all_detected"] is True

    def test_5_agents_registry_complete(self):
        """默认 agent 注册表包含 5 个。"""
        assert len(DEFAULT_AGENTS) == 5

    def test_default_symbols_not_empty(self):
        """默认标的非空。"""
        assert len(DEFAULT_SYMBOLS) > 0

    def test_all_strategies_produce_decisions(self, harness):
        """所有策略都能产生决策。"""
        agents = [
            AgentAdapter(name=f"test-{s}", strategy=s, mock=True)
            for s in ["momentum", "mean_revert", "value", "sentiment", "ensemble"]
        ]
        report = harness.run_evaluation(agents, "2024-01-01", "2024-02-28")
        for result in report.results:
            assert result.n_total_decisions > 0
            assert result.error is None


# ============================================================
# 数据结构测试
# ============================================================

class TestDataStructures:
    """数据结构测试。"""

    def test_contamination_report_defaults(self):
        """ContaminationReport 默认值。"""
        r = ContaminationReport()
        assert r.is_contaminated is False
        assert r.contamination_score == 0.0

    def test_agent_eval_result_accuracy(self):
        """AgentEvalResult 准确率计算。"""
        r = AgentEvalResult(agent_name="test", strategy="momentum",
                           n_correct_decisions=8, n_total_decisions=10)
        assert r.accuracy == 0.8

    def test_agent_eval_result_zero_accuracy(self):
        """零决策准确率为 0。"""
        r = AgentEvalResult(agent_name="test", strategy="momentum")
        assert r.accuracy == 0.0

    def test_eval_report_to_dict(self):
        """EvalReport 序列化。"""
        report = AITraderEvalReport(
            start_date="2024-01-01", end_date="2024-06-30",
            symbols=["A"],
            results=[AgentEvalResult(agent_name="test", strategy="momentum")],
        )
        data = report.to_dict()
        assert data["start_date"] == "2024-01-01"
        assert len(data["results"]) == 1
