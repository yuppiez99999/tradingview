"""分析 TOP 文件的 TYPE_IGNORE 上下文, 确定 error code 补充方案."""
import pathlib
import re

TYPE_IGNORE_PATTERN = re.compile(r"(.+?)\s*#\s*type:\s*ignore(\[[^\]]+\])?\s*$", re.IGNORECASE)

# TOP 文件列表
TOP_FILES = [
    "automated_execution_system.py",  # 8 处
    "build_plan_executor.py",  # 24 处
    "utils/ifind_client.py",  # 34 处
    "utils/data_provider.py",  # 33 处
]

for f in TOP_FILES:
    p = pathlib.Path(f)
    if not p.exists():
        continue
    try:
        source = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    lines = source.splitlines()
    print(f"\n{'='*60}")
    print(f"=== {f} ===")
    print(f"{'='*60}")
    count = 0
    for i, line in enumerate(lines, 1):
        m = TYPE_IGNORE_PATTERN.search(line)
        if m:
            count += 1
            code = m.group(2) or "(bare)"
            content = m.group(1).rstrip()
            if count <= 20:  # 前 20 处
                print(f"  L{i}: [{code}] {content[:100]}")
    print(f"  ... Total: {count} 处")
