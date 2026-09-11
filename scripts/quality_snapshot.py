#!/usr/bin/env python3
"""代码质量快照 - 每周看板数据生成器.

配套文档: docs/CODE_REVIEW_PROCESS.md §7.1

跟踪 docs/CODE_REVIEW_STANDARD.md §5 门禁表中的核心指标, 判断治理是否真的在推进。
所有数字均为实测, 不依赖任何缓存或历史快照。

用法:
    python scripts/quality_snapshot.py            # 打印快照
    python scripts/quality_snapshot.py --json     # JSON 输出 (供看板消费)

兼容 Python 3.8。
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 审计 item 8 (2026-09-10): 业务时间走 now_bj() (naive 北京时间), 消除本机时区依赖
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))

from utils.datetime_utils import now_bj  # noqa: E402

P0_FILES = [
    "alpha_hedge_engine.py",
    "build_plan_executor.py",
    "daily_build_and_hedge.py",
    "daily_trade_executor.py",
    "executor/premarket.py",  # 2026-09-10 拆解: 盘前指令生成簇 (与宿主同为 P0)
    "hedge_execution_orders.py",
    "hedge_quantity_calculator.py",
    "institutional_pipeline_runner.py",
    "live_scheduler.py",
    "rebalance_execution_orders.py",
    "run_daily_eod.py",
    "signal_monitor.py",
    "signal_post_processing.py",
    "stop_loss_monitor.py",
    "today_hedge_decision.py",
    "vol_adjusted_stop_loss.py",
]

_RE_EXCEPTION = re.compile(r"\.exception\(")
_RE_ERROR = re.compile(r"\.error\(")


# 扫描时必须排除的目录: 第三方库/虚拟环境/归档
EXCLUDE_DIRS = frozenset(
    {
        ".venv",
        "qlib_env",
        "qlib",
        ".tmp_pip",
        ".pip_cache",
        "backups",
        "_archive",
        "_archive_dead_code",
        "external",
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".pytest_cache",
    }
)

# 参与统计的业务分区
TRACKED_ZONES = (
    "utils",
    "v8.3_institutional",
    "scripts",
    "research",
    "tests",
    "tools",
    "ui",
    "ai_decision",
)


def git_ls(pattern: str) -> list[str]:
    """git 索引查询。注意: 索引可能与工作区脱节, 仅用于污染检测。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", pattern],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [f for f in out.split("\n") if f.strip()]


def git_worktree_status() -> dict[str, int]:
    """工作区与 git 索引的偏离度。

    代码审查的前提是变更被提交。未提交的删除/修改越多,
    PR 就越无法反映真实变更, 审查也就越失效。
    """
    result = {"deleted": 0, "modified": 0, "untracked": 0}
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return result
    for line in out.split("\n"):
        if not line:
            continue
        code = line[:2]
        if "D" in code:
            result["deleted"] += 1
        elif "M" in code:
            result["modified"] += 1
        elif code == "??":
            result["untracked"] += 1
    return result


def walk_py_files() -> list[str]:
    """扫描磁盘上真实存在的业务 Python 文件 (相对路径, 正斜杠)。

    不用 git ls-files: 该仓库索引中有大量已删除文件的幽灵条目,
    以索引取数会严重失真。度量必须基于现实。
    """
    found = []
    for root, dirs, files in os.walk(str(REPO)):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, name), str(REPO))
            found.append(rel.replace(os.sep, "/"))
    return found


def zone_of(rel: str) -> str:
    """判定文件所属分区; 不参与统计的返回空串。"""
    if "_archive" in rel or rel.startswith("external/"):
        return ""
    if "/" not in rel:
        return "根目录(P0所在)"
    top = rel.split("/")[0]
    return top + "/" if top in TRACKED_ZONES else ""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def count_prints(text: str, filename: str = "<mem>") -> int:
    """用 AST 精确统计 print() 调用, 避免字符串/注释误判。"""
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "print":
                n += 1
    return n


def count_silent_except(text: str) -> int:
    """统计静默异常: except 后 4 行内无 log/raise/warn。"""
    lines = text.split("\n")
    n = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("except Exception") or s == "except:":
            body = [x.strip() for x in lines[i + 1 : i + 5] if x.strip()]
            joined = " ".join(body[:4]).lower()
            if not ("log" in joined or "raise" in joined or "warn" in joined):
                n += 1
    return n


def scan_zone(files: list[str]) -> dict[str, int]:
    agg = {"files": 0, "print": 0, "exception": 0, "error": 0, "silent_except": 0}
    for rel in files:
        p = REPO / rel
        if not p.exists():
            continue
        text = read(p)
        if not text:
            continue
        agg["files"] += 1
        agg["print"] += count_prints(text, rel)
        agg["exception"] += len(_RE_EXCEPTION.findall(text))
        agg["error"] += len(_RE_ERROR.findall(text))
        agg["silent_except"] += count_silent_except(text)
    return agg


def collect() -> dict:
    all_py = walk_py_files()

    buckets = {}  # type: Dict[str, List[str]]
    for rel in all_py:
        z = zone_of(rel)
        if z:
            buckets.setdefault(z, []).append(rel)

    zone_data = {name: scan_zone(fs) for name, fs in buckets.items()}
    # 按文件数降序, 便于阅读
    zone_data = dict(sorted(zone_data.items(), key=lambda kv: -kv[1]["files"]))

    p0_data = scan_zone([f for f in P0_FILES if (REPO / f).exists()])

    # git 污染: 误提交的虚拟环境二进制
    git_pollution = len(git_ls("qlib_env"))
    worktree = git_worktree_status()

    decimal_files = 0
    biggest = ("", 0)
    for rel in all_py:
        text = read(REPO / rel)
        if not text:
            continue
        # 拆分字面量, 避免本文件自身被计入 (自指误报)
        if ("from " + "decimal import") in text:
            decimal_files += 1
        n = text.count("\n")
        if n > biggest[1]:
            biggest = (rel, n)

    return {
        "timestamp": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "disk_py_files": len(all_py),
        "zones": zone_data,
        "p0": p0_data,
        "git_pollution": git_pollution,
        "worktree": worktree,
        "decimal_files": decimal_files,
        "biggest_file": {"path": biggest[0], "lines": biggest[1]},
    }


# 门禁目标, 严格对应 docs/CODE_REVIEW_STANDARD.md §5 阶段1
GATES = [
    ("未提交变更总数", "worktree_dirty", 100, "le"),
    ("git 中 qlib_env 文件", "git_pollution", 0, "le"),
    ("P0 区 print()", "p0_print", 60, "le"),
    ("P0 区静默异常", "p0_silent", 6, "le"),
    ("单文件最大行数", "max_lines", 3000, "le"),
]


def render(data: dict) -> None:
    print("=" * 74)
    print("  代码质量快照   {}".format(data["timestamp"]))
    print("=" * 74)
    print("磁盘实存业务 Python 文件: {}".format(data["disk_py_files"]))

    wt = data["worktree"]
    dirty = wt["deleted"] + wt["modified"] + wt["untracked"]
    print("\n【工作区卫生】  <- 代码审查的前提")
    print(
        "  已删除未提交: {:<6} 已修改未提交: {:<6} 未跟踪: {}".format(
            wt["deleted"], wt["modified"], wt["untracked"]
        )
    )
    print(f"  未提交变更合计: {dirty}")
    if dirty > 100:
        print("  [!] 变更未进版本控制, PR 无法反映真实改动, 审查将失效")

    print("\n【日志规范分区对比】  <- 核心治理指标")
    print(
        "  {:<22}{:>6}{:>9}{:>8}{:>12}{:>9}{:>7}".format(
            "分区", "文件", "print()", "密度", "exception()", "error()", "静默"
        )
    )
    for name, z in data["zones"].items():
        density = (z["print"] / z["files"]) if z["files"] else 0
        print(
            "  {:<22}{:>6}{:>9}{:>8.1f}{:>12}{:>9}{:>7}".format(
                name,
                z["files"],
                z["print"],
                density,
                z["exception"],
                z["error"],
                z["silent_except"],
            )
        )
    print("  注: 密度 = print()/文件。utils/ 是唯一有 CI 门禁的区, 可作基准。")

    p0 = data["p0"]
    print("\n【P0 生产交易路径】({} 个文件)".format(p0["files"]))
    print(
        "  print()     : {:<6} 静默异常: {:<6} exception(): {}".format(
            p0["print"], p0["silent_except"], p0["exception"]
        )
    )

    print("\n【其他】")
    print("  git 中 qlib_env 文件 : {}".format(data["git_pollution"]))
    print("  使用 Decimal 的文件: {}".format(data["decimal_files"]))
    print(
        "  最大文件           : {} ({} 行)".format(
            data["biggest_file"]["path"], data["biggest_file"]["lines"]
        )
    )

    actual = {
        "worktree_dirty": dirty,
        "git_pollution": data["git_pollution"],
        "p0_print": p0["print"],
        "p0_silent": p0["silent_except"],
        "max_lines": data["biggest_file"]["lines"],
    }
    print("\n【阶段1 门禁达成情况】")
    passed = 0
    for label, key, target, op in GATES:
        val = actual[key]
        ok = (val <= target) if op == "le" else (val >= target)
        passed += ok
        print(
            "  [{}] {:<20} {:>7}  (目标 {} {})".format(
                "PASS" if ok else "FAIL",
                label,
                val,
                "<=" if op == "le" else ">=",
                target,
            )
        )
    print(f"\n  达成 {passed}/{len(GATES)}")
    print("\n依据: docs/CODE_REVIEW_STANDARD.md §5")


def main() -> int:
    ap = argparse.ArgumentParser(description="代码质量快照")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    data = collect()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
