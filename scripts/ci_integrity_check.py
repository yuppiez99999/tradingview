#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ci_integrity_check.py — CI 完整性自检 (R1 / C6 机检落点)

工业级差距分析 (R1) 的核心修复: CI 引用了若干 scripts/*.py, 任何缺失或不可
运行都会使 CI 必然失败 (C6 FAIL)。本脚本提供 *可独立执行* 的完整性校验:

    1. 解析 .github/workflows/ci.yml 中所有 `python scripts/xxx.py` 引用
    2. 验证每个脚本存在
    3. 可选: 对每个脚本做 `python scripts/xxx.py --help` 烟测 (确认可导入/可运行)

本脚本同时被 industrial_grade_check.check_c6_ci_runnable 的语义所覆盖 (存在性),
但它进一步提供 "可运行性" 维度的实证, 输出结构化 JSON 供质量门禁消费。

退出码:
    0 = 所有 CI 引用脚本存在 (且 --strict 时均可运行)
    1 = 存在缺失或不可运行的脚本

用法:
    python scripts/ci_integrity_check.py [--strict] [--output reports/ci/ci_integrity.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PYTHON = os.environ.get("PYTHON_EXECUTABLE") or (
    str(ROOT / ".venv" / "Scripts" / "python.exe")
    if (ROOT / ".venv" / "Scripts" / "python.exe").exists() else "python"
)


class ScriptRef(NamedTuple):
    ref: str
    exists: bool
    runnable: Optional[bool]
    detail: str


def extract_refs() -> List[str]:
    if not CI_YML.exists():
        return []
    content = CI_YML.read_text(encoding="utf-8", errors="replace")
    refs = re.findall(r"python\s+(scripts/[^\s\"']+\.py)", content)
    # 去重保序
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def smoke(ref: str) -> (bool, str):
    p = ROOT / ref
    if not p.exists():
        return False, "file missing"
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            [PYTHON, str(p), "--help"], cwd=str(ROOT), capture_output=True,
            text=True, encoding="utf-8", errors="replace", env=env, timeout=60,
        )
        # 能返回 (无论 --help 是否被识别) 且不抛异常即视为可运行
        if proc.returncode in (0, 2):
            return True, f"rc={proc.returncode}"
        return False, f"rc={proc.returncode}: {proc.stderr[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CI integrity check")
    parser.add_argument("--strict", action="store_true",
                        help="额外对每个脚本做 --help 烟测 (可运行性)")
    parser.add_argument("--output", default=str(ROOT / "reports" / "ci" / "ci_integrity.json"))
    args = parser.parse_args(argv)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    refs = extract_refs()
    results: List[ScriptRef] = []
    for ref in refs:
        p = ROOT / ref
        exists = p.exists()
        runnable = None
        detail = "exists" if exists else "missing"
        if exists and args.strict:
            runnable, detail = smoke(ref)
        results.append(ScriptRef(ref, exists, runnable, detail))

    n_missing = sum(1 for r in results if not r.exists)
    n_unrunnable = sum(1 for r in results if r.runnable is False)
    n_total = len(results)
    passed = n_missing == 0 and (not args.strict or n_unrunnable == 0)

    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "ci_yaml": str(CI_YML),
        "total_refs": n_total,
        "missing": n_missing,
        "unrunnable_strict": n_unrunnable,
        "passed": passed,
        "refs": [r._asdict() for r in results],
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"[CI-INTEGRITY] refs={n_total} missing={n_missing} "
          f"unrunnable={n_unrunnable} passed={passed}")
    print(f"[CI-INTEGRITY] report -> {out_path}")
    for r in results:
        if not r.exists:
            print(f"  [MISSING] {r.ref}")
        elif r.runnable is False:
            print(f"  [UNRUNNABLE] {r.ref}: {r.detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
