"""external_strategy_adapter 单元测试 — 外部策略适配器"""

from pathlib import Path
from unittest.mock import patch

from utils.external_strategy_adapter import (
    ExternalStrategyAdapter,
    get_adapter,
)


class TestExternalStrategyAdapterInit:
    def test_nonexistent_dir(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        assert adapter._strategies == {}

    def test_available_strategies_empty(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        assert adapter.available_strategies == []


class TestAnalyze:
    def test_strategy_not_found(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        result = adapter.analyze("600519.SH", strategy="unknown")
        assert result["direction"] == "neutral"
        assert result["confidence"] == 0.0
        assert result["symbol"] == "600519.SH"

    def test_strategy_exists_no_llm(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        adapter._strategies = {"test": {"name": "test", "instructions": "do something"}}
        with patch("utils.external_strategy_adapter._LLM_AVAILABLE", False):
            result = adapter.analyze("600519.SH", strategy="test")
        assert result["direction"] == "neutral"


class TestAnalyzeAll:
    def test_empty(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        result = adapter.analyze_all("600519.SH")
        assert result == []

    def test_multiple_strategies(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        adapter._strategies = {"s1": {"name": "s1"}, "s2": {"name": "s2"}}
        result = adapter.analyze_all("600519.SH")
        assert len(result) == 2


class TestGetConsensus:
    def test_no_signals(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        result = adapter.get_consensus("600519.SH")
        assert result["direction"] == "neutral"

    def test_bullish_consensus(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        adapter._strategies = {"s1": {"name": "s1"}, "s2": {"name": "s2"}}
        with patch.object(
            adapter,
            "analyze_all",
            return_value=[
                {"direction": "bullish", "confidence": 0.8, "score": 50},
                {"direction": "bullish", "confidence": 0.7, "score": 40},
            ],
        ):
            result = adapter.get_consensus("600519.SH")
        assert result["direction"] == "bullish"
        assert result["bull_count"] == 2

    def test_bearish_consensus(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        with patch.object(
            adapter,
            "analyze_all",
            return_value=[
                {"direction": "bearish", "confidence": 0.8, "score": -50},
                {"direction": "bullish", "confidence": 0.6, "score": 30},
                {"direction": "bearish", "confidence": 0.7, "score": -40},
            ],
        ):
            result = adapter.get_consensus("600519.SH")
        assert result["direction"] == "bearish"

    def test_neutral_consensus(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        with patch.object(
            adapter,
            "analyze_all",
            return_value=[
                {"direction": "bullish", "confidence": 0.8, "score": 50},
                {"direction": "bearish", "confidence": 0.8, "score": -50},
            ],
        ):
            result = adapter.get_consensus("600519.SH")
        assert result["direction"] == "neutral"


class TestBuildPrompt:
    def test_basic(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        strat_def = {
            "name": "test",
            "display_name": "测试策略",
            "instructions": "分析规则",
        }
        system, user = adapter._build_prompt("600519.SH", strat_def, None)
        assert "测试策略" in system
        assert "600519.SH" in user
        assert "分析规则" in user

    def test_with_market_data(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        strat_def = {"name": "test", "instructions": "规则"}
        system, user = adapter._build_prompt(
            "A", strat_def, {"close": 10.0, "volume": 1000}
        )
        assert "close" in user


class TestParseLLMResponse:
    def test_valid_json(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        response = '{"direction": "bullish", "confidence": 0.8, "score": 60, "reasoning": "看涨"}'
        result = adapter._parse_llm_response("A", "test", response)
        assert result["direction"] == "bullish"
        assert result["confidence"] == 0.8
        assert result["score"] == 60

    def test_invalid_json(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        result = adapter._parse_llm_response("A", "test", "not json at all")
        assert result["direction"] == "neutral"

    def test_partial_json(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        response = '一些文字 {"direction": "bearish", "confidence": 0.6} 更多文字'
        result = adapter._parse_llm_response("A", "test", response)
        assert result["direction"] == "bearish"


class TestNeutralSignal:
    def test_basic(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        result = adapter._neutral_signal("A", "test", "原因")
        assert result["direction"] == "neutral"
        assert result["confidence"] == 0.0
        assert result["score"] == 0
        assert result["reasoning"] == "原因"


class TestGetStatus:
    def test_basic(self):
        adapter = ExternalStrategyAdapter(strategy_dir=Path("/nonexistent"))
        status = adapter.get_status()
        assert "strategy_count" in status
        assert "strategies" in status
        assert "llm_available" in status


class TestGetAdapter:
    def test_singleton(self):
        a1 = get_adapter()
        a2 = get_adapter()
        assert a1 is a2
