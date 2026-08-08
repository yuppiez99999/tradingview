"""按子目录统计 utils/ 下各目录的 except Exception 分布。

用途: 确定第二阶段推进优先级（与交易/风控/数据最相关的子目录先修）。

用法:
    py -3.11 scripts/_scan_utils_by_subdir.py
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

_PATTERN = re.compile(r"\bexcept\s+Exception\b")


def scan_file(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for line in fh if _PATTERN.search(line))
    except OSError:
        return 0


def main() -> int:
    utils = Path(__file__).resolve().parent.parent / "utils"
    if not utils.exists():
        print(f"[ERROR] utils/ 不存在: {utils}")
        return 1

    subdir_counts: dict[str, int] = defaultdict(int)
    subdir_files: dict[str, int] = defaultdict(int)
    root_counts = 0
    root_files = 0

    for py in sorted(utils.rglob("*.py")):
        count = scan_file(py)
        if count == 0:
            continue
        rel = py.relative_to(utils)
        if len(rel.parts) == 1:
            root_counts += count
            root_files += 1
        else:
            subdir = rel.parts[0]
            subdir_counts[subdir] += count
            subdir_files[subdir] += 1

    total = root_counts + sum(subdir_counts.values())
    total_files = root_files + sum(subdir_files.values())

    print(f"\n=== utils/ except Exception 子目录统计 (共 {total} 处, {total_files} 文件) ===\n")

    rows = []
    for subdir, cnt in subdir_counts.items():
        rows.append((subdir, cnt, subdir_files[subdir]))
    rows.sort(key=lambda r: r[1], reverse=True)

    for subdir, cnt, nfiles in rows:
        bar = "█" * min(50, int(cnt / max(1, total) * 100))
        print(f"  {cnt:>4} 处 / {nfiles:>3} 文件  utils/{subdir}/")
    if root_counts:
        print(f"  {root_counts:>4} 处 / {root_files:>3} 文件  utils/ (根目录)")

    print(f"\n  总计: {total} 处 / {total_files} 文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
