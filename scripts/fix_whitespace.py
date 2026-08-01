import re
from pathlib import Path

root = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
pat_trailing = re.compile(r'[ \t]+\n')
pat_blank = re.compile(r'^[ \t]+$', re.MULTILINE)

fixed = 0
for path in root.rglob("*.py"):
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue
    new_text, n1 = pat_trailing.subn('\n', text)
    new_text, n2 = pat_blank.subn('', new_text)
    if n1 or n2:
        path.write_text(new_text, encoding="utf-8")
        fixed += 1

print(f"fixed_files={fixed}")
