#!/usr/bin/env python
"""
_smoke_runner.py — GAP-1 烟雾测试运行器 (真实实现, 非占位)

R1 修复项。CI "GAP-1 Smoke Test" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    "烟雾测试" 是工业级系统的第一道可执行护栏 — 它在 CI 早期快速验证
    "核心模块能被导入且关键契约符号存在", 而不必跑完整 pytest 套件。

实现:
    1. 导入契约自检: 验证 docs 所述关键模块/符号可被成功 import
       (如 utils.notify.send_alert, automated_execution_system 的
        AutomatedExecutionSystem, hedge_order_executor 的 OptionsSimBroker 等)。
    2. tests/smoke 收集: 若存在 tests/smoke 目录, 用 pytest 收集并运行
       该目录下的烟雾测试; 若目录不存在, 退化为仅做导入自检 (不阻断)。
    3. 关键数据契约文件存在性: 验证 positions.json / system_config.json
       等运行时必需文件就位。

退出码:
    0 = 烟雾自检定通过 (导入齐全 + 契约文件齐全 + 可选 pytest 通过)
    1 = 关键导入或契约失败 (阻断合并)

用法:
    python scripts/_smoke_runner.py [--smoke-dir tests/smoke] [--skip-pytest]
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports" / "ci"


class Check(NamedTuple):
    cid: str
    desc: str
    passed: bool
    detail: str


# 关键导入契约: (模块, [必需符号...])
# 注意: 根目录脚本(hedge_order_executor/rebalance_order_executor 等)多为
# CLI 入口, 不导出类, 因此只需验证模块可 import 即可 (symbols 为空列表)。
IMPORT_CONTRACTS = [
    ("utils.notify", ["send_alert", "send_sms_alert", "send_async_alert"]),
    ("utils.execution.fills_store", ["FillsStore"]),
    ("utils.execution.fills_pnl_bridge", ["augment_market_prices"]),
    ("automated_execution_system", ["AutomatedExecutionSystem"]),
    ("hedge_order_executor", []),
    ("rebalance_order_executor", []),
    ("build_plan_executor", []),
    ("daily_build_and_hedge", []),
    ("scripts.industrial_grade_check", ["main", "run_all_checks"]),
    ("scripts.pre_commit_check", ["main"]),
]

# 运行时必需契约文件
CONTRACT_FILES = [
    "config/positions.json",
    "config/system_config.json",
]


def check_imports() -> list[Check]:
    out: list[Check] = []
    # 确保 ROOT 在 sys.path 最前 (与 pytest 全量收集修复一致: 条件追加)
    if ROOT not in sys.path:
        sys.path.insert(0, str(ROOT))
    for mod, symbols in IMPORT_CONTRACTS:
        cid = "IMP-" + mod.split(".")[-1]
        try:
            m = importlib.import_module(mod)
            missing = [s for s in symbols if not hasattr(m, s)]
            if missing:
                out.append(
                    Check(
                        cid,
                        f"import {mod} (missing {missing})",
                        False,
                        f"missing symbols: {missing}",
                    )
                )
            else:
                out.append(Check(cid, f"import {mod}", True, "OK"))
        except (ImportError, AttributeError) as e:
            out.append(Check(cid, f"import {mod}", False, f"{type(e).__name__}: {e}"))
    return out


def check_contract_files() -> list[Check]:
    out: list[Check] = []
    for rel in CONTRACT_FILES:
        p = ROOT / rel
        ok = p.exists() and p.stat().st_size > 0
        out.append(
            Check(
                "FILE-" + rel.replace("/", "_").replace(".", "_"),
                f"contract file {rel} exists & non-empty",
                ok,
                "OK" if ok else f"missing/empty: {rel}",
            )
        )
    return out


def run_pytest_smoke(smoke_dir: Path) -> list[Check]:
    out: list[Check] = []
    if not smoke_dir.exists():
        out.append(
            Check(
                "PT-absent",
                "tests/smoke directory present",
                True,
                "no smoke dir, skipped (non-blocking)",
            )
        )
        return out
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(smoke_dir), "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
    )
    passed = proc.returncode == 0
    out.append(
        Check(
            "PT-smoke",
            f"pytest {smoke_dir} pass",
            passed,
            "exit=0" if passed else (proc.stdout[-500:] + proc.stderr[-500:]),
        )
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GAP-1 smoke test runner")
    parser.add_argument("--smoke-dir", default=str(ROOT / "tests" / "smoke"))
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args(argv)

    REPORTS.mkdir(parents=True, exist_ok=True)
    checks: list[Check] = []
    checks += check_imports()
    checks += check_contract_files()
    if not args.skip_pytest:
        checks += run_pytest_smoke(Path(args.smoke_dir))

    n_fail = sum(1 for c in checks if not c.passed)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": ts,
        "total": len(checks),
        "fail": n_fail,
        "checks": [c._asdict() for c in checks],
    }
    out_path = REPORTS / f"smoke_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(f"[SMOKE] total={len(checks)} fail={n_fail} report={out_path}")
    for c in checks:
        if not c.passed:
            print(f"  [FAIL] {c.cid}: {c.desc} -> {c.detail}")
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
