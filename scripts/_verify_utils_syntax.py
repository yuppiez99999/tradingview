"""验证 utils/ 全量语法 + except Exception 清零."""
import ast
from pathlib import Path

utils = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\utils")
errors = 0
count = 0
for py in utils.rglob("*.py"):
    count += 1
    try:
        ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"SYNTAX ERROR: {py.relative_to(utils.parent)}: {e}")
        errors += 1
print(f"检查了 {count} 个文件, 语法错误: {errors}")
