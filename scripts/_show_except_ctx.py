"""显示指定文件中每个 except Exception 的上下文 (前后 5 行)"""
import sys
from pathlib import Path

def show(file_path: str, context: int = 5):
    p = Path(file_path)
    if not p.exists():
        print(f"文件不存在: {file_path}")
        return
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    matches = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("except Exception"):
            matches.append(i)
    print(f"\n=== {p.name} ({len(matches)} 处) ===\n")
    for idx, m in enumerate(matches):
        start = max(0, m - context)
        end = min(len(lines) - 1, m + 2)
        print(f"--- [{idx+1}/{len(matches)}] L{m+1}: {lines[m].strip()} ---")
        for i in range(start, end + 1):
            mark = ">>>" if i == m else "   "
            print(f"  {mark} L{i+1}: {lines[i].rstrip()}")
        print()

if __name__ == "__main__":
    files = sys.argv[1:]
    for f in files:
        show(f)
