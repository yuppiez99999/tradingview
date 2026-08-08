"""快速扫描指定目录的裸 # type: ignore (无错误码)。"""
import re
import sys
from pathlib import Path
from collections import Counter

if len(sys.argv) < 2:
    print("用法: py -3 _scan_dir.py <dir>")
    sys.exit(1)

target_dir = Path(sys.argv[1])
if not target_dir.exists():
    print(f"目录不存在: {target_dir}")
    sys.exit(1)

# 裸 # type: ignore (无错误码) - 负向先行断言：后面不是 [
BARE_RE = re.compile(r"#\s*type:\s*ignore\s*(?!\[)", re.IGNORECASE)

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                "build", "dist", "qlib_env", "qlib", "references", "_archive"}


def should_skip(p: Path) -> bool:
    parts = set(p.parts)
    return bool(parts & EXCLUDE_DIRS)


bare_files = Counter()
bare_total = 0
sample_lines = []

for py in target_dir.rglob("*.py"):
    if should_skip(py):
        continue
    try:
        text = py.read_text(encoding="utf-8")
    except Exception:
        continue
    rel = str(py.relative_to(target_dir))

    matches = list(BARE_RE.finditer(text))
    if matches:
        bare_files[rel] = len(matches)
        bare_total += len(matches)
        # 收集前几行的样本
        for m in matches[:3]:
            # 找到所在行
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].strip()
            line_num = text.count("\n", 0, m.start()) + 1
            sample_lines.append((rel, line_num, line[:100]))


print(f"=== BARE # type: ignore in {target_dir} ===")
print(f"Total: {bare_total}")
print(f"Files affected: {len(bare_files)}")
print()
if bare_files:
    print("Files:")
    for f, n in bare_files.most_common():
        print(f"  {n:4d}  {f}")
    print()
    print("Samples (first 10):")
    for rel, ln, line in sample_lines[:10]:
        print(f"  {rel}:{ln}")
        print(f"    {line}")
