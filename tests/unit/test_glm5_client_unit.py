"""test_glm5_client_unit.py — GLM-5 客户端 LiteLLMRouter 薄包装单元测试

覆盖要点:
    - GLM5Config (默认值/环境变量覆盖)
    - GLM5Client.chat (输入验证: 空消息/超长/temperature越界/max_tokens越界)
    - GLM5Client.chat (router 不可用返回空响应)
    - GLM5Client.is_ready / test_connection / get_stats
    - get_glm5_client 单例
    - quick_chat
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from utils.glm5_client import GLM5Client, GLM5Config, get_glm5_client, quick_chat


# ============================================================
# GLM5Config
# ============================================================


class TestGLM5Config:
    @pytest.mark.unit
    def test_defaults(self):
        cfg = GLM5Config()
        assert cfg.mode == "api"
        assert cfg.temperature == 0.3
        assert cfg.max_new_tokens == 3000

    @pytest.mark.unit
    def test_env_override_mode(self, monkeypatch):
        monkeypatch.setenv("GLM5_MODE", "ollama")
        cfg = GLM5Config()
        assert cfg.mode == "ollama"

    @pytest.mark.unit
    def test_env_override_api_key(self, monkeypatch):
        monkeypatch.setenv("VOLCENGINE_API_KEY", "test_key")
        cfg = GLM5Config()
        assert cfg.api_key == "test_key"

    @pytest.mark.unit
    def test_explicit_api_key_takes_priority(self, monkeypatch):
        monkeypatch.setenv("VOLCENGINE_API_KEY", "env_key")
        cfg = GLM5Config(api_key="explicit_key")
        assert cfg.api_key == "explicit_key"


# ============================================================
# GLM5Client 输入验证
# ============================================================


class TestChatValidation:
    @pytest.mark.unit
    def test_empty_message_raises(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="不能为空"):
            client.chat("")

    @pytest.mark.unit
    def test_whitespace_message_raises(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="不能为空"):
            client.chat("   ")

    @pytest.mark.unit
    def test_too_long_message_raises(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="超过100000"):
            client.chat("x" * 100001)

    @pytest.mark.unit
    def test_temperature_out_of_range(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="temperature"):
            client.chat("test", temperature=3.0)

    @pytest.mark.unit
    def test_negative_temperature(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="temperature"):
            client.chat("test", temperature=-0.1)

    @pytest.mark.unit
    def test_max_tokens_out_of_range(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat("test", max_tokens=0)

    @pytest.mark.unit
    def test_max_tokens_too_large(self):
        client = GLM5Client()
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat("test", max_tokens=100001)


# ============================================================
# GLM5Client router 不可用
# ============================================================


class TestRouterUnavailable:
    @pytest.mark.unit
    def test_chat_returns_empty_when_router_none(self, monkeypatch):
        """LiteLLMRouter 不可用 → 返回空 content"""
        client = GLM5Client()
        # mock _get_router 返回 None
        monkeypatch.setattr(client, "_get_router", lambda: None)
        result = client.chat("test message")
        assert result["content"] == ""
        assert "error" in result

    @pytest.mark.unit
    def test_is_ready_false_when_router_none(self, monkeypatch):
        client = GLM5Client()
        monkeypatch.setattr(client, "_get_router", lambda: None)
        assert client.is_ready() is False

    @pytest.mark.unit
    def test_test_connection_fails_when_router_none(self, monkeypatch):
        client = GLM5Client()
        monkeypatch.setattr(client, "_get_router", lambda: None)
        result = client.test_connection()
        assert result["success"] is False

    @pytest.mark.unit
    def test_get_stats_empty_when_router_none(self, monkeypatch):
        client = GLM5Client()
        monkeypatch.setattr(client, "_get_router", lambda: None)
        stats = client.get_stats()
        assert "error" in stats


# ============================================================
# GLM5Client router 可用 (mock)
# ============================================================


class TestRouterAvailable:
    @pytest.mark.unit
    def test_is_ready_true(self, monkeypatch):
        client = GLM5Client()
        mock_router = MagicMock()
        monkeypatch.setattr(client, "_get_router", lambda: mock_router)
        assert client.is_ready() is True

    @pytest.mark.unit
    def test_chat_success(self, monkeypatch):
        """mock router 返回正常响应"""
        client = GLM5Client()
        mock_router = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test reply"
        mock_response.provider.model = "glm-5"
        mock_response.provider.name = "zhipu"
        mock_response.provider.latency_ms = 100
        mock_response.usage.as_dict.return_value = {"prompt_tokens": 10}
        mock_router.chat.return_value = mock_response
        monkeypatch.setattr(client, "_get_router", lambda: mock_router)

        # mock ChatRequest
        mock_gateway = MagicMock()
        mock_gateway.ChatRequest = MagicMock()
        monkeypatch.setitem(sys.modules, "utils.llm_gateway", mock_gateway)

        result = client.chat("test")
        assert result["content"] == "test reply"
        assert result["model"] == "glm-5"

    @pytest.mark.unit
    def test_chat_exception_returns_error(self, monkeypatch):
        """router.chat 抛异常 → 返回 error"""
        client = GLM5Client()
        mock_router = MagicMock()
        mock_router.chat.side_effect = RuntimeError("api down")
        monkeypatch.setattr(client, "_get_router", lambda: mock_router)

        mock_gateway = MagicMock()
        mock_gateway.ChatRequest = MagicMock()
        monkeypatch.setitem(sys.modules, "utils.llm_gateway", mock_gateway)

        result = client.chat("test")
        assert result["content"] == ""
        assert "error" in result


# ============================================================
# 单例 + quick_chat
# ============================================================


class TestSingleton:
    @pytest.mark.unit
    def test_get_glm5_client_singleton(self, monkeypatch):
        """重置单例后, 两次调用返回同一实例"""
        import utils.glm5_client as mod
        monkeypatch.setattr(mod, "_glm5_instance", None)
        c1 = get_glm5_client()
        c2 = get_glm5_client()
        assert c1 is c2

    @pytest.mark.unit
    def test_quick_chat_returns_string(self, monkeypatch):
        """quick_chat 返回 content 字符串"""
        import utils.glm5_client as mod
        monkeypatch.setattr(mod, "_glm5_instance", None)
        client = get_glm5_client()
        monkeypatch.setattr(client, "_get_router", lambda: None)
        result = quick_chat("test")
        assert isinstance(result, str)