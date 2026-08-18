"""etf_flow_decision 单元测试 — ETF资金流向决策模块."""

from __future__ import annotations

from utils.etf_flow_decision import (
    ETFFlowDecisionEngine,
    INTRADAY_END,
    INTRADAY_START,
    POST_MARKET_END,
    POST_MARKET_START,
    PRE_MARKET_END,
    PRE_MARKET_START,
    SIGNAL_CONFIG,
)


class TestConstants:
    def test_time_windows(self):
        assert PRE_MARKET_START == "09:15"
        assert PRE_MARKET_END == "09:25"
        assert INTRADAY_START == "09:30"
        assert INTRADAY_END == "15:00"
        assert POST_MARKET_START == "15:00"
        assert POST_MARKET_END == "15:30"

    def test_signal_config(self):
        assert SIGNAL_CONFIG["strong_inflow"] == 5.0
        assert SIGNAL_CONFIG["medium_inflow"] == 2.0
        assert SIGNAL_CONFIG["weak_inflow"] == 0.5
        assert SIGNAL_CONFIG["llm_weight"] == 0.3
        assert SIGNAL_CONFIG["flow_weight"] == 0.5
        assert SIGNAL_CONFIG["price_weight"] == 0.2
        assert SIGNAL_CONFIG["min_confidence"] == 0.4


class TestParseEtfFlowData:
    def test_empty(self):
        engine = ETFFlowDecisionEngine()
        result = engine._parse_etf_flow_data({})
        assert result == {}

    def test_strong_inflow(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "510300": {"net_flow_yi": 10.0, "source": "wind_mcp", "name": "沪深300", "change_pct": 1.5},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert "510300" in result
        assert result["510300"]["strength"] == 1.0

    def test_strong_outflow(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "510300": {"net_flow_yi": -10.0, "source": "wind_mcp", "name": "沪深300", "change_pct": -1.5},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["510300"]["strength"] == -1.0

    def test_medium_inflow(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "510300": {"net_flow_yi": 3.0, "source": "eastmoney_push2"},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["510300"]["strength"] == 0.6

    def test_weak_inflow(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "510300": {"net_flow_yi": 1.0, "source": "sina_http"},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["510300"]["strength"] == 0.3

    def test_neutral(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "510300": {"net_flow_yi": 0.1, "source": "price_momentum"},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["510300"]["strength"] == 0.0

    def test_source_confidence(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "A": {"net_flow_yi": 10.0, "source": "wind_mcp"},
            "B": {"net_flow_yi": 10.0, "source": "eastmoney_push2"},
            "C": {"net_flow_yi": 10.0, "source": "sina_http"},
            "D": {"net_flow_yi": 10.0, "source": "price_momentum"},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["A"]["source_confidence"] == 0.9
        assert result["B"]["source_confidence"] == 0.8
        assert result["C"]["source_confidence"] == 0.7
        assert result["D"]["source_confidence"] == 0.5

    def test_unknown_source(self):
        engine = ETFFlowDecisionEngine()
        flow_data = {
            "A": {"net_flow_yi": 10.0, "source": "unknown_source"},
        }
        result = engine._parse_etf_flow_data(flow_data)
        assert result["A"]["source_confidence"] == 0.6


class TestEngineInit:
    def test_basic(self):
        engine = ETFFlowDecisionEngine()
        assert engine._local_llm_client is None
        assert engine._cache_ttl == 300
        assert engine._decision_cache == {}

    def test_tracker_loaded(self):
        engine = ETFFlowDecisionEngine()
        assert engine.tracker is not None

    def test_fusion_engine_loaded(self):
        engine = ETFFlowDecisionEngine()
        assert engine.fusion_engine is not None


class TestCallLlmAnalysis:
    def test_llm_none_returns_none(self):
        engine = ETFFlowDecisionEngine()
        from unittest.mock import patch
        with patch.object(engine, "_get_llm_client", return_value=None):
            result = engine._call_llm_analysis("test prompt")
        assert result is None