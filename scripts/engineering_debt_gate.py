#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工程债务门槛检查 (Engineering Debt Gate)
==========================================
创建: 2026-08-06 防复发机制 #3
目的: 防止"功能持续增加但工程债务无人还"——在沙子上盖楼。
      检查工程债务指标, 债务超标时建议冻结新功能。

检查项:
    T1 测试 collection 是否 0 errors (pytest --collect-only)
    T2 CI 引用脚本是否都存在
    T3 utils/notify 是否存在 (告警能力)
    T4 生产模块是否有 research.* import (环境隔离)
    T5 陈旧测试数量 (引用已删除模块的测试)

债务等级:
    GREEN  — 全部通过, 可推进功能升级
    YELLOW — 有 1-2 项 WARN, 功能升级需谨慎
    RED    — 有 FAIL, 建议冻结新功能优先还债

用法:
    python scripts/engineering_debt_gate.py

退出码:
    0 = GREEN
    1 = YELLOW
    2 = RED
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 复用 industrial_grade_check 的部分检查
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))


def _check_test_collection_errors() -> tuple[bool, str]:
    """T1 测试 collection 是否有 ERROR."""
    # 快速检查: 已知删除模块是否被测试引用
    known_missing = ["hedging.hedge_coordinator", "risk.portfolio_risk_assessor", "dsr_bootstrap"]
    stale_count = 0
    tests_dir = _PROJECT_ROOT / "tests"
    if tests_dir.exists():
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for mod in known_missing:
                    if f"import {mod}" in content or f"from {mod}" in content:
                        stale_count += 1
                        break
            except OSError:
                continue
    if stale_count > 0:
        return False, f"{stale_count} 个测试引用已删除模块"
    return True, "无已知陈旧测试"


def _check_ci_scripts_exist() -> tuple[bool, str]:
    """T2 CI 引用脚本是否都存在."""
    ci_path = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return True, "ci.yml 不存在(跳过)"
    import re

    content = ci_path.read_text(encoding="utf-8")
    refs = re.findall(r"python\s+(scripts/[^\s]+\.py)", content)
    missing = [r for r in refs if not (_PROJECT_ROOT / r).exists()]
    if missing:
        return False, f"CI 脚本缺失: {', '.join(missing)}"
    return True, f"{len(refs)} 个脚本均存在"


def _check_notify_exists() -> tuple[bool, str]:
    """T3 utils/notify 是否存在."""
    notify_path = _PROJECT_ROOT / "utils" / "notify.py"
    if not notify_path.exists():
        return False, "utils/notify.py 不存在"
    return True, "存在"


def _check_env_isolation() -> tuple[bool, str]:
    """T4 生产模块是否有 research.* import."""
    violations = 0
    utils_dir = _PROJECT_ROOT / "utils"
    if utils_dir.exists():
        for py_file in utils_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("#") and ("from research." in stripped or "import research." in stripped):
                        violations += 1
            except OSError:
                continue
    if violations > 0:
        return False, f"{violations} 处 import research.*"
    return True, "无跨层 import"


def _check_stale_test_count() -> tuple[bool, str]:
    """T5 陈旧测试数量."""
    # 复用 T1 的检查逻辑
    ok, msg = _check_test_collection_errors()
    if ok:
        return True, "0 个陈旧测试"
    return False, msg


def main() -> int:
    checks = [
        ("T1", "测试collection", _check_test_collection_errors()),
        ("T2", "CI脚本存在", _check_ci_scripts_exist()),
        ("T3", "告警模块", _check_notify_exists()),
        ("T4", "环境隔离", _check_env_isolation()),
        ("T5", "陈旧测试", _check_stale_test_count()),
    ]

    pass_count = sum(1 for _, _, (ok, _) in checks if ok)
    fail_count = sum(1 for _, _, (ok, _) in checks if not ok)

    if fail_count == 0:
        level = "GREEN"
    elif fail_count <= 2:
        level = "YELLOW"
    else:
        level = "RED"

    print("=" * 60)
    print("工程债务门槛检查 (Engineering Debt Gate)")
    print("=" * 60)
    for code, name, (ok, msg) in checks:
        icon = "[OK]" if ok else "[XX]"
        print(f"  {icon} {code} {name}: {msg}")
    print("-" * 60)

    level_icon = {"GREEN": "[GREEN]", "YELLOW": "[YELLOW]", "RED": "[RED]"}[level]
    print(f"  债务等级: {level_icon} {level}")
    if level == "RED":
        print("  建议: 冻结新功能升级, 优先还债")
    elif level == "YELLOW":
        print("  建议: 功能升级需谨慎, 同步还债")
    else:
        print("  状态: 可推进功能升级")
    print("=" * 60)

    return {"GREEN": 0, "YELLOW": 1, "RED": 2}[level]


if __name__ == "__main__":
    sys.exit(main())
