"""GitHub 热门项目集成 Flag 的 Smoke 测试.

验证目标 (Phase 1 完成标准):
  1. 5 个 flag 全部注册到 feature_flags.yaml
  2. 所有 flag 默认值 = False (满足 ADR-003 "默认不改变现状" 铁律)
  3. 每个 flag 定义包含 required / rollback_seconds / fallback / critical_path 字段
  4. 现有 flag 注册表未被破坏 (回归保护)

参考: .trae/documents/GitHub热门项目集成方案.md §2.5
"""

from __future__ import annotations

import pytest

from utils.infra.feature_flags import (
    FeatureFlags,
    FlagNotFoundError,
    is_enabled,
    list_flags,
)

# ============================================================
# 期望的 5 个 GitHub 集成 Flag
# ============================================================
EXPECTED_FLAGS = [
    "USE_VIBE_BACKTEST_BRIDGE",
    "USE_VIBE_FACTOR_INJECTION",
    "USE_KRONOS_PREDICTOR",
    "USE_LAST30DAYS_SENTIMENT",
    "USE_UNLIMITED_OCR",
]

# 每个 flag 必须包含的字段
REQUIRED_FIELDS = ["default", "description", "requires", "rollback_seconds", "fallback"]


# ============================================================
# Fixture: 每个测试前重置单例, 确保从配置文件干净加载
# ============================================================
@pytest.fixture(autouse=True)
def _reset_flags():
    FeatureFlags.reset_instance()
    yield
    FeatureFlags.reset_instance()


# ============================================================
# 测试 1: 所有 flag 已注册
# ============================================================
def test_all_github_integration_flags_registered() -> None:
    """5 个 flag 必须全部在注册表中 (未注册时 get_flag_def 抛 FlagNotFoundError)."""
    flags = FeatureFlags.get_instance()
    missing = []
    for name in EXPECTED_FLAGS:
        try:
            flags.get_flag_def(name)
        except FlagNotFoundError:
            missing.append(name)
    assert not missing, f"未注册的 flag: {missing}"


# ============================================================
# 测试 2: 默认值铁律 (全部 False)
# ============================================================
def test_all_github_integration_flags_default_false() -> None:
    """所有 5 个 flag 默认必须为 False (ADR-003 铁律)."""
    for name in EXPECTED_FLAGS:
        assert is_enabled(name) is False, f"Flag {name} 默认值不是 False, 违反 ADR-003 铁律"


# ============================================================
# 测试 3: 定义字段完整性
# ============================================================
def test_flag_definitions_have_required_fields() -> None:
    """每个 flag 定义必须包含 required/rollback_seconds/fallback/critical_path 字段."""
    flags = FeatureFlags.get_instance()
    for name in EXPECTED_FLAGS:
        flag_def = flags.get_flag_def(name)
        for field in REQUIRED_FIELDS:
            assert field in flag_def, f"{name} 缺少字段: {field}"
        assert flag_def["critical_path"] is False, (
            f"{name} 必须非关键路径 (critical_path=False), 否则违反 Phase 1 仅配置层变更原则"
        )
        assert isinstance(flag_def["requires"], list), f"{name} requires 必须为列表"
        assert flag_def["rollback_seconds"] >= 0, f"{name} rollback_seconds 必须 >= 0"


# ============================================================
# 测试 4: list_flags 包含新 flag
# ============================================================
def test_list_flags_includes_github_integration() -> None:
    """list_flags() 返回值必须包含 5 个新 flag."""
    all_flags = {f["name"]: f for f in list_flags()}
    for name in EXPECTED_FLAGS:
        assert name in all_flags, f"{name} 未出现在 list_flags() 结果中"
        assert all_flags[name]["current_value"] is False


# ============================================================
# 测试 5: 回归保护 — 现有关键 flag 仍存在
# ============================================================
def test_existing_flags_not_broken() -> None:
    """回归保护: 追加新 flag 不影响现有 flag 注册."""
    existing_critical = [
        "USE_LLM_REPORT_ANALYZER",
        "USE_MULTI_FACTOR_SIGNAL",
        "USE_AB_TESTING_FRAMEWORK",
    ]
    flags = FeatureFlags.get_instance()
    for name in existing_critical:
        try:
            flags.get_flag_def(name)
        except FlagNotFoundError:
            pytest.fail(f"现有 flag {name} 因本次修改丢失, 回归失败")
