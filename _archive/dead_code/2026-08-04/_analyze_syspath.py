"""分析 SYS_PATH 调用模式，为统一化方案提供数据支持。"""
import ast
import re
from pathlib import Path
from collections import Counter

ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                "build", "dist", "qlib_env", "qlib", "references", "_archive"}

# 分类
categories = {
    "root_entry": [],      # 根目录入口脚本
    "utils_core": [],      # utils/ 核心模块
    "tests": [],           # 测试文件
    "scripts": [],         # scripts/ 工具脚本
    "research": [],        # research/ 研究脚本
    "ms_strategy": [],     # ms_strategy/ 策略模块
    "other": [],           # 其他
}

# 路径模式统计
path_patterns = Counter()
# 调用形式统计
call_forms = Counter()  # insert vs append

SYS_PATH_RE = re.compile(r"sys\.path\.(insert|append)\s*\(")


def categorize(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) == 1:
        return "root_entry"
    if parts[0] == "utils":
        return "utils_core"
    if parts[0] == "tests":
        return "tests"
    if parts[0] == "scripts":
        return "scripts"
    if parts[0] == "research":
        return "research"
    if parts[0] == "ms_strategy":
        return "ms_strategy"
    return "other"


for py in ROOT.rglob("*.py"):
    parts = set(py.parts)
    if parts & EXCLUDE_DIRS:
        continue
    try:
        text = py.read_text(encoding="utf-8")
    except Exception:
        continue
    rel = str(py.relative_to(ROOT))

    matches = list(SYS_PATH_RE.finditer(text))
    if not matches:
        continue

    cat = categorize(rel)
    categories[cat].append((rel, len(matches)))

    for m in matches:
        call_forms[m.group(1)] += 1
        # 提取参数 (简单匹配)
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end].strip()
        # 提取路径参数
        if "BASE_DIR" in line or "PROJECT_ROOT" in line or "__file__" in line:
            path_patterns["project_root_var"] += 1
        elif "os.path" in line or "Path(" in line:
            path_patterns["path_constructor"] += 1
        elif '"' in line or "'" in line:
            path_patterns["string_literal"] += 1
        else:
            path_patterns["other"] += 1


print("=" * 60)
print("=== SYS_PATH 调用分类统计 ===")
print("=" * 60)
for cat, files in categories.items():
    total = sum(n for _, n in files)
    print(f"\n{cat}: {total} calls in {len(files)} files")
    # 显示前 5 个文件
    for f, n in sorted(files, key=lambda x: -x[1])[:5]:
        print(f"  {n:3d}  {f}")

print("\n" + "=" * 60)
print("=== 调用形式统计 ===")
print(f"  insert: {call_forms['insert']}")
print(f"  append: {call_forms['append']}")

print("\n" + "=" * 60)
print("=== 路径参数模式 ===")
for p, n in path_patterns.most_common():
    print(f"  {n:4d}  {p}")
