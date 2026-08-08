"""扫描所有 .py 文件的语法错误。"""
import subprocess
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: py -3 _check_syntax.py <dir>")
    sys.exit(1)

target = Path(sys.argv[1])
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                "build", "dist", "qlib_env", "qlib", "references", "_archive"}

failed_files = []
checked = 0

for py_file in sorted(target.rglob("*.py")):
    parts = set(py_file.parts)
    if parts & EXCLUDE_DIRS:
        continue
    checked += 1
    # 用 py_compile 模块在子进程里检查，捕获 stderr
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(py_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # 提取错误信息
        err_lines = result.stderr.strip().split("\n")
        err_info = ""
        for line in err_lines:
            if "SyntaxError" in line or "Error" in line:
                err_info = line.strip()
                break
        if not err_info and err_lines:
            err_info = err_lines[-1][:200]
        failed_files.append((str(py_file), err_info))

print(f"Checked: {checked} files")
print(f"Failed: {len(failed_files)} files")
print()
for f, err in failed_files:
    print(f"FAIL: {f}")
    print(f"  {err}")
    print()
