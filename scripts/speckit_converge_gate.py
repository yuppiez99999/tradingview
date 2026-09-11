#!/usr/bin/env python
"""speckit converge 门禁薄包装（spec-kit 集成 §5, 2026-09-11）.

spec-kit 命令（/speckit.implement 与 /speckit.converge）在本仓的"完成"唯一定义::

    .venv/Scripts/python.exe scripts/speckit_converge_gate.py \
        --files <本次改动的 .py ...> \
        --pytest-args "<tasks.md 声明的测试范围>"

四门禁按序执行（fail-closed, 任一失败 exit 1, 后续门禁不再跑）:

    G1  scripts/ruff_incremental_gate.py <files>   ruff 增量零新增（无 .py 改动时按其自身语义跳过）
    G2  pytest <pytest-args>                       tasks.md 声明的测试范围全绿
    G3  scripts/industrial_grade_check.py           工业级判据（WARN 可接受, 同 pre-commit 口径, 不加 --strict）
    G4  scripts/assert_data_validity.py            数据非零断言（缺产物 = FAIL, 空产物 = FAIL）

铁律:
    * --pytest-args 缺失 = 拒跑（未声明测试范围不得收敛 —— 防"缺数据/空集合 = 通过"假 PASS）;
    * 门禁输出不吞: 失败时打印该门禁尾部输出, 须摘录进 specs/<feature>/implementation-notes.md;
    * 本脚本只读不写（除 stdout）, 不触碰生产目录。

设计文档: cairn/spec-kit-sdd-integration-20260911.md §5
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATE_TIMEOUT_SECONDS = 1800
_FAIL_TAIL_LINES = 30


class GateSpec(NamedTuple):
    name: str
    cmd: List[str]


def build_gates(files: List[str], pytest_args: str) -> List[GateSpec]:
    """按 G1→G4 顺序构造四门禁命令。pytest_args 用 shlex 切分以支持带引号的 -k 表达式。"""
    py_files = [f for f in files if f.endswith(".py")]
    return [
        GateSpec(
            "G1-ruff-incremental",
            [sys.executable, str(PROJECT_ROOT / "scripts" / "ruff_incremental_gate.py"), *py_files],
        ),
        GateSpec("G2-pytest", [sys.executable, "-m", "pytest", *shlex.split(pytest_args)]),
        GateSpec(
            "G3-industrial-grade",
            [sys.executable, str(PROJECT_ROOT / "scripts" / "industrial_grade_check.py")],
        ),
        GateSpec(
            "G4-data-validity",
            [sys.executable, str(PROJECT_ROOT / "scripts" / "assert_data_validity.py")],
        ),
    ]


def _run_gate(gate: GateSpec) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        gate.cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GATE_TIMEOUT_SECONDS,
        env=env,
    )


def _tail(text: str, n: int = _FAIL_TAIL_LINES) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:]) if lines else "(no output)"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="spec-kit 四门禁收敛检查 (fail-closed)")
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="本次改动的 .py 文件（相对仓库根）; 空 = G1 按其自身语义跳过",
    )
    parser.add_argument(
        "--pytest-args",
        required=True,
        help="tasks.md 声明的测试范围, 如 'tests/unit/test_x.py -q'（必填, 未声明不得收敛）",
    )
    args = parser.parse_args(argv)

    gates = build_gates(args.files, args.pytest_args)
    for gate in gates:
        shown_cmd = " ".join(shlex.quote(c) for c in gate.cmd[1:])
        print(f"[speckit-gate] RUN {gate.name}: {shown_cmd}")
        try:
            proc = _run_gate(gate)
        except subprocess.TimeoutExpired:
            print(
                f"[speckit-gate] FAIL {gate.name}: 超时 (> {GATE_TIMEOUT_SECONDS}s) "
                "—— 收敛不成立, 摘录本行进 implementation-notes.md"
            )
            return 1
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            print(f"[speckit-gate] FAIL {gate.name} (exit {proc.returncode}) —— 尾部输出:")
            print(_tail(out))
            print(
                "[speckit-gate] 收敛不成立: 把上方输出摘录进 "
                "specs/<feature>/implementation-notes.md, 修复后重跑"
            )
            return 1
        print(f"[speckit-gate] PASS {gate.name} — {_tail(out, 1)}")

    print("[speckit-gate] ALL PASS —— converge 收敛成立 (exit 0)")
    return 0


if __name__ == "__main__":
    try:  # GBK 控制台兜底: 中文输出不被 UnicodeEncodeError 杀死
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    raise SystemExit(main(sys.argv[1:]))
