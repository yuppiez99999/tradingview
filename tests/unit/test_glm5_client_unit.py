"""test_glm5_client_unit.py — GLM-5 客户端单一入口 (call_glm 委托) 单元测试

覆盖要点 (item 12 GLM-5 路径归一):
    - GLM5Config (默认值/环境变量覆盖)
    - GLM5Client.chat (输入验证: 空消息/超长/temperature越界/max_tokens越界)
    - GLM5Client.chat 必须委托全系统唯一 GLM-5 实现 call_glm (路径归一 pin 测试)
    - GLM5Client.is_ready / test_connection / get_stats
    - get_glm5_client 单例
    - quick_chat
"""

from __future__ import annotations

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
        monkeypatch.setenv("ZHIPUAI_API_KEY", "test_key")
        cfg = GLM5Config()
        assert cfg.api_key == "test_key"

    @pytest.mark.unit
    def test_explicit_api_key_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_API_KEY", "env_key")
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


class TestApiKeyMissing:
    """GLM_API_KEY 未配置 (call_glm 前置条件不满足) → 失败降级 (fail-open)."""

    @pytest.mark.unit
    def test_chat_returns_empty_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        client = GLM5Client()
        result = client.chat("test message")
        assert result["content"] == ""
        assert "error" in result

    @pytest.mark.unit
    def test_is_ready_false_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        client = GLM5Client()
        assert client.is_ready() is False

    @pytest.mark.unit
    def test_test_connection_fails_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        client = GLM5Client()
        result = client.test_connection()
        assert result["success"] is False

    @pytest.mark.unit
    def test_get_stats_no_error(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        client = GLM5Client()
        stats = client.get_stats()
        assert "error" not in stats
        assert stats["total_calls"] == 0


# ============================================================
# GLM5Client 委托 call_glm (item 12 路径归一)
# ============================================================


class TestCallGlmDelegation:
    """GLM5Client.chat 必须走全系统唯一的 GLM-5 实现 call_glm (非 deepseek)."""

    @pytest.mark.unit
    def test_is_ready_true_with_api_key(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test_key")
        client = GLM5Client()
        assert client.is_ready() is True

    @pytest.mark.unit
    def test_chat_success_delegates_to_call_glm(self, monkeypatch):
        """chat 成功路径: 委托 call_glm 并返回其文本."""
        import utils.alpha.llm.providers.glm as glm_mod

        captured: dict = {}

        def fake_call_glm(
            prompt, system, temperature, max_tokens, timeout, provider_cfg
        ):
            captured["prompt"] = prompt
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            captured["provider_cfg"] = provider_cfg
            return "test reply"

        monkeypatch.setattr(glm_mod, "call_glm", fake_call_glm)
        client = GLM5Client()
        result = client.chat("test", temperature=0.5, max_tokens=128)
        assert result["content"] == "test reply"
        assert result["provider"] == "glm"
        assert captured["prompt"] == "test"
        assert captured["temperature"] == 0.5
        assert captured["max_tokens"] == 128
        assert captured["provider_cfg"] == {}

    @pytest.mark.unit
    def test_chat_uses_default_model(self, monkeypatch):
        monkeypatch.delenv("GLM_MODEL", raising=False)
        import utils.alpha.llm.providers.glm as glm_mod

        monkeypatch.setattr(glm_mod, "call_glm", lambda **kw: "x")
        client = GLM5Client()
        result = client.chat("test")
        assert result["model"] == "glm-5.2"

    @pytest.mark.unit
    def test_chat_none_from_call_glm_returns_error(self, monkeypatch):
        """call_glm 返回 None (无 Key/调用失败) → 空 content + error."""
        import utils.alpha.llm.providers.glm as glm_mod

        monkeypatch.setattr(glm_mod, "call_glm", lambda **kw: None)
        client = GLM5Client()
        result = client.chat("test")
        assert result["content"] == ""
        assert "error" in result

    @pytest.mark.unit
    def test_chat_exception_returns_error(self, monkeypatch):
        """call_glm 抛异常 → chat 捕获并返回 error (fail-open)."""
        import utils.alpha.llm.providers.glm as glm_mod

        def boom(**kw):
            raise RuntimeError("api down")

        monkeypatch.setattr(glm_mod, "call_glm", boom)
        client = GLM5Client()
        result = client.chat("test")
        assert result["content"] == ""
        assert "error" in result

    @pytest.mark.unit
    def test_stats_counts_calls(self, monkeypatch):
        import utils.alpha.llm.providers.glm as glm_mod

        monkeypatch.setattr(glm_mod, "call_glm", lambda **kw: "ok")
        client = GLM5Client()
        client.chat("a")
        client.chat("b")
        stats = client.get_stats()
        assert stats["total_calls"] == 2
        assert stats["success_calls"] == 2
        assert stats["success_rate"] == 1.0


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
        """quick_chat 返回 content 字符串 (委托 call_glm)"""
        import utils.alpha.llm.providers.glm as glm_mod
        import utils.glm5_client as mod

        monkeypatch.setattr(mod, "_glm5_instance", None)
        monkeypatch.setattr(glm_mod, "call_glm", lambda **kw: "reply")
        result = quick_chat("test")
        assert isinstance(result, str)
        assert result == "reply"
