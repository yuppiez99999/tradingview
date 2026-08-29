"""ifind_news_analyzer 单元测试 — iFinD 资讯读取 + 标的研判"""

from unittest.mock import MagicMock, patch

import pytest

from utils.ifind_news_analyzer import (
    IFinDNewsAnalyzer,
    NewsItem,
    StockInsight,
)


class TestNewsItem:
    def test_defaults(self):
        item = NewsItem(title="t", snippet="s", source="src", publish_time="2026-01-01")
        assert item.title == "t"
        assert item.url is None
        assert item.sentiment == "neutral"
        assert item.relevance == 0.0
        assert item.entities == []

    def test_custom(self):
        item = NewsItem(
            title="t",
            snippet="s",
            source="src",
            publish_time="2026-01-01",
            url="http://x",
            sentiment="positive",
            relevance=0.9,
            entities=["A", "B"],
        )
        assert item.url == "http://x"
        assert item.sentiment == "positive"
        assert item.relevance == 0.9
        assert item.entities == ["A", "B"]


class TestStockInsight:
    def test_defaults(self):
        si = StockInsight(
            symbol="600519", name="茅台", direction="positive", confidence=0.8
        )
        assert si.reasons == []
        assert si.news_count == 0
        assert si.updated_at != ""

    def test_custom(self):
        si = StockInsight(
            symbol="A",
            name="X",
            direction="negative",
            confidence=0.6,
            reasons=["r1", "r2"],
            news_count=5,
        )
        assert si.reasons == ["r1", "r2"]
        assert si.news_count == 5


class TestIFinDNewsAnalyzerInit:
    def test_default_skill_dir(self):
        analyzer = IFinDNewsAnalyzer()
        assert analyzer.skill_dir is not None

    def test_custom_skill_dir(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/fake/path")
        assert analyzer.skill_dir == "/fake/path"

    def test_call_none_on_import_failure(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._call is None


class TestAvailable:
    def test_not_available(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer.available() is False

    def test_available_with_mock_call(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        analyzer._call = MagicMock()
        assert analyzer.available() is True


class TestSearchNews:
    def test_no_call_returns_empty(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer.search_news("query")
        assert result == []

    def test_with_mock_call(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        analyzer._call = MagicMock(return_value={"ok": True, "data": {}})
        with patch.object(analyzer, "_parse_news_result", return_value=[MagicMock()]):
            result = analyzer.search_news("query")
        assert len(result) == 1


class TestSearchNotice:
    def test_no_call_returns_empty(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer.search_notice("query")
        assert result == []


class TestSearchTrending:
    def test_no_call_returns_empty(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer.search_trending("AI")
        assert result == []

    def test_with_mock_call(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        analyzer._call = MagicMock(return_value={"ok": True, "data": {}})
        with patch.object(analyzer, "_parse_news_result", return_value=[MagicMock()]):
            result = analyzer.search_trending("AI", industry_name="科技")
        assert len(result) == 1
        call_args = analyzer._call.call_args
        assert call_args[0][2]["keyword"] == "AI"
        assert call_args[0][2]["industry_name"] == "科技"


class TestAnalyzeSymbol:
    def test_empty_symbol_raises(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        with pytest.raises(ValueError, match="symbol"):
            analyzer.analyze_symbol("")

    def test_with_news(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        mock_items = [
            NewsItem(
                title="预增", snippet="利好", source="s", publish_time="2026-01-01"
            )
        ]
        with (
            patch.object(analyzer, "search_news", return_value=mock_items),
            patch.object(analyzer, "search_notice", return_value=[]),
        ):
            insight = analyzer.analyze_symbol("600519", name="茅台")
        assert insight.symbol == "600519"
        assert insight.name == "茅台"
        assert insight.news_count == 1

    def test_no_name(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        with (
            patch.object(analyzer, "search_news", return_value=[]),
            patch.object(analyzer, "search_notice", return_value=[]),
        ):
            insight = analyzer.analyze_symbol("600519")
        assert insight.name == "600519"


class TestBatchAnalyze:
    def test_empty(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer.batch_analyze([])
        assert result == []

    def test_multiple(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        mock_insight = StockInsight(
            symbol="A", name="X", direction="neutral", confidence=0.5
        )
        with patch.object(analyzer, "analyze_symbol", return_value=mock_insight):
            result = analyzer.batch_analyze(["A", "B", "C"])
        assert len(result) == 3

    def test_with_name_map(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        mock_insight = StockInsight(
            symbol="A", name="X", direction="neutral", confidence=0.5
        )
        with patch.object(
            analyzer, "analyze_symbol", return_value=mock_insight
        ) as mock_fn:
            analyzer.batch_analyze(["A"], name_map={"A": "茅台"})
        mock_fn.assert_called_with("A", name="茅台", size=4, days=3)


class TestParseNewsResult:
    def test_empty(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._parse_news_result({}) == []

    def test_none(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._parse_news_result(None) == []

    def test_list_of_dicts(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        data = {
            "data": [
                {
                    "title": "测试新闻",
                    "snippet": "内容",
                    "source": "源",
                    "publish_time": "2026-01-01",
                }
            ]
        }
        result = analyzer._parse_news_result(data)
        assert len(result) == 1
        assert result[0].title == "测试新闻"

    def test_chinese_keys(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        data = {
            "data": [
                {
                    "资讯标题": "标题",
                    "资讯内容": "内容",
                    "来源": "源",
                    "日期": "2026-01-01",
                }
            ]
        }
        result = analyzer._parse_news_result(data)
        assert len(result) == 1
        assert result[0].title == "标题"

    def test_mcp_wrapped(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        import json

        inner = [
            {
                "title": "MCP",
                "snippet": "x",
                "source": "s",
                "publish_time": "2026-01-01",
            }
        ]
        data = {"result": {"content": [{"text": json.dumps(inner)}]}}
        result = analyzer._parse_news_result(data)
        assert len(result) == 1
        assert result[0].title == "MCP"


class TestExtractResults:
    def test_list(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._extract_results([1, 2, 3]) == [1, 2, 3]

    def test_non_dict_non_list(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._extract_results("string") == []

    def test_dict_with_data_list(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer._extract_results({"data": [1, 2]})
        assert result == [1, 2]

    def test_dict_with_data_dict(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer._extract_results({"data": {"data": [1, 2]}})
        assert result == [1, 2]

    def test_dict_with_result_results(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        result = analyzer._extract_results({"result": {"results": [1, 2]}})
        assert result == [1, 2]

    def test_empty_dict(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        assert analyzer._extract_results({}) == []


class TestDeriveInsight:
    def test_empty_items(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        direction, conf, reasons = analyzer._derive_insight([], "A")
        assert direction == "neutral"
        assert conf == 0.0
        assert "未检索到相关资讯" in reasons

    def test_positive_keywords(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        items = [
            NewsItem(
                title="公司预增",
                snippet="业绩增长",
                source="s",
                publish_time="2026-01-01",
            ),
            NewsItem(
                title="中标大单",
                snippet="订单增长",
                source="s",
                publish_time="2026-01-01",
            ),
        ]
        direction, conf, reasons = analyzer._derive_insight(items, "A")
        assert direction == "positive"
        assert conf > 0.5

    def test_negative_keywords(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        items = [
            NewsItem(
                title="业绩下滑", snippet="亏损", source="s", publish_time="2026-01-01"
            ),
            NewsItem(
                title="减持", snippet="预减", source="s", publish_time="2026-01-01"
            ),
        ]
        direction, conf, reasons = analyzer._derive_insight(items, "A")
        assert direction == "negative"
        assert conf > 0.5

    def test_neutral_mixed(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        items = [
            NewsItem(
                title="增长", snippet="利好", source="s", publish_time="2026-01-01"
            ),
            NewsItem(
                title="下滑", snippet="利空", source="s", publish_time="2026-01-01"
            ),
        ]
        direction, conf, reasons = analyzer._derive_insight(items, "A")
        assert direction == "neutral"

    def test_no_signal(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        items = [
            NewsItem(
                title="普通新闻",
                snippet="无特殊信号",
                source="s",
                publish_time="2026-01-01",
            )
        ]
        direction, conf, reasons = analyzer._derive_insight(items, "A")
        assert direction == "neutral"
        assert conf == 0.4


class TestExtractEntities:
    def test_code_with_exchange(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        entities = analyzer._extract_entities("600519.SH 涨停")
        assert "600519.SH" in entities

    def test_pure_code(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        entities = analyzer._extract_entities("代码000001")
        assert "000001" in entities

    def test_no_entities(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        entities = analyzer._extract_entities("无代码文本")
        assert entities == []

    def test_max_10(self):
        analyzer = IFinDNewsAnalyzer(skill_dir="/nonexistent")
        text = " ".join([f"{i:06d}" for i in range(20)])
        entities = analyzer._extract_entities(text)
        assert len(entities) <= 10
