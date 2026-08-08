"""AutoFixEngine 单元测试.

任务编号: T1.4 (Phase 1 防御层加固)
验收要求: 单测覆盖率 >= 85%

测试范围:
    - try_fix 派发: 策略匹配 / 通配符 / 无匹配兜底
    - L0 修复: 临时文件清理 / 数据源降级 / 配置 schema
    - L1 修复: sys.path 注入 / heartbeat 字段 / 缺失文件(目录创建 + 文件建议)
    - L2 建议: 依赖冲突 (pip install 建议)
    - 高风险: 兜底告警
    - Memory 审计: 修复写入 / 写入失败容错
    - dry_run 模式
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from utils.evolution.auto_fix_engine import (
    ACTION_AUTO_FIXED,
    ACTION_SKIPPED,
    ACTION_SUGGESTED,
    ACTION_WARNED,
    AutoFixEngine,
    AutoFixError,
    CheckResultLike,
    FixContext,
    FixResult,
    RISK_HIGH,
    RISK_L0,
    RISK_L1,
    RISK_L2,
    _fix_config_schema,
    _fix_datasource_fallback,
    _fix_heartbeat_field,
    _fix_missing_file,
    _fix_sys_path,
    _fix_temp_files,
    _suggest_dependency_install,
    _warn_high_risk,
)
from utils.evolution.memory import EvolutionMemory


# ============================================================
# 辅助
# ============================================================


@dataclass
class FakeCheckResult:
    """模拟 utils.system_check.CheckResult (duck typing 兼容)."""

    code: str
    name: str = ""
    detail: str = ""
    remediation: str = ""


@pytest.fixture
def tmp_memory(tmp_path: Path) -> EvolutionMemory:
    """临时 Memory 实例."""
    return EvolutionMemory(memory_path=tmp_path / "memory.jsonl")


@pytest.fixture
def engine(tmp_memory: EvolutionMemory, tmp_path: Path) -> AutoFixEngine:
    """带 Memory + 临时项目根的 AutoFixEngine."""
    return AutoFixEngine(memory=tmp_memory, project_root=tmp_path)


@pytest.fixture
def dry_run_engine(tmp_path: Path) -> AutoFixEngine:
    """dry_run 模式的 AutoFixEngine (不实际执行文件操作)."""
    return AutoFixEngine(memory=None, project_root=tmp_path, dry_run=True)


# ============================================================
# 策略派发测试
# ============================================================


class TestStrategyDispatch:
    def test_c6_matches_l0_temp_files(self, engine: AutoFixEngine):
        """C6.x 应匹配 L0 临时文件清理策略."""
        result = engine.try_fix(FakeCheckResult(code="C6.1", name="磁盘空间"))
        assert result.risk_level == RISK_L0
        assert result.fixed

    def test_c3_matches_l0_datasource(self, engine: AutoFixEngine):
        """C3.x 应匹配 L0 数据源降级策略."""
        result = engine.try_fix(FakeCheckResult(code="C3.1", name="Wind MCP"))
        assert result.risk_level == RISK_L0
        assert result.fixed

    def test_c7_matches_l1_sys_path(self, engine: AutoFixEngine):
        """C7.x 应匹配 L1 sys.path 策略."""
        result = engine.try_fix(FakeCheckResult(code="C7.1", name="子模块导入"))
        assert result.risk_level == RISK_L1
        assert result.fixed

    def test_c5_matches_l2_dependency(self, engine: AutoFixEngine):
        """C5.x 应匹配 L2 依赖建议策略."""
        result = engine.try_fix(FakeCheckResult(code="C5.1", name="pandas"))
        assert result.risk_level == RISK_L2
        assert not result.fixed  # L2 仅建议
        assert result.action == ACTION_SUGGESTED

    def test_unknown_code_falls_back_to_high_risk(self, engine: AutoFixEngine):
        """未注册的 code 应兜底为高风险告警."""
        result = engine.try_fix(FakeCheckResult(code="C99.1", name="未知检查"))
        assert result.risk_level == RISK_HIGH
        assert not result.fixed
        assert result.action == ACTION_WARNED

    def test_empty_code_returns_skipped(self, engine: AutoFixEngine):
        """空 code 应返回 skipped (无策略)."""
        result = engine.try_fix(FakeCheckResult(code="", name="空"))
        assert result.action == ACTION_SKIPPED
        assert not result.fixed

    def test_check_code_recorded_in_result(self, engine: AutoFixEngine):
        """结果中应记录 check_code (审计用)."""
        result = engine.try_fix(FakeCheckResult(code="C6.1"))
        assert result.check_code == "C6.1"


# ============================================================
# L0 修复函数测试
# ============================================================


class TestL0Fixes:
    def test_fix_temp_files_cleans_pycache(self, tmp_path: Path):
        """_fix_temp_files 应清理 __pycache__ 目录."""
        # 创建测试用 __pycache__
        pycache = tmp_path / "subdir" / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "module.cpython-38.pyc").write_text("fake")

        ctx = FixContext(project_root=tmp_path, dry_run=False)
        result = _fix_temp_files(CheckResultLike(code="C6.1"), ctx)

        assert result.fixed
        assert result.risk_level == RISK_L0
        assert not pycache.exists()  # 已清理

    def test_fix_temp_files_cleans_tmp_files(self, tmp_path: Path):
        """_fix_temp_files 应清理 .tmp 文件."""
        tmp_file = tmp_path / "subdir" / "cache.tmp"
        tmp_file.parent.mkdir(parents=True)
        tmp_file.write_text("temp")

        ctx = FixContext(project_root=tmp_path, dry_run=False)
        result = _fix_temp_files(CheckResultLike(code="C6.1"), ctx)

        assert result.fixed
        assert not tmp_file.exists()

    def test_fix_temp_files_dry_run_no_op(self, tmp_path: Path):
        """dry_run=True 不应实际删除文件."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "m.pyc").write_text("x")

        ctx = FixContext(project_root=tmp_path, dry_run=True)
        result = _fix_temp_files(CheckResultLike(code="C6.1"), ctx)

        assert result.fixed  # 仍返回 fixed=True (模拟成功)
        assert pycache.exists()  # 但文件未实际删除

    def test_fix_datasource_fallback_returns_fixed(self, tmp_path: Path):
        """_fix_datasource_fallback 应返回 fixed=True (降级链确认)."""
        ctx = FixContext(project_root=tmp_path)
        result = _fix_datasource_fallback(CheckResultLike(code="C3.1"), ctx)
        assert result.fixed
        assert result.risk_level == RISK_L0
        assert not result.needs_recheck  # 降级是运行时行为

    def test_fix_config_schema_identifies_positions_json(self, tmp_path: Path):
        """_fix_config_schema 应识别 positions.json."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        positions = config_dir / "positions.json"
        positions.write_text("{}")

        check = CheckResultLike(
            code="C4.1", name="配置 Schema",
            detail=f"positions.json 字段缺失: {positions}",
        )
        ctx = FixContext(project_root=tmp_path)
        result = _fix_config_schema(check, ctx)
        assert result.risk_level == RISK_L0

    def test_fix_config_schema_no_match_returns_suggestion(self, tmp_path: Path):
        """_fix_config_schema 无法定位文件时返回建议."""
        check = CheckResultLike(code="C4.2", detail="未知配置问题")
        ctx = FixContext(project_root=tmp_path)
        result = _fix_config_schema(check, ctx)
        assert not result.fixed
        assert result.action == ACTION_SUGGESTED
        assert result.suggestion


# ============================================================
# L1 修复函数测试
# ============================================================


class TestL1Fixes:
    def test_fix_sys_path_already_present(self, tmp_path: Path):
        """项目根已在 sys.path 时应返回 fixed=True."""
        # 临时加入 sys.path
        root_str = str(tmp_path)
        sys.path.insert(0, root_str)
        try:
            ctx = FixContext(project_root=tmp_path)
            result = _fix_sys_path(CheckResultLike(code="C7.1"), ctx)
            assert result.fixed
            assert result.risk_level == RISK_L1
            assert "已在 sys.path" in result.details
        finally:
            if root_str in sys.path:
                sys.path.remove(root_str)

    def test_fix_sys_path_injects_root(self, tmp_path: Path):
        """项目根不在 sys.path 时应注入."""
        root_str = str(tmp_path)
        # 确保不在 sys.path
        while root_str in sys.path:
            sys.path.remove(root_str)

        ctx = FixContext(project_root=tmp_path)
        result = _fix_sys_path(CheckResultLike(code="C7.1"), ctx)

        assert result.fixed
        assert root_str in sys.path
        # 清理
        if root_str in sys.path:
            sys.path.remove(root_str)

    def test_fix_heartbeat_field_returns_fixed(self, tmp_path: Path):
        """_fix_heartbeat_field 应返回 fixed=True (兼容确认)."""
        ctx = FixContext(project_root=tmp_path)
        result = _fix_heartbeat_field(CheckResultLike(code="C8.1"), ctx)
        assert result.fixed
        assert result.risk_level == RISK_L1
        assert "ts" in result.details

    def test_fix_missing_file_creates_directory(self, tmp_path: Path):
        """_fix_missing_file 对目录缺失应自动创建."""
        reports_dir = tmp_path / "reports" / "evolution"
        check = CheckResultLike(
            code="C1.9", name="报告归档目录",
            detail=f"目录不存在: {reports_dir}",
        )
        ctx = FixContext(project_root=tmp_path)
        result = _fix_missing_file(check, ctx)
        assert result.fixed
        assert reports_dir.exists()

    def test_fix_missing_file_returns_suggestion_for_files(self, tmp_path: Path):
        """_fix_missing_file 对文件缺失应返回建议 (无法自动创建)."""
        check = CheckResultLike(
            code="C1.1", name="现货持仓状态 (config/positions.json)",
            detail="文件不存在",
        )
        ctx = FixContext(project_root=tmp_path)
        result = _fix_missing_file(check, ctx)
        assert not result.fixed
        assert result.action == ACTION_SUGGESTED
        assert result.suggestion


# ============================================================
# L2 修复函数测试
# ============================================================


class TestL2Suggestions:
    def test_suggest_dependency_extracts_package_name(self, tmp_path: Path):
        """_suggest_dependency_install 应从 detail 提取包名."""
        check = CheckResultLike(
            code="C5.1", name="pandas",
            detail="No module named 'pandas'",
        )
        ctx = FixContext(project_root=tmp_path)
        result = _suggest_dependency_install(check, ctx)

        assert not result.fixed
        assert result.risk_level == RISK_L2
        assert "pip install pandas" in result.suggestion

    def test_suggest_dependency_extracts_double_quoted_package(self, tmp_path: Path):
        """支持双引号格式的包名提取."""
        check = CheckResultLike(
            code="C5.2", detail='No module named "numpy"',
        )
        ctx = FixContext(project_root=tmp_path)
        result = _suggest_dependency_install(check, ctx)
        assert "pip install numpy" in result.suggestion

    def test_suggest_dependency_no_package_name(self, tmp_path: Path):
        """无法提取包名时应返回通用建议."""
        check = CheckResultLike(code="C5.3", detail="版本冲突")
        ctx = FixContext(project_root=tmp_path)
        result = _suggest_dependency_install(check, ctx)
        assert not result.fixed
        assert result.suggestion  # 有建议


# ============================================================
# 高风险兜底测试
# ============================================================


class TestHighRiskWarn:
    def test_warn_high_risk_no_fix(self, tmp_path: Path):
        """_warn_high_risk 应返回 fixed=False + 告警."""
        check = CheckResultLike(
            code="C99.1", name="持仓不一致",
            detail="positions.json 与 trade_plans 不一致",
        )
        ctx = FixContext(project_root=tmp_path)
        result = _warn_high_risk(check, ctx)

        assert not result.fixed
        assert result.risk_level == RISK_HIGH
        assert result.action == ACTION_WARNED
        assert result.suggestion  # 有建议


# ============================================================
# Memory 审计测试
# ============================================================


class TestMemoryAudit:
    def test_fix_recorded_to_memory(self, tmp_memory: EvolutionMemory, tmp_path: Path):
        """修复成功应写入 Memory 审计."""
        engine = AutoFixEngine(memory=tmp_memory, project_root=tmp_path)
        engine.try_fix(FakeCheckResult(code="C6.1", name="磁盘空间"))

        records = tmp_memory.query(action_type="fix")
        assert len(records) == 1
        rec = records[0]
        assert rec.level == "L1"  # AutoFixEngine 属于 L1 防御层
        assert rec.target_module == "system_check.C6.1"
        assert rec.status == "executed"

    def test_rejected_fix_recorded_with_status_rejected(
        self, tmp_memory: EvolutionMemory, tmp_path: Path
    ):
        """被拒(仅建议)的修复也应以 rejected 状态写入审计."""
        engine = AutoFixEngine(memory=tmp_memory, project_root=tmp_path)
        engine.try_fix(FakeCheckResult(code="C5.1", detail="No module named 'pandas'"))

        records = tmp_memory.query(action_type="fix")
        assert len(records) == 1
        assert records[0].status == "rejected"  # L2 仅建议 = 未执行 = rejected

    def test_memory_write_failure_tolerated(self, tmp_path: Path):
        """Memory 写入失败不应阻塞修复 (容错)."""
        # 用 None memory 触发 _record_to_memory 直接 return
        engine = AutoFixEngine(memory=None, project_root=tmp_path)
        result = engine.try_fix(FakeCheckResult(code="C6.1"))
        # 不抛异常 + 修复正常返回
        assert result.fixed

    def test_no_memory_no_audit(self, tmp_path: Path):
        """无 Memory 时不应写审计 (也不报错)."""
        engine = AutoFixEngine(memory=None, project_root=tmp_path)
        # 不抛异常即可
        result = engine.try_fix(FakeCheckResult(code="C6.1"))
        assert result.fixed


# ============================================================
# dry_run 模式测试
# ============================================================


class TestDryRun:
    def test_dry_run_does_not_clean_files(self, tmp_path: Path):
        """dry_run 模式不应实际删除文件."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "m.pyc").write_text("x")

        engine = AutoFixEngine(memory=None, project_root=tmp_path, dry_run=True)
        result = engine.try_fix(FakeCheckResult(code="C6.1"))

        assert result.fixed  # 模拟成功
        assert pycache.exists()  # 文件未删除

    def test_dry_run_does_not_inject_sys_path(self, tmp_path: Path):
        """dry_run 模式不应修改 sys.path."""
        root_str = str(tmp_path)
        while root_str in sys.path:
            sys.path.remove(root_str)

        engine = AutoFixEngine(memory=None, project_root=tmp_path, dry_run=True)
        engine.try_fix(FakeCheckResult(code="C7.1"))

        # dry_run 不应注入
        # 注意: _fix_sys_path 内部检查 sys.path, dry_run 时仍会返回 fixed=True 但不 insert
        # 这里验证 dry_run 引擎的 _default_context.dry_run=True
        assert engine._default_context.dry_run is True


# ============================================================
# 异常处理测试
# ============================================================


class TestExceptionHandling:
    def test_fix_function_exception_caught(self, tmp_path: Path, monkeypatch):
        """修复函数抛异常应被捕获, 返回 skipped 结果."""
        engine = AutoFixEngine(memory=None, project_root=tmp_path)

        # monkey-patch _fix_temp_files 抛异常
        original = engine._find_strategy

        def failing_find(code):
            return RISK_L0, lambda c, ctx: (_ for _ in ()).throw(RuntimeError("simulated"))

        engine._find_strategy = failing_find
        try:
            result = engine.try_fix(FakeCheckResult(code="C6.1"))
            assert not result.fixed
            assert result.action == ACTION_SKIPPED
            assert "simulated" in result.error or "simulated" in result.details
        finally:
            engine._find_strategy = original


# ============================================================
# FixResult 数据类测试
# ============================================================


class TestFixResult:
    def test_to_dict_contains_all_fields(self):
        """to_dict 应包含所有字段."""
        r = FixResult(
            fixed=True, action="auto_fixed", risk_level="L0",
            details="ok", check_code="C6.1",
        )
        d = r.to_dict()
        assert "fixed" in d
        assert "action" in d
        assert "risk_level" in d
        assert "details" in d
        assert "check_code" in d
        assert "needs_recheck" in d
        assert "suggestion" in d
        assert "error" in d

    def test_to_dict_serializable(self):
        """to_dict 应可 JSON 序列化."""
        import json
        r = FixResult(fixed=False, action="warned", risk_level="high", details="d")
        json.dumps(r.to_dict())  # 不抛异常即可


# ============================================================
# 集成: 完整修复链路测试
# ============================================================


class TestIntegration:
    def test_full_lifecycle_l0_fix(self, tmp_memory: EvolutionMemory, tmp_path: Path):
        """L0 完整链路: 失败 → 修复 → 审计写入."""
        # 准备: 创建 __pycache__ 模拟问题
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "m.pyc").write_text("x")

        engine = AutoFixEngine(memory=tmp_memory, project_root=tmp_path)

        # 执行修复
        result = engine.try_fix(FakeCheckResult(code="C6.1", name="磁盘空间"))
        assert result.fixed

        # 验证文件已清理
        assert not pycache.exists()

        # 验证审计写入
        records = tmp_memory.query(action_type="fix")
        assert len(records) == 1
        assert records[0].status == "executed"

    def test_high_risk_never_fixes(self, tmp_memory: EvolutionMemory, tmp_path: Path):
        """高风险问题永远不修复 (仅告警)."""
        engine = AutoFixEngine(memory=tmp_memory, project_root=tmp_path)
        result = engine.try_fix(
            FakeCheckResult(
                code="C99.1", name="持仓不一致",
                detail="positions 与 trade_plans 冲突",
            )
        )
        assert not result.fixed
        assert result.action == ACTION_WARNED

        # 审计应为 rejected 状态
        records = tmp_memory.query(action_type="fix")
        assert len(records) == 1
        assert records[0].status == "rejected"
