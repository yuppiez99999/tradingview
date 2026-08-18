# -*- coding: utf-8 -*-
"""graph_data_source 单元测试 — GNN 图数据源边构建"""
from unittest.mock import patch, MagicMock

import pytest

from utils.graph_data_source import (
    GraphDataSource,
    _safe_float,
    _market_of,
    get_graph_data_source,
)


class TestSafeFloat:
    def test_normal(self):
        assert _safe_float(3.14) == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_string(self):
        assert _safe_float("1.5") == 1.5

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_empty_string(self):
        assert _safe_float("") == 0.0

    def test_invalid(self):
        assert _safe_float("abc") == 0.0

    def test_nan(self):
        assert _safe_float(float("nan")) == 0.0

    def test_custom_default(self):
        assert _safe_float(float("nan"), default=-1.0) == -1.0

    def test_zero_string(self):
        assert _safe_float("0") == 0.0


class TestMarketOf:
    def test_sh_6xx(self):
        assert _market_of("600519") == "1"

    def test_sh_5xx(self):
        assert _market_of("510300") == "1"

    def test_sh_9xx(self):
        assert _market_of("900001") == "1"

    def test_sz_0xx(self):
        assert _market_of("000001") == "0"

    def test_sz_3xx(self):
        assert _market_of("300750") == "0"

    def test_bj_4xx(self):
        assert _market_of("430047") == "0"

    def test_bj_8xx(self):
        assert _market_of("830799") == "0"

    def test_with_whitespace(self):
        assert _market_of("  600519  ") == "1"


class TestGraphDataSourceInit:
    def test_defaults(self):
        ds = GraphDataSource()
        assert ds.cache_ttl == 3600
        assert ds._cache == {}
        assert "eastmoney_push2" in ds.source_health
        assert "ths_hot_reason" in ds.source_health

    def test_custom_ttl(self):
        ds = GraphDataSource(cache_ttl=600)
        assert ds.cache_ttl == 600

    def test_session_created(self):
        ds = GraphDataSource()
        assert ds._session is not None


class TestCached:
    def test_cache_miss(self):
        ds = GraphDataSource()
        fetcher = MagicMock(return_value="value")
        result = ds._cached("key1", fetcher)
        assert result == "value"
        fetcher.assert_called_once()

    def test_cache_hit(self):
        ds = GraphDataSource()
        fetcher = MagicMock(return_value="value")
        ds._cached("key1", fetcher)
        fetcher.reset_mock()
        result = ds._cached("key1", fetcher)
        assert result == "value"
        fetcher.assert_not_called()

    def test_none_not_cached(self):
        ds = GraphDataSource()
        fetcher = MagicMock(return_value=None)
        result = ds._cached("key1", fetcher)
        assert result is None
        fetcher.reset_mock()
        ds._cached("key1", fetcher)
        fetcher.assert_called_once()

    def test_ttl_expiry(self):
        ds = GraphDataSource(cache_ttl=0)
        import time
        fetcher = MagicMock(side_effect=["v1", "v2"])
        r1 = ds._cached("key1", fetcher)
        time.sleep(0.01)
        r2 = ds._cached("key1", fetcher)
        assert r1 == "v1"
        assert r2 == "v2"


class TestBuildConceptEdges:
    def test_no_concepts(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_concept_blocks", return_value=[]):
            edges = ds.build_concept_edges(["A", "B"])
        assert edges == []

    def test_shared_concepts(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_concept_blocks", side_effect=[
            ["AI", "芯片", "科技"], ["AI", "芯片", "新能源"]
        ]):
            edges = ds.build_concept_edges(["A", "B"])
        assert len(edges) == 1
        assert edges[0]["source"] == "A"
        assert edges[0]["target"] == "B"
        assert edges[0]["relation_type"] == "PARTNER"
        assert "concept_shared" in edges[0]["source_info"]

    def test_no_shared_concepts(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_concept_blocks", side_effect=[
            ["AI", "芯片"], ["新能源", "光伏"]
        ]):
            edges = ds.build_concept_edges(["A", "B"])
        assert edges == []

    def test_min_concept_share(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_concept_blocks", side_effect=[
            ["AI"], ["AI"]
        ]):
            edges = ds.build_concept_edges(["A", "B"], min_concept_share=2)
        assert edges == []

    def test_three_symbols(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_concept_blocks", side_effect=[
            ["AI", "芯片"], ["AI", "光伏"], ["AI", "芯片"]
        ]):
            edges = ds.build_concept_edges(["A", "B", "C"])
        assert len(edges) == 3


class TestBuildIndustryEdges:
    def test_same_industry(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_industry_relationship", side_effect=[
            {"industry": "半导体"}, {"industry": "半导体"}
        ]):
            edges = ds.build_industry_edges(["A", "B"])
        assert len(edges) == 1
        assert edges[0]["relation_type"] == "COMPETITOR"
        assert edges[0]["strength"] == 0.7

    def test_different_industry(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_industry_relationship", side_effect=[
            {"industry": "半导体"}, {"industry": "银行"}
        ]):
            edges = ds.build_industry_edges(["A", "B"])
        assert edges == []

    def test_no_industry(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_industry_relationship", side_effect=[None, None]):
            edges = ds.build_industry_edges(["A", "B"])
        assert edges == []

    def test_empty_industry(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_industry_relationship", side_effect=[
            {"industry": ""}, {"industry": ""}
        ]):
            edges = ds.build_industry_edges(["A", "B"])
        assert edges == []


class TestBuildThematicEdges:
    def test_no_themes(self):
        ds = GraphDataSource()
        with patch.object(ds, "get_themes", return_value=[]):
            edges = ds.build_thematic_edges()
        assert edges == []

    def test_shared_theme(self):
        ds = GraphDataSource()
        themes = [
            {"code": "A", "reason": "AI+芯片"},
            {"code": "B", "reason": "AI+光伏"},
        ]
        with patch.object(ds, "get_themes", return_value=themes):
            edges = ds.build_thematic_edges()
        assert len(edges) >= 1
        assert any(e["source_info"] == "theme:AI" for e in edges)

    def test_single_stock_no_edge(self):
        ds = GraphDataSource()
        themes = [{"code": "A", "reason": "AI"}]
        with patch.object(ds, "get_themes", return_value=themes):
            edges = ds.build_thematic_edges()
        assert edges == []


class TestBuildMainBusinessEdges:
    def test_no_data(self):
        ds = GraphDataSource()
        with patch.object(ds, "fetch_main_business", return_value=None):
            edges = ds.build_main_business_edges(["A", "B"])
        assert edges == []

    def test_shared_products(self):
        ds = GraphDataSource()
        with patch.object(ds, "fetch_main_business", side_effect=[
            {"products": ["芯片设计", "EDA"], "industries": []},
            {"products": ["芯片设计", "封测"], "industries": []},
        ]):
            edges = ds.build_main_business_edges(["A", "B"])
        assert len(edges) == 1
        assert edges[0]["relation_type"] == "PARTNER"

    def test_no_shared(self):
        ds = GraphDataSource()
        with patch.object(ds, "fetch_main_business", side_effect=[
            {"products": ["芯片"], "industries": []},
            {"products": ["银行"], "industries": []},
        ]):
            edges = ds.build_main_business_edges(["A", "B"])
        assert edges == []


class TestBuildGraphEdges:
    def test_combines_all(self):
        ds = GraphDataSource()
        with patch.object(ds, "build_industry_edges", return_value=[{"s": 1}]), \
             patch.object(ds, "build_concept_edges", return_value=[{"s": 2}]), \
             patch.object(ds, "build_main_business_edges", return_value=[{"s": 3}]), \
             patch.object(ds, "build_thematic_edges", return_value=[{"s": 4}]):
            edges = ds.build_graph_edges(["A", "B"])
        assert len(edges) == 4

    def test_exclude_themes(self):
        ds = GraphDataSource()
        with patch.object(ds, "build_industry_edges", return_value=[{"s": 1}]), \
             patch.object(ds, "build_concept_edges", return_value=[{"s": 2}]), \
             patch.object(ds, "build_main_business_edges", return_value=[{"s": 3}]), \
             patch.object(ds, "build_thematic_edges", return_value=[{"s": 4}]):
            edges = ds.build_graph_edges(["A", "B"], include_themes=False)
        assert len(edges) == 3

    def test_exclude_main_business(self):
        ds = GraphDataSource()
        with patch.object(ds, "build_industry_edges", return_value=[{"s": 1}]), \
             patch.object(ds, "build_concept_edges", return_value=[{"s": 2}]), \
             patch.object(ds, "build_main_business_edges", return_value=[{"s": 3}]), \
             patch.object(ds, "build_thematic_edges", return_value=[{"s": 4}]):
            edges = ds.build_graph_edges(["A", "B"], include_main_business=False)
        assert len(edges) == 3


class TestGetGraphDataSource:
    def test_singleton(self):
        ds1 = get_graph_data_source()
        ds2 = get_graph_data_source()
        assert ds1 is ds2

    def test_returns_graph_data_source(self):
        ds = get_graph_data_source()
        assert isinstance(ds, GraphDataSource)