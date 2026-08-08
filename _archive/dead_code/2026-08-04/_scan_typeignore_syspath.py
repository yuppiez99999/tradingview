"""扫描 TYPE_IGNORE 与 SYS_PATH 真实分布 (区分项目代码 vs 第三方 references/).

输出:
    1. TYPE_IGNORE: 按文件分布 + 样本 (含/不含错误码两种)
    2. SYS_PATH: 按文件分布 + 样本 (sys.path.insert / sys.path.append)
    3. 区分项目代码 (root, 不含 references/) vs 第三方 (references/)
"""
from __future__ import annotations

import collections
import pathlib
import re

ROOT = pathlib.Path(".")
EXCLUDE_DIRS = {"references", ".git", "__pycache__", ".venv", "venv", "node_modules",
                "qlib_env", ".mypy_cache", ".pytest_cache", "build", "dist"}

# 匹配 # type: ignore (含/不含错误码)
TYPE_IGNORE_PATTERN = re.compile(r"#\s*type:\s*ignore(?:\[[^\]]+\])?", re.IGNORECASE)

# 匹配 sys.path.insert / sys.path.append
SYS_PATH_PATTERN = re.compile(r"sys\.path\.(?:insert|append)\s*\(")


def is_excluded(path: pathlib.Path) -> bool:
    """判断路径是否在排除目录中."""
    try:
        rel_parts = path.relative_to(ROOT).parts
    except ValueError:
        return True
    for part in rel_parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def scan_directory(label: str, predicate) -> None:
    """扫描目录并报告统计."""
    type_ignore_total = 0
    type_ignore_files: dict[str, int] = collections.Counter()
    type_ignore_samples: list[tuple[str, int, str]] = []

    sys_path_total = 0
    sys_path_files: dict[str, int] = collections.Counter()
    sys_path_samples: list[tuple[str, int, str]] = []

    files_scanned = 0

    for py_file in pathlib.Path(".").rglob("*.py"):
        if not predicate(py_file):
            continue
        files_scanned += 1
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(py_file)

        # TYPE_IGNORE
        for m in TYPE_IGNORE_PATTERN.finditer(source):
            type_ignore_total += 1
            type_ignore_files[rel] += 1
            if len(type_ignore_samples) < 15:
                ln = source[: m.start()].count("\n") + 1
                lines = source.splitlines()
                line_content = lines[ln - 1].strip() if ln <= len(lines) else ""
                type_ignore_samples.append((rel, ln, line_content))

        # SYS_PATH
        for m in SYS_PATH_PATTERN.finditer(source):
            sys_path_total += 1
            sys_path_files[rel] += 1
            if len(sys_path_samples) < 15:
                ln = source[: m.start()].count("\n") + 1
                lines = source.splitlines()
                # 取该行 + 下一行作为上下文
                ctx = lines[ln - 1].strip() if ln <= len(lines) else ""
                sys_path_samples.append((rel, ln, ctx))

    print(f"\n{'=' * 60}")
    print(f"=== {label} ===")
    print(f"Files scanned: {files_scanned}")
    print(f"{'=' * 60}")

    print(f"\n--- TYPE_IGNORE ---")
    print(f"Total: {type_ignore_total}")
    print(f"Files affected: {len(type_ignore_files)}")
    print(f"\nTOP 20 files:")
    for f, c in type_ignore_files.most_common(20):
        print(f"  {c:3d}  {f}")
    print(f"\nSamples (first 15):")
    for f, ln, content in type_ignore_samples:
        print(f"  {f}:{ln}")
        print(f"      {content[:120]}")

    print(f"\n--- SYS_PATH ---")
    print(f"Total: {sys_path_total}")
    print(f"Files affected: {len(sys_path_files)}")
    print(f"\nTOP 20 files:")
    for f, c in sys_path_files.most_common(20):
        print(f"  {c:3d}  {f}")
    print(f"\nSamples (first 15):")
    for f, ln, content in sys_path_samples:
        print(f"  {f}:{ln}")
        print(f"      {content[:120]}")


# 1. 项目代码 (排除 references/ 等)
def is_project_code(path: pathlib.Path) -> bool:
    return not is_excluded(path)


# 2. 第三方代码 (仅 references/)
def is_third_party(path: pathlib.Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
        return "references" in rel.parts
    except ValueError:
        return False


scan_directory("PROJECT CODE (excluding references/)", is_project_code)
scan_directory("THIRD-PARTY (references/ only)", is_third_party)
