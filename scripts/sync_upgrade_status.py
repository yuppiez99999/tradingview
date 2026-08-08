#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
计划文档代码驱动更新 (Upgrade Status Sync from Git)
=====================================================
创建: 2026-08-06 防复发机制 #4
目的: 防止"计划文档与代码状态脱节"——计划说"未启动"但代码已经做了。
      通过扫描 git log 中的标记, 自动检测已完成但未更新状态的升级项。

工作原理:
    1. 读取计划文档中的升级项状态表
    2. 扫描 git log 中的 [Ux] [Gx] [Px] 标记
    3. 对比: 如果 git log 有标记但文档状态是"未启动", 报告不一致

用法:
    python scripts/sync_upgrade_status.py                          # 检查不一致
    python scripts/sync_upgrade_status.py --plan docs/XXX.md       # 指定计划文档
    python scripts/sync_upgrade_status.py --update                 # 自动更新文档状态(实验性)

退出码:
    0 = 文档与代码一致
    1 = 发现不一致 (应更新文档)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 升级项标记模式: [U1] [G3] [P0-2] [W6] 等
MARKER_PATTERN = re.compile(r"\[([UGPWTFS]\d+(?:-\d+)?)\]")

# 计划文档默认路径
DEFAULT_PLAN = _PROJECT_ROOT / "docs" / "自我升级计划完成进度及后续工程_20260806.md"


def get_git_markers(max_commits: int = 100) -> dict[str, list[str]]:
    """扫描 git log 中的升级项标记.

    Returns:
        {marker: [commit_hash, ...]} 字典
    """
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}", "--oneline", "--format=%H %s"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}

    markers: dict[str, list[str]] = {}
    stdout_text = result.stdout.decode("utf-8", errors="replace")
    for line in stdout_text.splitlines():
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        commit_hash, message = parts
        found = MARKER_PATTERN.findall(message)
        for marker in found:
            markers.setdefault(marker, []).append(commit_hash[:8])

    return markers


def parse_plan_status(plan_path: Path) -> dict[str, dict[str, str]]:
    """解析计划文档中的升级项状态表.

    Returns:
        {marker: {"status": "...", "name": "...", "line": N}} 字典
    """
    if not plan_path.exists():
        return {}

    content = plan_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    items: dict[str, dict[str, str]] = {}

    # 匹配表格行: | U1 | ... | ✅ 完成 | ... 或 | G3 | ... | 🔲 未启动 | ...
    table_pattern = re.compile(r"^\|\s*([UGPWTFS]\d+(?:-\d+)?)\s*\|.*?\|\s*(.{1,30})\s*\|")

    for i, line in enumerate(lines, 1):
        m = table_pattern.match(line)
        if m:
            marker = m.group(1)
            # 提取状态关键词
            if "完成" in line or "PASS" in line.upper():
                status = "done"
            elif "未启动" in line or "🔲" in line:
                status = "not_started"
            elif "进行中" in line or "观察" in line or "🟡" in line:
                status = "in_progress"
            elif "待用户" in line or "⏳" in line:
                status = "waiting"
            else:
                status = "unknown"
            items[marker] = {"status": status, "line": i, "raw": line.strip()}

    return items


def check_consistency(plan_path: Path) -> list[dict[str, Any]]:
    """检查计划文档与 git log 的一致性."""
    git_markers = get_git_markers()
    plan_items = parse_plan_status(plan_path)

    inconsistencies: list[dict[str, Any]] = []

    for marker, commits in git_markers.items():
        if marker in plan_items:
            plan_status = plan_items[marker]["status"]
            # 如果 git 有提交但计划标"未启动", 是不一致
            if plan_status == "not_started":
                inconsistencies.append({
                    "marker": marker,
                    "issue": f"git log 有 {len(commits)} 次提交但计划标'未启动'",
                    "commits": commits,
                    "plan_line": plan_items[marker]["line"],
                    "plan_raw": plan_items[marker]["raw"],
                })

    return inconsistencies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="计划文档代码驱动更新检查")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN), help="计划文档路径")
    parser.add_argument("--update", action="store_true", help="自动更新文档状态(实验性)")
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"[XX] 计划文档不存在: {plan_path}")
        return 1

    print("=" * 70)
    print("计划文档代码驱动更新检查 (Upgrade Status Sync)")
    print(f"计划文档: {plan_path.name}")
    print("=" * 70)

    inconsistencies = check_consistency(plan_path)

    if not inconsistencies:
        print("[OK] 计划文档与 git log 一致")
        return 0

    print(f"[!!] 发现 {len(inconsistencies)} 处不一致:")
    for inc in inconsistencies:
        print(f"  [{inc['marker']}] {inc['issue']}")
        print(f"    提交: {', '.join(inc['commits'][:3])}")
        print(f"    计划第 {inc['plan_line']} 行: {inc['plan_raw'][:80]}")
    print()
    print("修复方法: 更新计划文档中对应项的状态为'已完成'")

    if args.update:
        print("(自动更新功能待实现, 请手动更新)")

    return 1


if __name__ == "__main__":
    sys.exit(main())
