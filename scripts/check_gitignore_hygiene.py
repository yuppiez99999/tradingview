#!/usr/bin/env python
"""仓库卫生门禁 —— 「churn 与产物夹带」防复发检查 (2026-09-12 任务4)

判据(两向断言, 任一违反即 RC=1):
  A) MUST_IGNORE  —— 运行时产物/散落物 **必须** 被忽略 (缺一条 = 夹带缺口)
  B) MUST_TRACK   —— 白名单/政策放行的源码 **必须不** 被忽略 (多一条 = 误伤生产源码)
  C) 夹带清单 `git ls-files --others --exclude-standard` 不得含已知散落物
  D) index 内 CRLF blob 必须 = 0 (行尾 churn 由 .gitattributes `* text=auto eol=lf` 保证)

用法:
  python scripts/check_gitignore_hygiene.py                 # 断言当前工作区 (RC=0 通过)
  python scripts/check_gitignore_hygiene.py --against-head  # 临时换用 HEAD 版 .gitignore 再断言,
                                                           # 用于证明「修复前会失败」; 跑完自动还原

坑位(实测):
  * 喂给 git 的路径清单必须是 LF + 无 BOM, 否则行尾 `\\r` 让**文件级**规则失配(目录级仍命中)。
  * 必须带 `-c core.quotepath=false`, 否则含中文的路径被回显成 `"\\344\\270\\211..."` 转义形式,
    与字面量比对失败 → 假缺口。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- 断言集合
# A) 必须被忽略(运行时产物 / 根级散落物 / 缓存)
MUST_IGNORE = [
    # 根级散落产物(修复前实测就在夹带清单里)
    "2026-09-12.md",
    "apply_0912_report_edits.py",
    "三百万ETF期权五年方略_20260912.html",
    # 依赖 / 缓存目录
    "node_modules/x/index.js",
    ".mypy_cache/3.14/foo.json",
    ".ruff_cache/0.1/foo",
    # 渲染产物 / 二进制文档 / 补丁残留
    "backtests/new_test_20260912/dashboard.html",
    "backtests/new_test_20260912/chart.svg",
    "foo.xlsx",
    "bar.orig",
    "baz.rej",
    # 既有规则回归(防改坏)
    "backtests/etf200w_opt_20260911/index.html",
    "backtests/etf200w_opt_20260911/chart_1.png",
    "backtests/etf200w_opt_20260911/klines_qfq.csv",
    "backtests/etf200w_opt_v91_20260911/_raw/fund_000001.json",
    "backtests/etf200w_opt_v91_20260911/.playwright-cli/log.txt",
    "reports/coverage.xml",
    "monitor_reports/x.json",
    "output/foo.json",
    "sim_snapshots/x.json",
    "v8.3_institutional/reports/hedge_execution_fill_20260912.json",
    "utils/execution/reports/hedge_execution_orders_20260912.json",
    "docs/_mypy_run1.txt",
    "docs/pytest_report1.xml",
    "eod_dry_run_summary_20260912.json",
    "tests/e2e/eod_reports/x.json",
    "llmkey.txt",
    "daily_trade_executor.log",
    ".env",
    "backups/_ptmp3/x.py",
    "_tmp_x.py",
    "debug_x.py",
    "htmlcov/index.html",
    ".pytest_cache/v/cache/lastfailed",
    "__pycache__/foo.cpython-314.pyc",
    "_archive/x.md",
    "~$高价值GitHub项目清单.xlsx",
]

# B) 必须不被忽略(白名单 / 政策放行的源码)
MUST_TRACK = [
    # backtests 政策: 只入库 .py/.csv/.json 核心产物
    "backtests/etf200w_opt_20260911/summary_1.json",
    "backtests/etf200w_opt_v91_20260911/calc_wind_5y.py",
    # CI 冻结基线
    "reports/ruff_baseline.json",
    "reports/ci/coverage_baseline.json",
    # 运行必需配置白名单
    "config/etf_option_subportfolio.yaml",
    "config/kill_switch.yaml",
    "config/risk_thresholds.yaml",
    "config/feature_flags.yaml",
    "config/schema.py",
    # 生产源码目录(曾被 data/ models/ 误伤, 2026-08-29 白名单)
    "utils/data/foo.py",
    "quant_modules/ai_hedge_fund/data/foo.py",
    "ms_strategy/src/data/foo.py",
    "utils/fineng/models/foo.py",
    "data/etf_option_backtest/foo.py",
    "cache/foo.py",
    # 一次性脚本规则收窄为根级锚定后, 子目录脚本必须仍可入库
    "scripts/check_eod_status.py",
    "scripts/check_utf8_mojibake.py",
    # 手写模板(非渲染产物)
    "backtests/_etf_rotation_2014/dashboard_template.html",
]

KNOWN_STRAYS = {"2026-09-12.md", "apply_0912_report_edits.py", "三百万ETF期权五年方略_20260912.html"}


def git(*args: str, stdin: bytes | None = None) -> str:
    r = subprocess.run(["git", *args], input=stdin, capture_output=True)
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def ignored_set(paths: list[str]) -> set[str]:
    payload = ("\n".join(paths) + "\n").encode("utf-8")  # LF, 无 BOM
    out = git("-c", "core.quotepath=false", "check-ignore", "--stdin", stdin=payload)
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def count_index_crlf() -> int:
    return sum(1 for ln in git("ls-files", "--eol").splitlines() if "i/crlf" in ln)


def run_checks(label: str) -> int:
    ign = ignored_set(MUST_IGNORE + MUST_TRACK)
    gaps = [p for p in MUST_IGNORE if p not in ign]
    hurt = [p for p in MUST_TRACK if p in ign]
    smuggle = [
        ln
        for ln in git("-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard").splitlines()
        if ln.strip()
    ]
    leak = sorted(set(smuggle) & KNOWN_STRAYS)
    crlf = count_index_crlf()

    print(f"\n===== {label} =====")
    print(f"[A] 必须忽略 {len(MUST_IGNORE)} 个 -> 缺口 {len(gaps)}")
    for p in gaps:
        print(f"    !! 缺口: {p}")
    print(f"[B] 必须入库 {len(MUST_TRACK)} 个 -> 误伤 {len(hurt)}")
    for p in hurt:
        print(f"    !! 误伤: {p}")
    print(f"[C] `git add -A` 会夹带 {len(smuggle)} 个; 其中已知散落物 {len(leak)} 个 {leak}")
    for p in smuggle:
        if p not in KNOWN_STRAYS:
            print(f"    (其他未跟踪, 非散落物) {p}")
    print(f"[D] index 内 CRLF blob = {crlf} (判据 == 0)")
    if crlf:
        for ln in git("ls-files", "--eol").splitlines():
            if "i/crlf" in ln:
                print(f"    !! {ln}")

    bad = len(gaps) + len(hurt) + len(leak) + (1 if crlf else 0)
    print(f"[结论] {'PASS' if bad == 0 else f'FAIL ({bad} 项)'}")
    return 0 if bad == 0 else 1


def main() -> int:
    top = git("rev-parse", "--show-toplevel").strip()
    if not top:
        print("FAIL: 不在 git 仓库内")
        return 2
    os.chdir(top)

    if "--against-head" not in sys.argv:
        return run_checks("当前 .gitignore / .gitattributes")

    head_gi = git("show", "HEAD:.gitignore")
    if not head_gi.strip():
        print("FAIL: 读不到 HEAD 版 .gitignore")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        bak = os.path.join(tmp, "fixed")
        shutil.copyfile(".gitignore", bak)
        try:
            with open(".gitignore", "wb") as fh:
                fh.write(head_gi.encode("utf-8"))
            rc = run_checks("HEAD 版 .gitignore (修复前, 预期 FAIL)")
        finally:
            shutil.copyfile(bak, ".gitignore")
        restored = open(".gitignore", "rb").read() == open(bak, "rb").read()
    print(f"\n已还原 .gitignore: {restored}")
    return rc if restored else 2


if __name__ == "__main__":
    sys.exit(main())
