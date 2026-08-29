#!/usr/bin/env python
"""
_select_tests_by_diff.py — 基于 git diff 的 AST 智能选测 (真实实现)

R1 修复项。CI "Smart Test Selection" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    利用 git diff (PR 分支 vs 目标分支) 确定本次变更的生产代码文件,
    通过 AST 解析构建模块级 import 依赖图, 反向推断 (reverse BFS) 哪些
    测试文件会受这些变更影响, 输出一个 pytest 可消费的选择集。

输出约定 (stdout, 供 CI 管道消费):
    ALL               -> 变更影响面过大 / 无法判定, 运行全量测试
    NONE              -> 无生产代码变更, 跳过测试
    tests/foo.py tests/bar.py ...   -> 受影响测试文件列表 (空格分隔)

退出码:
    0 = 正常输出选择集 (无论 ALL/NONE/列表)
    2 = 入参错误或 git 不可用 (由调用方决定是否 fail-close)

用法:
    python scripts/_select_tests_by_diff.py \
        --base origin/main --head HEAD \
        [--tests-root tests] [--max-diff-files 40] [--output-file reports/ci/selected_tests.txt]
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


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


def get_changed_files(base: str, head: str) -> list[str]:
    # 优先 merge-base (准确反映 PR 增量)
    try:
        mb = run_git(["merge-base", base, head]).strip()
        range_spec = f"{mb}...{head}"
    except RuntimeError:
        range_spec = f"{base}...{head}"
    out = run_git(["diff", "--name-only", "--diff-filter=ACMR", range_spec])
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def module_name_of(py_path: Path) -> str:
    """将仓库内 .py 路径映射为可 import 的模块名 (点分)."""
    rel = py_path.resolve().relative_to(ROOT)
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts)


def build_import_graph(py_files: list[Path]) -> dict[str, set[str]]:
    """解析每个生产模块 import 的其它仓库内模块, 返回 模块->被依赖模块集合.

    同时返回 反向图 (被依赖 -> 依赖者), 用于反向 BFS。
    """
    graph: dict[str, set[str]] = {}
    for p in py_files:
        mod = module_name_of(p)
        imports: set[str] = set()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            graph[mod] = imports
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        graph[mod] = imports
    return graph


def reverse_graph(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = defaultdict(set)
    for mod, deps in graph.items():
        for d in deps:
            rev[d].add(mod)
    return rev


def collect_py_roots(roots: list[str]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        rp = ROOT / r
        if rp.is_file() and rp.suffix == ".py":
            out.append(rp)
        elif rp.is_dir():
            out.extend(rp.rglob("*.py"))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AST-based smart test selection")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--tests-root", default="tests")
    parser.add_argument("--max-diff-files", type=int, default=40)
    parser.add_argument(
        "--output-file", default=str(ROOT / "reports" / "ci" / "selected_tests.txt")
    )
    parser.add_argument(
        "--diff-file-list",
        default=None,
        help="可选: 直接传入变更文件列表 (逗号分隔), 跳过 git diff",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式: 抑制 stderr 上的告警/进度输出 (结果 token 始终输出到 stdout)",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 变更文件
    if args.diff_file_list:
        changed = [c for c in args.diff_file_list.split(",") if c.strip()]
    else:
        try:
            changed = get_changed_files(args.base, args.head)
        except RuntimeError as e:
            # git 不可用 / 无远端 -> fail-open 返回 ALL (运行全量)
            # 结果 token 必须走 stdout: CI 用 $(...) 只捕获 stdout, 写到 stderr 会导致
            # 调用方拿到空字符串 (2026-08-29 修正)
            print("ALL")
            if not args.quiet:
                print(f"[SELECT-TESTS][WARN] {e}; defaulting to ALL", file=sys.stderr)
            out_path.write_text("ALL\n", encoding="utf-8")
            return 0

    # 仅看仓库内的 .py 变更
    changed_py = [c for c in changed if c.endswith(".py") and (ROOT / c).exists()]
    changed_prod = [
        c
        for c in changed_py
        if not c.startswith(args.tests_root + "/") and c != "tests"
    ]

    if not changed:
        out_path.write_text("NONE\n", encoding="utf-8")
        print("NONE")
        return 0

    # 变更面过大 -> ALL
    if len(changed_prod) > args.max_diff_files:
        out_path.write_text("ALL\n", encoding="utf-8")
        print("ALL")
        return 0

    # 2. 构建依赖图
    prod_roots = [
        "scripts",
        "utils",
        "ai_decision",
        "cli",
        "core",
        "quant_modules",
        "lgb_trainer",
        "v8.3_institutional",
        "15_每日工作流",
        ".",
    ]
    prod_files = collect_py_roots(prod_roots)
    # 仅保留仓库内的非测试文件
    prod_files = [
        p
        for p in prod_files
        if not str(p).replace("\\", "/").startswith(args.tests_root + "/")
    ]
    graph = build_import_graph(prod_files)
    rev = reverse_graph(graph)

    # 3. 反向 BFS: 从变更的模块名找出所有 (间接) 依赖它的模块
    changed_mods = {module_name_of(ROOT / c) for c in changed_prod}
    affected: set[str] = set()
    queue = deque(changed_mods)
    while queue:
        m = queue.popleft()
        affected.add(m)
        for dependent in rev.get(m, ()):
            if dependent not in affected:
                queue.append(dependent)

    # 4. 映射到测试文件: 测试文件若 import 了 affected 模块, 则选中
    test_files = collect_py_roots([args.tests_root])
    selected: list[str] = []
    for tf in test_files:
        module_name_of(tf)
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        tf_imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    tf_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    tf_imports.add(node.module)
        if tf_imports & affected:
            selected.append(str(tf.resolve().relative_to(ROOT)).replace("\\", "/"))

    if not selected:
        out_path.write_text("NONE\n", encoding="utf-8")
        print("NONE")
        return 0

    result_line = " ".join(selected)
    out_path.write_text(result_line + "\n", encoding="utf-8")
    print(result_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
