"""分析根目录核心入口脚本的 SYS_PATH 模式, 评估统一化可行性."""
import pathlib
import re

ROOT = pathlib.Path(".")
# 根目录的 .py 文件 (核心入口脚本)
root_entry_scripts = sorted(ROOT.glob("*.py"))

SYS_PATH_PATTERN = re.compile(r"sys\.path\.(?:insert|append)\s*\(([^)]*)\)")
PROJECT_ROOT_PATTERN = re.compile(r"(PROJECT_ROOT|BASE_DIR|_project_root|BASE|_BASE_DIR|REPO_ROOT)\s*=")

print("=== 根目录入口脚本 SYS_PATH 模式分析 ===\n")

for py_file in root_entry_scripts:
    if py_file.name.startswith("_"):
        continue
    try:
        source = py_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue

    lines = source.splitlines()
    sys_path_lines = []
    project_root_lines = []

    for i, line in enumerate(lines, 1):
        if SYS_PATH_PATTERN.search(line):
            sys_path_lines.append((i, line.strip()))
        if PROJECT_ROOT_PATTERN.search(line):
            project_root_lines.append((i, line.strip()))

    if sys_path_lines or project_root_lines:
        print(f"--- {py_file.name} ---")
        for ln, content in project_root_lines:
            print(f"  L{ln}: {content}")
        for ln, content in sys_path_lines:
            print(f"  L{ln}: {content}")
        print()
