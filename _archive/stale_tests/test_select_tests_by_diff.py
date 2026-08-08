"""GAP-5 智能测试选择 — 单元测试.

覆盖:
    - AST import 提取 (extract_imports)
    - 文件路径→模块名转换 (filepath_to_module)
    - 变更分类 (classify_changes: 源码/测试/基础设施)
    - 反向依赖索引 (build_reverse_dependency_index)
    - 受影响测试选择 (select_affected_tests)
    - 端到端选择逻辑 (select_tests)
    - 数据类不可变性 (TestSelectionResult)
    - CLI 入口 (main)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 将 scripts/ 加入 sys.path, 直接 import (避免 importlib 动态加载导致
# Python 3.8 dataclass 无法解析 Tuple[str, ...] 字符串注解的 __module__ 问题)
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
import _select_tests_by_diff as _select  # noqa: E402


# ============================================================
# 1. AST import 提取
# ============================================================
class TestExtractImports:
    """验证 AST import 语句提取."""

    def test_simple_import(self, tmp_path):
        """import foo.bar → 提取 foo.bar."""
        f = tmp_path / "test_x.py"
        f.write_text("import foo.bar\n", encoding="utf-8")
        imports = _select.extract_imports(f)
        assert "foo.bar" in imports

    def test_import_with_alias(self, tmp_path):
        """import foo.bar as fb → 提取 foo.bar (不含 alias)."""
        f = tmp_path / "test_x.py"
        f.write_text("import foo.bar as fb\n", encoding="utf-8")
        imports = _select.extract_imports(f)
        assert "foo.bar" in imports

    def test_from_import(self, tmp_path):
        """from foo.bar import baz → 提取 foo.bar."""
        f = tmp_path / "test_x.py"
        f.write_text("from foo.bar import baz\n", encoding="utf-8")
        imports = _select.extract_imports(f)
        assert "foo.bar" in imports

    def test_multiple_imports(self, tmp_path):
        """多个 import 语句全部提取."""
        f = tmp_path / "test_x.py"
        f.write_text(
            "import os\nimport sys\nfrom pathlib import Path\nimport pandas as pd\n",
            encoding="utf-8",
        )
        imports = _select.extract_imports(f)
        assert "os" in imports
        assert "sys" in imports
        assert "pathlib" in imports
        assert "pandas" in imports

    def test_relative_import_skipped(self, tmp_path):
        """相对导入 (from . import x) 被跳过."""
        f = tmp_path / "test_x.py"
        f.write_text("from . import sibling\nfrom .sibling import func\n", encoding="utf-8")
        imports = _select.extract_imports(f)
        # 相对导入不出现在结果中
        assert len(imports) == 0

    def test_syntax_error_returns_empty(self, tmp_path):
        """语法错误文件返回空集合 (不抛异常)."""
        f = tmp_path / "test_x.py"
        f.write_text("def broken(:\n", encoding="utf-8")
        imports = _select.extract_imports(f)
        assert imports == set()

    def test_empty_file(self, tmp_path):
        """空文件返回空集合."""
        f = tmp_path / "test_x.py"
        f.write_text("", encoding="utf-8")
        assert _select.extract_imports(f) == set()

    def test_nested_import_in_function(self, tmp_path):
        """函数内的 import 也能被提取 (ast.walk 遍历)."""
        f = tmp_path / "test_x.py"
        f.write_text(
            "def test_foo():\n    import inner_module\n    pass\n",
            encoding="utf-8",
        )
        imports = _select.extract_imports(f)
        assert "inner_module" in imports


# ============================================================
# 2. 文件路径→模块名变体转换
# ============================================================
class TestFilepathToModule:
    """验证文件路径到模块名变体的转换 (多根 sys.path 配置)."""

    def test_utils_alpha_module(self):
        """utils/alpha/drift_monitor.py → 含完整路径和去前缀变体."""
        result = _select.filepath_to_module_variants("utils/alpha/drift_monitor.py")
        assert "utils.alpha.drift_monitor" in result
        assert "alpha.drift_monitor" in result

    def test_v83_src_module(self):
        """v8.3_institutional/src/foo.py → 含 src.foo 和 foo 变体."""
        result = _select.filepath_to_module_variants("v8.3_institutional/src/foo.py")
        assert "foo" in result
        assert "src.foo" in result

    def test_v83_root_module(self):
        """v8.3_institutional/daily_workflow.py → 含 daily_workflow 变体."""
        result = _select.filepath_to_module_variants("v8.3_institutional/daily_workflow.py")
        assert "daily_workflow" in result

    def test_research_module(self):
        """research/lgbm_reproducibility.py → 含 lgbm_reproducibility 变体."""
        result = _select.filepath_to_module_variants("research/lgbm_reproducibility.py")
        assert "lgbm_reproducibility" in result

    def test_nested_module(self):
        """深层嵌套模块正确转换."""
        result = _select.filepath_to_module_variants("utils/alpha/sub/deep.py")
        assert "utils.alpha.sub.deep" in result
        assert "alpha.sub.deep" in result

    def test_backslash_path(self):
        """Windows 反斜杠路径也能处理."""
        result = _select.filepath_to_module_variants("utils\\alpha\\drift_monitor.py")
        assert any("drift_monitor" in v for v in result)

    def test_returns_set(self):
        """返回值是 set."""
        result = _select.filepath_to_module_variants("utils/alpha/drift_monitor.py")
        assert isinstance(result, set)
        assert len(result) >= 1


# ============================================================
# 3. 变更文件分类
# ============================================================
class TestClassifyChanges:
    """验证变更文件分类 (源码/测试/基础设施)."""

    def test_source_change_classified(self):
        """源码文件变更归类为 source."""
        source, test, infra = _select.classify_changes(["utils/alpha/drift_monitor.py"])
        assert len(source) == 1
        assert len(test) == 0
        assert len(infra) == 0

    def test_test_change_classified(self):
        """测试文件变更归类为 test."""
        source, test, infra = _select.classify_changes(["tests/unit/test_foo.py"])
        assert len(source) == 0
        assert len(test) == 1
        assert len(infra) == 0

    def test_infra_file_triggers_infra(self):
        """conftest.py 变更归类为 infra."""
        source, test, infra = _select.classify_changes(["tests/conftest.py"])
        assert len(source) == 0
        assert len(test) == 0
        assert len(infra) == 1

    def test_pytest_ini_triggers_infra(self):
        """pytest.ini 变更归类为 infra."""
        _, _, infra = _select.classify_changes(["pytest.ini"])
        assert len(infra) == 1

    def test_ci_workflow_triggers_infra(self):
        """.github/workflows/ci.yml 变更归类为 infra."""
        _, _, infra = _select.classify_changes([".github/workflows/ci.yml"])
        assert len(infra) == 1

    def test_init_py_triggers_infra(self):
        """__init__.py 变更归类为 infra (保守策略)."""
        _, _, infra = _select.classify_changes(["utils/alpha/__init__.py"])
        assert len(infra) == 1

    def test_mixed_changes(self):
        """混合变更正确分类."""
        files = [
            "utils/alpha/drift_monitor.py",        # source
            "tests/unit/test_drift_monitor.py",    # test
            "pytest.ini",                           # infra
            "README.md",                            # 无分类 (非 .py, 非测试, 非源码根)
        ]
        source, test, infra = _select.classify_changes(files)
        assert len(source) == 1
        assert len(test) == 1
        assert len(infra) == 1

    def test_markdown_not_classified(self):
        """README.md 变更不归入任何类别."""
        source, test, infra = _select.classify_changes(["README.md"])
        assert len(source) == 0
        assert len(test) == 0
        assert len(infra) == 0

    def test_backslash_path_classified(self):
        """Windows 反斜杠路径也能正确分类."""
        source, _, _ = _select.classify_changes(["utils\\alpha\\drift_monitor.py"])
        assert len(source) == 1


# ============================================================
# 4. 反向依赖索引
# ============================================================
class TestReverseDependencyIndex:
    """验证反向依赖索引构建."""

    def test_index_built_successfully(self):
        """索引能成功构建 (非空)."""
        index = _select.build_reverse_dependency_index()
        # 至少能索引到一些模块
        assert isinstance(index, dict)
        assert len(index) > 0

    def test_index_contains_utils_modules(self):
        """索引包含 utils 下的模块 (被测试 import)."""
        index = _select.build_reverse_dependency_index()
        # test_drift_monitor.py 应该 import 了 utils.alpha.drift_monitor
        # 所以索引里应该有 alpha.drift_monitor 或 utils.alpha.drift_monitor
        has_alpha = any("alpha" in k for k in index.keys())
        assert has_alpha, "索引应包含 alpha 相关模块"

    def test_index_values_are_paths(self):
        """索引值是 Path 列表."""
        index = _select.build_reverse_dependency_index()
        for _key, paths in index.items():
            assert isinstance(paths, list)
            for p in paths:
                assert isinstance(p, Path)


# ============================================================
# 5. 受影响测试选择
# ============================================================
class TestSelectAffectedTests:
    """验证受影响测试选择逻辑."""

    def test_source_change_selects_dependent_test(self):
        """源码变更选中依赖它的测试."""
        # 构造 mock 反向索引: drift_monitor → test_drift_monitor.py
        test_file = _PROJECT_ROOT / "tests" / "unit" / "test_drift_monitor_sim_mode.py"
        if not test_file.exists():
            pytest.skip("test_drift_monitor_sim_mode.py 不存在")
        reverse_index = {
            "alpha.drift_monitor": [test_file],
            "utils.alpha.drift_monitor": [test_file],
        }
        affected = _select.select_affected_tests(
            source_changes=["utils/alpha/drift_monitor.py"],
            test_changes=[],
            reverse_index=reverse_index,
        )
        assert test_file in affected

    def test_test_change_selects_itself(self):
        """测试文件变更选中自身."""
        test_path = "tests/unit/test_foo.py"
        abs_path = _PROJECT_ROOT / test_path
        # 创建临时文件
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("# temp test\n", encoding="utf-8")
        try:
            affected = _select.select_affected_tests(
                source_changes=[],
                test_changes=[test_path],
                reverse_index={},
            )
            assert abs_path in affected
        finally:
            abs_path.unlink(missing_ok=True)

    def test_no_changes_no_selection(self):
        """无源码无测试变更 → 空集合."""
        affected = _select.select_affected_tests(
            source_changes=[],
            test_changes=[],
            reverse_index={"foo": []},
        )
        assert len(affected) == 0

    def test_prefix_match_selects_broader_tests(self):
        """前缀匹配: 变更 utils/alpha/foo.py 命中 import utils.alpha 的测试."""
        test_file = _PROJECT_ROOT / "tests" / "unit" / "test_bar.py"
        reverse_index = {
            "utils.alpha": [test_file],  # 测试 import 了 utils.alpha
        }
        affected = _select.select_affected_tests(
            source_changes=["utils/alpha/foo.py"],
            test_changes=[],
            reverse_index=reverse_index,
        )
        # filepath_to_module_variants 返回 {utils.alpha.foo, alpha.foo, foo}
        # 前缀匹配 utils.alpha 命中 reverse_index["utils.alpha"]
        assert test_file in affected


# ============================================================
# 6. 端到端选择逻辑 (select_tests)
# ============================================================
class TestSelectTests:
    """验证端到端测试选择逻辑."""

    def test_no_changes_returns_full_suite(self):
        """无变更 → 全量 (fallback)."""
        with mock.patch.object(_select, "get_changed_files", return_value=[]):
            result = _select.select_tests()
        assert result.is_full_suite is True
        assert "no_changes" in result.reason

    def test_infra_change_returns_full_suite(self):
        """基础设施变更 → 全量."""
        with mock.patch.object(
            _select, "get_changed_files", return_value=["pytest.ini"]
        ):
            result = _select.select_tests()
        assert result.is_full_suite is True
        assert "infra_changed" in result.reason

    def test_no_test_relevant_changes_returns_empty(self):
        """非测试相关变更 (如 README.md) → 空选择."""
        with mock.patch.object(
            _select, "get_changed_files", return_value=["README.md", "docs/guide.md"]
        ):
            result = _select.select_tests()
        assert result.is_full_suite is False
        assert len(result.selected_tests) == 0
        assert "no_test_relevant" in result.reason

    def test_source_change_with_no_affected_test_falls_back_full(self):
        """源码变更但找不到受影响测试 → 全量 (保守)."""
        # mock 空反向索引, 确保 filepath_to_module_variants 的所有变体都不命中
        with mock.patch.object(
            _select, "get_changed_files", return_value=["utils/nonexistent_module.py"]
        ), mock.patch.object(
            _select, "build_reverse_dependency_index", return_value={}
        ):
            result = _select.select_tests()
        assert result.is_full_suite is True
        assert "fallback_full" in result.reason

    def test_result_has_scan_duration(self):
        """结果包含扫描耗时."""
        with mock.patch.object(_select, "get_changed_files", return_value=[]):
            result = _select.select_tests()
        assert result.scan_duration_ms > 0


# ============================================================
# 7. 数据类不可变性
# ============================================================
class TestTestSelectionResult:
    """验证 TestSelectionResult 数据类 (frozen=True)."""

    def test_default_values(self):
        """默认值正确."""
        result = _select.TestSelectionResult()
        assert result.is_full_suite is False
        assert result.selected_tests == ()
        assert result.reason == ""
        assert result.changed_files_count == 0

    def test_immutable(self):
        """frozen=True, 不可修改."""
        result = _select.TestSelectionResult(is_full_suite=True)
        with pytest.raises((AttributeError, TypeError)):
            result.is_full_suite = False  # type: ignore[assignment]
    def test_to_dict_serializable(self):
        """to_dict 输出可 JSON 序列化."""
        result = _select.TestSelectionResult(
            selected_tests=("tests/unit/test_foo.py",),
            is_full_suite=False,
            reason="test",
            changed_files_count=1,
        )
        d = result.to_dict()
        # 应能 JSON 序列化 (CI 消费)
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["is_full_suite"] is False
        assert parsed["selected_tests"] == ["tests/unit/test_foo.py"]
        assert parsed["changed_files_count"] == 1

    def test_is_full_suite_excludes_selected_tests(self):
        """全量模式时 selected_tests 应为空."""
        result = _select.TestSelectionResult(is_full_suite=True)
        assert result.selected_tests == ()


# ============================================================
# 8. CLI 入口
# ============================================================
class TestCliMain:
    """验证 CLI main 入口."""

    def test_main_no_changes_returns_zero(self, capsys):
        """无变更时 main 返回 0."""
        with mock.patch.object(_select, "get_changed_files", return_value=[]):
            old_argv = sys.argv
            sys.argv = ["_select_tests_by_diff.py"]
            try:
                rc = _select.main()
            finally:
                sys.argv = old_argv
        assert rc == 0

    def test_main_json_output(self, capsys):
        """--json 输出合法 JSON."""
        with mock.patch.object(_select, "get_changed_files", return_value=[]):
            old_argv = sys.argv
            sys.argv = ["_select_tests_by_diff.py", "--json"]
            try:
                rc = _select.main()
            finally:
                sys.argv = old_argv
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["is_full_suite"] is True
        assert rc == 0

    def test_main_quiet_output_all(self, capsys):
        """--quiet 全量时输出 'ALL'."""
        with mock.patch.object(_select, "get_changed_files", return_value=[]):
            old_argv = sys.argv
            sys.argv = ["_select_tests_by_diff.py", "--quiet"]
            try:
                rc = _select.main()
            finally:
                sys.argv = old_argv
        captured = capsys.readouterr()
        assert captured.out.strip() == "ALL"
        assert rc == 0

    def test_main_quiet_output_none(self, capsys):
        """--quiet 无测试相关变更时输出 'NONE'."""
        with mock.patch.object(
            _select, "get_changed_files", return_value=["README.md"]
        ):
            old_argv = sys.argv
            sys.argv = ["_select_tests_by_diff.py", "--quiet"]
            try:
                rc = _select.main()
            finally:
                sys.argv = old_argv
        captured = capsys.readouterr()
        assert captured.out.strip() == "NONE"
        assert rc == 0

    def test_main_error_fallback_full(self, capsys):
        """异常时 fallback 全量, 返回 1."""
        with mock.patch.object(
            _select, "select_tests", side_effect=RuntimeError("boom")
        ):
            old_argv = sys.argv
            sys.argv = ["_select_tests_by_diff.py", "--quiet"]
            try:
                rc = _select.main()
            finally:
                sys.argv = old_argv
        captured = capsys.readouterr()
        assert captured.out.strip() == "ALL"
        assert rc == 1


# ============================================================
# 9. 真实场景集成测试 (慢, 标记 slow)
# ============================================================
@pytest.mark.slow
class TestRealScenario:
    """真实场景集成测试 (调用真实 git + AST 扫描)."""

    def test_real_index_has_reasonable_size(self):
        """真实反向索引规模合理 (至少 50 个模块)."""
        index = _select.build_reverse_dependency_index()
        # 68 个测试文件, 每个 import 至少几个模块, 索引应有一定规模
        assert len(index) >= 50

    def test_real_scan_duration_under_3s(self):
        """真实扫描耗时 < 3s (CI 友好)."""
        import time
        start = time.perf_counter()
        _select.build_reverse_dependency_index()
        duration = time.perf_counter() - start
        assert duration < 3.0, f"扫描耗时 {duration:.2f}s 超过 3s 阈值"

    def test_drift_monitor_change_selects_correct_test(self):
        """变更 drift_monitor.py 应选中 test_drift_monitor_sim_mode.py."""
        # 用真实反向索引
        reverse_index = _select.build_reverse_dependency_index()
        affected = _select.select_affected_tests(
            source_changes=["utils/alpha/drift_monitor.py"],
            test_changes=[],
            reverse_index=reverse_index,
        )
        affected_names = [p.name for p in affected]
        # 至少选中一个 drift_monitor 相关测试
        assert any("drift_monitor" in n for n in affected_names), \
            f"应选中 drift_monitor 相关测试, 实际: {affected_names}"
