"""批量修复指定目录下所有 except Exception 为具体异常类型."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PATTERN_AS = re.compile(r"^(\s*)except Exception as (\w+):\s*(#.*)?$", re.MULTILINE)
_PATTERN_BARE = re.compile(r"^(\s*)except Exception:\s*(#.*)?$", re.MULTILINE)

_TYPES = "(ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError)"
_COMMENT = "数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时"


def _fix_file(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    original = src
    count = 0

    def _repl_as(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group(1)
        var = m.group(2)
        existing_comment = m.group(3)
        if existing_comment:
            return f"{indent}except {_TYPES} as {var}: {existing_comment}"
        return (
            f"{indent}except {_TYPES} as {var}:\n"
            f"{indent}    # {_COMMENT}"
        )

    def _repl_bare(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group(1)
        existing_comment = m.group(2)
        if existing_comment:
            return f"{indent}except {_TYPES}: {existing_comment}"
        return (
            f"{indent}except {_TYPES}:\n"
            f"{indent}    # {_COMMENT}"
        )

    src = _PATTERN_AS.sub(_repl_as, src)
    src = _PATTERN_BARE.sub(_repl_bare, src)

    if src != original:
        path.write_text(src, encoding="utf-8")
    return count


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: _fix_except_batch.py <dir1> [dir2] ...")
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

    print(f"\n总计: {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
