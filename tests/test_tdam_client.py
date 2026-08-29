"""tests/test_tdam_client.py — TDAM 客户端单元测试.

测试范围:
  1. TDAMConfig / TDAMSearchResult 数据类
  2. CircuitBreaker 三态转换 (CLOSED → OPEN → HALF_OPEN → CLOSED)
  3. TDAMClient offline 模式 (不发请求)
  4. TDAMClient Feature Flag 关闭时降级
  5. 缓存读写

运行:
    py -3.8 -m pytest tests/test_tdam_client.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保项目根在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.tdam_client import (
    CircuitBreaker,
    CircuitState,
    TDAMClient,
    TDAMConfig,
    TDAMSearchResult,
)


# ============================================================
# 数据类测试
# ============================================================
class TestTDAMConfig:
    """TDAMConfig 数据类测试."""

    def test_default_config(self):
        """默认配置."""
        config = TDAMConfig()
        assert config.base_url == "http://localhost:8125"
        assert config.timeout == 10
        assert config.max_retries == 2
        assert config.offline is False

    def test_custom_config(self):
        """自定义配置."""
        config = TDAMConfig(
            base_url="http://192.168.1.100:8125",
            timeout=30,
            offline=True,
        )
        assert config.base_url == "http://192.168.1.100:8125"
        assert config.timeout == 30
        assert config.offline is True


class TestTDAMSearchResult:
    """TDAMSearchResult 数据类测试."""

    def test_default_result(self):
        """默认结果 (失败态)."""
        result = TDAMSearchResult()
        assert result.success is False
        assert result.items == []
        assert result.degraded is False

    def test_success_result(self):
        """成功结果."""
        result = TDAMSearchResult(
            success=True,
            items=[{"title": "test", "content": "hello"}],
            total=1,
            latency_ms=42.5,
        )
        assert result.success is True
        assert result.total == 1
        assert result.items[0]["title"] == "test"

    def test_to_dict(self):
        """序列化为字典."""
        result = TDAMSearchResult(success=True, items=[{"a": 1}], total=1)
        d = result.to_dict()
        assert d["success"] is True
        assert d["total"] == 1
        assert d["items"] == [{"a": 1}]


# ============================================================
# 熔断器测试
# ============================================================
class TestCircuitBreaker:
    """熔断器三态转换测试."""

    def test_initial_state_closed(self):
        """初始状态为 CLOSED."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_open_after_threshold(self):
        """连续失败达阈值后熔断 (OPEN)."""
        cb = CircuitBreaker(threshold=3, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # 还未到阈值
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_half_open_after_reset(self):
        """熔断冷却后进入半开 (HALF_OPEN)."""
        cb = CircuitBreaker(threshold=1, reset_seconds=0)  # 立即冷却
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.1)  # 等待冷却
        assert cb.can_execute() is True  # 半开允许试探
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        """半开态成功 → 恢复 CLOSED."""
        cb = CircuitBreaker(threshold=1, reset_seconds=0)
        cb.record_failure()
        time.sleep(0.1)
        cb.can_execute()  # 触发半开
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_failure_reopens(self):
        """半开态失败 → 重新熔断 (OPEN)."""
        cb = CircuitBreaker(threshold=1, reset_seconds=0)
        cb.record_failure()
        time.sleep(0.1)
        cb.can_execute()  # 触发半开
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_count(self):
        """成功请求重置失败计数."""
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


# ============================================================
# TDAMClient 测试
# ============================================================
class TestTDAMClient:
    """TDAM 客户端测试."""

    def test_offline_mode_returns_degraded(self):
        """离线模式返回降级结果 (不发请求)."""
        client = TDAMClient(TDAMConfig(offline=True))
        # 离线模式也需要 flag 启用
        with patch.object(client, "_check_flag", return_value=True):
            result = client.search_memory("test query")
        assert result.success is False
        assert result.degraded is True
        assert "offline" in result.error

    def test_flag_disabled_returns_degraded(self):
        """Feature Flag 关闭时返回降级结果."""
        client = TDAMClient(TDAMConfig())
        with patch.object(client, "_check_flag", return_value=False):
            result = client.search_memory("test query")
        assert result.success is False
        assert result.degraded is True
        assert "feature_flag" in result.error

    def test_health_check_offline_returns_false(self):
        """离线模式健康检查返回 False."""
        client = TDAMClient(TDAMConfig(offline=True))
        assert client.health_check() is False

    def test_search_memory_success(self):
        """成功检索记忆."""
        client = TDAMClient(TDAMConfig())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [{"title": "气象因子", "content": "..."}],
            "total": 1,
        }

        with (
            patch.object(client, "_check_flag", return_value=True),
            patch.object(client._session, "get", return_value=mock_response),
        ):
            result = client.search_memory("气象因子")

        assert result.success is True
        assert result.total == 1
        assert result.items[0]["title"] == "气象因子"

    def test_search_memory_connection_failure_degrades(self):
        """连接失败后降级 (不发请求, 返回空结果)."""
        import requests as req

        client = TDAMClient(TDAMConfig(max_retries=0, timeout=1))

        with (
            patch.object(client, "_check_flag", return_value=True),
            patch.object(client._circuit, "can_execute", return_value=True),
            patch.object(
                client._session, "get", side_effect=req.ConnectionError("refused")
            ),
        ):
            result = client.search_memory("test")

        assert result.success is False
        assert result.degraded is True

    def test_circuit_open_returns_degraded(self):
        """熔断器开启时返回降级结果."""
        client = TDAMClient(TDAMConfig())
        client._circuit.state = CircuitState.OPEN
        client._circuit.last_failure_time = time.time()  # 刚熔断

        with patch.object(client, "_check_flag", return_value=True):
            result = client.search_memory("test")

        assert result.success is False
        assert result.degraded is True
        assert "circuit_open" in result.error


# ============================================================
# 缓存读写测试
# ============================================================
class TestCacheIO:
    """缓存读写测试."""

    def test_save_and_load_cache(self, tmp_path):
        """保存并读取缓存."""
        config = TDAMConfig(cache_dir=tmp_path)
        client = TDAMClient(config)

        result = TDAMSearchResult(
            success=True,
            items=[{"title": "test", "content": "hello"}],
            total=1,
            latency_ms=10.0,
        )

        cache_path = client.save_cache("test query", result, "2026-08-06")
        assert cache_path.exists()

        loaded = TDAMClient.load_cache(cache_path)
        assert loaded is not None
        assert loaded["query"] == "test query"
        assert loaded["date"] == "2026-08-06"
        assert loaded["result"]["success"] is True
        assert loaded["result"]["total"] == 1

    def test_load_nonexistent_cache(self, tmp_path):
        """读取不存在的缓存返回 None."""
        path = tmp_path / "nonexistent.json"
        assert TDAMClient.load_cache(path) is None

    def test_load_corrupted_cache(self, tmp_path):
        """读取损坏的缓存返回 None."""
        path = tmp_path / "corrupt.json"
        path.write_text("{invalid json", encoding="utf-8")
        assert TDAMClient.load_cache(path) is None
