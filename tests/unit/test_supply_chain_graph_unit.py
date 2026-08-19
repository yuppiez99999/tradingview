"""Supply Chain Graph 单元测试.

被测模块: utils/supply_chain_graph.py
覆盖目标: >=85%
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.supply_chain_graph import (  # noqa: E402
    SupplyChainEdge,
    SupplyChainGraph,
    SupplyChainResult,
)


def _make_edge(source="A", target="B", rtype="SUPPLIER", strength=0.5, **kwargs):
    return SupplyChainEdge(source=source, target=target, relation_type=rtype, strength=strength, **kwargs)


class TestSupplyChainEdge:
    def test_default_values(self):
        e = _make_edge()
        assert e.source == "A"
        assert e.target == "B"
        assert e.relation_type == "SUPPLIER"
        assert e.strength == 0.5
        assert e.revenue_share == 0.0
        assert e.up_elasticity is None
        assert e.down_elasticity is None

    def test_with_elasticity(self):
        e = _make_edge(up_elasticity=0.8, down_elasticity=0.3)
        assert e.up_elasticity == 0.8
        assert e.down_elasticity == 0.3


class TestSupplyChainGraphInit:
    def test_default_init(self):
        g = SupplyChainGraph()
        assert g.decay_per_hop == 0.6
        assert g.max_hops == 4
        assert g.hub_threshold == 5
        assert len(g.all_nodes) == 0

    def test_custom_params(self):
        g = SupplyChainGraph(decay_per_hop=0.5, max_propagation_hops=3)
        assert g.decay_per_hop == 0.5
        assert g.max_hops == 3


class TestAddEdge:
    def test_single_edge(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        assert "A" in g.all_nodes
        assert "B" in g.all_nodes
        assert len(g.adjacency["A"]) == 1

    def test_multiple_edges(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "C"))
        assert len(g.all_nodes) == 3
        assert len(g.adjacency["A"]) == 1
        assert len(g.adjacency["B"]) == 1

    def test_add_edges_batch(self):
        g = SupplyChainGraph()
        edges = [_make_edge("A", "B"), _make_edge("B", "C"), _make_edge("C", "D")]
        count = g.add_edges(edges)
        assert count == 3
        assert len(g.all_nodes) == 4

    def test_load_default_chains(self):
        g = SupplyChainGraph()
        count = g.load_default_chains()
        assert count > 0
        assert len(g.all_nodes) > 0


class TestPropagateImpact:
    def test_unknown_source(self):
        g = SupplyChainGraph()
        assert g.propagate_impact("UNKNOWN") == []

    def test_single_hop(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.8))
        paths = g.propagate_impact("A", max_hops=1)
        assert len(paths) == 1
        assert paths[0].target == "B"
        assert paths[0].hops == 1

    def test_multi_hop(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.8))
        g.add_edge(_make_edge("B", "C", strength=0.7))
        paths = g.propagate_impact("A", max_hops=3)
        assert len(paths) >= 2
        targets = [p.target for p in paths]
        assert "B" in targets
        assert "C" in targets

    def test_decay(self):
        g = SupplyChainGraph(decay_per_hop=0.5)
        g.add_edge(_make_edge("A", "B", strength=1.0))
        paths = g.propagate_impact("A", max_hops=1)
        assert paths[0].total_strength == 0.5

    def test_visited_no_cycles(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "A"))
        paths = g.propagate_impact("A", max_hops=5)
        targets = [p.target for p in paths]
        assert "A" not in targets


class TestPropagateAsymmetricImpact:
    def test_up_direction(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", up_elasticity=0.9, down_elasticity=0.2))
        paths = g.propagate_asymmetric_impact("A", direction="up", max_hops=1)
        assert len(paths) == 1

    def test_down_direction(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", up_elasticity=0.9, down_elasticity=0.2))
        paths = g.propagate_asymmetric_impact("A", direction="down", max_hops=1)
        assert len(paths) == 1

    def test_invalid_direction(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        with pytest.raises(ValueError):
            g.propagate_asymmetric_impact("A", direction="invalid")

    def test_fallback_to_strength(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.6))
        paths = g.propagate_asymmetric_impact("A", direction="up", max_hops=1)
        assert len(paths) == 1


class TestGetAffectedSymbols:
    def test_no_affected(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.01))
        affected = g.get_affected_symbols("A", impact_threshold=0.5)
        assert len(affected) == 0

    def test_with_affected(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.9))
        affected = g.get_affected_symbols("A", impact_threshold=0.01)
        assert "B" in affected


class TestComputeCentrality:
    def test_empty_graph(self):
        g = SupplyChainGraph()
        metrics = g.compute_centrality()
        assert metrics == {}

    def test_simple_graph(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "C"))
        metrics = g.compute_centrality()
        assert "A" in metrics
        assert "B" in metrics
        assert "C" in metrics
        assert metrics["B"].in_degree >= 1
        assert metrics["B"].out_degree >= 1


class TestAssessRiskContagion:
    def test_empty_shocks(self):
        g = SupplyChainGraph()
        result = g.assess_risk_contagion({})
        assert isinstance(result, dict)

    def test_single_shock(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", strength=0.8))
        result = g.assess_risk_contagion({"A": 1.0})
        assert "A" in result
        assert result["A"] >= 1.0


class TestAnalyze:
    def test_empty_graph(self):
        g = SupplyChainGraph()
        result = g.analyze()
        assert isinstance(result, SupplyChainResult)
        assert result.nodes == []

    def test_simple_graph(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "C"))
        result = g.analyze()
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
        assert result.num_components >= 1


class TestFindPath:
    def test_no_path(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        assert g.find_path("B", "A") is None

    def test_direct_path(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        path = g.find_path("A", "B")
        assert path == ["A", "B"]

    def test_multi_hop_path(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "C"))
        path = g.find_path("A", "C")
        assert path == ["A", "B", "C"]

    def test_same_node(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        path = g.find_path("A", "A")
        assert path == ["A"]


class TestGetRelations:
    def test_no_relations(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B"))
        rels = g.get_relations("C")
        assert isinstance(rels, dict)

    def test_with_relations(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", rtype="SUPPLIER"))
        g.add_edge(_make_edge("C", "A", rtype="CUSTOMER"))
        rels = g.get_relations("A")
        assert isinstance(rels, dict)


class TestSummarize:
    def test_empty_graph(self):
        g = SupplyChainGraph()
        summary = g.summarize()
        assert isinstance(summary, dict)

    def test_with_data(self):
        g = SupplyChainGraph()
        g.load_default_chains()
        summary = g.summarize()
        assert isinstance(summary, dict)


class TestGetAsymmetryRatio:
    def test_unknown_source(self):
        g = SupplyChainGraph()
        result = g.get_asymmetry_ratio("UNKNOWN")
        assert isinstance(result, dict)

    def test_normal(self):
        g = SupplyChainGraph()
        g.add_edge(_make_edge("A", "B", up_elasticity=0.9, down_elasticity=0.3))
        result = g.get_asymmetry_ratio("A")
        assert isinstance(result, dict)
