"""web_content_extractor 单元测试 (Wave 12-A #4).

被测模块: utils/web_content_extractor.py
验收门禁: 提取准确率>90% / 正文清洗替换正则 / 单测覆盖 / 降级不崩溃
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.web_content_extractor import WebContentExtractor  # noqa: E402

_SAMPLE_HTML = """
<html>
<head><title>测试新闻标题</title></head>
<body>
<nav>导航菜单</nav>
<article>
<h1>中国股市收盘大涨</h1>
<p>今日上证指数收盘上涨2.5%，深证成指上涨3.1%。
两市成交额突破1.5万亿元，北向资金净流入超100亿元。</p>
<p>分析师认为，市场上涨主要受政策利好和外资流入推动。</p>
</article>
<footer>版权信息</footer>
</body>
</html>
"""

_EXPECTED_KEYWORDS = ["上证指数", "成交额", "北向资金"]


class TestExtractText:
    """extract_text 方法测试."""

    def test_returns_string(self):
        extractor = WebContentExtractor()
        result = extractor.extract_text(_SAMPLE_HTML)
        assert result is not None
        assert isinstance(result, str)

    def test_extracts_main_content(self):
        extractor = WebContentExtractor()
        result = extractor.extract_text(_SAMPLE_HTML)
        assert result is not None
        for keyword in _EXPECTED_KEYWORDS:
            assert keyword in result

    def test_empty_html_returns_none(self):
        extractor = WebContentExtractor()
        assert extractor.extract_text("") is None
        assert extractor.extract_text(None) is None

    @patch("utils.web_content_extractor._TRAFILATURA_AVAILABLE", True)
    @patch("utils.web_content_extractor.trafilatura")
    def test_include_metadata(self, mock_tf):
        mock_tf.bare_extraction.return_value = {
            "text": "正文内容",
            "title": "测试新闻标题",
        }
        extractor = WebContentExtractor()
        result = extractor.extract_text(_SAMPLE_HTML, include_metadata=True)
        assert result is not None
        assert isinstance(result, dict)
        assert "text" in result
        assert "title" in result

    def test_extraction_accuracy(self):
        extractor = WebContentExtractor()
        result = extractor.extract_text(_SAMPLE_HTML)
        assert result is not None
        found = sum(1 for kw in _EXPECTED_KEYWORDS if kw in result)
        accuracy = found / len(_EXPECTED_KEYWORDS)
        assert accuracy > 0.9


class TestExtractFromUrl:
    """extract_from_url 方法测试."""

    @patch("utils.web_content_extractor._TRAFILATURA_AVAILABLE", True)
    @patch("utils.web_content_extractor.trafilatura")
    def test_success(self, mock_tf):
        mock_tf.fetch_url.return_value = _SAMPLE_HTML
        mock_tf.extract.return_value = "提取的正文"
        extractor = WebContentExtractor()
        result = extractor.extract_from_url("http://example.com/news")
        assert result == "提取的正文"

    @patch("utils.web_content_extractor.trafilatura")
    def test_fetch_returns_none(self, mock_tf):
        mock_tf.fetch_url.return_value = None
        extractor = WebContentExtractor()
        result = extractor.extract_from_url("http://example.com/news")
        assert result is None

    @patch("utils.web_content_extractor.trafilatura")
    def test_exception_no_crash(self, mock_tf):
        mock_tf.fetch_url.side_effect = Exception("network error")
        extractor = WebContentExtractor()
        result = extractor.extract_from_url("http://example.com/news")
        assert result is None


class TestExtractBatch:
    """extract_batch 方法测试."""

    def test_batch_extraction(self):
        extractor = WebContentExtractor()
        items = [_SAMPLE_HTML, _SAMPLE_HTML, ""]
        result = extractor.extract_batch(items)
        assert len(result) == 3
        assert result[0] is not None
        assert result[2] is None

    def test_empty_batch(self):
        extractor = WebContentExtractor()
        assert extractor.extract_batch([]) == []


class TestCleanText:
    """clean_text 方法测试."""

    def test_removes_html_tags(self):
        extractor = WebContentExtractor()
        result = extractor.clean_text("<p>hello</p>")
        assert result == "hello"

    def test_compresses_whitespace(self):
        extractor = WebContentExtractor()
        result = extractor.clean_text("hello    world\n\nfoo")
        assert result == "hello world foo"

    def test_empty_input(self):
        extractor = WebContentExtractor()
        assert extractor.clean_text("") == ""
        assert extractor.clean_text(None) == ""

    def test_preserves_chinese(self):
        extractor = WebContentExtractor()
        result = extractor.clean_text("<p>中国股市</p>")
        assert result == "中国股市"


class TestRegexFallback:
    """正则降级后端测试."""

    def test_regex_fallback(self):
        with patch("utils.web_content_extractor._TRAFILATURA_AVAILABLE", False):
            extractor = WebContentExtractor()
            result = extractor.extract_text("<p>正文内容</p>")
            assert result is not None
            assert "正文内容" in result

    def test_regex_fallback_empty(self):
        with patch("utils.web_content_extractor._TRAFILATURA_AVAILABLE", False):
            extractor = WebContentExtractor()
            result = extractor.extract_text("<p></p>")
            assert result is None
