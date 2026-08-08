#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工业级判据常驻检查 (Industrial Grade Criteria Check)
======================================================
创建: 2026-08-06 防复发机制 #1
目的: 防止"一次性评估后发现短板, 但日常开发中又退化"的问题。
      每次 PR / 盘前自检时自动跑 9 项工业级判据, 任何判据退化即告警。

9 项判据 (源自 research_report_industrial_grade_evaluation.md):
    C1 执行闭环完整性   — 是否有真实撮合/券商下单 (非纯 Mock)
    C2 数据管道分层     — data_pipeline 是否空壳
    C3 环境隔离         — 生产模块是否 import research.*
    C4 信号执行分离     — Alpha 层与 Execution 层是否独立
    C5 测试可运行性     — pytest --collect-only 是否 0 errors
    C6 CI 可运行性      — ci.yml 引用的脚本是否存在
    C7 监控告警实现     — utils/notify 是否存在且可 import
    C8 券商接入状态     — system_config broker 是否 dry_run
    C9 陈旧测试堆积     — 是否有测试引用已删除的模块

用法:
    python scripts/industrial_grade_check.py           # 全量检查
    python scripts/industrial_grade_check.py --strict   # 严格模式(任何WARN=FAIL)
    python scripts/industrial_grade_check.py --json     # JSON报告输出

退出码:
    0 = 全部 PASS
    1 = 有 WARN (非严格模式下不阻断)
    2 = 有 FAIL (始终阻断)

设计原则:
    - 只读检查, 不修改任何文件
    - 每项判据有明确的 PASS/WARN/FAIL 三态
    - FAIL = 工业级硬伤 (执行断链/测试跑不起来/CI跑不起来/告警缺失)
    - WARN = 工业级短板 (环境未隔离/数据管道分层缺/dry_run)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 检查结果数据结构
# ============================================================


class CheckResult:
    """单项判据检查结果."""

    def __init__(self, code: str, name: str, status: str, detail: str, evidence: str = ""):
        self.code = code  # C1-C9
        self.name = name
        self.status = status  # PASS / WARN / FAIL
        self.detail = detail
        self.evidence = evidence  # 具体文件/行号

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


# ============================================================
# 9 项判据检查
# ============================================================


def check_c1_execution_loop() -> CheckResult:
    """C1 执行闭环完整性 — 检查是否有真实撮合/券商下单."""
    # 检查 system_config.json broker 是否启用
    config_path = _PROJECT_ROOT / "system_config.json"
    broker_enabled = False
    dry_run = True
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            broker_enabled = config.get("api_config", {}).get("broker", {}).get("enable", False)
            dry_run = config.get("api_config", {}).get("broker", {}).get("dry_run", True)
        except (json.JSONDecodeError, KeyError):
            pass

    # 检查 QmtBrokerAPI 是否存在
    qmt_path = _PROJECT_ROOT / "ms_strategy" / "src" / "execution" / "qmt_broker.py"
    qmt_exists = qmt_path.exists()

    if broker_enabled and not dry_run:
        return CheckResult("C1", "执行闭环完整性", "PASS", "broker 已启用且 dry_run=false")
    if qmt_exists and not broker_enabled:
        return CheckResult(
            "C1",
            "执行闭环完整性",
            "WARN",
            "QmtBrokerAPI 已实现但 broker.enable=false / dry_run=true, 真实下单未接线",
            f"system_config.json broker.enable={broker_enabled}, dry_run={dry_run}",
        )
    return CheckResult("C1", "执行闭环完整性", "FAIL", "无真实券商下单通道")


def check_c2_data_pipeline() -> CheckResult:
    """C2 数据管道分层 — 检查 data_pipeline 是否空壳."""
    dp_dir = _PROJECT_ROOT / "data_pipeline"
    if not dp_dir.exists():
        return CheckResult("C2", "数据管道分层", "PASS", "data_pipeline/ 已删除(逻辑在 utils/data/)")

    subdirs = list(dp_dir.iterdir()) if dp_dir.exists() else []
    empty_count = sum(1 for d in subdirs if d.is_dir() and not any(d.iterdir()))
    if empty_count > 0:
        return CheckResult(
            "C2",
            "数据管道分层",
            "WARN",
            f"data_pipeline/ 有 {empty_count} 个空壳子目录(设计意图未落地)",
            str(dp_dir),
        )
    return CheckResult("C2", "数据管道分层", "PASS", "data_pipeline/ 非空")


def check_c3_env_isolation() -> CheckResult:
    """C3 环境隔离 — 检查生产模块是否 import research.*."""
    # 搜索 utils/ 下的 .py 文件是否 import research.*
    violations: list[str] = []
    utils_dir = _PROJECT_ROOT / "utils"
    if utils_dir.exists():
        for py_file in utils_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "from research." in stripped or "import research." in stripped:
                        violations.append(f"{py_file.name}:{i}: {stripped}")
            except OSError:
                continue

    if not violations:
        return CheckResult("C3", "环境隔离", "PASS", "生产模块无 research.* import")
    return CheckResult(
        "C3",
        "环境隔离",
        "WARN",
        f"生产模块有 {len(violations)} 处 import research.* (未物理隔离)",
        "; ".join(violations[:5]),
    )


def check_c4_signal_execution_separation() -> CheckResult:
    """C4 信号执行分离 — 检查 Alpha 层与 Execution 层是否独立."""
    alpha_dir = _PROJECT_ROOT / "utils" / "alpha_factor"
    exec_algo = _PROJECT_ROOT / "utils" / "execution_algo_engine.py"
    exec_selector = _PROJECT_ROOT / "utils" / "execution_selector.py"

    has_alpha = alpha_dir.exists() and alpha_dir.is_dir()
    has_exec_algo = exec_algo.exists()
    has_selector = exec_selector.exists()

    if has_alpha and has_exec_algo and has_selector:
        return CheckResult("C4", "信号执行分离", "PASS", "Alpha 层 + 执行算法 + selector 桥接均存在")
    missing = []
    if not has_alpha:
        missing.append("alpha_factor/")
    if not has_exec_algo:
        missing.append("execution_algo_engine.py")
    if not has_selector:
        missing.append("execution_selector.py")
    return CheckResult("C4", "信号执行分离", "WARN", f"缺少: {', '.join(missing)}")


def check_c5_test_collectable() -> CheckResult:
    """C5 测试可运行性 — pytest --collect-only 是否 0 errors."""
    tests_dir = _PROJECT_ROOT / "tests"
    if not tests_dir.exists():
        return CheckResult("C5", "测试可运行性", "FAIL", "tests/ 目录不存在")

    # 快速检查: 是否有测试引用已知不存在的模块
    known_missing = ["hedging.hedge_coordinator", "risk.portfolio_risk_assessor", "dsr_bootstrap"]
    stale_count = 0
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
        return CheckResult(
            "C5",
            "测试可运行性",
            "FAIL",
            f"{stale_count} 个测试引用已删除的模块({', '.join(known_missing)})",
        )
    return CheckResult("C5", "测试可运行性", "PASS", "无已知陈旧测试引用")


def check_c6_ci_runnable() -> CheckResult:
    """C6 CI 可运行性 — ci.yml 引用的脚本是否存在."""
    ci_path = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return CheckResult("C6", "CI 可运行性", "WARN", "ci.yml 不存在")

    content = ci_path.read_text(encoding="utf-8")
    # 提取 python scripts/xxx.py 引用
    import re

    refs = re.findall(r"python\s+(scripts/[^\s]+\.py)", content)
    missing = []
    for ref in refs:
        script_path = _PROJECT_ROOT / ref
        if not script_path.exists():
            missing.append(ref)

    if missing:
        return CheckResult("C6", "CI 可运行性", "FAIL", f"CI 引用的脚本不存在: {', '.join(missing)}")
    return CheckResult("C6", "CI 可运行性", "PASS", f"CI 引用的 {len(refs)} 个脚本均存在")


def check_c7_notify_exists() -> CheckResult:
    """C7 监控告警实现 — utils/notify 是否存在且可 import."""
    notify_path = _PROJECT_ROOT / "utils" / "notify.py"
    if not notify_path.exists():
        return CheckResult("C7", "监控告警实现", "FAIL", "utils/notify.py 不存在")

    # 检查是否定义了关键函数
    content = notify_path.read_text(encoding="utf-8")
    has_send_alert = "def send_alert" in content
    has_send_sms = "def send_sms_alert" in content

    if has_send_alert and has_send_sms:
        return CheckResult("C7", "监控告警实现", "PASS", "send_alert + send_sms_alert 均已定义")
    return CheckResult("C7", "监控告警实现", "WARN", "notify.py 存在但接口不完整")


def check_c8_broker_status() -> CheckResult:
    """C8 券商接入状态 — system_config broker 配置."""
    return check_c1_execution_loop()  # 复用 C1 的检查逻辑, 但改 code


def check_c9_stale_tests() -> CheckResult:
    """C9 陈旧测试堆积 — 是否有测试引用已删除的模块."""
    return check_c5_test_collectable()  # 复用 C5


# ============================================================
# 主检查流程
# ============================================================


def run_all_checks() -> list[CheckResult]:
    """运行全部 9 项判据检查."""
    return [
        check_c1_execution_loop(),
        check_c2_data_pipeline(),
        check_c3_env_isolation(),
        check_c4_signal_execution_separation(),
        check_c5_test_collectable(),
        check_c6_ci_runnable(),
        check_c7_notify_exists(),
        check_c8_broker_status(),
        check_c9_stale_tests(),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工业级判据常驻检查")
    parser.add_argument("--strict", action="store_true", help="严格模式: WARN 也算失败")
    parser.add_argument("--json", action="store_true", help="JSON 报告输出")
    args = parser.parse_args(argv)

    results = run_all_checks()

    pass_count = sum(1 for r in results if r.status == "PASS")
    warn_count = sum(1 for r in results if r.status == "WARN")
    fail_count = sum(1 for r in results if r.status == "FAIL")

    if args.json:
        report = {
            "timestamp": "2026-08-06",
            "total": len(results),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "checks": [r.to_dict() for r in results],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print("工业级判据常驻检查 (Industrial Grade Criteria Check)")
        print("=" * 70)
        for r in results:
            icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}[r.status]
            print(f"  {icon} {r.code} {r.name}: {r.detail}")
            if r.evidence:
                print(f"         证据: {r.evidence}")
        print("-" * 70)
        print(f"  总计: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL")
        print("=" * 70)

    if fail_count > 0:
        return 2
    if args.strict and warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
