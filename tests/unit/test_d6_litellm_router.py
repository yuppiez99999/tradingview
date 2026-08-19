"""D6 LiteLLM Router 单元测试.

覆盖:
    - types: ChatRequest/ChatResponse/Usage/ProviderInfo 数据类
    - LiteLLMRouter: chat/chat_simple/chat_deep/get_stats/reset_stats 单例
    - 场景路由: SCENE_PROVIDER_MAP / SCENE_TEMPERATURE_MAP
    - glm5_client 重构: GLM5Client.chat 返回 dict / is_ready / test_connection
    - 向后兼容: get_glm5_client / quick_chat
    - 错误降级: inner_router=None / chat 异常

运行:
    python -m pytest tests/unit/test_d6_litellm_router.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.llm_gateway import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LiteLLMRouter,
    ProviderInfo,
    Usage,
)
from utils.llm_gateway.types import (
    SCENE_PROVIDER_MAP,
    SCENE_TEMPERATURE_MAP,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_inner_router() -> MagicMock:
    """Mock 内部 LLMRouter."""
    router = MagicMock()
    router.chat.return_value = "测试回复"
    router.chat_deep.return_value = "深度回复"
    router._fallback_chain = ["deepseek", "doubao", "glm"]
    router._providers_config = {
        "deepseek": {"model": "deepseek-chat"},
        "doubao": {"model": "doubao-pro-32k"},
    }
    return router


@pytest.fixture
def router(mock_inner_router: MagicMock) -> LiteLLMRouter:
    """带 mock inner router 的 LiteLLMRouter."""
    LiteLLMRouter.reset_instance()
    r = LiteLLMRouter(inner_router=mock_inner_router)
    return r


# ============================================================
# 1. types 数据类
# ============================================================


class TestTypes:
    def test_usage(self) -> None:
        u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 20
        assert u.total_tokens == 30
        d = u.as_dict()
        assert d["prompt_tokens"] == 10

    def test_provider_info(self) -> None:
        p = ProviderInfo(name="deepseek", model="deepseek-chat", latency_ms=100.0)
        assert p.name == "deepseek"
        assert p.success is True

    def test_chat_message(self) -> None:
        m = ChatMessage(role="user", content="hello")
        assert m.as_dict() == {"role": "user", "content": "hello"}

    def test_chat_request_default(self) -> None:
        req = ChatRequest(prompt="测试")
        assert req.prompt == "测试"
        assert req.system == ""
        assert req.scene == "default"

    def test_chat_request_from_messages(self) -> None:
        msgs = [
            ChatMessage(role="system", content="系统提示"),
            ChatMessage(role="user", content="用户问题"),
        ]
        req = ChatRequest.from_messages(msgs, scene="intraday")
        assert req.system == "系统提示"
        assert req.prompt == "用户问题"
        assert req.scene == "intraday"

    def test_chat_response_success(self) -> None:
        provider = ProviderInfo(name="deepseek", model="deepseek-chat")
        resp = ChatResponse(content="回复", provider=provider)
        assert resp.success is True
        assert resp.content == "回复"

    def test_chat_response_failed(self) -> None:
        provider = ProviderInfo(name="none", model="none", success=False, error="失败")
        resp = ChatResponse(content="", provider=provider)
        assert resp.success is False

    def test_scene_provider_map(self) -> None:
        assert SCENE_PROVIDER_MAP["intraday"] == "deepseek"
        assert SCENE_PROVIDER_MAP["report"] == "doubao"

    def test_scene_temperature_map(self) -> None:
        assert SCENE_TEMPERATURE_MAP["intraday"] == 0.1
        assert SCENE_TEMPERATURE_MAP["report"] == 0.5


# ============================================================
# 2. LiteLLMRouter 单例
# ============================================================


class TestLiteLLMRouterSingleton:
    def test_get_instance(self) -> None:
        LiteLLMRouter.reset_instance()
        r1 = LiteLLMRouter.get_instance()
        r2 = LiteLLMRouter.get_instance()
        assert r1 is r2

    def test_reset_instance(self) -> None:
        r1 = LiteLLMRouter.get_instance()
        LiteLLMRouter.reset_instance()
        r2 = LiteLLMRouter.get_instance()
        assert r1 is not r2


# ============================================================
# 3. LiteLLMRouter.chat
# ============================================================


class TestLiteLLMRouterChat:
    def test_chat_success(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        req = ChatRequest(prompt="你好", scene="intraday")
        resp = router.chat(req)
        assert resp.success is True
        assert resp.content == "测试回复"
        assert resp.provider.name == "deepseek"
        assert resp.provider.model == "deepseek-chat"
        assert resp.usage.total_tokens > 0
        mock_inner_router.chat.assert_called_once()

    def test_chat_scene_temperature(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        """场景路由自动选择温度."""
        req = ChatRequest(prompt="测试", scene="intraday")
        router.chat(req)
        call_args = mock_inner_router.chat.call_args
        # intraday 场景温度 0.1
        assert call_args.kwargs["temperature"] == 0.1

    def test_chat_report_scene(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        req = ChatRequest(prompt="生成报告", scene="report")
        router.chat(req)
        call_args = mock_inner_router.chat.call_args
        assert call_args.kwargs["temperature"] == 0.5

    def test_chat_explicit_temperature(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        """显式温度覆盖场景温度."""
        req = ChatRequest(prompt="测试", temperature=0.8, scene="intraday")
        router.chat(req)
        call_args = mock_inner_router.chat.call_args
        assert call_args.kwargs["temperature"] == 0.8

    def test_chat_none_response(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        """inner router 返回 None."""
        mock_inner_router.chat.return_value = None
        req = ChatRequest(prompt="测试")
        resp = router.chat(req)
        assert resp.success is False
        assert "失败" in resp.provider.error

    def test_chat_exception(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        """inner router 抛异常."""
        mock_inner_router.chat.side_effect = RuntimeError("网络错误")
        req = ChatRequest(prompt="测试")
        resp = router.chat(req)
        assert resp.success is False
        assert "网络错误" in resp.provider.error

    def test_chat_simple(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        result = router.chat_simple("你好", scene="intraday")
        assert result == "测试回复"

    def test_chat_simple_failed(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        mock_inner_router.chat.return_value = None
        result = router.chat_simple("你好")
        assert result is None

    def test_chat_deep(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        req = ChatRequest(prompt="复杂分析")
        resp = router.chat_deep(req)
        assert resp.success is True
        assert resp.content == "深度回复"
        assert resp.provider.name == "deepseek-reasoner"
        mock_inner_router.chat_deep.assert_called_once()


# ============================================================
# 4. LiteLLMRouter 统计
# ============================================================


class TestLiteLLMRouterStats:
    def test_stats_empty(self, router: LiteLLMRouter) -> None:
        stats = router.get_stats()
        assert stats["total_calls"] == 0
        assert stats["success_rate"] == 0.0

    def test_stats_after_call(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        req = ChatRequest(prompt="测试")
        router.chat(req)
        stats = router.get_stats()
        assert stats["total_calls"] == 1
        assert stats["success_calls"] == 1
        assert stats["success_rate"] == 1.0
        assert stats["total_tokens"] > 0
        assert "deepseek" in stats["provider_stats"]

    def test_stats_failed_call(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        mock_inner_router.chat.return_value = None
        router.chat(ChatRequest(prompt="测试"))
        stats = router.get_stats()
        assert stats["total_calls"] == 1
        assert stats["success_calls"] == 0
        assert stats["success_rate"] == 0.0

    def test_reset_stats(self, router: LiteLLMRouter, mock_inner_router: MagicMock) -> None:
        router.chat(ChatRequest(prompt="测试"))
        router.reset_stats()
        stats = router.get_stats()
        assert stats["total_calls"] == 0
        assert stats["provider_stats"] == {}


# ============================================================
# 5. LiteLLMRouter 错误降级
# ============================================================


class TestLiteLLMRouterError:
    def test_inner_router_none(self) -> None:
        """inner_router=None 时降级."""
        LiteLLMRouter.reset_instance()
        r = LiteLLMRouter(inner_router=None)
        # mock _get_inner_router 返回 None
        with patch.object(r, "_get_inner_router", return_value=None):
            resp = r.chat(ChatRequest(prompt="测试"))
            assert resp.success is False
            assert "不可用" in resp.provider.error


# ============================================================
# 6. 模块级快捷函数
# ============================================================


class TestModuleFunctions:
    def test_chat_function(self, mock_inner_router: MagicMock) -> None:
        from utils.llm_gateway.litellm_router import chat
        LiteLLMRouter.reset_instance()
        # 创建实例并设为单例
        r = LiteLLMRouter(inner_router=mock_inner_router)
        with patch.object(LiteLLMRouter, "get_instance", return_value=r):
            result = chat("你好")
        assert result == "测试回复"

    def test_chat_with_response(self, mock_inner_router: MagicMock) -> None:
        from utils.llm_gateway.litellm_router import chat_with_response
        LiteLLMRouter.reset_instance()
        r = LiteLLMRouter(inner_router=mock_inner_router)
        with patch.object(LiteLLMRouter, "get_instance", return_value=r):
            resp = chat_with_response(ChatRequest(prompt="你好"))
        assert resp.content == "测试回复"


# ============================================================
# 7. GLM5Client 重构验证
# ============================================================


class TestGLM5Client:
    def test_glm5_config_default(self) -> None:
        from utils.glm5_client import GLM5Config
        cfg = GLM5Config()
        assert cfg.mode == "api"
        assert cfg.temperature == 0.3
        assert cfg.max_new_tokens == 3000

    def test_glm5_client_init(self) -> None:
        from utils.glm5_client import GLM5Client
        client = GLM5Client()
        assert client.config is not None
        assert client._router is None  # 延迟加载

    def test_glm5_client_is_ready(self) -> None:
        from utils.glm5_client import GLM5Client
        client = GLM5Client()
        # is_ready 应返回 bool (不抛异常)
        assert isinstance(client.is_ready(), bool)

    def test_glm5_client_chat_validation(self) -> None:
        """chat 输入验证."""
        from utils.glm5_client import GLM5Client
        client = GLM5Client()
        with pytest.raises(ValueError, match="不能为空"):
            client.chat("")
        with pytest.raises(ValueError, match="不能超过"):
            client.chat("x" * 100001)
        with pytest.raises(ValueError, match="temperature"):
            client.chat("test", temperature=3.0)
        with pytest.raises(ValueError, match="max_tokens"):
            client.chat("test", max_tokens=0)

    def test_glm5_client_chat_returns_dict(self) -> None:
        """chat 返回 dict 含 content 字段 (向后兼容)."""
        from utils.glm5_client import GLM5Client
        from utils.llm_gateway import ChatResponse, ProviderInfo, Usage

        # mock router
        mock_router = MagicMock()
        provider = ProviderInfo(name="deepseek", model="deepseek-chat")
        mock_router.chat.return_value = ChatResponse(
            content="回复内容", provider=provider, usage=Usage(10, 20, 30)
        )
        client = GLM5Client()
        client._router = mock_router
        result = client.chat("测试")
        assert isinstance(result, dict)
        assert result["role"] == "assistant"
        assert result["content"] == "回复内容"
        assert result["model"] == "deepseek-chat"
        assert result["provider"] == "deepseek"
        assert result["usage"]["total_tokens"] == 30

    def test_glm5_client_test_connection(self) -> None:
        from utils.glm5_client import GLM5Client
        from utils.llm_gateway import ChatResponse, ProviderInfo

        mock_router = MagicMock()
        mock_router.chat.return_value = ChatResponse(
            content="pong",
            provider=ProviderInfo(name="deepseek", model="deepseek-chat", latency_ms=50.0),
        )
        client = GLM5Client()
        client._router = mock_router
        result = client.test_connection()
        assert result["success"] is True
        assert result["provider"] == "deepseek"

    def test_glm5_client_get_stats(self) -> None:
        from utils.glm5_client import GLM5Client
        mock_router = MagicMock()
        mock_router.get_stats.return_value = {"total_calls": 5}
        client = GLM5Client()
        client._router = mock_router
        stats = client.get_stats()
        assert stats["total_calls"] == 5

    def test_glm5_client_chat_error(self) -> None:
        """chat 异常时返回含 error 的 dict."""
        from utils.glm5_client import GLM5Client
        mock_router = MagicMock()
        mock_router.chat.side_effect = RuntimeError("网络错误")
        client = GLM5Client()
        client._router = mock_router
        result = client.chat("测试")
        assert result["content"] == ""
        assert "error" in result


# ============================================================
# 8. 向后兼容: get_glm5_client / quick_chat
# ============================================================


class TestGLM5ClientCompat:
    def test_get_glm5_client_singleton(self) -> None:
        import utils.glm5_client as mod
        mod._glm5_instance = None  # 重置
        c1 = mod.get_glm5_client()
        c2 = mod.get_glm5_client()
        assert c1 is c2

    def test_quick_chat(self) -> None:
        """quick_chat 返回字符串."""
        import utils.glm5_client as mod
        from utils.llm_gateway import ChatResponse, ProviderInfo

        mock_router = MagicMock()
        mock_router.chat.return_value = ChatResponse(
            content="快速回复",
            provider=ProviderInfo(name="deepseek", model="deepseek-chat"),
        )
        mod._glm5_instance = None
        client = mod.get_glm5_client()
        client._router = mock_router
        result = mod.quick_chat("你好")
        assert result == "快速回复"
