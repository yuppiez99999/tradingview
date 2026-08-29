"""v87_release_gate 单元测试.

覆盖场景:
    1. Sprint 1 门禁全 PASS
    2. Sprint 1 门禁部分 FAIL
    3. 文件缺失 fail-closed
    4. JSON 解析失败

对齐 tasks T1.7.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.v87_release_gate import (
    DAILY_WORKFLOW_MAX_LINES,
    SPRINT1_COVERAGE_TARGET,
    GateCheckItem,
    ReleaseVerdict,
    SprintGateVerdict,
    _check_coverage,
    _check_daily_workflow_lines,
    _check_phase_b_stable_days,
    _check_r10_cleared,
    check_all,
    check_sprint_gate,
)

# ============================================================
# 场景 1: Sprint 1 门禁全 PASS
# ============================================================


class TestSprint1AllPass:
    """Sprint 1 门禁全 PASS."""

    def test_check_sprint_gate_returns_verdict(self):
        verdict = check_sprint_gate(1)
        assert isinstance(verdict, SprintGateVerdict)
        assert verdict.sprint == 1

    def test_check_sprint_gate_has_4_items(self):
        verdict = check_sprint_gate(1)
        assert len(verdict.items) == 4

    def test_check_sprint_gate_item_names(self):
        verdict = check_sprint_gate(1)
        names = [item.name for item in verdict.items]
        assert "phase_b_stable_days" in names
        assert "r10_bare_except_cleared" in names
        assert "g7_coverage" in names
        assert "daily_workflow_lines" in names

    def test_r10_always_passes(self):
        item = _check_r10_cleared()
        assert item.passed is True

    def test_all_passed_flag_consistent(self):
        verdict = check_sprint_gate(1)
        expected = all(item.passed for item in verdict.items)
        assert verdict.all_passed == expected

    def test_blocked_flag_opposite_of_all_passed(self):
        verdict = check_sprint_gate(1)
        assert verdict.blocked == (not verdict.all_passed)


# ============================================================
# 场景 2: Sprint 1 门禁部分 FAIL
# ============================================================


class TestSprint1PartialFail:
    """Sprint 1 门禁部分 FAIL."""

    def test_phase_b_fail_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.PHASE_B_STATUS_FILE",
            tmp_path / "nonexistent.json",
        )
        item = _check_phase_b_stable_days()
        assert item.passed is False
        assert "不存在" in item.current_value or "失败" in item.current_value

    def test_coverage_fail_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.COVERAGE_BASELINE_PATH",
            tmp_path / "nonexistent.json",
        )
        item = _check_coverage()
        assert item.passed is False

    def test_daily_workflow_fail_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.DAILY_WORKFLOW_PATH",
            tmp_path / "nonexistent.py",
        )
        item = _check_daily_workflow_lines()
        assert item.passed is False
        assert "不存在" in item.current_value

    def test_suggestions_populated_when_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.PHASE_B_STATUS_FILE",
            tmp_path / "nonexistent.json",
        )
        verdict = check_sprint_gate(1)
        if not verdict.all_passed:
            assert len(verdict.suggestions) > 0


# ============================================================
# 场景 3: 文件缺失 fail-closed
# ============================================================


class TestFileMissingFailClosed:
    """文件缺失时 fail-closed."""

    def test_phase_b_missing_returns_fail_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.PHASE_B_STATUS_FILE",
            tmp_path / "missing.json",
        )
        item = _check_phase_b_stable_days()
        assert isinstance(item, GateCheckItem)
        assert item.passed is False

    def test_coverage_missing_returns_fail_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.COVERAGE_BASELINE_PATH",
            tmp_path / "missing.json",
        )
        item = _check_coverage()
        assert isinstance(item, GateCheckItem)
        assert item.passed is False

    def test_daily_workflow_missing_returns_fail_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "scripts.v87_release_gate.DAILY_WORKFLOW_PATH",
            tmp_path / "missing.py",
        )
        item = _check_daily_workflow_lines()
        assert isinstance(item, GateCheckItem)
        assert item.passed is False


# ============================================================
# 场景 4: JSON 解析失败
# ============================================================


class TestJSONParseFailure:
    """JSON 解析失败时 fail-closed."""

    def test_phase_b_corrupted_json(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json}", encoding="utf-8")
        monkeypatch.setattr(
            "scripts.v87_release_gate.PHASE_B_STATUS_FILE",
            bad_file,
        )
        item = _check_phase_b_stable_days()
        assert item.passed is False
        assert "失败" in item.current_value or "error" in item.current_value.lower()

    def test_coverage_corrupted_json(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all", encoding="utf-8")
        monkeypatch.setattr(
            "scripts.v87_release_gate.COVERAGE_BASELINE_PATH",
            bad_file,
        )
        item = _check_coverage()
        assert item.passed is False


# ============================================================
# 辅助测试: Sprint 2/3 门禁 + 总验收骨架
# ============================================================


class TestSprint23Gate:
    """Sprint 2/3 门禁骨架."""

    def test_sprint_2_returns_verdict(self):
        verdict = check_sprint_gate(2)
        assert verdict.sprint == 2
        assert len(verdict.items) == 3

    def test_sprint_3_returns_verdict(self):
        verdict = check_sprint_gate(3)
        assert verdict.sprint == 3
        assert len(verdict.items) == 4

    def test_sprint_3_cvar_passed(self):
        verdict = check_sprint_gate(3)
        cvar_item = next(
            (i for i in verdict.items if i.name == "cvar_acceptance"), None
        )
        assert cvar_item is not None
        assert cvar_item.passed is True


class TestReleaseAllSkeleton:
    """v8.7 发布总验收骨架."""

    def test_check_all_returns_release_verdict(self):
        verdict = check_all()
        assert isinstance(verdict, ReleaseVerdict)

    def test_check_all_not_ready_skeleton(self):
        verdict = check_all()
        assert verdict.is_ready is False
        assert len(verdict.remaining_risks) > 0

    def test_check_all_has_items(self):
        verdict = check_all()
        assert len(verdict.items) >= 1


# ============================================================
# 辅助测试: 常量
# ============================================================


class TestConstants:
    """常量值校验."""

    def test_sprint1_coverage_target(self):
        assert SPRINT1_COVERAGE_TARGET == 0.55

    def test_daily_workflow_max_lines(self):
        assert DAILY_WORKFLOW_MAX_LINES == 3000
