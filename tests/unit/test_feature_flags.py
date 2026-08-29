"""Feature Flag 框架单元测试.

任务: T1.3
验收标准:
    1. is_enabled("USE_INTEGRATED_BOOTSTRAP") 返回 False (默认值)
    2. enable(name, signer, co_signer) 写入审计日志
    3. disable(name, signer) 单签即可
    4. 配置走 ConfigManager 4 级优先级 (HC-5)
    5. 单测覆盖率 >= 90%
    6. mypy strict 通过
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.infra.feature_flags import (
    FeatureFlags,
    FlagNotFoundError,
    FlagPermissionError,
    audit_trail,
    disable,
    enable,
    is_enabled,
    list_flags,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def temp_override_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时覆盖目录 (每个测试独立)."""
    override_dir = tmp_path / "flag_overrides"
    override_dir.mkdir()
    monkeypatch.setenv("QUANT_FLAG_OVERRIDE_DIR", str(override_dir))

    # 同时重定向审计日志目录
    audit_dir = tmp_path / "flag_audit"
    audit_dir.mkdir()

    # 重置单例, 让下一个 get_instance 重新加载
    FeatureFlags.reset_instance()

    yield override_dir

    FeatureFlags.reset_instance()


@pytest.fixture
def flags_instance(temp_override_dir: Path) -> FeatureFlags:
    """获取干净的 FeatureFlags 单例."""
    instance = FeatureFlags.get_instance()
    # 重定向审计目录到临时目录
    instance._audit_log_dir = temp_override_dir.parent / "flag_audit"
    instance._audit_log_dir.mkdir(exist_ok=True)
    return instance


# ============================================================
# 测试 1: 默认值 (铁律 ADR-003)
# ============================================================
class TestFlagDefaults:
    """所有 flag 默认值必须 = False (不改变现状)."""

    def test_known_flag_returns_false_by_default(
        self, flags_instance: FeatureFlags
    ) -> None:
        """已注册 flag 默认 False."""
        assert flags_instance.is_enabled("USE_INTEGRATED_BOOTSTRAP") is False
        assert flags_instance.is_enabled("USE_LLM_REPORT_ANALYZER") is False
        assert flags_instance.is_enabled("USE_RISK_BUS_EVENT_DRIVEN") is False

    def test_unknown_flag_returns_false(self, flags_instance: FeatureFlags) -> None:
        """未注册 flag 返回 False (不抛异常)."""
        assert flags_instance.is_enabled("NON_EXISTENT_FLAG_XYZ") is False

    def test_all_registered_flags_default_false(
        self, flags_instance: FeatureFlags
    ) -> None:
        """遍历所有 flag, 确认默认值都是 False (ADR-003 铁律).

        例外: USE_VOL_REGIME_WEIGHTER (2026-08-05 双签授权 Phase 0 实战监控, 观察期只读模式).
        """
        _AUTHORIZED_TRUE_DEFAULTS = {"USE_VOL_REGIME_WEIGHTER"}
        for flag in flags_instance.list_flags():
            if flag["name"] in _AUTHORIZED_TRUE_DEFAULTS:
                continue
            assert flag["default"] is False, (
                f"Flag {flag['name']} default={flag['default']} 违反 ADR-003 "
                f"(必须等于当前生产行为, 即 False)"
            )


# ============================================================
# 测试 2: 启用 (双签)
# ============================================================
class TestEnableFlag:
    """enable() 必须双签."""

    def test_enable_with_dual_signature(self, flags_instance: FeatureFlags) -> None:
        """双签启用成功."""
        flags_instance.enable(
            "USE_INTEGRATED_BOOTSTRAP",
            signer="alice",
            co_signer="bob_risk_officer",
            reason="Phase 1 T1.5 完成, 启用 bootstrap",
        )
        assert flags_instance.is_enabled("USE_INTEGRATED_BOOTSTRAP") is True

    def test_enable_without_co_signer_raises(
        self, flags_instance: FeatureFlags
    ) -> None:
        """缺少 co_signer 抛 FlagPermissionError."""
        with pytest.raises(FlagPermissionError):
            flags_instance.enable(
                "USE_INTEGRATED_BOOTSTRAP",
                signer="alice",
                co_signer="",
                reason="尝试缺少双签",
            )

    def test_enable_same_signer_raises(self, flags_instance: FeatureFlags) -> None:
        """signer 和 co_signer 相同抛异常."""
        with pytest.raises(FlagPermissionError):
            flags_instance.enable(
                "USE_INTEGRATED_BOOTSTRAP",
                signer="alice",
                co_signer="alice",
                reason="自己签自己",
            )

    def test_enable_unknown_flag_raises(self, flags_instance: FeatureFlags) -> None:
        """启用未注册 flag 抛 FlagNotFoundError."""
        with pytest.raises(FlagNotFoundError):
            flags_instance.enable(
                "NON_EXISTENT_FLAG",
                signer="alice",
                co_signer="bob",
            )


# ============================================================
# 测试 3: 禁用 (单签)
# ============================================================
class TestDisableFlag:
    """disable() 单签即可."""

    def test_disable_with_single_signature(self, flags_instance: FeatureFlags) -> None:
        """单签禁用成功."""
        # 先启用
        flags_instance.enable(
            "USE_LLM_REPORT_ANALYZER", signer="alice", co_signer="bob"
        )
        assert flags_instance.is_enabled("USE_LLM_REPORT_ANALYZER") is True

        # 单签禁用
        flags_instance.disable(
            "USE_LLM_REPORT_ANALYZER",
            signer="carol_risk",
            reason="发现 LLM 调用延迟超标, 紧急关闭",
        )
        assert flags_instance.is_enabled("USE_LLM_REPORT_ANALYZER") is False

    def test_disable_without_signer_raises(self, flags_instance: FeatureFlags) -> None:
        """缺少 signer 抛异常."""
        with pytest.raises(FlagPermissionError):
            flags_instance.disable("USE_LLM_REPORT_ANALYZER", signer="")


# ============================================================
# 测试 4: 审计日志
# ============================================================
class TestAuditTrail:
    """所有变更必须写入审计日志."""

    def test_enable_writes_audit_log(self, flags_instance: FeatureFlags) -> None:
        """enable 写入 JSONL 审计日志."""
        flags_instance.enable(
            "USE_INTEGRATED_BOOTSTRAP",
            signer="alice",
            co_signer="bob",
            reason="测试审计",
        )

        trail = flags_instance.audit_trail("USE_INTEGRATED_BOOTSTRAP")
        assert len(trail) >= 1
        record = trail[-1]
        assert record["flag_name"] == "USE_INTEGRATED_BOOTSTRAP"
        assert record["enabled"] is True
        assert record["signer"] == "alice"
        assert record["co_signer"] == "bob"
        assert record["reason"] == "测试审计"
        assert record["action"] == "enable"
        assert "timestamp" in record

    def test_disable_writes_audit_log(self, flags_instance: FeatureFlags) -> None:
        """disable 写入 JSONL 审计日志."""
        flags_instance.enable(
            "USE_LLM_REPORT_ANALYZER", signer="alice", co_signer="bob"
        )
        flags_instance.disable(
            "USE_LLM_REPORT_ANALYZER", signer="carol", reason="紧急关闭"
        )

        trail = flags_instance.audit_trail("USE_LLM_REPORT_ANALYZER")
        assert len(trail) >= 2
        # 最后一条是 disable
        last = trail[-1]
        assert last["action"] == "disable"
        assert last["enabled"] is False
        assert last["signer"] == "carol"

    def test_audit_trail_empty_for_unknown_flag(
        self, flags_instance: FeatureFlags
    ) -> None:
        """未操作的 flag 审计轨迹为空."""
        trail = flags_instance.audit_trail("NEVER_TOUCHED_FLAG")
        assert trail == []


# ============================================================
# 测试 5: 模块级快捷函数
# ============================================================
class TestModuleLevelAPI:
    """模块级快捷函数与单例方法等价."""

    def test_is_enabled_shortcut(self, flags_instance: FeatureFlags) -> None:
        assert is_enabled("USE_INTEGRATED_BOOTSTRAP") is False

    def test_enable_disable_shortcuts(self, flags_instance: FeatureFlags) -> None:
        enable("USE_INTEGRATED_BOOTSTRAP", signer="alice", co_signer="bob")
        assert is_enabled("USE_INTEGRATED_BOOTSTRAP") is True

        disable("USE_INTEGRATED_BOOTSTRAP", signer="carol")
        assert is_enabled("USE_INTEGRATED_BOOTSTRAP") is False

    def test_list_flags_shortcut(self, flags_instance: FeatureFlags) -> None:
        flags = list_flags()
        assert len(flags) > 0
        names = [f["name"] for f in flags]
        assert "USE_INTEGRATED_BOOTSTRAP" in names

    def test_audit_trail_shortcut(self, flags_instance: FeatureFlags) -> None:
        enable("USE_INTEGRATED_BOOTSTRAP", signer="alice", co_signer="bob")
        trail = audit_trail("USE_INTEGRATED_BOOTSTRAP")
        assert len(trail) >= 1


# ============================================================
# 测试 6: 持久化与重载
# ============================================================
class TestPersistenceAndReload:
    """覆盖文件持久化 + 热加载."""

    def test_override_persists_across_instances(
        self, flags_instance: FeatureFlags
    ) -> None:
        """覆盖文件在单例重置后仍生效."""
        flags_instance.enable(
            "USE_INTEGRATED_BOOTSTRAP", signer="alice", co_signer="bob"
        )
        assert flags_instance.is_enabled("USE_INTEGRATED_BOOTSTRAP") is True

        # 重置单例, 重新加载
        FeatureFlags.reset_instance()
        new_instance = FeatureFlags.get_instance()
        # 覆盖文件仍在, flag 仍为 True
        assert new_instance.is_enabled("USE_INTEGRATED_BOOTSTRAP") is True

    def test_reload_picks_up_new_override(
        self, flags_instance: FeatureFlags, temp_override_dir: Path
    ) -> None:
        """reload() 检测新覆盖文件."""
        assert flags_instance.is_enabled("USE_DAILY_WORKFLOW_V2") is False

        # 手动写覆盖文件
        override_file = temp_override_dir / "USE_DAILY_WORKFLOW_V2.json"
        override_file.write_text(
            json.dumps(
                {
                    "flag_name": "USE_DAILY_WORKFLOW_V2",
                    "enabled": True,
                    "signer": "external",
                    "co_signer": "risk_officer",
                    "reason": "外部覆盖",
                    "action": "enable",
                    "timestamp": "2026-07-26T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )

        flags_instance.reload()
        assert flags_instance.is_enabled("USE_DAILY_WORKFLOW_V2") is True


# ============================================================
# 测试 7: flag 定义元数据
# ============================================================
class TestFlagMetadata:
    """flag 定义元数据可查询."""

    def test_get_flag_def(self, flags_instance: FeatureFlags) -> None:
        flag_def = flags_instance.get_flag_def("USE_RISK_BUS_EVENT_DRIVEN")
        assert flag_def["default"] is False
        assert flag_def["critical_path"] is True
        assert flag_def["rollback_seconds"] == 0
        assert "Shadow 14天" in flag_def["requires"]

    def test_get_flag_def_unknown_raises(self, flags_instance: FeatureFlags) -> None:
        with pytest.raises(FlagNotFoundError):
            flags_instance.get_flag_def("NON_EXISTENT")

    def test_list_flags_includes_current_value(
        self, flags_instance: FeatureFlags
    ) -> None:
        _AUTHORIZED_TRUE_DEFAULTS = {"USE_VOL_REGIME_WEIGHTER"}
        flags = flags_instance.list_flags()
        for flag in flags:
            assert "current_value" in flag
            assert "overridden" in flag
            if flag["name"] in _AUTHORIZED_TRUE_DEFAULTS:
                continue
            assert flag["current_value"] is False  # 默认值


# ============================================================
# 测试 8: 关键路径 flag 验证
# ============================================================
class TestCriticalPathFlags:
    """关键路径 flag (执行层/风控层) 验证."""

    def test_execution_router_flag_is_critical(
        self, flags_instance: FeatureFlags
    ) -> None:
        """USE_AUTOMATED_EXECUTION_ROUTER 必须是 critical_path + rollback=0."""
        flag_def = flags_instance.get_flag_def("USE_AUTOMATED_EXECUTION_ROUTER")
        assert flag_def["critical_path"] is True
        assert flag_def["rollback_seconds"] == 0

    def test_risk_bus_flag_is_critical(self, flags_instance: FeatureFlags) -> None:
        """USE_RISK_BUS_EVENT_DRIVEN 必须是 critical_path + rollback=0."""
        flag_def = flags_instance.get_flag_def("USE_RISK_BUS_EVENT_DRIVEN")
        assert flag_def["critical_path"] is True
        assert flag_def["rollback_seconds"] == 0
