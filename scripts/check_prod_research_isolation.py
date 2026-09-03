#!/usr/bin/env python
"""生产链路 ↔ research 隔离检测器 (v8.7.1 次级项 P2, 机制级 CI 门禁).

目标: 把「研究 / 生产隔离」(ROADMAP 三线收敛) 从流程级约定固化为机制级检查——
禁止生产 / 执行 / 风控 / 决策链路 import `research.*` 研究实验包。

背景 (2026-09-03 首轮复核):
- 既有判据 T4 (`scripts/engineering_debt_gate.py` _check_env_isolation) 与
  C3 (`scripts/industrial_grade_check.py` check_c3_env_isolation) 仅扫 `utils/`
  目录且用行文本正则, 未覆盖 `ai_decision/` `cli/` `quant_modules/` 根生产入口
  等完整生产边界 → 本脚本以 AST import 精确解析补齐该机制。
- 当前代码库生产边界内 `research.*` import 数为 0 (G5 于 2026-08-11 清零),
  本门禁为防回归的前置防线; 机制先行不触碰任何生产代码与既有门禁三件套。

用法:
    python scripts/check_prod_research_isolation.py [path ...] [--json out.json] [--warn-only] [--selftest]
- 无参: 扫描内置生产边界 (白名单目录 + 根生产入口文件)。
- 传 path: 只扫给定文件 / 目录 (供 CI 增量或本地定向复核)。
- --warn-only: 发现违规仅打印并返回 0 (不阻断, 供本地摸底)。
- 默认: 发现任何违规返回 1 (阻断)。

豁免:
- import 语句所在行行尾加注释 `# allow-research-import` 可显式豁免
  (先例: P0 print 门禁的 `# allow-print`)。仅当确需在边界内引用研究资产
  且已经人工评审时使用; 优先做法是把研究资产迁移到 `utils/factor_research.*`
  等生产兼容路径。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---- 生产边界: 白名单目录 (研究侧 research/ 等天然不在其中) ----
_PROD_DIRS = [
    "ai_decision",
    "cli",
    "core",
    "quant_modules",
    "reporting",
    "tools",
    "utils",
    "ms_strategy/src",
    "15_每日工作流",
]

# ---- 生产边界: 根目录执行 / 调度 / 风控 / 决策入口文件 ----
_PROD_ROOT_FILES = [
    "量化策略系统_统一入口_v8.6.py",
    "automated_execution_system.py",
    "institutional_pipeline_runner.py",
    "daily_trade_executor.py",
    "daily_trading_workflow.py",
    "daily_build_and_hedge.py",
    "daily_hedge_update.py",
    "build_plan_executor.py",
    "hedge_order_executor.py",
    "rebalance_order_executor.py",
    "hedge_execution_orders.py",
    "rebalance_execution_orders.py",
    "run_daily_eod.py",
    "run_eod_evolution_rebalance.py",
    "shadow_account_system.py",
    "launch_shadow_account.py",
    "live_scheduler.py",
    "signal_monitor.py",
    "signal_post_processing.py",
    "stop_loss_monitor.py",
    "today_hedge_decision.py",
    "alpha_hedge_engine.py",
]

_EXEMPT_MARK = "# allow-research-import"


def _is_research_ref(module: Optional[str], name: Optional[str] = None) -> bool:
    """判断 import 是否指向顶层 research 包 (research / research.xxx)."""
    for ref in (module, name):
        if not ref:
            continue
        if ref == "research" or ref.startswith("research."):
            return True
    return False


def _line_is_exempt(lines: list[str], lineno: int) -> bool:
    """该 import 行是否带显式豁免注释."""
    if not lines or lineno < 1 or lineno > len(lines):
        return False
    return _EXEMPT_MARK in lines[lineno - 1]


def scan_file(path: Path) -> list[dict]:
    """返回该文件的 research.* import 违规列表 [{file,line,stmt}]."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, OSError):
        return []
    lines = src.splitlines()

    violations: list[dict] = []
    for node in ast.walk(tree):
        hit: Optional[str] = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_research_ref(alias.name):
                    hit = f"import {alias.name}"
                    break
        elif isinstance(node, ast.ImportFrom):
            mod = node.module
            if _is_research_ref(mod):
                # from research import X / from research.xxx import Y
                hit = "from {} import {}".format(mod or "", ", ".join(a.name for a in node.names))
        if hit:
            if not _line_is_exempt(lines, getattr(node, "lineno", 0)):
                violations.append(
                    {
                        "file": str(path),
                        "line": getattr(node, "lineno", 0),
                        "stmt": hit,
                    }
                )
    return violations


def scan_paths(paths: list[Path]) -> list[dict]:
    all_v: list[dict] = []
    for p in paths:
        if p.is_file() and p.suffix == ".py":
            all_v.extend(scan_file(p))
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if any(part.startswith("__pycache__") for part in f.parts):
                    continue
                if any(part.startswith(".") for part in f.parts):
                    continue
                all_v.extend(scan_file(f))
    return all_v


def _default_paths() -> list[Path]:
    paths: list[Path] = []
    for d in _PROD_DIRS:
        p = _PROJECT_ROOT / d
        if p.is_dir():
            paths.append(p)
    for f in _PROD_ROOT_FILES:
        p = _PROJECT_ROOT / f
        if p.is_file():
            paths.append(p)
    return paths


# ============================================================
# 内建自测 (--selftest, 无需 pytest)
# ============================================================

_SAMPLE_BAD = (
    "from research.technical import boll\n"
    "def f():\n"
    "    import research.quant as rq\n"
    "    return boll\n"
)
_SAMPLE_EXEMPT = (
    "import research.experiment as exp  # allow-research-import\n"  # noqa: E501
    "def f():\n"
    "    return exp\n"
)
_SAMPLE_GOOD = (
    "from utils.factor_research import run  # 生产兼容路径, 不应告警\n"  # noqa: E501
    "def f():\n"
    "    import json\n"
    "    return run()\n"
)


def _selftest() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad_research.py"
        bad.write_text(_SAMPLE_BAD, encoding="utf-8")
        exm = Path(tmp) / "exempt_research.py"
        exm.write_text(_SAMPLE_EXEMPT, encoding="utf-8")
        good = Path(tmp) / "good_prod.py"
        good.write_text(_SAMPLE_GOOD, encoding="utf-8")

        bad_v = scan_file(bad)
        exm_v = scan_file(exm)
        good_v = scan_file(good)

        ok = True
        if len(bad_v) != 2:
            print(f"[SELFTEST] FAIL: 违规样本应命中 2 处, 实得 {len(bad_v)}: {bad_v}")
            ok = False
        if exm_v:
            print(f"[SELFTEST] FAIL: 豁免注释样本被误报: {exm_v}")
            ok = False
        if good_v:
            print(f"[SELFTEST] FAIL: 合法生产兼容 import 被误报: {good_v}")
            ok = False

    if ok:
        print("[SELFTEST] PASS: research.* 违规可检出 / 豁免注释生效 / 生产兼容路径不误报")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="要扫描的文件/目录 (缺省=内置生产边界)")
    ap.add_argument("--json", metavar="out.json", help="同时输出机器可读 JSON")
    ap.add_argument("--warn-only", action="store_true", help="仅告警不阻断 (exit 0)")
    ap.add_argument("--selftest", action="store_true", help="内建自测")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    paths = [Path(x) for x in args.paths] if args.paths else _default_paths()
    violations = scan_paths(paths)

    if args.json:
        out = {
            "tool": "check_prod_research_isolation",
            "scope": [str(p) for p in paths],
            "violation_count": len(violations),
            "violations": violations,
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if violations:
        print(f"[PROD-ISO] 生产边界发现 {len(violations)} 处 research.* import (违反研究/生产隔离):")
        for v in violations:
            print(f"  - {v['file']}:{v['line']}  {v['stmt']}")
        print(
            "[PROD-ISO] 处置: ①把研究资产迁到 utils/factor_research.* 等生产兼容路径; "
            "②确需引用时加 `# allow-research-import` 豁免注释并留评审记录。"
        )
        if args.warn_only:
            print("[PROD-ISO] warn-only 模式, 返回 0。")
            return 0
        return 1

    print(f"[PROD-ISO] 通过 - 生产边界 {len(paths)} 个路径无 research.* import。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
