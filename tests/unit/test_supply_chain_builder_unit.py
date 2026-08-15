"""supply_chain_builder 单元测试.

被测模块: utils/supply_chain_builder.py
覆盖目标: >=90%

测试供应链图构建器: build / _to_edges / analyze / summarize / propagate /
load_positions_symbols / main。graph_data_source (HTTP) 全程 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import utils.supply_chain_builder as scb  # noqa: E402
from utils.supply_chain_builder import (  # noqa: E402
    DEFAULT_SYMBOLS,
    SupplyChainBuilder,
    load_positions_symbols,
    main,
)


@pytest.fixture
def mock_data_source(monkeypatch):
    """mock graph_data_source 单例工厂, 返回 MagicMock 数据源."""
    ds = MagicMock()
    ds.build_graph_edges.return_value = []
    monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
    return ds


# ============================================================
# SupplyChainBuilder
# ============================================================


class SupplyChainBuilderTest:
    """SupplyChainBuilder 测试."""

    # ------ __init__ ------
    def test_init_cleans_symbols(self, mock_data_source):
        b = SupplyChainBuilder(["  600519  ", "", "000001", None])
        # None 会被 str(None)="None" strip 后保留, 但空串被过滤
        assert "600519" in b.symbols
        assert "000001" in b.symbols

    def test_init_strips_and_filters_empty(self, mock_data_source):
        b = SupplyChainBuilder(["  600519  ", "   ", ""])
        assert b.symbols == ["600519"]

    def test_init_max_hops_floor(self, mock_data_source):
        b = SupplyChainBuilder(["X"], max_hops=0)
        assert b.max_hops == 1
        b2 = SupplyChainBuilder(["X"], max_hops=-5)
        assert b2.max_hops == 1

    def test_init_default_flags(self, mock_data_source):
        b = SupplyChainBuilder(["X"])
        assert b.include_default_chains is True
        assert b.include_themes is True
        assert b.max_hops == 2

    def test_init_int_max_hops(self, mock_data_source):
        b = SupplyChainBuilder(["X"], max_hops=3.7)
        assert b.max_hops == 3

    # ------ build: 异常 ------
    def test_build_empty_symbols_raises(self, mock_data_source):
        b = SupplyChainBuilder([], include_default_chains=False)
        with pytest.raises(ValueError, match="symbols 不能为空"):
            b.build()

    # ------ build: 正常 ------
    def test_build_only_default_chains(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        info = b.build()
        assert info["node_count"] > 0
        assert info["edge_count"] > 0
        assert info["real_edges"] == 0
        assert info["default_added"] > 0
        assert info["symbols"] == ["688981"]

    def test_build_without_default_chains(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=False)
        info = b.build()
        assert info["default_added"] == 0
        assert info["real_edges"] == 0

    def test_build_with_real_edges(self, mock_data_source):
        mock_data_source.build_graph_edges.return_value = [
            {"source": "A", "target": "B", "relation_type": "SUPPLIER", "strength": 0.8},
            {"source": "B", "target": "C", "relation_type": "CUSTOMER", "strength": 0.5},
        ]
        b = SupplyChainBuilder(["A", "B", "C"], include_default_chains=False)
        info = b.build()
        assert info["real_edges"] == 2
        assert info["edge_count"] == 2
        assert info["default_added"] == 0

    def test_build_default_chains_skip_existing(self, mock_data_source):
        # 真实边覆盖了某默认边 (688981→300308), 默认链应跳过该边
        # DEFAULT_CHAINS 共 9 边 (compute 3 + nev 3 + semi 3), 跳过 1 → default_added=8
        mock_data_source.build_graph_edges.return_value = [
            {"source": "688981", "target": "300308", "relation_type": "SUPPLIER", "strength": 0.9},
        ]
        b = SupplyChainBuilder(["688981", "300308"], include_default_chains=True)
        info = b.build()
        assert info["real_edges"] == 1
        assert info["default_added"] == 8

    def test_build_include_themes_passed_through(self, mock_data_source):
        b = SupplyChainBuilder(["A"], include_default_chains=False, include_themes=False)
        b.build()
        mock_data_source.build_graph_edges.assert_called_once()
        _, kwargs = mock_data_source.build_graph_edges.call_args
        assert kwargs.get("include_themes") is False

    # ------ _to_edges ------
    def test_to_edges_normal(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [
            {"source": "A", "target": "B", "relation_type": "SUPPLIER", "strength": 0.7},
            {"source": "B", "target": "C", "relation_type": "PARTNER"},
        ]
        edges = b._to_edges(raw)
        assert len(edges) == 2
        assert edges[0].source == "A"
        assert edges[0].strength == 0.7
        assert edges[1].strength == 0.5  # 默认

    def test_to_edges_missing_source_skipped(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [{"target": "B", "relation_type": "X"}]
        assert b._to_edges(raw) == []

    def test_to_edges_missing_target_skipped(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [{"source": "A", "relation_type": "X"}]
        assert b._to_edges(raw) == []

    def test_to_edges_missing_relation_type_skipped(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [{"source": "A", "target": "B"}]
        assert b._to_edges(raw) == []

    def test_to_edges_invalid_strength_skipped(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [{"source": "A", "target": "B", "relation_type": "X", "strength": "abc"}]
        assert b._to_edges(raw) == []

    def test_to_edges_non_dict_skipped(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [None, "string", 123]
        assert b._to_edges(raw) == []

    def test_to_edges_mixed_valid_invalid(self, mock_data_source):
        b = SupplyChainBuilder(["A"])
        raw = [
            {"source": "A", "target": "B", "relation_type": "SUPPLIER"},
            {"target": "B", "relation_type": "X"},  # 缺 source
            None,
            {"source": "C", "target": "D", "relation_type": "PARTNER", "strength": 0.3},
        ]
        edges = b._to_edges(raw)
        assert len(edges) == 2
        assert edges[0].source == "A"
        assert edges[1].source == "C"

    # ------ analyze ------
    def test_analyze_returns_graph_info_and_summary(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        result = b.analyze()
        assert "graph_info" in result
        assert "summary" in result
        assert result["graph_info"]["node_count"] > 0
        assert "total_nodes" in result["summary"]

    # ------ summarize ------
    def test_summarize_returns_string(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        s = b.summarize()
        assert isinstance(s, str)
        assert "GNN 供应链关系图谱摘要" in s
        assert "最大传播跳数" in s

    def test_summarize_contains_hubs_and_bottlenecks_section(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        s = b.summarize()
        assert "[枢纽节点]" in s
        assert "[瓶颈节点]" in s

    def test_summarize_no_high_risk_section_when_empty(self, mock_data_source):
        # 单节点无边 → 无 high_risk
        mock_data_source.build_graph_edges.return_value = []
        b = SupplyChainBuilder(["LONELY"], include_default_chains=False)
        s = b.summarize()
        assert "GNN 供应链关系图谱摘要" in s

    # ------ propagate ------
    def test_propagate_returns_list(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        paths = b.propagate("688981", 1.0)
        assert isinstance(paths, list)

    def test_propagate_unknown_source_returns_empty(self, mock_data_source):
        b = SupplyChainBuilder(["688981"], include_default_chains=True)
        paths = b.propagate("NOT_EXIST", 1.0)
        assert paths == []


# ============================================================
# load_positions_symbols
# ============================================================


class LoadPositionsSymbolsTest:
    """load_positions_symbols 模块函数测试."""

    def test_file_not_exist_returns_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert result == DEFAULT_SYMBOLS

    def test_dict_structure_with_code(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"600519": {"code": "600519"}, "000001": {}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert "600519" in result
        assert "000001" in result

    def test_dict_structure_key_as_code_when_no_code_field(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"600519": {}, "000001": 123}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert "600519" in result
        assert "000001" in result

    def test_dict_structure_meta_skipped(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"meta": {"ver": "1"}, "600519": {}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert "meta" not in result
        assert "600519" in result

    def test_list_structure(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": [{"code": "600519"}, {"symbol": "000001"}, "300750"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert result == ["600519", "000001", "300750"]

    def test_strip_exchange_suffix(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"588080.SH": {}, "159920.SZ": {}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert "588080" in result
        assert "159920" in result
        assert all(".SH" not in r and ".SZ" not in r for r in result)

    def test_dedup(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"positions": {"600519": {}, "600519.SH": {}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert result.count("600519") == 1

    def test_top_level_dict_without_positions_key(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text(
            json.dumps({"600519": {"code": "600519"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert "600519" in result

    def test_invalid_json_returns_default(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "positions.json").write_text("not a json", encoding="utf-8")
        monkeypatch.setattr(scb, "_DIR", tmp_path / "utils")
        result = load_positions_symbols()
        assert result == DEFAULT_SYMBOLS


# ============================================================
# main
# ============================================================


class MainTest:
    """main() CLI 入口测试."""

    def test_main_with_symbols(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "600519,000001"])
        assert main() == 0

    def test_main_no_symbols_uses_positions(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(scb, "load_positions_symbols", lambda: ["600519"])
        monkeypatch.setattr(sys, "argv", ["prog"])
        assert main() == 0

    def test_main_from_positions(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(scb, "load_positions_symbols", lambda: ["600519"])
        monkeypatch.setattr(sys, "argv", ["prog", "--from-positions"])
        assert main() == 0

    def test_main_no_default_no_themes(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "600519", "--no-default", "--no-themes"])
        assert main() == 0

    def test_main_verbose(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "600519", "--verbose"])
        assert main() == 0

    def test_main_propagate(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "600519", "--propagate", "600519"])
        assert main() == 0

    def test_main_max_hops(self, monkeypatch):
        ds = MagicMock()
        ds.build_graph_edges.return_value = []
        monkeypatch.setattr(scb, "get_graph_data_source", lambda: ds)
        monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "600519", "--max-hops", "3"])
        assert main() == 0