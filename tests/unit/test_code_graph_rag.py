"""
CodeGraphRAG 单元测试
=====================

测试 W.A.2 新增的代码库知识图谱 RAG 封装:
- 符号检索 (search_symbol)
- 调用关系 (find_callers / find_callees / find_importers / find_inheritors)
- 影响半径分析 (impact_analysis)
- 统计信息 (stats)
- 连接管理 (close / context manager)

使用真实 graph.db (只读, 不破坏数据). graph.db 不存在时 skip.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.ai_tools.code_graph_rag import (  # noqa: E402
    CodeGraphRAG,
    EdgeInfo,
    ImpactResult,
    SymbolLocation,
    get_code_graph_rag,
)

_DB_PATH = _PROJECT_ROOT / ".code-review-graph" / "graph.db"
_HAS_DB = _DB_PATH.exists()

pytestmark = pytest.mark.skipif(not _HAS_DB, reason=f"graph.db 不存在: {_DB_PATH}")


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def rag():
    """CodeGraphRAG 实例"""
    r = CodeGraphRAG(str(_DB_PATH))
    yield r
    r.close()


# ============================================================
# 测试组 1: 连接与统计
# ============================================================


class TestConnection:
    def test_stats(self, rag):
        s = rag.stats()
        assert s["node_count"] > 0
        assert s["edge_count"] > 0
        assert "Function" in s["node_kind_distribution"]
        assert "CALLS" in s["edge_kind_distribution"]

    def test_context_manager(self):
        with CodeGraphRAG(str(_DB_PATH)) as r:
            s = r.stats()
            assert s["node_count"] > 0

    def test_singleton(self):
        r1 = get_code_graph_rag()
        r2 = get_code_graph_rag()
        assert r1 is r2


# ============================================================
# 测试组 2: 符号检索
# ============================================================


class TestSearchSymbol:
    def test_search_by_name(self, rag):
        locs = rag.search_symbol("AlphaHedgeEngine")
        assert len(locs) > 0
        assert any(loc.name == "AlphaHedgeEngine" for loc in locs)

    def test_search_with_kind_filter(self, rag):
        locs = rag.search_symbol("AlphaHedgeEngine", kind="Class")
        assert len(locs) > 0
        assert all(loc.kind == "Class" for loc in locs)

    def test_search_empty_name(self, rag):
        assert rag.search_symbol("") == []

    def test_search_no_match(self, rag):
        locs = rag.search_symbol("ZZZNoSuchSymbolXYZ123")
        assert locs == []

    def test_search_limit(self, rag):
        locs = rag.search_symbol("test", limit=5)
        assert len(locs) <= 5

    def test_get_node_detail(self, rag):
        locs = rag.search_symbol("AlphaHedgeEngine", kind="Class")
        if locs:
            detail = rag.get_node_detail(locs[0].qualified_name)
            assert detail is not None
            assert detail.qualified_name == locs[0].qualified_name

    def test_get_node_detail_not_found(self, rag):
        assert rag.get_node_detail("nonexistent::QualifiedName") is None

    def test_symbol_to_dict(self, rag):
        locs = rag.search_symbol("AlphaHedgeEngine", kind="Class", limit=1)
        if locs:
            d = locs[0].to_dict()
            assert d["name"] == "AlphaHedgeEngine"
            assert "file_path" in d


# ============================================================
# 测试组 3: 调用关系
# ============================================================


class TestCallRelations:
    def test_find_callers(self, rag):
        callers = rag.find_callers("AlphaHedgeEngine")
        assert isinstance(callers, list)
        for c in callers:
            assert c.kind == "CALLS"
            assert "AlphaHedgeEngine" in c.target_qualified

    def test_find_callees(self, rag):
        callees = rag.find_callees("AlphaHedgeEngine")
        assert isinstance(callees, list)
        for c in callees:
            assert c.kind == "CALLS"

    def test_find_importers(self, rag):
        importers = rag.find_importers("signal_fusion")
        assert isinstance(importers, list)

    def test_find_inheritors(self, rag):
        inh = rag.find_inheritors("SignalFusionEngine")
        assert isinstance(inh, list)

    def test_empty_name(self, rag):
        assert rag.find_callers("") == []
        assert rag.find_callees("") == []

    def test_edge_to_dict(self, rag):
        callers = rag.find_callers("AlphaHedgeEngine", limit=1)
        if callers:
            d = callers[0].to_dict()
            assert d["kind"] == "CALLS"
            assert "source_qualified" in d


# ============================================================
# 测试组 4: 影响半径分析
# ============================================================


class TestImpactAnalysis:
    def test_impact_signal_fusion(self, rag):
        result = rag.impact_analysis("utils/signal_fusion.py")
        assert isinstance(result, ImpactResult)
        assert "utils/signal_fusion.py" in result.changed_files
        assert result.impact_radius >= 0

    def test_impact_empty_path(self, rag):
        result = rag.impact_analysis("")
        assert result.changed_files == [""]
        assert result.impact_radius == 0

    def test_impact_nonexistent_file(self, rag):
        result = rag.impact_analysis("ZZZNoSuchFileXYZ.py")
        assert result.changed_symbols == []
        assert result.impact_radius == 0

    def test_impact_to_dict(self, rag):
        result = rag.impact_analysis("utils/signal_fusion.py")
        d = result.to_dict()
        assert "impact_radius" in d
        assert "impacted_files" in d
        assert d["impact_radius"] == result.impact_radius

    def test_impact_depth_control(self, rag):
        r1 = rag.impact_analysis("utils/signal_fusion.py", max_depth=1)
        r2 = rag.impact_analysis("utils/signal_fusion.py", max_depth=2)
        assert r2.impact_radius >= r1.impact_radius


# ============================================================
# 测试组 5: 数据类
# ============================================================


class TestDataclasses:
    def test_symbol_location_fields(self):
        s = SymbolLocation(
            kind="Function",
            name="foo",
            qualified_name="path::foo",
            file_path="path.py",
            line_start=10,
            line_end=20,
        )
        assert s.kind == "Function"
        assert s.is_test is False

    def test_edge_info_fields(self):
        e = EdgeInfo(
            kind="CALLS",
            source_qualified="a::foo",
            target_qualified="b::bar",
            file_path="a.py",
            line=5,
        )
        assert e.confidence == 1.0

    def test_impact_result_default(self):
        r = ImpactResult()
        assert r.impact_radius == 0
        assert r.changed_files == []
