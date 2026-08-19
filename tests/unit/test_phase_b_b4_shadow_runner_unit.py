"""Phase B B4 shadow runner 单元测试.

覆盖场景:
    1. 闭环正常: LLM 反馈闭环完整, KnowledgeBase 写入→读取→ideation 反哺
    2. LLM 不可用降级: LLM 模块缺失时降级至 B3, need_rollback=False
    3. 知识库写入失败: KnowledgeBase 写入异常, need_rollback=True + rollback_reason

对齐 tasks T1.3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.phase_b_b4_shadow_runner import (
    LLM_FAILURE_THRESHOLD,
    WARMUP_TARGET_DAYS,
    B3Status,
    B4ShadowResult,
    LLMLoopResult,
    _validate_llm_feedback_loop,
    check_b3_status,
    check_flag_invariant,
    run_shadow,
)

# ============================================================
# 场景 1: 闭环正常
# ============================================================

class TestLoopClosed:
    """LLM 反馈闭环正常: KnowledgeBase 写入→读取→ideation 反哺."""

    def test_run_shadow_returns_b4shadow_result(self):
        result = run_shadow()
        assert isinstance(result, B4ShadowResult)

    def test_run_shadow_loop_closed_when_kb_works(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=True,
            kb_write_success=True,
            kb_read_success=True,
            ideation_feedback_received=True,
            entries_written=1,
            entries_read=1,
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.loop_closed is True
        assert result.kb_write_success is True
        assert result.kb_read_success is True
        assert result.ideation_feedback_received is True

    def test_run_shadow_no_rollback_when_loop_closed(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=True,
            kb_write_success=True,
            kb_read_success=True,
            ideation_feedback_received=True,
            entries_written=1,
            entries_read=1,
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.need_rollback is False
        assert len(result.suggestion) > 0

    def test_run_shadow_suggestion_contains_entries(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=True,
            kb_write_success=True,
            kb_read_success=True,
            ideation_feedback_received=True,
            entries_written=3,
            entries_read=5,
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert "3" in result.suggestion
        assert "5" in result.suggestion


# ============================================================
# 场景 2: LLM 不可用降级
# ============================================================

class TestLLMUnavailableDegradation:
    """LLM 不可用: 降级至 B3, need_rollback=False, llm_available=False."""

    def test_run_shadow_llm_unavailable_degrades_to_b3(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=False,
            error_message="LLM 模块不可用 (降级至 B3): ImportError",
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.loop_closed is False
        assert result.llm_available is False
        assert result.need_rollback is False
        assert "降级" in result.suggestion

    def test_validate_llm_loop_with_import_error(self, monkeypatch):
        def raise_import_error(*args, **kwargs):
            raise ImportError("No module named 'utils.llm_evolution'")

        monkeypatch.setattr(
            "builtins.__import__",
            raise_import_error
        )
        result = _validate_llm_feedback_loop()
        assert result.loop_closed is False
        assert "不可用" in result.error_message or "ImportError" in result.error_message


# ============================================================
# 场景 3: 知识库写入失败
# ============================================================

class TestKnowledgeBaseWriteFailure:
    """知识库写入失败: need_rollback=True + rollback_reason 记录."""

    def test_run_shadow_kb_write_failure_triggers_rollback(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=False,
            kb_write_success=False,
            error_message="知识库写入失败: OSError",
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.kb_write_success is False
        assert result.need_rollback is True
        assert "知识库写入失败" in result.rollback_reason

    def test_run_shadow_kb_read_failure_triggers_rollback(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=False,
            kb_write_success=True,
            kb_read_success=False,
            error_message="知识库读取失败: OSError",
            entries_written=1,
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.kb_write_success is True
        assert result.kb_read_success is False
        assert result.need_rollback is True
        assert "知识库读取失败" in result.rollback_reason

    def test_run_shadow_kb_write_failure_suggestion(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=False,
            kb_write_success=False,
            error_message="知识库写入失败: disk full",
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert "需人工介入" in result.suggestion


# ============================================================
# 辅助测试: B3 状态检查 + Flag 不变式
# ============================================================

class TestB3StatusCheck:
    """B3 状态前置检查."""

    def test_check_b3_status_returns_b3status(self):
        result = check_b3_status()
        assert isinstance(result, B3Status)
        assert hasattr(result, "enabled")
        assert hasattr(result, "healthy")
        assert hasattr(result, "shadow_days")

    def test_check_b3_status_message_present(self):
        result = check_b3_status()
        assert len(result.message) > 0

    def test_check_b3_status_healthy_when_stable(self, tmp_path, monkeypatch):
        b3_file = tmp_path / "b3_shadow_status.json"
        b3_file.write_text(json.dumps({
            "warmup_days": WARMUP_TARGET_DAYS,
            "rollback_count": 0,
            "run_count": 7,
        }), encoding="utf-8")
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner.SHADOW_REPORT_DIR",
            tmp_path
        )
        result = check_b3_status()
        assert result.healthy is True
        assert result.shadow_days >= WARMUP_TARGET_DAYS

    def test_check_b3_status_unhealthy_when_rollback(self, tmp_path, monkeypatch):
        b3_file = tmp_path / "b3_shadow_status.json"
        b3_file.write_text(json.dumps({
            "warmup_days": WARMUP_TARGET_DAYS,
            "rollback_count": 2,
            "run_count": 7,
        }), encoding="utf-8")
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner.SHADOW_REPORT_DIR",
            tmp_path
        )
        result = check_b3_status()
        assert result.healthy is False


class TestFlagInvariant:
    """Feature flag 不变式校验."""

    def test_check_flag_invariant_returns_bool(self):
        result = check_flag_invariant()
        assert isinstance(result, bool)


# ============================================================
# 辅助测试: LLM 反馈闭环验证器
# ============================================================

class TestLLMFeedbackLoopValidator:
    """LLM 反馈闭环验证器."""

    def test_validate_returns_llmloop_result(self):
        result = _validate_llm_feedback_loop()
        assert isinstance(result, LLMLoopResult)

    def test_validate_with_none_paths(self):
        result = _validate_llm_feedback_loop(None)
        assert isinstance(result, LLMLoopResult)

    def test_validate_loop_closed_or_error_message(self):
        result = _validate_llm_feedback_loop()
        if result.loop_closed:
            assert result.kb_write_success is True
            assert result.kb_read_success is True
        else:
            assert len(result.error_message) > 0 or not result.ideation_feedback_received


# ============================================================
# 辅助测试: 降级护栏边界
# ============================================================

class TestDegradationGuardBoundary:
    """降级护栏边界条件."""

    def test_llm_available_when_loop_closed(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=True,
            kb_write_success=True,
            kb_read_success=True,
            ideation_feedback_received=True,
            entries_written=1,
            entries_read=1,
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.llm_available is True

    def test_no_rollback_when_llm_degrades_gracefully(self, monkeypatch):
        mock_loop_result = LLMLoopResult(
            loop_closed=False,
            error_message="LLM 模块不可用 (降级至 B3): timeout",
        )
        monkeypatch.setattr(
            "scripts.phase_b_b4_shadow_runner._validate_llm_feedback_loop",
            lambda *a, **kw: mock_loop_result
        )
        result = run_shadow()
        assert result.need_rollback is False
        assert result.llm_available is False

    def test_failure_threshold_constant(self):
        assert LLM_FAILURE_THRESHOLD == 3

    def test_warmup_target_days_constant(self):
        assert WARMUP_TARGET_DAYS == 7
