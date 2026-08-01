"""智能测试选择 — GAP-5 交付物.

ECC verification-loop / mle-workflow 修复:
    PR 只跑受变更影响的测试, 而非每次全量 68 个测试文件.

设计原则:
    1. 基于 AST 静态依赖分析 (不执行代码, < 3s)
    2. 变更文件 → 模块名 → 反向查找 import 它的测试
    3. fail-safe: 无法确定依赖时回退全量 (宁可多跑不可漏跑)
    4. 输出 JSON 供 CI 消费 (pytest --pyargs $(python _select_tests_by_diff.py))

依赖图构建:
    Step 1: 扫描 tests/**/*.py, AST 提取每个测试文件的 import 语句
    Step 2: 构建 {被依赖模块: [测试文件...]} 反向索引
    Step 3: 变更文件路径 → 模块名 → 查反向索引 → 受影响测试
    Step 4: 变更测试文件自身 → 直接选中
    Step 5: 变更 conftest.py / pytest.ini / setup.py → 全量 (基础设施)

用法:
    # 本地: 打印受影响测试列表
    python scripts/_select_tests_by_diff.py

    # CI: 输出 JSON 供脚本消费
    python scripts/_select_tests_by_diff.py --json --base origin/main --head HEAD

    # 配合 pytest
    TESTS=$(python scripts/_select_tests_by_diff.py --quiet)
    if [ "$TESTS" = "ALL" ]; then
        pytest tests/unit
    else
        pytest $TESTS
    fi

退出码:
    0 = 成功 (无论全量还是子集)
    1 = 错误 (git/AST 解析失败, 应回退全量)
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 测试目录 (扫描这些目录建立依赖图)
TEST_DIRS = [
    "tests/unit",
    "tests/integration",
    "tests/smoke",
    "tests/e2e",
]

# 生产代码根目录 (变更这些目录的文件才需要反向查找测试)
SOURCE_ROOTS = [
    "utils",
    "v8.3_institutional/src",
    "v8.3_institutional",
    "research",
]

# 变更这些文件 → 触发全量测试 (基础设施变更影响所有测试)
INFRA_FILES = {
    "conftest.py",
    "pytest.ini",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "mypy.ini",
    ".pylintrc",
    "requirements.txt",
    ".coveragerc",
    "scripts/_smoke_runner.py",
    "scripts/_tdd_guard.py",
    "scripts/_select_tests_by_diff.py",
    "scripts/_check_coverage_trend.py",
}

# 变更这些目录下的文件 → 触发全量测试 (CI / 工作流变更)
INFRA_DIRS = {
    ".github/workflows",
    "tests/conftest.py",
}


@dataclass(frozen=True)
class TestSelectionResult:
    """测试选择结果 (不可变)."""

    selected_tests: tuple[str, ...] = field(default_factory=tuple)
    is_full_suite: bool = False
    reason: str = ""
    changed_files_count: int = 0
    source_changes_count: int = 0
    test_changes_count: int = 0
    infra_changes_count: int = 0
    scan_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_tests": list(self.selected_tests),
            "is_full_suite": self.is_full_suite,
            "reason": self.reason,
            "changed_files_count": self.changed_files_count,
            "source_changes_count": self.source_changes_count,
            "test_changes_count": self.test_changes_count,
            "infra_changes_count": self.infra_changes_count,
            "scan_duration_ms": round(self.scan_duration_ms, 1),
        }


# ============================================================
# Git diff 获取
# ============================================================
def _run_git(args: list[str]) -> str:
    """执行 git 命令, 返回 stdout (失败返回空串)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def get_changed_files(base: str = "origin/main", head: str = "HEAD") -> list[str]:
    """获取 base..head 之间变更的文件列表.

    Args:
        base: 基线分支 (如 origin/main)
        head: 目标分支 (如 HEAD)

    Returns:
        变更文件路径列表 (相对项目根, 正斜杠)
    """
    output = _run_git(["diff", "--name-only", "--diff-filter=AM", f"{base}..{head}"])
    if output:
        return [f.strip() for f in output.split("\n") if f.strip()]
    # fallback: 未提交的变更 (本地开发场景)
    output = _run_git(["status", "--porcelain"])
    files: list[str] = []
    for line in output.split("\n"):
        if line.strip():
            filepath = line[3:].strip().strip('"').replace("\\", "/")
            if filepath:
                files.append(filepath)
    return files


# ============================================================
# AST 依赖分析
# ============================================================
def extract_imports(filepath: Path) -> set[str]:
    """从 Python 文件 AST 提取所有 import 的模块名.

    支持:
        import foo.bar.baz          → foo.bar.baz
        import foo.bar as fb        → foo.bar
        from foo.bar import baz     → foo.bar
        from . import sibling       → (相对导入, 跳过)
        from .sibling import func   → (相对导入, 跳过)

    Args:
        filepath: .py 文件路径

    Returns:
        模块名集合 (绝对导入, 相对导入已过滤)
    """
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name and not alias.name.startswith("."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # 相对导入 (level > 0) 跳过, 无法静态解析目标模块
            if node.level and node.level > 0:
                continue
            if node.module and not node.module.startswith("."):
                imports.add(node.module)
    return imports


def filepath_to_module_variants(filepath: str) -> set[str]:
    """文件路径转所有可能的模块名变体 (dotted).

    由于 sys.path 注入了多个根 (项目根 / v8.3_institutional / v8.3_institutional/src),
    同一文件可能被多种方式 import. 为避免漏匹配, 返回所有变体:
        utils/alpha/drift_monitor.py → {utils.alpha.drift_monitor, alpha.drift_monitor, drift_monitor}
        v8.3_institutional/src/foo.py → {v8.3_institutional.src.foo, src.foo, foo}
        v8.3_institutional/daily_workflow.py → {v8.3_institutional.daily_workflow, daily_workflow}

    Args:
        filepath: 相对项目根的文件路径 (正斜杠)

    Returns:
        所有可能的 dotted 模块名集合
    """
    parts = list(Path(filepath).with_suffix("").parts)
    if not parts:
        return set()

    variants: set[str] = set()
    # 完整路径 (从项目根 import)
    variants.add(".".join(parts))
    # 对每个 source root, 生成去掉该 root 前缀的变体
    for root in SOURCE_ROOTS:
        root_parts = list(Path(root).parts)
        if len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts:
            suffix = parts[len(root_parts):]
            if suffix:
                variants.add(".".join(suffix))
    return variants


# ============================================================
# 反向依赖索引构建
# ============================================================
def build_reverse_dependency_index() -> dict[str, list[Path]]:
    """扫描所有测试文件, 构建 {被依赖模块: [测试文件...]} 反向索引.

    Returns:
        字典: 模块名 → 依赖它的测试文件列表
    """
    reverse_index: dict[str, list[Path]] = {}
    for test_dir in TEST_DIRS:
        abs_test_dir = _PROJECT_ROOT / test_dir
        if not abs_test_dir.exists():
            continue
        for test_file in abs_test_dir.glob("**/test_*.py"):
            imports = extract_imports(test_file)
            for module in imports:
                # 索引完整模块名 + 所有前缀 (utils.alpha.foo / utils.alpha / utils)
                parts = module.split(".")
                for i in range(len(parts), 0, -1):
                    prefix = ".".join(parts[:i])
                    reverse_index.setdefault(prefix, []).append(test_file)
    # 去重
    for key in list(reverse_index.keys()):
        seen = set()
        unique = []
        for p in reverse_index[key]:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        reverse_index[key] = unique
    return reverse_index


# ============================================================
# 测试选择核心逻辑
# ============================================================
def classify_changes(
    changed_files: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """将变更文件分为三类: 源码 / 测试 / 基础设施.

    Args:
        changed_files: 变更文件列表

    Returns:
        (source_changes, test_changes, infra_changes)
    """
    source_changes: list[str] = []
    test_changes: list[str] = []
    infra_changes: list[str] = []

    for filepath in changed_files:
        normalized = filepath.replace("\\", "/")
        filename = Path(normalized).name

        # 1. 基础设施文件
        if filename in INFRA_FILES:
            infra_changes.append(normalized)
            continue
        is_infra_dir = False
        for infra_dir in INFRA_DIRS:
            if normalized.startswith(infra_dir):
                is_infra_dir = True
                break
        if is_infra_dir:
            infra_changes.append(normalized)
            continue

        # 2. 测试文件
        if normalized.startswith("tests/") and filename.startswith("test_") and filename.endswith(".py"):
            test_changes.append(normalized)
            continue

        # 3. 源码文件 (在 SOURCE_ROOTS 下)
        for root in SOURCE_ROOTS:
            if normalized.startswith(root + "/") and normalized.endswith(".py"):
                # 排除 __init__.py (空文件变更影响小)
                if filename == "__init__.py":
                    # __init__.py 变更可能影响包导出, 保守视为基础设施
                    infra_changes.append(normalized)
                else:
                    source_changes.append(normalized)
                break
    return source_changes, test_changes, infra_changes


def select_affected_tests(
    source_changes: list[str],
    test_changes: list[str],
    reverse_index: dict[str, list[Path]],
) -> set[Path]:
    """根据源码变更 + 测试变更, 选择受影响的测试文件.

    Args:
        source_changes: 变更的源码文件列表
        test_changes: 变更的测试文件列表 (自身就是测试, 直接选中)
        reverse_index: 反向依赖索引

    Returns:
        受影响的测试文件集合
    """
    affected: set[Path] = set()

    # 1. 变更的测试文件自身直接选中
    for test_path in test_changes:
        abs_path = _PROJECT_ROOT / test_path
        if abs_path.exists():
            affected.add(abs_path)

    # 2. 源码变更 → 反向查找依赖它的测试
    for source_file in source_changes:
        # 获取所有可能的模块名变体 (因 sys.path 多根配置)
        module_variants = filepath_to_module_variants(source_file)
        for module_name in module_variants:
            # 精确匹配
            if module_name in reverse_index:
                for test_file in reverse_index[module_name]:
                    affected.add(test_file)
            # 前缀匹配: 变更 utils/alpha/__init__.py 命中所有 import utils.alpha 的测试
            parts = module_name.split(".")
            for i in range(len(parts), 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in reverse_index:
                    for test_file in reverse_index[prefix]:
                        affected.add(test_file)

    return affected


# ============================================================
# 主入口
# ============================================================
def select_tests(
    base: str = "origin/main",
    head: str = "HEAD",
) -> TestSelectionResult:
    """智能测试选择主逻辑.

    Args:
        base: 基线分支
        head: 目标分支

    Returns:
        TestSelectionResult
    """
    import time as _time

    start = _time.perf_counter()

    # 1. 获取变更
    changed = get_changed_files(base, head)
    if not changed:
        return TestSelectionResult(
            is_full_suite=True,
            reason="no_changes_detected_fallback_full",
            scan_duration_ms=(_time.perf_counter() - start) * 1000,
        )

    # 2. 分类
    source_changes, test_changes, infra_changes = classify_changes(changed)

    # 3. 基础设施变更 → 全量
    if infra_changes:
        return TestSelectionResult(
            is_full_suite=True,
            reason=f"infra_changed: {', '.join(infra_changes[:3])}",
            changed_files_count=len(changed),
            source_changes_count=len(source_changes),
            test_changes_count=len(test_changes),
            infra_changes_count=len(infra_changes),
            scan_duration_ms=(_time.perf_counter() - start) * 1000,
        )

    # 4. 无源码也无测试变更 → 无需跑测试
    if not source_changes and not test_changes:
        return TestSelectionResult(
            selected_tests=(),
            is_full_suite=False,
            reason="no_test_relevant_changes",
            changed_files_count=len(changed),
            scan_duration_ms=(_time.perf_counter() - start) * 1000,
        )

    # 5. 构建反向索引 + 选择受影响测试
    reverse_index = build_reverse_dependency_index()
    affected = select_affected_tests(source_changes, test_changes, reverse_index)

    # 6. 无受影响测试 → 但有源码变更, 保守回退全量
    if not affected and source_changes:
        return TestSelectionResult(
            is_full_suite=True,
            reason=f"no_affected_test_found_fallback_full (source: {', '.join(source_changes[:3])})",
            changed_files_count=len(changed),
            source_changes_count=len(source_changes),
            test_changes_count=len(test_changes),
            scan_duration_ms=(_time.perf_counter() - start) * 1000,
        )

    # 7. 排序输出 (确定性, CI 友好)
    sorted_tests = sorted(affected, key=lambda p: str(p.relative_to(_PROJECT_ROOT)))
    selected = tuple(str(p.relative_to(_PROJECT_ROOT)).replace("\\", "/") for p in sorted_tests)

    return TestSelectionResult(
        selected_tests=selected,
        is_full_suite=False,
        reason=f"selected {len(selected)} affected tests",
        changed_files_count=len(changed),
        source_changes_count=len(source_changes),
        test_changes_count=len(test_changes),
        scan_duration_ms=(_time.perf_counter() - start) * 1000,
    )


def main() -> int:
    """CLI 主入口.

    Returns:
        0 = 成功, 1 = 错误
    """
    parser = argparse.ArgumentParser(description="GAP-5 智能测试选择")
    parser.add_argument("--base", default="origin/main", help="基线分支 (默认 origin/main)")
    parser.add_argument("--head", default="HEAD", help="目标分支 (默认 HEAD)")
    parser.add_argument("--json", action="store_true", help="输出 JSON (供 CI 消费)")
    parser.add_argument("--quiet", action="store_true", help="只输出测试列表 (空格分隔), 全量时输出 ALL")
    args = parser.parse_args()

    try:
        result = select_tests(base=args.base, head=args.head)
    except Exception as e:  # noqa: BLE001  # fail-safe, 任何异常回退全量
        if args.json:
            print(json.dumps({"is_full_suite": True, "error": str(e)}))
        elif args.quiet:
            print("ALL")
        else:
            print(f"[GAP-5] ERROR: {e}, fallback to full suite")
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.quiet:
        if result.is_full_suite:
            print("ALL")
        elif not result.selected_tests:
            print("NONE")
        else:
            print(" ".join(result.selected_tests))
        return 0

    # 人类可读输出
    print("=" * 60)
    print("SMART TEST SELECTION (GAP-5)")
    print("=" * 60)
    print(f"  Changed files:      {result.changed_files_count}")
    print(f"  Source changes:     {result.source_changes_count}")
    print(f"  Test changes:       {result.test_changes_count}")
    print(f"  Infra changes:      {result.infra_changes_count}")
    print(f"  Scan duration:      {result.scan_duration_ms:.1f}ms")
    print(f"  Decision:           {'FULL SUITE' if result.is_full_suite else f'{len(result.selected_tests)} tests'}")
    print(f"  Reason:             {result.reason}")
    print("-" * 60)
    if result.is_full_suite:
        print("  → Run full test suite (pytest tests/unit tests/integration)")
    elif result.selected_tests:
        print("  Selected tests:")
        for t in result.selected_tests:
            print(f"    • {t}")
    else:
        print("  → No tests to run (no test-relevant changes)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
