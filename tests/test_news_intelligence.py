"""新闻智能引擎单测 — mock WebScraper + GLM5Client, 不依赖真实网络/LLM

测试覆盖:
- NewsArticle / NewsIntelligenceReport 不可变 dataclass
- resolve_keyword 代码→关键词
- NewsIntelligenceEngine.get_report 主流程 (LLM 成功/失败/降级)
- _parse_llm_response JSON 解析 (正常/异常/边界)
- _extract_json (markdown 代码块/裸 JSON/无 JSON)
- _to_str_tuple (list/tuple/截断/非列表)
- NewsIntelligenceSignalSource.get_signal → SignalResult
- 中性降级 (空代码/无新闻/LLM 异常)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.news_intelligence import (
    NewsArticle,
    NewsIntelligenceEngine,
    NewsIntelligenceReport,
    resolve_keyword,
)
from utils.signal_sources.news_intelligence_signal_source import (
    NewsIntelligenceSignalSource,
)

# ============================================================
# 辅助: 构造 mock 采集器 / mock LLM
# ============================================================

def make_mock_scraper(articles):
    """构造 mock WebScraper, fetch_news 返回指定文章列表"""
    scraper = MagicMock()
    scraper.fetch_news = MagicMock(return_value=articles)
    return scraper


def make_mock_llm(response_content):
    """构造 mock GLM5Client, chat 返回指定 content"""
    llm = MagicMock()
    llm.chat = MagicMock(return_value={"content": response_content, "role": "assistant"})
    return llm


def make_mock_articles(n=3):
    """构造 n 条模拟新闻 (dict 格式, 模拟 WebScraper 返回)"""
    return [
        {
            "title": f"测试新闻 {i+1}",
            "content": f"这是第 {i+1} 条新闻的内容, 涉及利好消息.",
            "url": f"https://example.com/news/{i+1}",
            "source": "新浪财经",
            "published_at": "2026-08-21T10:00:00",
        }
        for i in range(n)
    ]


# ============================================================
# NewsArticle / NewsIntelligenceReport 不可变性
# ============================================================

class TestNewsArticleImmutable:
    def test_frozen_dataclass(self):
        art = NewsArticle(title="测试")
        try:
            art.title = "修改"
            raise AssertionError("应该不可变")
        except AttributeError:
            pass

    def test_defaults(self):
        art = NewsArticle(title="标题")
        assert art.content == ""
        assert art.url == ""
        assert art.source == ""
        assert art.published_at == ""


class TestNewsIntelligenceReportImmutable:
    def test_frozen_dataclass(self):
        rep = NewsIntelligenceReport(code="600519.SH")
        try:
            rep.score = 0.9
            raise AssertionError("应该不可变")
        except AttributeError:
            pass

    def test_defaults(self):
        rep = NewsIntelligenceReport(code="600519.SH")
        assert rep.score == 0.5
        assert rep.action == "HOLD"
        assert rep.confidence == 0.0
        assert rep.key_points == ()
        assert rep.risk_factors == ()
        assert rep.fallback_used is False


# ============================================================
# resolve_keyword
# ============================================================

class TestResolveKeyword:
    def test_sh_suffix(self):
        assert resolve_keyword("600519.SH") == "600519"

    def test_sz_suffix(self):
        assert resolve_keyword("000001.SZ") == "000001"

    def test_no_suffix(self):
        assert resolve_keyword("贵州茅台") == "贵州茅台"

    def test_empty(self):
        assert resolve_keyword("") == ""

    def test_plain_code(self):
        assert resolve_keyword("002371") == "002371"


# ============================================================
# NewsIntelligenceEngine — LLM 成功路径
# ============================================================

class TestEngineLLMSuccess:
    def test_llm_returns_valid_json(self):
        llm_response = '{"score": 0.8, "action": "BUY", "confidence": 0.7, "summary": "利好消息", "key_points": ["业绩增长"], "risk_factors": ["估值偏高"]}'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(3)),
            llm_client=make_mock_llm(llm_response),
        )
        report = engine.get_report("600519.SH")

        assert report.code == "600519.SH"
        assert report.score == 0.8
        assert report.action == "BUY"
        assert report.confidence == 0.7
        assert report.summary == "利好消息"
        assert report.key_points == ("业绩增长",)
        assert report.risk_factors == ("估值偏高",)
        assert report.article_count == 3
        assert report.fallback_used is False

    def test_llm_returns_markdown_fenced_json(self):
        llm_response = '```json\n{"score": 0.3, "action": "SELL", "confidence": 0.6, "summary": "利空"}\n```'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(2)),
            llm_client=make_mock_llm(llm_response),
        )
        report = engine.get_report("000001.SZ")

        assert report.score == 0.3
        assert report.action == "SELL"
        assert report.confidence == 0.6

    def test_score_clamped_to_01(self):
        llm_response = '{"score": 1.5, "action": "BUY", "confidence": 0.8}'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(1)),
            llm_client=make_mock_llm(llm_response),
        )
        report = engine.get_report("600519.SH")
        assert report.score == 1.0

    def test_confidence_clamped_to_01(self):
        llm_response = '{"score": 0.7, "action": "BUY", "confidence": -0.5}'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(1)),
            llm_client=make_mock_llm(llm_response),
        )
        report = engine.get_report("600519.SH")
        assert report.confidence == 0.0

    def test_low_confidence_becomes_hold(self):
        llm_response = '{"score": 0.9, "action": "BUY", "confidence": 0.1}'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(1)),
            llm_client=make_mock_llm(llm_response),
            min_confidence=0.3,
        )
        report = engine.get_report("600519.SH")
        assert report.action == "HOLD"
        assert report.confidence == 0.0

    def test_invalid_action_becomes_hold(self):
        llm_response = '{"score": 0.7, "action": "UNKNOWN", "confidence": 0.8}'
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper(make_mock_articles(1)),
            llm_client=make_mock_llm(llm_response),
        )
        report = engine.get_report("600519.SH")
        assert report.action == "HOLD"


# ============================================================
# NewsIntelligenceEngine — 降级路径
# ============================================================

class TestEngineFallback:
    def test_empty_code_returns_neutral(self):
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper([]),
            llm_client=make_mock_llm("{}"),
        )
        report = engine.get_report("")
        assert report.score == 0.5
        assert report.action == "HOLD"
        assert report.confidence == 0.0
        assert report.fallback_used is True

    def test_no_news_returns_neutral(self):
        engine = NewsIntelligenceEngine(
            web_scraper=make_mock_scraper([]),
            llm_client=make_mock_llm("{}"),
        )
        report = engine.get_report("600519.SH")
        assert report.score == 0.5
        assert report.confidence == 0.0
        assert "无新闻数据" in report.summary

    def test_llm_returns_empty_content_falls_to_sentiment(self):
        scraper = make_mock_scraper(make_mock_articles(2))
        llm = MagicMock()
        llm.chat = MagicMock(return_value={"content": ""})

        sentiment_engine = MagicMock()
        sentiment_signal = MagicMock()
        sentiment_signal.composite_sentiment = 0.6
        sentiment_signal.confidence = 0.8
        sentiment_result = MagicMock()
        sentiment_result.signals = {"600519.SH": sentiment_signal}
        sentiment_engine.analyze = MagicMock(return_value=sentiment_result)
        sentiment_engine.add_news_batch = MagicMock()

        engine = NewsIntelligenceEngine(
            web_scraper=scraper,
            llm_client=llm,
            sentiment_engine=sentiment_engine,
        )
        report = engine.get_report("600519.SH")

        assert report.fallback_used is True
        assert report.score == 0.8  # (0.6 + 1) / 2
        assert report.action == "BUY"

    def test_llm_returns_invalid_json_falls_to_sentiment(self):
        scraper = make_mock_scraper(make_mock_articles(1))
        llm = make_mock_llm("这不是JSON")

        sentiment_engine = MagicMock()
        sentiment_signal = MagicMock()
        sentiment_signal.composite_sentiment = -0.4
        sentiment_signal.confidence = 0.7
        sentiment_result = MagicMock()
        sentiment_result.signals = {"600519.SH": sentiment_signal}
        sentiment_engine.analyze = MagicMock(return_value=sentiment_result)
        sentiment_engine.add_news_batch = MagicMock()

        engine = NewsIntelligenceEngine(
            web_scraper=scraper,
            llm_client=llm,
            sentiment_engine=sentiment_engine,
        )
        report = engine.get_report("600519.SH")

        assert report.fallback_used is True
        assert report.score == 0.3  # (-0.4 + 1) / 2
        assert report.action == "SELL"

    def test_scraper_exception_returns_neutral(self):
        scraper = MagicMock()
        scraper.fetch_news = MagicMock(side_effect=RuntimeError("网络错误"))
        engine = NewsIntelligenceEngine(
            web_scraper=scraper,
            llm_client=make_mock_llm("{}"),
        )
        report = engine.get_report("600519.SH")
        assert report.score == 0.5
        assert report.confidence == 0.0


# ============================================================
# _extract_json / _to_str_tuple
# ============================================================

class TestExtractJson:
    def test_plain_json(self):
        result = NewsIntelligenceEngine._extract_json('{"score": 0.8}')
        assert result == '{"score": 0.8}'

    def test_markdown_fenced(self):
        result = NewsIntelligenceEngine._extract_json('```json\n{"score": 0.8}\n```')
        assert result == '{"score": 0.8}'

    def test_markdown_fenced_no_lang(self):
        result = NewsIntelligenceEngine._extract_json('```\n{"score": 0.8}\n```')
        assert result == '{"score": 0.8}'

    def test_no_json(self):
        result = NewsIntelligenceEngine._extract_json("纯文本无JSON")
        assert result is None

    def test_empty(self):
        result = NewsIntelligenceEngine._extract_json("")
        assert result is None


class TestToStrTuple:
    def test_list(self):
        assert NewsIntelligenceEngine._to_str_tuple(["a", "b", "c"]) == ("a", "b", "c")

    def test_tuple(self):
        assert NewsIntelligenceEngine._to_str_tuple(("x", "y")) == ("x", "y")

    def test_non_iterable(self):
        assert NewsIntelligenceEngine._to_str_tuple(None) == ()
        assert NewsIntelligenceEngine._to_str_tuple(42) == ()

    def test_truncates_to_10(self):
        result = NewsIntelligenceEngine._to_str_tuple([str(i) for i in range(20)])
        assert len(result) == 10

    def test_each_item_truncated_to_100(self):
        long_str = "A" * 200
        result = NewsIntelligenceEngine._to_str_tuple([long_str])
        assert len(result[0]) == 100

    def test_skips_empty(self):
        result = NewsIntelligenceEngine._to_str_tuple(["a", "", "b"])
        assert result == ("a", "b")


# ============================================================
# _convert_to_article
# ============================================================

class TestConvertToArticle:
    def test_dict_input(self):
        item = {"title": "标题", "content": "内容", "url": "http://x", "source": "源", "published_at": "2026"}
        art = NewsIntelligenceEngine._convert_to_article(item)
        assert art is not None
        assert art.title == "标题"
        assert art.content == "内容"

    def test_object_input(self):
        item = MagicMock(title="标题", content="内容", url="http://x", source="源", published_at="2026")
        art = NewsIntelligenceEngine._convert_to_article(item)
        assert art is not None
        assert art.title == "标题"

    def test_content_truncated_to_500(self):
        item = {"title": "T", "content": "X" * 1000}
        art = NewsIntelligenceEngine._convert_to_article(item)
        assert len(art.content) == 500


# ============================================================
# NewsIntelligenceSignalSource
# ============================================================

class TestNewsIntelligenceSignalSource:
    def test_get_signal_success(self):
        mock_engine = MagicMock()
        mock_engine.get_report = MagicMock(return_value=NewsIntelligenceReport(
            code="600519.SH",
            score=0.8,
            action="BUY",
            confidence=0.7,
            summary="利好",
            article_count=3,
        ))
        source = NewsIntelligenceSignalSource(engine=mock_engine)
        result = source.get_signal("600519.SH")

        assert result is not None
        assert result.code == "600519.SH"
        assert result.source == "news_intel"
        assert result.score == 0.8
        assert result.action == "BUY"
        assert result.confidence == 0.7

    def test_get_signal_neutral_on_empty_code(self):
        mock_engine = MagicMock()
        source = NewsIntelligenceSignalSource(engine=mock_engine)
        result = source.get_signal("")

        assert result is not None
        assert result.score == 0.5
        assert result.action == "HOLD"
        assert result.confidence == 0.0
        mock_engine.get_report.assert_not_called()

    def test_low_confidence_becomes_hold(self):
        mock_engine = MagicMock()
        mock_engine.get_report = MagicMock(return_value=NewsIntelligenceReport(
            code="600519.SH",
            score=0.9,
            action="BUY",
            confidence=0.1,
        ))
        source = NewsIntelligenceSignalSource(engine=mock_engine, min_confidence=0.3)
        result = source.get_signal("600519.SH")

        assert result.action == "HOLD"
        assert result.confidence == 0.0

    def test_engine_exception_returns_neutral(self):
        mock_engine = MagicMock()
        mock_engine.get_report = MagicMock(side_effect=RuntimeError("引擎故障"))
        source = NewsIntelligenceSignalSource(engine=mock_engine)
        result = source.get_signal("600519.SH")

        assert result is not None
        assert result.score == 0.5
        assert result.confidence == 0.0

    def test_fallback_report_tagged_in_reason(self):
        mock_engine = MagicMock()
        mock_engine.get_report = MagicMock(return_value=NewsIntelligenceReport(
            code="600519.SH",
            score=0.6,
            action="HOLD",
            confidence=0.5,
            summary="词典降级",
            fallback_used=True,
        ))
        source = NewsIntelligenceSignalSource(engine=mock_engine, min_confidence=0.3)
        result = source.get_signal("600519.SH")

        assert "词典降级" in result.reason

    def test_close_calls_engine_close(self):
        mock_engine = MagicMock()
        source = NewsIntelligenceSignalSource(engine=mock_engine)
        source._owns_engine = True
        source.close()
        mock_engine.close.assert_called_once()
