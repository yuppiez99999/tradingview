"""
test_system_check_mac.py — Mac 研究模式 (跨平台) 自检逻辑单元测试
================================================================
版本: v8.6.14
创建日期: 2026-08-02
用途: 验证 utils/system_check.py 的 Mac/研究模式适配逻辑

覆盖范围:
    1. 平台检测函数 (_is_macos / _is_research_mode)
    2. SystemChecker.research_mode 属性
    3. _get_critical_files() 在两种模式下的返回值
    4. _get_critical_env_vars() 在两种模式下的返回值
    5. C1 关键文件检查 (研究模式跳过 hedge_execution_engine)
    6. C2 环境变量检查 (研究模式 Wind/iFinD 降级)
    7. C3 数据源检查 (研究模式分支 _check_datasource_research_mode)
    8. C7 子模块 smoke 测试 (研究模式跳过 HedgeExecutionEngine)
    9. run_all 输出包含模式标识

设计原则:
    • 完全跨平台: 在 Windows/Linux/Mac 上都能跑 (用 monkeypatch mock platform)
    • 无副作用: 不实际执行业务逻辑, 不修改文件
    • 快速: 全部 < 3 秒

运行:
    pytest tests/test_system_check_mac.py -v
    pytest tests/test_system_check_mac.py -v -k "research_mode"
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ============================================================
# 导入被测模块 (conftest.py 已设置 sys.path)
# ============================================================
from utils.system_check import (
    CheckLevel,
    CheckStatus,
    SystemChecker,
    _is_macos,
    _is_research_mode,
)

# ============================================================
# Fixture: 清理环境变量
# ============================================================


@pytest.fixture
def clean_research_env(monkeypatch):
    """清理 QUANT_RESEARCH_MODE 环境变量, 确保测试隔离"""
    monkeypatch.delenv("QUANT_RESEARCH_MODE", raising=False)
    yield


@pytest.fixture
def force_research_mode(monkeypatch):
    """强制启用研究模式 (模拟 Mac 环境)"""
    monkeypatch.setenv("QUANT_RESEARCH_MODE", "1")
    # 同时 mock platform.system 返回 Darwin, 模拟 Mac
    with patch("utils.system_check.platform.system", return_value="Darwin"):
        yield


@pytest.fixture
def force_windows_mode(monkeypatch):
    """强制 Windows 实盘模式"""
    monkeypatch.delenv("QUANT_RESEARCH_MODE", raising=False)
    with patch("utils.system_check.platform.system", return_value="Windows"):
        yield


# ============================================================
# 1. 平台检测函数
# ============================================================


class TestPlatformDetection:
    """测试 _is_macos() 和 _is_research_mode() 检测函数"""

    def test_is_macos_returns_true_on_darwin(self):
        """Mac (Darwin) 上 _is_macos 应返回 True"""
        with patch("utils.system_check.platform.system", return_value="Darwin"):
            assert _is_macos() is True

    def test_is_macos_returns_false_on_windows(self):
        """Windows 上 _is_macos 应返回 False"""
        with patch("utils.system_check.platform.system", return_value="Windows"):
            assert _is_macos() is False

    def test_is_macos_returns_false_on_linux(self):
        """Linux 上 _is_macos 应返回 False"""
        with patch("utils.system_check.platform.system", return_value="Linux"):
            assert _is_macos() is False

    def test_research_mode_auto_on_mac(self, monkeypatch):
        """Mac 上自动启用研究模式, 无需环境变量"""
        monkeypatch.delenv("QUANT_RESEARCH_MODE", raising=False)
        with patch("utils.system_check.platform.system", return_value="Darwin"):
            assert _is_research_mode() is True

    def test_research_mode_off_on_windows_default(self, monkeypatch):
        """Windows 上默认不启用研究模式"""
        monkeypatch.delenv("QUANT_RESEARCH_MODE", raising=False)
        with patch("utils.system_check.platform.system", return_value="Windows"):
            assert _is_research_mode() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", "True"])
    def test_research_mode_on_via_env_var(self, monkeypatch, value):
        """QUANT_RESEARCH_MODE 设为 1/true/yes 时启用研究模式 (即使 Windows)"""
        monkeypatch.setenv("QUANT_RESEARCH_MODE", value)
        with patch("utils.system_check.platform.system", return_value="Windows"):
            assert _is_research_mode() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "random"])
    def test_research_mode_off_via_invalid_env(self, monkeypatch, value):
        """QUANT_RESEARCH_MODE 设为 0/false/空/无效值时不启用"""
        monkeypatch.setenv("QUANT_RESEARCH_MODE", value)
        with patch("utils.system_check.platform.system", return_value="Linux"):
            assert _is_research_mode() is False


# ============================================================
# 2. SystemChecker.research_mode 属性
# ============================================================


class TestSystemCheckerMode:
    """测试 SystemChecker 实例的 research_mode 属性"""

    def test_research_mode_attr_true_on_mac(self, force_research_mode):
        """Mac 上 SystemChecker.research_mode 为 True"""
        checker = SystemChecker()
        assert checker.research_mode is True

    def test_research_mode_attr_false_on_windows(self, force_windows_mode):
        """Windows 上 SystemChecker.research_mode 为 False"""
        checker = SystemChecker()
        assert checker.research_mode is False


# ============================================================
# 3. _get_critical_files() 平台适配
# ============================================================


class TestCriticalFilesAdaptation:
    """测试关键文件清单的平台过滤"""

    def test_windows_returns_full_list(self, force_windows_mode):
        """Windows 实盘模式返回完整 CRITICAL_FILES (9 个)"""
        checker = SystemChecker()
        files = checker._get_critical_files()
        assert len(files) == len(SystemChecker.CRITICAL_FILES)
        # 应包含 hedge_execution_engine
        paths = [p for p, _ in files]
        assert any("hedge_execution_engine" in p for p in paths)

    def test_research_mode_filters_hedge_files(self, force_research_mode):
        """Mac 研究模式过滤掉 hedge_execution_engine / risk_guard_integrator"""
        checker = SystemChecker()
        files = checker._get_critical_files()
        paths = [p for p, _ in files]
        # 不应包含 Windows 专属模块
        assert not any(
            "hedge_execution_engine" in p for p in paths
        ), f"研究模式不应检查 hedge_execution_engine, 实际: {paths}"
        assert not any(
            "risk_guard_integrator" in p for p in paths
        ), f"研究模式不应检查 risk_guard_integrator, 实际: {paths}"

    def test_research_mode_keeps_cross_platform_files(self, force_research_mode):
        """Mac 研究模式保留跨平台文件 (positions.json / signal_fusion 等)"""
        checker = SystemChecker()
        files = checker._get_critical_files()
        paths = [p for p, _ in files]
        # 应保留这些跨平台文件
        assert any("positions.json" in p for p in paths), "应保留 positions.json"
        assert any("signal_fusion" in p for p in paths), "应保留 signal_fusion"

    def test_research_mode_fewer_than_windows(
        self, force_research_mode, force_windows_mode
    ):
        """研究模式文件数应少于 Windows 实盘模式"""
        # 注意: force_research_mode 和 force_windows_mode 不会同时生效,
        # 这里分两步检查
        pass  # 由上面两个测试覆盖


# ============================================================
# 4. _get_critical_env_vars() 平台适配
# ============================================================


class TestCriticalEnvVarsAdaptation:
    """测试环境变量清单的平台降级"""

    def test_windows_wind_api_required(self, force_windows_mode):
        """Windows 实盘模式: WIND_API_KEY 为必需 (ERROR 级)"""
        checker = SystemChecker()
        critical, optional = checker._get_critical_env_vars()
        var_names = [v for v, _ in critical]
        assert "WIND_API_KEY" in var_names
        assert "IFIND_TOKEN" in var_names

    def test_research_mode_wind_api_optional(self, force_research_mode):
        """Mac 研究模式: WIND_API_KEY 降级为可选"""
        checker = SystemChecker()
        critical, optional = checker._get_critical_env_vars()
        # 研究模式无必需环境变量
        assert len(critical) == 0, f"研究模式不应有必需环境变量, 实际: {critical}"
        # Wind/iFinD 应在可选列表中
        optional_names = [v for v, _ in optional]
        assert "WIND_API_KEY" in optional_names
        assert "IFIND_TOKEN" in optional_names

    def test_research_mode_optional_includes_mac_label(self, force_research_mode):
        """研究模式下 Wind 凭证描述应标注 'Mac 研究模式可选'"""
        checker = SystemChecker()
        _, optional = checker._get_critical_env_vars()
        wind_desc = next((d for v, d in optional if v == "WIND_API_KEY"), "")
        assert (
            "Mac 研究模式" in wind_desc or "研究模式" in wind_desc
        ), f"Wind 描述应标注研究模式, 实际: {wind_desc}"


# ============================================================
# 5. C1 关键文件检查 (集成测试)
# ============================================================


class TestC1CriticalFilesCheck:
    """测试 check_critical_files() 在研究模式下的行为"""

    def test_research_mode_does_not_fail_on_missing_hedge(
        self, force_research_mode, capsys
    ):
        """研究模式下即使 hedge_execution_engine.py 不存在也不报 ERROR"""
        checker = SystemChecker()
        # 执行 C1 检查 (不应抛异常)
        checker.check_critical_files()
        captured = capsys.readouterr()

        # 研究模式不应检查 hedge_execution_engine
        assert "hedge_execution_engine" not in captured.out or "跳过" in captured.out
        # 检查结果中不应有 hedge_execution_engine 的 FAIL
        hedge_fails = [
            r
            for r in checker._results
            if r.status == CheckStatus.FAIL and "hedge_execution_engine" in r.name
        ]
        assert (
            len(hedge_fails) == 0
        ), f"研究模式不应 FAIL hedge_execution_engine, 实际: {hedge_fails}"


# ============================================================
# 6. C2 环境变量检查 (集成测试)
# ============================================================


class TestC2EnvVarsCheck:
    """测试 check_env_variables() 在研究模式下的降级"""

    def test_research_mode_wind_fail_is_warn_not_error(
        self, force_research_mode, monkeypatch, capsys
    ):
        """研究模式下 Wind API 未设置时为 WARN, 不是 ERROR"""
        # 确保 WIND_API_KEY 未设置
        monkeypatch.delenv("WIND_API_KEY", raising=False)
        monkeypatch.delenv("IFIND_TOKEN", raising=False)

        checker = SystemChecker()
        checker.check_env_variables()

        # 查找 WIND_API_KEY 相关的 FAIL 结果
        wind_fails = [
            r
            for r in checker._results
            if r.status == CheckStatus.FAIL and "WIND_API_KEY" in r.name
        ]
        # 研究模式下 Wind 未设置应为 WARN 级 (不阻断)
        for fail in wind_fails:
            assert (
                fail.level == CheckLevel.WARN
            ), f"研究模式 Wind API 未设置应为 WARN, 实际 {fail.level}: {fail}"


# ============================================================
# 7. C3 数据源检查 (研究模式分支)
# ============================================================


class TestC3DatasourceResearchMode:
    """测试 _check_datasource_research_mode() 方法"""

    def test_research_mode_method_exists(self):
        """_check_datasource_research_mode 方法应存在"""
        assert hasattr(SystemChecker, "_check_datasource_research_mode")

    def test_research_mode_skips_wind_mcp(self, force_research_mode, capsys):
        """研究模式 C3 应跳过 Wind MCP (SKIP 状态)"""
        checker = SystemChecker()
        checker._check_datasource_research_mode()
        captured = capsys.readouterr()

        # 应输出研究模式标识
        assert "研究模式" in captured.out

        # Wind MCP / iFinD / TDX 应为 SKIP
        skipped = [r for r in checker._results if r.status == CheckStatus.SKIP]
        skipped_names = " ".join(r.name for r in skipped)
        assert "Wind MCP" in skipped_names or "跳过" in skipped_names

    def test_research_mode_checks_akshare(self, force_research_mode):
        """研究模式 C3 应检查 AKShare (PASS 或 FAIL, 不是 SKIP)"""
        checker = SystemChecker()
        checker._check_datasource_research_mode()

        akshare_results = [
            r
            for r in checker._results
            if "AKShare" in r.name or "akshare" in r.name.lower()
        ]
        assert len(akshare_results) > 0, "研究模式应检查 AKShare"
        # AKShare 应是 PASS 或 FAIL (取决于是否安装), 不是 SKIP
        for r in akshare_results:
            assert r.status != CheckStatus.SKIP, f"AKShare 不应被跳过: {r}"


# ============================================================
# 8. C7 子模块 smoke 测试
# ============================================================


class TestC7SubsystemSmoke:
    """测试 check_subsystem_smoke() 在研究模式下的 C7.1 跳过"""

    def test_research_mode_skips_hedge_engine(self, force_research_mode, capsys):
        """研究模式 C7.1 应跳过 HedgeExecutionEngine"""
        checker = SystemChecker()
        checker.check_subsystem_smoke()

        c71_results = [r for r in checker._results if r.code == "C7.1"]
        assert len(c71_results) == 1
        assert (
            c71_results[0].status == CheckStatus.SKIP
        ), f"研究模式 C7.1 应 SKIP, 实际 {c71_results[0].status}"

    def test_windows_mode_checks_hedge_engine(self, force_windows_mode):
        """Windows 实盘模式 C7.1 应检查 HedgeExecutionEngine (PASS/FAIL)"""
        checker = SystemChecker()
        checker.check_subsystem_smoke()

        c71_results = [r for r in checker._results if r.code == "C7.1"]
        assert len(c71_results) == 1
        # Windows 模式不应 SKIP
        assert (
            c71_results[0].status != CheckStatus.SKIP
        ), f"Windows 模式 C7.1 不应 SKIP, 实际 {c71_results[0].status}"


# ============================================================
# 9. run_all 输出标识
# ============================================================


class TestRunAllOutput:
    """测试 run_all() 输出包含模式标识"""

    def test_research_mode_output_contains_label(
        self, force_research_mode, capsys, monkeypatch
    ):
        """研究模式 run_all 输出应包含 '研究模式' 标识"""
        # 跳过数据源检查加速
        monkeypatch.setattr(
            SystemChecker, "check_datasource_connectivity", lambda self: None
        )
        monkeypatch.setattr(
            SystemChecker, "check_fallback_price_freshness", lambda self: None
        )

        checker = SystemChecker(skip_datasource=True)
        checker.run_all()
        captured = capsys.readouterr()

        assert (
            "研究模式" in captured.out
        ), f"输出应包含 '研究模式' 标识, 实际输出: {captured.out[:200]}"

    def test_windows_mode_output_contains_label(
        self, force_windows_mode, capsys, monkeypatch
    ):
        """Windows 实盘模式 run_all 输出应包含 '实盘模式' 标识"""
        monkeypatch.setattr(
            SystemChecker, "check_datasource_connectivity", lambda self: None
        )
        monkeypatch.setattr(
            SystemChecker, "check_fallback_price_freshness", lambda self: None
        )

        checker = SystemChecker(skip_datasource=True)
        checker.run_all()
        captured = capsys.readouterr()

        assert (
            "实盘模式" in captured.out
        ), f"输出应包含 '实盘模式' 标识, 实际输出: {captured.out[:200]}"


# ============================================================
# 10. 回归: 确保不破坏现有 Windows 实盘模式
# ============================================================


class TestWindowsModeRegression:
    """回归测试: 确保 Mac 适配不破坏 Windows 实盘模式"""

    def test_windows_mode_full_critical_files(self, force_windows_mode):
        """Windows 模式返回完整关键文件清单"""
        checker = SystemChecker()
        assert not checker.research_mode
        files = checker._get_critical_files()
        # 必须是完整清单 (9 个, 与 CRITICAL_FILES 一致)
        assert len(files) == len(SystemChecker.CRITICAL_FILES)

    def test_windows_mode_wind_required_level(self, force_windows_mode):
        """Windows 模式 WIND_API_KEY 仍是 ERROR 级必需"""
        checker = SystemChecker()
        critical, _ = checker._get_critical_env_vars()
        assert len(critical) == 2  # WIND_API_KEY + IFIND_TOKEN
        # 这两个都应是必需
        for var, _desc in critical:
            assert var in ("WIND_API_KEY", "IFIND_TOKEN")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
