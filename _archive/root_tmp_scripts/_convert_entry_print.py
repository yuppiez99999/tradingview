"""临时脚本: 转换 量化策略系统_统一入口_v8.6.py 的 print→logger (完成后删除).

修复版: ensure_logger_setup 正确处理多行 import (from x import (...)) 的结束位置.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def find_print_calls(content: str) -> list[tuple[int, int, str]]:
    """找到所有 print(...) 调用的 (start, end, args_str). 用括号匹配精确处理多行."""
    calls = []
    i = 0
    while i < len(content):
        m = re.match(r'\bprint\s*\(', content[i:])
        if not m:
            i += 1
            continue
        start = i + m.start()
        # 排除 obj.print() 误匹配
        if start > 0 and content[start - 1] == '.':
            i += 1
            continue
        paren_start = i + m.end() - 1
        depth = 1
        j = paren_start + 1
        in_string = None
        while j < len(content) and depth > 0:
            ch = content[j]
            if in_string:
                if ch == '\\':
                    j += 2
                    continue
                if ch == in_string:
                    in_string = None
            else:
                if ch in ('"', "'"):
                    in_string = ch
                elif ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
            j += 1
        if depth != 0:
            i += 1
            continue
        args_str = content[paren_start + 1:j - 1]
        calls.append((start, j, args_str))
        i = j
    return calls


def determine_level(args_str: str) -> str:
    """根据参数内容判断日志级别 (适配含 emoji 的入口脚本)."""
    upper = args_str.upper()
    # ERROR 级: ❌ / 错误 / 失败 / 异常 / [ERROR] / [FAIL]
    if any(x in args_str for x in ['❌', '错误', '失败', '异常']) or \
       any(x in upper for x in ['[ERROR]', '[FAIL]', '[CRITICAL]', 'EXCEPTION']):
        return 'error'
    # WARNING 级: ⚠️ / 警告 / 注意 / [WARN]
    if any(x in args_str for x in ['⚠️', ' warning', '警告', '注意']) or \
       any(x in upper for x in ['[WARN]', 'CAUTION']):
        return 'warning'
    # 其他 (✅ 🚀 📋 📝 等) → info
    return 'info'


def ensure_logger_setup(content: str) -> str:
    """确保文件顶部有 import logging + logger 定义.

    修复版: 用括号深度跟踪, 正确找到最后一个 import 语句的结束位置,
    避免在多行 import (from x import (...)) 中间插入.
    """
    has_import = bool(re.search(r'^import\s+logging\b', content, re.MULTILINE))
    has_logger = bool(re.search(r'^logger\s*=\s*logging\.getLogger\b', content, re.MULTILINE))

    if has_import and has_logger:
        return content

    lines = content.split('\n')
    # 找最后一个 import 语句的结束行号 (处理多行 import)
    last_import_end = -1
    in_docstring = False
    docstring_char = None
    paren_depth = 0
    in_import = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # 处理 docstring
        if in_docstring:
            if docstring_char in stripped:
                in_docstring = False
            continue

        # 跟踪括号深度 (多行 import)
        if paren_depth > 0:
            paren_depth += line.count('(') - line.count(')')
            if paren_depth == 0:
                # 多行 import 结束
                last_import_end = idx
                in_import = False
            continue

        # 检查是否进入 docstring
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count(stripped[:3]) < 2:
                in_docstring = True
                docstring_char = stripped[:3]
            continue

        # 跳过注释和空行
        if not stripped or stripped.startswith('#'):
            continue

        # 检查是否是 import 语句
        if stripped.startswith(('import ', 'from ')):
            last_import_end = idx
            # 检查是否是多行 import (from x import ( )
            paren_depth = line.count('(') - line.count(')')
            if paren_depth > 0:
                in_import = True
                continue
        elif in_import:
            # 在多行 import 中, 已由 paren_depth 分支处理
            pass
        else:
            # 非 import 语句且不在多行 import 中, 停止搜索
            # (但允许 __future__ 注解等在 import 之后)
            if last_import_end >= 0:
                break

    if last_import_end < 0:
        # 没有 import 语句, 在 docstring 之后插入
        last_import_end = 0
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if in_docstring:
                if docstring_char in stripped:
                    in_docstring = False
                    last_import_end = idx
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count(stripped[:3]) < 2:
                    in_docstring = True
                    docstring_char = stripped[:3]
                else:
                    last_import_end = idx
                continue
            if not stripped or stripped.startswith('#'):
                continue
            last_import_end = idx
            break

    insert_lines = []
    if not has_import:
        insert_lines.append('import logging')
    if not has_logger:
        if not has_import:
            insert_lines.append('')
        insert_lines.append('logger = logging.getLogger(__name__)')

    if not insert_lines:
        return content

    # 在 last_import_end + 1 位置插入
    # 如果该位置不是空行, 加一个空行分隔
    insert_idx = last_import_end + 1
    if insert_idx < len(lines) and lines[insert_idx].strip() and insert_lines:
        insert_lines.insert(0, '')

    new_lines = lines[:insert_idx] + insert_lines + lines[insert_idx:]
    return '\n'.join(new_lines)


def convert_file(filepath: str) -> tuple[int, int]:
    """转换单个文件. 返回 (转换数, 删除空 print 数)."""
    path = Path(filepath)
    content = path.read_text(encoding='utf-8')

    calls = find_print_calls(content)
    if not calls:
        return 0, 0

    converted = 0
    deleted = 0
    for start, end, args_str in reversed(calls):
        if args_str.strip() == '':
            # 空 print(): 删除整行
            line_start = content.rfind('\n', 0, start) + 1
            line_end = content.find('\n', end)
            if line_end == -1:
                line_end = len(content)
            full_line = content[line_start:line_end]
            if full_line.strip() in ('print()', 'print( )'):
                content = content[:line_start] + content[line_end + 1:]
                deleted += 1
            else:
                content = content[:start] + 'logger.debug("---")' + content[end:]
                converted += 1
            continue

        level = determine_level(args_str)
        replacement = f'logger.{level}({args_str})'
        content = content[:start] + replacement + content[end:]
        converted += 1

    content = ensure_logger_setup(content)
    path.write_text(content, encoding='utf-8')
    return converted, deleted


def main() -> None:
    """主入口."""
    filepath = sys.argv[1] if len(sys.argv) > 1 else '量化策略系统_统一入口_v8.6.py'
    converted, deleted = convert_file(filepath)
    print(f'{filepath}: converted={converted}, deleted_empty={deleted}')


if __name__ == '__main__':
    main()
