"""TDD 守卫 — GAP-4 交付物.

ECC tdd-workflow 修复:
    新增生产代码文件必须有对应的测试文件, 否则 CI 阻断.

规则:
    1. 扫描 git diff 中的新增 .py 文件 (utils/*.py, v8.3_institutional/src/*.py)
    2. 排除: _*.py (私有脚本) / test_*.py (自身是测试) / __init__.py / tools/* / scripts/*
    3. 对每个新增生产代码文件, 检查 tests/unit/test_{module_name}.py 是否存在
    4. 缺失则输出 TDD GUARD FAILED, exit 1

用法:
    # 本地验证
    python scripts/_tdd_guard.py

    # CI 中 (PR 触发)
    python scripts/_tdd_guard.py --base origin/main --head HEAD

退出码:
    0 = 通过 (所有新增生产代码都有对应测试, 或无新增生产代码)
    1 = 失败 (有新增生产代码缺少对应测试)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 监控的生产代码目录
PRODUCTION_DIRS = [
    "utils",
    "v8.3_institutional/src",
]

# 排除的文件模式
EXCLUDE_PATTERNS = [
    "__init__.py",
    "setup.py",
    "conftest.py",
]

# 排除的目录
EXCLUDE_DIRS = [
    "tests",
    "scripts",
    "tools",
    "research",
    "cloud_train",
    "qlib_env",
    "15_每日工作流",
    "ms_strategy",
    "docs",
    ".trae",
    ".github",
]


def _run_git(args: list[str]) -> str:
    """执行 git 命令, 返回输出."""
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
        变更文件路径列表 (相对项目根目录)
    """
    # 用 git diff --name-only 获取变更文件
    output = _run_git(["diff", "--name-only", "--diff-filter=AM", f"{base}..{head}"])
    if not output:
        # fallback: 用 status 获取未提交的变更
        output = _run_git(["status", "--porcelain"])
        # 解析 porcelain 格式 (前 3 字符是状态, 后面是文件名)
        files = []
        for line in output.split("\n"):
            if line.strip():
                # 格式: " M path/to/file" 或 "A  path/to/file"
                filepath = line[3:].strip().strip('"')
                if filepath:
                    files.append(filepath)
        return files
    return [f.strip() for f in output.split("\n") if f.strip()]


def is_production_code(filepath: str) -> bool:
    """判断文件是否为生产代码 (需要对应测试).

    Args:
        filepath: 文件路径 (相对项目根)

    Returns:
        True = 生产代码 (需要对应测试)
    """
    # 必须是 .py 文件
    if not filepath.endswith(".py"):
        return False

    # 排除私有脚本 (_*.py, 但 test_*.py 不排除因为它就是测试)
    filename = Path(filepath).name
    if filename.startswith("_") and not filename.startswith("test_"):
        return False

    # 排除特定文件
    if filename in EXCLUDE_PATTERNS:
        return False

    # 排除测试文件自身
    if filename.startswith("test_"):
        return False

    # 排除特定目录
    parts = Path(filepath).parts
    for exclude_dir in EXCLUDE_DIRS:
        if exclude_dir in parts:
            return False

    # 必须在监控的生产代码目录内
    for prod_dir in PRODUCTION_DIRS:
        if filepath.startswith(prod_dir + "/") or filepath.startswith(prod_dir + "\\"):
            return True

    return False


def find_expected_test(filepath: str) -> Path:
    """根据生产代码文件路径, 推导对应的测试文件路径.

    规则:
        utils/alpha/drift_monitor.py → tests/unit/test_drift_monitor.py
        v8.3_institutional/src/foo.py → tests/unit/test_foo.py

    Args:
        filepath: 生产代码文件路径

    Returns:
        对应的测试文件路径 (tests/unit/test_{module_name}.py)
    """
    module_name = Path(filepath).stem  # 不含扩展名的文件名
    return _PROJECT_ROOT / "tests" / "unit" / f"test_{module_name}.py"


def find_actual_tests(filepath: str) -> list[Path]:
    """查找实际存在的相关测试文件 (含 test_{module}_*.py 模式).

    Args:
        filepath: 生产代码文件路径

    Returns:
        实际存在的测试文件列表
    """
    module_name = Path(filepath).stem
    tests_dir = _PROJECT_ROOT / "tests" / "unit"
    if not tests_dir.exists():
        return []
    # 匹配 test_{module}.py 和 test_{module}_*.py
    matches = list(tests_dir.glob(f"test_{module_name}.py"))
    matches.extend(tests_dir.glob(f"test_{module_name}_*.py"))
    # 也检查 tests/integration
    integ_dir = _PROJECT_ROOT / "tests" / "integration"
    if integ_dir.exists():
        matches.extend(integ_dir.glob(f"test_{module_name}.py"))
        matches.extend(integ_dir.glob(f"test_{module_name}_*.py"))
    return list(set(matches))


def main() -> int:
    """主入口.

    Returns:
        0 = 通过, 1 = 失败
    """
    parser = argparse.ArgumentParser(description="GAP-4 TDD 守卫")
    parser.add_argument("--base", default="origin/main", help="基线分支 (默认 origin/main)")
    parser.add_argument("--head", default="HEAD", help="目标分支 (默认 HEAD)")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    print("=" * 60)
    print("TDD GUARD (GAP-4)")
    print("=" * 60)

    # 1. 获取变更文件
    changed = get_changed_files(args.base, args.head)
    if not changed:
        print("[GAP-4] 无变更文件 (或无法获取 git diff), 跳过 TDD 检查")
        print("[GAP-4] PASS (无变更)")
        return 0

    print(f"[GAP-4] 变更文件: {len(changed)} 个")

    # 2. 过滤出生产代码
    production_files = [f for f in changed if is_production_code(f)]
    if not production_files:
        print("[GAP-4] 无新增生产代码文件, 跳过 TDD 检查")
        print("[GAP-4] PASS (无生产代码变更)")
        return 0

    print(f"[GAP-4] 生产代码文件: {len(production_files)} 个")

    # 3. 检查每个生产代码文件是否有对应测试
    missing: list[tuple[str, Path]] = []
    for filepath in production_files:
        actual_tests = find_actual_tests(filepath)
        if not actual_tests:
            expected = find_expected_test(filepath)
            missing.append((filepath, expected))
            print(f"  [MISS] {filepath} → 期望 {expected.relative_to(_PROJECT_ROOT)}")
        else:
            if args.verbose:
                test_names = [str(t.relative_to(_PROJECT_ROOT)) for t in actual_tests]
                print(f"  [OK]   {filepath} → {test_names}")

    # 4. 判定
    if missing:
        print("\n" + "=" * 60)
        print(f"TDD GUARD FAILED: {len(missing)} 个生产代码文件缺少对应测试")
        print("=" * 60)
        print("\n缺失的测试文件:")
        for filepath, expected in missing:
            print(f"  {filepath}")
            print(f"    → 期望: {expected.relative_to(_PROJECT_ROOT)}")
        print("\n请为新增生产代码编写测试, 或在 PR 中说明为何不需要测试.")
        print("如需绕过 (不推荐), 可在 commit message 中加入 [skip-tdd-guard].")
        return 1
    else:
        print("\n" + "=" * 60)
        print(f"TDD GUARD PASSED: {len(production_files)} 个生产代码文件都有对应测试")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
