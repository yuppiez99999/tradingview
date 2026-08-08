"""快速扫描裸 # type: ignore (无错误码) 和 SYS_PATH 调用分布。

只统计项目代码 (排除 references/ 和 _archive/)。
"""
import re
from pathlib import Path
from collections import Counter

ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")

# 裸 # type: ignore (无错误码) - 负向先行断言：后面不是 [
BARE_RE = re.compile(r"#\s*type:\s*ignore\s*(?!\[)", re.IGNORECASE)
# 裸 # type: ignore 行尾匹配
BARE_EOL_RE = re.compile(r"#\s*type:\s*ignore\s*$", re.IGNORECASE)
# sys.path.insert / sys.path.append
SYS_PATH_RE = re.compile(r"sys\.path\.(?:insert|append)\s*\(")

EXCLUDE_DIRS = {"references", "_archive", ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "build", "dist", "qlib_env"}


def should_skip(p: Path) -> bool:
    parts = set(p.parts)
    if parts & EXCLUDE_DIRS:
        return True
    return False


bare_files = Counter()
syspath_files = Counter()
bare_total = 0
syspath_total = 0

for py in ROOT.rglob("*.py"):
    if should_skip(py):
        continue
    try:
        text = py.read_text(encoding="utf-8")
    except Exception:
        continue
    rel = str(py.relative_to(ROOT))

    # bare type: ignore (无错误码)
    bare_matches = list(BARE_RE.finditer(text))
    if bare_matches:
        bare_files[rel] = len(bare_matches)
        bare_total += len(bare_matches)

    # sys.path 调用
    sys_matches = list(SYS_PATH_RE.finditer(text))
    if sys_matches:
        syspath_files[rel] = len(sys_matches)
        syspath_total += len(sys_matches)


print("=" * 60)
print("=== BARE # type: ignore (no error code) ===")
print(f"Total: {bare_total}")
print(f"Files affected: {len(bare_files)}")
print("\nTOP 30 files:")
for f, n in bare_files.most_common(30):
    print(f"  {n:4d}  {f}")


print("\n" + "=" * 60)
print("=== SYS_PATH (sys.path.insert/append) ===")
print(f"Total: {syspath_total}")
print(f"Files affected: {len(syspath_files)}")
print("\nTOP 30 files:")
for f, n in syspath_files.most_common(30):
    print(f"  {n:4d}  {f}")
