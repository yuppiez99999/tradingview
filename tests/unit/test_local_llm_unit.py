"""local_llm 单元测试 — 本地 LLM 推理客户端"""
from unittest.mock import MagicMock, patch

from utils.local_llm import (
    LocalLLMClient,
    get_local_llm,
    local_llm_available,
)


class TestLocalLLMClientInit:
    def test_defaults(self):
        client = LocalLLMClient()
        assert client.n_ctx == 2048
        assert client.n_gpu_layers == 0
        assert client.n_threads == 4
        assert client.temperature == 0.7
        assert client.max_tokens == 2048
        assert client._model is None
        assert client._available is None

    def test_custom_params(self):
        client = LocalLLMClient(
            model_path="/fake/model.gguf",
            n_ctx=4096,
            n_gpu_layers=10,
            n_threads=8,
            temperature=0.3,
            max_tokens=512,
        )
        assert client.model_path == "/fake/model.gguf"
        assert client.n_ctx == 4096
        assert client.n_gpu_layers == 10
        assert client.n_threads == 8
        assert client.temperature == 0.3
        assert client.max_tokens == 512

    def test_env_var_model_path(self):
        with patch.dict("os.environ", {"LOCAL_LLM_MODEL_PATH": "/env/model.gguf"}):
            client = LocalLLMClient()
            assert client.model_path == "/env/model.gguf"


class TestIsAvailable:
    def test_model_not_found(self):
        client = LocalLLMClient(model_path="/nonexistent/model.gguf")
        assert client.is_available() is False

    def test_cached_result(self):
        client = LocalLLMClient(model_path="/nonexistent/model.gguf")
        client.is_available()
        result2 = client.is_available()
        assert result2 is False

    def test_llama_cpp_not_installed(self):
        client = LocalLLMClient(model_path="/fake/path.gguf")
        with patch("os.path.exists", return_value=True):
            with patch.dict("sys.modules", {"llama_cpp": None}):
                with patch("builtins.__import__", side_effect=ImportError):
                    assert client.is_available() is False


class TestFormatPrompt:
    def test_user_only(self):
        client = LocalLLMClient()
        result = client._format_prompt([{"role": "user", "content": "hello"}])
        assert "<|im_start|>user" in result
        assert "hello" in result
        assert "<|im_end|>" in result
        assert result.endswith("<|im_start|>assistant\n")

    def test_system_and_user(self):
        client = LocalLLMClient()
        result = client._format_prompt([
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ])
        assert "<|im_start|>system" in result
        assert "you are helpful" in result
        assert "<|im_start|>user" in result
        assert "hi" in result

    def test_assistant_role(self):
        client = LocalLLMClient()
        result = client._format_prompt([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "q2"},
        ])
        assert "<|im_start|>assistant" in result
        assert "a" in result

    def test_empty_messages(self):
        client = LocalLLMClient()
        result = client._format_prompt([])
        assert result == "<|im_start|>assistant\n"

    def test_unknown_role_skipped(self):
        client = LocalLLMClient()
        result = client._format_prompt([{"role": "unknown", "content": "x"}])
        assert "x" not in result


class TestChat:
    def test_chat_response(self):
        client = LocalLLMClient(model_path="/fake.gguf")
        mock_model = MagicMock(return_value={
            "choices": [{"text": "test response"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        with patch.object(client, "_load_model", return_value=mock_model):
            response = client.chat([{"role": "user", "content": "hi"}])
        assert response["choices"][0]["message"]["content"] == "test response"
        assert response["model"] == "local-qwen2.5-72b"
        assert response["usage"]["prompt_tokens"] == 10

    def test_chat_custom_temp(self):
        client = LocalLLMClient(model_path="/fake.gguf")
        mock_model = MagicMock(return_value={
            "choices": [{"text": "resp"}],
            "usage": {},
        })
        with patch.object(client, "_load_model", return_value=mock_model):
            client.chat([{"role": "user", "content": "hi"}], temperature=0.1, max_tokens=100)
        call_kwargs = mock_model.call_args[1]
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["max_tokens"] == 100


class TestStreamResponse:
    def test_stream(self):
        client = LocalLLMClient()
        chunks = [
            {"choices": [{"text": "a"}]},
            {"choices": [{"text": "b"}]},
            {"choices": [{"text": ""}]},
        ]
        result = list(client._stream_response(chunks))
        assert result == ["a", "b"]


class TestGenerate:
    def test_generate_with_system(self):
        client = LocalLLMClient(model_path="/fake.gguf")
        mock_model = MagicMock(return_value={
            "choices": [{"text": "generated"}],
            "usage": {},
        })
        with patch.object(client, "_load_model", return_value=mock_model):
            result = client.generate("hello", system_prompt="be helpful")
        assert result == "generated"

    def test_generate_without_system(self):
        client = LocalLLMClient(model_path="/fake.gguf")
        mock_model = MagicMock(return_value={
            "choices": [{"text": "no system"}],
            "usage": {},
        })
        with patch.object(client, "_load_model", return_value=mock_model):
            result = client.generate("hello")
        assert result == "no system"


class TestGetLocalLLM:
    def test_returns_none_if_unavailable(self):
        import utils.local_llm as mod
        old = mod._default_client
        mod._default_client = None
        try:
            result = get_local_llm()
            assert result is None
        finally:
            mod._default_client = old

    def test_returns_client_if_available(self):
        import utils.local_llm as mod
        old = mod._default_client
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mod._default_client = mock_client
        try:
            result = get_local_llm()
            assert result is mock_client
        finally:
            mod._default_client = old


class TestLocalLLMAvailable:
    def test_false_when_unavailable(self):
        import utils.local_llm as mod
        old = mod._default_client
        mod._default_client = None
        try:
            assert local_llm_available() is False
        finally:
            mod._default_client = old

    def test_true_when_available(self):
        import utils.local_llm as mod
        old = mod._default_client
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mod._default_client = mock_client
        try:
            assert local_llm_available() is True
        finally:
            mod._default_client = old
