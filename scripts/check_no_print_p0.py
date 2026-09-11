#!/usr/bin/env python3
"""P0 生产交易文件 print() 检查器.

配套文档: docs/CODE_REVIEW_STANDARD.md §2.1

为什么需要这个检查:
    P0 文件由定时任务 (live_scheduler.py) 驱动执行, print() 输出走 stdout,
    不会进入日志文件。交易日出现故障时, 排查将无任何记录可查。

用法:
    # pre-commit 场景 (由 hook 传入文件列表)
    python scripts/check_no_print_p0.py daily_trade_executor.py

    # 全量巡检 (不传参数, 扫描全部 P0 文件)
    python scripts/check_no_print_p0.py

    # 基线模式: 只报告不阻断, 用于观察存量趋势
    python scripts/check_no_print_p0.py --report-only

退出码:
    0 = 通过    1 = 发现违规

兼容 Python 3.8 (ruff.toml: target-version = "py38")。
"""
from __future__ import annotations  # noqa: F401  (Py3.8 compat for list[tuple[...]] etc.)

import argparse
import ast
import sys
from pathlib import Path

# P0 生产交易路径文件, 与 docs/CODE_REVIEW_STANDARD.md §4 保持一致
# G-2 修复 (2026-08-08): 补充重构后的真正下单引擎 + 回测引擎, 关闭门禁盲区.
# 见 docs/CODE_REVIEW_GAP_AUDIT_2026-08-08.md G-2 + docs/CODE_REVIEW_COMPREHENSIVE_20260808.md B6.
P0_FILES = frozenset({
    "alpha_hedge_engine.py",
    "automated_execution_system.py",  # G-2: basename 匹配, 同时覆盖根薄包装 + utils/execution/ 真实现
    "build_plan_executor.py",
    "daily_build_and_hedge.py",
    "daily_trade_executor.py",
    # 2026-09-10 拆解: daily_trade_executor.py 的盘前指令生成簇迁至此文件,
    # 属同一 P0 生产路径, 必须随重构同步登记 (子目录相对路径形式)。
    "executor/premarket.py",
    "hedge_execution_orders.py",
    "hedge_quantity_calculator.py",
    "institutional_pipeline_runner.py",
    "live_scheduler.py",
    "rebalance_execution_orders.py",
    "run_daily_eod.py",
    # signal_monitor.py 已从 P0 移除: 纯 CLI 工具 (if __name__=="__main__" 触发),
    # 不被任何模块 import, 不由 live_scheduler 定时驱动。print 为 CLI 报告输出 (合法)。
    "signal_post_processing.py",
    "stop_loss_monitor.py",
    "today_hedge_decision.py",
    "vol_adjusted_stop_loss.py",
    "wt_backtest_engine.py",  # G-2: 回测引擎原在双重门禁盲区 (P0 名单外 + pylint 四目录外)
})

# 允许豁免的标记: 行尾加 # allow-print 表示有意为之 (如 CLI 交互输出)
ALLOW_MARKER = "allow-print"


def find_prints(path: Path) -> list[tuple[int, str]]:
    """返回 [(行号, 源码行)]。解析失败时返回空列表并告警。"""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        print(f"[warn] 无法读取 {path}: {exc}", file=sys.stderr)
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # 语法错误单独报, 不当作 print 违规
        print(f"[warn] {path} 语法错误 (line {exc.lineno}): {exc.msg}",
              file=sys.stderr)
        return []

    lines = source.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "print"):
            continue
        lineno = getattr(node, "lineno", 0)
        raw = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if ALLOW_MARKER in raw:
            continue
        hits.append((lineno, raw.strip()))
    return hits


def resolve_targets(argv_files: list[str], repo_root: Path) -> list[Path]:
    """确定要检查的文件: 传参则过滤出 P0, 未传参则全量 P0。"""
    if argv_files:
        out = []
        for name in argv_files:
            p = Path(name)
            rel = p.as_posix()
            # 先按"子目录/文件.py"整体匹配, 再退回 basename 匹配 (保留子目录解析)
            if rel in P0_FILES:
                out.append(p if p.is_absolute() else repo_root / rel)
            elif p.name in P0_FILES:
                out.append(p if p.is_absolute() else repo_root / p.name)
        return out
    return [repo_root / n for n in sorted(P0_FILES)]


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 交易文件 print() 检查")
    parser.add_argument("files", nargs="*", help="待检查文件 (缺省=全量 P0)")
    parser.add_argument("--report-only", action="store_true",
                        help="只报告不阻断, 用于观察存量趋势")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    targets = resolve_targets(args.files, repo_root)

    total = 0
    detail = []
    for path in targets:
        if not path.exists():
            continue
        hits = find_prints(path)
        if hits:
            total += len(hits)
            detail.append((path, hits))

    if not total:
        print("[OK] P0 文件无 print() 违规")
        return 0

    print("=" * 68)
    print(f"P0 生产交易文件发现 {total} 处 print()")
    print("=" * 68)
    for path, hits in detail:
        try:
            shown = path.relative_to(repo_root)
        except ValueError:
            shown = path
        print(f"\n  {shown}  ({len(hits)} 处)")
        for lineno, raw in hits[:5]:
            snippet = raw if len(raw) <= 76 else raw[:73] + "..."
            print(f"    :{lineno:<6} {snippet}")
        if len(hits) > 5:
            print(f"    ... 另有 {len(hits) - 5} 处")

    print("\n" + "-" * 68)
    print("原因: P0 文件由定时任务驱动, print() 输出不进日志, 故障无法追溯。")
    print("修复: 替换为 logger.info/warning/error; 异常场景用 logger.exception。")
    print("豁免: 确属 CLI 交互输出, 行尾加  # allow-print")
    print("依据: docs/CODE_REVIEW_STANDARD.md §2.1")

    return 0 if args.report_only else 1


if __name__ == "__main__":
    sys.exit(main())
