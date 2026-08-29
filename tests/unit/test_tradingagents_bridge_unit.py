"""tradingagents_bridge 单元测试 — TradingAgents HTTP 桥接客户端

覆盖:
- TradingAgentsBridge: 初始化/可用性/端口检测/HTTP/分析/降级/中性结果
- get_bridge / analyze / is_available 便捷函数
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils import tradingagents_bridge
from utils.tradingagents_bridge import TradingAgentsBridge

# ============================================================
# TradingAgentsBridge
# ============================================================


class TestTradingAgentsBridge:
    """TradingAgentsBridge 桥接客户端测试"""

    def setup_method(self):
        tradingagents_bridge._default_bridge = None

    def test_init_defaults(self):
        b = TradingAgentsBridge()
        assert b.host == "127.0.0.1"
        assert b.port == 8490
        assert b.timeout == 120
        assert b._base_url == "http://127.0.0.1:8490"
        assert b._available is None
        assert b._last_check == 0.0

    def test_init_custom(self):
        b = TradingAgentsBridge(host="localhost", port=9000, timeout=60)
        assert b.host == "localhost"
        assert b.port == 9000
        assert b.timeout == 60
        assert b._base_url == "http://localhost:9000"

    def test_base_url_property(self):
        b = TradingAgentsBridge(host="x", port=1234)
        assert b.base_url == "http://x:1234"

    def test_is_available_port_closed(self):
        """端口不可达 → False"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=False):
            assert b.is_available() is False

    def test_is_available_caches(self):
        """缓存有效期内不重复检测"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=False) as m:
            assert b.is_available() is False
            assert b.is_available() is False
        assert m.call_count == 1

    def test_is_available_force_check(self):
        """force_check=True 忽略缓存"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=False) as m:
            assert b.is_available() is False
            assert b.is_available(force_check=True) is False
        assert m.call_count == 2

    def test_is_available_port_open_health_ok(self):
        """端口开 + 健康检查通过 → True"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=True):
            with patch.object(
                b,
                "_http_get",
                return_value={
                    "status": "ok",
                    "tradingagents_available": True,
                    "python_version": "3.11.0",
                },
            ):
                assert b.is_available() is True

    def test_is_available_port_open_health_bad(self):
        """端口开 + 健康检查 status!=ok → False"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=True):
            with patch.object(b, "_http_get", return_value={"status": "error"}):
                assert b.is_available() is False

    def test_is_available_port_open_health_no_ta(self):
        """端口开 + tradingagents_available=False → False"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=True):
            with patch.object(
                b,
                "_http_get",
                return_value={"status": "ok", "tradingagents_available": False},
            ):
                assert b.is_available() is False

    def test_is_available_health_exception(self):
        """健康检查异常 → False"""
        b = TradingAgentsBridge()
        with patch.object(b, "_check_port", return_value=True):
            with patch.object(b, "_http_get", side_effect=RuntimeError("boom")):
                assert b.is_available() is False

    def test_check_port_success(self):
        b = TradingAgentsBridge()
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock):
            assert b._check_port() is True

    def test_check_port_refused(self):
        b = TradingAgentsBridge()
        with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            assert b._check_port() is False

    def test_check_port_timeout(self):
        b = TradingAgentsBridge()
        with patch("socket.create_connection", side_effect=TimeoutError()):
            assert b._check_port() is False

    def test_get_analysts_unavailable(self):
        b = TradingAgentsBridge()
        with patch.object(b, "is_available", return_value=False):
            assert b.get_analysts() == []

    def test_get_analysts_success(self):
        b = TradingAgentsBridge()
        with patch.object(b, "is_available", return_value=True):
            with patch.object(
                b, "_http_get", return_value={"analysts": ["fundamental", "technical"]}
            ):
                assert b.get_analysts() == ["fundamental", "technical"]

    def test_get_analysts_no_field(self):
        b = TradingAgentsBridge()
        with (
            patch.object(b, "is_available", return_value=True),
            patch.object(b, "_http_get", return_value={}),
        ):
            assert b.get_analysts() == []

    def test_analyze_empty_ticker(self):
        b = TradingAgentsBridge()
        result = b.analyze("")
        assert result["action"] == "HOLD"
        assert result["source"] == "neutral"

    def test_analyze_unavailable_fallback(self):
        """微服务不可用 → 降级"""
        b = TradingAgentsBridge()
        with patch.object(b, "is_available", return_value=False):
            with patch.object(
                b,
                "_fallback_to_local",
                return_value={"action": "HOLD", "source": "fallback_local"},
            ):
                result = b.analyze("AAPL", "2026-08-01")
        assert result["source"] == "fallback_local"

    def test_analyze_success(self):
        """微服务可用 → 正常返回"""
        b = TradingAgentsBridge()
        mock_resp = {
            "decision": {"action": "BUY", "confidence": 0.8, "reasoning": "bullish"},
            "state_summary": {"tech": "up"},
            "ticker": "AAPL",
            "date": "2026-08-01",
            "timestamp": "2026-08-01 10:00:00",
        }
        with patch.object(b, "is_available", return_value=True):
            with patch.object(b, "_http_post", return_value=mock_resp):
                result = b.analyze("AAPL", "2026-08-01")
        assert result["action"] == "BUY"
        assert result["confidence"] == 0.8
        assert result["source"] == "tradingagents"

    def test_analyze_no_decision_field(self):
        """微服务返回无 decision → 降级"""
        b = TradingAgentsBridge()
        with patch.object(b, "is_available", return_value=True):
            with patch.object(b, "_http_post", return_value={"error": "bad"}):
                with patch.object(
                    b,
                    "_fallback_to_local",
                    return_value={"action": "HOLD", "source": "fallback_local"},
                ):
                    result = b.analyze("AAPL", "2026-08-01")
        assert result["source"] == "fallback_local"

    def test_analyze_url_error(self):
        """URLError → 降级"""
        from urllib.error import URLError

        b = TradingAgentsBridge()
        with patch.object(b, "is_available", return_value=True):
            with patch.object(b, "_http_post", side_effect=URLError("timeout")):
                with patch.object(
                    b,
                    "_fallback_to_local",
                    return_value={"action": "HOLD", "source": "fallback_local"},
                ):
                    result = b.analyze("AAPL", "2026-08-01")
        assert result["source"] == "fallback_local"

    def test_neutral_result(self):
        b = TradingAgentsBridge()
        r = b._neutral_result("AAPL", "2026-08-01", "test reason")
        assert r["action"] == "HOLD"
        assert r["confidence"] == 0.0
        assert r["source"] == "neutral"
        assert r["ticker"] == "AAPL"
        assert "test reason" in r["reasoning"]

    def test_neutral_result_no_reason(self):
        b = TradingAgentsBridge()
        r = b._neutral_result("AAPL", "2026-08-01")
        assert r["reasoning"] == "中性决策"

    def test_fallback_to_local_import_error(self):
        """本地 orchestrator 不可用 → 中性决策"""
        b = TradingAgentsBridge()
        with patch("builtins.__import__", side_effect=ImportError):
            r = b._fallback_to_local("AAPL", "2026-08-01")
        assert r["source"] == "neutral"

    def test_http_get_success(self):
        b = TradingAgentsBridge()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("utils.tradingagents_bridge.urlopen", return_value=mock_resp):
            result = b._http_get("/test")
        assert result == {"key": "value"}

    def test_http_get_exception(self):
        b = TradingAgentsBridge()
        with patch("utils.tradingagents_bridge.urlopen", side_effect=OSError("fail")):
            assert b._http_get("/test") is None

    def test_http_post_success(self):
        b = TradingAgentsBridge()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"result": "ok"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("utils.tradingagents_bridge.urlopen", return_value=mock_resp):
            result = b._http_post("/analyze", {"ticker": "AAPL"})
        assert result == {"result": "ok"}

    def test_http_post_url_error(self):
        from urllib.error import URLError

        b = TradingAgentsBridge()
        with patch("utils.tradingagents_bridge.urlopen", side_effect=URLError("fail")):
            assert b._http_post("/analyze", {}) is None


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """get_bridge / analyze / is_available 测试"""

    def setup_method(self):
        tradingagents_bridge._default_bridge = None

    def test_get_bridge_singleton(self):
        b1 = tradingagents_bridge.get_bridge()
        b2 = tradingagents_bridge.get_bridge()
        assert b1 is b2

    def test_get_bridge_creates_instance(self):
        b = tradingagents_bridge.get_bridge()
        assert isinstance(b, TradingAgentsBridge)

    def test_is_available_convenience(self):
        with patch.object(TradingAgentsBridge, "is_available", return_value=False):
            assert tradingagents_bridge.is_available() is False

    def test_analyze_convenience(self):
        with patch.object(
            TradingAgentsBridge, "analyze", return_value={"action": "HOLD"}
        ):
            result = tradingagents_bridge.analyze("")
        assert result["action"] == "HOLD"
