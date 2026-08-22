"""Skill 安全扫描单测 — mock subprocess, 不依赖真实 skillspector"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.skill_security_scan import (
    ScanResult,
    _parse_sarif,
    get_changed_skill_files,
    is_skill_file,
    run_precommit_check,
    scan_skills,
)


class TestIsSkillFile:
    def test_claude_skills(self):
        assert is_skill_file(Path(".claude/skills/foo/SKILL.md")) is True

    def test_skill_md(self):
        assert is_skill_file(Path("any/dir/SKILL.md")) is True

    def test_skills_dir_md(self):
        assert is_skill_file(Path("project/skills/my-skill.md")) is True

    def test_non_skill(self):
        assert is_skill_file(Path("utils/foo.py")) is False
        assert is_skill_file(Path("README.md")) is False


class TestGetChangedSkillFiles:
    def test_no_git(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert get_changed_skill_files(tmp_path) == []

    def test_filters_skill_files(self, tmp_path):
        skill_file = tmp_path / ".claude/skills/foo/SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=".claude/skills/foo/SKILL.md\nutils/foo.py\n",
                stderr="",
            )
            result = get_changed_skill_files(tmp_path)
            assert len(result) == 1
            assert result[0] == skill_file


class TestParseSarif:
    def test_parse_levels(self, tmp_path):
        sarif = tmp_path / "test.sarif"
        sarif.write_text(
            '{"runs": [{"results": ['
            '{"level": "error"}, {"level": "warning"}, '
            '{"level": "note"}, {"level": "none"}'
            "]}]}"
        )
        crit, high, med, low = _parse_sarif(sarif)
        assert (crit, high, med, low) == (1, 1, 1, 1)

    def test_parse_bad_json(self, tmp_path):
        sarif = tmp_path / "bad.sarif"
        sarif.write_text("not json")
        assert _parse_sarif(sarif) == (0, 0, 0, 0)


class TestScanSkills:
    def test_empty_targets(self):
        result = scan_skills([])
        assert result.available is True
        assert result.scanned == 0

    def test_not_available(self, tmp_path):
        with patch("scripts.skill_security_scan._check_available", return_value=False):
            result = scan_skills([tmp_path / "SKILL.md"])
            assert result.available is False
            assert "not installed" in result.error

    def test_scan_success_no_block(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text("test")
        with patch("scripts.skill_security_scan._check_available", return_value=True), \
             patch("scripts.skill_security_scan._parse_sarif", return_value=(0, 0, 1, 0)), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = scan_skills([skill], output_dir=tmp_path)
            assert result.available is True
            assert result.blocked is False
            assert result.medium == 1

    def test_scan_blocked(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text("test")
        with patch("scripts.skill_security_scan._check_available", return_value=True), \
             patch("scripts.skill_security_scan._parse_sarif", return_value=(1, 1, 0, 0)), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = scan_skills([skill], output_dir=tmp_path)
            assert result.blocked is True
            assert result.critical == 1
            assert result.high == 1

    def test_scan_timeout(self, tmp_path):
        import subprocess as sp

        skill = tmp_path / "SKILL.md"
        skill.write_text("test")
        with patch("scripts.skill_security_scan._check_available", return_value=True), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=1)):
            result = scan_skills([skill], output_dir=tmp_path)
            assert "超时" in result.error


class TestPrecommitCheck:
    def test_skip_env(self, tmp_path):
        with patch.dict("os.environ", {"SKIP_SKILL_SCAN": "1"}):
            passed, msg = run_precommit_check(tmp_path)
            assert passed is True
            assert "SKIP" in msg

    def test_no_changes(self, tmp_path):
        with patch.dict("os.environ", {"SKIP_SKILL_SCAN": ""}), \
             patch("scripts.skill_security_scan.get_changed_skill_files", return_value=[]):
            passed, msg = run_precommit_check(tmp_path)
            assert passed is True
            assert "无 skill" in msg

    def test_blocked(self, tmp_path):
        blocked_result = ScanResult(
            scanned=1, critical=1, high=0, blocked=True, available=True,
            sarif_path=Path("/tmp/x.sarif"),
        )
        with patch.dict("os.environ", {"SKIP_SKILL_SCAN": ""}), \
             patch("scripts.skill_security_scan.get_changed_skill_files", return_value=[Path("x")]), \
             patch("scripts.skill_security_scan.scan_skills", return_value=blocked_result):
            passed, msg = run_precommit_check(tmp_path)
            assert passed is False
            assert "阻断" in msg

    def test_not_available_pass(self, tmp_path):
        unavail = ScanResult(available=False, error="not installed")
        with patch.dict("os.environ", {"SKIP_SKILL_SCAN": ""}), \
             patch("scripts.skill_security_scan.get_changed_skill_files", return_value=[Path("x")]), \
             patch("scripts.skill_security_scan.scan_skills", return_value=unavail):
            passed, msg = run_precommit_check(tmp_path)
            assert passed is True
            assert "容错" in msg

    def test_exception_pass(self, tmp_path):
        with patch.dict("os.environ", {"SKIP_SKILL_SCAN": ""}), \
             patch("scripts.skill_security_scan.get_changed_skill_files", side_effect=RuntimeError("boom")):
            passed, msg = run_precommit_check(tmp_path)
            assert passed is True
            assert "容错" in msg
