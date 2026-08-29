"""
G7 Coverage Boost: utils/ifind_news_analyzer.py (169 lines, 0% -> target ~80%)
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from utils.ifind_news_analyzer import IFinDNewsAnalyzer, NewsItem, StockInsight


def _ok_data(results):
    return {"ok": True, "data": {"results": results}}


def _ok_result(result):
    return {"ok": True, "data": {"result": result}}


class TestNewsItem:
    def test_default_values(self):
        item = NewsItem(
            title="title",
            snippet="snippet",
            source="source",
            publish_time="2026-01-01",
        )
        assert item.url is None
        assert item.sentiment == "neutral"
        assert item.relevance == 0.0
        assert item.entities == []

    def test_custom_values(self):
        item = NewsItem(
            title="t",
            snippet="s",
            source="src",
            publish_time="now",
            url="http://example.com",
            sentiment="positive",
            relevance=0.9,
            entities=["000001.SZ"],
        )
        assert item.url == "http://example.com"
        assert item.sentiment == "positive"
        assert item.relevance == 0.9
        assert item.entities == ["000001.SZ"]


class TestStockInsight:
    def test_default_values(self):
        insight = StockInsight(
            symbol="000001.SZ", name="平安银行", direction="positive", confidence=0.8
        )
        assert insight.reasons == []
        assert insight.news_count == 0
        assert isinstance(insight.updated_at, str)

    def test_custom_values(self):
        insight = StockInsight(
            symbol="000002.SZ",
            name="万科A",
            direction="negative",
            confidence=0.3,
            reasons=["利空"],
            news_count=2,
            updated_at="2026-01-01T00:00:00",
        )
        assert insight.reasons == ["利空"]
        assert insight.news_count == 2
        assert insight.updated_at == "2026-01-01T00:00:00"


class TestIFinDNewsAnalyzerInit:
    def test_call_available(self):
        with patch("utils.ifind_news_analyzer.sys"):
            mock_call = MagicMock()
            with patch.dict("sys.modules", {"call": MagicMock(call=mock_call)}):
                analyzer = IFinDNewsAnalyzer()
            assert analyzer.available() is True

    def test_call_unavailable(self):
        with patch("utils.ifind_news_analyzer.sys") as mock_sys:
            mock_sys.warnoptions = []
            mock_sys.path.insert(0, "/fake")
            with patch.dict("sys.modules", {}, clear=False):
                sys.modules.pop("call", None)
                with patch("utils.ifind_news_analyzer.logger") as mock_logger:
                    analyzer = IFinDNewsAnalyzer()
                    assert analyzer.available() is False
                    mock_logger.error.assert_called()


class TestAvailable:
    def test_available_true(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock()
        assert analyzer.available() is True

    def test_available_false(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        assert analyzer.available() is False


class TestSearchNews:
    def test_when_call_unavailable(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        assert analyzer.search_news("query") == []

    def test_when_call_available(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_data([]))
        result = analyzer.search_news("query", size=3, days=1)
        assert isinstance(result, list)


class TestSearchNotice:
    def test_when_call_unavailable(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        assert analyzer.search_notice("query") == []

    def test_when_call_available(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_data([]))
        result = analyzer.search_notice("query", size=3, days=1)
        assert isinstance(result, list)


class TestSearchTrending:
    def test_call_unavailable(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        assert analyzer.search_trending("AI") == []

    def test_without_industry_and_timescope(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_result({"results": []}))
        result = analyzer.search_trending("AI", size=3)
        assert isinstance(result, list)
        call_args = analyzer._call.call_args
        assert call_args[0][:2] == ("news", "search_trending_news")
        request = call_args[0][2]
        assert request["keyword"] == "AI"
        assert request["size"] == 3

    def test_with_industry_and_timescope(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_result({"results": []}))
        result = analyzer.search_trending(
            "AI", industry_name="电子", time_scope="7天", size=5
        )
        assert isinstance(result, list)
        call_args = analyzer._call.call_args
        request = call_args[0][2]
        assert request["industry_name"] == "电子"
        assert request["time_scope"] == "7天"

    def test_api_failure_returns_empty(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(side_effect=RuntimeError("network error"))
        with patch("utils.ifind_news_analyzer.logger") as mock_logger:
            result = analyzer.search_trending("AI")
        assert result == []
        mock_logger.error.assert_called()


class TestAnalyzeSymbol:
    def test_empty_symbol_raises(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        with pytest.raises(ValueError):
            analyzer.analyze_symbol("")

    def test_analyze_without_name(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        insight = analyzer.analyze_symbol("000001.SZ")
        assert insight.symbol == "000001.SZ"
        assert insight.name == "000001.SZ"
        assert insight.news_count == 0
        assert insight.reasons == ["未检索到相关资讯"]

    def test_analyze_with_name_and_news(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(
            return_value=_ok_result(
                {
                    "results": [
                        {
                            "title": "利好",
                            "snippet": "好",
                            "source": "src",
                            "date": "2026-01-01",
                        }
                    ]
                }
            )
        )
        insight = analyzer.analyze_symbol("000001.SZ", name="平安银行")
        assert insight.symbol == "000001.SZ"
        assert insight.name == "平安银行"
        assert insight.news_count > 0


class TestBatchAnalyze:
    def test_batch_success(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_result({"results": []}))
        with patch.object(
            analyzer,
            "analyze_symbol",
            return_value=StockInsight(
                symbol="000001.SZ", name="", direction="positive", confidence=0.5
            ),
        ) as mock_analyze:
            results = analyzer.batch_analyze(
                ["000001.SZ", "000002.SZ"], name_map={"000001.SZ": "A"}
            )
        assert len(results) == 2
        assert mock_analyze.call_count == 2

    def test_batch_failure_skips(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        with patch.object(analyzer, "analyze_symbol", side_effect=RuntimeError("fail")):
            with patch("utils.ifind_news_analyzer.logger") as mock_logger:
                results = analyzer.batch_analyze(["000001.SZ"])
        assert results == []
        mock_logger.error.assert_called()


class TestCallNews:
    def test_call_unavailable(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = None
        assert analyzer._call_news("search_news", "query") == []

    def test_success(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(return_value=_ok_result({"results": []}))
        with patch("utils.ifind_news_analyzer.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-01-01"
            result = analyzer._call_news("search_news", "query", size=3, days=1)
        assert isinstance(result, list)

    def test_failure_logs_error(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        analyzer._call = MagicMock(side_effect=RuntimeError("fail"))
        with patch("utils.ifind_news_analyzer.logger") as mock_logger:
            result = analyzer._call_news("search_news", "query")
        assert result == []
        mock_logger.error.assert_called()


class TestParseNewsResult:
    def test_plain_dict(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        data = {
            "data": [
                {
                    "title": "t",
                    "snippet": "s",
                    "source": "src",
                    "publish_time": "2026-01-01",
                }
            ]
        }
        items = analyzer._parse_news_result(data)
        assert len(items) == 1
        assert items[0].title == "t"

    def test_mcp_wrapped_text(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        inner = json.dumps(
            {
                "result": {
                    "results": [
                        {
                            "title": "t",
                            "snippet": "s",
                            "source": "src",
                            "date": "2026-01-01",
                        }
                    ]
                }
            }
        )
        data = {"result": {"content": [{"text": inner}]}}
        items = analyzer._parse_news_result(data)
        assert len(items) == 1
        assert items[0].title == "t"

    def test_list_input(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        data = {
            "data": [
                {
                    "title": "t",
                    "snippet": "s",
                    "source": "src",
                    "publish_time": "2026-01-01",
                }
            ]
        }
        items = analyzer._parse_news_result(data)
        assert len(items) == 1

    def test_non_dict_returns_empty(self):
        analyzer = IFinDNewsAnalyzer.__new__(IFinDNewsAnalyzer)
        items = analyzer._parse_news_result("bad")
        assert items == []
