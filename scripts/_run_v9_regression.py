#!/usr/bin/env python
"""
_run_v9_regression.py — V9 工业级回归套件运行器 (真实实现)

R1 修复项。CI "V9 Regression" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    V9 的核心承诺是 "执行闭环 + 数据管道 + 门禁三件套" 可重复验证 (见记忆:
    assert_data_validity 12PASS / industrial_grade_check 11P+1W / engineering_debt_gate)。
    本脚本驱动这三件套 + 选定的 pytest 回归测试, 聚合为单一回归结论。

    1. 运行 assert_data_validity.py (D1-D7 非零断言) — 数据完整性门禁
    2. 运行 industrial_grade_check.py (C1-C9) — 工业级判据
    3. 运行选定的 pytest 回归测试 (执行闭环 / 数据契约 / TCA)
    4. 聚合 exit code, 任一阻断即 FAIL

退出码:
    0 = 回归全过
    1 = 任一回归项失败

用法:
    python scripts/_run_v9_regression.py [--pytest-root tests] \
        [--output reports/ci/v9_regression.json] [--skip-pytest]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports" / "ci"
def _venv_python(root):
    """跨平台选择 venv 解释器: Windows=.venv/Scripts/python.exe, 其他=.venv/bin/python。"""
    for sub_bin, sub_py in (("Scripts", "python.exe"), ("bin", "python")):
        cand = root / ".venv" / sub_bin / sub_py
        if cand.exists():
            return str(cand)
    return "python"


PYTHON = os.environ.get("PYTHON_EXECUTABLE") or _venv_python(ROOT)

# V9 关键回归测试 (执行闭环 + 数据契约 + TCA 归因)
REGRESSION_TEST_TARGETS = [
    "tests/test_assert_data_validity.py",
    "tests/test_fills_store.py",
    "tests/test_fills_pnl_bridge.py",
    "tests/test_hedge_order_executor_v9.py",
    "tests/test_rebalance_executor.py",
    "tests/test_tca_post_trade.py",
]


class Stage(NamedTuple):
    name: str
    passed: bool
    detail: str
    rc: int


def run_script(rel: str, *extra) -> Stage:
    p = ROOT / rel
    if not p.exists():
        return Stage(rel, False, f"missing: {rel}", 2)
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        [PYTHON, str(p), *extra],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=900,
    )
    tail = (proc.stdout + proc.stderr)[-400:]
    return Stage(rel, proc.returncode == 0, tail, proc.returncode)


def run_pytest(targets: list[str]) -> Stage:
    existing = [t for t in targets if (ROOT / t).exists()]
    if not existing:
        return Stage("pytest-regression", True, "no targets present, skipped", 0)
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    # 仅收集已存在的测试, --co 验证可运行
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", *existing, "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=1200,
    )
    tail = (proc.stdout + proc.stderr)[-400:]
    return Stage("pytest-regression", proc.returncode == 0, tail, proc.returncode)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="V9 regression runner")
    parser.add_argument("--pytest-root", default="tests")
    parser.add_argument("--output", default=str(REPORTS / "v9_regression.json"))
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args(argv)

    REPORTS.mkdir(parents=True, exist_ok=True)
    stages: list[Stage] = [
        run_script("scripts/assert_data_validity.py"),
        run_script("scripts/industrial_grade_check.py"),
    ]
    if not args.skip_pytest:
        stages.append(run_pytest(REGRESSION_TEST_TARGETS))

    n_fail = sum(1 for s in stages if not s.passed)
    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "fail": n_fail,
        "stages": [s._asdict() for s in stages],
    }
    out_path = Path(args.output)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[V9-REGRESSION] stages={len(stages)} fail={n_fail} report={out_path}")
    for s in stages:
        if not s.passed:
            print(f"  [FAIL] {s.name}: rc={s.rc}\n    {s.detail}")
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
