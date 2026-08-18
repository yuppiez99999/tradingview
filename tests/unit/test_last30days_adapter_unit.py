"""last30days_adapter 单元测试 — 全球社交舆情适配器."""

from __future__ import annotations

import json
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from utils.last30days_adapter import (
    Last30DaysAdapter,
    Last30DaysSignal,
    get_adapter,
    search_topic as module_search_topic,
)


class TestLast30DaysSignal:
    def test_defaults(self):
        s = Last30DaysSignal(topic="gold", platform="reddit")
        assert s.topic == "gold"
        assert s.platform == "reddit"
        assert s.sentiment_score == 0.0
        assert s.mention_count == 0
        assert s.engagement_score == 0.0
        assert s.source_urls == []

    def test_custom(self):
        s = Last30DaysSignal(
            topic="AI",
            platform="x",
            sentiment_score=0.8,
            mention_count=100,
            engagement_score=500.0,
            source_urls=["http://example.com"],
            title="AI boom",
        )
        assert s.sentiment_score == 0.8
        assert s.mention_count == 100
        assert s.source_urls == ["http://example.com"]

    def test_to_dict(self):
        s = Last30DaysSignal(topic="gold", platform="reddit", sentiment_score=0.5)
        d = s.to_dict()
        assert d["topic"] == "gold"
        assert d["platform"] == "reddit"
        assert d["sentiment_score"] == 0.5


class TestSupportedPlatforms:
    def test_set(self):
        assert "reddit" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "x" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "youtube" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "hn" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "tiktok" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "polymarket" in Last30DaysAdapter.SUPPORTED_PLATFORMS
        assert "github" in Last30DaysAdapter.SUPPORTED_PLATFORMS


class TestAggregateSentiment:
    def test_empty(self):
        result = Last30DaysAdapter.aggregate_sentiment([])
        assert result["composite_sentiment"] == 0.0
        assert result["total_mentions"] == 0
        assert result["total_engagement"] == 0.0
        assert result["platform_count"] == 0
        assert result["positive_ratio"] == 0.0
        assert result["negative_ratio"] == 0.0

    def test_with_engagement(self):
        signals = [
            Last30DaysSignal(topic="t", platform="reddit", sentiment_score=0.8, engagement_score=100),
            Last30DaysSignal(topic="t", platform="x", sentiment_score=-0.4, engagement_score=50),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        assert result["total_engagement"] == 150.0
        assert result["platform_count"] == 2
        expected_composite = (0.8 * 100 + (-0.4) * 50) / 150
        assert result["composite_sentiment"] == round(expected_composite, 4)

    def test_without_engagement(self):
        signals = [
            Last30DaysSignal(topic="t", platform="reddit", sentiment_score=0.6),
            Last30DaysSignal(topic="t", platform="x", sentiment_score=0.4),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        assert result["composite_sentiment"] == 0.5

    def test_positive_negative_ratio(self):
        signals = [
            Last30DaysSignal(topic="t", platform="reddit", sentiment_score=0.5),
            Last30DaysSignal(topic="t", platform="x", sentiment_score=-0.5),
            Last30DaysSignal(topic="t", platform="youtube", sentiment_score=0.05),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        assert result["positive_ratio"] == round(1 / 3, 4)
        assert result["negative_ratio"] == round(1 / 3, 4)

    def test_total_mentions(self):
        signals = [
            Last30DaysSignal(topic="t", platform="reddit", mention_count=50),
            Last30DaysSignal(topic="t", platform="x", mention_count=30),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        assert result["total_mentions"] == 80


class TestCacheKey:
    def test_deterministic(self):
        adapter = Last30DaysAdapter()
        k1 = adapter._cache_key("gold", ["reddit", "x"], 7)
        k2 = adapter._cache_key("gold", ["reddit", "x"], 7)
        assert k1 == k2

    def test_platform_order_independent(self):
        adapter = Last30DaysAdapter()
        k1 = adapter._cache_key("gold", ["reddit", "x"], 7)
        k2 = adapter._cache_key("gold", ["x", "reddit"], 7)
        assert k1 == k2

    def test_different_topic(self):
        adapter = Last30DaysAdapter()
        assert adapter._cache_key("gold", ["reddit"], 7) != adapter._cache_key("oil", ["reddit"], 7)


class TestParseCliOutput:
    def test_valid_list(self):
        adapter = Last30DaysAdapter()
        stdout = json.dumps([
            {"platform": "reddit", "sentiment_score": 0.5, "mention_count": 10},
            {"platform": "x", "sentiment_score": -0.3, "engagement_score": 50},
        ]).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit", "x"])
        assert len(signals) == 2
        assert signals[0].platform == "reddit"
        assert signals[0].sentiment_score == 0.5
        assert signals[1].platform == "x"

    def test_dict_with_results(self):
        adapter = Last30DaysAdapter()
        stdout = json.dumps({"results": [{"platform": "hn", "sentiment_score": 0.1}]}).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "tech", ["hn"])
        assert len(signals) == 1
        assert signals[0].platform == "hn"

    def test_invalid_json(self):
        adapter = Last30DaysAdapter()
        signals = adapter._parse_cli_output(b"not json", "gold", ["reddit"])
        assert signals == []

    def test_non_list_non_dict(self):
        adapter = Last30DaysAdapter()
        signals = adapter._parse_cli_output(b"42", "gold", ["reddit"])
        assert signals == []

    def test_unsupported_platform_filtered(self):
        adapter = Last30DaysAdapter()
        stdout = json.dumps([
            {"platform": "reddit", "sentiment_score": 0.5},
            {"platform": "facebook", "sentiment_score": 0.3},
        ]).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit"])
        assert len(signals) == 1
        assert signals[0].platform == "reddit"

    def test_non_dict_item_skipped(self):
        adapter = Last30DaysAdapter()
        stdout = json.dumps(["string", 42, {"platform": "reddit"}]).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit"])
        assert len(signals) == 1


class TestSearchTopic:
    @patch("utils.last30days_adapter.is_enabled", return_value=False)
    def test_flag_disabled(self, _mock):
        adapter = Last30DaysAdapter()
        assert adapter.search_topic("gold") == []

    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    def test_empty_topic(self, _mock):
        adapter = Last30DaysAdapter()
        assert adapter.search_topic("") == []
        assert adapter.search_topic("   ") == []

    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    def test_invalid_platforms(self, _mock):
        adapter = Last30DaysAdapter()
        result = adapter.search_topic("gold", platforms=["facebook", "myspace"])
        assert result == []


class TestGetHealth:
    @patch("utils.last30days_adapter.is_enabled", return_value=False)
    def test_basic(self, _mock):
        adapter = Last30DaysAdapter()
        adapter._cli_available = False
        health = adapter.get_health()
        assert health["flag_enabled"] is False
        assert health["cli_available"] is False
        assert "reddit" in health["supported_platforms"]
        assert health["cache_ttl"] == 600


class TestGetAdapter:
    def test_singleton(self):
        a1 = get_adapter()
        a2 = get_adapter()
        assert a1 is a2


class TestModuleSearchTopic:
    @patch("utils.last30days_adapter.is_enabled", return_value=False)
    def test_flag_disabled(self, _mock):
        assert module_search_topic("gold") == []


class TestCheckCli:
    @patch("utils.last30days_adapter.subprocess.run")
    def test_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        adapter = Last30DaysAdapter()
        assert adapter.cli_available is True

    @patch("utils.last30days_adapter.subprocess.run")
    def test_not_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        adapter = Last30DaysAdapter()
        assert adapter.cli_available is False

    @patch("utils.last30days_adapter.subprocess.run", side_effect=FileNotFoundError)
    def test_filenotfound(self, _mock):
        adapter = Last30DaysAdapter()
        assert adapter.cli_available is False

    @patch("utils.last30days_adapter.subprocess.run", side_effect=TimeoutExpired(cmd="npx", timeout=5))
    def test_timeout(self, _mock):
        adapter = Last30DaysAdapter()
        assert adapter.cli_available is False

    @patch("utils.last30days_adapter.subprocess.run", side_effect=OSError("err"))
    def test_oserror(self, _mock):
        adapter = Last30DaysAdapter()
        assert adapter.cli_available is False


class TestSearchTopicFlagEnabled:
    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    @patch("utils.last30days_adapter.subprocess.run", side_effect=FileNotFoundError)
    def test_cli_not_available(self, _run, _flag):
        adapter = Last30DaysAdapter()
        adapter._cli_available = False
        result = adapter.search_topic("no_cli_topic", platforms=["reddit"], days=7)
        assert result == []

    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    @patch("utils.last30days_adapter.subprocess.run")
    def test_cli_returns_data(self, mock_run, _flag):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout=json.dumps([{"platform": "reddit", "sentiment_score": 0.5, "mention_count": 10}]).encode("utf-8"),
            ),
        ]
        adapter = Last30DaysAdapter()
        result = adapter.search_topic("gold", platforms=["reddit"], days=7)
        assert len(result) == 1
        assert result[0].platform == "reddit"

    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    @patch("utils.last30days_adapter.subprocess.run")
    def test_cli_nonzero_return(self, mock_run, _flag):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr=b"error"),
        ]
        adapter = Last30DaysAdapter()
        result = adapter.search_topic("nonzero_topic", platforms=["reddit"], days=7)
        assert result == []

    @patch("utils.last30days_adapter.is_enabled", return_value=True)
    @patch("utils.last30days_adapter.subprocess.run")
    def test_cli_timeout(self, mock_run, _flag):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            TimeoutExpired(cmd="npx", timeout=30),
        ]
        adapter = Last30DaysAdapter()
        result = adapter.search_topic("timeout_topic", platforms=["reddit"], days=7)
        assert result == []


class TestSaveLoadCache:
    def test_round_trip(self, tmp_path):
        adapter = Last30DaysAdapter()
        with patch("utils.last30days_adapter._CACHE_DIR", tmp_path):
            signals = [Last30DaysSignal(topic="gold", platform="reddit", sentiment_score=0.5)]
            key = "testkey123"
            adapter._save_cache(key, signals)
            loaded = adapter._load_cache(key)
            assert loaded is not None
            assert len(loaded) == 1
            assert loaded[0].platform == "reddit"

    def test_load_nonexistent(self, tmp_path):
        adapter = Last30DaysAdapter()
        with patch("utils.last30days_adapter._CACHE_DIR", tmp_path):
            assert adapter._load_cache("nonexist") is None

    def test_load_expired(self, tmp_path):
        adapter = Last30DaysAdapter(cache_ttl=0)
        with patch("utils.last30days_adapter._CACHE_DIR", tmp_path):
            signals = [Last30DaysSignal(topic="gold", platform="reddit")]
            key = "expiredkey"
            adapter._save_cache(key, signals)
            import time
            time.sleep(0.01)
            assert adapter._load_cache(key) is None


class TestWriteAudit:
    def test_basic(self, tmp_path):
        adapter = Last30DaysAdapter()
        with patch("utils.last30days_adapter._AUDIT_DIR", tmp_path):
            signals = [Last30DaysSignal(topic="gold", platform="reddit", sentiment_score=0.5)]
            adapter._write_audit("gold", ["reddit"], 7, signals)
            files = list(tmp_path.glob("query_*.jsonl"))
            assert len(files) == 1