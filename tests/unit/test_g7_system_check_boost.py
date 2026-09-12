"""G7 覆盖率冲刺 — system_check 补充测试

目标: 将 utils/system_check.py 覆盖率从约 56% 提升到 85%+
测试重点:
    - CheckResult / SystemCheckReport 数据结构
    - SystemChecker 初始化 (strict / skip_datasource / research_mode)
    - _get_critical_files / _get_critical_env_vars 平台适配
    - check_env_variables (C2)
    - check_python_dependencies (C5)
    - check_disk_and_permissions (C6)
    - check_subsystem_smoke (C7)
    - run_all / format_report 主流程
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.system_check import (  # noqa: E402
    CheckLevel,
    CheckResult,
    CheckStatus,
    SystemChecker,
    SystemCheckReport,
    _is_macos,
    _is_research_mode,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def checker_default():
    return SystemChecker(strict=False, skip_datasource=False)


@pytest.fixture
def checker_strict():
    return SystemChecker(strict=True, skip_datasource=True)


@pytest.fixture
def sample_report():
    return SystemCheckReport(
        check_time=now_bj().isoformat(),
        project_root=str(PROJECT_ROOT),
        total=2,
        passed=1,
        failed=1,
        skipped=0,
        warnings=0,
        blocking_failures=1,
        exit_code=1,
        results=[
            CheckResult(
                code="C1.1",
                name="test1",
                level=CheckLevel.ERROR,
                status=CheckStatus.PASS,
            ),
            CheckResult(
                code="C1.2",
                name="test2",
                level=CheckLevel.ERROR,
                status=CheckStatus.FAIL,
                remediation="fix",
            ),
        ],
        error_summary=["test2 failed"],
    )


# ============================================================
# 平台检测
# ============================================================


class TestPlatformDetection:
    def test_is_macos_returns_bool(self):
        assert isinstance(_is_macos(), bool)

    def test_is_research_mode_macos(self, monkeypatch):
        monkeypatch.setattr("utils.system_check.platform.system", lambda: "Darwin")
        assert _is_research_mode() is True

    def test_is_research_mode_windows_default(self, monkeypatch):
        monkeypatch.setattr("utils.system_check.platform.system", lambda: "Windows")
        monkeypatch.delenv("QUANT_RESEARCH_MODE", raising=False)
        assert _is_research_mode() is False

    def test_is_research_mode_windows_truthy(self, monkeypatch):
        monkeypatch.setattr("utils.system_check.platform.system", lambda: "Windows")
        monkeypatch.setenv("QUANT_RESEARCH_MODE", "1")
        assert _is_research_mode() is True

    def test_is_research_mode_windows_false_string(self, monkeypatch):
        monkeypatch.setattr("utils.system_check.platform.system", lambda: "Windows")
        monkeypatch.setenv("QUANT_RESEARCH_MODE", "false")
        assert _is_research_mode() is False


# ============================================================
# CheckResult / SystemCheckReport
# ============================================================


class TestDataClasses:
    def test_check_result_is_blocking_error_fail(self):
        r = CheckResult(
            code="C1", name="test", level=CheckLevel.ERROR, status=CheckStatus.FAIL
        )
        assert r.is_blocking is True

    def test_check_result_is_not_blocking_warn_fail(self):
        r = CheckResult(
            code="C1", name="test", level=CheckLevel.WARN, status=CheckStatus.FAIL
        )
        assert r.is_blocking is False

    def test_check_result_is_not_blocking_pass(self):
        r = CheckResult(
            code="C1", name="test", level=CheckLevel.ERROR, status=CheckStatus.PASS
        )
        assert r.is_blocking is False

    def test_report_all_passed_true(self, sample_report):
        sample_report.blocking_failures = 0
        sample_report.failed = 0
        assert sample_report.all_passed is True

    def test_report_all_passed_false(self, sample_report):
        assert sample_report.all_passed is False

    def test_report_properties(self, sample_report):
        assert sample_report.total == 2
        assert sample_report.passed == 1
        assert sample_report.failed == 1
        assert sample_report.blocking_failures == 1
        assert sample_report.exit_code == 1
        assert len(sample_report.results) == 2


# ============================================================
# SystemChecker 初始化与平台适配
# ============================================================


class TestSystemCheckerInit:
    def test_default_init(self):
        checker = SystemChecker()
        assert checker.strict is False
        assert checker.skip_datasource is False
        assert isinstance(checker.research_mode, bool)
        assert checker._results == []

    def test_strict_mode(self):
        checker = SystemChecker(strict=True)
        assert checker.strict is True

    def test_skip_datasource(self):
        checker = SystemChecker(skip_datasource=True)
        assert checker.skip_datasource is True

    def test_critical_files_windows(self, monkeypatch):
        monkeypatch.setattr("utils.system_check._is_research_mode", lambda: False)
        checker = SystemChecker()
        files = checker._get_critical_files()
        paths = [p for p, _ in files]
        assert "config/positions.json" in paths
        assert "utils/hedge_execution_engine.py" in paths

    def test_critical_files_research_mode(self, monkeypatch):
        monkeypatch.setattr("utils.system_check._is_research_mode", lambda: True)
        checker = SystemChecker()
        files = checker._get_critical_files()
        paths = [p for p, _ in files]
        assert "utils/hedge_execution_engine.py" not in paths
        assert "utils/risk_guard_integrator.py" not in paths

    def test_critical_env_vars_windows(self, monkeypatch):
        monkeypatch.setattr("utils.system_check._is_research_mode", lambda: False)
        checker = SystemChecker()
        critical, optional = checker._get_critical_env_vars()
        var_names = [v for v, _ in critical]
        assert "WIND_API_KEY" in var_names
        # API 重构: iFinD 数据源已移除, IFIND_TOKEN 不再是关键环境变量

    def test_critical_env_vars_research_mode(self, monkeypatch):
        monkeypatch.setattr("utils.system_check._is_research_mode", lambda: True)
        checker = SystemChecker()
        critical, optional = checker._get_critical_env_vars()
        assert critical == []
        assert any("Wind" in desc for _, desc in optional)


# ============================================================
# 注册辅助方法
# ============================================================


class TestRegisterHelpers:
    def test_pass_adds_result(self, checker_default):
        checker_default._pass("C1", "test", CheckLevel.ERROR, "ok", 1.0)
        assert len(checker_default._results) == 1
        assert checker_default._results[0].status == CheckStatus.PASS

    def test_fail_adds_result(self, checker_default):
        checker_default._fail("C1", "test", CheckLevel.ERROR, "fail", "fix", 2.0)
        assert len(checker_default._results) == 1
        assert checker_default._results[0].status == CheckStatus.FAIL

    def test_skip_adds_result(self, checker_default):
        checker_default._skip("C1", "test", CheckLevel.INFO, "skipped")
        assert len(checker_default._results) == 1
        assert checker_default._results[0].status == CheckStatus.SKIP

    def test_results_accumulate(self, checker_default):
        checker_default._pass("C1", "a", CheckLevel.INFO)
        checker_default._fail("C2", "b", CheckLevel.ERROR, remediation="r")
        checker_default._skip("C3", "c", CheckLevel.WARN)
        assert len(checker_default._results) == 3


# ============================================================
# C2 环境变量检查
# ============================================================


class TestCheckEnvVariables:
    def test_critical_var_set_shows_masked(self, checker_default, monkeypatch):
        monkeypatch.setenv("WIND_API_KEY", "abcdefghijklmnop")
        checker_default.check_env_variables()
        results = checker_default._results
        c2_results = [r for r in results if r.code.startswith("C2.")]
        wind = [r for r in c2_results if "WIND_API_KEY" in r.name]
        assert len(wind) == 1
        assert wind[0].status == CheckStatus.PASS
        assert "***" in wind[0].detail

    def test_critical_var_missing_fails(self, checker_default, monkeypatch):
        monkeypatch.delenv("WIND_API_KEY", raising=False)
        monkeypatch.delenv("IFIND_TOKEN", raising=False)
        monkeypatch.setenv("TS_TOKEN", "tok")
        checker_default.check_env_variables()
        results = checker_default._results
        c2_results = [r for r in results if r.code.startswith("C2.")]
        critical = [r for r in c2_results if r.level == CheckLevel.ERROR]
        assert any(r.status == CheckStatus.FAIL for r in critical)

    def test_optional_var_missing_is_warn(self, checker_default, monkeypatch):
        monkeypatch.delenv("TS_TOKEN", raising=False)
        checker_default.check_env_variables()
        results = checker_default._results
        ts = [r for r in results if "TS_TOKEN" in r.name]
        assert len(ts) == 1
        assert ts[0].level == CheckLevel.WARN
        assert ts[0].status == CheckStatus.FAIL


# ============================================================
# C5 Python 依赖检查
# ============================================================


class TestCheckPythonDependencies:
    def test_critical_module_importable(self, checker_default):
        checker_default.check_python_dependencies()
        results = checker_default._results
        c5 = [r for r in results if r.code.startswith("C5.")]
        pandas = [r for r in c5 if "pandas" in r.name.lower()]
        assert len(pandas) == 1
        assert pandas[0].status == CheckStatus.PASS

    def test_optional_module_missing_is_warn(self, checker_default):
        checker_default.check_python_dependencies()
        results = checker_default._results
        c5 = [r for r in results if r.code.startswith("C5.")]
        optional = [r for r in c5 if r.level == CheckLevel.WARN]
        assert len(optional) == 4

    def test_critical_module_mocked_unavailable(self, checker_default, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pandas":
                raise ImportError("no pandas")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        checker_default.check_python_dependencies()
        results = checker_default._results
        pandas = [r for r in results if "pandas" in r.name.lower()]
        assert len(pandas) == 1
        assert pandas[0].status == CheckStatus.FAIL


# ============================================================
# C6 磁盘与权限
# ============================================================


class TestCheckDiskAndPermissions:
    def test_writable_dirs_created(self, checker_default, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.system_check.PROJECT_ROOT", tmp_path)
        (tmp_path / "v8.3_institutional" / "trade_plans").mkdir(
            parents=True, exist_ok=True
        )
        (tmp_path / "v8.3_institutional" / "reports").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data_cache").mkdir(parents=True, exist_ok=True)
        (tmp_path / "每日报告归档").mkdir(parents=True, exist_ok=True)
        checker_default.check_disk_and_permissions()
        results = checker_default._results
        c6 = [r for r in results if r.code.startswith("C6.")]
        write_perms = [r for r in c6 if "写权限" in r.name]
        assert len(write_perms) == 4

    def test_disk_space_check(self, checker_default, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.system_check.PROJECT_ROOT", tmp_path)
        import shutil

        usage = MagicMock()
        usage.free = 10 * 1024**3
        usage.total = 100 * 1024**3
        usage.used = 90 * 1024**3
        monkeypatch.setattr(shutil, "disk_usage", lambda _: usage)
        checker_default.check_disk_and_permissions()
        results = checker_default._results
        disk = [r for r in results if "磁盘剩余空间" in r.name]
        assert len(disk) == 1
        assert disk[0].status == CheckStatus.PASS


# ============================================================
# C7 子系统 Smoke
# ============================================================


class TestSubsystemSmoke:
    def test_smoke_runs(self, checker_default):
        checker_default.check_subsystem_smoke()
        results = checker_default._results
        c7 = [r for r in results if r.code.startswith("C7.")]
        assert len(c7) >= 3

    def test_smoke_research_mode_skips_hedge(self, checker_default, monkeypatch):
        monkeypatch.setattr(checker_default, "research_mode", True)
        checker_default.check_subsystem_smoke()
        results = checker_default._results
        hedge = [r for r in results if "HedgeExecutionEngine" in r.name]
        assert len(hedge) == 1
        assert hedge[0].status == CheckStatus.SKIP


# ============================================================
# 主流程 run_all / format_report
# ============================================================


class TestRunAllAndReport:
    def test_run_all_returns_report(self, checker_default):
        report = checker_default.run_all()
        assert isinstance(report, SystemCheckReport)
        assert report.total > 0

    def test_run_all_strict_mode(self, checker_strict):
        report = checker_strict.run_all()
        assert isinstance(report, SystemCheckReport)

    def test_format_report_contains_summary(self, checker_default):
        report = checker_default.run_all()
        text = checker_default.format_report(report)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_run_all_report_has_timestamp(self, checker_default):
        report = checker_default.run_all()
        assert report.check_time
        datetime.fromisoformat(report.check_time)

    def test_run_all_results_list(self, checker_default):
        report = checker_default.run_all()
        assert isinstance(report.results, list)
        assert report.total == len(report.results)
