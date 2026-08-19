"""D3 单元测试 — KnowledgeBase 知识沉淀库."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.llm_evolution.knowledge_base import KnowledgeBase, KnowledgeEntry

# ============================================================
# 测试夹具
# ============================================================

def _make_kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(path=tmp_path / "kb.jsonl", max_context_entries=10)


def _make_hypothesis(description: str = "低估值因子有效", style: str = "value"):
    from utils.llm_evolution.strategy_ideation import Hypothesis
    h = Hypothesis(
        id="hyp_test_001",
        description=description,
        factor_direction="long_small",
        strategy_style=style,
        llm_model="deepseek-chat",
    )
    h.compute_diversity_hash()
    return h


def _make_verdict(passed: bool = True) -> dict:
    if passed:
        return {
            "factor_name": "EP",
            "rank_ic_mean": 0.05,
            "icir": 0.8,
            "ic_positive_ratio": 0.65,
            "enter_ab_bucket": True,
            "falsified": False,
            "falsified_reason": "",
        }
    return {
        "factor_name": "BAD",
        "rank_ic_mean": 0.01,
        "icir": 0.2,
        "ic_positive_ratio": 0.40,
        "enter_ab_bucket": False,
        "falsified": True,
        "falsified_reason": "IC 不显著",
    }


# ============================================================
# KnowledgeEntry 测试
# ============================================================

class TestKnowledgeEntry:
    def test_to_jsonl_roundtrip(self):
        entry = KnowledgeEntry(
            entry_id="kb_001",
            timestamp="2026-08-12T10:00:00",
            hypothesis_id="hyp_001",
            description="测试假设",
            factor_name="EP",
            status="validated",
            rank_ic_mean=0.05,
        )
        line = entry.to_jsonl()
        data = json.loads(line)
        assert data["entry_id"] == "kb_001"
        assert data["status"] == "validated"

        restored = KnowledgeEntry.from_jsonl(line)
        assert restored is not None
        assert restored.entry_id == "kb_001"
        assert restored.factor_name == "EP"

    def test_from_jsonl_invalid(self):
        assert KnowledgeEntry.from_jsonl("not json") is None
        assert KnowledgeEntry.from_jsonl("") is None


# ============================================================
# 持久化
# ============================================================

class TestPersist:
    def test_persist_validated(self, tmp_path):
        kb = _make_kb(tmp_path)
        hyp = _make_hypothesis()
        verdict = _make_verdict(passed=True)
        entry = kb.persist(hyp, verdict)
        assert entry.status == "validated"
        assert entry.factor_name == "EP"
        assert entry.rank_ic_mean == 0.05

    def test_persist_falsified(self, tmp_path):
        kb = _make_kb(tmp_path)
        hyp = _make_hypothesis()
        verdict = _make_verdict(passed=False)
        entry = kb.persist(hyp, verdict)
        assert entry.status == "falsified"
        assert "IC 不显著" in entry.falsified_reason

    def test_persist_writes_to_file(self, tmp_path):
        kb = _make_kb(tmp_path)
        hyp = _make_hypothesis()
        kb.persist(hyp, _make_verdict())
        assert (tmp_path / "kb.jsonl").exists()
        lines = (tmp_path / "kb.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_persist_multiple(self, tmp_path):
        kb = _make_kb(tmp_path)
        for i in range(5):
            hyp = _make_hypothesis(description=f"假设{i}")
            kb.persist(hyp, _make_verdict(passed=i % 2 == 0))
        entries = kb.load_all()
        assert len(entries) == 5

    def test_persist_entry_directly(self, tmp_path):
        kb = _make_kb(tmp_path)
        entry = KnowledgeEntry(entry_id="kb_001", description="直接写入")
        kb.persist_entry(entry)
        entries = kb.load_all()
        assert len(entries) == 1
        assert entries[0].entry_id == "kb_001"


# ============================================================
# 查询
# ============================================================

class TestQuery:
    def test_query_by_status(self, tmp_path):
        kb = _make_kb(tmp_path)
        kb.persist(_make_hypothesis("A"), _make_verdict(True))
        kb.persist(_make_hypothesis("B"), _make_verdict(False))
        validated = kb.query(status="validated")
        falsified = kb.query(status="falsified")
        assert len(validated) == 1
        assert len(falsified) == 1

    def test_query_by_style(self, tmp_path):
        kb = _make_kb(tmp_path)
        kb.persist(_make_hypothesis("A", style="value"), _make_verdict(True))
        kb.persist(_make_hypothesis("B", style="momentum"), _make_verdict(True))
        value_only = kb.query(strategy_style="value")
        assert len(value_only) == 1

    def test_query_by_factor_name(self, tmp_path):
        kb = _make_kb(tmp_path)
        kb.persist(_make_hypothesis(), _make_verdict(True))
        result = kb.query(factor_name="EP")
        assert len(result) == 1
        result = kb.query(factor_name="NONEXIST")
        assert len(result) == 0

    def test_query_empty_kb(self, tmp_path):
        kb = _make_kb(tmp_path)
        assert kb.query(status="validated") == []
        assert kb.load_all() == []


# ============================================================
# LLM 上下文反馈
# ============================================================

class TestLoadContext:
    def test_context_with_entries(self, tmp_path):
        kb = _make_kb(tmp_path)
        kb.persist(_make_hypothesis("低估值因子"), _make_verdict(True))
        kb.persist(_make_hypothesis("动量因子"), _make_verdict(False))
        ctx = kb.load_context_for_ideation()
        assert "已验证假设" in ctx
        assert "已证伪假设" in ctx
        assert "低估值因子" in ctx

    def test_context_empty(self, tmp_path):
        kb = _make_kb(tmp_path)
        ctx = kb.load_context_for_ideation()
        assert ctx == ""

    def test_context_max_entries(self, tmp_path):
        kb = KnowledgeBase(path=tmp_path / "kb.jsonl", max_context_entries=3)
        for i in range(10):
            kb.persist(_make_hypothesis(f"假设{i}"), _make_verdict(True))
        ctx = kb.load_context_for_ideation()
        # 最多 3 条已验证
        lines = [line for line in ctx.splitlines() if line.startswith("  -")]
        assert len(lines) <= 3


# ============================================================
# 统计
# ============================================================

class TestStats:
    def test_stats_empty(self, tmp_path):
        kb = _make_kb(tmp_path)
        stats = kb.stats()
        assert stats["total"] == 0

    def test_stats_with_entries(self, tmp_path):
        kb = _make_kb(tmp_path)
        kb.persist(_make_hypothesis("A", style="value"), _make_verdict(True))
        kb.persist(_make_hypothesis("B", style="momentum"), _make_verdict(False))
        kb.persist(_make_hypothesis("C", style="value"), _make_verdict(True))
        stats = kb.stats()
        assert stats["total"] == 3
        assert stats["validated"] == 2
        assert stats["falsified"] == 1
        assert stats["validation_rate"] == pytest.approx(2/3)
        assert stats["by_style"]["value"] == 2
        assert stats["by_style"]["momentum"] == 1
        assert stats["unique_factors"] >= 1


# ============================================================
# 归因与教训
# ============================================================

class TestAttribution:
    def test_attribution_validated(self, tmp_path):
        kb = _make_kb(tmp_path)
        hyp = _make_hypothesis()
        entry = kb.persist(hyp, _make_verdict(True))
        assert "验证成功" in entry.attribution
        assert "可扩展" in entry.lessons

    def test_attribution_falsified(self, tmp_path):
        kb = _make_kb(tmp_path)
        hyp = _make_hypothesis()
        entry = kb.persist(hyp, _make_verdict(False))
        assert "验证失败" in entry.attribution
        assert "IC 不显著" in entry.lessons or "不显著" in entry.lessons
