"""test_llm_client_unit.py — 统一 LLM 客户端单元测试

覆盖要点:
    - chat: 主路径 GLM5 (返回 dict 剥 content) / 降级 legacy / 两者均不可用返回 None
    - chat: GLM5 异常降级 legacy / legacy 异常返回 None
    - generate_analysis (system 注入金融分析助手)
    - test_connection (glm5/legacy/available 三态)
    - quick_chat (返回 str, None → "")
    - chat_deep (max_tokens=4000)
    - _record_usage (落盘 jsonl, 价格计算)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import utils.llm_client as mod


@pytest.fixture(autouse=True)
def _reset_clients(monkeypatch):
    """每个测试前重置全局客户端状态"""
    monkeypatch.setattr(mod, "_glm5_client", None)
    monkeypatch.setattr(mod, "_legacy_client", None)
    monkeypatch.setattr(mod, "_clients_loaded", False)


# ============================================================
# chat: 主路径 GLM5
# ============================================================


class TestChatGLM5:
    @pytest.mark.unit
    def test_glm5_returns_content_string(self, monkeypatch):
        """GLM5 返回 dict {content, model, usage} → 剥出 content 字符串"""
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": "分析结果",
            "model": "glm-5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_client)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("分析一下")
        assert result == "分析结果"

    @pytest.mark.unit
    def test_glm5_empty_content_falls_to_legacy(self, monkeypatch):
        """GLM5 返回 content="" → 降级 legacy"""
        mock_glm5 = MagicMock()
        mock_glm5.chat.return_value = {"content": "", "model": "glm-5", "usage": {}}
        mock_legacy = MagicMock()
        mock_legacy.chat.return_value = "legacy 结果"
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result == "legacy 结果"

    @pytest.mark.unit
    def test_glm5_exception_falls_to_legacy(self, monkeypatch):
        """GLM5 chat 抛异常 → 降级 legacy"""
        mock_glm5 = MagicMock()
        mock_glm5.chat.side_effect = RuntimeError("api down")
        mock_legacy = MagicMock()
        mock_legacy.chat.return_value = "legacy 兜底"
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result == "legacy 兜底"

    @pytest.mark.unit
    def test_glm5_dict_without_content_key(self, monkeypatch):
        """GLM5 返回 dict 但无 content key → 降级 legacy"""
        mock_glm5 = MagicMock()
        mock_glm5.chat.return_value = {"model": "glm-5", "usage": {}}
        mock_legacy = MagicMock()
        mock_legacy.chat.return_value = "legacy"
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result == "legacy"


# ============================================================
# chat: 降级 legacy
# ============================================================


class TestChatLegacy:
    @pytest.mark.unit
    def test_legacy_returns_string(self, monkeypatch):
        """glm5 不可用, legacy 返回字符串"""
        mock_legacy = MagicMock()
        mock_legacy.chat.return_value = "legacy only"
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result == "legacy only"

    @pytest.mark.unit
    def test_legacy_exception_returns_none(self, monkeypatch):
        """legacy chat 抛异常 → 返回 None"""
        mock_legacy = MagicMock()
        mock_legacy.chat.side_effect = OSError("conn fail")
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result is None

    @pytest.mark.unit
    def test_legacy_empty_string_returns_none(self, monkeypatch):
        """legacy 返回空字符串 → chat 返回 None"""
        mock_legacy = MagicMock()
        mock_legacy.chat.return_value = ""
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat("test")
        assert result is None


# ============================================================
# chat: 两者均不可用
# ============================================================


class TestChatNoneAvailable:
    @pytest.mark.unit
    def test_both_none_returns_none(self, monkeypatch):
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.chat("test")
        assert result is None

    @pytest.mark.unit
    def test_both_none_with_system_prompt(self, monkeypatch):
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.chat("test", system="系统提示")
        assert result is None


# ============================================================
# generate_analysis
# ============================================================


class TestGenerateAnalysis:
    @pytest.mark.unit
    def test_generate_analysis_injects_system(self, monkeypatch):
        """generate_analysis 注入 system='你是一个专业的金融分析助手。'"""
        mock_glm5 = MagicMock()
        mock_glm5.chat.return_value = {
            "content": "分析",
            "model": "glm-5",
            "usage": {},
        }
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.generate_analysis("分析这只股票")
        assert result == "分析"
        # 验证 system_prompt 被注入
        call_kwargs = mock_glm5.chat.call_args
        assert call_kwargs.kwargs["system_prompt"] == "你是一个专业的金融分析助手。"

    @pytest.mark.unit
    def test_generate_analysis_no_clients_returns_none(self, monkeypatch):
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.generate_analysis("test")
        assert result is None


# ============================================================
# test_connection
# ============================================================


class TestConnection:
    @pytest.mark.unit
    def test_both_available(self, monkeypatch):
        mock_glm5 = MagicMock()
        mock_glm5.is_ready.return_value = True
        mock_legacy = MagicMock()
        mock_legacy.test_connection.return_value = {"success": True}
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)

        result = mod.test_connection()
        assert result["glm5"] is True
        assert result["legacy"] is True
        assert result["available"] is True

    @pytest.mark.unit
    def test_glm5_only(self, monkeypatch):
        mock_glm5 = MagicMock()
        mock_glm5.is_ready.return_value = True
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.test_connection()
        assert result["glm5"] is True
        assert result["legacy"] is False
        assert result["available"] is True

    @pytest.mark.unit
    def test_legacy_only(self, monkeypatch):
        mock_legacy = MagicMock()
        mock_legacy.test_connection.return_value = {"success": True}
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)

        result = mod.test_connection()
        assert result["glm5"] is False
        assert result["legacy"] is True
        assert result["available"] is True

    @pytest.mark.unit
    def test_neither_available(self, monkeypatch):
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.test_connection()
        assert result["glm5"] is False
        assert result["legacy"] is False
        assert result["available"] is False

    @pytest.mark.unit
    def test_glm5_is_ready_exception(self, monkeypatch):
        """glm5.is_ready 抛异常 → glm5=False"""
        mock_glm5 = MagicMock()
        mock_glm5.is_ready.side_effect = RuntimeError("err")
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.test_connection()
        assert result["glm5"] is False

    @pytest.mark.unit
    def test_legacy_test_connection_returns_bool(self, monkeypatch):
        """legacy.test_connection 返回 bool 而非 dict"""
        mock_legacy = MagicMock()
        mock_legacy.test_connection.return_value = True
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: mock_legacy)

        result = mod.test_connection()
        assert result["legacy"] is True


# ============================================================
# quick_chat / chat_deep
# ============================================================


class TestConvenience:
    @pytest.mark.unit
    def test_quick_chat_returns_string(self, monkeypatch):
        mock_glm5 = MagicMock()
        mock_glm5.chat.return_value = {
            "content": "结果",
            "model": "glm-5",
            "usage": {},
        }
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.quick_chat("test")
        assert isinstance(result, str)
        assert result == "结果"

    @pytest.mark.unit
    def test_quick_chat_none_returns_empty_string(self, monkeypatch):
        monkeypatch.setattr(mod, "_load_glm5", lambda: None)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)

        result = mod.quick_chat("test")
        assert result == ""

    @pytest.mark.unit
    def test_chat_deep_uses_larger_max_tokens(self, monkeypatch):
        """chat_deep 默认 max_tokens=4000"""
        mock_glm5 = MagicMock()
        mock_glm5.chat.return_value = {
            "content": "深度",
            "model": "glm-5",
            "usage": {},
        }
        monkeypatch.setattr(mod, "_load_glm5", lambda: mock_glm5)
        monkeypatch.setattr(mod, "_load_legacy", lambda: None)
        monkeypatch.setattr(mod, "_record_usage", lambda *a, **kw: None)

        result = mod.chat_deep("深度分析")
        assert result == "深度"
        call_kwargs = mock_glm5.chat.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4000


# ============================================================
# _record_usage
# ============================================================


class TestRecordUsage:
    @pytest.mark.unit
    def test_record_usage_writes_jsonl(self, monkeypatch, tmp_path):
        """_record_usage 落盘 jsonl, 包含成本计算"""
        log_path = tmp_path / "llm_usage.jsonl"
        monkeypatch.setattr(mod, "_USAGE_LOG", log_path)

        mod._record_usage("glm-5", prompt_tokens=1000, completion_tokens=500,
                          latency_ms=200, source="glm5")

        assert log_path.exists()
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").strip().split("\n")]
        assert len(rows) == 1
        row = rows[0]
        assert row["model"] == "glm-5"
        assert row["prompt_tokens"] == 1000
        assert row["completion_tokens"] == 500
        assert row["latency_ms"] == 200
        assert row["source"] == "glm5"
        # 成本 = 1000/1000 * 0.01 + 500/1000 * 0.03 = 0.01 + 0.015 = 0.025
        assert row["cost_cny"] == 0.025

    @pytest.mark.unit
    def test_record_usage_unknown_model_uses_default(self, monkeypatch, tmp_path):
        """未知模型使用 default 价格表"""
        log_path = tmp_path / "llm_usage.jsonl"
        monkeypatch.setattr(mod, "_USAGE_LOG", log_path)

        mod._record_usage("unknown-model", 1000, 0, 100, "test")
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").strip().split("\n")]
        # default input=0.01 → 1000/1000 * 0.01 = 0.01
        assert rows[0]["cost_cny"] == 0.01

    @pytest.mark.unit
    def test_record_usage_io_error_silent(self, monkeypatch, tmp_path):
        """IO 异常静默吞掉 (不影响主流程)"""
        # 指向一个不可能的路径 (文件名是已存在的目录)
        bad_path = tmp_path / "subdir"
        bad_path.mkdir()
        monkeypatch.setattr(mod, "_USAGE_LOG", bad_path)  # 这是目录, open 会失败

        # 不应抛异常
        mod._record_usage("glm-5", 100, 50, 10, "test")