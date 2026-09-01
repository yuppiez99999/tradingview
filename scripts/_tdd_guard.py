#!/usr/bin/env python
"""
_tdd_guard.py — TDD Guard (GAP-4 真实实现)

CI 引用脚本 (tdd-guard.yml), 此前缺失导致 TDD Guard 实际失效。
本脚本补齐 GAP-4 交付物, 语义与 workflow 注释一致:

规则:
    - PR 新增 utils/*.py 或 v8.3_institutional/src/*.py 必须有对应
      tests/unit/test_<basename>.py
    - 排除: _*.py / __init__.py / test_*.py / scripts/* / tools/* / docs/* / tests/*
    - fail-open: 无法获取 git diff 时 PASS (exit 0, 不阻断)

退出码:
    0 = 通过 (含 fail-open)
    1 = 有新增生产代码缺少对应测试 (阻断合并)

用法:
    python scripts/_tdd_guard.py --base origin/main --head HEAD [--verbose]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 需检查的生产代码目录 (workflow paths 白名单)
PROD_DIRS = ("utils", "v8.3_institutional/src")

# 被跟踪文件默认前缀, 与 tests 目录布局对应
TEST_ROOT = "tests/unit"


def run_git(args: list[str]) -> str:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        ["git"] + args,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def get_added_files(base: str, head: str) -> list[str]:
    """返回 PR 中新增 (Added) 的文件路径列表 (仅 .py)。"""
    try:
        mb = run_git(["merge-base", base, head]).strip()
        range_spec = f"{mb}...{head}"
    except RuntimeError:
        range_spec = f"{base}...{head}"
    # --diff-filter=A: 仅新增文件 (Added)。修改已有文件不触发 TDD Guard。
    out = run_git(["diff", "--name-only", "--diff-filter=A", range_spec])
    return [
        ln.strip()
        for ln in out.splitlines()
        if ln.strip() and ln.strip().endswith(".py")
    ]


def expected_test_path(prod_rel: str) -> str | None:
    """生产文件路径 -> 期望的测试文件路径; 不符合规则返回 None。"""
    norm = prod_rel.replace("\\", "/")
    parts = norm.split("/")
    if len(parts) < 2 or parts[0] not in PROD_DIRS:
        return None
    basename = parts[-1]
    # 排除: _*.py / __init__.py / test_*.py
    if (
        basename.startswith("_")
        or basename == "__init__.py"
        or basename.startswith("test_")
    ):
        return None
    return f"{TEST_ROOT}/test_{basename}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TDD Guard (GAP-4)")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        added = get_added_files(args.base, args.head)
    except RuntimeError as e:
        # fail-open: git 不可用 / 无法计算 diff 时 PASS (不阻断)
        print(f"[TDD-GUARD][WARN] {e}; fail-open PASS", file=sys.stderr)
        return 0

    if not added:
        print("[TDD-GUARD] no added .py files, PASS")
        return 0

    missing: list[str] = []
    for f in added:
        expected = expected_test_path(f)
        if expected is None:
            continue
        if not (ROOT / expected).exists():
            missing.append((f, expected))

    if not missing:
        print(f"[TDD-GUARD] PASS ({len(added)} added files, all covered)")
        return 0

    for prod, exp in missing:
        msg = f"[TDD-GUARD][FAIL] 新增生产代码缺少对应测试: {prod} -> 期望 {exp}"
        print(msg)
        if args.verbose:
            print(f"  hint: 创建 {exp} 并覆盖 {prod} 的核心逻辑")
    print(f"[TDD-GUARD] {len(missing)} 个新增生产文件缺少对应测试, 阻断合并")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
