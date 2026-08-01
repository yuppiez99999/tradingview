#!/usr/bin/env python3
"""
Pre-commit Check (Python 包装器)
================================
版本: v8.6.12
用途: git pre-commit hook 调用入口,在提交前自动执行 P0 自检。

调用方式 (3 种):
    1. 直接调用 (调试):
        python scripts/pre_commit_check.py

    2. 通过 git hook 自动调用 (.git/hooks/pre-commit):
        #!/bin/sh
        exec python scripts/pre_commit_check.py

    3. 通过 core.hooksPath 共享 hook (推荐团队使用):
        git config core.hooksPath githooks
        # githooks/pre-commit 调用此脚本

退出码:
    0 = 自检通过,允许提交
    1 = 自检失败,阻止提交 (必须修复后重试)
    2 = 自检脚本异常 (容错通过,允许提交)

设计原则:
    - 默认 --skip-datasource: 加速 pre-commit,避免每次提交都连数据源
    - 静默模式: 仅输出失败项与结论,不刷屏
    - 容错: 自检本身异常时 exit 0,不阻断开发流程
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# 项目根目录 (此脚本位于 scripts/pre_commit_check.py)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # 跳过条件 1: 非 pre-commit 阶段 (如 rebase/merge)
    # git 会在 MERGE_HEAD 存在时跳过 hook,但显式检查更稳妥
    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        print("[pre-commit] 非 git 仓库,跳过自检")
        return 0

    # 跳过条件 2: 环境变量 SKIP_P0_CHECK=1 (紧急提交时使用)
    import os
    if os.environ.get("SKIP_P0_CHECK") == "1":
        print("[pre-commit] SKIP_P0_CHECK=1,跳过 P0 自检")
        return 0

    # 跳过条件 3: 仅文档/配置变更 (可选优化,避免每次都跑)
    # 检查暂存区文件,若全是 .md/.txt/.gitignore,可跳过
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=10,
        )
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        if staged_files:
            doc_only = all(
                f.endswith((".md", ".txt", ".gitignore", ".editorconfig"))
                for f in staged_files
            )
            if doc_only:
                print(f"[pre-commit] 仅文档/配置变更 ({len(staged_files)} 文件),跳过 P0 自检")
                return 0
    except Exception as e:
        print(f"[pre-commit] 检查暂存区异常 (容错继续): {e}", file=sys.stderr)

    # 执行 P0 自检 (--skip-datasource 加速)
    check_script = PROJECT_ROOT / "scripts" / "run_p0_startup_check.py"
    if not check_script.exists():
        print(f"[pre-commit] 自检脚本不存在: {check_script}", file=sys.stderr)
        return 0  # 容错通过

    print("[pre-commit] 执行 P0 启动自检 (--skip-datasource)...")
    try:
        result = subprocess.run(
            [sys.executable, str(check_script), "--skip-datasource"],
            cwd=str(PROJECT_ROOT),
            timeout=120,  # 2 分钟超时
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        print("[pre-commit] P0 自检超时 (120s),容错通过", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"[pre-commit] P0 自检异常 (容错通过): {e}", file=sys.stderr)
        return 0

    if exit_code == 0:
        print("[pre-commit] ✅ P0 自检通过,允许提交")
        return 0
    else:
        print(f"[pre-commit] ❌ P0 自检失败 (exit={exit_code}),阻止提交", file=sys.stderr)
        print("[pre-commit] 修复后重试,或临时用 SKIP_P0_CHECK=1 跳过", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
