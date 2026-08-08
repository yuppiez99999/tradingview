"""精细化修复: 把'导入降级模式'的 except (ValueError, TypeError, ...) 改为 except ImportError.

场景: 一批 try: import xxx / from xxx import yyy 后跟 except (ValueError, TypeError, ...)
     这种场景实际只应捕获 ImportError, 把它改成 ImportError 让降级语义更准确.

修复逻辑:
    1. 读取文件源码, 找到所有 `except (ValueError, TypeError, KeyError, ...)` 块
    2. 向上回看 try 块开头, 判断 try 块体是否仅含 import 语句
    3. 若是, 把 except 子句替换为 `except ImportError:` 或 `except ImportError as e:`
    4. 不动其他场景

用法: py -3.11 scripts/_fix_import_fallback_except.py <dir1> [dir2] ...
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 匹配批量工具替换后的 except 子句
_EXCEPT_PATTERN = re.compile(
    r"^(\s*)except \(ValueError, TypeError, KeyError, AttributeError, "
    r"RuntimeError, OSError, TimeoutError, ConnectionError\)( as (\w+))?:",
    re.MULTILINE,
)

# try: ... 块开头 (用于判断是否为导入降级)
_TRY_PATTERN = re.compile(r"^(\s*)try:\s*$", re.MULTILINE)


def _is_import_only_block(src: str, try_start: int, except_start: int) -> bool:
    """判断 try 块体是否仅含 import 语句 (允许注释/空行/pass)."""
    body = src[try_start:except_start]
    # 找 try: 后的内容
    lines = body.splitlines()[1:]  # 跳过 try: 这一行
    has_import = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            has_import = True
            continue
        if stripped.startswith("_") and "=" in stripped:  # _VAR = True/False 之类的赋值
            continue
        if stripped in ("pass",):
            continue
        return False
    return has_import


def _fix_file(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    original = src
    count = 0

    def _repl(m: re.Match) -> str:
        nonlocal count
        indent = m.group(1)
        var = m.group(3)
        except_pos = m.start()

        # 向上查找最近的 try:
        # 从 except_pos 向上扫描,跳过空白和代码,找到 try:
        search_start = max(0, except_pos - 1500)
        prefix = src[search_start:except_pos]

        # 找最后一个 try: 在 prefix 中
        try_matches = list(_TRY_PATTERN.finditer(prefix))
        if not try_matches:
            return m.group(0)  # 不修改

        last_try = try_matches[-1]
        try_abs_pos = search_start + last_try.start()

        # 判断 try 块体
        if not _is_import_only_block(src, try_abs_pos, except_pos):
            return m.group(0)  # 不是导入降级, 不改

        count += 1
        if var:
            return f"{indent}except ImportError as {var}:"
        return f"{indent}except ImportError:"

    src = _EXCEPT_PATTERN.sub(_repl, src)
    if src != original:
        path.write_text(src, encoding="utf-8")
    return count


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: _fix_import_fallback_except.py <dir1> [dir2] ...")
        return 1

    total = 0
    for d in sys.argv[1:]:
        target = Path(d).resolve()
        if not target.exists():
            print(f"[SKIP] 不存在: {target}")
            continue
        dir_total = 0
        for py in sorted(target.rglob("*.py")):
            n = _fix_file(py)
            if n:
                print(f"  {py.relative_to(target.parent)}: {n}")
                dir_total += n
        print(f"  [{target.name}] 小计: {dir_total} 处")
        total += dir_total

    print(f"\n总计: {total} 处导入降级模式已修正为 ImportError")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
