#!/usr/bin/env python3
"""
NaN 污染守卫检查 (F-4/F-5/F-6 门禁).

扫描代码中可能导致 NaN 传播的高风险模式:
  - np.corrcoef 调用后未接 nan_to_num 防护 (F-5)
  - IC / 相关性计算前未检查常数序列 (std < 阈值) (F-4/F-6)

设计原则:
  - 低误报: 只检查最明确的高风险模式, 避免泛化扫描导致门禁空转
  - 可追溯: 每条报告关联具体修复参考 (F-4/F-5/F-6)
  - 渐进式: 先拦截最明确的违规, 再逐步扩展规则

用法:
    python scripts/check_nan_pollution.py [--path DIR]

退出码:
    0  未发现违规
    1  发现违规
    2  工具自身错误
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 需要扫描的目录 (聚焦核心风险模块)
DEFAULT_PATHS = [
    _PROJECT_ROOT / "utils",
    _PROJECT_ROOT / "ms_strategy" / "src",
]

# 文件扩展名
SCAN_EXTS = {".py"}

# 忽略目录
IGNORE_DIRS = {
    ".venv", "venv", "__pycache__", "_archive", "external",
    "node_modules", ".git",
}

# 高风险 API 模式
CORRCOEF_CALLS = {"np.corrcoef", "numpy.corrcoef"}
NAN_TO_NUM_CALLS = {"np.nan_to_num", "numpy.nan_to_num"}


class _Violation:
    def __init__(self, filepath: Path, lineno: int, message: str) -> None:
        self.filepath = filepath
        self.lineno = lineno
        self.message = message

    def __str__(self) -> str:
        rel = self.filepath.relative_to(_PROJECT_ROOT)
        return f"{rel}:{self.lineno}: {self.message}"


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix in SCAN_EXTS:
                files.append(fpath)
    return files


def _get_line_text(lines: list[str], lineno: int) -> str:
    """获取指定行文本 (1-based)."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


def _check_corrcoef_nan_protection(
    filepath: Path, tree: ast.AST, lines: list[str],
) -> list[_Violation]:
    """检查 np.corrcoef 调用后是否有 nan_to_num 防护 (F-5)."""
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = ""
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                func_name = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            func_name = func.id

        if func_name not in CORRCOEF_CALLS:
            continue

        lineno = getattr(node, "lineno", 0)
        if lineno == 0:
            continue

        # 检查后续 10 行内是否有 nan_to_num 防护
        found_protection = False
        for i in range(lineno, min(lineno + 11, len(lines) + 1)):
            line_text = _get_line_text(lines, i)
            for protect in NAN_TO_NUM_CALLS:
                if protect in line_text:
                    found_protection = True
                    break
            if found_protection:
                break

        if not found_protection:
            violations.append(
                _Violation(
                    filepath,
                    lineno,
                    "np.corrcoef 调用后未检测到 nan_to_num 防护 (F-5)",
                )
            )

    return violations


def _check_ic_constant_series_guard(
    filepath: Path, tree: ast.AST, lines: list[str],
) -> list[_Violation]:
    """检查 IC / 相关性计算前是否有常数序列防护 (F-4/F-6)."""
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = ""
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                func_name = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            func_name = func.id

        if func_name not in CORRCOEF_CALLS:
            continue

        lineno = getattr(node, "lineno", 0)
        if lineno == 0:
            continue

        # 检查前 20 行内是否有 std 检查防护
        found_guard = False
        start_line = max(1, lineno - 20)
        for i in range(start_line, lineno + 1):
            line_text = _get_line_text(lines, i)
            if "np.std" in line_text or "std(" in line_text:
                # 检查是否有 > 阈值比较
                if ">" in line_text and (
                    "1e-" in line_text or "0.0" in line_text or "0 " in line_text
                    or "1e-9" in line_text or "1e-12" in line_text
                ):
                    found_guard = True
                    break

        if not found_guard:
            violations.append(
                _Violation(
                    filepath,
                    lineno,
                    f"{func_name} 调用前未检测到常数序列防护 (std 检查) (F-4/F-6)",
                )
            )

    return violations


def scan_file(fpath: Path) -> list[_Violation]:
    """扫描单个 Python 文件."""
    if fpath.suffix not in SCAN_EXTS:
        return []
    try:
        source = fpath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
    except Exception as exc:
        print(f"[WARN] 无法解析 {fpath}: {exc}", file=sys.stderr)
        return []

    return (
        _check_corrcoef_nan_protection(fpath, tree, lines)
        + _check_ic_constant_series_guard(fpath, tree, lines)
    )


def scan(path: Path) -> tuple[list[_Violation], int]:
    """扫描指定路径下的 Python 文件."""
    all_violations: list[_Violation] = []
    scanned = 0

    if path.is_file():
        files = [path]
    else:
        files = _iter_python_files(path)

    for fpath in files:
        violations = scan_file(fpath)
        if violations:
            all_violations.extend(violations)
            scanned += 1

    return all_violations, scanned


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NaN 污染守卫检查 (F-4/F-5/F-6)"
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="要扫描的目录或文件 (默认: utils/ ms_strategy/src/)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        type=Path,
        default=None,
        help="要检查的具体文件列表 (用于 pre-commit 暂存文件检查)",
    )
    args = parser.parse_args()

    all_violations: list[_Violation] = []
    total_scanned = 0

    if args.files:
        # 只检查指定文件 (pre-commit 暂存文件模式)
        for fpath in args.files:
            violations = scan_file(fpath)
            all_violations.extend(violations)
            if violations:
                total_scanned += 1
    else:
        # 扫描整个目录
        targets = (
            [args.path] if args.path else [p for p in DEFAULT_PATHS if p.exists()]
        )
        if not targets:
            print("[INFO] 未找到需要扫描的目录", file=sys.stderr)
            return 0

        for target in targets:
            violations, scanned = scan(target)
            all_violations.extend(violations)
            total_scanned += scanned

    if all_violations:
        print(f"[FAIL] 发现 {len(all_violations)} 处 NaN 污染风险 (扫描 {total_scanned} 文件):")
        for v in all_violations:
            print(f"  {v}")
        return 1

    print(f"[PASS] NaN 守卫检查通过 (扫描 {total_scanned} 文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
