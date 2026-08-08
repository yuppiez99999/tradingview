"""批量修复 utils/evolution/ 下所有 except Exception 为具体异常类型."""
from __future__ import annotations

import re
from pathlib import Path

_EVOLUTION = Path(__file__).resolve().parent.parent / "utils" / "evolution"

_PATTERN_AS_E = re.compile(r"^(\s*)except Exception as e:$", re.MULTILINE)
_PATTERN_AS_OTHER = re.compile(r"^(\s*)except Exception as (\w+):$", re.MULTILINE)
_PATTERN_BARE = re.compile(r"^(\s*)except Exception:$", re.MULTILINE)

_TYPES_CALC = "(ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError)"
_TYPES_IO = "(ImportError, ModuleNotFoundError, OSError, AttributeError, SyntaxError, ValueError, TypeError)"
_TYPES_GENERIC = _TYPES_CALC

_COMMENT_CALC = "数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络"
_COMMENT_IO = "模块导入/文件IO异常: 未安装/路径错误/读取失败/语法错误/类型不匹配"


def _fix_file(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    original = src
    count = 0

    def _repl_as_e(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group(1)
        return (
            f"{indent}except {_TYPES_CALC} as e:\n"
            f"{indent}    # {_COMMENT_CALC}"
        )

    def _repl_as_other(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group(1)
        var = m.group(2)
        return (
            f"{indent}except {_TYPES_CALC} as {var}:\n"
            f"{indent}    # {_COMMENT_CALC}"
        )

    def _repl_bare(m: re.Match) -> str:
        nonlocal count
        count += 1
        indent = m.group(1)
        return (
            f"{indent}except {_TYPES_CALC}:\n"
            f"{indent}    # {_COMMENT_CALC}"
        )

    src = _PATTERN_AS_E.sub(_repl_as_e, src)
    src = _PATTERN_AS_OTHER.sub(_repl_as_other, src)
    src = _PATTERN_BARE.sub(_repl_bare, src)

    if src != original:
        path.write_text(src, encoding="utf-8")
    return count


def main() -> int:
    total = 0
    for py in sorted(_EVOLUTION.rglob("*.py")):
        n = _fix_file(py)
        if n:
            print(f"  {py.relative_to(_EVOLUTION.parent.parent)}: {n} 处")
            total += n
    print(f"\nevolution/ 共修复 {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
