#!/usr/bin/env python3
"""
Windows 脚本健康检查 (bat 行尾 + 解释器路径存在性)
====================================================
版本: v8.6.14
来源: 2026-09-01 代码质量扫描 P0-2/P0-4 防复发机制
      (08-19 曾修过 3 个线上任务的坏解释器路径但未修源头注册脚本, 导致复发;
       run_eod_workflow.bat LF-only 行尾在 cmd 下解析必炸)

检查内容:
    A. 所有 git-tracked .bat/.cmd 必须为 CRLF 行尾 (裸 LF 在 cmd 解析会出错)
    B. .bat/.cmd/.ps1 中引用的 python 解释器路径必须真实存在
       - 绝对路径 (盘符开头, 以 python.exe 结尾) → 直接校验存在性
       - 项目相对路径 (.venv 下的 python.exe) → 按 PROJECT_ROOT 校验
       - 裸 `python` / `py -3.11` (PATH 依赖) → 不校验 (宽松, 避免误伤)

用法:
    python scripts/check_windows_scripts.py            # 全量 (git-tracked)
    python scripts/check_windows_scripts.py --staged   # 仅暂存区文件
    python scripts/check_windows_scripts.py --quiet    # 只输出失败项

退出码: 0=通过  1=存在违规
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 排除目录 (与 ruff.toml extend-exclude 对齐: 第三方/生成代码)
EXCLUDE_DIRS = {
    "temp", "qlib_env", "node_modules", "build", "dist",
    "ifind-finance-data-1.3.0", "second-brain", "external",
}

# python.exe 路径 token: 盘符绝对路径 或 .venv 开头的相对路径
_PY_EXE_RE = re.compile(
    r'(?P<path>(?:[A-Za-z]:\\[\w\\\-\.\u4e00-\u9fff]+?)|\.venv[\w\\\-\./]+?)python\.exe',
    re.IGNORECASE,
)


def _git_tracked_files() -> list[str]:
    """返回 git-tracked 文件列表 (git 不可用时返回空)."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=15,
        )
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.strip().split("\n") if f]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _staged_files() -> list[str]:
    """返回暂存区文件列表."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=15,
        )
        return [f for f in result.stdout.strip().split("\n") if f]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _is_excluded(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return any(p in EXCLUDE_DIRS for p in parts[:-1])


def check_bat_line_endings(files: list[Path], verbose: bool) -> list[str]:
    """检查 A: .bat/.cmd 必须无裸 LF (CRLF)."""
    violations: list[str] = []
    for f in files:
        if f.suffix.lower() not in (".bat", ".cmd"):
            continue
        if not f.exists():
            continue
        data = f.read_bytes()
        if not data:
            continue
        # 逐行找裸 LF: 前一个字节不是 \r 的 \n
        bare_lf_lines = [
            i + 1 for i, b in enumerate(data) if b == 0x0A and (i == 0 or data[i - 1] != 0x0D)
        ]
        if bare_lf_lines:
            violations.append(
                f"{f.relative_to(PROJECT_ROOT)}: 裸 LF 行尾 (共 {len(bare_lf_lines)} 行, "
                f"如第 {bare_lf_lines[0]} 行) — cmd 解析会出错, 请转 CRLF"
            )
        elif verbose:
            print(f"  [OK] {f.relative_to(PROJECT_ROOT)}: CRLF")
    return violations


def check_interpreter_paths(files: list[Path], verbose: bool) -> list[str]:
    """检查 B: 脚本中引用的 python.exe 路径必须存在."""
    violations: list[str] = []
    for f in files:
        if f.suffix.lower() not in (".bat", ".cmd", ".ps1"):
            continue
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "python.exe" not in line.lower():
                continue
            for m in _PY_EXE_RE.finditer(line):
                ref = m.group("path") + "python.exe"
                # 展开 %VAR% 形式的环境变量 (脚本里常见 %~dp0)
                expanded = os.path.expandvars(ref)
                candidates = [
                    Path(expanded),
                    PROJECT_ROOT / ref,          # 相对项目根
                    f.parent / ref,               # 相对脚本所在目录
                ]
                if not any(c.exists() for c in candidates):
                    violations.append(
                        f"{f.relative_to(PROJECT_ROOT)}:{lineno}: 引用不存在的解释器 '{ref}'"
                    )
                elif verbose:
                    print(f"  [OK] {f.relative_to(PROJECT_ROOT)}:{lineno}: {ref}")
    return violations


def main() -> int:
    # Windows 控制台默认 GBK, 强制 stdout/stderr 用 UTF-8 (与 pre_commit_check.py 同款)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description="bat 行尾 + 解释器路径健康检查")
    parser.add_argument("--staged", action="store_true", help="仅检查暂存区文件")
    parser.add_argument("--quiet", action="store_true", help="只输出失败项")
    args = parser.parse_args()

    if args.staged:
        rel_files = [f for f in _staged_files() if not _is_excluded(f)]
    else:
        rel_files = [f for f in _git_tracked_files() if not _is_excluded(f)]

    if not rel_files:
        print("[check_windows_scripts] 无待检查文件, 跳过")
        return 0

    files = [PROJECT_ROOT / f for f in rel_files]
    verbose = not args.quiet

    if verbose:
        print(f"[check_windows_scripts] 检查 {len(files)} 个文件 (bat/cmd 行尾 + ps1/bat 解释器路径)")

    violations = check_bat_line_endings(files, verbose)
    violations += check_interpreter_paths(files, verbose)

    if violations:
        print(f"\n[check_windows_scripts] ❌ 发现 {len(violations)} 处违规:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("[check_windows_scripts] ✅ 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
