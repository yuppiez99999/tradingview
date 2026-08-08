import pathlib
import re

ROOT = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
PATTERN = re.compile(r"^([ \t]*)(import|from)\b", re.MULTILINE)

def fix_file(path: pathlib.Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines:
        return False

    # 文件顶部注释/编码/docstring保留区
    head_end = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            head_end = i + 1
        elif s.startswith('"""') or s.startswith("'''"):
            head_end = i + 1
            break
        else:
            break

    # 找到第一个顶层导入
    first_import = None
    for i in range(head_end, len(lines)):
        if PATTERN.match(lines[i]):
            first_import = i
            break

    if first_import is None or first_import == 0:
        return False

    insert_block = []
    for i in range(head_end, first_import):
        s = lines[i].strip()
        if s:
            insert_block.append(lines[i])

    if not insert_block:
        return False

    new_lines = lines[:first_import] + insert_block + lines[first_import:]
    new_text = "\n".join(new_lines)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False

changed = 0
for p in ROOT.rglob("*.py"):
    try:
        if fix_file(p):
            changed += 1
    except Exception:
        pass

print("changed", changed)
