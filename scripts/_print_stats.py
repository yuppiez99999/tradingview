"""统计 print() 调用在各顶级目录的分布"""
import re
from pathlib import Path
from collections import Counter

ROOT = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
EXCLUDE = {"venv", "env", "node_modules", ".git", "__pycache__",
           "vibe_trading", "qlib", "_archive"}

by_dir = Counter()
total = 0
sample_by_dir = {}

for root, dirs, files in __import__("os").walk(ROOT):
    dirs[:] = [d for d in dirs if d not in EXCLUDE
               and not (d == "references" and "research" in root)
               and not (d == "Vibe-Trading" and "research" in root)]
    for fname in files:
        if not fname.endswith(".py"):
            continue
        fpath = Path(root) / fname
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"(?<![\w\.])print\s*\(", line):
                # 排除明显是文档示例
                if line.strip().startswith(">>>") or "Usage:" in line:
                    continue
                total += 1
                rel = fpath.relative_to(ROOT)
                top = rel.parts[0]
                by_dir[top] += 1
                if top not in sample_by_dir:
                    sample_by_dir[top] = []
                if len(sample_by_dir[top]) < 3:
                    sample_by_dir[top].append(f"{rel}:{line_no}  {line.strip()[:80]}")

print(f"Total print() calls: {total}")
print(f"\nBy top-level directory:")
for d, c in by_dir.most_common():
    print(f"  {d:<30} {c}")
    for s in sample_by_dir.get(d, [])[:2]:
        print(f"    {s}")
