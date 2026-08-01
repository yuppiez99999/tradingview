# -*- coding: utf-8 -*-
"""LLMRouter 单元测试 — T2.1.

验证以下方面:
    1. 基础结构: 单例/线程安全/配置加载
    2. Feature Flag 透传 (HC-1): flag=False 时透传到旧 llm_client
    3. Fallback 链: P0 失败 → P1 接管 → ... → 全失败
    4. 审计日志: 成功/失败均记录 JSONL
    5. Provider 实现: 4 个 provider 调用逻辑
    6. 异常处理: 静默降级 vs 抛异常
    7. chat_deep 深度模式 + 降级
    8. 连通性探测
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# 导入被测模块
from utils.alpha.llm_router import (
    AllProvidersFailedError,
    CallRecord,
    LLMRouter,
    LLMRouterError,
    ProviderNotConfiguredError,
    chat,
    chat_deep,
    list_providers,
    reload,
    test_connection,
)
from utils.infra.feature_flags import FeatureFlags

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_router_and_flags():
    """每个测试前重置 LLMRouter 和 FeatureFlags 单例."""
    LLMRouter.reset_instance()
    FeatureFlags.reset_instance()
    yield
    LLMRouter.reset_instance()
    FeatureFlags.reset_instance()


@pytest.fixture
def flag_disabled():
    """Feature Flag 关闭 (默认状态, 透传到旧 llm_client)."""
    # FeatureFlags 默认值即为 False (HC-1)
    # 不需要做任何事, 仅仅是为了语义清晰
    yield


@pytest.fixture
def flag_enabled():
    """Feature Flag 开启 (启用新 LLMRouter)."""
    # 通过 reports/flag_overrides/ 写入覆盖文件
    override_dir = _PROJECT_ROOT / "reports" / "flag_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "USE_LLM_REPORT_ANALYZER.json"
    override_file.write_text(json.dumps({
        "flag_name": "USE_LLM_REPORT_ANALYZER",
        "enabled": True,
        "signer": "test",
        "co_signer": "test_cosigner",
        "reason": "test",
        "action": "enable",
        "timestamp": "2026-07-26T00:00:00Z",
    }), encoding="utf-8")

    FeatureFlags.reset_instance()
    yield

    # 清理
    if override_file.exists():
        override_file.unlink()


@pytest.fixture
def router_with_mocks(flag_enabled):
    """启用 flag 并注入 mock provider 的 router."""
    router = LLMRouter.get_instance()

    # 注入 mock provider (含 deepseek, 当前 fallback chain 第一位)
    mock_deepseek = MagicMock(return_value="[DeepSeek] 你好")
    mock_doubao = MagicMock(return_value="[豆包] 你好")
    mock_glm = MagicMock(return_value="[GLM] 你好")
    mock_siliconflow = MagicMock(return_value="[SiliconFlow] 你好")
    mock_ollama = MagicMock(return_value="[Ollama] 你好")

    router.register_provider("deepseek", mock_deepseek)
    router.register_provider("doubao", mock_doubao)
    router.register_provider("glm", mock_glm)
    router.register_provider("siliconflow", mock_siliconflow)
    router.register_provider("ollama", mock_ollama)

    yield router, {
        "deepseek": mock_deepseek,
        "doubao": mock_doubao,
        "glm": mock_glm,
        "siliconflow": mock_siliconflow,
        "ollama": mock_ollama,
    }


# ============================================================
# 1. 基础结构测试
# ============================================================

class TestLLMRouterBasics:
    """基础结构测试."""

    def test_singleton_pattern(self):
        """单例模式: 多次获取返回同一实例."""
        r1 = LLMRouter.get_instance()
        r2 = LLMRouter.get_instance()
        assert r1 is r2

    def test_reset_instance(self):
        """reset_instance 后获取新实例."""
        r1 = LLMRouter.get_instance()
        LLMRouter.reset_instance()
        r2 = LLMRouter.get_instance()
        assert r1 is not r2

    def test_singleton_thread_safety(self):
        """多线程并发获取单例."""
        instances: List[LLMRouter] = []
        errors: List[Exception] = []

        def worker():
            try:
                instances.append(LLMRouter.get_instance())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(instances) == 10
        assert all(inst is instances[0] for inst in instances)

    def test_default_fallback_chain(self):
        """默认 fallback 链: deepseek → doubao → glm → siliconflow → ollama."""
        router = LLMRouter.get_instance()
        assert router._fallback_chain == ["deepseek", "doubao", "glm", "siliconflow", "ollama"]

    def test_default_timeout_5_seconds(self):
        """默认超时 5 秒 (云 API, HC-2)."""
        router = LLMRouter.get_instance()
        assert router._default_timeout == 5

    def test_ollama_timeout_30_seconds(self):
        """Ollama 超时 30 秒 (本地模型加载慢)."""
        router = LLMRouter.get_instance()
        assert router._ollama_timeout == 30

    def test_silent_fallback_default_true(self):
        """默认静默降级 True (返回 None 而非抛异常)."""
        router = LLMRouter.get_instance()
        assert router._silent_fallback is True

    def test_audit_log_enabled_default_true(self):
        """默认审计日志启用."""
        router = LLMRouter.get_instance()
        assert router._audit_log_enabled is True

    def test_feature_flag_name(self):
        """Feature Flag 名称: USE_LLM_REPORT_ANALYZER."""
        router = LLMRouter.get_instance()
        assert router._feature_flag_name == "USE_LLM_REPORT_ANALYZER"


# ============================================================
# 2. Feature Flag 透传测试 (HC-1)
# ============================================================

class TestFeatureFlagPassthrough:
    """Feature Flag 透传测试."""

    def test_passthrough_when_flag_disabled(self, flag_disabled):
        """flag=False 时透传到旧 llm_client.chat (HC-1)."""
        router = LLMRouter.get_instance()

        # Mock 旧 llm_client.chat
        with patch.dict(sys.modules, {"llm_client": MagicMock(chat=MagicMock(return_value="legacy_reply"))}):
            result = router.chat("你好", system="system")

        assert result == "legacy_reply"

    def test_passthrough_no_temperature_max_tokens(self, flag_disabled):
        """透传时不传 temperature/max_tokens (旧接口兼容)."""
        router = LLMRouter.get_instance()

        legacy_chat = MagicMock(return_value="legacy")
        with patch.dict(sys.modules, {"llm_client": MagicMock(chat=legacy_chat)}):
            router.chat("prompt", "system")

        legacy_chat.assert_called_once()
        _args, kwargs = legacy_chat.call_args
        # 默认不传 temperature/max_tokens
        assert "temperature" not in kwargs
        assert "max_tokens" not in kwargs

    def test_passthrough_with_temperature_max_tokens(self, flag_disabled):
        """透传时显式传 temperature/max_tokens."""
        router = LLMRouter.get_instance()

        legacy_chat = MagicMock(return_value="legacy")
        with patch.dict(sys.modules, {"llm_client": MagicMock(chat=legacy_chat)}):
            router.chat("prompt", "system", temperature=0.5, max_tokens=1000)

        legacy_chat.assert_called_once_with("prompt", "system", temperature=0.5, max_tokens=1000)

    def test_passthrough_module_not_found(self, flag_disabled):
        """透传模块不存在时返回 None."""
        router = LLMRouter.get_instance()
        router._passthrough_module = "nonexistent_module_xyz"

        result = router.chat("prompt")
        assert result is None

    def test_passthrough_function_not_found(self, flag_disabled):
        """透传函数不存在时返回 None."""
        router = LLMRouter.get_instance()

        with patch.dict(sys.modules, {"llm_client": MagicMock(spec=[])}):
            result = router.chat("prompt")

        assert result is None


# ============================================================
# 3. Fallback 链测试
# ============================================================

class TestFallbackChain:
    """Fallback 链测试."""

    def test_first_provider_success(self, router_with_mocks):
        """P0 (deepseek) 成功, 不调用其他 provider."""
        router, mocks = router_with_mocks

        result = router.chat("你好")

        assert result == "[DeepSeek] 你好"
        mocks["deepseek"].assert_called_once()
        mocks["doubao"].assert_not_called()
        mocks["glm"].assert_not_called()
        mocks["siliconflow"].assert_not_called()
        mocks["ollama"].assert_not_called()

    def test_p0_fail_p1_takeover(self, router_with_mocks):
        """P0 失败 → P1 (doubao) 接管."""
        router, mocks = router_with_mocks
        mocks["deepseek"].return_value = None  # P0 软失败

        result = router.chat("你好")

        assert result == "[豆包] 你好"
        mocks["deepseek"].assert_called_once()
        mocks["doubao"].assert_called_once()

    def test_p0_p1_fail_p2_takeover(self, router_with_mocks):
        """P0 + P1 失败 → P2 (glm) 接管."""
        router, mocks = router_with_mocks
        mocks["deepseek"].return_value = None
        mocks["doubao"].return_value = None

        result = router.chat("你好")

        assert result == "[GLM] 你好"
        mocks["glm"].assert_called_once()

    def test_p0_p1_p2_fail_p3_takeover(self, router_with_mocks):
        """P0+P1+P2 失败 → P3 (siliconflow) 接管."""
        router, mocks = router_with_mocks
        mocks["deepseek"].return_value = None
        mocks["doubao"].return_value = None
        mocks["glm"].return_value = None

        result = router.chat("你好")

        assert result == "[SiliconFlow] 你好"
        mocks["siliconflow"].assert_called_once()

    def test_all_fail_silent_fallback(self, router_with_mocks):
        """所有 provider 失败, silent_fallback=True 返回 None."""
        router, mocks = router_with_mocks
        for m in mocks.values():
            m.return_value = None

        result = router.chat("你好")

        assert result is None

    def test_all_fail_raise_when_silent_false(self, flag_enabled):
        """所有 provider 失败, silent_fallback=False 抛 AllProvidersFailedError."""
        router = LLMRouter.get_instance()
        router._silent_fallback = False

        # 注入全部失败的 mock (含 deepseek, 当前 fallback chain 第一位)
        for name in ["deepseek", "doubao", "glm", "siliconflow", "ollama"]:
            router.register_provider(name, MagicMock(return_value=None))

        with pytest.raises(AllProvidersFailedError) as exc_info:
            router.chat("你好")

        # 验证异常信息
        assert "所有 provider 失败" in str(exc_info.value)
        assert exc_info.value.tried_providers == ["deepseek", "doubao", "glm", "siliconflow", "ollama"]

    def test_provider_exception_continues_fallback(self, router_with_mocks):
        """Provider 抛异常时继续 fallback."""
        router, mocks = router_with_mocks
        # deepseek (P0) 抛异常 → doubao (P1) 接管
        mocks["deepseek"].side_effect = RuntimeError("连接超时")
        mocks["doubao"].return_value = "[豆包] OK"

        result = router.chat("你好")

        assert result == "[豆包] OK"
        mocks["deepseek"].assert_called_once()
        mocks["doubao"].assert_called_once()

    def test_disabled_provider_skipped(self, flag_enabled):
        """已禁用的 provider 被跳过."""
        router = LLMRouter.get_instance()

        called: List[str] = []

        def make_mock(name):
            def fn(prompt, system, temp, tokens, timeout):
                called.append(name)
                return f"[{name}]"
            return fn

        router.register_provider("deepseek", make_mock("deepseek"))
        router.register_provider("doubao", make_mock("doubao"))
        router.register_provider("glm", make_mock("glm"))
        router.register_provider("siliconflow", make_mock("siliconflow"))
        router.register_provider("ollama", make_mock("ollama"))

        # 禁用 deepseek (P0), 期望 doubao (P1) 接管
        router._providers_config["deepseek"] = {"enabled": False, "timeout_seconds": 5}

        result = router.chat("你好")

        assert result == "[doubao]"
        assert "deepseek" not in called
        assert "doubao" in called


# ============================================================
# 4. 审计日志测试
# ============================================================

class TestAuditLog:
    """审计日志测试."""

    def test_success_recorded(self, router_with_mocks, tmp_path):
        """成功调用记录审计日志."""
        router, _mocks = router_with_mocks
        router._audit_log_dir = tmp_path
        router._audit_log_enabled = True

        router.chat("你好", system="system_prompt")

        # 查找日志文件
        log_files = list(tmp_path.glob("calls_*.jsonl"))
        assert len(log_files) == 1

        # 解析日志
        records = []
        with open(log_files[0], "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        assert len(records) >= 1
        success_records = [r for r in records if r["success"]]
        assert len(success_records) >= 1
        # deepseek 是 fallback chain 第一位, 成功时记录的 provider 应为 deepseek
        assert success_records[0]["provider"] == "deepseek"
        assert success_records[0]["response_preview"] == "[DeepSeek] 你好"

    def test_failure_recorded(self, router_with_mocks, tmp_path):
        """失败调用记录审计日志."""
        router, mocks = router_with_mocks
        router._audit_log_dir = tmp_path
        # deepseek 是 fallback chain 第一位, 让它软失败以触发审计记录
        mocks["deepseek"].return_value = None  # 软失败

        router.chat("你好")

        log_files = list(tmp_path.glob("calls_*.jsonl"))
        assert len(log_files) == 1

        records = []
        with open(log_files[0], "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        # 应该有失败记录 (deepseek) + 成功记录 (doubao)
        failure_records = [r for r in records if not r["success"]]
        assert len(failure_records) >= 1
        assert failure_records[0]["provider"] == "deepseek"
        assert failure_records[0]["error_type"] == "SoftFailure"

    def test_exception_recorded(self, router_with_mocks, tmp_path):
        """异常调用记录审计日志."""
        router, mocks = router_with_mocks
        router._audit_log_dir = tmp_path
        # deepseek (P0) 抛异常 → doubao (P1) 接管成功
        mocks["deepseek"].side_effect = RuntimeError("test error")

        router.chat("你好")

        log_files = list(tmp_path.glob("calls_*.jsonl"))
        records = []
        with open(log_files[0], "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        exception_records = [r for r in records if r["error_type"] == "RuntimeError"]
        assert len(exception_records) >= 1
        assert "test error" in exception_records[0]["error_message"]

    def test_audit_log_disabled(self, router_with_mocks, tmp_path):
        """禁用审计日志时不写入文件."""
        router, _mocks = router_with_mocks
        router._audit_log_dir = tmp_path
        router._audit_log_enabled = False

        router.chat("你好")

        log_files = list(tmp_path.glob("calls_*.jsonl"))
        assert len(log_files) == 0

    def test_call_record_to_dict(self):
        """CallRecord.to_dict() 序列化正确."""
        record = CallRecord(
            timestamp="2026-07-26T00:00:00Z",
            prompt="你好" * 100,  # 长 prompt
            system="system",
            provider="doubao",
            success=True,
            latency_ms=123.456,
            response_preview="reply",
        )

        d = record.to_dict()

        assert d["timestamp"] == "2026-07-26T00:00:00Z"
        assert d["provider"] == "doubao"
        assert d["success"] is True
        assert d["latency_ms"] == 123.46  # round(123.456, 2)
        # prompt 截断到 200 字符
        assert len(d["prompt_preview"]) == 200


# ============================================================
# 5. Provider 实现测试
# ============================================================

class TestProviderImplementation:
    """4 个 provider 实现测试."""

    def test_doubao_no_api_key_returns_none(self, flag_enabled):
        """豆包无 API Key 返回 None."""
        router = LLMRouter.get_instance()
        # 清除 API Key
        with patch.dict(os.environ, {"VOLCENGINE_API_KEY": ""}):
            result = router._call_doubao("prompt", "", 0.3, 100, 5)
        assert result is None

    def test_glm_no_api_key_returns_none(self, flag_enabled):
        """GLM 无 API Key 返回 None."""
        router = LLMRouter.get_instance()
        with patch.dict(os.environ, {"GLM_API_KEY": ""}):
            result = router._call_glm("prompt", "", 0.3, 100, 5)
        assert result is None

    def test_siliconflow_no_api_key_returns_none(self, flag_enabled):
        """SiliconFlow 无 API Key 返回 None."""
        router = LLMRouter.get_instance()
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}):
            result = router._call_siliconflow("prompt", "", 0.3, 100, 5)
        assert result is None

    def test_ollama_no_api_key_still_callable(self, flag_enabled):
        """Ollama 不需要 API Key, 应该尝试调用."""
        router = LLMRouter.get_instance()

        # Mock urllib.request.urlopen 模拟成功响应
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ollama reply"}}]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = router._call_ollama("prompt", "", 0.3, 100, 30)

        assert result == "ollama reply"

    def test_openai_compatible_chat_success(self, flag_enabled):
        """OpenAI 兼容接口调用成功."""
        router = LLMRouter.get_instance()

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "test reply"}}]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = router._openai_compatible_chat(
                base_url="https://api.test.com/v1",
                api_key="test_key",
                model="test-model",
                prompt="你好",
                system="system",
                temperature=0.3,
                max_tokens=100,
                timeout=5,
            )

        assert result == "test reply"

    def test_openai_compatible_chat_auth_error(self, flag_enabled):
        """OpenAI 兼容接口 401 认证错误不重试."""
        router = LLMRouter.get_instance()
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       url="https://api.test.com",
                       code=401,
                       msg="Unauthorized",
                       hdrs=None,
                       fp=None,
                   )):
            result = router._openai_compatible_chat(
                base_url="https://api.test.com/v1",
                api_key="bad_key",
                model="test-model",
                prompt="你好",
                system="",
                temperature=0.3,
                max_tokens=100,
                timeout=5,
            )

        assert result is None


# ============================================================
# 6. chat_deep 深度模式测试
# ============================================================

class TestChatDeep:
    """chat_deep 深度推理模式测试."""

    def test_chat_deep_passthrough(self, flag_disabled):
        """flag=False 时 chat_deep 透传到旧 llm_client.chat_deep."""
        router = LLMRouter.get_instance()

        legacy_module = MagicMock()
        legacy_module.chat_deep = MagicMock(return_value="deep_legacy")

        with patch.dict(sys.modules, {"llm_client": legacy_module}):
            result = router.chat_deep("复杂决策")

        assert result == "deep_legacy"

    def test_chat_deep_passthrough_no_chat_deep_func(self, flag_disabled):
        """旧 llm_client 没有 chat_deep 时降级到 chat."""
        router = LLMRouter.get_instance()

        legacy_module = MagicMock(spec=["chat"])  # 只有 chat, 没有 chat_deep
        legacy_module.chat = MagicMock(return_value="legacy_chat")

        with patch.dict(sys.modules, {"llm_client": legacy_module}):
            result = router.chat_deep("复杂决策")

        assert result == "legacy_chat"

    def test_chat_deep_success(self, flag_enabled):
        """flag=True 时 chat_deep 调用 Ollama deep model."""
        router = LLMRouter.get_instance()

        # Mock _call_deepseek_reasoner 返回 None (跳过 DeepSeek R1 云端)
        # Mock _call_ollama_deep 返回 deep_reply (本地 deep model 接管)
        with patch.object(router, "_call_deepseek_reasoner", return_value=None), \
             patch.object(router, "_call_ollama_deep", return_value="deep_reply") as mock_deep:
            result = router.chat_deep("复杂决策", system="system")

        assert result == "deep_reply"
        mock_deep.assert_called_once()

    def test_chat_deep_fallback_to_chat(self, flag_enabled):
        """chat_deep 失败时降级到普通 chat."""
        router = LLMRouter.get_instance()

        # _call_deepseek_reasoner 和 _call_ollama_deep 都返回 None
        # 触发降级到普通 chat
        with patch.object(router, "_call_deepseek_reasoner", return_value=None), \
             patch.object(router, "_call_ollama_deep", return_value=None), \
             patch.object(router, "chat", return_value="normal_reply") as mock_chat:
            result = router.chat_deep("复杂决策")

        assert result == "normal_reply"
        mock_chat.assert_called_once()


# ============================================================
# 7. 连通性探测与列表测试
# ============================================================

class TestConnectionAndList:
    """连通性探测与 provider 列表测试."""

    def test_list_providers(self, flag_enabled):
        """list_providers 返回 5 个 provider 配置."""
        router = LLMRouter.get_instance()

        providers = router.list_providers()

        assert len(providers) == 5
        names = [p["name"] for p in providers]
        assert names == ["deepseek", "doubao", "glm", "siliconflow", "ollama"]

    def test_list_providers_in_fallback_chain(self, flag_enabled):
        """所有 provider 都在 fallback 链中."""
        router = LLMRouter.get_instance()

        providers = router.list_providers()

        for p in providers:
            assert p["in_fallback_chain"] is True

    def test_list_providers_api_key_status(self, flag_enabled):
        """provider 列表反映 API Key 配置状态."""
        router = LLMRouter.get_instance()

        # 无 API Key
        with patch.dict(os.environ, {"VOLCENGINE_API_KEY": "", "GLM_API_KEY": ""}):
            providers = router.list_providers()

        doubao = next(p for p in providers if p["name"] == "doubao")
        glm = next(p for p in providers if p["name"] == "glm")
        ollama = next(p for p in providers if p["name"] == "ollama")

        assert doubao["api_key_configured"] is False
        assert glm["api_key_configured"] is False
        assert ollama["api_key_configured"] is True  # Ollama 不需要 key

    def test_test_connection_returns_dict(self, flag_enabled):
        """test_connection 返回正确结构."""
        router = LLMRouter.get_instance()

        # 所有 provider 都返回 None (无可用), 必须包含 deepseek (fallback chain 第一位)
        for name in ["deepseek", "doubao", "glm", "siliconflow", "ollama"]:
            router.register_provider(name, MagicMock(return_value=None))

        result = router.test_connection()

        assert "providers" in result
        assert "available" in result
        assert "status" in result
        assert isinstance(result["providers"], dict)
        assert result["status"] == "degraded"  # 无可用

    def test_test_connection_available(self, flag_enabled):
        """test_connection 找到可用 provider."""
        router = LLMRouter.get_instance()

        # deepseek 返回 None (不可用), doubao 可用
        # deepseek 是 fallback chain 第一位, 必须 mock 以避免真实 API 调用
        router.register_provider("deepseek", MagicMock(return_value=None))
        router.register_provider("doubao", MagicMock(return_value="pong"))

        # 设置 API Key
        with patch.dict(os.environ, {"VOLCENGINE_API_KEY": "test_key"}):
            result = router.test_connection()

        assert result["available"] == "doubao"
        assert result["status"] == "ok"


# ============================================================
# 8. 模块级快捷函数测试
# ============================================================

class TestModuleLevelFunctions:
    """模块级快捷函数测试."""

    def test_chat_function_uses_singleton(self, flag_disabled):
        """chat() 使用单例."""
        # Mock 旧 llm_client
        legacy_chat = MagicMock(return_value="legacy")
        with patch.dict(sys.modules, {"llm_client": MagicMock(chat=legacy_chat)}):
            result = chat("你好")

        assert result == "legacy"
        legacy_chat.assert_called_once()

    def test_chat_deep_function_uses_singleton(self, flag_disabled):
        """chat_deep() 使用单例."""
        legacy_module = MagicMock()
        legacy_module.chat_deep = MagicMock(return_value="deep_legacy")

        with patch.dict(sys.modules, {"llm_client": legacy_module}):
            result = chat_deep("复杂决策")

        assert result == "deep_legacy"

    def test_test_connection_function(self, flag_enabled):
        """test_connection() 快捷函数."""
        router = LLMRouter.get_instance()
        # 必须包含 deepseek (fallback chain 第一位) 以避免真实 API 调用
        for name in ["deepseek", "doubao", "glm", "siliconflow", "ollama"]:
            router.register_provider(name, MagicMock(return_value=None))

        result = test_connection()

        assert "providers" in result
        assert "status" in result

    def test_list_providers_function(self, flag_enabled):
        """list_providers() 快捷函数."""
        result = list_providers()

        assert len(result) == 5

    def test_reload_function(self, flag_enabled):
        """reload() 快捷函数."""
        # 不抛异常即可
        reload()


# ============================================================
# 9. 异常类测试
# ============================================================

class TestExceptions:
    """异常类测试."""

    def test_llm_router_error(self):
        """LLMRouterError 基础异常."""
        with pytest.raises(LLMRouterError):
            raise LLMRouterError("test")

    def test_all_providers_failed_error_attributes(self):
        """AllProvidersFailedError 属性."""
        err = AllProvidersFailedError(
            message="all failed",
            tried_providers=["doubao", "glm"],
            last_error=RuntimeError("inner"),
        )

        assert "all failed" in str(err)
        assert err.tried_providers == ["doubao", "glm"]
        assert isinstance(err.last_error, RuntimeError)

    def test_all_providers_failed_error_no_last_error(self):
        """AllProvidersFailedError 无 last_error."""
        err = AllProvidersFailedError(
            message="all failed",
            tried_providers=["doubao"],
        )

        assert err.last_error is None

    def test_provider_not_configured_error(self):
        """ProviderNotConfiguredError 异常."""
        with pytest.raises(ProviderNotConfiguredError):
            raise ProviderNotConfiguredError("not configured")


# ============================================================
# 10. 配置加载测试
# ============================================================

class TestConfigLoading:
    """配置加载测试."""

    def test_reload_config(self, flag_enabled):
        """reload() 重新加载配置."""
        router = LLMRouter.get_instance()
        original_chain = router._fallback_chain.copy()

        # 修改配置 (模拟)
        router._fallback_chain = ["ollama"]
        assert router._fallback_chain == ["ollama"]

        # reload 恢复
        router.reload()
        assert router._fallback_chain == original_chain

    def test_register_custom_provider(self, flag_enabled):
        """注册自定义 provider."""
        router = LLMRouter.get_instance()

        custom_fn = MagicMock(return_value="custom reply")
        router.register_provider("custom", custom_fn)

        assert "custom" in router._provider_fns
        assert router._provider_fns["custom"] is custom_fn


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
