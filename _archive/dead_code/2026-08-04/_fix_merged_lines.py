"""修复之前批量替换脚本导致的行合并问题。

模式：'# type: ignore[xxx]
<next statement>'
应该拆分为:
    # type: ignore[xxx]
    <indent><next statement>

兼容 Python 3.7+ (不使用 PEP 604 类型语法)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 匹配 # type: ignore[xxx] 后面的多个空格 + 非空白字符 (不跨行, 避免死循环)
MERGED_PATTERN = re.compile(r"(#\s*type:\s*ignore\[[^\]]+\])([ \t]{2,})(\S)")


def fix_merged_lines(text):
    """修复行合并问题，返回 (修复后的文本, 拆分次数)."""
    total_split = 0
    while True:
        m = MERGED_PATTERN.search(text)
        if not m:
            break
        # 找到行首，确定缩进
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_prefix = text[line_start:m.start()]
        # 提取缩进（前导空格）
        indent_match = re.match(r"^(\s*)", line_prefix)
        indent = indent_match.group(1) if indent_match else ""

        # 拆分：在 # type: ignore[xxx] 后插入换行 + 缩进
        replacement = m.group(1) + "\n" + indent + m.group(3)
        text = text[:m.start()] + replacement + text[m.end():]
        total_split += 1

    return text, total_split


def process_file(path, dry_run=False):
    """处理单个文件，返回拆分次数."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    fixed, n = fix_merged_lines(text)
    if n > 0 and not dry_run:
        path.write_text(fixed, encoding="utf-8")
    return n


def main():
    if len(sys.argv) < 2:
        print("usage: py -3 _fix_merged_lines.py <file_or_dir> [--dry-run]")
        sys.exit(1)

    target = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    total = 0
    if target.is_file():
        n = process_file(target, dry_run)
        total = n
        print(f"{target}: {n} splits")
    elif target.is_dir():
        for py_file in sorted(target.rglob("*.py")):
            n = process_file(py_file, dry_run)
            if n > 0:
                total += n
                print(f"{py_file}: {n} splits")
    else:
        print(f"path not exist: {target}")
        sys.exit(1)

    print(f"\nTotal split: {total}")


if __name__ == "__main__":
    main()
