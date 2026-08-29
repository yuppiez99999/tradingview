"""T1.5 集成测试: assert_system_ready(auto_fix=True) + run_p0_startup_check --auto-fix.

验收标准:
    1. assert_system_ready(auto_fix=False) 行为不变 (向后兼容)
    2. assert_system_ready(auto_fix=True) 检测失败时尝试 L0/L1 修复后重检
    3. python scripts/run_p0_startup_check.py --auto-fix 可执行
    4. 修复日志写入 reports/system_check/auto_fix_log.jsonl
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.system_check import (
    CheckStatus,
    assert_system_ready,
    run_auto_fix_and_recheck,
)

# ============================================================
# Mock 辅助
# ============================================================


class FakeCheckResult:
    """模拟 CheckResult."""

    def __init__(
        self,
        code="C6.1",
        name="测试项",
        status=CheckStatus.FAIL,
        detail="",
        remediation="",
    ):
        self.code = code
        self.name = name
        self.status = status
        self.level = None
        self.detail = detail
        self.remediation = remediation
        self.elapsed_ms = 0.0


class FakeReport:
    """模拟 SystemCheckReport."""

    def __init__(self, exit_code=0, results=None):
        self.exit_code = exit_code
        self.results = results or []
        self.check_time = "2026-08-02T00:00:00"
        self.project_root = Path(".")


# ============================================================
# 向后兼容测试
# ============================================================


class TestBackwardCompat:
    def test_auto_fix_false_default(self):
        """auto_fix 默认 False (向后兼容)."""
        import inspect

        sig = inspect.signature(assert_system_ready)
        assert sig.parameters["auto_fix"].default is False

    def test_auto_fix_false_no_intervention(self, tmp_path, monkeypatch):
        """auto_fix=False 时不应调用 AutoFixEngine (即使有失败)."""
        # Mock run_system_check 返回失败报告
        fail_report = FakeReport(
            exit_code=1,
            results=[FakeCheckResult(code="C99.1", status=CheckStatus.FAIL)],
        )
        monkeypatch.setattr(
            "utils.system_check.run_system_check",
            lambda **kw: fail_report,
        )

        # auto_fix=False 应直接 sys.exit, 不调用 run_auto_fix_and_recheck
        with patch("utils.system_check.run_auto_fix_and_recheck") as mock_af:
            with pytest.raises(SystemExit):
                assert_system_ready(auto_fix=False)
            mock_af.assert_not_called()  # 不应调用 auto_fix


# ============================================================
# auto_fix=True 行为测试
# ============================================================


class TestAutoFixTrue:
    def test_auto_fix_true_calls_recheck(self, monkeypatch):
        """auto_fix=True 且有失败时应调用 run_auto_fix_and_recheck."""
        fail_report = FakeReport(
            exit_code=1,
            results=[FakeCheckResult(code="C6.1", status=CheckStatus.FAIL)],
        )
        # 首次返回失败, 第二次 (重检) 返回通过
        call_count = [0]

        def mock_run(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return fail_report  # 首次失败
            return FakeReport(exit_code=0, results=[])  # 重检通过

        monkeypatch.setattr("utils.system_check.run_system_check", mock_run)

        # Mock run_auto_fix_and_recheck 直接返回通过报告 (不实际修复)
        def mock_recheck(report, strict, skip_datasource):
            return FakeReport(exit_code=0, results=[])

        monkeypatch.setattr("utils.system_check.run_auto_fix_and_recheck", mock_recheck)

        # 不应抛 SystemExit (重检通过)
        report = assert_system_ready(auto_fix=True)
        assert report.exit_code == 0

    def test_auto_fix_true_still_exits_on_persistent_failure(self, monkeypatch):
        """auto_fix=True 但修复后仍失败, 应 sys.exit."""
        fail_report = FakeReport(
            exit_code=1,
            results=[FakeCheckResult(code="C99.1", status=CheckStatus.FAIL)],
        )
        monkeypatch.setattr(
            "utils.system_check.run_system_check",
            lambda **kw: fail_report,
        )

        # Mock run_auto_fix_and_recheck 返回仍失败的报告
        monkeypatch.setattr(
            "utils.system_check.run_auto_fix_and_recheck",
            lambda report, strict, skip_datasource: fail_report,
        )

        with pytest.raises(SystemExit):
            assert_system_ready(auto_fix=True)

    def test_auto_fix_true_pass_through_when_no_failure(self, monkeypatch):
        """auto_fix=True 但无失败时不应调用修复."""
        pass_report = FakeReport(exit_code=0, results=[])
        monkeypatch.setattr(
            "utils.system_check.run_system_check",
            lambda **kw: pass_report,
        )

        with patch("utils.system_check.run_auto_fix_and_recheck") as mock_af:
            report = assert_system_ready(auto_fix=True)
            assert report.exit_code == 0
            mock_af.assert_not_called()  # 无失败不修复


# ============================================================
# run_auto_fix_and_recheck 测试
# ============================================================


class TestRunAutoFixAndRecheck:
    def test_returns_report(self, monkeypatch):
        """run_auto_fix_and_recheck 应返回 SystemCheckReport."""
        fail_report = FakeReport(
            exit_code=1,
            results=[FakeCheckResult(code="C6.1", status=CheckStatus.FAIL)],
        )

        # Mock run_system_check 重检返回通过
        monkeypatch.setattr(
            "utils.system_check.run_system_check",
            lambda **kw: FakeReport(exit_code=0, results=[]),
        )

        result = run_auto_fix_and_recheck(
            report=fail_report, strict=False, skip_datasource=False
        )
        assert result is not None
        assert result.exit_code == 0  # 重检通过

    def test_writes_auto_fix_log(self, tmp_path, monkeypatch):
        """修复结果应写入 reports/system_check/auto_fix_log.jsonl."""
        fail_report = FakeReport(
            exit_code=1,
            results=[
                FakeCheckResult(code="C6.1", status=CheckStatus.FAIL, name="磁盘")
            ],
        )

        # 切换到临时目录, 让 _log_auto_fix_result 写到临时位置
        monkeypatch.chdir(tmp_path)

        # Mock run_system_check 重检
        monkeypatch.setattr(
            "utils.system_check.run_system_check",
            lambda **kw: FakeReport(exit_code=0, results=[]),
        )

        run_auto_fix_and_recheck(
            report=fail_report, strict=False, skip_datasource=False
        )

        log_path = tmp_path / "reports" / "system_check" / "auto_fix_log.jsonl"
        assert log_path.exists()

        # 验证日志内容
        content = log_path.read_text(encoding="utf-8").strip()
        lines = content.split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert "check_code" in entry
        assert "fix_action" in entry
        assert "fixed" in entry

    def test_import_error_tolerated(self, monkeypatch):
        """AutoFixEngine 导入失败应容错返回原报告."""
        fail_report = FakeReport(exit_code=1, results=[])

        # Mock import 抛 ImportError
        import builtins

        original_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if "auto_fix_engine" in name:
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)

        result = run_auto_fix_and_recheck(
            report=fail_report, strict=False, skip_datasource=False
        )
        # 容错返回原报告
        assert result is fail_report


# ============================================================
# CLI 集成测试
# ============================================================


class TestCLIIntegration:
    def test_auto_fix_flag_in_help(self):
        """--auto-fix 应出现在 --help 输出中."""
        project_root = Path(__file__).resolve().parents[3]
        script = project_root / "scripts" / "run_p0_startup_check.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert "--auto-fix" in result.stdout

    def test_cli_executable(self, tmp_path):
        """脚本应可执行 (exit code 0/1/2, 不 crash)."""
        project_root = Path(__file__).resolve().parents[3]
        script = project_root / "scripts" / "run_p0_startup_check.py"
        # 用 --skip-datasource 加速 + --auto-fix
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--skip-datasource",
                "--auto-fix",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=120,
        )
        # 退出码应为 0/1/2 (不 crash 即可)
        assert result.returncode in (0, 1, 2)
