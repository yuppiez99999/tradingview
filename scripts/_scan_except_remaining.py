"""扫描指定目录下所有 .py 文件中剩余的 `except Exception` 分布。

用途:
    - 在完成 backtest_replay.py / providers.py / orchestrator.py 修复后,
      定位 eod_review / dashboard / health / config 等模块剩余的宽泛异常。
    - 输出按文件聚合的统计表, 便于按优先级继续修复。

用法:
    py -3.11 scripts/_scan_except_remaining.py [目录1] [目录2] ...
    默认扫描 ai_decision/ 目录。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


_PATTERN = re.compile(r"\bexcept\s+Exception\b")


def scan_file(path: Path) -> list[int]:
    """返回该文件中所有 `except Exception` 的行号列表。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [i + 1 for i, line in enumerate(lines) if _PATTERN.search(line)]


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        roots = [Path(a) for a in argv[1:]]
    else:
        roots = [Path(__file__).resolve().parent.parent / "ai_decision"]

    total = 0
    rows: list[tuple[str, int, list[int]]] = []
    for root in roots:
        if not root.exists():
            print(f"[WARN] 目录不存在: {root}")
            continue
        for py in sorted(root.rglob("*.py")):
            hits = scan_file(py)
            if hits:
                rows.append((py.name, len(hits), hits))
                total += len(hits)

    print(f"\n=== 剩余 except Exception 统计 (共 {total} 处, {len(rows)} 文件) ===\n")
    if not rows:
        print("  (无残留, 全部已修复为具体异常类型)")
        return 0
    # 按数量降序
    rows.sort(key=lambda r: r[1], reverse=True)
    for name, n, hits in rows:
        print(f"  {n:>3} 处  {name}  ->  L{hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
