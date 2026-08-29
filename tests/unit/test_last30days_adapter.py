"""Last30DaysAdapter 单元测试.

测试目标:
    1. Flag 透传 (HC-1): USE_LAST30DAYS_SENTIMENT=False 时返回空列表
    2. 失败安全: CLI 不可用 / 超时 / 异常时返回空列表, 不抛异常
    3. 缓存: TTL 内命中缓存, 过期重新查询
    4. 解析: CLI JSON 输出正确转换为 Last30DaysSignal
    5. 聚合: aggregate_sentiment 正确计算加权情感
    6. 审计: 查询结果写入 JSONL
    7. 健康检查: get_health 返回正确状态
    8. 单例: get_adapter 返回同一实例

注意: 单元测试 mock subprocess, 不调用真实 npx CLI.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.last30days_adapter import (
    Last30DaysAdapter,
    Last30DaysSignal,
    get_adapter,
    search_topic,
)


# ============================================================
# 测试数据: 模拟 CLI JSON 输出
# ============================================================
def _make_cli_output_list() -> list[dict]:
    """模拟 CLI 输出 (list 格式)."""
    return [
        {
            "platform": "reddit",
            "sentiment_score": 0.6,
            "mention_count": 120,
            "engagement_score": 3500.0,
            "first_seen": "2026-07-01T10:00:00Z",
            "last_seen": "2026-07-15T18:30:00Z",
            "source_urls": ["https://reddit.com/r/investing/abc"],
            "title": "Gold rallies on Fed dovish tone",
            "summary": "Reddit users bullish on gold",
        },
        {
            "platform": "x",
            "sentiment_score": -0.3,
            "mention_count": 80,
            "engagement_score": 1200.0,
            "first_seen": "2026-07-02T08:00:00Z",
            "last_seen": "2026-07-14T22:00:00Z",
            "source_urls": ["https://x.com/foo/status/123"],
            "title": "Gold overbought?",
            "summary": "Mixed views on X",
        },
    ]


def _make_cli_output_dict_wrapper() -> dict:
    """模拟 CLI 输出 (dict 包裹 results 字段)."""
    return {"results": _make_cli_output_list(), "metadata": {"total": 2}}


def _make_signal(topic: str = "gold price") -> Last30DaysSignal:
    """构造一个测试信号."""
    return Last30DaysSignal(
        topic=topic,
        platform="reddit",
        sentiment_score=0.5,
        mention_count=100,
        engagement_score=2000.0,
        first_seen="2026-07-01T00:00:00Z",
        last_seen="2026-07-15T00:00:00Z",
        source_urls=["https://reddit.com/test"],
        title="Test",
        summary="Test summary",
    )


# ============================================================
# 1. Flag 透传 (HC-1)
# ============================================================
class TestFlagGate:
    """Flag 关闭时必须降级为空列表."""

    def test_flag_disabled_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=False):
            adapter = Last30DaysAdapter()
            result = adapter.search_topic("gold price")
            assert result == []

    def test_flag_disabled_skips_cli(self) -> None:
        """Flag 关闭时不应调用 CLI."""
        with (
            patch("utils.last30days_adapter.is_enabled", return_value=False),
            patch("utils.last30days_adapter.subprocess.run") as mock_run,
        ):
            adapter = Last30DaysAdapter()
            adapter.search_topic("gold price")
            mock_run.assert_not_called()

    def test_flag_disabled_health_status(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=False):
            adapter = Last30DaysAdapter()
            health = adapter.get_health()
            assert health["flag_enabled"] is False


# ============================================================
# 2. 输入校验
# ============================================================
class TestInputValidation:
    """输入参数校验."""

    def test_empty_topic_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            assert adapter.search_topic("") == []
            assert adapter.search_topic("   ") == []

    def test_invalid_platforms_filtered(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            # 全部无效平台应返回空列表 (不调用 CLI)
            with patch.object(adapter, "_query_cli") as mock_query:
                result = adapter.search_topic(
                    "gold", platforms=["facebook", "instagram"]
                )
                assert result == []
                mock_query.assert_not_called()

    def test_days_clamped_to_range(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with patch.object(adapter, "_query_cli", return_value=[]) as mock_query:
                # days=0 → 1, days=100 → 30
                adapter.search_topic("gold", days=0)
                adapter.search_topic("gold", days=100)
                # _query_cli 应被调用 2 次 (因 cache key 不同)
                assert mock_query.call_count >= 2


# ============================================================
# 3. CLI 解析
# ============================================================
class TestCliParse:
    """CLI JSON 输出解析."""

    def test_parse_list_format(self) -> None:
        adapter = Last30DaysAdapter()
        stdout = json.dumps(_make_cli_output_list()).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit", "x"])
        assert len(signals) == 2
        assert signals[0].platform == "reddit"
        assert signals[0].sentiment_score == 0.6
        assert signals[1].platform == "x"
        assert signals[1].sentiment_score == -0.3

    def test_parse_dict_wrapper_format(self) -> None:
        adapter = Last30DaysAdapter()
        stdout = json.dumps(_make_cli_output_dict_wrapper()).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit", "x"])
        assert len(signals) == 2

    def test_parse_invalid_json_returns_empty(self) -> None:
        adapter = Last30DaysAdapter()
        signals = adapter._parse_cli_output(b"not json", "gold", ["reddit"])
        assert signals == []

    def test_parse_unsupported_platform_filtered(self) -> None:
        adapter = Last30DaysAdapter()
        data = [
            {"platform": "reddit", "sentiment_score": 0.5},
            {"platform": "facebook", "sentiment_score": 0.8},  # 不支持
        ]
        stdout = json.dumps(data).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit"])
        assert len(signals) == 1
        assert signals[0].platform == "reddit"

    def test_parse_invalid_item_skipped(self) -> None:
        adapter = Last30DaysAdapter()
        data = [
            {"platform": "reddit", "sentiment_score": 0.5},
            "not a dict",  # 应被跳过
            {"platform": "x", "sentiment_score": "invalid"},  # 类型错误应被跳过
        ]
        stdout = json.dumps(data).encode("utf-8")
        signals = adapter._parse_cli_output(stdout, "gold", ["reddit", "x"])
        # 只有第一条应被保留 (第三条 sentiment_score 不是 float/int 也应失败)
        # 实际 float("invalid") 会抛 ValueError, 该条被跳过
        assert len(signals) == 1


# ============================================================
# 4. 失败安全 (subprocess 异常)
# ============================================================
class TestFailSafe:
    """CLI 调用异常必须降级为空列表."""

    def test_cli_not_available_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with patch.object(adapter, "_check_cli", return_value=False):
                result = adapter.search_topic("gold")
                assert result == []

    def test_cli_timeout_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with (
                patch.object(adapter, "_check_cli", return_value=True),
                patch(
                    "utils.last30days_adapter.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=30),
                ),
            ):
                result = adapter.search_topic("gold")
                assert result == []

    def test_cli_oserror_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with (
                patch.object(adapter, "_check_cli", return_value=True),
                patch(
                    "utils.last30days_adapter.subprocess.run",
                    side_effect=OSError("command not found"),
                ),
            ):
                result = adapter.search_topic("gold")
                assert result == []

    def test_cli_nonzero_returncode_returns_empty(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = b"some error"
            mock_result.stdout = b"{}"
            with (
                patch.object(adapter, "_check_cli", return_value=True),
                patch(
                    "utils.last30days_adapter.subprocess.run", return_value=mock_result
                ),
            ):
                # 清除缓存以避免命中
                adapter._cli_available = True
                with patch.object(adapter, "_load_cache", return_value=None):
                    result = adapter.search_topic("gold")
                    assert result == []


# ============================================================
# 5. 缓存
# ============================================================
class TestCache:
    """TTL 缓存机制."""

    def test_cache_hit_skips_cli(self, tmp_path: Path) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            # 预先写入缓存
            cache_key = adapter._cache_key("gold", ["reddit"], 7)
            cache_file = tmp_path / f"last30days_{cache_key}.json"
            signals_data = [_make_signal().to_dict()]
            cache_file.write_text(json.dumps(signals_data), encoding="utf-8")

            with (
                patch("utils.last30days_adapter._CACHE_DIR", tmp_path),
                patch.object(adapter, "_query_cli") as mock_query,
            ):
                result = adapter.search_topic("gold", platforms=["reddit"], days=7)
                assert len(result) == 1
                assert result[0].platform == "reddit"
                mock_query.assert_not_called()

    def test_cache_expired_requeries(self, tmp_path: Path) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter(cache_ttl=0)  # 立即过期
            cache_key = adapter._cache_key("gold", ["reddit"], 7)
            cache_file = tmp_path / f"last30days_{cache_key}.json"
            cache_file.write_text(
                json.dumps([_make_signal().to_dict()]), encoding="utf-8"
            )

            # 将文件 mtime 设为 1 小时前
            import os

            old_time = (datetime.now() - timedelta(hours=1)).timestamp()
            os.utime(cache_file, (old_time, old_time))

            with (
                patch("utils.last30days_adapter._CACHE_DIR", tmp_path),
                patch.object(
                    adapter,
                    "_query_cli",
                    return_value=[_make_signal()],
                ) as mock_query,
            ):
                result = adapter.search_topic("gold", platforms=["reddit"], days=7)
                assert len(result) == 1
                mock_query.assert_called_once()

    def test_save_cache_atomic_write(self, tmp_path: Path) -> None:
        adapter = Last30DaysAdapter()
        with patch("utils.last30days_adapter._CACHE_DIR", tmp_path):
            key = "testkey123"
            adapter._save_cache(key, [_make_signal()])
            cache_file = tmp_path / f"last30days_{key}.json"
            assert cache_file.exists()
            # 临时文件应已被清理
            tmp_file = cache_file.with_suffix(".json.tmp")
            assert not tmp_file.exists()


# ============================================================
# 6. 情感聚合
# ============================================================
class TestAggregateSentiment:
    """aggregate_sentiment 计算逻辑."""

    def test_empty_signals_returns_zeros(self) -> None:
        result = Last30DaysAdapter.aggregate_sentiment([])
        assert result["composite_sentiment"] == 0.0
        assert result["total_mentions"] == 0
        assert result["platform_count"] == 0

    def test_weighted_by_engagement(self) -> None:
        """互动量高的信号权重更大."""
        signals = [
            Last30DaysSignal(
                topic="t",
                platform="reddit",
                sentiment_score=1.0,
                engagement_score=1000.0,
            ),
            Last30DaysSignal(
                topic="t", platform="x", sentiment_score=-1.0, engagement_score=100.0
            ),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        # 加权: (1.0*1000 + -1.0*100) / 1100 = 900/1100 ≈ 0.818
        assert result["composite_sentiment"] > 0.7
        assert result["platform_count"] == 2

    def test_no_engagement_falls_back_to_mean(self) -> None:
        signals = [
            Last30DaysSignal(
                topic="t", platform="reddit", sentiment_score=0.6, engagement_score=0.0
            ),
            Last30DaysSignal(
                topic="t", platform="x", sentiment_score=0.4, engagement_score=0.0
            ),
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        # 简单平均: (0.6 + 0.4) / 2 = 0.5
        assert abs(result["composite_sentiment"] - 0.5) < 0.01

    def test_positive_negative_ratio(self) -> None:
        signals = [
            Last30DaysSignal(topic="t", platform="reddit", sentiment_score=0.5),
            Last30DaysSignal(topic="t", platform="x", sentiment_score=0.3),
            Last30DaysSignal(topic="t", platform="hn", sentiment_score=-0.5),
            Last30DaysSignal(
                topic="t", platform="youtube", sentiment_score=0.0
            ),  # 中性
        ]
        result = Last30DaysAdapter.aggregate_sentiment(signals)
        # positive: >0.1 → 2 个; negative: <-0.1 → 1 个; 共 4 个
        assert result["positive_ratio"] == 0.5
        assert result["negative_ratio"] == 0.25


# ============================================================
# 7. 审计
# ============================================================
class TestAudit:
    """查询审计 JSONL 写入."""

    def test_audit_written_on_success(self, tmp_path: Path) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with (
                patch("utils.last30days_adapter._AUDIT_DIR", tmp_path),
                patch.object(adapter, "_query_cli", return_value=[_make_signal()]),
                patch.object(adapter, "_save_cache"),
            ):
                adapter.search_topic("gold", platforms=["reddit"], days=7)

            # 审计文件应存在
            audit_files = list(tmp_path.glob("query_*.jsonl"))
            assert len(audit_files) == 1
            line = audit_files[0].read_text(encoding="utf-8").strip()
            record = json.loads(line)
            assert record["topic"] == "gold"
            assert record["signal_count"] == 1
            assert "aggregated" in record

    def test_audit_not_written_when_no_signals(self, tmp_path: Path) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            with (
                patch("utils.last30days_adapter._AUDIT_DIR", tmp_path),
                patch.object(adapter, "_query_cli", return_value=[]),
                patch.object(adapter, "_save_cache"),
            ):
                adapter.search_topic("gold", platforms=["reddit"], days=7)

            # 无信号时不应写审计
            audit_files = list(tmp_path.glob("query_*.jsonl"))
            assert len(audit_files) == 0


# ============================================================
# 8. 健康检查 + 单例
# ============================================================
class TestHealthAndSingleton:
    """健康检查和单例模式."""

    def test_get_health_structure(self) -> None:
        with patch("utils.last30days_adapter.is_enabled", return_value=True):
            adapter = Last30DaysAdapter()
            adapter._cli_available = True
            health = adapter.get_health()
            assert "flag_enabled" in health
            assert "cli_available" in health
            assert "supported_platforms" in health
            assert "cache_ttl" in health
            assert "reddit" in health["supported_platforms"]

    def test_get_adapter_returns_singleton(self) -> None:
        """get_adapter 应返回同一实例."""
        # 重置单例
        import utils.last30days_adapter as mod

        mod._adapter = None
        a1 = get_adapter()
        a2 = get_adapter()
        assert a1 is a2

    def test_module_level_search_topic(self) -> None:
        """模块级快捷函数应正常工作."""
        with patch("utils.last30days_adapter.is_enabled", return_value=False):
            result = search_topic("gold")
            assert result == []


# ============================================================
# 9. 数据结构
# ============================================================
class TestLast30DaysSignal:
    """Last30DaysSignal 数据类."""

    def test_to_dict_roundtrip(self) -> None:
        sig = _make_signal()
        d = sig.to_dict()
        assert d["topic"] == "gold price"
        assert d["platform"] == "reddit"
        sig2 = Last30DaysSignal(**d)
        assert sig2.topic == sig.topic
        assert sig2.sentiment_score == sig.sentiment_score

    def test_default_values(self) -> None:
        sig = Last30DaysSignal(topic="t", platform="x")
        assert sig.sentiment_score == 0.0
        assert sig.mention_count == 0
        assert sig.source_urls == []
        assert sig.title == ""
