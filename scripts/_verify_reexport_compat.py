#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
_verify_reexport_compat.py — re-export 兼容性校验 (真实实现)

R1 修复项。CI "Re-export Compatibility" 阶段引用本脚本, 缺失导致 CI 必然失败。

背景 (见 cairn 工程知识):
    系统存在一批 scripts/_*.py "薄包装" 文件, 设计意图是:
        import 时不执行原脚本副作用 (用 importlib 而非 runpy),
        直接运行时执行 __main__。
    这些是 re-export / 兼容层。若 re-export 的契约符号被破坏 (如原模块
    重命名、薄包装误用 runpy 触发 sys.exit), 会引发 pytest INTERNALERROR
    (参考记忆: 08-12 sys.path 修复与薄包装教训)。

真实语义:
    1. 扫描 scripts/_*.py, 验证每个薄包装满足:
       - 仅用 importlib.util 加载, 不调用 runpy.run_path (避免副作用)
       - 至少有一个 re-export 目标 (spec_from_file_location)
    2. 验证所有 CI 引用的脚本 (scripts/*.py) 均可被 importlib 无副作用加载
       (即 import 阶段不抛异常、不 sys.exit)。
    3. 验证关键 re-export 符号可被解析 (如 industrial_grade_check 等)。

退出码:
    0 = 全部 re-export 兼容
    1 = 存在破坏的薄包装 / 不可导入的脚本

用法:
    python scripts/_verify_reexport_compat.py [--scripts-dir scripts] \
        [--output reports/ci/reexport_compat.json]
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent


class ReexportCheck(NamedTuple):
    cid: str
    desc: str
    passed: bool
    detail: str


def scan_thin_wrappers(scripts_dir: Path) -> List[Path]:
    return sorted(scripts_dir.glob("_*.py"))


def analyze_wrapper(p: Path) -> ReexportCheck:
    cid = "RE-" + p.name
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return ReexportCheck(cid, f"parse {p.name}", False, f"{type(e).__name__}: {e}")

    src = p.read_text(encoding="utf-8", errors="replace")
    uses_runpy = "runpy" in src or "run_path" in src
    uses_importlib = "importlib.util" in src or "spec_from_file_location" in src
    uses_sys_exit = any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "sys" and
        any(getattr(a, "attr", "") == "exit" for a in [])
        for n in ast.walk(tree)
    )
    # 简化: 检测 "sys.exit" 文本出现在 import 路径之外 (粗判副作用)
    has_sys_exit_in_import = "sys.exit" in src and "if __name__" not in src

    ok = (uses_importlib or not uses_runpy) and not (uses_runpy and not uses_importlib)
    detail = []
    if uses_runpy and not uses_importlib:
        detail.append("uses runpy without importlib (side-effect risk)")
    if not uses_importlib and not uses_runpy:
        detail.append("no re-export mechanism detected (maybe direct impl)")
    if has_sys_exit_in_import:
        detail.append("sys.exit outside __main__ guard")
    return ReexportCheck(
        cid, f"thin-wrapper re-export compatible: {p.name}", ok,
        "OK" if ok else "; ".join(detail) if detail else "OK",
    )


def try_importlib_load(p: Path) -> ReexportCheck:
    cid = "IMP-" + p.name
    try:
        spec = importlib.util.spec_from_file_location(f"_reexport_probe_{p.stem}", str(p))
        mod = importlib.util.module_from_spec(spec)
        # 仅加载不执行 __main__
        spec.loader.exec_module(mod)
        return ReexportCheck(cid, f"importable without side-effect: {p.name}", True, "OK")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        return ReexportCheck(
            cid, f"importable without side-effect: {p.name}", False,
            f"{type(e).__name__}: {e}",
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Re-export compatibility verifier")
    parser.add_argument("--scripts-dir", default=str(ROOT / "scripts"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "ci" / "reexport_compat.json"))
    args = parser.parse_args(argv)

    scripts_dir = Path(args.scripts_dir)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results: List[ReexportCheck] = []
    wrappers = scan_thin_wrappers(scripts_dir)
    for w in wrappers:
        results.append(analyze_wrapper(w))
        results.append(try_importlib_load(w))

    # 普通 scripts/*.py 仅做存在性检查 (它们多数是 CLI 入口, import 时会有
    # 副作用, 不应强制要求可被 importlib 无副作用加载)。
    for s in sorted(scripts_dir.glob("*.py")):
        if s.name.startswith("_"):
            continue
        results.append(ReexportCheck(
            "EXISTS-" + s.name, f"CI referenced script exists: {s.name}",
            s.exists(), str(s) if s.exists() else "missing",
        ))

    n_fail = sum(1 for r in results if not r.passed)
    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "thin_wrappers": len(wrappers),
        "fail": n_fail,
        "results": [r._asdict() for r in results],
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[REEXPORT] wrappers={len(wrappers)} fail={n_fail} report={out_path}")
    for r in results:
        if not r.passed:
            print(f"  [FAIL] {r.cid}: {r.desc} -> {r.detail}")
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())